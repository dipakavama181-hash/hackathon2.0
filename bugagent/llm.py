"""Claude client + prompt/response helpers. Any object with .chat(messages)->str can be injected."""
from __future__ import annotations

import json
import os
import re

from .patcher import Edit

DEFAULT_MODEL = os.environ.get("BUGAGENT_MODEL", "claude-sonnet-5-5")

SYSTEM = (
    "You are a senior software engineer acting as an automated bug-fixing agent. "
    "You receive failing test output and/or a static-analysis finding, plus source files. "
    "Respond with ONLY a JSON object, no prose and no markdown fences:\n"
    '{"explanation": "root cause and why the fix works", '
    '"edits": [{"file": "relative/path.py", "old": "exact existing text", "new": "replacement text"}]}\n'
    "Rules: keep edits minimal; each `old` must match the file exactly once (include surrounding lines "
    "if needed); fix the root cause in source code and never weaken or delete tests unless the test itself "
    "is wrong; make no unrelated changes; if you cannot fix it safely, return an empty edits list and explain."
)


class ClaudeClient:
    def __init__(self, model: str = DEFAULT_MODEL, max_tokens: int = 4000):
        import anthropic  # lazy: offline mode works without the SDK
        self.client = anthropic.Anthropic()
        self.model, self.max_tokens = model, max_tokens

    def chat(self, messages: list[dict]) -> str:
        r = self.client.messages.create(model=self.model, max_tokens=self.max_tokens,
                                        system=SYSTEM, messages=messages)
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


def make_default_llm(model: str = DEFAULT_MODEL):
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None, "ANTHROPIC_API_KEY not set"
    try:
        return ClaudeClient(model), ""
    except ImportError:
        return None, "`anthropic` package not installed (pip install anthropic)"


def parse_edits(reply: str) -> tuple[str, list[Edit]]:
    text = re.sub(r"^```(?:json)?|```$", "", reply.strip(), flags=re.M).strip()
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b < 0:
        return "model reply was not JSON", []
    try:
        data = json.loads(text[a:b + 1])
    except json.JSONDecodeError as e:
        return f"invalid JSON from model: {e}", []
    edits = [Edit(str(e.get("file", "")), str(e.get("old", "")), str(e.get("new", "")))
             for e in data.get("edits", []) if isinstance(e, dict)]
    return str(data.get("explanation", "")), edits


def files_from_traceback(output: str, root, limit: int = 6) -> list[str]:
    seen: list[str] = []
    for m in re.finditer(r"([\w./\\-]+\.py):\d+", output):
        rel = m.group(1).replace("\\", "/")
        p = (root / rel)
        if p.is_file() and rel not in seen and "site-packages" not in rel:
            seen.append(rel)
    return seen[:limit]


def render_files(sandbox, rels: list[str], cap: int = 12000) -> str:
    parts = []
    for rel in rels:
        try:
            parts.append(f"### {rel}\n```python\n{sandbox.read(rel)[:cap]}\n```")
        except Exception:
            pass
    return "\n\n".join(parts)
