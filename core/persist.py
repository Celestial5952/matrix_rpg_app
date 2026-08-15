"""Meta-state persistence. Pure I/O over pure data — no Matrix imports.

Only Player meta-state survives a restart (renown, gold, counters). The quest
board regenerates lazily the next time a player reads it, and an in-progress
run being lost on restart is an acceptable rare loss, not a bug to route
around — see README "Known gaps".
"""

from __future__ import annotations

import json
from pathlib import Path

from .state import Player

_FIELDS = ("mxid", "name", "renown", "gold", "runs_completed", "deaths")


def load_all(path: Path) -> dict[str, Player]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {
        mxid: Player(**{k: row[k] for k in _FIELDS})
        for mxid, row in raw.items()
    }


def save_all(path: Path, players: dict[str, Player]) -> None:
    raw = {mxid: {k: getattr(p, k) for k in _FIELDS} for mxid, p in players.items()}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2, sort_keys=True))
    tmp.replace(path)
