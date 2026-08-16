"""Meta-state persistence. Pure I/O over pure data — no Matrix imports.

What survives a restart: the Player, their Character, and the graveyard. What
does not: an in-progress run. Losing a contract to a restart lands the player
where `flee` would have — annoying, not destructive — and keeping a live
encounter on disk would mean persisting RNG state to stay honest. See README
"Known gaps".

Never raises. A crash here would take the bot down on every restart, which is
strictly worse than starting fresh.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .chargen import CLASSES_BY_KEY, RACES_BY_KEY
from .state import Character, Player, Tombstone

log = logging.getLogger("guildhall.persist")

# Persisted fields with the default used when a row predates the field. Adding
# one here is a forwards-compatible migration: old rows load with the default
# rather than raising, so a schema change never bricks an existing players.json.
_CHARACTER_FIELDS: dict[str, object] = {
    "name": "",
    "race_key": "",
    "class_key": "",
    "renown": 0,
    "gold": 0,
    "runs_completed": 0,
    "created_at": 0.0,
}

_TOMBSTONE_FIELDS: dict[str, object] = {
    "name": "",
    "race": "",
    "char_class": "",
    "renown": 0,
    "runs_completed": 0,
    "killed_by": "",
    "died_at": 0.0,
}


def _character_from(row: object, mxid: str) -> Character | None:
    if not isinstance(row, dict):
        return None
    kwargs = {k: row.get(k, d) for k, d in _CHARACTER_FIELDS.items()}
    # A character whose race or class was deleted from chargen.py cannot be
    # rendered or fought with. Dropping it loses that character; keeping it
    # would raise KeyError on the player's next message.
    if kwargs["race_key"] not in RACES_BY_KEY:
        log.warning("dropping %s: unknown race %r", mxid, kwargs["race_key"])
        return None
    if kwargs["class_key"] not in CLASSES_BY_KEY:
        log.warning("dropping %s: unknown class %r", mxid, kwargs["class_key"])
        return None
    try:
        return Character(**kwargs)
    except TypeError as exc:
        log.warning("skipping unloadable character for %s: %s", mxid, exc)
        return None


def _tombstones_from(rows: object) -> list[Tombstone]:
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            out.append(Tombstone(**{k: row.get(k, d)
                                    for k, d in _TOMBSTONE_FIELDS.items()}))
        except TypeError:
            continue
    return out


def load_all(path: Path) -> dict[str, Player]:
    """Load players. A bad file costs progress, not uptime."""
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
        players[mxid] = Player(
            mxid=mxid,  # the key is authoritative, not the stored field
            # "name" is the pre-character schema's spelling of display_name.
            display_name=row.get("display_name") or row.get("name") or mxid,
            character=_character_from(row.get("character"), mxid),
            graveyard=_tombstones_from(row.get("graveyard")),
        )
    return players


def save_all(path: Path, players: dict[str, Player]) -> None:
    """Write atomically — a crash mid-write must not truncate the live file."""
    raw = {}
    for mxid, p in players.items():
        char = p.character
        raw[mxid] = {
            "display_name": p.display_name,
            "character": None if char is None else {
                k: getattr(char, k) for k in _CHARACTER_FIELDS
            },
            "graveyard": [
                {k: getattr(t, k) for k in _TOMBSTONE_FIELDS} for t in p.graveyard
            ],
        }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2, sort_keys=True))
    tmp.replace(path)
