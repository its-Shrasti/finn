"""The one line of "why call them".

Default: a deterministic template built from the match reasons (by="template").
Optional (live mode): the cheap model rewrites the line from a PSEUDONYMISED
payload. The model never sees the contact's name, how you know them, when you
last spoke, your notes, or any stint other than the one that matched. It sees
"Contact A", one role/org/years, and the public event. The name is put back on
the device. The answer is cached in the owner's private namespace, never in
the shared LLM cache, and the scores it explains are computed before the model
is called: the model can change the wording, not the ranking.
"""
from __future__ import annotations

import json
import re

from .. import cache, llm
from .model import Network, Person

PLACEHOLDER = "Contact A"
EX_ANGLE = {"fund_raise": "so can tell you what management is likely to spend it on",
            "M&A": "so knows how this management buys and integrates",
            "litigation": "so can give you the background to the dispute",
            "press_release": "so can read what the clarification leaves unsaid",
            "capex": "so knows how management executes on new capacity"}
EVENT_NOUN = {"fund_raise": "fund raise", "M&A": "deal", "litigation": "ruling", "press_release": "clarification",
              "capex": "capex plan", "management_change": "leadership change"}


def first(p: Person) -> str:
    return p.name.split()[0]


def _stint_years(st: dict | None) -> int | None:
    m = re.match(r"(\d{4})–(\d{2}|now)", (st or {}).get("years", ""))
    if not m:
        return None
    a = int(m.group(1))
    b = 2026 if m.group(2) == "now" else 2000 + int(m.group(2))
    return b - a


# ---- templates (deterministic) --------------------------------------------------------
def template_path(net: Network, path: dict, target: Person) -> str:
    """Why this path into a person named in a filing."""
    if path.get("hops") == ["me"]:
        return "Your own link: " + path["links"][0]["reason"][0].lower() + path["links"][0]["reason"][1:] + "."
    c = net.contacts[path["contact"]]
    l = path["links"][0]
    lead = f"Through {first(net.contacts[path['via']])}: " if path.get("via") else ""
    sur = target.name.split()[-1]
    if l["kind"] == "worked_together":
        f = l["facts"]
        return (f"{lead}{first(c)} overlapped with {sur} at {f['org']} for {f['overlap_years']} years ({f['years']}). "
                f"Ask what {sur} is like to work with and what to expect in the first year.")
    if l["kind"] == "studied_together":
        return f"{lead}{first(c)} was in {sur}'s batch at {l['facts']['institution']}."
    if l["kind"] == "same_group":
        return f"{lead}{first(c)} was elsewhere in the same group at the same time: a reputation check, not a first-hand view."
    return f"{lead}{l['reason']}."


def template_event(net: Network, x: dict, company: str, category: str) -> str:
    """Why this person for a material event or a research call."""
    c = net.contacts[x["contact"]]
    noun = EVENT_NOUN.get(category, "filing")
    st = x.get("stint") or {}
    rel = x["relation"]
    lead = f"Through {first(net.contacts[x['via']])}: " if x.get("via") else ""
    if x.get("theme") and rel in ("expert",):
        return f"{lead}Your go-to for {x['theme']}. Can tell you what this {noun} means and what usually happens next."
    if rel == "ex-employee":
        n = _stint_years(st)
        role = (st.get("role") or "").replace(" (", ", ").replace(")", "")
        extra = f" and knows {x['theme']}" if x.get("theme") else ""
        if category == "research":
            return f"{lead}{n} years inside {company} ({role}): ask what the numbers don't show."
        return f"{lead}{first(c)} spent {n} years at {company} ({role}){extra}, {EX_ANGLE.get(category, 'so can give you the context behind it')}."
    if rel == "current employee":
        return f"{lead}Works at {company} today. Fine for public context, nothing more."
    if rel == "competitor":
        if not st.get("years", "").endswith("now"):
            return f"{lead}Was at {st.get('org')}, a competitor, until {st.get('years', '')[-2:] and '20' + st.get('years')[-2:]}: knows where {company} wins and loses."
        return f"{lead}Sees {company} from across the table at {st.get('org')}: ask where they win and lose against them."
    if rel in ("supplier side", "customer side", "adjacent industry"):
        what = x["reason"].split(": ", 1)[-1]
        if rel == "supplier side":
            return f"{lead}Sells to the industry from {st.get('org')}: {what}. Ask about input costs and how {company} buys."
        if rel == "customer side":
            return f"{lead}A buyer's view from {st.get('org')}: {what}. Ask how {company} wins orders and holds price."
        return f"{lead}Works in {what}. Ask what it really takes to ramp one."
    return f"{lead}{x['reason']}."


# ---- the model path (live only) -----------------------------------------------------------
def model_payload(net: Network, x: dict, event: dict) -> dict:
    """The MINIMUM the model may see. Pseudonymised; one matched stint; the public event."""
    st = x.get("stint") or {}
    out = {"contact": PLACEHOLDER, "relation": x.get("relation") or "path",
           "matched_experience": {"org": st.get("org"), "role": st.get("role"), "years": st.get("years")},
           "event": {"company": event.get("company"), "title": event.get("title"), "category": event.get("category")}}
    if x.get("theme"):
        out["expertise"] = x["theme"]
    if x.get("links"):                         # a path to a real person named in a filing (public)
        l = x["links"][0]
        out["matched_experience"] = {k: l["facts"].get(k) for k in ("org", "years", "overlap_years", "role", "institution") if l["facts"].get(k)}
        out["link"] = l["kind"]
    return out


def why(net: Network, x: dict, event: dict, template: str, *, use_model: bool = False, owner: str = "demo") -> dict:
    if not use_model:
        return {"text": template, "by": "template"}
    payload = model_payload(net, x, event)
    try:
        text = llm.complete(
            "cheap",
            "You write one line (max 22 words) telling an investor why to call a contact about an event. "
            f"Refer to the person only as '{PLACEHOLDER}'. Use only the JSON facts. No numbers that aren't in it.",
            json.dumps(payload, ensure_ascii=False), cache_ns=f"private/{owner}/why")
    except Exception:
        return {"text": template, "by": "template"}
    line = clean_line(text, payload)
    if not line:
        return {"text": template, "by": "template"}
    return {"text": line.replace(PLACEHOLDER, first(net.contacts[x["contact"]])), "by": "model"}


def clean_line(text: str, payload: dict) -> str | None:
    """Accept the model's line only if it is one plain sentence that names the contact by
    placeholder and uses no number absent from the facts it was given. Some replies come
    wrapped as ```json {"pitch": "..."}```: unwrap those. Anything else falls back to the template."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    if t.startswith("{"):
        try:
            obj = json.loads(t)
            t = next((v for v in obj.values() if isinstance(v, str)), "") if isinstance(obj, dict) else ""
        except ValueError:
            return None
    t = " ".join(t.strip().strip('"').split())
    if not t or PLACEHOLDER not in t or len(t.split()) > 30 or "{" in t:
        return None
    facts = json.dumps(payload, ensure_ascii=False)
    for n in re.findall(r"\d+", t):
        if n not in facts and not (len(n) == 4 and n[:2] in ("19", "20") and n[2:] in facts):
            return None
    return t
