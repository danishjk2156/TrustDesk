"""Per-deal workspaces: one customer's files are never reachable from another customer's deal.

    work/<deal_id>/answers.json, approvals.jsonl, gate.json, archive/

`deal_dir` validates the id (lowercase letters, digits, - or _), refuses anything that resolves outside
the work root (including through a symlink), and `resolve_in` does the same for a file inside a deal.
The work root is `work/` in the repo, or $TRUSTDESK_WORK.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEAL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")
WORK_ENV = "TRUSTDESK_WORK"


def work_root() -> Path:
    return Path(os.environ.get(WORK_ENV) or ROOT / "work").resolve()


def _inside(child: Path, parent: Path) -> bool:
    return parent == child or parent in child.parents


def deal_dir(deal_id: str, *, create: bool = False) -> Path:
    if not isinstance(deal_id, str) or not DEAL_ID.match(deal_id):
        raise ValueError(f"bad deal id {deal_id!r}: use lowercase letters, digits, - or _")
    root = work_root()
    path = (root / deal_id).resolve()
    if path.parent != root:
        raise ValueError(f"deal {deal_id!r} resolves outside the work directory")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_in(deal_id: str, relative: str) -> Path:
    """A file path inside one deal's folder. Absolute paths, `..` and symlinks out of the folder are refused."""
    base = deal_dir(deal_id)
    path = (base / relative).resolve()
    if not _inside(path, base) or path == base:
        raise ValueError(f"{relative!r} is outside the folder of deal {deal_id!r}")
    return path


def deal_of(path: str | Path) -> str | None:
    """The deal a path under the work root belongs to, or None if it is not under the work root."""
    resolved = Path(path).resolve()
    root = work_root()
    return resolved.relative_to(root).parts[0] if _inside(resolved, root) and resolved != root else None
