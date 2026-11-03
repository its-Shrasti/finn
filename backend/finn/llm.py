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
    by_tier: dict = field(default_factory=dict)

    def add(self, tier: str, model: str, tin: int, tout: int):
        self.calls += 1
        self.tokens_in += tin
        self.tokens_out += tout
        pin, pout = PRICES.get(model, (0, 0))
        cost = tin / 1e6 * pin + tout / 1e6 * pout
        self.usd += cost
        t = self.by_tier.setdefault(tier, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "usd": 0.0})
        t["calls"] += 1; t["tokens_in"] += tin; t["tokens_out"] += tout; t["usd"] += cost


METER = Meter()


class LLMUnavailable(RuntimeError):
    pass


def _gemini(model: str, system: str, prompt: str, json_mode: bool) -> tuple[str, int, int]:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise LLMUnavailable("GEMINI_API_KEY not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, **({"responseMimeType": "application/json"} if json_mode else {})},
    }
    r = requests.post(url, json=body, timeout=120)
    r.raise_for_status()
    d = r.json()
    text = d["candidates"][0]["content"]["parts"][0]["text"]
    u = d.get("usageMetadata", {})
    return text, u.get("promptTokenCount", 0), u.get("candidatesTokenCount", 0)


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


def complete(tier: str, system: str, prompt: str, *, json_mode: bool = False) -> str:
    cfg = MODELS[tier]
    k = cache.key(cfg["model"], system, prompt, json_mode)
    hit = cache.get("llm", k)
    if hit is not None:
        METER.cached += 1
        return hit["text"]
    text, tin, tout = _dispatch(cfg["provider"], cfg["model"], system, prompt, json_mode)
    METER.add(tier, cfg["model"], tin, tout)
    cache.put("llm", k, {"text": text})
    return text


def complete_json(tier: str, system: str, prompt: str) -> dict | list:
    raw = complete(tier, system, prompt, json_mode=True)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"[\[{].*[\]}]", raw, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def available() -> bool:
    try:
        complete("cheap", "Reply with OK.", "ping")
        return True
    except Exception:
        return False
