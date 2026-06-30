"""Utilities for calling LLM providers and parsing JSON array responses robustly."""

import json
import logging
import os
import re
import requests
from dataclasses import dataclass, field
from typing import Any

from openai import OpenAI

logger = logging.getLogger(__name__)

DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "gemma4:e2b")
DEFAULT_OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Input/output price per token by model. Update if OpenAI changes pricing.
# "cached_input" is the discounted rate for prompt-prefix tokens served from
# OpenAI's automatic prompt cache (50% of input for the 4o family).
_OPENAI_PRICE: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15 / 1_000_000, "cached_input": 0.075 / 1_000_000, "output": 0.60 / 1_000_000},
    "gpt-4o":      {"input": 2.50 / 1_000_000, "cached_input": 1.25 / 1_000_000, "output": 10.00 / 1_000_000},
}


@dataclass
class LLMResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0


def call_llm(prompt: str) -> LLMResponse:
    """Call the configured LLM provider and return an LLMResponse."""
    provider = os.environ.get("LLM_PROVIDER", "ollama")
    if provider == "openai":
        return call_openai_chat(prompt)
    else:
        return call_ollama_generate(prompt)


def call_openai_chat(prompt: str) -> LLMResponse:
    """Call OpenAI chat completions and return an LLMResponse with token counts."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY not set in environment")

    client = OpenAI(api_key=api_key)

    # Split prompt into system / user parts if marked up
    system_msg = ""
    user_msg = prompt
    if "SYSTEM MESSAGE:" in prompt and "USER INSTRUCTIONS:" in prompt:
        parts = prompt.split("USER INSTRUCTIONS:", 1)
        system_msg = parts[0].replace("SYSTEM MESSAGE:", "").strip()
        user_msg = "USER INSTRUCTIONS:\n" + parts[1]

    messages = []
    if system_msg:
        messages.append({"role": "system", "content": system_msg})
    messages.append({"role": "user", "content": user_msg})

    response = client.chat.completions.create(
        model=DEFAULT_OPENAI_MODEL,
        messages=messages,
        temperature=0.0,
    )

    usage = response.usage
    prompt_tokens = usage.prompt_tokens if usage else 0
    completion_tokens = usage.completion_tokens if usage else 0

    # Tokens served from OpenAI's automatic prompt cache (the stable system
    # prompt prefix) are billed at the discounted cached_input rate.
    cached_tokens = 0
    details = getattr(usage, "prompt_tokens_details", None) if usage else None
    if details is not None:
        cached_tokens = getattr(details, "cached_tokens", 0) or 0

    prices = _OPENAI_PRICE.get(DEFAULT_OPENAI_MODEL, {"input": 0.0, "output": 0.0})
    cached_rate = prices.get("cached_input", prices["input"])
    uncached_tokens = prompt_tokens - cached_tokens
    cost_usd = (
        uncached_tokens * prices["input"]
        + cached_tokens * cached_rate
        + completion_tokens * prices["output"]
    )

    return LLMResponse(
        text=response.choices[0].message.content or "",
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cached_tokens=cached_tokens,
        cost_usd=cost_usd,
    )


def call_ollama_generate(prompt: str, model: str = DEFAULT_OLLAMA_MODEL, timeout: int = 300) -> LLMResponse:
    """Call local Ollama's generate endpoint and return an LLMResponse with token counts."""
    payload = {
        "model": model,
        "prompt": prompt,
        "temperature": 0.0,
        "stream": False,
    }

    try:
        resp = requests.post("http://localhost:11434/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "response" in data:
            return LLMResponse(
                text=data["response"],
                prompt_tokens=data.get("prompt_eval_count", 0),
                completion_tokens=data.get("eval_count", 0),
                cost_usd=0.0,
            )
        return LLMResponse(text=resp.text)
    except Exception as e:
        logger.error("Ollama generate call failed; is the model available? %s", e)
        raise


def _find_json_array_bounds(text: str) -> tuple[int, int] | None:
    """Find a likely JSON array substring bounds in text."""
    start = text.find("[")
    if start == -1:
        return None

    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return (start, i + 1)
    return None


def parse_json_array_from_text(text: str) -> Any:
    """Parse the first JSON array found in `text` robustly."""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass

    bounds = _find_json_array_bounds(text)
    if not bounds:
        raise ValueError("No JSON array found in text")

    start, end = bounds
    candidate = text[start:end]

    candidate_fixed = re.sub(r",\s*,", ",", candidate)
    candidate_fixed = re.sub(r",\s*\]", "]", candidate_fixed)

    try:
        return json.loads(candidate_fixed)
    except Exception as e:
        logger.debug("Failed to parse extracted JSON array: %s", e)

    raise ValueError("Failed to parse JSON array from text")
