"""Meta-state persistence. Pure I/O over pure data — no Matrix imports.

Only Player meta-state survives a restart (renown, gold, counters). The quest
board regenerates lazily the next time a player reads it, and an in-progress
run being lost on restart is an acceptable rare loss, not a bug to route
around — see README "Known gaps".
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .state import Player

log = logging.getLogger("guildhall.persist")

# Persisted fields, with the default used when a row predates the field. Adding
# a field here is a forwards-compatible migration: old rows load with the
# default rather than raising, so a schema change never bricks an existing
# players.json.
_FIELDS: dict[str, object] = {
    "mxid": "",
    "name": "",
    "renown": 0,
    "gold": 0,
    "runs_completed": 0,
    "deaths": 0,
}


def load_all(path: Path) -> dict[str, Player]:
    """Load players. Never raises — a bad file costs progress, not uptime.

    A crash here would take the bot down on every restart, which is strictly
    worse than starting fresh: the corrupt file is moved aside so it can still
    be recovered by hand.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError(f"expected a JSON object, got {type(raw).__name__}")
    except (OSError, ValueError) as exc:
        salvage = path.with_suffix(path.suffix + ".corrupt")
        log.error("could not read %s (%s) — moving it to %s", path, exc, salvage)
        try:
            path.replace(salvage)
        except OSError:
            pass
        return {}

    players: dict[str, Player] = {}
    for mxid, row in raw.items():
        if not isinstance(row, dict):
            log.warning("skipping malformed row for %s", mxid)
            continue
        kwargs = {k: row.get(k, default) for k, default in _FIELDS.items()}
        kwargs["mxid"] = mxid  # the key is authoritative, not the stored field
        try:
            players[mxid] = Player(**kwargs)
        except TypeError as exc:
            log.warning("skipping unloadable row for %s: %s", mxid, exc)
    return players


def save_all(path: Path, players: dict[str, Player]) -> None:
    """Write atomically — a crash mid-write must not truncate the live file."""
    raw = {mxid: {k: getattr(p, k) for k in _FIELDS} for mxid, p in players.items()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2, sort_keys=True))
    tmp.replace(path)
