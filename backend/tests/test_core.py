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
    raw = json.loads((DATA / "portfolio_raw.json").read_text(encoding="utf-8"))
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
    inp = json.loads((DATA / "research_inputs.json").read_text(encoding="utf-8"))["KAYNES"]
    cq = metrics.cash_quality(inp["cf"], inp["annual"])
    assert cq["verdict"] == "weak" and cq["negative_cfo_years"] == 3


def test_ledger_verdicts():
    inp = json.loads((DATA / "research_inputs.json").read_text(encoding="utf-8"))
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


def test_partial_day_price_bar_is_dropped_until_after_the_close():
    from datetime import datetime, timedelta, timezone
    from finn.sources.prices import drop_partial_day
    ist = timezone(timedelta(hours=5, minutes=30))
    bars = [{"date": "2026-11-13", "close": 1, "volume": 1}, {"date": "2026-11-16", "close": 2, "volume": 1}]
    assert drop_partial_day(bars, datetime(2026, 11, 16, 11, 0, tzinfo=ist))[-1]["date"] == "2026-11-13"   # market open
    assert drop_partial_day(bars, datetime(2026, 11, 16, 17, 0, tzinfo=ist))[-1]["date"] == "2026-11-16"   # after close
    assert drop_partial_day(bars, datetime(2026, 11, 17, 11, 0, tzinfo=ist))[-1]["date"] == "2026-11-16"   # yesterday's bar is final


def test_model_list_replies_become_objects(monkeypatch):
    from finn import enrich, llm
    assert llm.as_obj([{"a": 1}, {"a": 2}]) == {"a": 1} and llm.as_obj("x") == {} and llm.as_obj({"b": 2}) == {"b": 2}
    # a filing that appoints several people: the model answers with a list; keep the most senior role
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: [
        {"incoming": "Asha Rao", "role": "Independent Director", "outgoing": None},
        {"incoming": "Vikas Mehta", "role": "Chief Executive Officer", "outgoing": "R Iyer"}])
    p = enrich.incoming("Board meeting outcome", "The Board approved appointments effective 1 December 2026.")
    assert p["name"] == "Vikas Mehta" and p["role"] == "Chief Executive Officer" and p["outgoing"] == "R Iyer"
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: [])
    assert enrich.incoming("Board meeting outcome", "Approved the dividend.") is None


def test_names_from_filings_lose_honorifics():
    from finn import enrich
    p = enrich.incoming("Appointment", "Appointment of Mr Shri Diwakar Nath Misra as Non-Executive Director of the company")
    assert p["name"] == "Diwakar Nath Misra"
    assert enrich._clean("Smt.  Asha  Rao") == "Asha Rao"


def test_person_leaving_is_never_taken_as_appointed():
    from finn import enrich
    assert enrich.incoming("Retirement", "Retirement of Mr. Noel N Tata as the Chairman", max_chars=0) is None or \
        enrich.incoming("Retirement", "Retirement of Mr. Noel N Tata as the Chairman")["name"] != "Noel N Tata"
    p = enrich.incoming("x", "Mr. Noel Tata retires; the Board appointed Mr. Bahram Vakil as Chairman with effect from 12 November 2026")
    assert p["name"] == "Bahram Vakil" and p["role"] == "Chairman"


def test_zipped_pdf_attachment_is_unpacked(monkeypatch, tmp_path):
    import io, zipfile
    from pypdf import PdfWriter
    from finn import cache
    from finn.sources import documents
    pdf = io.BytesIO(); w = PdfWriter(); w.add_blank_page(width=200, height=200); w.write(pdf)
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("filing.pdf", pdf.getvalue())

    class R:
        content = z.getvalue()
        def raise_for_status(self): pass
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(documents.requests, "get", lambda *a, **k: R())
    assert documents.text("https://nsearchives.nseindia.com/x.zip", max_pages=2).startswith("[p1]")


def test_board_outcome_with_several_decisions_picks_the_real_appointment(monkeypatch):
    """Real wording from Trent's 29 Oct 2026 filing and HDFC Bank's 27 Oct 2026 filing."""
    from finn import enrich, llm
    monkeypatch.setattr(llm, "complete_json", lambda *a, **k: (_ for _ in ()).throw(llm.LLMUnavailable("offline")))
    trent = ("1. Retirement of Mr. Noel N. Tata as the Chairman ... Mr. Noel N. Tata (DIN: 00024713) will retire from the office "
             "of Chairman and Director ... the Board has appointed Mr. Noel N. Tata as Chairman Emeritus with effect from 12th "
             "November 2026. 2. Appointment of Mr. Bahram Vakil as the Chairman Consequent to the above, the Board has appointed "
             "Mr. Bahram Vakil (DIN: 00283980) as Chairman of the Company. 3. Appointment of Mr. Rajiv Dube as an Additional Director")
    p = enrich.incoming("Change in Directors", trent)
    assert (p["name"], p["role"], p["outgoing"]) == ("Bahram Vakil", "Chairman", "Noel N. Tata")
    hdfc = ("Mr. Sashidhar Jagdishan has vacated the office as the MD & CEO of the Bank from the close of business hours on "
            "October 26, 2026 on account of completion of his tenure and Mr. Anup Bagchi has assumed charge as MD & CEO of the Bank")
    p = enrich.incoming("Change in MD & CEO", hdfc)
    assert (p["name"], p["role"], p["outgoing"]) == ("Anup Bagchi", "MD & CEO", "Sashidhar Jagdishan")
    assert enrich.incoming("Retirement", "Mr. Noel N. Tata will retire from the office of Non- Executive Director and Vice-Chairman") is None


def test_model_bio_needs_a_real_source():
    from finn.enrich import vet_bio
    bio = {"name": "A B", "bio": "Ran things.", "previous": ["X"], "sources": []}
    assert "bio" not in vet_bio(bio) and vet_bio(bio)["not_found"]
    made_up = {**bio, "sources": ["https://invented.example/a"]}
    assert "bio" not in vet_bio(made_up, {"https://real.example/p"})
    ok = {**bio, "sources": ["https://real.example/p", "https://invented.example/a"]}
    assert vet_bio(ok, {"https://real.example/p"})["sources"] == ["https://real.example/p"]
