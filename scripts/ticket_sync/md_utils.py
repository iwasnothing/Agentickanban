"""Markdown ticket helpers: YAML front-matter + `# Section` bodies.

A ticket file is::

    ---
    <yaml front-matter>
    ---

    # Objective
    ...
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any

import yaml

FRONT_MATTER_KEYS = [
    "id", "title", "status", "assignee_bot", "github_issue", "github_project_item_id",
    "priority", "labels", "created_at", "updated_at",
]
SECTION_ORDER = ["Objective", "Context", "Acceptance Criteria", "Attachments", "Execution Log", "Final Review"]
PRIORITIES = ["high", "medium", "low"]

_FM_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)
_H1_RE = re.compile(r"^# (.+?)\s*$", re.M)


def slugify(text: str, max_len: int = 48) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (text[:max_len].rstrip("-")) or "ticket"


def today() -> str:
    return date.today().isoformat()


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _normalize_meta(meta: dict) -> dict:
    out = {}
    for k, v in meta.items():
        if isinstance(v, (date, datetime)):
            v = v.isoformat()[:10]
        out[k] = v
    return out


def parse(text: str) -> tuple[dict, str]:
    """Split a ticket into (front-matter dict, markdown body)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, text
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("front-matter must be a mapping")
    return _normalize_meta(meta), text[m.end():]


def dump_front_matter(meta: dict) -> str:
    ordered = {k: meta.get(k) for k in FRONT_MATTER_KEYS if k in meta}
    for k, v in meta.items():
        if k not in ordered:
            ordered[k] = v
    lines = []
    for k, v in ordered.items():
        if isinstance(v, list):
            lines.append(f"{k}: [{', '.join(_scalar(x) for x in v)}]")
        else:
            lines.append(f"{k}: {_scalar(v)}")
    return "---\n" + "\n".join(lines) + "\n---\n"


def _scalar(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = str(v)
    # quote anything YAML might reinterpret
    dumped = yaml.safe_dump(s, default_flow_style=True, width=10_000).strip()
    if dumped.endswith("\n..."):
        dumped = dumped[:-4].strip()
    if dumped.endswith("..."):
        dumped = dumped[:-3].strip()
    return dumped


def render(meta: dict, body: str) -> str:
    return dump_front_matter(meta) + "\n" + body.lstrip("\n")


def sections(body: str) -> dict[str, str]:
    """Return {heading: content} for top-level `# Heading` sections (content stripped)."""
    out: dict[str, str] = {}
    matches = list(_H1_RE.finditer(body))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out[m.group(1).strip()] = body[m.end():end].strip("\n")
    return out


def build_body(secs: dict[str, str]) -> str:
    names = [s for s in SECTION_ORDER if s in secs] + [s for s in secs if s not in SECTION_ORDER]
    parts = []
    for name in names:
        content = (secs[name] or "").strip("\n")
        parts.append(f"# {name}\n{content}\n" if content else f"# {name}\n")
    return "\n".join(parts)


def set_section(body: str, name: str, content: str) -> str:
    secs = sections(body)
    secs[name] = content
    return build_body(secs)


def append_section(body: str, name: str, content: str) -> str:
    secs = sections(body)
    existing = secs.get(name, "").rstrip()
    secs[name] = (existing + "\n\n" + content.strip("\n")).strip("\n") if existing else content.strip("\n")
    return build_body(secs)


def bullet_list(items: list[str] | None, checkbox: bool = False, empty: str = "- (none)") -> str:
    items = [i for i in (items or []) if str(i).strip()]
    if not items:
        return empty
    prefix = "- [ ] " if checkbox else "- "
    return "\n".join(prefix + str(i).strip() for i in items)


def acceptance_criteria(body: str) -> list[dict]:
    """Parse `- [ ] text` / `- [x] text` lines of the Acceptance Criteria section."""
    out = []
    for line in sections(body).get("Acceptance Criteria", "").splitlines():
        m = re.match(r"^\s*-\s*\[([ xX])\]\s*(.+)$", line)
        if m:
            out.append({"done": m.group(1).lower() == "x", "text": m.group(2).strip()})
    return out


def count_turns(body: str, bot_id: str | None = None) -> int:
    log = sections(body).get("Execution Log", "")
    pat = r"^## Turn (\d+) \((?!superagent)([^)]+)\)" if bot_id is None else rf"^## Turn (\d+) \({re.escape(bot_id)}\)"
    nums = [int(m.group(1)) for m in re.finditer(pat, log, re.M)]
    return max(nums) if nums else 0
