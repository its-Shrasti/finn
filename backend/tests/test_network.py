"""Network layer: data validation and deterministic matching. No network, no model."""
import copy
from pathlib import Path

import pytest

from finn.network import model
from finn.network.model import NetworkError


@pytest.fixture(scope="module")
def raw():
    return model.raw()


@pytest.fixture(scope="module")
def net(raw):
    return model.build(*copy.deepcopy(raw))


# ---------- step 1: data + validation -----------------------------------------
def test_shipped_network_loads(net):
    assert 30 <= len(net.contacts) <= 45
    assert all(not c.real for c in net.contacts.values())
    assert {"anup bagchi", "bahram vakil", "prithvi vachani"} <= set(net.public)


def test_every_contact_must_be_marked_synthetic(raw):
    n, o, p = copy.deepcopy(raw)
    n["contacts"][0]["synthetic"] = False
    with pytest.raises(NetworkError, match="synthetic"):
        model.build(n, o, p)


def test_real_people_never_get_a_knows_link(raw):
    n, o, p = copy.deepcopy(raw)
    n["contacts"][0]["knows"].append({"id": "pub:anup-bagchi", "strength": 3})
    with pytest.raises(NetworkError, match="real person"):
        model.build(n, o, p)
    n, o, p = copy.deepcopy(raw)
    p["people"]["Anup Bagchi"]["knows"] = [{"id": "c01", "strength": 3}]
    with pytest.raises(NetworkError, match="real person"):
        model.build(n, o, p)


def test_public_career_facts_must_cite_a_source(raw):
    n, o, p = copy.deepcopy(raw)
    del p["people"]["Anup Bagchi"]["career"][1]["source"]
    with pytest.raises(NetworkError, match="source"):
        model.build(n, o, p)


def test_unpublished_years_stay_null(net):
    bagchi = net.public["anup bagchi"]
    assert all(d.year is None for d in bagchi.education)       # graduation years aren't in any cited source
    assert all(s.source for s in bagchi.career)


def test_unknown_org_and_unreachable_contact_fail(raw):
    n, o, p = copy.deepcopy(raw)
    n["contacts"][0]["career"][0]["org"] = "NOPE"
    with pytest.raises(NetworkError, match="unknown org"):
        model.build(n, o, p)
    n, o, p = copy.deepcopy(raw)
    n["contacts"][0]["tie"] = None
    with pytest.raises(NetworkError, match="via"):
        model.build(n, o, p)


def test_name_normalisation_for_filings():
    assert model.norm_name("Mr. Anup  Bagchi") == "anup bagchi"
    assert model.norm_name("SHRI Bahram N. Vakil") == "bahram n vakil"


def test_network_overlaps_the_demo_companies(net):
    """The demo only comes alive where the network overlaps holdings and research names."""
    worked = {s.org for c in net.contacts.values() for s in c.career}
    for org in ["PERSISTENT", "POLYCAB", "KAYNES", "ICICIBANK", "ICICISEC", "ICICIPRU", "AZB", "DIXON", "BAJFINANCE"]:
        assert org in worked, org


# ---------- step 2: matching -----------------------------------------------------
from finn.network import match as M
from finn.network.model import Degree, Stint


def test_overlap_years_and_decay(net):
    a = Stint("ICICIBANK", "Manager", 2016, 2022)
    b = Stint("ICICIBANK", "ED", 2017, 2023)
    l = M.stint_link(net, a, b)
    assert l.kind == "worked_together" and l.facts["overlap_years"] == 5
    # same overlap ten years earlier is worth less
    old = M.stint_link(net, Stint("ICICIBANK", "x", 2006, 2012), Stint("ICICIBANK", "y", 2007, 2013))
    assert old.strength < l.strength


def test_no_overlap_no_link_and_board_seats_never_count(net):
    assert M.stint_link(net, Stint("AZB", "x", 2001, 2003), Stint("AZB", "y", 2004, None)) is None
    assert M.stint_link(net, Stint("TRENT", "Merchandising", 2010, 2019),
                        Stint("TRENT", "Independent director", 2012, 2022, kind="board")) is None


def test_group_overlap_is_weaker_than_same_company(net):
    same = M.stint_link(net, Stint("ICICISEC", "x", 2012, 2016), Stint("ICICISEC", "y", 2011, 2016))
    grp = M.stint_link(net, Stint("ICICIPRU", "x", 2012, 2016), Stint("ICICISEC", "y", 2011, 2016))
    assert grp.kind == "same_group" and grp.strength < same.strength
    # Tata companies are deliberately not one employer
    assert M.stint_link(net, Stint("TCS", "x", 2014, 2016), Stint("TRENT", "y", 2014, 2016)) is None


def test_unknown_years_give_a_weak_unconfirmed_link(net):
    l = M.stint_link(net, Stint("AZB", "x", 2013, 2019), Stint("AZB", "y", None, None))
    assert l.kind == "same_employer" and l.strength == M.YEARS_UNKNOWN and "unconfirmed" in l.reason


def test_batchmates_vs_alumni(net):
    assert M.edu_link(net, Degree("IITK", "B.Tech", 2014), Degree("IITK", "B.Tech", 2014)).kind == "studied_together"
    assert M.edu_link(net, Degree("IITK", "B.Tech", 2014), Degree("IITK", "B.Tech", 2015)).strength == M.BATCHMATE_ADJ
    far = M.edu_link(net, Degree("IITK", "B.Tech", 2014), Degree("IITK", "B.Tech", 1990))
    unknown = M.edu_link(net, Degree("IITK", "B.Tech", 2014), Degree("IITK", "B.Tech", None))
    assert far.kind == unknown.kind == "alumni" and far.strength == M.ALUMNI


def test_bagchi_paths_rank_and_explain(net):
    r = M.paths_to(net, M.resolve(net, "Mr. Anup Bagchi"))
    top = r["paths"][0]
    assert top["contact"] == "c01" and top["label"] == "strong"           # batchmate, 5 yrs at ICICI Bank while he was ED
    assert "ICICI Bank, 2017–2022" in top["links"][0]["reason"] and "Executive Director" in top["links"][0]["reason"]
    assert all(p["hypothetical"] for p in r["paths"])                     # real person: inferred, never 'knows'
    two_hop = next(p for p in r["paths"] if p.get("contact") == "c04")
    assert two_hop["hops"] == ["me", "c05", "c04"]                        # you -> Pranav -> Ritu (ICICI Pru 2023–26)
    strengths = [p["strength"] for p in r["paths"]]
    assert strengths == sorted(strengths, reverse=True)


def test_your_own_link_to_bagchi_is_weak_alumni(net):
    p = M.pair(net, net.me, M.resolve(net, "Anup Bagchi"))
    assert p.links[0].kind == "alumni" and p.strength == M.ALUMNI


def test_vakil_link_ignores_board_seat_and_uses_azb(net):
    r = M.paths_to(net, M.resolve(net, "Bahram Vakil"))
    assert [p["contact"] for p in r["paths"]] == ["c19"]                 # not c26 (ex-Trent): his Trent seat was a board seat
    assert "AZB & Partners, 2013–2019" in r["paths"][0]["links"][0]["reason"]


def test_person_without_public_history_gets_no_path(net):
    r = M.paths_to(net, M.resolve(net, "Prithvi Vachani"))
    assert r["paths"] == [] and "cannot compute" in r["note"]


def test_ambiguous_or_unknown_name_resolves_to_nobody(net):
    assert M.resolve(net, "Anup") is None
    assert M.resolve(net, "Someone Else") is None


def test_who_to_call_relations_and_compliance_flag(net):
    rows = {x["contact"]: x for x in M.who_to_call(net, "PERSISTENT", limit=99)}
    assert rows["c20"]["relation"] == "ex-employee" and rows["c20"]["flag"] is None
    assert rows["c22"]["relation"] == "competitor"
    assert rows["c23"]["relation"] == "customer side" and rows["c23"]["via"] == "c20"
    assert rows["c21"]["relation"] == "current employee" and "unpublished" in rows["c21"]["flag"]
    assert M.who_to_call(net, "PERSISTENT")[0]["contact"] == "c20"
    # group subsidiary counts as the company for compliance (Reliance Retail -> Reliance)
    assert M.compliance(net, net.contacts["c26"], "RELIANCE")


def test_best_placed_weighs_expertise_by_event(net):
    lit = M.best_placed(net, "POLYCAB", "litigation", "legal", "Insolvency (CIRP) matter – NCLAT allows Polycab's appeal")
    assert lit["event"] == "litigation/insolvency" and lit["best"]["contact"] == "c19"      # the insolvency lawyer
    deal = M.best_placed(net, "KAYNES", "M&A", "deal")
    assert deal["best"]["contact"] == "c11"                                                 # ex-Kaynes corp dev
    clar = M.best_placed(net, "SUNPHARMA", "press_release", "clarification")
    assert clar["best"]["contact"] == "c27" and clar["weight"] == 0.0


def test_current_employees_are_demoted_and_bankers_cautioned(net):
    b = M.best_placed(net, "BAJFINANCE", "fund_raise", "equity", limit=9)
    rows = {x["contact"]: x for x in [b["best"], *b["others"]]}
    assert rows["c07"]["flag"] and rows["c07"]["score"] < rows["c09"]["score"]           # Arjun works there now
    assert "mandates" in rows["c08"]["flag"]                                             # ECM banker on a fund raise


def test_expertise_map(net):
    semi = M.expertise(net, "semiconductors")
    assert [x["contact"] for x in semi][:2] == ["c11", "c13"]
    assert M.expertise(net, "insolvency & restructuring")[0]["contact"] == "c19"


def test_matching_is_deterministic(net):
    a = M.paths_to(net, M.resolve(net, "Anup Bagchi"))
    b = M.paths_to(net, M.resolve(net, "Anup Bagchi"))
    assert a == b
    assert M.who_to_call(net, "KAYNES") == M.who_to_call(net, "KAYNES")


def test_stale_tie_cools(net):
    import dataclasses
    c = dataclasses.replace(net.contacts["c01"], last_spoke="2024-01-01")
    assert M.tie(net, c) == round(M.TIE[3] * M.STALE_FACTOR, 3)


def test_reconnect_nudge(net):
    nudges = M.reconnect(net, [{"ticker": "DIXON", "title": "Executive Director appointed", "id": "x"}])
    assert nudges and nudges[0]["contact"] == "c12"                      # ex-Dixon, last spoke Apr 2025


# ---------- step 3: overlay, why-lines, privacy --------------------------------------
import json as _json

from finn import cache, llm
from finn.network import explain, overlay

SNAP = Path(__file__).resolve().parents[2] / "app" / "snapshot.json"


@pytest.fixture(scope="module")
def snap():
    # The replayed demo snapshot, rebuilt in memory: deterministic whatever app/snapshot.json holds
    # (after a live run it holds today's real filings, so ids and counts differ).
    from finn import snapshot
    return _json.loads(_json.dumps(snapshot.build(live=False), default=str))


@pytest.fixture(scope="module")
def ov(snap, net):
    return overlay.build(snap, net)


def test_model_payload_is_pseudonymised_and_minimal(net):
    b = M.best_placed(net, "KAYNES", "M&A", "deal")["best"]
    c = net.contacts[b["contact"]]
    payload = _json.dumps(explain.model_payload(net, b, {"company": "Kaynes Technology", "title": "Acquisition", "category": "M&A"}))
    assert explain.PLACEHOLDER in payload
    for secret in [c.name, c.name.split()[0], c.tie_context, c.last_spoke, "IIM", "batchmate"]:
        assert secret not in payload, secret
    other = next(s for s in c.career if s.org != "KAYNES")                 # the stint that didn't match stays out
    assert net.orgs[other.org]["name"] not in payload


def test_model_why_uses_private_cache_and_puts_the_name_back_locally(net, monkeypatch, tmp_path):
    seen = {}

    def fake(provider, model, system, prompt, json_mode):
        seen["prompt"] = prompt
        return "Contact A ran corporate development there; ask how they integrate deals.", 10, 10
    monkeypatch.setattr(llm, "_dispatch", fake)
    monkeypatch.setattr(cache, "CACHE_DIR", tmp_path)
    b = M.best_placed(net, "KAYNES", "M&A", "deal")["best"]
    out = explain.why(net, b, {"company": "Kaynes", "title": "Acquisition", "category": "M&A"}, "tmpl", use_model=True, owner="u1")
    assert out["by"] == "model" and out["text"].startswith("Divya ")
    assert "Divya" not in seen["prompt"]
    assert list((tmp_path / "private" / "u1" / "why").glob("*.json")) and not (tmp_path / "llm").exists()


def test_model_failure_falls_back_to_the_labelled_template(net, monkeypatch):
    def boom(*a, **k):
        raise llm.LLMUnavailable("no key")
    monkeypatch.setattr(llm, "_dispatch", boom)
    monkeypatch.setattr(cache, "get", lambda *a, **k: None)
    b = M.best_placed(net, "KAYNES", "M&A", "deal")["best"]
    assert explain.why(net, b, {}, "tmpl", use_model=True) == {"text": "tmpl", "by": "template"}


def test_overlay_is_separate_and_holds_no_private_notes(ov, snap, net):
    import re
    blob = re.sub(r"https?://[^\"]+", "", _json.dumps(ov))                              # source URLs carry dates
    assert "last_spoke\"" not in blob
    assert all(c.notes not in blob for c in net.contacts.values() if c.notes)
    assert all(c.last_spoke not in blob for c in net.contacts.values() if c.last_spoke)   # only "N months ago"
    snap_blob = SNAP.read_text(encoding="utf-8")
    assert not any(c.name in snap_blob for c in net.contacts.values())                  # shared snapshot stays network-free
    assert ov["meta"]["synthetic"] is True and ov["meta"]["why_by"] == "template"
    assert all(p["synthetic"] for p in ov["people"].values())


def test_overlay_covers_the_demo_events(ov):
    ev = ov["events"]
    hdfc = ov["targets"]["Anup Bagchi"]
    assert ev["HDFCBANK-2026-10-27-2"]["kind"] == "person" and hdfc["paths"][0]["contact"] == "c01"
    assert hdfc["direct"]["links"][0]["kind"] == "alumni"
    assert ev["KAYNES-2026-11-02-0"]["best"]["contact"] == "c11"
    assert ev["POLYCAB-2026-10-30-0"]["best"]["contact"] == "c19"
    dixon = ev["DIXON-2026-10-30-4"]
    assert ov["targets"]["Prithvi Vachani"]["paths"] == [] and dixon["company_fallback"]["contact"] == "c12"
    assert all(x["why"]["by"] == "template" for e in ev.values() if e["kind"] == "event" for x in [e["best"], *e["others"]])
    assert ov["coverage"]["covered"] <= ov["coverage"]["material"]


def test_reconnect_never_nudges_toward_current_insiders(ov, net):
    for n in ov["reconnect"]:
        assert not M.compliance(net, net.contacts[n["contact"]], n["ticker"])


# ---------- onboarding: LinkedIn's official export --------------------------------------
from finn.network import importer

SAMPLE = Path(__file__).parent / "fixtures" / "Connections_sample.csv"


@pytest.fixture(scope="module")
def imported(raw):
    n, o, _ = raw
    return importer.to_contacts(SAMPLE.read_text(encoding="utf-8-sig"), o, n["contacts"], synthetic=True)


def test_linkedin_import_reads_past_the_notes_preamble(imported):
    names = [c["name"] for c in imported["contacts"]]
    assert names == ["Kabir Arora", "Leela Fernandes", "Omar Siddiqui", "Tara Menon"]
    assert imported["skipped"] == 2                                       # Vikram is already in the network; blank row


def test_linkedin_import_drops_url_and_email(imported):
    blob = _json.dumps(imported)
    assert "linkedin.com/in" not in blob and "@example.com" not in blob


def test_linkedin_import_resolves_companies_without_guessing(imported):
    c = {x["name"]: x for x in imported["contacts"]}
    assert c["Kabir Arora"]["career"] == [{"org": "ICICISEC", "role": "Vice President - Equity Sales", "from": None, "to": None}]
    assert c["Leela Fernandes"]["career"][0]["org"] == "KAYNES"           # "... India Limited" stripped
    assert c["Omar Siddiqui"]["career"] == [] and c["Omar Siddiqui"]["unresolved_company"] == "Bluefin Robotics"
    assert imported["needs_review"] == [c["Omar Siddiqui"]["id"]]
    assert c["Tara Menon"]["career"] == [] and c["Tara Menon"]["tie"]["context"] == "LinkedIn connection"


def test_linkedin_import_invents_no_years_or_closeness(imported):
    for c in imported["contacts"]:
        assert c["tie"]["closeness"] == 1 and c["tie"]["last_spoke"] is None   # connected-on is not last-spoke
        assert all(s["from"] is None for s in c["career"])
    assert imported["contacts"][0]["tie"]["context"] == "LinkedIn connection since 2019-03"


def test_imported_contacts_load_and_match_as_unconfirmed(raw, imported):
    n, o, p = copy.deepcopy(raw)
    n["contacts"] += imported["contacts"]
    net2 = model.build(n, o, p)
    kabir = next(c for c in net2.contacts.values() if c.name == "Kabir Arora")
    link = M.pair(net2, kabir, M.resolve(net2, "Anup Bagchi"))
    assert link.links[0].kind == "same_employer" and "unconfirmed" in link.links[0].reason   # no join year in the export
    assert M.compliance(net2, next(c for c in net2.contacts.values() if c.name == "Leela Fernandes"), "KAYNES")


def test_real_imports_need_an_explicit_switch(raw, imported):
    n, o, p = copy.deepcopy(raw)
    real = [dict(c, synthetic=False) for c in imported["contacts"]]
    n["contacts"] += real
    with pytest.raises(NetworkError, match="synthetic"):
        model.build(n, o, p)
    assert len(model.build(n, o, p, allow_real=True).contacts) == len(raw[0]["contacts"]) + len(real)


def test_not_a_connections_file_is_rejected():
    with pytest.raises(ValueError, match="First Name"):
        importer.read_rows("Name,Phone\nA,1\n")


def test_coverage_states_its_window(ov, snap):
    w = ov["coverage"]["window"]
    dates = {i["id"]: i["date"] for i in snap["items"]}
    assert w["from"] == min(dates[k] for k in ov["coverage"]["items"]) == "2026-10-15"
    assert w["to"] == max(dates[k] for k in ov["coverage"]["items"]) == "2026-11-02"
    assert (ov["coverage"]["covered"], ov["coverage"]["material"]) == (9, 10)       # the figures the README quotes
    week = [k for k in ov["coverage"]["items"] if dates[k] >= "2026-10-27"]           # the 7 days to 3 Nov
    assert (sum(ov["coverage"]["items"][k] for k in week), len(week)) == (7, 8)
