"""Build the app snapshot (one JSON the mobile app renders).

  python -m finn snapshot            # replay captured inputs (works offline)
  python -m finn snapshot --live     # NSE/BSE + yfinance + screener, real-time

Both paths run the same classify → score → cluster → enrich → alert code.
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta, timezone

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


def _items(companies: list[dict], cuts: dict[str, dict], use_llm: bool = False) -> list[priority.Item]:
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
            lab = classify.classify(a["title"], a.get("detail", ""), use_llm=use_llm)
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


IST = timezone(timedelta(hours=5, minutes=30))
RESEARCH_LIVE = DATA / "research_live"


def _live_companies(raw: dict, now: datetime, sources: dict) -> tuple[list[dict], dict]:
    """Live fetch, one ticker at a time, so one failure never sinks the run. A ticker
    screener.in can't serve falls back to the captured copy and is labelled so."""
    from .sources import filings, prices, screener
    captured = {c["ticker"]: c for c in raw["companies"]}
    tickers = list(PORTFOLIO)
    frm = (now - timedelta(days=30)).date()
    ann, sources["filings"] = filings.announcements_status(tickers, frm, now.date())
    companies, sources["fundamentals"] = [], {}
    for t in tickers:
        meta = {k: captured.get(t, {}).get(k) for k in ("sector", "kind")}
        try:
            sc = screener.company(t)
            c = {"ticker": t, "name": sc["name"], "weight": PORTFOLIO[t], "price": sc["price"], "mcap": sc["mcap"],
                 "pe": sc["pe"], "quarters": sc["quarters"][-5:], **meta}
            sources["fundamentals"][t] = f"screener.in ({sc['basis']})"
        except Exception as e:  # noqa: BLE001
            c = dict(captured[t])
            sources["fundamentals"][t] = f"captured {raw['_meta']['captured_at'][:10]} (screener.in failed: {type(e).__name__})"
        live_ann = [a for a in ann if a["ticker"] == t]
        if live_ann or not sources["filings"].get(t, "").startswith("none"):
            c["announcements"] = live_ann
        else:   # neither exchange answered: keep the captured filings for this ticker, labelled
            c["announcements"] = captured.get(t, {}).get("announcements", [])
            sources["filings"][t] += f"; showing captured feed from {raw['_meta']['captured_at'][:10]}"
        res = sorted(a["date"] for a in c["announcements"]
                     if classify.classify(a["title"], a.get("detail", ""), use_llm=False).category == "results")
        c["result_date"] = res[0] if res else captured.get(t, {}).get("result_date")
        for a in c["announcements"]:
            if "person" in a:
                continue
            lab = classify.classify(a["title"], a.get("detail", ""), use_llm=False)
            if lab.category == "management_change" and lab.subtype in ("top", "director"):
                try:
                    p = enrich.incoming(a["title"], a.get("detail", ""))
                    # NSE's announcement text often doesn't name the person ("Change in Managing
                    # Director and CEO"); the name is in the attached filing. Read its first pages.
                    if not p and a.get("url"):
                        from .sources import documents
                        pdf = documents.text(a["url"], max_pages=3)
                        p = enrich.incoming(a["title"], pdf, max_chars=6000)
                        if p:
                            p["from"] = "filing attachment"
                except Exception as e:  # noqa: BLE001  one odd filing must not stop the snapshot
                    p = None
                    sources.setdefault("person_errors", []).append(f"{t} {a['date']}: {type(e).__name__}")
                if p:
                    a["person"] = {"name": p["name"], "role": p["role"], "from": p.get("from", "announcement text"),
                                   **({"outgoing": p["outgoing"]} if p.get("outgoing") else {})}
        companies.append(c)
    bars, sources["prices"] = prices.eod_status(tickers)
    return companies, bars


def build(live: bool = False, now: datetime | None = None, live_research: bool = False) -> dict:
    raw = json.loads((DATA / "inputs" / "portfolio_raw.json").read_text(encoding="utf-8"))
    bios = json.loads((DATA / "inputs" / "bios.json").read_text(encoding="utf-8"))
    companies = raw["companies"]
    sources: dict = {}
    if live:
        now = now or datetime.now(IST).replace(microsecond=0)
        companies, bars_by = _live_companies(raw, now, sources)
        missing = [t for t in PORTFOLIO if t not in bars_by]
        eod_source = "yfinance" if bars_by else "unavailable"
        sources["prices_missing"] = missing     # no prices for these: shown as missing, never simulated
    else:
        now = now or datetime.fromisoformat(raw["_meta"]["captured_at"])
        eod_source = "simulated"
        bars_by = {c["ticker"]: simulated_eod(c["ticker"], c["price"], now.date()) for c in companies}

    for c in companies:
        for a in c.get("announcements", []):
            p = a.get("person")
            if p:
                if live and not bios.get(p["name"]):
                    try:
                        bios[p["name"]] = enrich.person_bio(p["name"], c["name"], p["role"])
                    except Exception as e:  # noqa: BLE001  no bio is fine ("No background found yet"); say why
                        sources.setdefault("bio_errors", []).append(f"{p['name']}: {type(e).__name__}: {str(e)[:200]}")
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

    classify.ERRORS.clear()
    items = priority.cluster(_items(companies, cuts, use_llm=live))
    if classify.ERRORS:
        from collections import Counter
        sources["classify_errors"] = [f"{n}x {m}" for m, n in Counter(classify.ERRORS).most_common(5)]
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
        research[t] = engine.run_live(t) if live_research else engine.run_from_inputs(t)
    # research generated live with `python -m finn research TICKER` (saved with its date and real cost)
    for f in sorted(RESEARCH_LIVE.glob("*.json")) if RESEARCH_LIVE.exists() else []:
        r = json.loads(f.read_text(encoding="utf-8"))
        research.setdefault(r["ticker"], r)

    return {
        "meta": {"as_of": now.isoformat(), "mode": "live" if live else "snapshot", "eod_source": eod_source,
                 "filings_source": _summary(sources.get("filings")) if live else "screener.in announcement feed (captured)",
                 "sources": sources,
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


def _summary(status: dict | None) -> str:
    if not status:
        return "unavailable"
    n = {"NSE": 0, "BSE": 0, "none": 0}
    for v in status.values():
        n["NSE" if v.startswith("NSE") else "BSE" if v.startswith("BSE") else "none"] += 1
    parts = [f"NSE for {n['NSE']}", f"BSE for {n['BSE']}"] + ([f"neither for {n['none']} (captured feed shown)"] if n["none"] else [])
    return "live: " + ", ".join(p for p in parts if not p.endswith(" 0")) + f" of {len(status)} tickers"


def write(live: bool = False, live_research: bool = False) -> str:
    snap = build(live=live, live_research=live_research)
    out = DATA.parent.parent / "app" / "snapshot.json"
    out.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
    return str(out)


def add_research() -> str:
    """Merge saved live research (data/research_live/*.json) into the existing
    app/snapshot.json without refetching anything else."""
    out = DATA.parent.parent / "app" / "snapshot.json"
    snap = json.loads(out.read_text(encoding="utf-8"))
    added = []
    for f in sorted(RESEARCH_LIVE.glob("*.json")) if RESEARCH_LIVE.exists() else []:
        r = json.loads(f.read_text(encoding="utf-8"))
        snap["research"][r["ticker"]] = r
        added.append(r["ticker"])
    out.write_text(json.dumps(snap, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
    return f"{out}: research now {sorted(snap['research'])} (added/updated {added})"
