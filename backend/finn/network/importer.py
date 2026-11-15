"""Onboarding: LinkedIn's official data export -> network.json contacts.

The investor downloads their own data from LinkedIn (Settings -> Data privacy ->
Get a copy of your data -> Connections) and gives FINN Connections.csv. No
scraping: this is the investor's own export, which LinkedIn provides to them.

What the file has: First Name, Last Name, URL, Email Address, Company, Position,
Connected On, preceded by a few "Notes:" lines. Only the CURRENT company and title.

What the importer does with it:
  * keeps name, current company and title; drops URL and email (not needed to match);
  * resolves the company to a canonical org id (orgs.json names/aliases, legal suffixes
    stripped); an unknown company is kept as text and flagged for review, never guessed;
  * creates a current stint with the start year left null: the export doesn't say
    when they joined, so overlaps with it are "unconfirmed" until the investor adds years;
  * sets closeness 1 (a connection, not a friend) and no last-spoke date: "Connected On"
    is when you connected, not when you last spoke;
  * skips anyone already in the network (same normalised name and company).
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path

from .model import NET_DIR, norm_name

SUFFIX = re.compile(r"\b(limited|ltd|pvt|private|inc|llp|llc|plc|corporation|corp|co)\b\.?", re.I)
HEADER = "first name"


def _norm_org(s: str) -> str:
    s = SUFFIX.sub(" ", (s or "").lower())
    s = re.sub(r"[^\w&\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def org_index(orgs: dict) -> dict[str, str]:
    idx = {}
    for oid, o in orgs["orgs"].items():
        for name in [o["name"], *o.get("aliases", [])]:
            idx[_norm_org(name)] = oid
    return idx


def resolve_org(company: str, idx: dict[str, str]) -> str | None:
    n = _norm_org(company)
    if not n:
        return None
    if n in idx:
        return idx[n]
    trimmed = re.sub(r"\s+india$", "", n)          # "Kaynes Technology India" -> "Kaynes Technology"
    return idx.get(trimmed)


def _date(s: str) -> str | None:
    for fmt in ("%d %b %Y", "%d-%b-%y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def read_rows(text: str) -> list[dict]:
    """Skip LinkedIn's 'Notes:' preamble and read from the header row on."""
    lines = text.lstrip("﻿").splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().lower().startswith(HEADER)), None)
    if start is None:
        raise ValueError("Not a LinkedIn Connections.csv: no 'First Name' header row found")
    return list(csv.DictReader(io.StringIO("\n".join(lines[start:]))))


def to_contacts(text: str, orgs: dict, existing: list[dict] | None = None, *, synthetic: bool = False,
                id_prefix: str = "li") -> dict:
    idx = org_index(orgs)
    seen = {(norm_name(c["name"]), next((s["org"] for s in c.get("career", []) if s.get("to") is None), None))
            for c in (existing or [])}
    contacts, review, skipped = [], [], 0
    for r in read_rows(text):
        name = f"{(r.get('First Name') or '').strip()} {(r.get('Last Name') or '').strip()}".strip()
        if not name:
            skipped += 1
            continue
        company, position = (r.get("Company") or "").strip(), (r.get("Position") or "").strip()
        oid = resolve_org(company, idx)
        if (norm_name(name), oid) in seen:
            skipped += 1
            continue
        seen.add((norm_name(name), oid))
        connected = _date(r.get("Connected On") or "")
        c = {"id": f"{id_prefix}{len(contacts) + 1:03d}", "name": name, "synthetic": synthetic, "source": "linkedin_export",
             "headline": ", ".join(x for x in (position, company) if x),
             "education": [],
             "career": [{"org": oid, "role": position, "from": None, "to": None}] if oid else [],
             "themes": [],
             "tie": {"closeness": 1, "context": "LinkedIn connection" + (f" since {connected[:7]}" if connected else ""),
                     "last_spoke": None},
             "knows": [], "notes": ""}
        if company and not oid:
            c["unresolved_company"] = company
            review.append(c["id"])
        contacts.append(c)
    return {"contacts": contacts, "needs_review": review, "skipped": skipped}


def run(csv_path: str, out: str | None = None, *, merge: bool = False) -> str:
    """CLI: write imported contacts next to the demo network (never over it unless --merge)."""
    orgs = json.loads((NET_DIR / "orgs.json").read_text())
    net_path = NET_DIR / "network.json"
    net = json.loads(net_path.read_text())
    res = to_contacts(Path(csv_path).read_text(encoding="utf-8-sig"), orgs, net["contacts"])
    if merge:
        net["contacts"] += res["contacts"]
        target = Path(out) if out else net_path
        target.write_text(json.dumps(net, ensure_ascii=False, indent=1))
    else:
        target = Path(out) if out else NET_DIR / "imported_linkedin.json"
        target.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    return f"{target}: {len(res['contacts'])} imported, {len(res['needs_review'])} need a company match, {res['skipped']} skipped"
