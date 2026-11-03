"""Tiny content-addressed disk cache.

Filings and transcripts never change once published, so anything derived from
them (extracted text, LLM extractions) is cached forever, keyed by a hash of
the inputs. Re-running research on the same company costs ~zero tokens.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable

from .config import CACHE_DIR


def key(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(json.dumps(p, sort_keys=True, default=str).encode())
    return h.hexdigest()[:24]


def _path(ns: str, k: str) -> Path:
    p = CACHE_DIR / ns
    p.mkdir(parents=True, exist_ok=True)
    return p / f"{k}.json"


def get(ns: str, k: str, max_age_s: float | None = None) -> Any | None:
    p = _path(ns, k)
    if not p.exists():
        return None
    if max_age_s is not None and time.time() - p.stat().st_mtime > max_age_s:
        return None
    return json.loads(p.read_text())


def put(ns: str, k: str, value: Any) -> Any:
    _path(ns, k).write_text(json.dumps(value, default=str))
    return value


def memo(ns: str, k: str, fn: Callable[[], Any], max_age_s: float | None = None) -> Any:
    hit = get(ns, k, max_age_s)
    return hit if hit is not None else put(ns, k, fn())
