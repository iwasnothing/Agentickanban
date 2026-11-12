import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def control(tmp_path, monkeypatch):
    """A throwaway copy of the control repo config with an isolated tickets/state dir + project repo."""
    c = tmp_path / "control"
    for d in ("superagent", "bots", "scripts"):
        shutil.copytree(ROOT / d, c / d, ignore=shutil.ignore_patterns("__pycache__"))
    (c / "tickets").mkdir(); (c / "attachments").mkdir()
    proj = tmp_path / "project"
    proj.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(proj)], check=True)
    (proj / "app.py").write_text("print('hi')\n")
    (proj / "tests").mkdir()
    (proj / "tests" / "test_ok.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertTrue(True)\n")
    subprocess.run(["git", "-C", str(proj), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(proj), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], check=True)
    ws_root = tmp_path / "workspaces"
    reg = (c / "bots" / "registry.yaml").read_text().replace("/workspace/aibots/workspaces/bot-001", str(ws_root / "bot-001"))
    (c / "bots" / "registry.yaml").write_text(reg)
    sb = (c / "bots" / "bot-001" / "sandbox.config.yaml").read_text().replace("/workspace/aibots/workspaces/bot-001", str(ws_root / "bot-001"))
    (c / "bots" / "bot-001" / "sandbox.config.yaml").write_text(sb)
    monkeypatch.setenv("AIBOTS_CONTROL_ROOT", str(c))
    monkeypatch.setenv("AIBOTS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AIBOTS_PROJECT_REPO", str(proj))
    monkeypatch.setenv("AIBOTS_WORKSPACES_ROOT", str(ws_root))
    monkeypatch.setenv("AIBOTS_AUTO_COMMIT", "0")
    monkeypatch.setenv("AIBOTS_PYTHON", sys.executable)
    for k in ("GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    return {"root": c, "project": proj, "workspaces": ws_root, "state": tmp_path / "state"}
