"""Shared configuration loading for the Kanban bots system."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONTROL_ROOT = Path(__file__).resolve().parents[1]
COLUMNS = ["backlog", "to-do", "planning", "in-progress", "in-review", "done"]


def control_root() -> Path:
    return Path(os.environ.get("AIBOTS_CONTROL_ROOT", DEFAULT_CONTROL_ROOT)).resolve()


def load_yaml(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


@dataclass
class Settings:
    root: Path
    raw: dict = field(default_factory=dict)

    @property
    def tickets_dir(self) -> Path:
        return self.root / "tickets"

    @property
    def attachments_dir(self) -> Path:
        return self.root / "attachments"

    @property
    def bots_dir(self) -> Path:
        return self.root / "bots"

    @property
    def state_dir(self) -> Path:
        p = os.environ.get("AIBOTS_STATE_DIR") or self.raw.get("paths", {}).get("state_dir")
        path = Path(p) if p else self.root / ".state"
        if not path.is_absolute():
            path = self.root / path
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def project_repo(self) -> Path:
        return Path(os.environ.get("AIBOTS_PROJECT_REPO") or self.raw.get("paths", {}).get("project_repo", "/workspace/aibots/project-repo"))

    @property
    def workspaces_root(self) -> Path:
        return Path(os.environ.get("AIBOTS_WORKSPACES_ROOT") or self.raw.get("paths", {}).get("workspaces_root", "/workspace/aibots/workspaces"))

    @property
    def columns(self) -> list[str]:
        return list(self.raw.get("tickets", {}).get("columns", COLUMNS))

    @property
    def auto_commit(self) -> bool:
        env = os.environ.get("AIBOTS_AUTO_COMMIT")
        if env is not None:
            return env.lower() in ("1", "true", "yes")
        return bool(self.raw.get("tickets", {}).get("auto_commit", False))

    @property
    def sync_after_mutation(self) -> bool:
        env = os.environ.get("AIBOTS_GITHUB_SYNC")
        if env is not None:
            return env.lower() in ("1", "true", "yes")
        return bool(self.raw.get("tickets", {}).get("sync_github_after_mutation", True))

    @property
    def github(self) -> dict:
        return dict(self.raw.get("github", {}))

    @property
    def omb(self) -> dict:
        return dict(self.raw.get("openmausbot", {}))

    @property
    def loop(self) -> dict:
        return dict(self.raw.get("loop", {}))

    def registry(self) -> dict[str, dict]:
        path = self.bots_dir / "registry.yaml"
        if not path.exists():
            return {}
        return dict((load_yaml(path) or {}).get("bots", {}) or {})

    def triage_rules(self) -> dict:
        path = self.root / "superagent" / "config" / "triage_rules.yaml"
        return load_yaml(path) if path.exists() else {}

    def sandbox_config(self, bot_id: str) -> dict:
        path = self.bots_dir / bot_id / "sandbox.config.yaml"
        return load_yaml(path) if path.exists() else {}


def load_settings(root: Path | None = None) -> Settings:
    root = (root or control_root()).resolve()
    cfg = root / "superagent" / "config" / "superagent.yaml"
    raw = load_yaml(cfg) if cfg.exists() else {}
    return Settings(root=root, raw=raw)
