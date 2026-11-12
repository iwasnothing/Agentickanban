"""Markdown ticket store. Folder location is canonical; front-matter `status` mirrors it."""
from __future__ import annotations

import fcntl
import re
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from scripts.config import Settings, load_settings
from scripts.ticket_sync import md_utils as md

EDITABLE_META = {"title", "priority", "labels", "assignee_bot", "github_issue", "github_project_item_id"}
SECTION_FIELDS = {
    "objective": "Objective",
    "context": "Context",
    "acceptance_criteria": "Acceptance Criteria",
    "attachments": "Attachments",
    "final_review": "Final Review",
}


class TicketError(ValueError):
    pass


class TicketStore:
    def __init__(self, settings: Settings | None = None, syncer: Callable[[str, str], Any] | None = None):
        self.s = settings or load_settings()
        self.syncer = syncer  # callable(ticket_id, event) -> result; default uses github_sync
        for col in self.s.columns:
            (self.s.tickets_dir / col).mkdir(parents=True, exist_ok=True)
        self.s.attachments_dir.mkdir(parents=True, exist_ok=True)

    # ---------- locking / paths ----------
    @contextmanager
    def _lock(self):
        lock_path = self.s.state_dir / "tickets.lock"
        with open(lock_path, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def _validate_column(self, col: str) -> str:
        col = (col or "").strip().lower().replace("_", "-").replace(" ", "-")
        aliases = {"todo": "to-do", "inprogress": "in-progress", "inreview": "in-review", "review": "in-review"}
        col = aliases.get(col, col)
        if col not in self.s.columns:
            raise TicketError(f"unknown column {col!r}; expected one of {self.s.columns}")
        return col

    @staticmethod
    def _validate_id(ticket_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", ticket_id or ""):
            raise TicketError(f"invalid ticket id {ticket_id!r}")
        return ticket_id

    def find(self, ticket_id: str) -> Path:
        self._validate_id(ticket_id)
        hits = [p for col in self.s.columns for p in [(self.s.tickets_dir / col / f"{ticket_id}.md")] if p.exists()]
        if not hits:
            raise TicketError(f"no such ticket: {ticket_id}")
        if len(hits) > 1:
            raise TicketError(f"ticket {ticket_id} exists in several columns: {[str(h) for h in hits]}")
        return hits[0]

    def _read(self, path: Path) -> tuple[dict, str]:
        meta, body = md.parse(path.read_text(encoding="utf-8"))
        meta["status"] = path.parent.name  # folder is canonical
        return meta, body

    def _write(self, path: Path, meta: dict, body: str) -> None:
        meta["status"] = path.parent.name
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(md.render(meta, body), encoding="utf-8")
        tmp.replace(path)

    # ---------- post-mutation hooks ----------
    def _after(self, ticket_id: str, event: str, message: str) -> dict:
        result: dict = {}
        if self.s.sync_after_mutation:
            try:
                if self.syncer is not None:
                    result["github"] = self.syncer(ticket_id, event)
                else:
                    from scripts.ticket_sync.github_sync import sync_ticket
                    result["github"] = sync_ticket(ticket_id, event=event, store=self)
            except Exception as e:  # sync failures never lose the local change
                result["github"] = {"ok": False, "error": str(e)}
        if self.s.auto_commit:
            result["commit"] = self._git_commit(message)
        return result

    def _git_commit(self, message: str) -> str:
        root = self.s.root
        if not (root / ".git").exists():
            return "skipped (control repo is not a git repo)"
        try:
            subprocess.run(["git", "-C", str(root), "add", "-A", "tickets", "attachments"], check=True, capture_output=True)
            r = subprocess.run(["git", "-C", str(root), "diff", "--cached", "--quiet", "--", "tickets", "attachments"])
            if r.returncode == 0:
                return "nothing to commit"
            subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", message, "--", "tickets", "attachments"],
                           check=True, capture_output=True)
            return subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        except subprocess.CalledProcessError as e:
            return f"commit failed: {(e.stderr or b'').decode(errors='replace')[:200]}"

    # ---------- API ----------
    def create(self, title: str, objective: str, context: list[str] | str | None = None,
               acceptance_criteria: list[str] | None = None, attachments: list[str] | None = None,
               priority: str = "medium", labels: list[str] | None = None, status: str = "backlog",
               ticket_id: str | None = None) -> dict:
        if not title or not title.strip():
            raise TicketError("title is required")
        if priority not in md.PRIORITIES:
            raise TicketError(f"priority must be one of {md.PRIORITIES}")
        status = self._validate_column(status)
        with self._lock():
            base = ticket_id or f"{md.today()}_{md.slugify(title)}"
            self._validate_id(base)
            tid, n = base, 2
            while any((self.s.tickets_dir / c / f"{tid}.md").exists() for c in self.s.columns):
                tid = f"{base}-{n}"
                n += 1
            stored = self._store_attachments(tid, attachments or [])
            ctx = context if isinstance(context, str) else md.bullet_list(context, empty="- (none)")
            body = md.build_body({
                "Objective": objective.strip(),
                "Context": ctx,
                "Acceptance Criteria": md.bullet_list(acceptance_criteria, checkbox=True, empty="- [ ] (to be defined)"),
                "Attachments": md.bullet_list(stored),
                "Execution Log": "",
                "Final Review": "- (filled when done)",
            })
            meta = {
                "id": tid, "title": title.strip(), "status": status, "assignee_bot": None,
                "github_issue": None, "github_project_item_id": None, "priority": priority,
                "labels": list(labels or []), "created_at": md.today(), "updated_at": md.today(),
            }
            path = self.s.tickets_dir / status / f"{tid}.md"
            self._write(path, meta, body)
        extra = self._after(tid, "create", f"tickets: create {tid}")
        return {"ticket_id": tid, "path": str(path.relative_to(self.s.root)), **extra}

    def _store_attachments(self, tid: str, attachments: list[str]) -> list[str]:
        out = []
        for a in attachments:
            src = Path(a).expanduser()
            if not src.is_absolute():
                src = (self.s.root / a)
            if src.is_file():
                dest_dir = self.s.attachments_dir / tid
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / src.name
                if src.resolve() != dest.resolve():
                    shutil.copy2(src, dest)
                out.append(str(dest.relative_to(self.s.root)))
            else:
                out.append(str(a))  # a URL or a reference; kept as-is
        return out

    def attachment_paths(self, ticket_id: str) -> list[Path]:
        d = self.s.attachments_dir / ticket_id
        return sorted(p for p in d.iterdir() if p.is_file()) if d.exists() else []

    def get(self, ticket_id: str) -> dict:
        path = self.find(ticket_id)
        meta, body = self._read(path)
        return {
            "ticket_id": ticket_id, "path": str(path.relative_to(self.s.root)), "metadata": meta,
            "sections": md.sections(body), "acceptance_criteria": md.acceptance_criteria(body),
            "markdown": path.read_text(encoding="utf-8"),
        }

    def update(self, ticket_id: str, patch: dict) -> dict:
        if not isinstance(patch, dict) or not patch:
            raise TicketError("patch must be a non-empty object")
        with self._lock():
            path = self.find(ticket_id)
            meta, body = self._read(path)
            changed = []
            for k, v in patch.items():
                if k in ("status", "id", "created_at"):
                    raise TicketError(f"{k} cannot be patched (use ticket_move for status)")
                if k in EDITABLE_META:
                    if k == "priority" and v not in md.PRIORITIES:
                        raise TicketError(f"priority must be one of {md.PRIORITIES}")
                    if k == "labels" and not isinstance(v, list):
                        raise TicketError("labels must be a list")
                    meta[k] = v
                elif k in SECTION_FIELDS:
                    if isinstance(v, list):
                        v = md.bullet_list(v, checkbox=(k == "acceptance_criteria"))
                    body = md.set_section(body, SECTION_FIELDS[k], str(v))
                elif k == "sections" and isinstance(v, dict):
                    for name, content in v.items():
                        body = md.set_section(body, str(name), str(content))
                else:
                    raise TicketError(f"unknown patch field {k!r}")
                changed.append(k)
            meta["updated_at"] = md.today()
            self._write(path, meta, body)
        extra = self._after(ticket_id, "update", f"tickets: update {ticket_id} ({', '.join(changed)})")
        return {"ticket_id": ticket_id, "changed": changed, **extra}

    def move(self, ticket_id: str, to_column: str) -> dict:
        to_column = self._validate_column(to_column)
        with self._lock():
            path = self.find(ticket_id)
            frm = path.parent.name
            meta, body = self._read(path)
            dest = self.s.tickets_dir / to_column / path.name
            if frm != to_column:
                path.replace(dest)
            meta["updated_at"] = md.today()
            self._write(dest, meta, body)
        extra = self._after(ticket_id, "move", f"tickets: move {ticket_id} {frm} -> {to_column}")
        return {"ticket_id": ticket_id, "from": frm, "to": to_column, **extra}

    def assign(self, ticket_id: str, bot_id: str | None) -> dict:
        if bot_id:
            reg = self.s.registry()
            if reg and bot_id not in reg:
                raise TicketError(f"unknown bot {bot_id!r}; registered: {sorted(reg)}")
        with self._lock():
            path = self.find(ticket_id)
            meta, body = self._read(path)
            meta["assignee_bot"] = bot_id or None
            meta["updated_at"] = md.today()
            self._write(path, meta, body)
        extra = self._after(ticket_id, "assign", f"tickets: assign {ticket_id} -> {bot_id}")
        return {"ticket_id": ticket_id, "assignee_bot": bot_id, **extra}

    def add_log(self, ticket_id: str, heading: str, lines: list[str], section: str = "Execution Log",
                sync: bool = True) -> dict:
        with self._lock():
            path = self.find(ticket_id)
            meta, body = self._read(path)
            entry = f"## {heading}\n" + "\n".join(f"- {l}" if not l.startswith("- ") else l for l in lines if l)
            body = md.append_section(body, section, entry)
            meta["updated_at"] = md.today()
            self._write(path, meta, body)
        extra = self._after(ticket_id, "update", f"tickets: log {ticket_id}: {heading}") if sync else {}
        return {"ticket_id": ticket_id, "heading": heading, **extra}

    def add_review_note(self, ticket_id: str, note: str, turn: int | None = None, final: bool = False) -> dict:
        if not note or not note.strip():
            raise TicketError("note is required")
        path = self.find(ticket_id)
        _, body = self._read(path)
        if final:
            with self._lock():
                meta, body = self._read(path)
                body = md.set_section(body, "Final Review", note.strip())
                meta["updated_at"] = md.today()
                self._write(path, meta, body)
            extra = self._after(ticket_id, "update", f"tickets: final review {ticket_id}")
            return {"ticket_id": ticket_id, "final": True, **extra}
        turn = turn or max(md.count_turns(body), 1)
        lines = [l.rstrip() for l in note.strip().splitlines() if l.strip()]
        lines.insert(0, f"Reviewed at {md.now_stamp()}")
        return self.add_log(ticket_id, f"Turn {turn} Review (superagent)", lines)

    def list(self, status: str | None = None, assignee_bot: str | None = None) -> list[dict]:
        cols = [self._validate_column(status)] if status else self.s.columns
        out = []
        for col in cols:
            for p in sorted((self.s.tickets_dir / col).glob("*.md")):
                try:
                    meta, _ = self._read(p)
                except Exception as e:  # keep listing even if one file is broken
                    out.append({"ticket_id": p.stem, "status": col, "error": str(e)})
                    continue
                if assignee_bot and meta.get("assignee_bot") != assignee_bot:
                    continue
                out.append({k: meta.get(k) for k in ("id", "title", "status", "assignee_bot", "priority",
                                                       "labels", "github_issue", "updated_at")})
        return out

    def set_meta_quiet(self, ticket_id: str, **fields) -> None:
        """Write sync results (github ids) back without triggering another sync."""
        with self._lock():
            path = self.find(ticket_id)
            meta, body = self._read(path)
            meta.update(fields)
            self._write(path, meta, body)
