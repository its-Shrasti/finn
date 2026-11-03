"""End-of-day price & volume via yfinance (.NS, falling back to .BO)."""
from __future__ import annotations

from .. import cache


def eod(tickers: list[str], period: str = "3mo") -> dict[str, list[dict]]:
    import yfinance as yf  # imported lazily: optional dependency

    k = cache.key("eod", sorted(tickers), period)
    hit = cache.get("eod", k, max_age_s=6 * 3600)  # EOD data: refresh a few times a day
    if hit:
        return hit
    out: dict[str, list[dict]] = {}
    syms = [f"{t}.NS" for t in tickers]
    df = yf.download(syms, period=period, interval="1d", group_by="ticker",
                     auto_adjust=False, progress=False, threads=True)
    for t, s in zip(tickers, syms):
        try:
            sub = df[s].dropna(subset=["Close"])
        except KeyError:
            sub = None
        if sub is None or sub.empty:  # BSE fallback
            sub = yf.download(f"{t}.BO", period=period, interval="1d", auto_adjust=False, progress=False)
            sub = sub.dropna(subset=["Close"]) if sub is not None else None
        if sub is None or sub.empty:
            continue
        out[t] = [{"date": str(ix.date()), "close": round(float(r["Close"]), 2), "volume": int(r["Volume"])}
                  for ix, r in sub.iterrows()]
    return cache.put("eod", k, out)
