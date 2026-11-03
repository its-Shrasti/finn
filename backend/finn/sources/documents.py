"""Fetch PDFs (annual reports, transcripts, results filings) → text → chunks.

Text is cached by URL forever (filings are immutable). Chunking is
paragraph-aware with overlap so a guidance sentence never gets split.
For transcripts we also pre-filter chunks by keyword before any model sees
them — management guidance lives in maybe 15% of a call, and we only pay for
that 15%.
"""
from __future__ import annotations

import io
import re

import requests

from .. import cache
from ..config import USER_AGENT

GUIDANCE_HINTS = re.compile(
    r"guid|target|expect|aspir|outlook|going forward|next (year|quarter|few)|fy ?2?\d|by (march|fy)|"
    r"margin|capex|growth|run.?rate|order book|working capital|cash flow|we will|we should|we aim",
    re.I)


def text(url: str, max_pages: int = 400) -> str:
    k = cache.key("doc", url)
    hit = cache.get("doc", k)
    if hit:
        return hit["text"]
    r = requests.get(url, headers={"User-Agent": USER_AGENT, "Referer": "https://www.bseindia.com/"}, timeout=60)
    r.raise_for_status()
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(r.content))
    pages = [p.extract_text() or "" for p in reader.pages[:max_pages]]
    t = "\n\n".join(f"[p{i + 1}] {p}" for i, p in enumerate(pages))
    cache.put("doc", k, {"text": t, "pages": len(pages)})
    return t


def chunks(t: str, size: int = 6000, overlap: int = 600) -> list[str]:
    paras = re.split(r"\n\s*\n", t)
    out, cur = [], ""
    for p in paras:
        if len(cur) + len(p) > size and cur:
            out.append(cur)
            cur = cur[-overlap:]
        cur += "\n\n" + p
    if cur.strip():
        out.append(cur)
    return out


def guidance_chunks(t: str, size: int = 6000) -> list[str]:
    """Keep only chunks dense in forward-looking language."""
    cs = chunks(t, size)
    scored = [(len(GUIDANCE_HINTS.findall(c)), c) for c in cs]
    keep = [c for s, c in scored if s >= 4]
    return keep or [c for _, c in sorted(scored, reverse=True)[:3]]
