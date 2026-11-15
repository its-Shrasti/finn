"""Deterministic, explainable matching. No model anywhere in this file.

Edges between two people come from evidence only:
  worked_together   same employer, overlapping years       (board seats never count)
  same_group        sister companies in one financial group, overlapping years
  same_employer     same employer but a year is not public  (weak, says so)
  studied_together  same institution, batch within a year
  alumni            same institution, other or unknown years (weak)
  knows             a link the investor entered between two contacts
  tie               the investor's own relationship with a contact (1–3, decays if stale)

Several independent links between the same two people combine by noisy-OR,
so two weak reasons beat one but never add up to certainty. A path's strength
is the product of its hops. Every number below is a rule, not a fit; they
are kept in one place so they can be tuned and tested.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date

from .model import Network, Person, Stint, norm_name

# ---- scoring rules ------------------------------------------------------------
TIE = {1: 0.45, 2: 0.70, 3: 0.90}          # how well you know a contact
KNOWS = {1: 0.35, 2: 0.60, 3: 0.85}        # how well one contact knows another
STALE_DAYS, STALE_FACTOR = 365, 0.85       # haven't spoken in a year: the tie is a bit colder
SAME_ORG_BASE, PER_YEAR, YEAR_CAP, SAME_UNIT = 0.50, 0.08, 4, 0.10
DECAY_PER_YEAR, DECAY_CAP = 0.03, 0.20     # an overlap 10 years ago counts for less
GROUP_BASE, GROUP_PER_YEAR, GROUP_DECAY_CAP = 0.25, 0.04, 0.15
YEARS_UNKNOWN = 0.20
BATCHMATE_SAME, BATCHMATE_ADJ, ALUMNI = 0.65, 0.50, 0.15
STRONG, MEDIUM = 0.50, 0.30

# who-to-call relevance by relation to the company
EX_EMPLOYEE = 0.85
CURRENT_EMPLOYEE = 0.60
COMPETITOR_NOW, COMPETITOR_EX = 0.60, 0.45
VALUE_CHAIN = 0.50
THEME_FIT = 0.10
THEME_EXPERT = 0.70
COMPLIANCE_DEMOTE = 0.5                    # current employees are a last resort around a live event


def label(s: float) -> str:
    return "strong" if s >= STRONG else "medium" if s >= MEDIUM else "weak"


@dataclass
class Link:
    kind: str
    strength: float
    reason: str
    facts: dict = field(default_factory=dict)


@dataclass
class Pair:
    strength: float
    links: list[Link]
    hypothetical: bool          # one side is a real person: inferred from public history, not a known relationship


def _year(net: Network) -> int:
    return int(net.as_of[:4])


def _surname(p: Person) -> str:
    return p.name.split()[-1]


def _org(net: Network, oid: str) -> str:
    return net.orgs[oid]["name"]


def _yrs(a: int, b: int | None) -> str:
    return f"{a}–{str(b)[-2:] if b else 'now'}" if a else "years not public"


def stint_link(net: Network, a: Stint, b: Stint, other: Person | None = None) -> Link | None:
    """Link between two stints. `other` is the person holding stint b (named in the reason)."""
    if a.kind != "employment" or b.kind != "employment":
        return None
    oa, ob = net.orgs[a.org], net.orgs[b.org]
    same = a.org == b.org
    group = not same and oa.get("group") and oa.get("group") == ob.get("group")
    if not (same or group):
        return None
    now = _year(net)
    who = f" while {_surname(other)} was {re.sub(r' \(.*\)', '', b.role)}" if other is not None and other.real and other.id != "me" else ""
    if a.start is None or b.start is None:
        if not same:
            return None
        return Link("same_employer", YEARS_UNKNOWN, f"Both at {_org(net, a.org)}, but the years aren't public, so the overlap is unconfirmed",
                    {"org": _org(net, a.org)})
    lo, hi = max(a.start, b.start), min(a.end or now, b.end or now)
    ov = hi - lo
    if ov <= 0:
        return None
    since = now - hi
    if same:
        unit = SAME_UNIT if a.unit and a.unit == b.unit else 0.0
        s = SAME_ORG_BASE + PER_YEAR * min(ov, YEAR_CAP) + unit - min(DECAY_CAP, DECAY_PER_YEAR * since)
        reason = f"{_org(net, a.org)}, {lo}–{hi}: {ov} year{'s' if ov > 1 else ''} overlap{who}"
        kind = "worked_together"
    else:
        s = GROUP_BASE + GROUP_PER_YEAR * min(ov, YEAR_CAP) - min(GROUP_DECAY_CAP, 0.02 * since)
        reason = f"Same group, different companies: {_org(net, a.org)} vs {_org(net, b.org)}, {lo}–{hi}"
        kind = "same_group"
    return Link(kind, round(max(0.05, min(0.95, s)), 3), reason,
                {"org": _org(net, a.org), "years": f"{lo}–{hi}", "overlap_years": ov, "role": a.role})


def edu_link(net: Network, a, b) -> Link | None:
    if a.inst != b.inst:
        return None
    inst = net.institutions[a.inst]["name"]
    if a.year and b.year and abs(a.year - b.year) <= 1:
        s = BATCHMATE_SAME if a.year == b.year else BATCHMATE_ADJ
        return Link("studied_together", s, f"{inst} batch of {a.year}" + ("" if a.year == b.year else f" / {b.year}"),
                    {"institution": inst, "year": a.year})
    why = "graduation year isn't public" if not (a.year and b.year) else f"{abs(a.year - b.year)} years apart"
    return Link("alumni", ALUMNI, f"Both {inst} alumni ({why}): a weak link", {"institution": inst})


def pair(net: Network, a: Person, b: Person) -> Pair | None:
    """All evidence linking a and b, best first, combined by noisy-OR."""
    best: dict[tuple, Link] = {}
    for sa in a.career:
        for sb in b.career:
            l = stint_link(net, sa, sb, b)
            if l:
                k = (l.kind, l.facts.get("org"))
                if k not in best or l.strength > best[k].strength:
                    best[k] = l
    # a same-company link makes the group link for that company redundant
    if any(l.kind == "worked_together" for l in best.values()):
        best = {k: l for k, l in best.items() if l.kind != "same_group"}
    for da in a.education:
        for db in b.education:
            l = edu_link(net, da, db)
            if l:
                k = (l.kind, da.inst)
                if k not in best or l.strength > best[k].strength:
                    best[k] = l
    if not best:
        return None
    links = sorted(best.values(), key=lambda l: -l.strength)
    miss = 1.0
    for l in links:
        miss *= (1 - l.strength)
    return Pair(round(min(0.95, 1 - miss), 3), links, hypothetical=a.real or b.real)


def tie(net: Network, c: Person) -> float:
    if c.closeness is None:
        return 0.0
    t = TIE[c.closeness]
    if c.last_spoke and (date.fromisoformat(net.as_of) - date.fromisoformat(c.last_spoke)).days > STALE_DAYS:
        t *= STALE_FACTOR
    return round(t, 3)


def _knows_edges(net: Network) -> dict[str, dict[str, tuple[int, str]]]:
    """Undirected contact-to-contact edges."""
    e: dict[str, dict[str, tuple[int, str]]] = {}
    for c in net.contacts.values():
        for kid, s, ctx in c.knows:
            e.setdefault(c.id, {})[kid] = (s, ctx)
            e.setdefault(kid, {}).setdefault(c.id, (s, ctx))
    return e


def reach(net: Network) -> dict[str, dict]:
    """How you reach each contact: directly, or through the best contact who knows them."""
    E = _knows_edges(net)
    out = {}
    for c in net.contacts.values():
        if c.closeness:
            out[c.id] = {"strength": tie(net, c), "via": None, "how": c.tie_context}
    for c in net.contacts.values():
        if c.id in out:
            continue
        best = None
        for a, (s, ctx) in E.get(c.id, {}).items():
            if a in out and out[a]["via"] is None:
                v = round(out[a]["strength"] * KNOWS[s], 3)
                if not best or v > best["strength"] or (v == best["strength"] and a < best["via"]):
                    best = {"strength": v, "via": a, "how": ctx}
        if best:
            out[c.id] = best
    return out


# ---- people: who knows X? ---------------------------------------------------------
def resolve(net: Network, name: str) -> Person | None:
    """Filing name -> person. Exact match after normalising honorifics and case; a
    middle initial may be dropped. Ambiguous -> None (no guessing)."""
    n = norm_name(name)
    if n in net.public:
        return net.public[n]
    short = re.sub(r"\b\w\b", "", n).split()
    hits = [p for k, p in net.public.items() if k.split() == short]
    hits += [c for c in net.contacts.values() if norm_name(c.name) in (n, " ".join(short))]
    return hits[0] if len(hits) == 1 else None


def paths_to(net: Network, target: Person, limit: int = 5) -> dict:
    """Best ways in to `target`: your own history, one hop through a contact,
    or two hops (you -> contact -> someone they know -> target)."""
    if not target.career and not target.education:
        return {"target": target.name, "paths": [], "note": target.not_found or "No career history to match against."}
    R = reach(net)
    E = _knows_edges(net)
    paths = []
    direct = pair(net, net.me, target)
    if direct:
        paths.append({"strength": direct.strength, "hops": ["me"], "via": None, "links": [asdict(l) for l in direct.links],
                      "hypothetical": direct.hypothetical})
    for c in net.contacts.values():
        if c.id == target.id:
            continue
        p = pair(net, c, target)
        if not p or c.id not in R:
            continue
        r = R[c.id]
        hops = ["me", r["via"], c.id] if r["via"] else ["me", c.id]
        paths.append({"strength": round(r["strength"] * p.strength, 3), "hops": hops, "contact": c.id, "via": r["via"],
                      "reach": r["strength"], "link": p.strength, "links": [asdict(l) for l in p.links],
                      "hypothetical": p.hypothetical})
    # two hops through a contact's own contacts (only if they aren't already reachable more directly)
    for a in net.contacts.values():
        if not a.closeness:
            continue
        for b_id, (s, ctx) in E.get(a.id, {}).items():
            b = net.contacts[b_id]
            if b.id == target.id or R.get(b.id, {}).get("via") is None and b.closeness:
                continue
            p = pair(net, b, target)
            if not p:
                continue
            v = round(tie(net, a) * KNOWS[s] * p.strength, 3)
            paths.append({"strength": v, "hops": ["me", a.id, b.id], "contact": b.id, "via": a.id, "reach": round(tie(net, a) * KNOWS[s], 3),
                          "link": p.strength, "links": [asdict(l) for l in p.links], "hypothetical": p.hypothetical})
    best: dict[str, dict] = {}
    for p in paths:
        k = p.get("contact", "me")
        if k not in best or p["strength"] > best[k]["strength"]:
            best[k] = p
    out = sorted(best.values(), key=lambda p: (-p["strength"], p.get("contact", "")))
    for p in out:
        p["label"] = label(p["strength"])
    return {"target": target.name, "paths": out[:limit], "total": len(out)}


# ---- companies: who should I call? ----------------------------------------------------
def _org_ids_for(net: Network, ticker: str) -> set[str]:
    o = net.orgs.get(ticker)
    if not o:
        return set()
    g = o.get("group")
    return {k for k, v in net.orgs.items() if k == ticker or (g and v.get("group") == g)}


def compliance(net: Network, c: Person, ticker: str) -> str | None:
    """A contact who works at the company today may hold unpublished information."""
    ids = _org_ids_for(net, ticker)
    for s in c.career:
        if s.end is None and s.org in ids and s.kind == "employment":
            return (f"Currently at {_org(net, s.org)}. Keep it to public information: "
                    "don't ask about unpublished numbers or deals.")
    return None


def _relation(net: Network, c: Person, ticker: str) -> dict | None:
    now = _year(net)
    ids = _org_ids_for(net, ticker)
    vc = net.value_chain.get(ticker, {})
    best = None

    def keep(rel, score, reason, stint):
        nonlocal best
        if not best or score > best["relevance"]:
            best = {"relation": rel, "relevance": round(score, 3), "reason": reason,
                    "stint": {"org": _org(net, stint.org), "role": stint.role, "years": _yrs(stint.start, stint.end)}}

    for s in c.career:
        if s.kind != "employment":
            continue
        if s.org in ids:
            if s.end is None:
                keep("current employee", CURRENT_EMPLOYEE, f"Works at {_org(net, s.org)} now ({s.role})", s)
            else:
                left = now - s.end
                rec = 1.0 if left <= 3 else 0.9 if left <= 7 else 0.75
                tenure = min((s.end - (s.start or s.end)), 6) * 0.02
                keep("ex-employee", EX_EMPLOYEE * rec + tenure,
                     f"{s.role} at {_org(net, s.org)}, {_yrs(s.start, s.end)}; left {left} year{'s' if left != 1 else ''} ago", s)
        elif s.org in vc.get("peers", []):
            if s.end is None:
                keep("competitor", COMPETITOR_NOW, f"{s.role} at {_org(net, s.org)}, a direct competitor", s)
            elif now - s.end <= 5:
                keep("competitor", COMPETITOR_EX, f"Was at competitor {_org(net, s.org)} until {s.end}", s)
        elif s.end is None:
            for kind, rel in (("upstream", "supplier side"), ("downstream", "customer side"), ("adjacent", "adjacent industry")):
                for x in vc.get(kind, []):
                    if x["org"] == s.org:
                        keep(rel, VALUE_CHAIN, f"{s.role} at {_org(net, s.org)}: {x['what']}", s)
    if best and set(c.themes) & set(vc.get("themes", [])):
        best["relevance"] = round(best["relevance"] + THEME_FIT, 3)
    return best


def who_to_call(net: Network, ticker: str, limit: int = 6) -> list[dict]:
    R = reach(net)
    out = []
    for c in net.contacts.values():
        if c.id not in R:
            continue
        rel = _relation(net, c, ticker)
        if not rel:
            continue
        r = R[c.id]
        out.append({"contact": c.id, **rel, "reach": r["strength"], "via": r["via"],
                    "score": round(rel["relevance"] * r["strength"], 3),
                    "flag": compliance(net, c, ticker)})
    out.sort(key=lambda x: (-x["score"], x["contact"]))
    for x in out:
        x["label"] = label(x["score"])
    return out[:limit]


# ---- events: who is best placed to explain this? --------------------------------------
def event_key(category: str, subtype: str, text: str) -> str:
    if category == "litigation" and re.search(r"insolven|cirp|nclt|nclat|ibc", text, re.I):
        return "litigation/insolvency"
    return f"{category}/{subtype}"


# when an event needs expertise the contact doesn't have, how much their company relation still helps
EVENT_FIT = {"theme": 1.0, "ex-employee": 0.5, "current employee": 0.5, "competitor": 0.25,
             "supplier side": 0.25, "customer side": 0.25, "adjacent industry": 0.25}


def best_placed(net: Network, ticker: str, category: str, subtype: str, text: str = "", limit: int = 3) -> dict:
    """relevance = (1-w) x company knowledge + w x event fit, where w comes from orgs.json
    (litigation leans on expertise; a clarification only on knowing the company)."""
    key = event_key(category, subtype, text)
    spec = net.event_themes.get(key, {"themes": [], "weight": 0.0})
    themes, w = spec.get("themes", []), spec.get("weight", 0.0)
    R = reach(net)
    rel = {x["contact"]: x for x in who_to_call(net, ticker, limit=99)}
    out = []
    for c in net.contacts.values():
        if c.id not in R:
            continue
        hit = next((t for t in themes if t in c.themes), None)
        co = rel.get(c.id)
        if not co and not hit:
            continue
        knows_co = min(1.0, co["relevance"]) if co else 0.0
        fit = EVENT_FIT["theme"] if hit else EVENT_FIT.get(co["relation"], 0.25)
        relevance = (1 - w) * knows_co + w * fit
        if co:
            base = {k: co[k] for k in ("relation", "reason", "stint", "flag")}
        else:
            stint = next((s for s in c.career if s.end is None), c.career[-1] if c.career else None)
            base = {"relation": "expert", "flag": None,
                    "reason": f"Your go-to for {hit}" + (f": {stint.role}, {_org(net, stint.org)}" if stint else ""),
                    "stint": {"org": _org(net, stint.org), "role": stint.role, "years": _yrs(stint.start, stint.end)} if stint else None}
        r = R[c.id]
        x = {"contact": c.id, **base, "theme": hit, "relevance": round(relevance, 3), "reach": r["strength"], "via": r["via"],
             "score": round(relevance * r["strength"], 3)}
        if x["flag"]:
            x["score"] = round(x["score"] * COMPLIANCE_DEMOTE, 3)
        cur_ib = any(s.end is None and net.orgs[s.org].get("sector") == "Investment banking" for s in c.career)
        if cur_ib and category == "fund_raise":
            x["flag"] = "Capital-markets banker: ask about structure, pricing and precedent, never live mandates."
        x["label"] = label(x["score"])
        out.append(x)
    out.sort(key=lambda x: (-x["score"], x["contact"]))
    return {"event": key, "themes": themes, "weight": w, "best": out[0] if out else None, "others": out[1:limit]}


# ---- themes: who's my go-to for X? ------------------------------------------------------
def expertise(net: Network, theme: str, limit: int = 5) -> list[dict]:
    R = reach(net)
    by_sector = net.themes.get("by_sector", {})
    now = _year(net)
    out = []
    for c in net.contacts.values():
        if c.id not in R:
            continue
        tagged = theme in c.themes
        yrs = sum(((s.end or now) - s.start) for s in c.career
                  if s.kind == "employment" and s.start and by_sector.get(net.orgs[s.org].get("sector")) == theme)
        if not tagged and not yrs:
            continue
        depth = (0.6 if tagged else 0.0) + min(0.4, 0.04 * yrs)
        r = R[c.id]
        out.append({"contact": c.id, "theme": theme, "tagged": tagged, "years": yrs, "depth": round(depth, 3),
                    "reach": r["strength"], "via": r["via"], "score": round(depth * r["strength"], 3),
                    "reason": ("You tagged them for it" if tagged else "Not tagged") + (f"; {yrs} year{'s' if yrs != 1 else ''} in the sector" if yrs else "")})
    out.sort(key=lambda x: (-x["score"], x["contact"]))
    for x in out:
        x["label"] = label(x["score"])
    return out[:limit]


# ---- keep warm -------------------------------------------------------------------------------
def reconnect(net: Network, material: list[dict], min_days: int = 180) -> list[dict]:
    """material: [{ticker, title, date}]. A contact you haven't spoken to in a while whose
    current or former employer just filed something material = a natural reason to call."""
    out = []
    asof = date.fromisoformat(net.as_of)
    for c in net.contacts.values():
        if not c.closeness or not c.last_spoke:
            continue
        gap = (asof - date.fromisoformat(c.last_spoke)).days
        if gap < min_days:
            continue
        for m in material:
            ids = _org_ids_for(net, m["ticker"])
            s = next((s for s in c.career if s.org in ids and s.kind == "employment"), None)
            # never nudge you toward a current insider right after a material event
            if s and s.end is not None:
                out.append({"contact": c.id, "ticker": m["ticker"], "item": m.get("id"), "title": m["title"], "days": gap,
                            "reason": f"Worked at {_org(net, s.org)}; you last spoke {gap // 30} months ago"})
                break
    out.sort(key=lambda x: (-x["days"], x["contact"]))
    return out
