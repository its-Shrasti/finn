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

NAME_RX = re.compile(r"(?:appoint(?:ment of|ed)?|takes over|elevat\w+|join\w*)\s+(?:of\s+)?(?:Mr\.?|Ms\.?|Mrs\.?|Dr\.?|Shri)?\s*([A-Z][a-z]+(?:\s+[A-Z]\.?)*(?:\s+[A-Z][a-z]+){1,3})")
NAME_RX2 = re.compile(r"([A-Z][a-z]+(?:\s+[A-Z][a-z']+){1,3})\s+(?:becomes|takes over|to take over|appointed|as (?:the )?(?:new )?(?:MD|CEO|CFO|Chairman))")


def incoming_person(title: str, detail: str) -> str | None:
    for rx in (NAME_RX2, NAME_RX):
        m = rx.search(detail) or rx.search(title)
        if m:
            return m.group(1).strip()
    try:
        out = llm.complete_json("cheap", "Extract names from corporate filings. JSON only.",
                                f"Filing: {title}\n{detail[:1500]}\nReturn {{\"incoming\": full name of the person joining/appointed or null}}")
        return out.get("incoming")
    except Exception:
        return None


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


def person_bio(name: str, company: str, role: str) -> dict:
    k = cache.key("bio", name, company)
    hit = cache.get("bio", k)
    if hit:
        return hit
    hits = _search(f'"{name}" {role} LinkedIn career') + _search(f'"{name}" {company} appointed background')
    ctx = "\n".join(f"- {h['title']}: {h['snippet']} ({h['url']})" for h in hits[:12])
    out = llm.complete_json(
        "cheap",
        "You write 3-line professional bios for investors from search snippets only. Never guess; omit what the snippets don't say. JSON only.",
        f"Person: {name}, new {role} at {company}.\nSnippets:\n{ctx}\n"
        'Return {"bio": "<=60 words: prior roles with years, education", "previous": ["Role, Org (years)"], "education": "...", "sources": [urls used]}')
    return cache.put("bio", k, {"name": name, **out})


def results_from_filing(ticker: str, pdf_url: str) -> dict:
    t = documents.text(pdf_url, max_pages=6)
    out = llm.complete_json(
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
