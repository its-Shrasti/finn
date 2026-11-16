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
    k = cache.key("doc", url, max_pages)
    hit = cache.get("doc", k)
    if hit:
        return hit["text"]
    referer = "https://www.nseindia.com/" if "nseindia" in url else "https://www.bseindia.com/"
    for attempt in (1, 2):          # exchange archives sometimes drop a download mid-stream: retry once
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT, "Referer": referer}, timeout=60)
            r.raise_for_status()
            break
        except (requests.ConnectionError, requests.exceptions.ChunkedEncodingError):
            if attempt == 2:
                raise
    from pypdf import PdfReader
    content = r.content
    if content[:2] == b"PK":            # exchanges sometimes attach a .zip holding the PDF
        import zipfile
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            pdfs = [n for n in z.namelist() if n.lower().endswith(".pdf")]
            if not pdfs:
                raise ValueError("zip attachment contains no PDF")
            content = z.read(pdfs[0])
    reader = PdfReader(io.BytesIO(content))
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
