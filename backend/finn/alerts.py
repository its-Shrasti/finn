"""Triggered alerts on end-of-day data and on filings.

The investor defines "unusual" (config.AlertRules, editable in the app); this
module only watches. EOD checks run on a daily OHLCV frame per ticker.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .config import AlertRules


@dataclass
class Alert:
    ticker: str
    date: str
    kind: str        # price_move | volume_spike | filing
    severity: int    # 1..3
    text: str
    value: float | None = None
    threshold: float | None = None


def eod_checks(ticker: str, bars: list[dict], rules: AlertRules) -> list[Alert]:
    """bars: [{date, close, volume}] oldest→newest, at least lookback+1 long."""
    r = rules.for_ticker(ticker)
    out: list[Alert] = []
    if len(bars) < 2:
        return out
    last, prev = bars[-1], bars[-2]
    chg = (last["close"] - prev["close"]) / prev["close"] * 100
    if abs(chg) >= r.price_move_pct:
        sev = 3 if abs(chg) >= 2 * r.price_move_pct else 2
        out.append(Alert(ticker, last["date"], "price_move", sev,
                         f"{'Up' if chg > 0 else 'Down'} {abs(chg):.1f}% on the day (your line: ±{r.price_move_pct:g}%)",
                         round(chg, 2), r.price_move_pct))
    window = bars[-(r.volume_lookback + 1):-1]
    if len(window) >= 5:
        avg = sum(b["volume"] for b in window) / len(window)
        if avg > 0:
            mult = last["volume"] / avg
            if mult >= r.volume_multiple:
                sev = 3 if mult >= 1.5 * r.volume_multiple else 2
                out.append(Alert(ticker, last["date"], "volume_spike", sev,
                                 f"Volume {mult:.1f}× its {len(window)}-day average (your line: {r.volume_multiple:g}×)",
                                 round(mult, 2), r.volume_multiple))
    return out


def scan(ticker: str, bars: list[dict], rules: AlertRules, sessions: int = 10) -> list[Alert]:
    """Run eod_checks as of each of the last `sessions` days — the alert log."""
    out: list[Alert] = []
    for i in range(max(2, len(bars) - sessions + 1), len(bars) + 1):
        out += eod_checks(ticker, bars[:i], rules)
    return out


def filing_alerts(items: list, rules: AlertRules) -> list[Alert]:
    out = []
    for it in items:
        if it.category in rules.for_ticker(it.ticker).watch_categories and it.tier == "act":
            out.append(Alert(it.ticker, it.date, "filing", 3 if it.score >= 80 else 2,
                             f"{it.category.replace('_', ' ')}: {it.title}"))
    return out


def to_dicts(alerts: list[Alert]) -> list[dict]:
    return [asdict(a) for a in alerts]
