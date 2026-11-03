"""Pull-side research for ANY NSE ticker.

Pipeline (map → measure → score → reduce):
  1. fundamentals  screener.in tables (from annual reports)            free, cached 12h
  2. metrics       CAGR, margins, D/E, CFO/PAT, CCC in Python           free
  3. map           last 4 transcripts → keyword-filtered chunks →
                   CHEAP model extracts guidance + reported figures      ~4 × 3 chunks
  4. measure       STRONG model dedupes promises, attaches checkable
                   measures (keys restricted to series we actually have) 1 call
  5. score         ledger.py computes verdicts                          free
  6. reduce        STRONG model writes snapshot, narrative-vs-numbers,
                   bull/bear — given only computed facts + ledger        1 call
Everything is cached by content hash; a re-run costs nothing.
"""
from __future__ import annotations

import json
from datetime import datetime

from .. import llm
from ..config import DATA
from ..sources import documents, screener
from . import ledger, metrics, prompts


def _facts(d: dict) -> dict:
    return {
        "trajectory": metrics.trajectory(d["annual"]),
        "quarterly": metrics.quarterly_trend(d["quarters"]),
        "balance_sheet": metrics.balance_sheet(d["bs"], d["annual"]) if d.get("bs") else {},
        "cash": metrics.cash_quality(d["cf"], d["annual"]) if d.get("cf") else {},
        "working_capital": metrics.working_capital(d.get("wc", [])),
    }


def _normalise_screener(raw: dict) -> dict:
    """screener annual columns are 'Mar 2026' → 'FY26'; keep last 6 years."""
    def fy(lbl: str) -> str:
        if lbl.upper() == "TTM":
            return "TTM"
        parts = lbl.split()
        return f"FY{parts[-1][-2:]}" if parts and parts[-1].isdigit() else lbl
    for key in ("annual", "bs", "cf", "wc"):
        rows = [dict(r, y=fy(r.pop("q"))) for r in raw.get(key, []) if r.get("q")]
        raw[key] = rows[-6:] if key == "annual" else [r for r in rows if r["y"] != "TTM"][-5:]
    raw["quarters"] = raw.get("quarters", [])[-8:]
    return raw


def run_live(ticker: str, n_calls: int = 4) -> dict:
    d = _normalise_screener(screener.company(ticker))
    facts = _facts(d)
    calls = []
    for c in d["docs"]["concalls"][:n_calls]:
        try:
            txt = documents.text(c["url"])
        except Exception:
            continue
        cid = c["date"] or c["url"][-12:]
        ext = {"guidance": [], "reported": {}, "pushback": []}
        for chunk in documents.guidance_chunks(txt)[:3]:
            part = llm.complete_json("cheap", prompts.GROUNDING, prompts.MAP.format(call_id=cid, date=c["date"], text=chunk))
            ext["guidance"] += part.get("guidance", [])
            ext["reported"].update(part.get("reported", {}))
            ext["pushback"] += part.get("pushback", [])
        calls.append({"id": cid, "date": c["date"], "url": c["url"], **ext})
    calls.reverse()  # oldest → newest
    series_keys = sorted({k for c in calls for k in c["reported"]})
    measured = llm.complete_json("strong", prompts.GROUNDING, prompts.MEASURE.format(
        series_keys=series_keys,
        guidance=json.dumps([{"call": c["id"], "guidance": c["guidance"]} for c in calls], indent=1),
        series=json.dumps([{"call": c["id"], **c["reported"]} for c in calls], indent=1)))
    led = ledger.build(measured.get("guidance", []), calls)
    pushback = [p for c in calls for p in c["pushback"]][:10]
    notes = llm.complete_json("strong", prompts.GROUNDING, prompts.REDUCE.format(
        name=d["name"], ticker=ticker, facts=json.dumps(facts)[:12000],
        ledger=json.dumps(led["rows"]), pushback="\n".join(pushback)))
    return assemble(ticker, d, facts, led, notes, calls, pushback, mode="live")


def run_from_inputs(ticker: str) -> dict:
    """Offline path used for the demo snapshot: same metrics + ledger code,
    inputs captured during the build, reduce-step output stored in research_notes.json."""
    inp = json.loads((DATA / "inputs" / "research_inputs.json").read_text())[ticker]
    notes = json.loads((DATA / "inputs" / "research_notes.json").read_text())[ticker]
    facts = _facts(inp)
    led = ledger.build(inp["guidance"], inp["calls"])
    return assemble(ticker, inp, facts, led, notes, inp["calls"], inp.get("pushback", []), mode="snapshot")


def assemble(ticker, d, facts, led, notes, calls, pushback, mode) -> dict:
    return {
        "ticker": ticker, "name": d["name"], "about": d.get("about", ""), "mode": mode,
        "generated_at": datetime.now().isoformat(timespec="minutes"),
        "price": d.get("price"), "mcap": d.get("mcap"), "pe": d.get("pe"), "roce": d.get("roce"),
        "shareholding": d.get("shareholding"), "segments": d.get("segments", []), "geo": d.get("geo", []),
        "annual": d["annual"], "quarters": d["quarters"], **facts,
        "ledger": led, "pushback": pushback, "events": d.get("events", []),
        "sources": [{"id": c["id"], "date": c.get("date"), "url": c.get("url"), "kind": "concall transcript"} for c in calls]
                   + [{"id": "screener", "url": f"https://www.screener.in/company/{ticker}/consolidated/", "kind": "AR-derived tables"}],
        **{k: notes.get(k) for k in ("snapshot", "narrative_vs_numbers", "bull", "bear", "watch", "generated_by", "data_note")},
        "cost": {"calls": llm.METER.calls, "cached": llm.METER.cached, "usd": round(llm.METER.usd, 4), "by_tier": llm.METER.by_tier},
    }
