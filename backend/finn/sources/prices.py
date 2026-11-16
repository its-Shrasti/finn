"""End-of-day price & volume via yfinance (.NS, falling back to .BO).

eod_status() also says, per ticker, where the bars came from (or why there are
none), so the snapshot can label data honestly. Nothing here is ever simulated.
"""
from __future__ import annotations

import math

from .. import cache


def _bars(sub) -> list[dict]:
    out = []
    for ix, r in sub.iterrows():
        c, v = r.get("Close"), r.get("Volume")
        if c is None or (isinstance(c, float) and math.isnan(c)):
            continue
        vol = None if v is None or (isinstance(v, float) and math.isnan(v)) else int(v)
        out.append({"date": str(ix.date()), "close": round(float(c), 2), "volume": vol})
    return out


def drop_partial_day(bars: list[dict], now=None) -> list[dict]:
    """Yahoo returns today's bar while the market is still open. That is a partial day,
    not end-of-day: drop it until 16:00 IST (NSE closes at 15:30)."""
    from datetime import datetime, timedelta, timezone
    now = now or datetime.now(timezone(timedelta(hours=5, minutes=30)))
    if bars and bars[-1]["date"] == now.date().isoformat() and (now.hour, now.minute) < (16, 0):
        return bars[:-1]
    return bars


def eod_status(tickers: list[str], period: str = "3mo") -> tuple[dict[str, list[dict]], dict[str, str]]:
    import yfinance as yf  # imported lazily: optional dependency

    k = cache.key("eod2", sorted(tickers), period)
    hit = cache.get("eod", k, max_age_s=6 * 3600)  # EOD data: refresh a few times a day
    if hit:
        return hit["bars"], hit["status"]
    out: dict[str, list[dict]] = {}
    status: dict[str, str] = {}
    syms = [f"{t}.NS" for t in tickers]
    try:
        df = yf.download(syms, period=period, interval="1d", group_by="ticker",
                         auto_adjust=False, progress=False, threads=True)
    except Exception as e:  # noqa: BLE001
        df, err = None, f"{type(e).__name__}: {e}"[:160]
    for t, s in zip(tickers, syms):
        sub = None
        try:
            sub = df[s].dropna(subset=["Close"]) if df is not None else None
        except KeyError:
            sub = None
        src = "yfinance .NS"
        if sub is None or sub.empty:  # BSE fallback
            try:
                sub = yf.download(f"{t}.BO", period=period, interval="1d", auto_adjust=False, progress=False)
                if sub is not None and hasattr(sub.columns, "levels") and len(sub.columns.levels) > 1:
                    sub = sub.droplevel(1, axis=1) if sub.columns.nlevels > 1 else sub
                sub = sub.dropna(subset=["Close"]) if sub is not None else None
                src = "yfinance .BO"
            except Exception as e:  # noqa: BLE001
                sub, src = None, f"failed: {type(e).__name__}"
        if sub is None or sub.empty:
            status[t] = "missing" if not src.startswith("failed") else src
            continue
        out[t] = drop_partial_day(_bars(sub))
        status[t] = src
    if out:   # never cache an empty result: the next run should try again
        cache.put("eod", k, {"bars": out, "status": status})
    return out, status


def eod(tickers: list[str], period: str = "3mo") -> dict[str, list[dict]]:
    return eod_status(tickers, period)[0]
