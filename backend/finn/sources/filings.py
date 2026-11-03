"""Corporate announcements from NSE (primary) and BSE (fallback).

NSE's JSON API needs the cookies its homepage sets, so we warm a session first.
BSE's API needs a Referer. Both return slightly different shapes; we normalise
to {ticker, date, title, detail, url, source}.
"""
from __future__ import annotations

from datetime import date, datetime

import requests

from .. import cache
from ..config import BSE_CODES, USER_AGENT

NSE_HOME = "https://www.nseindia.com"
NSE_API = "https://www.nseindia.com/api/corporate-announcements"
BSE_API = "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"


def _nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*",
                      "Accept-Language": "en-US,en;q=0.9", "Referer": NSE_HOME + "/"})
    s.get(NSE_HOME, timeout=15)
    s.get(NSE_HOME + "/companies-listing/corporate-filings-announcements", timeout=15)
    return s


def nse(ticker: str, frm: date, to: date, session: requests.Session | None = None) -> list[dict]:
    s = session or _nse_session()
    r = s.get(NSE_API, params={"index": "equities", "symbol": ticker,
                               "from_date": frm.strftime("%d-%m-%Y"), "to_date": to.strftime("%d-%m-%Y")}, timeout=20)
    r.raise_for_status()
    rows = r.json()
    rows = rows.get("data", rows) if isinstance(rows, dict) else rows
    out = []
    for x in rows:
        dt = datetime.strptime(x["an_dt"], "%d-%b-%Y %H:%M:%S")
        out.append({"ticker": ticker, "date": dt.date().isoformat(), "time": dt.strftime("%H:%M"),
                    "title": x.get("desc", "").strip(), "detail": (x.get("attchmntText") or "").strip(),
                    "url": x.get("attchmntFile"), "source": "NSE"})
    return out


def bse(ticker: str, frm: date, to: date) -> list[dict]:
    code = BSE_CODES.get(ticker)
    if not code:
        return []
    r = requests.get(BSE_API, params={"pageno": 1, "strCat": -1, "strPrevDate": frm.strftime("%Y%m%d"),
                                      "strScrip": code, "strSearch": "P", "strToDate": to.strftime("%Y%m%d"),
                                      "strType": "C", "subcategory": -1},
                     headers={"User-Agent": USER_AGENT, "Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"},
                     timeout=20)
    r.raise_for_status()
    out = []
    for x in r.json().get("Table", []):
        dt = datetime.fromisoformat(x["NEWS_DT"].split(".")[0])
        att = x.get("ATTACHMENTNAME")
        out.append({"ticker": ticker, "date": dt.date().isoformat(), "time": dt.strftime("%H:%M"),
                    "title": (x.get("NEWSSUB") or x.get("HEADLINE") or "").strip(),
                    "detail": (x.get("HEADLINE") or "").strip(),
                    "url": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att else None,
                    "source": "BSE", "bse_category": x.get("CATEGORYNAME")})
    return out


def announcements(tickers: list[str], frm: date, to: date) -> list[dict]:
    """Fetch for all tickers; NSE first, BSE if NSE fails for a ticker. Cached 30 min."""
    k = cache.key("ann", sorted(tickers), str(frm), str(to))
    hit = cache.get("ann", k, max_age_s=1800)
    if hit:
        return hit
    out: list[dict] = []
    try:
        sess = _nse_session()
    except Exception:
        sess = None
    for t in tickers:
        rows: list[dict] = []
        if sess:
            try:
                rows = nse(t, frm, to, sess)
            except Exception:
                rows = []
        if not rows:
            try:
                rows = bse(t, frm, to)
            except Exception:
                rows = []
        out.extend(rows)
    return cache.put("ann", k, out)
