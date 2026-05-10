"""Utilities for calling local Ollama and parsing JSON array responses robustly."""

import json
import logging
import re
import requests
from typing import Any

logger = logging.getLogger(__name__)


def call_ollama_generate(prompt: str, model: str = "gemma3:1b", timeout: int = 300) -> str:
    """Call local Ollama's generate endpoint and return raw text.

    Expects Ollama to be available at http://localhost:11434/api/generate
    """

    payload = {
        "model": model,
        "prompt": prompt,
        "temperature": 0.0,
        "stream": False,
    }

    try:
        resp = requests.post("http://localhost:11434/api/generate", json=payload, timeout=timeout)
        resp.raise_for_status()
        # Ollama returns JSON with a `response` field containing text
        data = resp.json()
        if isinstance(data, dict) and "response" in data:
            return data["response"]
        # Otherwise fallback to raw text
        return resp.text
    except Exception as e:
        logger.error("Ollama generate call failed; is the model available? %s", e)
        raise


def _find_json_array_bounds(text: str) -> tuple[int, int] | None:
    """Find a likely JSON array substring bounds in text.

    Returns (start_index, end_index) or None.
    This does a simple bracket-matching from the first '[' it finds.
    """
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
    """Parse the first JSON array found in `text` robustly.

    Returns a Python object (list) or raises ValueError.
    """
    # Fast path: try to parse the whole text
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass

    # Find a JSON array substring
    bounds = _find_json_array_bounds(text)
    if not bounds:
        raise ValueError("No JSON array found in text")

    start, end = bounds
    candidate = text[start:end]

    # Try to fix common issues: trailing commas -> remove
    candidate_fixed = re.sub(r",\s*,", ",", candidate)
    candidate_fixed = re.sub(r",\s*\]", "]", candidate_fixed)

    try:
        parsed = json.loads(candidate_fixed)
        return parsed
    except Exception as e:
        logger.debug("Failed to parse extracted JSON array; trying best-effort cleanup: %s", e)

    # As a last resort, try to heuristically extract objects with regex (very brittle)
    raise ValueError("Failed to parse JSON array from text")
