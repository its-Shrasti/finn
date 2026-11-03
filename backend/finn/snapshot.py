"""Build the app snapshot (one JSON the mobile app renders).

  python -m finn snapshot            # replay captured inputs (works offline)
  python -m finn snapshot --live     # NSE/BSE + yfinance + screener, real-time

Both paths run the same classify → score → cluster → enrich → alert code.
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta

from . import alerts, classify, enrich, priority, results
from .config import DATA, PORTFOLIO, AlertRules
from .research import engine

RESEARCH_DEMO = ["PERSISTENT", "POLYCAB", "KAYNES"]


def _trading_days(end: date, n: int) -> list[date]:
    out, d = [], end
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def simulated_eod(ticker: str, last_price: float, end: date, n: int = 60) -> list[dict]:
    """Seeded fat-tailed random walk ending at the real last price. Used ONLY
    when the sandbox can't reach Yahoo; every bar is flagged simulated."""
    rng = random.Random(f"finn:{ticker}")
    days = _trading_days(end, n)
    rets = []
    for _ in days[1:]:
        z = rng.gauss(0, 1)
        if rng.random() < 0.06:
            z *= rng.uniform(2.2, 3.4)          # occasional large move
        rets.append(z * 0.015)
    px = [last_price]
    for r in reversed(rets):
        px.append(px[-1] / (1 + r))
    px = px[::-1]
    base_vol = rng.uniform(0.8, 6.0) * 1e6 * (1000 / max(last_price, 50)) ** 0.5
    bars = []
    for i, d in enumerate(days):
        v = base_vol * math.exp(rng.gauss(0, 0.35))
        if i > 0 and abs(rets[i - 1]) > 0.03:
            v *= rng.uniform(1.8, 3.2)          # big moves come with volume
        elif rng.random() < 0.04:
            v *= rng.uniform(2.0, 3.0)
        bars.append({"date": d.isoformat(), "close": round(px[i], 2), "volume": int(v)})
    return bars


def _items(companies: list[dict], cuts: dict[str, dict]) -> list[priority.Item]:
    items = []
    for c in companies:
        anns = list(c.get("announcements", []))
        # A captured feed is truncated (latest N filings). If the results table shows a new
        # quarter but the results filing itself fell outside the feed, surface it anyway —
        # otherwise a -36% PAT quarter would silently go missing from the digest.
        has_filing = any(classify.classify(a["title"], use_llm=False).subtype == "filing"
                         and classify.classify(a["title"], use_llm=False).category == "results" for a in anns)
        if not has_filing and c.get("result_date") and c["ticker"] in cuts and cuts[c["ticker"]].get("available"):
            anns.append({"date": c["result_date"], "title": "Q2 FY27 results – quarter ended 30 Sep 2026",
                         "detail": "Results filing outside the captured feed; numbers from the parsed results table."
                                   + (" Filing date inferred." if c.get("result_date_approx") else "")})
        for i, a in enumerate(anns):
            lab = classify.classify(a["title"], a.get("detail", ""), use_llm=False)
            it = priority.Item(id=f"{c['ticker']}-{a['date']}-{i}", ticker=c["ticker"], date=a["date"],
                               title=a["title"], detail=a.get("detail", ""), category=lab.category, subtype=lab.subtype)
            surprise = cuts.get(c["ticker"], {}).get("surprise") if lab.category == "results" else None
            neg = any(f.get("tone") == "neg" for f in cuts.get(c["ticker"], {}).get("flags", [])) if lab.category == "results" else False
            priority.score(it, results_surprise=surprise, results_neg_flag=neg)
            if lab.category == "results" and lab.subtype == "filing" and c["ticker"] in cuts:
                it.enrichment = {"kind": "results", **cuts[c["ticker"]]}
            if lab.category == "management_change" and a.get("person"):
                it.enrichment = {"kind": "person", **a["person"], **(a.get("bio") or {})}
            items.append(it)
    return items


def build(live: bool = False, now: datetime | None = None) -> dict:
    raw = json.loads((DATA / "inputs" / "portfolio_raw.json").read_text())
    bios = json.loads((DATA / "inputs" / "bios.json").read_text())
    companies = raw["companies"]
    now = now or datetime.fromisoformat(raw["_meta"]["captured_at"])
    eod_source = "simulated"

    if live:
        from .sources import filings, prices, screener
        tickers = list(PORTFOLIO)
        frm = (now - timedelta(days=30)).date()
        ann = filings.announcements(tickers, frm, now.date())
        companies = []
        for t in tickers:
            s = screener.company(t)
            companies.append({"ticker": t, "name": s["name"], "weight": PORTFOLIO[t], "price": s["price"],
                              "mcap": s["mcap"], "pe": s["pe"], "quarters": s["quarters"][-5:],
                              "announcements": [a for a in ann if a["ticker"] == t]})
        bars_by = prices.eod(tickers)
        eod_source = "yfinance"
    else:
        bars_by = {c["ticker"]: simulated_eod(c["ticker"], c["price"], now.date()) for c in companies}

    for c in companies:
        for a in c.get("announcements", []):
            p = a.get("person")
            if p:
                if live and not bios.get(p["name"]):
                    try:
                        bios[p["name"]] = enrich.person_bio(p["name"], c["name"], p["role"])
                    except Exception:
                        pass
                a["bio"] = bios.get(p["name"])

    cuts = {}
    for c in companies:
        if c.get("quarters"):
            cuts[c["ticker"]] = results.first_cut(c["ticker"], c["quarters"])
        elif c.get("headline_only"):
            h = c["headline_only"]
            cuts[c["ticker"]] = {"available": True, "partial": True, "quarter": h["q"], "note": c.get("quarters_note"),
                                 "rows": [{"metric": "Revenue", "value": h["sales"], "yoy": h["sales_yoy_pct"]},
                                          {"metric": "EBITDA", "na": "not in snapshot"}, {"metric": "PBT", "na": "not in snapshot"},
                                          {"metric": "PAT", "value": h["pat"]}],
                                 "flags": [{"kind": "data_basis", "tone": "neutral", "text": c.get("quarters_note", "")}],
                                 "headline": f"Revenue {h['sales_yoy_pct']:+d}% YoY · PAT ₹{h['pat']:,} cr", "surprise": None}

    items = priority.cluster(_items(companies, cuts))
    items.sort(key=lambda x: (x.date, x.score), reverse=True)

    rules = AlertRules()
    eod_alerts = [a for t, b in bars_by.items() for a in alerts.scan(t, b, rules, sessions=10)]

    total_w = sum(c.get("weight", 0) for c in companies) or 1
    portfolio = [{
        "ticker": c["ticker"], "name": c["name"], "sector": c.get("sector", ""),
        "weight": round(c.get("weight", 0) / total_w * 100, 1), "price": c["price"], "mcap": c["mcap"], "pe": c["pe"],
        "kind": c.get("kind", "industrial"), "result": cuts.get(c["ticker"]),
        "result_date": c.get("result_date"), "result_date_approx": c.get("result_date_approx", False),
        "upcoming_result": c.get("upcoming_result"),
    } for c in companies]

    research = {}
    for t in RESEARCH_DEMO:
        research[t] = engine.run_live(t) if live else engine.run_from_inputs(t)

    return {
        "meta": {"as_of": now.isoformat(), "mode": "live" if live else "snapshot", "eod_source": eod_source,
                 "filings_source": "NSE/BSE" if live else "screener.in announcement feed (captured)",
                 "fundamentals_source": "screener.in (AR-derived tables) + concall transcripts",
                 "defaults": {"price_move_pct": rules.price_move_pct, "volume_multiple": rules.volume_multiple,
                              "volume_lookback": rules.volume_lookback, "watch_categories": sorted(rules.watch_categories)}},
        "portfolio": portfolio,
        "items": [it.__dict__ for it in items],
        "raw_count": sum(len(c.get("announcements", [])) for c in companies),
        "eod": bars_by,
        "eod_alerts": alerts.to_dicts(eod_alerts),
        "research": research,
    }


def write(live: bool = False) -> str:
    snap = build(live=live)
    out = DATA.parent.parent / "app" / "snapshot.json"
    out.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":"), default=str))
    return str(out)
