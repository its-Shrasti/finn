"""Network data: load and validate.

Three inputs, kept apart on purpose:
  data/network/network.json        PRIVATE  the investor's own contacts (synthetic in this demo)
  data/network/orgs.json           shared   employer/institution register + value chain
  data/inputs/public_careers.json  shared   public career history of people named in filings

Validation enforces the rules the matching relies on, so a bad edit fails
loudly instead of producing a confident-looking wrong path:
  * every contact is marked synthetic (this demo has no real contacts);
  * real people never carry, or receive, a 'knows' link;
  * every org / institution id resolves;
  * every public career fact cites a source; missing years stay null;
  * every contact is reachable: a direct tie, or 'via' a contact you know.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DATA

NET_DIR = DATA / "network"
HONORIFICS = re.compile(r"^(mr|ms|mrs|dr|shri|smt|prof)\.?\s+", re.I)


class NetworkError(ValueError):
    pass


@dataclass(frozen=True)
class Stint:
    org: str
    role: str
    start: int | None
    end: int | None          # None = present
    kind: str = "employment"  # or "board": never counts as working together
    unit: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class Degree:
    inst: str
    degree: str
    year: int | None
    source: str | None = None


@dataclass
class Person:
    id: str
    name: str
    real: bool
    career: list[Stint]
    education: list[Degree]
    headline: str = ""
    themes: list[str] = field(default_factory=list)
    closeness: int | None = None      # investor's own tie, 1–3; None = not known directly
    last_spoke: str | None = None
    tie_context: str = ""
    via: str | None = None
    knows: list[tuple[str, int, str]] = field(default_factory=list)
    notes: str = ""
    not_found: str | None = None
    filing: dict | None = None


@dataclass
class Network:
    me: Person
    contacts: dict[str, Person]
    public: dict[str, Person]          # keyed by canonical name
    orgs: dict
    institutions: dict
    themes: dict
    value_chain: dict
    event_themes: dict
    as_of: str
    meta: dict


def norm_name(s: str) -> str:
    s = HONORIFICS.sub("", (s or "").strip())
    s = re.sub(r"[^\w\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _stints(rows, *, need_source: bool) -> list[Stint]:
    out = []
    for r in rows:
        if need_source and not r.get("source"):
            raise NetworkError(f"public career fact without a source: {r}")
        a, b = r.get("from"), r.get("to")
        if a is not None and b is not None and a > b:
            raise NetworkError(f"stint ends before it starts: {r}")
        out.append(Stint(r["org"], r.get("role", ""), a, b, r.get("kind", "employment"), r.get("unit"), r.get("source")))
    return out


def _degrees(rows, *, need_source: bool) -> list[Degree]:
    out = []
    for r in rows:
        if need_source and not r.get("source"):
            raise NetworkError(f"public education fact without a source: {r}")
        out.append(Degree(r["inst"], r.get("degree", ""), r.get("year"), r.get("source")))
    return out


def build(net: dict, orgs: dict, public: dict, *, allow_real: bool = False) -> Network:
    """Validate raw JSON and return a Network. Pure function: tests feed it dicts.
    allow_real=False (the demo) rejects any contact not marked synthetic; a real
    deployment loading a LinkedIn import passes allow_real=True."""
    O, I = orgs["orgs"], orgs["institutions"]
    public_people: dict[str, Person] = {}
    for name, p in public["people"].items():
        if p.get("knows"):
            raise NetworkError(f"real person {name} carries 'knows' links; only public career overlap is allowed")
        pid = "pub:" + norm_name(name).replace(" ", "-")
        public_people[norm_name(name)] = Person(
            id=pid, name=name, real=True, career=_stints(p.get("career", []), need_source=True),
            education=_degrees(p.get("education", []), need_source=True), not_found=p.get("not_found"),
            filing=p.get("filing"), headline=(p.get("filing") or {}).get("role", ""))

    m = net["me"]
    me = Person(id="me", name=m.get("label", "You"), real=True, career=_stints(m.get("career", []), need_source=False),
                education=_degrees(m.get("education", []), need_source=False), headline=m.get("persona", ""))

    contacts: dict[str, Person] = {}
    for c in net["contacts"]:
        if c["id"] in contacts:
            raise NetworkError(f"duplicate contact id {c['id']}")
        if c.get("synthetic") is not True and not allow_real:
            raise NetworkError(f"contact {c['id']} is not marked synthetic; this demo holds fictional people only")
        if norm_name(c["name"]) in public_people:
            raise NetworkError(f"contact {c['id']} shares a name with a real person in filings")
        t = c.get("tie") or {}
        contacts[c["id"]] = Person(
            id=c["id"], name=c["name"], real=False, headline=c.get("headline", ""),
            career=_stints(c.get("career", []), need_source=False), education=_degrees(c.get("education", []), need_source=False),
            themes=c.get("themes", []), closeness=t.get("closeness"), last_spoke=t.get("last_spoke"),
            tie_context=t.get("context", ""), via=c.get("via"),
            knows=[(k["id"], int(k["strength"]), k.get("context", "")) for k in c.get("knows", [])], notes=c.get("notes", ""))

    real_ids = {p.id for p in public_people.values()}
    for p in [me, *contacts.values(), *public_people.values()]:
        for s in p.career:
            if s.org not in O:
                raise NetworkError(f"{p.id}: unknown org {s.org}")
        for d in p.education:
            if d.inst not in I:
                raise NetworkError(f"{p.id}: unknown institution {d.inst}")
    for c in contacts.values():
        if c.closeness is not None and c.closeness not in (1, 2, 3):
            raise NetworkError(f"{c.id}: closeness must be 1–3")
        if c.closeness is None and (not c.via or c.via not in contacts or contacts[c.via].closeness is None):
            raise NetworkError(f"{c.id}: not known directly, and 'via' doesn't point to someone you know")
        for kid, strength, _ in c.knows:
            if kid in real_ids or kid.startswith("pub:"):
                raise NetworkError(f"{c.id}: 'knows' link to a real person ({kid}) is not allowed")
            if kid not in contacts:
                raise NetworkError(f"{c.id}: knows unknown contact {kid}")
            if strength not in (1, 2, 3):
                raise NetworkError(f"{c.id}: knows strength must be 1–3")
    for t, vc in orgs.get("value_chain", {}).items():
        if t.startswith("_"):
            continue
        for o in vc.get("peers", []) + [x["org"] for k in ("upstream", "downstream", "adjacent") for x in vc.get(k, [])]:
            if o not in O:
                raise NetworkError(f"value chain {t}: unknown org {o}")

    return Network(me=me, contacts=contacts, public=public_people, orgs=O, institutions=I, themes=orgs["themes"],
                   value_chain={k: v for k, v in orgs.get("value_chain", {}).items() if not k.startswith("_")},
                   event_themes={k: v for k, v in orgs.get("event_themes", {}).items() if not k.startswith("_")},
                   as_of=net.get("_meta", {}).get("as_of", "2026-11-03"), meta=net.get("_meta", {}))


def raw(path: Path | None = None) -> tuple[dict, dict, dict]:
    net = json.loads((path or NET_DIR / "network.json").read_text())
    orgs = json.loads((NET_DIR / "orgs.json").read_text())
    public = json.loads((DATA / "inputs" / "public_careers.json").read_text())
    return net, orgs, public


def load(path: Path | None = None) -> Network:
    return build(*raw(path))
