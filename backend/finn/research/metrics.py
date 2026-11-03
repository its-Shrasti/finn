"""Deterministic research metrics. Every number in the research view comes
from here, computed from primary-source tables — never from a model."""
from __future__ import annotations


def cagr(a: float, b: float, years: float) -> float | None:
    if not a or a <= 0 or b <= 0 or years <= 0:
        return None
    return round(((b / a) ** (1 / years) - 1) * 100, 1)


def margin(x: dict, num: str = "ebitda", den: str = "sales") -> float | None:
    return round(x[num] / x[den] * 100, 1) if x.get(den) and x.get(num) is not None else None


def trajectory(annual: list[dict]) -> dict:
    yrs = [a for a in annual if a["y"] != "TTM"]
    if len(yrs) < 2:
        return {}
    a, b = yrs[0], yrs[-1]
    n = len(yrs) - 1
    margins = [{"y": x["y"], "ebitda_margin": margin(x), "pat_margin": margin(x, "pat")} for x in annual]
    m0, m1 = margins[0]["ebitda_margin"], margins[len(yrs) - 1]["ebitda_margin"]
    return {
        "span": f"{a['y']}–{b['y']}",
        "sales_cagr": cagr(a["sales"], b["sales"], n),
        "ebitda_cagr": cagr(a["ebitda"], b["ebitda"], n),
        "pat_cagr": cagr(a["pat"], b["pat"], n),
        "margins": margins,
        "margin_change_pp": round(m1 - m0, 1) if m0 is not None and m1 is not None else None,
        "operating_leverage": (cagr(a["ebitda"], b["ebitda"], n) or 0) > (cagr(a["sales"], b["sales"], n) or 0),
    }


def quarterly_trend(quarters: list[dict]) -> list[dict]:
    out = []
    for i, q in enumerate(quarters):
        ya = quarters[i - 4] if i >= 4 else None
        out.append({"q": q["q"], "sales": q["sales"], "ebitda_margin": margin(q), "pat": q.get("pat"),
                    "sales_yoy": round((q["sales"] / ya["sales"] - 1) * 100, 1) if ya else None})
    return out


def balance_sheet(bs: list[dict], annual: list[dict]) -> dict:
    last = bs[-1]
    ann = {a["y"]: a for a in annual}
    rows = []
    for b in bs:
        e = ann.get(b["y"], {}).get("ebitda")
        rows.append({"y": b["y"], "debt": b["borrowings"], "equity": b["equity"],
                     "de": round(b["borrowings"] / b["equity"], 2) if b["equity"] else None,
                     "debt_to_ebitda": round(b["borrowings"] / e, 2) if e else None})
    return {"rows": rows, "latest_de": rows[-1]["de"], "latest_debt": last["borrowings"],
            "debt_cagr": cagr(bs[0]["borrowings"], last["borrowings"], len(bs) - 1),
            "equity_cagr": cagr(bs[0]["equity"], last["equity"], len(bs) - 1)}


def cash_quality(cf: list[dict], annual: list[dict]) -> dict:
    ann = {a["y"]: a for a in annual}
    rows, cum_cfo, cum_pat, cum_fcf = [], 0.0, 0.0, 0.0
    for c in cf:
        pat = ann.get(c["y"], {}).get("pat")
        fcf = c["cfo"] + c["cfi"]  # proxy: cfi includes capex plus investments
        rows.append({"y": c["y"], "cfo": c["cfo"], "pat": pat, "fcf_proxy": round(fcf),
                     "cfo_to_pat": round(c["cfo"] / pat * 100) if pat else None})
        cum_cfo += c["cfo"]; cum_pat += pat or 0; cum_fcf += fcf
    conv = round(cum_cfo / cum_pat * 100) if cum_pat else None
    verdict = "strong" if conv and conv >= 90 else "adequate" if conv and conv >= 60 else "weak"
    neg_years = sum(1 for r in rows if r["cfo"] < 0)
    return {"rows": rows, "cum_cfo_to_pat": conv, "cum_fcf_proxy": round(cum_fcf), "verdict": verdict,
            "negative_cfo_years": neg_years, "external_funding": round(sum(c["cff"] for c in cf if c["cff"] > 0))}


def working_capital(wc: list[dict]) -> dict:
    if not wc:
        return {}
    a, b = wc[0], wc[-1]
    return {"rows": wc, "ccc_change": (b.get("ccc") or 0) - (a.get("ccc") or 0),
            "debtor_change": (b.get("debtor_days") or 0) - (a.get("debtor_days") or 0)}
