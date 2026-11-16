"""Promise ledger: what management said vs what the numbers did.

Each guidance item carries a machine-checkable `measure`. Verdicts are
computed here from reported figures, not judged by a model:
  delivered | beat | missed | walked_back | on_track | at_risk | pending | unscored
Qualitative promises stay "unscored" with the evidence we found — we'd rather
say "can't score this" than invent a verdict.
"""
from __future__ import annotations

ORDER = ["walked_back", "missed", "at_risk", "on_track", "delivered", "beat", "pending", "unscored"]


def _series(calls: list[dict], key: str) -> list[tuple[str, float]]:
    return [(c["id"], c["reported"][key]) for c in calls if key in c.get("reported", {})]


LOWER_IS_BETTER = ("days", "working capital", "debt", "leverage", "cost", "attrition", "npa")


def _after_promise(s: list[tuple[str, float]], item: dict, calls: list[dict]) -> list[tuple[str, float]]:
    """Only figures reported after the promise can test it; the call that made the
    promise gives the starting point, not the result."""
    ids = [c["id"] for c in calls]
    if item.get("made_in") not in ids:
        return s
    i = ids.index(item["made_in"])
    return [(cid, val) for cid, val in s if ids.index(cid) > i]


def _not_yet(item: dict, s: list[tuple[str, float]], unit: str) -> dict:
    last = f" (latest: {s[-1][1]:g}{unit} in {s[-1][0]}, when the promise was made)" if s else ""
    return {"verdict": "pending", "evidence": f"No figure reported after {item.get('made_in')} yet{last}"}


def score(item: dict, calls: list[dict]) -> dict:
    m = item.get("measure", {})
    t = m.get("type")
    v: dict = {"verdict": "unscored", "evidence": item.get("evidence", "")}

    if t == "range":
        s_all = _series(calls, m["series"])
        s = _after_promise(s_all, item, calls)
        unit = m.get("unit", "")
        u = unit if unit in ("%", "") else f" {unit}"
        gap_unit = " pp" if unit == "%" else u
        lower_better = any(w in f"{item.get('metric', '')} {m['series']}".lower().replace("_", " ") for w in LOWER_IS_BETTER)
        if s_all and not s:
            v = _not_yet(item, s_all, u)
        elif s and lower_better:
            last_id, last = s[-1]
            hist = ", ".join(f"{cid} {val:g}{u}" for cid, val in s_all)
            if last < m["lo"]:
                v = {"verdict": "beat", "evidence": f"{last:g}{u} in {last_id}, better than the {m['lo']:g}–{m['hi']:g}{u} band (lower is better) ({hist})"}
            elif last <= m["hi"]:
                v = {"verdict": "delivered", "evidence": f"{last:g}{u} in {last_id}, inside the band ({hist})"}
            else:
                gap = last - m["hi"]
                verdict = "at_risk" if (m.get("soft") or gap < 1) else "missed"
                v = {"verdict": verdict, "evidence": f"{last:g}{u} in {last_id}, {gap:g}{gap_unit} above the top of the band (lower is better) ({hist})"}
        elif s:
            last_id, last = s[-1]
            hist = ", ".join(f"{cid} {val:g}{u}" for cid, val in s_all)
            if last > m["hi"]:
                v = {"verdict": "beat", "evidence": f"Delivered {last:g}{u} in {last_id}, above the {m['lo']:g}–{m['hi']:g}{u} band ({hist})"}
            elif last >= m["lo"]:
                v = {"verdict": "delivered", "evidence": f"{last:g}{u} in {last_id}, inside the band ({hist})"}
            else:
                gap = m["lo"] - last
                # An undated aspiration can't be "missed" yet — only drifting away.
                verdict = "at_risk" if (m.get("soft") or gap < 1) else "missed"
                v = {"verdict": verdict,
                     "evidence": f"{last:g}{u} in {last_id}, {gap:.1f}{gap_unit} below the bottom of the band"
                                 + (" (an undated aspiration)" if m.get("soft") else "") + f" ({hist})"}

    elif t == "threshold":
        actual = m.get("actual")
        if actual is None and m.get("series"):
            s_all = _series(calls, m["series"])
            s = _after_promise(s_all, item, calls)
            actual = s[-1][1] if s else None
            if s_all and not s:
                return {**{k: item[k] for k in ("id", "made_in", "metric", "promise") if k in item}, **_not_yet(item, s_all, m.get("unit", ""))}
        if actual is not None:
            ok = actual >= m["min"]
            fmt = (lambda x: f"Rs {x:,.0f} cr") if m.get("unit") == "Rs cr" else (lambda x: f"{x:,.0f} {m['unit']}")
            if m.get("deadline") and not ok:
                v = {"verdict": "pending", "evidence": f"At {actual:g}{m['unit']} today vs >{m['min']:g}{m['unit']} by {m['deadline']}"}
            else:
                shortfall = (m["min"] - actual)
                pct = f" ({shortfall / abs(m['min']) * 100:.0f}% short)" if m["min"] else ""
                v = {"verdict": "delivered" if ok else "missed",
                     "evidence": f"{m.get('actual_label', 'Actual')} {fmt(actual)} vs promised {fmt(m['min'])}{pct if not ok and m['min'] else ''}"}

    elif t == "run_rate":
        s = _series(calls, "usd_rev_m")
        if s:
            last_id, last = s[-1]
            q_left = {"Q2FY27": 2, "Q3FY27": 1, "Q4FY27": 0}.get(last_id, None)
            if q_left is not None:
                need = ((m["target"] / last) ** (1 / q_left) - 1) * 100 if q_left else 0
                recent = [c["reported"].get("cc_qoq") for c in calls if c["reported"].get("cc_qoq") is not None]
                avg = sum(recent) / len(recent) if recent else 0
                v = {"verdict": "on_track" if need <= avg else "at_risk",
                     "evidence": f"At US${last:g}m/qtr in {last_id}; needs {need:.1f}% QoQ for {q_left} more quarter(s) vs a {avg:.1f}% average recently"}

    elif t == "direction":
        s = dict(_series(calls, m["series"]))
        a, b = s.get(m["from_call"]), s.get(m["to_call"])
        if a is not None and b is not None:
            went_up = b > a
            ok = went_up if m["expect"] == "up" else not went_up
            v = {"verdict": "delivered" if ok else "missed",
                 "evidence": f"{m['series'].replace('_', ' ')} {a:g} → {b:g} ({m['from_call']}→{m['to_call']})"}

    elif t == "revision":
        cut = (m["old_mid"] - m["new_mid"]) / m["old_mid"] * 100
        v = {"verdict": "walked_back", "evidence": f"{item.get('revision', '')} — midpoint cut ~{cut:.0f}% within one quarter"}

    elif t == "pending":
        v = {"verdict": "pending", "evidence": item.get("evidence") or f"Due {m.get('deadline', 'later')}; not yet testable"}

    if item.get("clarification"):
        v["evidence"] = (v.get("evidence", "") + f" · Note: {item['clarification']} ({item['clarified_in']})").strip(" ·")
    if item.get("caveat"):
        v["caveat"] = item["caveat"]
    return {**{k: item[k] for k in ("id", "made_in", "metric", "promise") if k in item}, **v}


def build(guidance: list[dict], calls: list[dict]) -> dict:
    rows = [score(g, calls) for g in guidance]
    rows.sort(key=lambda r: ORDER.index(r["verdict"]))
    tally = {k: sum(1 for r in rows if r["verdict"] == k) for k in ORDER}
    scored = sum(tally[k] for k in ("walked_back", "missed", "at_risk", "on_track", "delivered", "beat"))
    kept = tally["delivered"] + tally["beat"] + tally["on_track"]
    return {"rows": rows, "tally": {k: v for k, v in tally.items() if v},
            "credibility": round(kept / scored * 100) if scored else None, "scored": scored}
