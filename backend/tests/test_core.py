"""Tests for the deterministic core. No network, no model."""
import json
from pathlib import Path

import pytest

from finn import alerts, classify, priority, results
from finn.config import AlertRules
from finn.research import ledger, metrics

DATA = Path(__file__).resolve().parents[1] / "data" / "inputs"


# ---------- classifier --------------------------------------------------------
@pytest.mark.parametrize("title,cat,sub", [
    ("Unaudited financial results – quarter ended 30 Sep 2026", "results", "filing"),
    ("Newspaper publication of results", "results", "paperwork"),
    ("Investor presentation – Q2 FY27 results", "results", "presentation"),
    ("Change in Managing Director & CEO", "management_change", "top"),
    ("Retirement of Chairman; new Chairman appointed", "management_change", "top"),
    ("Change in senior management – Group Head, Business Banking superannuates", "management_change", "senior"),
    ("QIP – allotment of equity shares", "fund_raise", "equity"),
    ("Allotment of secured NCDs", "fund_raise", "debt"),
    ("Allotment of shares under ESOP/RSU", "unclassified", "esop"),
    ("Earnings call transcript", "earnings_call", "transcript"),
    ("Insolvency (CIRP) matter – NCLAT allows Polycab's appeal", "litigation", "legal"),
    ("Clarification on news item – Regulation 30(11)", "press_release", "clarification"),
    ("Credit rating – ICRA", "credit_rating", "rating"),
    ("Interim dividend declared", "dividend", "dividend"),
    ("Bagging of orders – buildings & factories", "press_release", "orders"),
    ("Production volume – October 2026", "press_release", "operating_data"),
    ("Closure of trading window", "unclassified", "paperwork"),
    ("ESG rating – SES ESG Research", "unclassified", "paperwork"),
    ("Analyst/investor meet – outcome", "investor_meetings", "meet"),
])
def test_classify(title, cat, sub):
    lab = classify.classify(title, use_llm=False)
    assert (lab.category, lab.subtype) == (cat, sub)


def test_all_captured_filings_classify_without_llm():
    raw = json.loads((DATA / "portfolio_raw.json").read_text())
    for c in raw["companies"]:
        for a in c["announcements"]:
            assert classify.classify(a["title"], a["detail"], use_llm=False).rule != "fallthrough", a["title"]


# ---------- prioritisation -----------------------------------------------------
def _item(title, ticker="TCS", detail=""):
    lab = classify.classify(title, detail, use_llm=False)
    return priority.score(priority.Item("x", ticker, "2026-11-01", title, detail, lab.category, lab.subtype))


def test_ceo_change_is_act_and_esop_is_noise():
    assert _item("Change in Managing Director & CEO", "HDFCBANK").tier == "act"
    assert _item("Allotment of shares under ESOP/RSU", "HDFCBANK").tier == "noise"


def test_lender_debt_raise_is_noise_but_qip_is_act():
    assert _item("Allotment of secured NCDs", "BAJFINANCE").tier == "noise"
    assert _item("QIP – allotment of equity shares", "PERSISTENT").tier == "act"


def test_shell_subsidiary_is_not_material_mna():
    it = _item("Incorporation of wholly owned subsidiary in Canada", "TITAN", "CAD 5,000 capital")
    assert it.category == "M&A" and it.tier == "noise"


def test_cluster_merges_results_family():
    items = [_item(t, "RELIANCE") for t in ["Unaudited financial results – quarter ended 30 Sep 2026",
                                              "Investor presentation – Q2 FY27 results", "Earnings call transcript"]]
    out = priority.cluster(items)
    assert len(out) == 1 and out[0].subtype == "filing" and len(out[0].related) == 2


# ---------- results first cut ------------------------------------------------
Q = [{"q": "Sep 2025", "sales": 14478, "ebitda": 4527, "other_income": 470, "dep": 730, "pbt": 4168, "tax_pct": 25, "pat": 3125},
     {"q": "Dec 2025", "sales": 15521, "ebitda": 4948, "other_income": 89, "dep": 732, "pbt": 4227, "tax_pct": 20, "pat": 3381},
     {"q": "Mar 2026", "sales": 14612, "ebitda": 3954, "other_income": 458, "dep": 775, "pbt": 3551, "tax_pct": 23, "pat": 2710},
     {"q": "Jun 2026", "sales": 15300, "ebitda": 4418, "other_income": 520, "dep": 739, "pbt": 4099, "tax_pct": 29, "pat": 2901},
     {"q": "Sep 2026", "sales": 16199, "ebitda": 4744, "other_income": -752, "dep": 758, "pbt": 3159, "tax_pct": 37, "pat": 1995}]


def test_first_cut_math():
    c = results.first_cut("SUNPHARMA", Q)
    rows = {r["metric"]: r for r in c["rows"]}
    assert c["quarter"] == "Sep 2026" and c["year_ago_q"] == "Sep 2025"
    assert rows["Revenue"]["yoy"] == 11.9 and rows["Revenue"]["qoq"] == 5.9
    assert rows["PAT"]["yoy"] == -36.2
    assert rows["EBITDA"]["yoy"] == 4.8


def test_below_the_line_flag_names_the_drivers():
    flags = results.first_cut("SUNPHARMA", Q)["flags"]
    btl = next(f for f in flags if f["kind"] == "below_the_line")
    assert btl["tone"] == "neg" and "other income" in btl["text"] and "tax rate 25%→37%" in btl["text"]


def test_lender_has_no_ebitda():
    q = [{"q": "Sep 2025", "sales": 100, "pbt": 30, "pat": 22, "gnpa": 1.2},
         {"q": "Jun 2026", "sales": 110, "pbt": 31, "pat": 23, "gnpa": 0.96},
         {"q": "Sep 2026", "sales": 120, "pbt": 33, "pat": 25, "gnpa": 1.06}]
    c = results.first_cut("BAJFINANCE", q)
    assert next(r for r in c["rows"] if r["metric"] == "EBITDA")["na"]
    assert any(f["kind"] == "asset_quality" for f in c["flags"])


# ---------- EOD alerts --------------------------------------------------------
def _bars(closes, vols):
    return [{"date": f"2026-10-{i + 1:02d}", "close": c, "volume": v} for i, (c, v) in enumerate(zip(closes, vols))]


def test_price_and_volume_alerts_respect_user_thresholds():
    bars = _bars([100] * 21 + [105], [1000] * 21 + [2500])
    kinds = {a.kind for a in alerts.eod_checks("X", bars, AlertRules(price_move_pct=4, volume_multiple=2))}
    assert kinds == {"price_move", "volume_spike"}
    assert alerts.eod_checks("X", bars, AlertRules(price_move_pct=6, volume_multiple=3)) == []


def test_per_ticker_override():
    bars = _bars([100] * 21 + [103], [1000] * 22)
    rules = AlertRules(price_move_pct=4, per_ticker_overrides={"X": {"price_move_pct": 2}})
    assert [a.kind for a in alerts.eod_checks("X", bars, rules)] == ["price_move"]


# ---------- research ----------------------------------------------------------
def test_metrics_cash_quality_flags_kaynes():
    inp = json.loads((DATA / "research_inputs.json").read_text())["KAYNES"]
    cq = metrics.cash_quality(inp["cf"], inp["annual"])
    assert cq["verdict"] == "weak" and cq["negative_cfo_years"] == 3


def test_ledger_verdicts():
    inp = json.loads((DATA / "research_inputs.json").read_text())
    k = {r["id"]: r["verdict"] for r in ledger.build(inp["KAYNES"]["guidance"], inp["KAYNES"]["calls"])["rows"]}
    assert k["k1"] == "missed" and k["k2"] == "missed" and k["k3"] == "walked_back"
    p = {r["id"]: r["verdict"] for r in ledger.build(inp["POLYCAB"]["guidance"], inp["POLYCAB"]["calls"])["rows"]}
    assert p["g1"] == "beat" and p["g4"] == "pending"
    s = {r["id"]: r["verdict"] for r in ledger.build(inp["PERSISTENT"]["guidance"], inp["PERSISTENT"]["calls"])["rows"]}
    assert s["p1"] == "on_track" and s["p2"] == "at_risk" and s["p5"] == "missed"


def test_results_miss_is_act_but_beat_is_know():
    miss = _item("Unaudited financial results – quarter ended 30 Sep 2026", "SUNPHARMA")
    priority.score(miss, results_surprise=-36.2, results_neg_flag=True)
    beat = _item("Unaudited financial results – quarter ended 30 Sep 2026", "POLYCAB")
    priority.score(beat, results_surprise=42.9)
    assert miss.tier == "act" and beat.tier == "know"
