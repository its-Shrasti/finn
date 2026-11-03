"""Results first-cut: Revenue / EBITDA / PBT / PAT, QoQ and YoY.

Pure arithmetic on the parsed results table — no model in the loop, so the
numbers are exactly the filing's. On top, a few deterministic "read-through"
flags that tell the investor *why* the bottom line moved, because a PAT miss
driven by a one-off tax line is a different story from an operating miss.

Definitions (screener.in convention):
  EBITDA = Sales - operating expenses (excludes other income)
  For lenders EBITDA is not meaningful and is withheld.
"""
from __future__ import annotations

from .config import LENDERS

METRICS = [("sales", "Revenue"), ("ebitda", "EBITDA"), ("pbt", "PBT"), ("pat", "PAT")]


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return round((a - b) / abs(b) * 100, 1)


def _find(quarters: list[dict], label: str) -> dict | None:
    return next((q for q in quarters if q["q"] == label), None)


def _year_ago(label: str) -> str:
    mon, yr = label.split()
    return f"{mon} {int(yr) - 1}"


def first_cut(ticker: str, quarters: list[dict]) -> dict:
    if len(quarters) < 2:
        return {"available": False, "reason": "fewer than two quarters"}
    cur, prev = quarters[-1], quarters[-2]
    ya = _find(quarters, _year_ago(cur["q"]))
    lender = ticker in LENDERS
    rows = []
    for key, name in METRICS:
        if key == "ebitda" and lender:
            rows.append({"metric": name, "na": "n/m for lenders"})
            continue
        v = cur.get(key)
        rows.append({
            "metric": name, "value": v,
            "prev": prev.get(key), "qoq": _pct(v, prev.get(key)),
            "year_ago": ya.get(key) if ya else None, "yoy": _pct(v, ya.get(key)) if ya else None,
        })
    margins = {}
    if not lender and cur.get("ebitda") and cur.get("sales"):
        m = lambda q: round(q["ebitda"] / q["sales"] * 100, 1) if q and q.get("sales") else None
        margins = {"ebitda_margin": m(cur), "prev": m(prev), "year_ago": m(ya)}
    flags = read_through(cur, prev, ya, lender)
    return {
        "available": True, "quarter": cur["q"], "prev_q": prev["q"], "year_ago_q": ya["q"] if ya else None,
        "rows": rows, "margins": margins, "flags": flags, "headline": headline(rows, margins, flags),
        "surprise": next((r.get("yoy") for r in rows if r["metric"] == "PAT"), None),
    }


def read_through(cur: dict, prev: dict, ya: dict | None, lender: bool) -> list[dict]:
    flags: list[dict] = []
    if not ya:
        return flags
    pat_yoy = _pct(cur.get("pat"), ya.get("pat"))
    if not lender and cur.get("ebitda") is not None:
        e_yoy = _pct(cur.get("ebitda"), ya.get("ebitda"))
        if pat_yoy is not None and e_yoy is not None and abs(pat_yoy - e_yoy) >= 15:
            drivers = []
            oi_d = (cur.get("other_income") or 0) - (ya.get("other_income") or 0)
            if abs(oi_d) >= 0.05 * abs(ya.get("pbt") or 1):
                drivers.append(f"other income {'+' if oi_d >= 0 else '−'}₹{abs(oi_d):,.0f} cr YoY")
            dep_d = (cur.get("dep") or 0) - (ya.get("dep") or 0)
            if dep_d >= 0.08 * abs(ya.get("pbt") or 1):
                drivers.append(f"depreciation +₹{dep_d:,.0f} cr")
            t0, t1 = ya.get("tax_pct"), cur.get("tax_pct")
            if t0 is not None and t1 is not None and abs(t1 - t0) >= 5 and 0 <= t1 <= 60 and 0 <= t0 <= 60:
                drivers.append(f"tax rate {t0}%→{t1}%")
            flags.append({
                "kind": "below_the_line", "tone": "neg" if pat_yoy < e_yoy else "pos",
                "text": f"PAT {pat_yoy:+.0f}% vs EBITDA {e_yoy:+.0f}% YoY — gap is below the operating line"
                        + (f" ({'; '.join(drivers)})" if drivers else ""),
            })
        if cur.get("sales") and ya.get("sales") and ya.get("ebitda") is not None:
            m1 = cur["ebitda"] / cur["sales"] * 100
            m0 = ya["ebitda"] / ya["sales"] * 100
            if m1 - m0 <= -1.5:
                flags.append({"kind": "margin", "tone": "neg", "text": f"EBITDA margin {m0:.1f}% → {m1:.1f}% YoY ({m1 - m0:+.1f} pp)"})
            elif m1 - m0 >= 1.5:
                flags.append({"kind": "margin", "tone": "pos", "text": f"EBITDA margin {m0:.1f}% → {m1:.1f}% YoY ({m1 - m0:+.1f} pp)"})
    if lender and cur.get("gnpa") is not None and prev.get("gnpa") is not None:
        d = cur["gnpa"] - prev["gnpa"]
        if abs(d) >= 0.05:
            flags.append({"kind": "asset_quality", "tone": "neg" if d > 0 else "pos",
                          "text": f"Gross NPA {prev['gnpa']:.2f}% → {cur['gnpa']:.2f}% QoQ"})
    prior_tax = [q.get("tax_pct") for q in (prev, ya) if q and q.get("tax_pct") is not None]
    if any(t < 0 or t > 60 for t in prior_tax):
        flags.append({"kind": "base_effect", "tone": "neutral", "text": "Comparison quarter had an unusual tax line — read PBT, not PAT"})
    return flags


def headline(rows: list[dict], margins: dict, flags: list[dict]) -> str:
    r = {x["metric"]: x for x in rows}
    parts = []
    rev = r.get("Revenue", {})
    if rev.get("yoy") is not None:
        parts.append(f"Revenue {rev['yoy']:+.0f}% YoY")
    if margins.get("ebitda_margin") is not None:
        parts.append(f"EBITDA margin {margins['ebitda_margin']:.1f}%")
    pat = r.get("PAT", {})
    if pat.get("yoy") is not None:
        parts.append(f"PAT {pat['yoy']:+.0f}%")
    s = " · ".join(parts)
    neg = [f for f in flags if f["tone"] == "neg"]
    if neg:
        s += " — watch: " + neg[0]["kind"].replace("_", " ")
    return s
