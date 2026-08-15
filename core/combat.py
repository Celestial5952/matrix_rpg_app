"""Combat resolution.

Every function here takes state and returns a list of narration lines. Nothing
formats for Matrix, nothing prints. The adapter decides how lines become
messages (one per line, or joined into a single edited message).
"""

from __future__ import annotations

import random

from .content import MONSTERS
from .state import BASE_POWER, FOCUS_REGEN, POTION_HEAL, Encounter, Monster, Run

FIREBALL_COST = 3
FIREBALL_MULTIPLIER = 1.8
GUARD_FOCUS_GAIN = 2
GUARD_REDUCTION = 0.4  # incoming damage multiplier while guarding
MONSTER_GUARD_REDUCTION = 0.5


def roll(base: float, rng: random.Random, variance: float = 0.18) -> int:
    """Damage roll with a little spread, never less than 1."""
    lo, hi = base * (1 - variance), base * (1 + variance)
    return max(1, round(rng.uniform(lo, hi)))


def hp_bar(current: int, maximum: int, width: int = 10) -> str:
    filled = max(0, min(width, round(width * current / maximum))) if maximum else 0
    return "█" * filled + "░" * (width - filled)


def spawn(monster_key: str, rng: random.Random) -> Encounter:
    monster: Monster = MONSTERS[monster_key]
    return Encounter(
        monster=monster,
        hp=monster.max_hp,
        next_move=rng.choice(monster.moves),
    )


def player_turn(run: Run, action: str) -> list[str]:
    """Resolve the player's chosen action. Assumes action is already validated."""
    enc = run.encounter
    assert enc is not None
    lines: list[str] = []
    guard_mult = MONSTER_GUARD_REDUCTION if enc.guarding else 1.0

    if action == "strike":
        raw = roll(BASE_POWER, run.rng)
        dealt = max(1, round((raw - enc.monster.armor) * guard_mult))
        enc.hp -= dealt
        lines.append(f"You strike the {enc.monster.name} for **{dealt}**.")
        if enc.monster.armor and guard_mult == 1.0:
            lines[-1] += f" _(armour absorbed {min(raw - 1, enc.monster.armor)})_"

    elif action == "fireball":
        run.focus -= FIREBALL_COST
        # Fireball ignores armour — that is its whole reason to exist against
        # the armoured monsters, not raw numbers.
        dealt = max(1, round(roll(BASE_POWER * FIREBALL_MULTIPLIER, run.rng) * guard_mult))
        enc.hp -= dealt
        lines.append(
            f"Fire leaps from your hand and washes over the {enc.monster.name} "
            f"for **{dealt}**. _(armour ignored)_"
        )

    elif action == "guard":
        run.guard_active = True
        run.focus = min(run.max_focus, run.focus + GUARD_FOCUS_GAIN)
        lines.append(
            f"You set your feet and raise your guard. _(+{GUARD_FOCUS_GAIN} focus)_"
        )

    elif action == "potion":
        run.potions -= 1
        healed = min(POTION_HEAL, run.max_hp - run.hp)
        run.hp += healed
        lines.append(
            f"You drain a flask. _(+{healed} HP, {run.potions} left)_"
        )

    if enc.guarding and action in ("strike", "fireball"):
        lines.append(f"The {enc.monster.name}'s guard blunts the blow.")
    enc.guarding = False
    return lines


def monster_turn(run: Run) -> list[str]:
    """Execute the move the monster telegraphed last turn, then roll the next."""
    enc = run.encounter
    assert enc is not None
    lines: list[str] = []
    move = enc.next_move
    incoming_mult = GUARD_REDUCTION if run.guard_active else 1.0

    if move.kind == "guard":
        enc.guarding = True
        lines.append(f"The {enc.monster.name} braces itself.")
    else:
        dealt = max(1, round(roll(enc.monster.power * move.multiplier, run.rng) * incoming_mult))
        run.hp -= dealt
        verb = "hits you hard" if move.kind == "heavy" else "hits you"
        lines.append(f"**{move.name}** — the {enc.monster.name} {verb} for **{dealt}**.")
        if run.guard_active:
            lines.append("Your guard takes most of it.")
        if move.kind == "drain":
            healed = min(dealt // 2, enc.monster.max_hp - enc.hp)
            if healed > 0:
                enc.hp += healed
                lines.append(f"The {enc.monster.name} draws {healed} HP from the wound.")

    run.guard_active = False
    run.focus = min(run.max_focus, run.focus + FOCUS_REGEN)
    enc.next_move = run.rng.choice(enc.monster.moves)
    return lines


def available_actions(run: Run) -> list[tuple[str, str]]:
    """(key, label) pairs the player can pick right now, in menu order."""
    actions = [("strike", "Strike")]
    if run.focus >= FIREBALL_COST:
        actions.append(("fireball", f"Fireball ({FIREBALL_COST} focus)"))
    else:
        actions.append(("fireball", f"Fireball — need {FIREBALL_COST} focus"))
    actions.append(("guard", f"Guard (+{GUARD_FOCUS_GAIN} focus)"))
    if run.potions > 0:
        actions.append(("potion", f"Potion (+{POTION_HEAL} HP, {run.potions} left)"))
    return actions


def action_is_legal(run: Run, action: str) -> tuple[bool, str]:
    if action == "fireball" and run.focus < FIREBALL_COST:
        return False, f"Not enough focus — you have {run.focus}, Fireball costs {FIREBALL_COST}."
    if action == "potion" and run.potions <= 0:
        return False, "You're out of potions."
    return True, ""
