"""Risk assessment for pending worker permission-gate approvals.

Used by the Superagent watchdog. Conservative and explicit: only clearly
low-risk requests may be auto-approved (well-known data libraries from a
normal package index; already-allowed or official/safe hosts). Everything
else is high risk: reject, suggest a counter-proposal that still serves the
user's high-level intent, and never claim the gate approved it.

Never invent figures, copy leftover artifacts as fresh official results,
tick official AC, mark Done to clear a stall, auto-buy, or log in.
"""
from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Iterable

from scripts.permission_gate.gate import (
    host_is_allowed,
    normalize_host,
    split_segments,
)

# Well-known data libraries that only help read/plot official public data.
# This list is the low-risk allowlist; anything else is high risk.
LOW_RISK_PYPI_PACKAGES = frozenset({
    "xlrd", "xlwt", "xlutils", "openpyxl", "pyxlsb", "python-calamine", "odfpy",
    "html5lib", "lxml", "beautifulsoup4", "bs4",
    "pandas", "numpy", "matplotlib", "plotly", "kaleido",
    "python-dateutil", "pytz", "chardet", "charset-normalizer",
})

# Harmless pip flags that do not change the index or widen trust.
_SAFE_PIP_FLAGS = frozenset({
    "-q", "--quiet", "-v", "--verbose", "-U", "--upgrade",
    "--no-cache-dir", "--no-deps", "--prefer-binary",
    "--disable-pip-version-check", "--no-warn-script-location", "--user",
})
_SAFE_PIP_FLAGS_WITH_VALUE = frozenset({
    "--upgrade-strategy", "--only-binary", "--timeout", "--retries",
})
_UNSAFE_PIP_FLAGS = frozenset({
    "--index-url", "-i", "--extra-index-url", "--find-links", "-f",
    "--trusted-host", "--no-index", "--pre", "--requirement", "-r",
    "--editable", "-e", "--constraint", "-c", "--src", "--prefix",
    "--root", "--target", "-t", "--config-settings",
})

_PIP_HEAD = re.compile(
    r"^(?:python3?\s+-m\s+)?(?:pip3?|uv\s+pip|pipx)\s+install\b",
    re.I,
)
_PKG_TOKEN = re.compile(
    r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]+\])?(?:(?:===|==|!=|<=|>=|~=|<|>).+)?$"
)
_CD_RE = re.compile(r"^cd\b", re.I)
_APT_RE = re.compile(r"^(?:sudo\s+)?(?:apt|apt-get|brew)\s+", re.I)
_NPM_RE = re.compile(r"^(?:npm|pnpm|yarn|bun)\s+(?:install|i|add)\b", re.I)
_DESTRUCTIVE_RE = re.compile(
    r"^(?:rm\s+-[a-zA-Z]*r|git\s+(?:reset\s+--hard|clean\s+-[a-zA-Z]*f|push)|"
    r"(?:ssh|scp|nc|ncat|telnet)\b)",
    re.I,
)
_FIND_ROOT_RE = re.compile(r"^find\s+/\s", re.I)

_APPROVAL_CLAIM_RE = re.compile(
    r"\bhas been approved\b|\bhas been allowed\b|\bnow allows\b",
    re.I,
)


@dataclass
class RiskAssessment:
    level: str  # low | high
    category: str
    rationale: str
    approve: bool
    counter_proposal: str | None = None
    packages: list[str] = field(default_factory=list)
    host: str | None = None


def normalize_pkg(name: str) -> str:
    return (name or "").strip().lower().replace("_", "-")


def _norm_packages(extra: Iterable[str] | None) -> set[str]:
    out = set(LOW_RISK_PYPI_PACKAGES)
    for name in extra or []:
        n = normalize_pkg(str(name))
        if n:
            out.add(n)
    return out


def _intent_text(intent) -> str:
    if intent is None:
        return ""
    if isinstance(intent, str):
        return intent
    parts = [
        getattr(intent, "high_level", "") or "",
        getattr(intent, "objective", "") or "",
        getattr(intent, "context", "") or "",
        getattr(intent, "title", "") or "",
        getattr(intent, "summary", "") or "",
    ]
    if isinstance(intent, dict):
        parts = [str(intent.get(k) or "") for k in
                 ("high_level", "objective", "context", "title", "summary")]
    return " ".join(p for p in parts if p)


def _official_source_counter(intent_text: str, host: str | None = None) -> str:
    blocked = f" Do not contact {host}." if host else ""
    text = intent_text or ""
    hk = bool(re.search(r"hong\s*kong|\bhk\b", text, re.I) and
              re.search(r"resident|propert|housing|price|sales", text, re.I))
    if hk:
        return (
            f"{blocked} Continue the existing ticket using official public sources "
            "already allowed, such as rvd.gov.hk, data.gov.hk, and Land Registry "
            "open monthly statistics on landreg.gov.hk. Do not use paid or "
            "login-walled sources, do not invent figures, do not copy leftover "
            "artifacts and call them fresh official results, and do not tick or "
            "shrink official acceptance-criteria checkboxes."
        ).strip()
    return (
        f"{blocked} Continue the existing ticket with already-allowed official "
        "public / open-data sources that serve the user's original high-level "
        "intent. Do not use paid or login-walled sources, do not invent figures, "
        "do not copy leftover artifacts and call them fresh official results, "
        "and do not tick or shrink official acceptance-criteria checkboxes."
    ).strip()


def assess_host(
    host: str,
    *,
    allowed_hosts: set[str] | None = None,
    safe_suffixes: list[str] | None = None,
    intent=None,
) -> RiskAssessment:
    host = normalize_host(host)
    allowed = {normalize_host(h) for h in (allowed_hosts or set()) if normalize_host(h)}
    suffixes = [normalize_host(s) for s in (safe_suffixes or []) if normalize_host(s)]
    if host and host_is_allowed(host, allowed):
        return RiskAssessment(
            level="low",
            category="host",
            rationale=f"Host {host} is already on the allowlist.",
            approve=True,
            host=host,
        )
    if host and suffixes and host_is_allowed(host, set(suffixes)):
        return RiskAssessment(
            level="low",
            category="host",
            rationale=(
                f"Host {host} matches official/safe open-data suffix "
                f"({', '.join(suffixes)}); allowing it does not open the rest of the internet."
            ),
            approve=True,
            host=host,
        )
    return RiskAssessment(
        level="high",
        category="host",
        rationale=(
            f"Host {host} is not on the official/safe suffix list "
            f"({', '.join(suffixes) or 'none configured'}) and is not already allowed. "
            "Unknown hosts are not auto-approved."
        ),
        approve=False,
        counter_proposal=_official_source_counter(_intent_text(intent), host),
        host=host,
    )


def _parse_pip_packages(segment: str, allowed_pkgs: set[str]) -> RiskAssessment | None:
    if not _PIP_HEAD.search(segment.strip()):
        return None
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return RiskAssessment(
            level="high", category="pip_install", approve=False,
            rationale="pip install command could not be parsed safely.",
            counter_proposal=_install_counter(),
        )
    # Locate the install verb
    install_at = None
    for i, tok in enumerate(tokens):
        if tok.lower() == "install":
            install_at = i
            break
    if install_at is None:
        return RiskAssessment(
            level="high", category="pip_install", approve=False,
            rationale="command looks like pip but has no install verb.",
            counter_proposal=_install_counter(),
        )
    packages: list[str] = []
    skip_next = False
    for tok in tokens[install_at + 1:]:
        if skip_next:
            skip_next = False
            continue
        if tok in _UNSAFE_PIP_FLAGS or tok.startswith("--index-url=") or tok.startswith("--extra-index-url="):
            return RiskAssessment(
                level="high", category="pip_install", approve=False,
                rationale=(
                    f"pip flag {tok} would change the package index, trust, or install "
                    "source and is not auto-approved."
                ),
                counter_proposal=_install_counter(),
            )
        if tok in _SAFE_PIP_FLAGS:
            continue
        name = tok.split("=", 1)[0]
        if name in _SAFE_PIP_FLAGS_WITH_VALUE or tok.split("=")[0] in _SAFE_PIP_FLAGS_WITH_VALUE:
            if "=" not in tok:
                skip_next = True
            continue
        if tok.startswith("-"):
            return RiskAssessment(
                level="high", category="pip_install", approve=False,
                rationale=f"unrecognized pip flag {tok} is not auto-approved.",
                counter_proposal=_install_counter(),
            )
        if "://" in tok or tok.startswith("git+") or tok.startswith("file:"):
            return RiskAssessment(
                level="high", category="pip_install", approve=False,
                rationale="pip install from a URL or VCS source is not auto-approved.",
                counter_proposal=_install_counter(),
            )
        m = _PKG_TOKEN.fullmatch(tok)
        if not m:
            return RiskAssessment(
                level="high", category="pip_install", approve=False,
                rationale=f"package token {tok!r} is not a simple PyPI name and is not auto-approved.",
                counter_proposal=_install_counter(),
            )
        packages.append(normalize_pkg(m.group(1)))
    if not packages:
        return RiskAssessment(
            level="high", category="pip_install", approve=False,
            rationale="pip install with no package list is not auto-approved.",
            counter_proposal=_install_counter(),
        )
    unknown = [p for p in packages if p not in allowed_pkgs]
    if unknown:
        return RiskAssessment(
            level="high", category="pip_install", approve=False,
            rationale=(
                "pip install includes packages not on the low-risk data-library "
                f"allowlist: {', '.join(unknown)}. Unknown or access-expanding "
                "packages are not auto-approved."
            ),
            counter_proposal=_install_counter(unknown),
            packages=packages,
        )
    listed = ", ".join(packages)
    return RiskAssessment(
        level="low",
        category="pip_install",
        approve=True,
        rationale=(
            f"Installing well-known data libraries ({listed}) from the default "
            "package index with no index/trust flags is low risk."
        ),
        packages=packages,
    )


def _install_counter(unknown: list[str] | None = None) -> str:
    extra = f" Do not install {', '.join(unknown)}." if unknown else ""
    return (
        f"{extra} Use packages already installed (including the Python standard "
        "library) and official public data sources already allowed. If a clearly "
        "low-risk data library (for example xlrd, openpyxl, or html5lib) is still "
        "required, request a simple pip install of only those packages with no "
        "custom index, --trusted-host, or URL source. Do not use apt/npm or paid/"
        "login-walled sources, do not invent figures, do not copy leftover artifacts "
        "and call them fresh official results, and do not tick official AC."
    ).strip()


def _high(category: str, rationale: str, counter: str) -> RiskAssessment:
    return RiskAssessment(level="high", category=category, rationale=rationale,
                          approve=False, counter_proposal=counter)


def assess_command(
    command: str,
    *,
    intent=None,
    extra_packages: Iterable[str] | None = None,
) -> RiskAssessment:
    cmd = (command or "").strip()
    if not cmd:
        return _high("unclear", "Empty or missing command is not auto-approved.",
                     "Report the blocked step and continue with already-allowed tools. "
                     "Do not invent data or tick official AC.")
    allowed_pkgs = _norm_packages(extra_packages)
    assessments: list[RiskAssessment] = []
    for seg in split_segments(cmd):
        seg = seg.strip()
        if not seg:
            continue
        pip = _parse_pip_packages(seg, allowed_pkgs)
        if pip:
            assessments.append(pip)
            continue
        if _CD_RE.search(seg):
            # Classification only; the exact full command is what the gate hashes.
            continue
        if _APT_RE.search(seg):
            assessments.append(_high(
                "system_package",
                "apt/brew installs expand system packages and are not auto-approved.",
                "Do not use apt-get or brew. Prefer already-installed Python packages "
                "or a simple pip install of well-known data libraries if still needed. "
                + _official_source_counter(_intent_text(intent)),
            ))
            continue
        if _NPM_RE.search(seg):
            assessments.append(_high(
                "system_package",
                "npm/pnpm/yarn/bun install is not on the low-risk allowlist.",
                "Use already-installed packages and already-allowed tools. "
                "Do not invent data or tick official AC.",
            ))
            continue
        if _DESTRUCTIVE_RE.search(seg):
            assessments.append(_high(
                "destructive",
                f"Command segment {seg!r} is destructive or expands remote access "
                "and is not auto-approved.",
                "Do not delete, force-push, or open an ssh/nc session. Keep work in "
                "the existing ticket and workspace. Do not invent figures, do not copy "
                "leftover artifacts and call them fresh official results, and do not "
                "tick official AC.",
            ))
            continue
        if _FIND_ROOT_RE.search(seg) or re.search(r"\s/\s|\s/$\s*|find\s+/", seg):
            if re.search(r"\bfind\s+/", seg) or re.search(r"\s/(?:home|etc|root|var|usr)\b", seg):
                assessments.append(_high(
                    "path",
                    "Filesystem search outside the bot workspace is not auto-approved.",
                    "Search only inside the bot workspace. Do not copy leftover artifacts "
                    "from elsewhere and present them as fresh official results. Do not "
                    "invent figures or tick official AC.",
                ))
                continue
        if re.search(r"\b(pip3?|npm|apt-get|apt|brew|sudo)\b", seg, re.I):
            assessments.append(_high(
                "unclear",
                f"Command segment {seg!r} is not a clearly low-risk install and is not auto-approved.",
                _install_counter(),
            ))
            continue
        # Other needs_approval (paths, ssh leftovers, unclear)
        assessments.append(_high(
            "unclear",
            f"Command segment {seg!r} is not on the conservative low-risk list.",
            "Skip this command. Continue with already-allowed tools and official "
            "public sources that serve the user's high-level intent. Do not invent "
            "data, do not copy leftovers as fresh official results, and do not tick official AC.",
        ))
    if not assessments:
        # Only `cd` segments or empty — not an install we can bless.
        return _high(
            "unclear",
            "No clearly low-risk install was found in the command.",
            "Continue with already-allowed tools. Do not invent data or tick official AC.",
        )
    highs = [a for a in assessments if not a.approve]
    if highs:
        first = highs[0]
        return first
    lows = [a for a in assessments if a.approve]
    first = lows[0]
    if len(lows) > 1:
        pkgs: list[str] = []
        for a in lows:
            pkgs.extend(a.packages)
        first.packages = pkgs
    return first


def assess_pending(
    *,
    command: str | None = None,
    host: str | None = None,
    allowed_hosts: set[str] | None = None,
    safe_suffixes: list[str] | None = None,
    intent=None,
    extra_packages: Iterable[str] | None = None,
) -> RiskAssessment:
    """Evaluate a pending host or command approval."""
    if host:
        return assess_host(
            host,
            allowed_hosts=allowed_hosts,
            safe_suffixes=safe_suffixes,
            intent=intent,
        )
    return assess_command(command or "", intent=intent, extra_packages=extra_packages)


def claims_gate_approval(text: str) -> bool:
    """True if briefing text tells the worker something has been approved/allowed."""
    return bool(_APPROVAL_CLAIM_RE.search(text or ""))
