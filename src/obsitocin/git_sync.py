"""Git vault synchronization for multi-device knowledge sharing."""

from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from obsitocin.config import GIT_REMOTE, OBS_DIR, VAULT_DIR


class SyncStatus(Enum):
    SUCCESS = "success"
    NO_GIT = "no_git"
    NO_REMOTE = "no_remote"
    CONFLICT = "conflict"
    NOTHING_TO_SYNC = "nothing_to_sync"
    ERROR = "error"


@dataclass
class SyncResult:
    status: SyncStatus
    files_committed: int = 0
    files_pulled: int = 0
    hostname: str = ""
    commit_sha: str = ""
    message: str = ""
    conflicts: list[str] = field(default_factory=list)


# ── Generated file patterns (auto-resolved with "ours" on conflict) ──

_GENERATED_PATTERNS = ("_MOC.md", "_index.md")


def _is_generated_file(path: str) -> bool:
    """Check if a file path matches a generated file pattern."""
    return any(path.endswith(p) for p in _GENERATED_PATTERNS)


# ── Git primitives ──

def _run_git(
    args: list[str], cwd: Path, *, timeout: int = 30
) -> subprocess.CompletedProcess:
    """Run a git command. Raises FileNotFoundError if git not found."""
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def is_git_repo(vault_dir: Path) -> bool:
    try:
        r = _run_git(["rev-parse", "--is-inside-work-tree"], vault_dir)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def has_remote(vault_dir: Path) -> bool:
    try:
        r = _run_git(["remote"], vault_dir)
        return r.returncode == 0 and r.stdout.strip() != ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def get_remote_name(vault_dir: Path) -> str:
    """Return configured remote or 'origin'."""
    return GIT_REMOTE or "origin"


def get_current_branch(vault_dir: Path) -> str:
    try:
        r = _run_git(["branch", "--show-current"], vault_dir)
        return r.stdout.strip() or "main"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "main"


def get_hostname() -> str:
    raw = platform.node()
    if raw.endswith(".local"):
        raw = raw[:-6]
    clean = re.sub(r"[^a-zA-Z0-9._-]", "-", raw)
    return clean[:50] or "unknown-host"


# ── Conflict resolution ──

def _get_conflicted_files(vault_dir: Path) -> list[str]:
    """List files with merge conflicts."""
    r = _run_git(["diff", "--name-only", "--diff-filter=U"], vault_dir)
    if r.returncode != 0:
        return []
    return [f for f in r.stdout.strip().splitlines() if f]


def _resolve_conflicts(vault_dir: Path) -> list[str]:
    """Auto-resolve generated files with 'ours'. Returns unresolved files."""
    conflicted = _get_conflicted_files(vault_dir)
    unresolved = []
    for path in conflicted:
        if _is_generated_file(path):
            _run_git(["checkout", "--ours", path], vault_dir)
            _run_git(["add", path], vault_dir)
        else:
            unresolved.append(path)
    return unresolved


# ── Sync workflow steps ──

def git_pull(vault_dir: Path, remote: str, branch: str) -> tuple[bool, list[str]]:
    """git pull --no-rebase. Returns (success, conflict_files)."""
    r = _run_git(["pull", "--no-rebase", remote, branch], vault_dir, timeout=60)
    if r.returncode == 0:
        return True, []
    # Check for conflicts
    conflicts = _resolve_conflicts(vault_dir)
    if conflicts:
        return False, conflicts
    # All conflicts auto-resolved, finalize merge
    _run_git(["commit", "--no-edit"], vault_dir)
    return True, []


def git_stage_vault(vault_dir: Path) -> int:
    """Stage all changes under obsitocin/ subdirectory. Returns staged file count."""
    if OBS_DIR is None:
        return 0
    obs_rel = "obsitocin/"
    _run_git(["add", obs_rel], vault_dir)
    r = _run_git(["diff", "--cached", "--name-only"], vault_dir)
    if r.returncode != 0:
        return 0
    files = [f for f in r.stdout.strip().splitlines() if f]
    return len(files)


def git_commit(vault_dir: Path, file_count: int, hostname: str) -> str:
    """Create commit. Returns SHA or empty string if nothing to commit."""
    msg = f"obsitocin: {file_count} topics updated from {hostname}"
    r = _run_git(["commit", "-m", msg], vault_dir)
    if r.returncode != 0:
        return ""
    # Get commit SHA
    sha_r = _run_git(["rev-parse", "HEAD"], vault_dir)
    return sha_r.stdout.strip() if sha_r.returncode == 0 else ""


def git_push(vault_dir: Path, remote: str, branch: str) -> bool:
    """git push. Never force-pushes. Returns success."""
    r = _run_git(["push", remote, branch], vault_dir, timeout=60)
    return r.returncode == 0


# ── Main entry point ──

def sync(
    *,
    local_only: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Full sync workflow: pull → stage → commit → push."""
    if VAULT_DIR is None:
        return SyncResult(status=SyncStatus.ERROR, message="VAULT_DIR not configured")

    vault_dir = Path(VAULT_DIR) if not isinstance(VAULT_DIR, Path) else VAULT_DIR

    if not is_git_repo(vault_dir):
        return SyncResult(status=SyncStatus.NO_GIT)

    hostname = get_hostname()

    if not local_only:
        if not has_remote(vault_dir):
            return SyncResult(status=SyncStatus.NO_REMOTE)
        remote = get_remote_name(vault_dir)
        branch = get_current_branch(vault_dir)
        success, conflicts = git_pull(vault_dir, remote, branch)
        if not success and conflicts:
            return SyncResult(
                status=SyncStatus.CONFLICT,
                hostname=hostname,
                conflicts=conflicts,
            )

    # Stage vault changes
    if dry_run:
        r = _run_git(["status", "--porcelain", "obsitocin/"], vault_dir)
        changed = len([l for l in r.stdout.strip().splitlines() if l]) if r.returncode == 0 else 0
        return SyncResult(
            status=SyncStatus.SUCCESS if changed else SyncStatus.NOTHING_TO_SYNC,
            files_committed=changed,
            hostname=hostname,
            message=f"Dry run: {changed} file(s) would be committed",
        )

    staged = git_stage_vault(vault_dir)
    if staged == 0:
        return SyncResult(status=SyncStatus.NOTHING_TO_SYNC, hostname=hostname)

    commit_sha = git_commit(vault_dir, staged, hostname)

    if not local_only:
        remote = get_remote_name(vault_dir)
        branch = get_current_branch(vault_dir)
        git_push(vault_dir, remote, branch)

    return SyncResult(
        status=SyncStatus.SUCCESS,
        files_committed=staged,
        hostname=hostname,
        commit_sha=commit_sha,
    )
