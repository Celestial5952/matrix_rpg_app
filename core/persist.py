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

from .adventures import ADVENTURES, contract_for
from .chargen import CLASSES_BY_KEY, RACES_BY_KEY, SLOTS
from .content import MODIFIERS_BY_KEY, MONSTERS, QUESTS_BY_KEY, scaled_monster
from .items import ITEMS
import random

from .guild import Guild
from .state import Character, Contract, Encounter, Player, Run, Tombstone

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
    "portals_used": 0,
    "created_at": 0.0,
    "inventory": None,  # None -> {}; see _inventory_from
    "loadout": None,    # None -> {}; see _loadout_from
}

# Run fields stored verbatim. The encounter, contract and RNG need rebuilding.
_RUN_FIELDS: dict[str, object] = {
    "hp": 0, "max_hp": 1, "focus": 0, "max_focus": 0, "power": 1,
    "focus_regen": 1, "stage": 0, "pending_guard": None,
    "next_attack_bonus": 0.0,
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
    kwargs["inventory"] = _inventory_from(kwargs["inventory"])
    kwargs["loadout"] = _loadout_from(kwargs["loadout"], kwargs["class_key"])
    run = _run_from_dict(row.get("run"))
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
        character = Character(**kwargs)
    except TypeError as exc:
        log.warning("skipping unloadable character for %s: %s", mxid, exc)
        return None
    character.run = run
    return character


def _inventory_from(raw: object) -> dict[str, int]:
    """Drop anything that is not a currently-known item with a positive count.

    An item deleted from items.py would otherwise raise on the player's next
    `!bag`, taking the bot down for everyone in the room.
    """
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, count in raw.items():
        if key in ITEMS and isinstance(count, int) and count > 0:
            out[key] = count
    return out


def _loadout_from(raw: object, class_key: str) -> dict[str, str]:
    """Keep only entries naming a real ability of this class in that slot.

    Character.abilities also falls back per-slot, so a bad entry could never
    crash — but sanitising here means a renamed ability is dropped once on
    load rather than silently ignored on every render.
    """
    if not isinstance(raw, dict):
        return {}
    cls = CLASSES_BY_KEY.get(class_key)
    if cls is None:
        return {}
    by_key = {a.key: a for a in cls.pool}
    out: dict[str, str] = {}
    for slot, key in raw.items():
        if slot not in SLOTS or not isinstance(key, str):
            continue
        ability = by_key.get(key)
        if ability is not None and ability.slot == slot:
            out[slot] = key
    return out


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


def _contract_to_dict(c: Contract) -> dict:
    return {
        "quest": c.quest.key,
        "adventure": c.adventure_key,
        "name": c.name, "tier": c.tier, "flavor": c.flavor,
        "pool": list(c.pool), "stages": c.stages,
        "gold": c.gold, "renown": c.renown, "story": c.story,
        "modifiers": [m.key for m in c.modifiers],
    }


def _contract_from_dict(d: object) -> Contract | None:
    if not isinstance(d, dict):
        return None

    # Adventures are rebuilt wholesale from the table rather than from the
    # saved row: the chapters are authored content, and reconstructing prose
    # from a save would let an edited adventure resume with stale text.
    adventure_key = d.get("adventure")
    if adventure_key:
        adventure = ADVENTURES.get(adventure_key)
        if adventure is None:
            return None  # adventure removed from the game
        return contract_for(adventure)

    quest = QUESTS_BY_KEY.get(d.get("quest"))
    if quest is None:
        return None  # template deleted from content.py
    modifiers = tuple(MODIFIERS_BY_KEY[k] for k in d.get("modifiers", [])
                      if k in MODIFIERS_BY_KEY)
    pool = tuple(k for k in d.get("pool", quest.pool) if k in MONSTERS)
    if not pool:
        return None
    return Contract(
        quest=quest,
        name=d.get("name", quest.name),
        tier=d.get("tier", quest.tier),
        flavor=d.get("flavor", quest.flavor),
        pool=pool,
        stages=max(1, int(d.get("stages", quest.stages))),
        gold=int(d.get("gold", quest.gold)),
        renown=int(d.get("renown", quest.renown)),
        modifiers=modifiers,
        story=bool(d.get("story", quest.story)),
    )


def _run_to_dict(run: Run) -> dict:
    state = run.rng.getstate()
    out = {k: getattr(run, k) for k in _RUN_FIELDS}
    out["party_key"] = run.party_key
    out["quest"] = _contract_to_dict(run.quest)
    out["uses"] = dict(run.uses)
    # getstate() is (version, tuple[int, ...], float|None); JSON flattens the
    # inner tuple, so _run_from_dict has to put it back or the run replays
    # different numbers than it would have.
    out["rng"] = [state[0], list(state[1]), state[2]]
    enc = run.encounter
    out["encounter"] = None if enc is None else {
        "monster": enc.monster.key,
        "hp": enc.hp,
        "next_move": enc.monster.moves.index(enc.next_move),
        "guarding": enc.guarding,
    }
    return out


def _run_from_dict(d: object) -> Run | None:
    # A party run is shared state; restoring it per-member would silently turn
    # one party fight into several identical solo ones. Dropped on purpose.
    if isinstance(d, dict) and d.get("party_key"):
        return None

    """Rebuild a live run. Returns None if anything no longer lines up —
    losing the contract is the documented fallback, and it is far better than
    resuming a fight against a monster whose moves have changed underneath."""
    if not isinstance(d, dict):
        return None
    contract = _contract_from_dict(d.get("quest"))
    if contract is None:
        return None

    try:
        rng = random.Random()
        version, state, gauss = d["rng"]
        rng.setstate((version, tuple(state), gauss))
    except (KeyError, ValueError, TypeError):
        return None

    encounter = None
    raw = d.get("encounter")
    if isinstance(raw, dict):
        base = MONSTERS.get(raw.get("monster"))
        if base is None:
            return None
        monster = scaled_monster(base, contract)
        idx = raw.get("next_move", 0)
        if not isinstance(idx, int) or not 0 <= idx < len(monster.moves):
            return None
        encounter = Encounter(
            monster=monster,
            hp=int(raw.get("hp", monster.max_hp)),
            next_move=monster.moves[idx],
            guarding=bool(raw.get("guarding", False)),
        )

    kwargs = {k: d.get(k, default) for k, default in _RUN_FIELDS.items()}
    uses = d.get("uses")
    try:
        return Run(quest=contract, encounter=encounter, rng=rng,
                   uses={k: v for k, v in uses.items()
                         if isinstance(v, int)} if isinstance(uses, dict) else {},
                   **kwargs)
    except (TypeError, ValueError):
        return None


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
                **{k: getattr(char, k) for k in _CHARACTER_FIELDS},
                "run": None if char.run is None else _run_to_dict(char.run),
            },
            "graveyard": [
                {k: getattr(t, k) for k in _TOMBSTONE_FIELDS} for t in p.graveyard
            ],
        }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(raw, indent=2, sort_keys=True))
    tmp.replace(path)


# --- the guild -------------------------------------------------------------
# Stored separately from players.json: it belongs to no one, and keeping it
# out of the per-player file means a corrupt save cannot cost the whole
# server's shared progress.

_GUILD_FIELDS: dict[str, object] = {
    "renown": 0,
    "contracts_completed": 0,
    "adventures_completed": 0,
    "members": 0,
}


def load_guild(path: Path) -> Guild:
    """Never raises. A missing or broken guild file starts a new charter."""
    if not path.exists():
        return Guild()
    try:
        raw = json.loads(path.read_text())
        if not isinstance(raw, dict):
            raise ValueError(f"expected an object, got {type(raw).__name__}")
    except (OSError, ValueError) as exc:
        salvage = path.with_suffix(path.suffix + ".corrupt")
        log.error("could not read %s (%s) — moving it to %s", path, exc, salvage)
        try:
            path.replace(salvage)
        except OSError:
            pass
        return Guild()

    values = {}
    for key, default in _GUILD_FIELDS.items():
        value = raw.get(key, default)
        values[key] = value if isinstance(value, int) and value >= 0 else default
    return Guild(**values)


def save_guild(path: Path, guild: Guild) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({k: getattr(guild, k) for k in _GUILD_FIELDS},
                              indent=2, sort_keys=True))
    tmp.replace(path)
