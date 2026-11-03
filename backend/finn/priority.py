"""Prioritisation: turn a pile of filings into three tiers.

score = base(category, subtype)  +  position weight  +  event modifiers
  tier "act"    >= 60   -> shown expanded, with enrichment
  tier "know"   30–59   -> one line
  tier "noise"  <  30   -> collapsed into a count ("23 routine filings")

Filings about the same event (results filing + deck + call audio + newspaper
ad) are clustered into one card so the investor reads one thing, not five.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .config import LENDERS, PORTFOLIO

BASE: dict[tuple[str, str], int] = {
    ("results", "filing"): 48, ("results", "presentation"): 25, ("results", "paperwork"): 5,
    ("results", "board_intimation"): 35,
    ("management_change", "top"): 85, ("management_change", "director"): 45, ("management_change", "senior"): 40,
    ("M&A", "deal"): 60,
    ("fund_raise", "equity"): 65, ("fund_raise", "buyback"): 60, ("fund_raise", "debt"): 25,
    ("litigation", "legal"): 70,
    ("press_release", "clarification"): 60, ("press_release", "orders"): 40,
    ("press_release", "operating_data"): 45, ("press_release", "general"): 20,
    ("credit_rating", "rating"): 30,
    ("dividend", "dividend"): 25, ("bonus_split", "bonus_split"): 45, ("capex", "capex"): 50,
    ("pledging", "pledge"): 70, ("insider_trading", "insider"): 50,
    ("earnings_call", "transcript"): 18, ("earnings_call", "recording"): 6,
    ("investor_meetings", "meet"): 8, ("agm_egm", "meeting"): 25, ("postal_ballot", "ballot"): 20,
    ("unclassified", "paperwork"): 2, ("unclassified", "esop"): 4,
}

# words that upgrade or downgrade within a category
UP = [("downgrad", 30), ("upgrad", 15), ("resign", 10), ("halt", 10), ("warning letter", 20),
      ("import alert", 20), ("highest", 8), ("record", 5), ("qip", 5)]
DOWN = [("reaffirm", -10), ("continued", -10), ("extension of timeline", -15), ("timeline extended", -15),
        ("incorporation of", -35), ("update on acquisition", -10), ("loss of share", -10)]


@dataclass
class Item:
    id: str
    ticker: str
    date: str
    title: str
    detail: str
    category: str
    subtype: str
    score: int = 0
    tier: str = "noise"
    why: str = ""
    related: list = field(default_factory=list)
    enrichment: dict | None = None


def score(item: Item, *, results_surprise: float | None = None, results_neg_flag: bool = False) -> Item:
    s = BASE.get((item.category, item.subtype), 15)
    text = (item.title + " " + item.detail).lower()
    for w, d in UP + DOWN:
        if w in text:
            s += d
    # Lenders raise debt every week; it's inventory, not news.
    if item.category == "fund_raise" and item.subtype == "debt" and item.ticker in LENDERS:
        s = min(s, 10)
    # A results filing matters more the bigger the surprise.
    if item.category == "results" and item.subtype == "filing":
        # Beats are worth knowing; misses need a decision. A profit decline or a
        # deteriorating line (margin, asset quality, below-the-line hit) lifts a
        # results filing into "act"; an in-line or better quarter stays "know".
        if results_surprise is not None:
            s += min(30, int(-results_surprise)) if results_surprise < 0 else min(5, int(results_surprise / 6))
        if results_neg_flag:
            s += 10
    s += round(PORTFOLIO.get(item.ticker, 3) / 9 * 6)  # 0–6 for position size
    item.score = max(0, min(100, s))
    item.tier = "act" if item.score >= 60 else "know" if item.score >= 30 else "noise"
    return item


def cluster(items: list[Item]) -> list[Item]:
    """Merge same-ticker, same-event filings. Results-family filings collapse
    into the results filing; same (category, subtype) on the same ticker within
    the window collapse into the highest-scoring one."""
    groups: dict[tuple, list[Item]] = defaultdict(list)
    for it in items:
        fam = it.category
        if it.category in ("results", "earnings_call") or (it.category == "dividend" and any(
                o.ticker == it.ticker and o.category == "results" and o.subtype == "filing" and o.date == it.date for o in items)):
            fam = "results_family"
        if it.category == "management_change":
            fam = f"mgmt:{it.date}"
        groups[(it.ticker, fam)].append(it)
    out = []
    for (_, fam), its in groups.items():
        its.sort(key=lambda x: (-x.score, -int(x.date.replace("-", ""))))
        lead = its[0]
        lead.related = [{"date": o.date, "title": o.title, "category": o.category} for o in its[1:]]
        out.append(lead)
    return out
