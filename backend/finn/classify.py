"""Announcement classifier.

Rules first, model second. ~90% of exchange filings have formulaic subjects
("Outcome of Board Meeting", "Allotment of ESOP"), so ordered regex rules
classify them for free and deterministically. Only what falls through goes to
the cheap model tier — and that result is cached by subject text.

Beyond the companion doc's categories we add a *subtype*, because the category
alone doesn't say whether to care: a results *filing* is signal; the newspaper
ad confirming it is paperwork. Same category, opposite priority.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import cache, llm

CATEGORIES = [
    "results", "M&A", "fund_raise", "management_change", "litigation", "insider_trading",
    "earnings_call", "investor_meetings", "credit_rating", "dividend", "bonus_split", "capex",
    "press_release", "postal_ballot", "agm_egm", "pledging", "unclassified",
]


@dataclass
class Label:
    category: str
    subtype: str
    rule: str  # which rule fired, or "llm"


# (category, subtype, pattern). Order matters: first match wins.
RULES: list[tuple[str, str, str]] = [
    # --- routine paperwork that *mentions* important words: catch these first
    ("unclassified", "paperwork", r"loss of share certificate|duplicate share|reg(ulation)?\.? ?74 ?\(5\)|compliance certificate|trading window|esg (rating|report|score)|newspaper publication(?!.*result)"),
    ("unclassified", "esop", r"\b(esop|esos|rsu)\b|stock options?|employee stock"),
    ("results", "paperwork", r"newspaper.*result|result.*newspaper|publication of (financial )?results"),
    ("earnings_call", "transcript", r"transcript"),
    ("earnings_call", "recording", r"audio|video recording|recording of"),
    ("results", "presentation", r"press release.*(q[1-4] ?fy|result|profit)|investor presentation|earnings presentation|results? (deck|presentation)"),
    ("results", "board_intimation", r"board meeting.*(result|financial)|intimation.*(result|financial results)"),
    ("results", "filing", r"(un)?audited (standalone |consolidated )?(financial )?results|financial results|results for the quarter|quarter(ly)? results|q[1-4] ?fy\d\d results"),
    # --- people
    ("management_change", "top", r"(md|managing director|ceo|chief executive|cfo|chief financial|chairman|chairperson)\b.*(appoint|resign|step|retire|cessation|change|takes over)|(appoint|resign|retire|change|cessation).*(\bmd\b|managing director|\bceo\b|\bcfo\b|chairman|chairperson)|change in (md|managing director|ceo|cfo)|retirement of .*chairman"),
    ("management_change", "director", r"change in (directorate|director)|appointment of .*director|executive director|independent director|resignation of .*director"),
    ("management_change", "senior", r"change in (senior )?management|key managerial|kmp|group head|superannuat"),
    # --- capital
    ("pledging", "pledge", r"pledg|encumbrance"),
    ("insider_trading", "insider", r"sast|regulation 7\(2\)|insider|acquisition of shares by promoter|disposal of shares"),
    ("fund_raise", "equity", r"\bqip\b|qualified institution|preferential|rights issue|\bwarrants?\b"),
    ("fund_raise", "buyback", r"buy ?back"),
    ("fund_raise", "debt", r"\bncds?\b|non[- ]convertible|commercial paper|debentures?|bonds?\b"),
    ("bonus_split", "bonus_split", r"\bbonus\b|sub-?division|stock split|split of"),
    ("dividend", "dividend", r"dividend|record date"),
    ("credit_rating", "rating", r"credit rating|\brating\b(?!.*esg)|crisil|icra|care ratings|india ratings"),
    ("M&A", "deal", r"acqui|merger|amalgamation|demerger|scheme of arrangement|joint venture|\bjv\b|business transfer|stake (sale|purchase)|share purchase agreement|wholly[- ]owned subsidiary|incorporation of .*subsidiary"),
    ("litigation", "legal", r"cirp|insolvency|nclt|nclat|litigation|lawsuit|penalt|show cause|sebi order|regulatory order|\bfine\b|tax demand|usfda|warning letter|import alert|observations?"),
    ("press_release", "clarification", r"clarification|rumou?r|verification of (market )?rumou?r|news item|spurt in volume"),
    ("capex", "capex", r"capex|capacity expansion|new plant|greenfield|brownfield|commission(ed|ing) of"),
    ("press_release", "orders", r"order(s)? (win|received|bagged)|bagging|receiving of order|contract(s)? (win|award)"),
    ("press_release", "operating_data", r"production volume|monthly sales|sales (for|in) (the month|october|november|december|january|february|march|april|may|june|july|august|september)|business update|operational (update|snapshot)|operational and financial snapshot|operational & financial snapshot"),
    ("postal_ballot", "ballot", r"postal ballot"),
    ("agm_egm", "meeting", r"\bagm\b|\begm\b|annual general|extraordinary general|shareholder meeting"),
    ("investor_meetings", "meet", r"analyst|investor meet|investor conference|roadshow|non-deal|conference call|earnings call"),
    ("press_release", "general", r"press release|media release|launch|partnership|mou|enters|collaborat"),
]

_COMPILED = [(c, s, re.compile(p, re.I)) for c, s, p in RULES]


def classify(title: str, detail: str = "", *, use_llm: bool = True) -> Label:
    text = f"{title}"
    for cat, sub, rx in _COMPILED:
        if rx.search(text):
            return Label(cat, sub, rx.pattern[:40])
    # second pass on detail text (subjects are sometimes generic "Regulation 30")
    for cat, sub, rx in _COMPILED:
        if detail and rx.search(detail):
            return Label(cat, sub, "detail:" + rx.pattern[:34])
    if use_llm:
        try:
            return _llm_classify(title, detail)
        except Exception:
            pass
    return Label("unclassified", "unknown", "fallthrough")


def _llm_classify(title: str, detail: str) -> Label:
    k = cache.key("classify", title, detail)
    hit = cache.get("classify", k)
    if hit:
        return Label(**hit)
    out = llm.complete_json(
        "cheap",
        "You label Indian stock-exchange corporate announcements. Answer JSON only.",
        f"Categories: {', '.join(CATEGORIES)}.\nSubject: {title}\nDetail: {detail[:600]}\n"
        'Return {"category": one of the categories, "subtype": "short snake_case"}',
    )
    cat = out.get("category") if out.get("category") in CATEGORIES else "unclassified"
    lab = Label(cat, out.get("subtype", "llm"), "llm")
    cache.put("classify", k, lab.__dict__)
    return lab
