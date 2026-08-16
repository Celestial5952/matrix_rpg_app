"""Combat resolution.

Every function takes state and returns a list of narration lines. Nothing
formats for Matrix, nothing prints. Abilities come from the character's class,
so adding a class needs no changes here.
"""

from __future__ import annotations

import random

from .content import MONSTERS
from .state import Ability, Character, Encounter, Monster

MONSTER_GUARD_REDUCTION = 0.5


def roll(base: float, rng: random.Random, variance: float = 0.18) -> int:
    lo, hi = base * (1 - variance), base * (1 + variance)
    return max(1, round(rng.uniform(lo, hi)))


def hp_bar(current: int, maximum: int, width: int = 10) -> str:
    filled = max(0, min(width, round(width * current / maximum))) if maximum else 0
    return "█" * filled + "░" * (width - filled)


def spawn(monster_key: str, rng: random.Random) -> Encounter:
    monster: Monster = MONSTERS[monster_key]
    return Encounter(monster=monster, hp=monster.max_hp,
                     next_move=rng.choice(monster.moves))


def available_actions(char: Character) -> list[tuple[Ability, bool, str]]:
    """(ability, usable, why-not) in menu order — always the same four slots."""
    run = char.run
    assert run is not None
    out = []
    for ab in char.abilities:
        usable, why = ability_is_legal(char, ab)
        out.append((ab, usable, why))
    return out


def ability_is_legal(char: Character, ab: Ability) -> tuple[bool, str]:
    run = char.run
    assert run is not None
    if ab.cost and run.focus < ab.cost:
        return False, f"{ab.name} needs {ab.cost} focus — you have {run.focus}."
    if ab.uses is not None and run.uses.get(ab.key, 0) <= 0:
        return False, f"No {ab.name} left this contract."
    return True, ""


def player_turn(char: Character, ab: Ability) -> list[str]:
    """Resolve the chosen ability. Assumes it has already been validated."""
    run = char.run
    assert run is not None and run.encounter is not None
    enc = run.encounter
    lines: list[str] = []

    if ab.cost:
        run.focus -= ab.cost
    if ab.uses is not None:
        run.uses[ab.key] = run.uses.get(ab.key, 0) - 1

    if ab.kind == "attack":
        monster_guard = MONSTER_GUARD_REDUCTION if enc.guarding else 1.0
        raw = roll(run.power * ab.multiplier, run.rng)
        dealt = raw if ab.ignores_armor else max(1, raw - enc.monster.armor)
        dealt = max(1, round(dealt * monster_guard))
        enc.hp -= dealt
        lines.append(f"**{ab.name}** — you hit the {enc.monster.name} for **{dealt}**.")
        if ab.ignores_armor and enc.monster.armor:
            lines.append(f"_{enc.monster.armor} armour ignored._")
        elif enc.monster.armor:
            lines.append(f"_Armour absorbs {min(raw - 1, enc.monster.armor)}._")
        if enc.guarding:
            lines.append(f"The {enc.monster.name}'s guard blunts the blow.")

    elif ab.kind == "guard":
        run.pending_guard = ab.guard_reduction
        if ab.focus_gain:
            run.focus = min(run.max_focus, run.focus + ab.focus_gain)
        lines.append(f"**{ab.name}** — {ab.blurb.lower().rstrip('.')}."
                     + (f" _(+{ab.focus_gain} focus)_" if ab.focus_gain else ""))

    elif ab.kind == "heal":
        healed = min(ab.heal, run.max_hp - run.hp)
        run.hp += healed
        left = run.uses.get(ab.key, 0)
        lines.append(f"**{ab.name}** — you recover **{healed}** HP. _({left} left)_")

    enc.guarding = False
    return lines


def monster_turn(char: Character) -> list[str]:
    """Execute the telegraphed move, then roll the next one."""
    run = char.run
    assert run is not None and run.encounter is not None
    enc = run.encounter
    lines: list[str] = []
    move = enc.next_move
    guard = run.pending_guard

    if move.kind == "guard":
        enc.guarding = True
        lines.append(f"The {enc.monster.name} braces itself.")
    else:
        raw = roll(enc.monster.power * move.multiplier, run.rng)
        dealt = max(1, round(raw * (guard if guard is not None else 1.0)))
        run.hp -= dealt
        verb = "hits you hard" if move.kind == "heavy" else "hits you"
        lines.append(f"**{move.name}** — the {enc.monster.name} {verb} for **{dealt}**.")
        if guard is not None:
            lines.append(f"_Your guard absorbs {raw - dealt}._")
        if move.kind == "drain":
            healed = min(dealt // 2, enc.monster.max_hp - enc.hp)
            if healed > 0:
                enc.hp += healed
                lines.append(f"The {enc.monster.name} draws {healed} HP from the wound.")

    run.pending_guard = None
    run.focus = min(run.max_focus, run.focus + run.focus_regen)
    enc.next_move = run.rng.choice(enc.monster.moves)
    return lines
