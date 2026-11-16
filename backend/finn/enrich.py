"""Push-side enrichment.

management_change → who is the incoming person? (name from the filing, short
    professional bio from a web search — LinkedIn/company bio pages)
results           → headline numbers QoQ/YoY. Preferred path: parsed results
    table (exact). Fallback: read the actual results PDF filing and have the
    cheap model transcribe the four rows × three columns the filing already
    prints (current, previous, year-ago quarter); arithmetic stays in Python.
"""
from __future__ import annotations

import os
import re

import requests

from . import cache, llm, results
from .config import USER_AGENT
from .sources import documents

# Initials ("V. Srinivasa Rangan", "Noel N. Tata") only count when followed by a full stop or a space,
# so the "P" of "Puri" is never mistaken for one.
_NAME = r"((?:[A-Z]\.\s*)*[A-Z][a-z]+(?:\s+[A-Z](?:\.|(?=\s)))*(?:\s+[A-Z][a-z']+){0,3})"
NAME_RX = re.compile(r"(?i:appoint(?:ment of|ed)?|re-appointment of|takes over|elevat\w+|join\w*)\s+(?:of\s+)?(?:(?i:Mr|Ms|Mrs|Dr|Shri|Smt)\.?\s+)+" + _NAME)
NAME_RX2 = re.compile(_NAME + r"\s+(?:becomes|takes over|to take over|(?:is |has been )?appointed|(?:has |will )?(?:assumed|assumes|assume|taken|take) charge|"
                     r"as (?:the )?(?:new )?(?:MD|CEO|CFO|Chairman))")
# a departure word that directly introduces the name ("Retirement of Mr. X", "resignation of Shri Y")
LEAVING_RX = re.compile(r"(?:retire\w*|resign\w*|cessation|ceas\w*|superannuation|demise|vacation)\s+(?:of\s+|by\s+)?"
                        r"(?:(?:Mr|Ms|Mrs|Dr|Shri|Smt)\.?\s+)*$", re.I)
NOT_A_NAME = {"The", "Board", "Company", "Bank", "Limited", "Exchange", "Committee", "Directors", "Director", "Shareholders",
              "Members", "Nomination", "Remuneration", "Government", "Ministry", "Reserve", "India"}


def incoming_person(title: str, detail: str) -> str | None:
    for rx in (NAME_RX2, NAME_RX):
        m = rx.search(detail) or rx.search(title)
        if m:
            return m.group(1).strip()
    try:
        out = llm.complete_obj("cheap", "Extract names from corporate filings. JSON only.",
                                f"Filing: {title}\n{detail[:1500]}\nReturn {{\"incoming\": full name of the person joining/appointed or null}}")
        return out.get("incoming")
    except Exception:
        return None


ROLE_RX = re.compile(r"\bas\s+(?:the\s+)?(?:new\s+)?((?:Managing Director|MD)(?:\s*(?:&|and)\s*(?:CEO|Chief Executive Officer))?|"
                     r"Chief (?:Executive|Financial|Operating|Technology) Officer|CEO|CFO|COO|Chairman|Chairperson|"
                     r"Executive Director|Whole[- ]?time Director|Independent Director|Non-?\s*Executive Director|Govt\.? Nominee Director|Company Secretary)", re.I)


HONORIFIC = re.compile(r"^(?:(?:Mr|Ms|Mrs|Dr|Shri|Smt|Prof|Sri)\.?\s+)+", re.I)


def _clean(name: str) -> str:
    return HONORIFIC.sub("", re.sub(r"\s+", " ", name or "")).strip(" .,")


SENIORITY = [r"managing director|\bmd\b|ceo|chief executive", r"chair(?!man emeritus)", r"cfo|chief financial",
             r"whole[- ]?time|executive director", r"director|secretary|officer"]


def _rank(role: str) -> int:
    return next((i for i, rx in enumerate(SENIORITY) if re.search(rx, role or "", re.I)), len(SENIORITY))


def _leaving(text: str) -> set[str]:
    """Names the same filing says are retiring, resigning or ceasing ("Retirement of Mr. X",
    "Mr. X ... will retire", "X has tendered his resignation")."""
    out = set()
    for m in re.finditer(r"(?i:retirement|resignation|cessation)\s+of\s+(?:(?i:Mr|Ms|Mrs|Dr|Shri|Smt)\.?\s+)*" + _NAME, text):
        out.add(_clean(m.group(1)))
    for m in re.finditer(_NAME + r"(?:\s*\([^)]{0,30}\))?,?\s+(?:will retire|retires|has retired|has tendered (?:his|her) resignation|"
                         r"resigns|has resigned|steps down|will step down|ceases|has vacated|vacates|will vacate)", text):
        out.add(_clean(m.group(1)))
    return out


def _appointments(text: str) -> list[tuple[str, str]]:
    """Every (name, role) appointment the text states, the role read from just after the name."""
    found = []
    for rx in (NAME_RX, NAME_RX2):
        for m in rx.finditer(text):
            cand = _clean(m.group(1))
            words = cand.replace(".", " ").split()
            if len([w for w in words if len(w) > 1]) < 2 or set(words) & NOT_A_NAME:
                continue
            if rx is NAME_RX2 and LEAVING_RX.search(text[max(0, m.start(1) - 40):m.start(1)]):
                continue
            after = text[m.end(1):m.end(1) + 160]
            rm = ROLE_RX.search(after)
            role = rm.group(1).strip() if rm and rm.start() < 90 else ""
            if re.search(r"emeritus", after[:(rm.end() + 12) if rm else 40], re.I):
                continue                    # an honorary title for someone leaving is not an appointment
            found.append((cand, role))
    return found


def incoming(title: str, detail: str, max_chars: int = 1500) -> dict | None:
    """Who is joining, in what role, replacing whom. Patterns first: every stated appointment,
    minus anyone the filing says is leaving, most senior role first. The cheap model only if
    the patterns find no one. Returns None for filings that appoint nobody."""
    text = f"{title}\n{detail}"
    leaving = _leaving(text)
    apps = [(n, r) for n, r in _appointments(text) if n not in leaving]
    apps.sort(key=lambda a: (_rank(a[1]) if a[1] else len(SENIORITY) + 1))
    name, role = (apps[0] if apps else (None, None))
    if name and role:
        out = {"name": name, "role": role, "by": "regex"}
        if leaving:
            out["outgoing"] = sorted(leaving)[0]
        return out
    try:
        raw = llm.complete_json("cheap", "Extract appointments from Indian corporate filings. JSON only. Use null when the text doesn't say.",
                                f"Filing: {title}\n{detail[:max_chars]}\nReturn ONE JSON object {{\"incoming\": full name of the person appointed or null, "
                                f"\"role\": their new role or null, \"outgoing\": full name of the person leaving or null}}. "
                                f"If several people are appointed, return the most senior one.")
    except Exception:  # noqa: BLE001
        raw = {}
    # several appointees in one filing: keep the most senior role (MD/CEO/CFO/Chairman first)
    cands = [c for c in (raw if isinstance(raw, list) else [raw]) if isinstance(c, dict) and c.get("incoming")]
    top = re.compile(r"managing director|\bmd\b|ceo|chief executive|cfo|chief financial|chair", re.I)
    cands = [c for c in cands if _clean(c["incoming"]) not in leaving]     # the model can name a leaver too
    out = next((c for c in cands if top.search(c.get("role") or "")), cands[0] if cands else {})
    name = _clean(name or out.get("incoming") or "")
    if not name:
        return None
    return {"name": name, "role": role or out.get("role") or "", "outgoing": out.get("outgoing"), "by": "regex+model" if out else "regex"}


def _search(q: str) -> list[dict]:
    """SerpAPI if a key is set, else DuckDuckGo's HTML endpoint."""
    if os.getenv("SERPAPI_KEY"):
        r = requests.get("https://serpapi.com/search.json", params={"q": q, "api_key": os.getenv("SERPAPI_KEY"), "num": 8}, timeout=20)
        return [{"title": x.get("title"), "snippet": x.get("snippet"), "url": x.get("link")} for x in r.json().get("organic_results", [])]
    r = requests.post("https://html.duckduckgo.com/html/", data={"q": q}, headers={"User-Agent": USER_AGENT}, timeout=20)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r.text, "html.parser")
    return [{"title": a.get_text(" ", strip=True), "snippet": (s.get_text(" ", strip=True) if s else ""), "url": a.get("href")}
            for a, s in ((res.select_one(".result__a"), res.select_one(".result__snippet")) for res in soup.select(".result")) if a][:8]


NO_BIO = "No sourced background found. FINN shows career facts only with a source link."


def vet_bio(out: dict, allowed: set | None = None) -> dict:
    """Keep a model-written bio only if it cites at least one URL the search returned.
    Real people's career facts are never shown without a source."""
    srcs = [u for u in (out.get("sources") or []) if isinstance(u, str) and u.startswith("http")
            and (allowed is None or u in allowed)]
    if not srcs or not out.get("bio"):
        return {"name": out.get("name"), "not_found": NO_BIO}
    return {**out, "sources": srcs}


def person_bio(name: str, company: str, role: str) -> dict:
    k = cache.key("bio", name, company)
    hit = cache.get("bio", k)
    if hit:
        return vet_bio(hit)
    hits = _search(f'"{name}" {role} LinkedIn career') + _search(f'"{name}" {company} appointed background')
    if not hits:   # nothing to ground a bio in: don't ask the model (not cached; the search may work next run)
        return {"name": name, "not_found": NO_BIO}
    ctx = "\n".join(f"- {h['title']}: {h['snippet']} ({h['url']})" for h in hits[:12])
    out = llm.complete_obj(
        "cheap",
        "You write 3-line professional bios for investors from search snippets only. Never guess; omit what the snippets don't say. JSON only.",
        f"Person: {name}, new {role} at {company}.\nSnippets:\n{ctx}\n"
        'Return {"bio": "<=60 words: prior roles with years, education", "previous": ["Role, Org (years)"], "education": "...", "sources": [urls used]}')
    return cache.put("bio", k, vet_bio({"name": name, **out}, {h["url"] for h in hits[:12]}))


def results_from_filing(ticker: str, pdf_url: str) -> dict:
    t = documents.text(pdf_url, max_pages=6)
    out = llm.complete_obj(
        "cheap",
        "You transcribe numbers from Indian quarterly results statements exactly as printed. JSON only.",
        "From the CONSOLIDATED statement (else standalone), copy for the three quarter columns "
        "(current, preceding, corresponding quarter last year): revenue from operations, total expenses, "
        "other income, finance costs, depreciation, profit before tax, tax expense, net profit. Convert to Rs crore.\n"
        'Return {"basis": "consolidated|standalone", "columns": [{"q": "Mon YYYY", "sales":..,"expenses":..,"other_income":..,"interest":..,"dep":..,"pbt":..,"tax":..,"pat":..}, ...] oldest first}\n\n'
        + t[:24000])
    cols = out.get("columns", [])
    for c in cols:  # EBITDA computed, never transcribed
        if c.get("pbt") is not None:
            c["ebitda"] = round(c["pbt"] + (c.get("interest") or 0) + (c.get("dep") or 0) - (c.get("other_income") or 0), 1)
    cut = results.first_cut(ticker, cols)
    cut["basis"] = out.get("basis")
    cut["source"] = pdf_url
    return cut
