"""Timestamped snapshots of the state directory, with rotation.

A single `players.json` that gets rewritten on every command is one bad write,
one fat-fingered `rm`, or one admin mistake away from erasing everyone's
characters. This keeps the last few days' worth on disk beside it.

Never raises: a failed backup must not take the bot down, only say so in the log.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger("guildhall.backup")

BACKUP_DIR = "backups"
# What gets copied. The sync token is deliberately absent: restoring an old one
# makes the server replay history at us.
FILES = ("players.json", "guild.json")
_STAMP = "%Y%m%dT%H%M%SZ"


def _snapshots(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def _same(source_dir: Path, snapshot: Path) -> bool:
    """True if `snapshot` already holds exactly what is on disk now."""
    for name in FILES:
        live, saved = source_dir / name, snapshot / name
        if live.exists() != saved.exists():
            return False
        if live.exists() and live.read_bytes() != saved.read_bytes():
            return False
    return True


def snapshot(state_dir: Path, keep: int = 14,
             now: float | None = None) -> Path | None:
    """Copy the state files into backups/<UTC stamp>/ and prune old ones.

    Returns the new snapshot, or None when there was nothing to copy, nothing
    had changed since the last snapshot, or it failed.
    """
    if keep <= 0:
        return None
    try:
        present = [n for n in FILES if (state_dir / n).exists()]
        if not present:
            return None

        root = state_dir / BACKUP_DIR
        existing = _snapshots(root)
        if existing and _same(state_dir, existing[-1]):
            return None

        stamp = time.strftime(_STAMP, time.gmtime(time.time() if now is None else now))
        dest = root / stamp
        dest.mkdir(parents=True, exist_ok=True)
        for name in present:
            shutil.copy2(state_dir / name, dest / name)

        for old in _snapshots(root)[:-keep]:
            shutil.rmtree(old, ignore_errors=True)
        log.info("backed up %s to %s", ", ".join(present), dest)
        return dest
    except OSError:
        log.exception("backup of %s failed", state_dir)
        return None
