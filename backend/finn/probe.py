"""`python -m finn probe`: try each live source once and say plainly what works.

Run this before `snapshot --live`. Writes data/runs/probe.json. It reports whether
GEMINI_API_KEY is set, never its value.
"""
from __future__ import annotations

import json
import os
import platform
import sys
import time
from datetime import date, timedelta

from .config import DATA


def _try(name: str, fn, report: dict):
    t0 = time.time()
    try:
        out = fn()
        report[name] = {"ok": True, "detail": out, "secs": round(time.time() - t0, 1)}
    except Exception as e:  # noqa: BLE001
        r = getattr(e, "response", None)
        why = f"HTTP {r.status_code}" if r is not None else ""
        report[name] = {"ok": False, "error": f"{type(e).__name__} {why}".strip(), "message": str(e)[:240],
                        "secs": round(time.time() - t0, 1)}
    print(f"{'PASS' if report[name]['ok'] else 'FAIL'}  {name:<22} {report[name].get('detail') or report[name].get('error')}")


def run() -> dict:
    from .sources import filings, screener
    report: dict = {"python": sys.version.split()[0], "platform": platform.platform(),
                    "utf8_mode": bool(sys.flags.utf8_mode), "gemini_key_set": bool(os.getenv("GEMINI_API_KEY"))}
    try:
        import yfinance
        report["yfinance_version"] = yfinance.__version__
    except Exception as e:  # noqa: BLE001
        report["yfinance_version"] = f"not importable: {e}"
    print(json.dumps({k: report[k] for k in list(report)}, indent=1))
    to = date.today()
    frm = to - timedelta(days=30)

    def nse():
        s = filings._nse_session()
        rows = filings.nse("HDFCBANK", frm, to, s)
        return f"{len(rows)} HDFCBANK filings in 30 days; latest {rows[0]['date']} '{rows[0]['title'][:60]}'" if rows else "0 rows"

    def bse():
        rows = filings.bse("HDFCBANK", frm, to)
        return f"{len(rows)} HDFCBANK filings in 30 days; latest {rows[0]['date']} '{rows[0]['title'][:60]}'" if rows else "0 rows"

    def yahoo():
        import yfinance as yf
        df = yf.download("HDFCBANK.NS", period="1mo", interval="1d", auto_adjust=False, progress=False)
        if df is None or df.empty:
            raise RuntimeError("empty frame")
        if df.columns.nlevels > 1:
            df = df.droplevel(1, axis=1)
        last = df.dropna(subset=["Close"]).iloc[-1]
        return f"{len(df)} bars; last {df.index[-1].date()} close {float(last['Close']):.2f} volume {last['Volume']}"

    def scr(t):
        def f():
            d = screener.company(t)
            q = [x for x in d["quarters"] if x.get("sales") is not None]
            return (f"{d['name']} ({d['basis']}); {len(q)} quarters, latest {q[-1]['q']} sales {q[-1]['sales']}; "
                    f"{len(d['docs']['concalls'])} transcripts, newest {d['docs']['concalls'][0]['date'] if d['docs']['concalls'] else '-'}")
        return f

    def gemini_models():
        import requests
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", params={"pageSize": 200},
                         headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]}, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        names = sorted(m["name"].split("/")[-1] for m in r.json().get("models", [])
                       if "generateContent" in m.get("supportedGenerationMethods", []))
        report["gemini_models_available"] = names
        return f"{len(names)} text models available to this key"

    def gemini(tier):
        def f():
            from . import llm
            before = (llm.METER.tokens_in, llm.METER.tokens_out)
            txt = llm.complete(tier, "Reply with the single word OK.", f"ping {time.time()}")
            return (f"{llm.MODELS[tier]['model']} replied {txt.strip()[:20]!r}; "
                    f"{llm.METER.tokens_in - before[0]} in / {llm.METER.tokens_out - before[1]} out tokens")
        return f

    _try("nse", nse, report)
    _try("bse", bse, report)
    _try("yahoo", yahoo, report)
    for t in ("HDFCBANK", "NAVINFLUOR", "PIIND"):
        _try(f"screener {t}", scr(t), report)
    if report["gemini_key_set"]:
        _try("gemini models", gemini_models, report)
        _try("gemini cheap", gemini("cheap"), report)
        _try("gemini strong", gemini("strong"), report)
    else:
        report["gemini"] = {"ok": False, "error": "GEMINI_API_KEY not set in this terminal"}
        print("FAIL  gemini                 GEMINI_API_KEY not set in this terminal")
    runs = DATA / "runs"
    runs.mkdir(exist_ok=True)
    (runs / "probe.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report
