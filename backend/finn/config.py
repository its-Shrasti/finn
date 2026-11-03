"""Central configuration: portfolio, alert defaults, model routing.

Everything a user would tune lives here (or in env vars), so the engine itself
never hard-codes a ticker.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CACHE_DIR = Path(os.getenv("FINN_CACHE", DATA / "cache"))

# ---- Portfolio (mock) ------------------------------------------------------
# weight = % of portfolio. Used to break ties in prioritisation: the same news
# matters more on a 9% position than on a 3% one.
PORTFOLIO: dict[str, float] = {
    "RELIANCE": 9, "HDFCBANK": 9, "TCS": 8, "BAJFINANCE": 7, "LT": 7,
    "SUNPHARMA": 6, "MARUTI": 6, "TITAN": 6, "NTPC": 6, "POLYCAB": 6,
    "ASIANPAINT": 5, "TRENT": 5, "PERSISTENT": 5, "DIXON": 4, "KAYNES": 3,
}

# BSE scrip codes — needed only for the BSE fallback feed.
BSE_CODES: dict[str, str] = {
    "RELIANCE": "500325", "HDFCBANK": "500180", "TCS": "532540", "BAJFINANCE": "500034",
    "LT": "500510", "SUNPHARMA": "524715", "MARUTI": "532500", "TITAN": "500114",
    "NTPC": "532555", "POLYCAB": "542652", "ASIANPAINT": "500820", "TRENT": "500251",
    "PERSISTENT": "533179", "DIXON": "540699", "KAYNES": "543664",
}

# Lenders: EBITDA is meaningless for a bank/NBFC (interest is their cost of goods).
LENDERS = {"HDFCBANK", "BAJFINANCE", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK", "BAJAJFINSV", "CHOLAFIN"}


# ---- Alert rules (user-editable in the app) --------------------------------
@dataclass
class AlertRules:
    price_move_pct: float = 4.0          # |1-day close-to-close move| >= this
    volume_multiple: float = 2.0         # today's volume >= N x trailing average
    volume_lookback: int = 20            # trailing window (sessions)
    gap_from_52w_high_pct: float | None = None  # optional: within X% of 52w high
    watch_categories: set[str] = field(default_factory=lambda: {
        "results", "M&A", "management_change", "fund_raise", "litigation",
        "credit_rating", "pledging", "insider_trading",
    })
    per_ticker_overrides: dict[str, dict] = field(default_factory=dict)

    def for_ticker(self, t: str) -> "AlertRules":
        o = self.per_ticker_overrides.get(t)
        if not o:
            return self
        return AlertRules(**{**self.__dict__, **o, "per_ticker_overrides": {}})


# ---- Model routing ----------------------------------------------------------
# Two tiers. "cheap" does bulk extraction (classify a filing, pull guidance
# sentences out of a 40-page transcript chunk). "strong" only runs the final
# reasoning step (promise-vs-delivery verdicts, bull/bear). Any OpenAI-compatible
# endpoint works; defaults are free tiers.
MODELS = {
    "cheap": {
        "provider": os.getenv("FINN_CHEAP_PROVIDER", "gemini"),
        "model": os.getenv("FINN_CHEAP_MODEL", "gemini-2.5-flash-lite"),
    },
    "strong": {
        "provider": os.getenv("FINN_STRONG_PROVIDER", "gemini"),
        "model": os.getenv("FINN_STRONG_MODEL", "gemini-2.5-pro"),
    },
}

# Rough public list prices (USD per 1M tokens: input, output) for cost reporting.
PRICES = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.0),
    "llama-3.1-8b-instant": (0.05, 0.08),
    "llama-3.3-70b-versatile": (0.59, 0.79),
}

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
