"""HTTP API for the app (optional — the app also runs from a static snapshot).

  uvicorn finn.api:app --reload
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import llm, snapshot
from .research import engine

app = FastAPI(title="FINN")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
_SNAP: dict | None = None


def _snap(refresh: bool = False, live: bool = True) -> dict:
    global _SNAP
    if _SNAP is None or refresh:
        _SNAP = snapshot.build(live=live)
    return _SNAP


@app.get("/snapshot")
def get_snapshot(refresh: bool = False, live: bool = True):
    return _snap(refresh, live)


@app.get("/research/{ticker}")
def research(ticker: str):
    try:
        return engine.run_live(ticker.upper())
    except Exception as e:  # unknown ticker, no transcripts, no API key...
        raise HTTPException(422, f"research failed for {ticker}: {e}")


class Ask(BaseModel):
    q: str
    ticker: str | None = None


@app.post("/ask")
def ask(body: Ask):
    """Grounded Q&A: the model only sees the snapshot (filings, computed results,
    research facts) — not the open web."""
    snap = _snap()
    ctx = {"items": snap["items"][:60], "portfolio": snap["portfolio"]}
    if body.ticker and body.ticker in snap["research"]:
        ctx["research"] = snap["research"][body.ticker]
    import json
    ans = llm.complete("strong",
                       "You are FINN, an investor's assistant. Answer in <=80 words using ONLY the JSON context. "
                       "Cite tickers and dates. If the context can't answer, say so.",
                       f"Context: {json.dumps(ctx, default=str)[:30000]}\n\nQuestion: {body.q}")
    return {"answer": ans, "cost": llm.METER.__dict__}
