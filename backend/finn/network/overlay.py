"""Per-user network overlay: private network x shared snapshot -> app/network.json.

The shared snapshot (filings, results, research) is identical for every user
and can be cached and served from a CDN. The overlay is computed per user,
from their private network, and is never merged back into the snapshot.
It holds display fields only: no notes, no contact details.
"""
from __future__ import annotations

import json
from datetime import date

from ..config import DATA
from . import explain, match
from .model import Network, load

APP = DATA.parent.parent / "app"
EVENT_CATS = {"M&A", "fund_raise", "litigation", "capex"}
EVENT_KEYS = {"press_release/clarification"}


def _initials(name: str) -> str:
    return "".join(w[0] for w in name.split()[:2]).upper()


def _person_card(net: Network, cid: str, R: dict) -> dict:
    c = net.contacts[cid]
    cur = next((s for s in c.career if s.end is None), None)
    months = None
    if c.last_spoke:
        months = (date.fromisoformat(net.as_of) - date.fromisoformat(c.last_spoke)).days // 30
    r = R.get(cid, {})
    return {"name": c.name, "initials": _initials(c.name), "headline": c.headline, "synthetic": True,
            "closeness": c.closeness, "how": c.tie_context or r.get("how", ""), "via": r.get("via"),
            "reach": r.get("strength"), "last_spoke_months": months, "themes": c.themes,
            "now": {"org": net.orgs[cur.org]["name"], "role": cur.role} if cur else None,
            "career": [{"org": net.orgs[s.org]["name"], "role": s.role, "years": match._yrs(s.start, s.end)} for s in c.career],
            "education": [{"inst": net.institutions[d.inst]["name"], "year": d.year} for d in c.education]}


def _target(net: Network, name: str, ticker: str, role: str, use_model: bool) -> dict:
    p = match.resolve(net, name)
    if p is None:
        return {"name": name, "paths": [], "note": "Name in the filing didn't match a known public profile, so no path is computed."}
    r = match.paths_to(net, p)
    for x in r["paths"]:
        if x.get("contact"):
            x["why"] = explain.why(net, x, {"company": ticker, "title": f"{name} named {role}", "category": "management_change"},
                                   explain.template_path(net, x, p), use_model=use_model)
        else:
            x["why"] = {"text": explain.template_path(net, x, p), "by": "template"}
    direct = next((x for x in r["paths"] if x.get("hops") == ["me"]), None)
    if direct is None:
        d = match.pair(net, net.me, p)
        if d:
            direct = {"strength": d.strength, "label": match.label(d.strength), "links": [l.__dict__ for l in d.links],
                      "why": {"text": "Your own link: " + d.links[0].reason[0].lower() + d.links[0].reason[1:] + ".", "by": "template"}}
    return {"name": p.name, "role": role, "ticker": ticker, "hypothetical": True,
            "career": [{"org": net.orgs[s.org]["name"], "role": s.role, "years": match._yrs(s.start, s.end) if s.start else "years not public",
                        "kind": s.kind, "source": s.source} for s in p.career],
            "education": [{"inst": net.institutions[d.inst]["name"], "degree": d.degree, "year": d.year, "source": d.source} for d in p.education],
            "paths": [x for x in r["paths"] if x.get("contact")], "direct": direct, "total": r.get("total", 0), "note": r.get("note")}


def build(snapshot: dict, net: Network | None = None, *, use_model: bool = False) -> dict:
    net = net or load()
    R = match.reach(net)
    names = {p["ticker"]: p["name"] for p in snapshot["portfolio"]}
    names.update({t: r["name"] for t, r in snapshot.get("research", {}).items()})

    events, targets, material = {}, {}, []
    for it in snapshot["items"]:
        if it["tier"] == "noise":
            continue
        e = it.get("enrichment") or {}
        key = f"{it['category']}/{it['subtype']}"
        text = it["title"] + " " + it.get("detail", "")
        if it["category"] == "management_change" and e.get("kind") == "person":
            t = _target(net, e["name"], it["ticker"], e.get("role", ""), use_model)
            targets[t["name"]] = t
            ev = {"kind": "person", "target": t["name"]}
            if not t["paths"]:                 # no way in to the person: fall back to who knows the company
                rows = match.who_to_call(net, it["ticker"], limit=99)
                rows = [r for r in rows if r["relation"] == "ex-employee"] or rows
                if rows:
                    x = rows[0]
                    x["why"] = {"text": explain.template_event(net, x, names[it["ticker"]], "research"), "by": "template"}
                    ev["company_fallback"] = x
            events[it["id"]] = ev
            material.append(it)
        elif it["category"] in EVENT_CATS or key in EVENT_KEYS:
            b = match.best_placed(net, it["ticker"], it["category"], it["subtype"], text)
            for x in filter(None, [b["best"], *b["others"]]):
                x["why"] = explain.why(net, x, {"company": names[it["ticker"]], "title": it["title"], "category": it["category"]},
                                       explain.template_event(net, x, names[it["ticker"]], it["category"]), use_model=use_model)
            events[it["id"]] = {"kind": "event", "event": b["event"], "themes": b["themes"], "best": b["best"], "others": b["others"]}
            material.append(it)

    companies = {}
    for t in sorted(names):
        rows = match.who_to_call(net, t, limit=6)
        for x in rows:
            x["why"] = {"text": explain.template_event(net, x, names[t], "research"), "by": "template"}
        if rows:
            companies[t] = rows

    themes = {}
    for th in net.themes["list"]:
        rows = match.expertise(net, th, limit=4)
        if rows:
            themes[th] = rows

    def covered(ev):
        if ev["kind"] == "person":
            t = targets[ev["target"]]
            return bool(t["paths"]) and t["paths"][0]["label"] != "weak"
        return bool(ev["best"]) and ev["best"]["label"] != "weak"

    nudges = match.reconnect(net, [{"ticker": i["ticker"], "title": i["title"], "id": i["id"]} for i in material])
    used = {x["contact"] for ev in events.values() for x in ([ev.get("best")] + ev.get("others", []) + [ev.get("company_fallback")]) if x}
    used |= {x["contact"] for t in targets.values() for x in t["paths"]}
    used |= {x["contact"] for rows in [*companies.values(), *themes.values()] for x in rows}
    used |= {n["contact"] for n in nudges}
    used |= {R[c]["via"] for c in list(used) if R.get(c, {}).get("via")}

    index = {"people": {}, "orgs": {}, "themes": sorted(themes)}
    for cid in sorted(used):                      # sorted: byte-identical rebuilds
        index["people"][net.contacts[cid].name.lower()] = cid
    for t in targets.values():
        index["people"][t["name"].lower()] = "target:" + t["name"]
    for oid, o in net.orgs.items():
        if oid in names:
            for a in [o["name"], *o.get("aliases", []), oid]:
                index["orgs"][a.lower()] = oid

    return {
        "meta": {"synthetic": True, "owner": net.meta.get("owner"), "as_of": net.as_of,
                 "contacts": len(net.contacts), "why_by": "model" if use_model else "template",
                 "rules": "Deterministic scoring in backend/finn/network/match.py. No model chose or ranked anyone.",
                 "privacy": "Private per-user overlay, never merged into the shared snapshot. Display fields only: no notes or contact details."},
        "me": {"persona": net.me.headline},
        "people": {cid: _person_card(net, cid, R) for cid in sorted(used)},
        "targets": targets,
        "events": events,
        "companies": companies,
        "themes": themes,
        # Every coverage number carries its window. This one spans every material event in the
        # snapshot; the app recomputes per window (24h ... 30 days, "the 7 days to 3 Nov") from "items".
        "coverage": {"material": len(events), "covered": sum(1 for e in events.values() if covered(e)),
                     "window": {"from": min((i["date"] for i in material), default=None),
                                "to": max((i["date"] for i in material), default=None),
                                "label": "all material filings in the snapshot"},
                     "definition": "material = leadership change with a named person, M&A, equity raise, litigation, capex or exchange clarification, "
                                   "not routine; covered = a medium or strong link to someone who can explain it",
                     "items": {k: covered(v) for k, v in events.items()}},
        "reconnect": nudges,
        "index": index,
    }


def _why_counts(ov: dict) -> dict:
    n = {"model": 0, "template": 0}
    def walk(x):
        if isinstance(x, dict):
            if isinstance(x.get("why"), dict) and "by" in x["why"]:
                n[x["why"]["by"]] = n.get(x["why"]["by"], 0) + 1
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk({k: v for k, v in ov.items() if k != "meta"})
    return n


def write(use_model: bool = False) -> str:
    from .. import llm
    llm.METER.reset()
    snap = json.loads((APP / "snapshot.json").read_text(encoding="utf-8"))
    ov = build(snap, use_model=use_model)
    counts = _why_counts(ov)
    ov["meta"]["why_counts"] = counts
    ov["meta"]["why_by"] = "model" if counts.get("model") else "template"
    if use_model:
        m = llm.METER
        # Cached answers carry the token counts metered when they were generated, so a
        # re-run (e.g. after a parsing fix) still reports what producing the lines cost.
        from datetime import date
        ov["meta"]["why_model"] = {"model": llm.MODELS["cheap"]["model"], "run_on": date.today().isoformat(),
                                   "calls": m.calls + m.cached, "new_calls_this_run": m.calls,
                                   "tokens_in": m.tokens_in + m.cached_tokens_in, "tokens_out": m.tokens_out + m.cached_tokens_out,
                                   "usd_list_price": round(m.usd + m.cached_usd, 5), "billed": "$0 on the Gemini free tier"}
    out = APP / "network.json"
    out.write_text(json.dumps(ov, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
    cost = ov["meta"].get("why_model")
    return f"{out}: why-lines {counts}" + (f"; model meter {json.dumps(cost)}" if cost else "")
