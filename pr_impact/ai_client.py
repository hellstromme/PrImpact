"""Anthropic API client wrapper for PrImpact.

Handles the low-level Claude API call, JSON parsing, and response logging.
Called only by ai_layer.py.
"""

import json
import os
import re
import secrets
import sys
import tempfile
from typing import Union

import anthropic

MODEL = "claude-sonnet-4-5"
MAX_RESPONSE_TOKENS = 4096


def _call_claude(client: anthropic.Anthropic, prompt: str) -> str:
    for attempt in range(2):
        try:
            message = client.messages.create(
                model=MODEL,
                max_tokens=MAX_RESPONSE_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
            block = message.content[0]
            if not isinstance(block, anthropic.types.TextBlock):
                raise ValueError(f"Unexpected content block type: {type(block)}")
            return block.text
        except Exception:
            if attempt == 1:
                raise
    return ""  # unreachable


def _parse_json_safe(raw: str) -> Union[dict, list]:
    # Strip markdown code fences if present
    text = re.sub(r"^```(?:json)?\s*\n?", "", raw.strip(), flags=re.MULTILINE)
    text = re.sub(r"\n?```\s*$", "", text.strip(), flags=re.MULTILINE)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to extracting the first JSON object or array in the text
    match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def _log_response(label: str, raw: str) -> None:
    if not os.environ.get("PR_IMPACT_DUMP_RESPONSES"):
        return
    try:
        suffix = secrets.token_hex(8)
        path = os.path.join(tempfile.gettempdir(), f"primpact_{label}_{suffix}.txt")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(raw)
    except Exception:
        pass


def call_api(client: anthropic.Anthropic, prompt: str, label: str) -> Union[dict, list]:
    """Call Claude, log the raw response, and parse JSON. Returns {} on any failure."""
    try:
        raw = _call_claude(client, prompt)
        _log_response(label, raw)
        return _parse_json_safe(raw)
    except Exception as exc:
        print(f"[pr-impact] AI call '{label}' failed: {exc}", file=sys.stderr)
        return {}
