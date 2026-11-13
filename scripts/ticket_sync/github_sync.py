"""Sync Markdown tickets to GitHub Issues + GitHub Projects (v2).

Markdown is authoritative. Mapping:
  title                -> issue title
  body sections        -> issue body
  labels               -> issue labels (+ status/<column>, bot/<bot-id>, priority/<p>)
  status (folder)      -> Projects v2 "Status" single-select option; done -> issue closed
  assignee_bot         -> Projects v2 "Bot" single-select custom field
  priority             -> Projects v2 "Priority" single-select custom field

Auth: GITHUB_TOKEN (classic/fine-grained PAT or a GitHub App installation token).
Target: GITHUB_REPO=owner/name, GITHUB_PROJECT=owner/NUMBER or NUMBER (optional).
With no token (or --dry-run) nothing is sent: the planned actions are logged to
.state/github-sync.log and returned.

CLI:  python -m scripts.ticket_sync.github_sync --all [--dry-run]
      python -m scripts.ticket_sync.github_sync --ticket <id>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.config import load_settings  # noqa: E402

API = "https://api.github.com"
Transport = Callable[[str, str, dict | None], tuple[int, Any]]


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def default_transport(token: str) -> Transport:
    def call(method: str, url: str, body: dict | None) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url if url.startswith("http") else API + url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("X-GitHub-Api-Version", "2022-11-28")
        req.add_header("User-Agent", "aibots-ticket-sync")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"message": raw.decode(errors="replace")[:500]}
    return call


class GitHubSync:
    def __init__(self, store, token: str | None = None, repo: str | None = None, project: str | None = None,
                 transport: Transport | None = None, dry_run: bool | None = None):
        self.store = store
        s = store.s
        gh = s.github
        self.token = token if token is not None else os.environ.get(gh.get("token_env", "GITHUB_TOKEN"), "")
        self.repo = repo if repo is not None else (os.environ.get(gh.get("repo_env", "GITHUB_REPO"), "") or gh.get("repo", "") or "")
        self.project = project if project is not None else (os.environ.get(gh.get("project_env", "GITHUB_PROJECT"), "") or gh.get("project", "") or "")
        self.status_field = gh.get("status_field", "Status")
        self.bot_field = gh.get("bot_field", "Bot")
        self.priority_field = gh.get("priority_field", "Priority")
        self.status_labels = gh.get("status_labels", True)
        self.bot_labels = gh.get("bot_labels", True)
        self.dry_run = dry_run if dry_run is not None else not (self.token and self.repo)
        self.http = transport or (default_transport(self.token) if self.token else None)
        self.log_path = s.state_dir / "github-sync.log"
        self.cache_path = s.state_dir / "github-project-cache.json"
        self.actions: list[str] = []
        self.warnings: list[str] = []

    # ---------- helpers ----------
    def _log(self, msg: str) -> None:
        self.actions.append(msg)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now().isoformat(timespec='seconds')} {'[dry-run] ' if self.dry_run else ''}{msg}\n")

    def _req(self, method: str, url: str, body: dict | None = None) -> Any:
        status, data = self.http(method, url, body)
        if status >= 400:
            raise RuntimeError(f"GitHub {method} {url} -> {status}: {(data or {}).get('message', data)}")
        return data

    def _gql(self, query: str, variables: dict) -> dict:
        data = self._req("POST", "/graphql", {"query": query, "variables": variables})
        if data and data.get("errors") and not data.get("data"):
            raise RuntimeError(f"GraphQL error: {data['errors'][0].get('message')}")
        return (data or {}).get("data") or {}

    def labels_for(self, meta: dict) -> list[str]:
        labels = [str(l) for l in (meta.get("labels") or [])]
        if self.status_labels:
            labels.append(f"status/{meta['status']}")
        if self.bot_labels and meta.get("assignee_bot"):
            labels.append(f"bot/{meta['assignee_bot']}")
        if meta.get("priority"):
            labels.append(f"priority/{meta['priority']}")
        return sorted(set(labels))

    @staticmethod
    def issue_body(ticket: dict) -> str:
        secs = ticket["sections"]
        parts = []
        for name in ["Objective", "Context", "Acceptance Criteria", "Attachments", "Execution Log", "Final Review"]:
            if name in secs:
                parts.append(f"## {name}\n{secs[name].strip() or '_(empty)_'}")
        parts.append(f"---\n_Synced from `{ticket['path']}` (Markdown ticket `{ticket['ticket_id']}` is the source of truth)._")
        return "\n\n".join(parts)

    # ---------- project v2 ----------
    def _project_ref(self) -> tuple[str, int] | None:
        if not self.project:
            return None
        if "/" in self.project:
            owner, num = self.project.rsplit("/", 1)
        else:
            owner, num = self.repo.split("/", 1)[0], self.project
        return owner, int(num)

    def project_meta(self) -> dict | None:
        ref = self._project_ref()
        if not ref:
            return None
        key = f"{ref[0]}/{ref[1]}"
        if self.cache_path.exists():
            cache = json.loads(self.cache_path.read_text())
            if cache.get("key") == key:
                return cache
        fields_q = ("projectV2(number:$number){ id fields(first:50){ nodes{ "
                    "... on ProjectV2SingleSelectField { id name options { id name } } "
                    "... on ProjectV2FieldCommon { id name } } } }")
        proj = None
        for kind in ("organization", "user"):
            try:
                d = self._gql(f"query($owner:String!,$number:Int!){{ {kind}(login:$owner){{ {fields_q} }} }}",
                              {"owner": ref[0], "number": ref[1]})
                proj = ((d.get(kind) or {}).get("projectV2"))
            except RuntimeError:
                proj = None
            if proj:
                break
        if not proj:
            raise RuntimeError(f"Project {key} not found or token lacks project scope")
        fields = {}
        for f in proj["fields"]["nodes"]:
            if f and f.get("name"):
                fields[f["name"]] = {"id": f["id"], "options": {o["name"]: o["id"] for o in f.get("options", [])}}
        meta = {"key": key, "id": proj["id"], "fields": fields}
        self.cache_path.write_text(json.dumps(meta, indent=2))
        return meta

    def _set_single_select(self, pmeta: dict, item_id: str, field: str, value: str | None) -> None:
        if not value:
            return
        f = pmeta["fields"].get(field)
        if not f:
            self.warnings.append(f"project has no field {field!r}")
            return
        opt = next((oid for name, oid in f["options"].items() if _norm(name) == _norm(value)), None)
        if not opt:
            self.warnings.append(f"field {field!r} has no option {value!r} (add it in the project settings)")
            return
        self._log(f"project item {item_id}: set {field} = {value}")
        if not self.dry_run:
            self._gql("mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){ updateProjectV2ItemFieldValue(input:{projectId:$p,"
                      "itemId:$i,fieldId:$f,value:{singleSelectOptionId:$o}}){ projectV2Item{ id } } }",
                      {"p": pmeta["id"], "i": item_id, "f": f["id"], "o": opt})

    # ---------- main ----------
    def sync(self, ticket_id: str, event: str = "update") -> dict:
        t = self.store.get(ticket_id)
        meta = t["metadata"]
        title, body, labels = meta.get("title", ticket_id), self.issue_body(t), self.labels_for(meta)
        state = "closed" if meta["status"] == "done" else "open"
        number, node_id, item_id = meta.get("github_issue"), None, meta.get("github_project_item_id")
        target = self.repo or "<GITHUB_REPO unset>"

        if not number:
            self._log(f"{ticket_id} ({event}): create issue in {target}: title={title!r} labels={labels}")
            if not self.dry_run:
                data = self._req("POST", f"/repos/{self.repo}/issues", {"title": title, "body": body, "labels": labels})
                number, node_id = data["number"], data["node_id"]
                self.store.set_meta_quiet(ticket_id, github_issue=number)
                if state == "closed":
                    self._req("PATCH", f"/repos/{self.repo}/issues/{number}", {"state": "closed"})
        else:
            self._log(f"{ticket_id} ({event}): update issue #{number} in {target}: state={state} labels={labels}")
            if not self.dry_run:
                data = self._req("PATCH", f"/repos/{self.repo}/issues/{number}",
                                 {"title": title, "body": body, "labels": labels, "state": state,
                                  **({"state_reason": "completed"} if state == "closed" else {})})
                node_id = data.get("node_id")

        if self.project:
            if self.dry_run:
                self._log(f"{ticket_id}: project {self.project}: ensure item; set {self.status_field}={meta['status']}, "
                          f"{self.bot_field}={meta.get('assignee_bot')}, {self.priority_field}={meta.get('priority')}")
            else:
                pmeta = self.project_meta()
                if not item_id:
                    if not node_id:
                        node_id = self._req("GET", f"/repos/{self.repo}/issues/{number}")["node_id"]
                    d = self._gql("mutation($p:ID!,$c:ID!){ addProjectV2ItemById(input:{projectId:$p,contentId:$c}){ item{ id } } }",
                                  {"p": pmeta["id"], "c": node_id})
                    item_id = d["addProjectV2ItemById"]["item"]["id"]
                    self._log(f"{ticket_id}: added to project as {item_id}")
                    self.store.set_meta_quiet(ticket_id, github_project_item_id=item_id)
                self._set_single_select(pmeta, item_id, self.status_field, meta["status"])
                self._set_single_select(pmeta, item_id, self.bot_field, meta.get("assignee_bot"))
                self._set_single_select(pmeta, item_id, self.priority_field, meta.get("priority"))
        elif not self.dry_run:
            self.warnings.append("GITHUB_PROJECT not set: only the issue was synced")

        for w in self.warnings:
            self._log(f"WARNING {ticket_id}: {w}")
        return {"ok": True, "mode": "dry-run" if self.dry_run else "live", "issue": number,
                "project_item": item_id, "actions": self.actions, "warnings": self.warnings}


def sync_ticket(ticket_id: str, event: str = "update", store=None, **kw) -> dict:
    if store is None:
        from scripts.ticket_sync.store import TicketStore
        store = TicketStore()
    return GitHubSync(store, **kw).sync(ticket_id, event)


def main(argv: list[str] | None = None) -> int:
    from scripts.ticket_sync.store import TicketStore
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticket")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="never call GitHub, even with a token")
    a = ap.parse_args(argv)
    os.environ["AIBOTS_GITHUB_SYNC"] = "0"  # avoid recursive sync from store hooks
    store = TicketStore(load_settings())
    ids = [a.ticket] if a.ticket else [t["id"] for t in store.list()]
    results = {tid: sync_ticket(tid, event="manual", store=store, dry_run=True if a.dry_run else None) for tid in ids}
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
