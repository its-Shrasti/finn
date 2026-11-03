"""screener.in company page → structured fundamentals + document links.

One page gives: quarterly results, 10-12y P&L, balance sheet, cash flow,
ratios (debtor/inventory/payable days, ROCE), shareholding, and links to
annual reports and concall transcripts. Works for any listed ticker, which is
what makes research generalise beyond the demo set.
"""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup

from .. import cache
from ..config import USER_AGENT

BASE = "https://www.screener.in/company/{t}/{v}"

ROW_MAP = {  # screener label (normalised) -> our key
    "sales": "sales", "revenue": "sales", "operating profit": "ebitda", "financing profit": "fin_profit",
    "other income": "other_income", "interest": "interest", "depreciation": "dep",
    "profit before tax": "pbt", "tax %": "tax_pct", "net profit": "pat", "eps in rs": "eps",
    "gross npa %": "gnpa", "net npa %": "nnpa", "borrowings": "borrowings", "reserves": "reserves",
    "equity capital": "equity_capital", "total assets": "total_assets",
    "cash from operating activity": "cfo", "cash from investing activity": "cfi",
    "cash from financing activity": "cff", "debtor days": "debtor_days", "inventory days": "inventory_days",
    "days payable": "payable_days", "cash conversion cycle": "ccc", "working capital days": "wc_days",
    "roce %": "roce", "promoters": "promoters", "fiis": "fii", "diis": "dii", "public": "public",
}


def _num(s: str) -> float | None:
    s = s.strip().replace(",", "").replace("%", "")
    try:
        return float(s)
    except ValueError:
        return None


def _table(soup: BeautifulSoup, section_id: str) -> list[dict]:
    sec = soup.find("section", id=section_id)
    if not sec:
        return []
    tbl = sec.find("table", class_="data-table")
    if not tbl:
        return []
    heads = [th.get_text(strip=True) for th in tbl.find("thead").find_all("th")][1:]
    cols: list[dict] = [{"q": h} for h in heads]
    for tr in tbl.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        label = re.sub(r"[\s+]+$", "", tds[0].get_text(" ", strip=True)).lower().rstrip(" +").strip()
        key = ROW_MAP.get(label)
        if not key:
            continue
        for i, td in enumerate(tds[1:len(cols) + 1]):
            cols[i][key] = _num(td.get_text(strip=True))
    return cols


def _docs(soup: BeautifulSoup) -> dict:
    out = {"annual_reports": [], "concalls": [], "announcements": []}
    sec = soup.find("section", id="documents")
    if not sec:
        return out
    for a in sec.select(".annual-reports a"):
        out["annual_reports"].append({"label": a.get_text(" ", strip=True)[:40], "url": a.get("href")})
    for li in sec.select(".concalls li"):
        date = li.find("div")
        tr = next((a for a in li.find_all("a") if "transcript" in a.get_text(strip=True).lower()), None)
        if tr:
            out["concalls"].append({"date": date.get_text(strip=True) if date else "", "url": tr.get("href")})
    for a in sec.select(".announcements a")[:20]:
        out["announcements"].append({"title": a.get_text(" ", strip=True)[:200], "url": a.get("href")})
    return out


def company(ticker: str, consolidated: bool = True) -> dict:
    k = cache.key("screener", ticker, consolidated)
    hit = cache.get("screener", k, max_age_s=12 * 3600)
    if hit:
        return hit
    url = BASE.format(t=ticker.upper(), v="consolidated/" if consolidated else "")
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
    if r.status_code == 404 and consolidated:
        return company(ticker, consolidated=False)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    name = soup.find("h1").get_text(strip=True) if soup.find("h1") else ticker
    about = soup.select_one(".company-profile .about p")
    top = {li.find("span", class_="name").get_text(strip=True).lower(): _num(li.find("span", class_="number").get_text())
           for li in soup.select("#top-ratios li") if li.find("span", class_="number")}
    quarters = _table(soup, "quarters")
    # a consolidated page with no quarterly data means the company only reports standalone
    if consolidated and not any(q.get("sales") for q in quarters):
        return company(ticker, consolidated=False)
    data = {
        "ticker": ticker.upper(), "name": name, "basis": "consolidated" if consolidated else "standalone",
        "about": about.get_text(" ", strip=True) if about else "",
        "price": top.get("current price"), "mcap": top.get("market cap"), "pe": top.get("stock p/e"),
        "roce": top.get("roce"),
        "quarters": quarters, "annual": _table(soup, "profit-loss"), "bs": _table(soup, "balance-sheet"),
        "cf": _table(soup, "cash-flow"), "wc": _table(soup, "ratios"),
        "shareholding": (_table(soup, "shareholding") or [{}])[-1], "docs": _docs(soup), "url": url,
    }
    for b in data["bs"]:
        if b.get("reserves") is not None:
            b["equity"] = (b.get("reserves") or 0) + (b.get("equity_capital") or 0)
    return cache.put("screener", k, data)
