"""Model router.

Design rules:
  * Numbers never come from a model. Models extract text spans and make
    judgements; every figure the user sees is computed in Python from source data.
  * Two tiers (config.MODELS): cheap for bulk map steps, strong for the one
    reduce step that needs reasoning.
  * Every call is cached by (model, prompt) hash and metered, so a research run
    reports its own cost.
  * Providers: Gemini (free tier), Groq, or any OpenAI-compatible endpoint
    (Ollama for fully local). Selected per tier via env vars.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field

import requests

from . import cache
from .config import MODELS, PRICES


@dataclass
class Meter:
    calls: int = 0
    cached: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0
    retries: int = 0
    by_tier: dict = field(default_factory=dict)
    models: dict = field(default_factory=dict)      # tier -> models that actually answered
    fallbacks: list = field(default_factory=list)   # why a fallback model was used
    cached_tokens_in: int = 0      # tokens of cached answers, as metered when they were generated
    cached_tokens_out: int = 0
    cached_usd: float = 0.0

    def reset(self):
        self.__init__()

    def add(self, tier: str, model: str, tin: int, tout: int):
        self.calls += 1
        self.tokens_in += tin
        self.tokens_out += tout
        pin, pout = PRICES.get(model, (0, 0))
        cost = tin / 1e6 * pin + tout / 1e6 * pout
        self.usd += cost
        t = self.by_tier.setdefault(tier, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "usd": 0.0})
        t["calls"] += 1; t["tokens_in"] += tin; t["tokens_out"] += tout; t["usd"] += cost
        self.used(tier, model)

    def add_cached(self, tier: str, model: str, hit: dict):
        self.cached += 1
        self.used(tier, model)
        tin, tout = hit.get("tin", 0), hit.get("tout", 0)
        pin, pout = PRICES.get(model, (0, 0))
        self.cached_tokens_in += tin; self.cached_tokens_out += tout
        self.cached_usd += tin / 1e6 * pin + tout / 1e6 * pout

    def used(self, tier: str, model: str):
        ms = self.models.setdefault(tier, [])
        if model not in ms:
            ms.append(model)


METER = Meter()


class LLMUnavailable(RuntimeError):
    pass


def _retry_hint(r) -> float | None:
    """Seconds Google asks us to wait, from a 429's RetryInfo detail (e.g. "17s")."""
    try:
        for d in r.json().get("error", {}).get("details", []):
            if str(d.get("@type", "")).endswith("RetryInfo") and d.get("retryDelay"):
                return float(str(d["retryDelay"]).rstrip("s"))
    except (ValueError, AttributeError):
        pass
    return None


def _gemini(model: str, system: str, prompt: str, json_mode: bool) -> tuple[str, int, int]:
    """The key goes in a header, never the URL, so it can't leak into an error
    message, a traceback or a log. Retries rate limits and transient errors."""
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise LLMUnavailable("GEMINI_API_KEY not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, **({"responseMimeType": "application/json"} if json_mode else {})},
    }
    for attempt in range(5):
        try:
            r = requests.post(url, json=body, timeout=180, headers={"x-goog-api-key": key})
        except (requests.Timeout, requests.ConnectionError) as e:
            # An overloaded model can hold the connection open with no answer. Don't wait
            # another 3 minutes on it: report busy, so a tier with a fallback moves on.
            print(f"[llm] {model} no answer ({type(e).__name__}) after 180s", file=sys.stderr)
            raise LLMUnavailable(f"Gemini {model} timed out: {type(e).__name__}") from None
        if r.status_code in (429, 500, 503) and attempt < 4:
            wait = min(60, 8 * 2 ** attempt)
            hint = _retry_hint(r)
            if hint:
                wait = min(90, max(wait, hint))           # Google's own suggested delay, if it gives one
            msg = ""
            try:
                msg = r.json().get("error", {}).get("message", "")[:140]
            except ValueError:
                pass
            print(f"[llm] {model} HTTP {r.status_code}, retry {attempt + 1}/4 in {wait:.0f}s: {msg}", file=sys.stderr)
            METER.retries += 1
            time.sleep(wait)
            continue
        break
    if r.status_code != 200:
        # Report status and Google's message only; never the request (it carries the key header).
        try:
            msg = r.json().get("error", {}).get("message", "")[:300]
        except ValueError:
            msg = r.text[:300]
        raise LLMUnavailable(f"Gemini {model} HTTP {r.status_code}: {msg}")
    d = r.json()
    parts = d["candidates"][0]["content"]["parts"]
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    u = d.get("usageMetadata", {})
    # Gemini 2.5 bills "thinking" tokens as output tokens; count them or the meter under-reports.
    return text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)


def _openai_compat(base: str, key_env: str, model: str, system: str, prompt: str, json_mode: bool):
    key = os.getenv(key_env, "none")
    body = {"model": model, "temperature": 0.1,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    r = requests.post(f"{base}/chat/completions", json=body, timeout=120,
                      headers={"Authorization": f"Bearer {key}"})
    r.raise_for_status()
    d = r.json()
    u = d.get("usage", {})
    return d["choices"][0]["message"]["content"], u.get("prompt_tokens", 0), u.get("completion_tokens", 0)


def _dispatch(provider: str, model: str, system: str, prompt: str, json_mode: bool):
    if provider == "gemini":
        return _gemini(model, system, prompt, json_mode)
    if provider == "groq":
        return _openai_compat("https://api.groq.com/openai/v1", "GROQ_API_KEY", model, system, prompt, json_mode)
    if provider == "ollama":
        return _openai_compat(os.getenv("OLLAMA_URL", "http://localhost:11434/v1"), "OLLAMA_KEY", model, system, prompt, json_mode)
    if provider == "openai":
        return _openai_compat(os.getenv("OPENAI_BASE", "https://api.openai.com/v1"), "OPENAI_API_KEY", model, system, prompt, json_mode)
    raise LLMUnavailable(f"unknown provider {provider}")


def _busy(e: Exception) -> bool:
    return any(x in str(e) for x in (" HTTP 503", " HTTP 429", " HTTP 500", " timed out"))


def complete(tier: str, system: str, prompt: str, *, json_mode: bool = False, cache_ns: str = "llm") -> str:
    """cache_ns: the shared "llm" namespace for public inputs (filings, transcripts);
    a per-user private namespace for anything derived from the investor's network.
    If the tier's model stays overloaded (503/429 after retries) and the tier names a
    fallback, the fallback answers instead; the meter records which model did."""
    cfg = MODELS[tier]
    models = [cfg["model"]] + ([cfg["fallback"]] if cfg.get("fallback") else [])
    for m in models:   # a cached answer from either model is reused
        hit = cache.get(cache_ns, cache.key(m, system, prompt, json_mode))
        if hit is not None:
            METER.add_cached(tier, m, hit)
            return hit["text"]
    for i, m in enumerate(models):
        try:
            text, tin, tout = _dispatch(cfg["provider"], m, system, prompt, json_mode)
        except LLMUnavailable as e:
            if i + 1 < len(models) and _busy(e):
                METER.fallbacks.append(f"{m} -> {models[i + 1]}: {str(e)[:120]}")
                print(f"[llm] {m} still unavailable; falling back to {models[i + 1]}", file=sys.stderr)
                continue
            raise
        METER.add(tier, m, tin, tout)
        cache.put(cache_ns, cache.key(m, system, prompt, json_mode), {"text": text, "model": m, "tin": tin, "tout": tout})
        return text
    raise LLMUnavailable(f"no model answered for tier {tier}")


def complete_json(tier: str, system: str, prompt: str, cache_ns: str = "llm") -> dict | list:
    raw = complete(tier, system, prompt, json_mode=True, cache_ns=cache_ns)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", raw, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def as_obj(x) -> dict:
    """Models sometimes answer a one-object question with a list (e.g. a filing that
    appoints three people). Callers that expect an object get the first object."""
    if isinstance(x, dict):
        return x
    if isinstance(x, list):
        return next((i for i in x if isinstance(i, dict)), {})
    return {}


def complete_obj(tier: str, system: str, prompt: str, cache_ns: str = "llm") -> dict:
    return as_obj(complete_json(tier, system, prompt, cache_ns))


def available() -> bool:
    try:
        complete("cheap", "Reply with OK.", "ping")
        return True
    except Exception:
        return False
