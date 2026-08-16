"""Duels: two characters, no monster, nowhere to go.

Consensual, binding, and non-lethal.

- **Consensual**: a duel only starts when the challenged player accepts.
- **Binding**: once accepted there is no withdrawing. `!portal` does not work,
  the party cannot be left, and no contract can be taken until it is settled.
- **Non-lethal**: the loser is beaten, not killed. Pairing "no escape" with
  permadeath would mean one accepted challenge could end a character somebody
  spent hours on, which makes duelling a griefing vector and something nobody
  does twice. The stakes are the wager and the record instead.

Duellists fight from a fresh copy of their kit — full health, full focus, full
ability charges — so a duel never spends the consumables a contract needs, and
winning is not a question of who shopped more recently. Inventory is untouched
for the same reason: this is a test of the character, not the shopping.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from .combat import MONSTER_GUARD_REDUCTION, roll
from .state import Ability, Character

_ids = itertools.count(1)

WIN_RENOWN = 2
# How long the loser spends pulling pints. Long enough to sting, short enough
# that an evening is not written off.
BAR_DUTY_HOURS = 8


@dataclass
class Duelist:
    """A combatant's state for the duration of a duel."""

    mxid: str
    name: str
    hp: int
    max_hp: int
    focus: int
    max_focus: int
    power: int
    focus_regen: int
    uses: dict[str, int] = field(default_factory=dict)
    pending_guard: float | None = None
    next_attack_bonus: float = 0.0

    @property
    def standing(self) -> bool:
        return self.hp > 0


def duelist_from(character: Character, mxid: str) -> Duelist:
    from .state import focus_regen_for

    return Duelist(
        mxid=mxid,
        name=character.name,
        hp=character.max_hp,
        max_hp=character.max_hp,
        focus=character.max_focus,
        max_focus=character.max_focus,
        power=character.power,
        focus_regen=focus_regen_for(character.max_focus),
        uses={a.key: a.uses for a in character.abilities if a.uses is not None},
    )


@dataclass
class Duel:
    key: str
    challenger: str          # mxid
    opponent: str            # mxid
    wager: int = 0
    accepted: bool = False
    left: Duelist | None = None
    right: Duelist | None = None
    turn: str = ""
    rng: random.Random = field(default_factory=random.Random, repr=False)

    def duelist(self, mxid: str) -> Duelist | None:
        if self.left and self.left.mxid == mxid:
            return self.left
        if self.right and self.right.mxid == mxid:
            return self.right
        return None

    def other(self, mxid: str) -> Duelist | None:
        if self.left and self.left.mxid == mxid:
            return self.right
        if self.right and self.right.mxid == mxid:
            return self.left
        return None

    @property
    def combatants(self) -> tuple[Duelist, Duelist]:
        assert self.left and self.right
        return self.left, self.right

    def involves(self, mxid: str) -> bool:
        return mxid in (self.challenger, self.opponent)


@dataclass
class Duels:
    by_key: dict[str, Duel] = field(default_factory=dict)

    def for_player(self, mxid: str) -> Duel | None:
        """The player's live duel, if any."""
        for duel in self.by_key.values():
            if duel.accepted and duel.involves(mxid):
                return duel
        return None

    def pending_for(self, mxid: str) -> list[Duel]:
        """Challenges waiting on this player's answer."""
        return [d for d in self.by_key.values()
                if not d.accepted and d.opponent == mxid]

    def issued_by(self, mxid: str) -> list[Duel]:
        return [d for d in self.by_key.values()
                if not d.accepted and d.challenger == mxid]

    def challenge(self, challenger: str, opponent: str, wager: int = 0) -> Duel:
        duel = Duel(key=f"duel-{next(_ids)}", challenger=challenger,
                    opponent=opponent, wager=wager, rng=random.Random())
        self.by_key[duel.key] = duel
        return duel

    def begin(self, duel: Duel, challenger: Character,
              opponent: Character) -> None:
        duel.accepted = True
        duel.left = duelist_from(challenger, duel.challenger)
        duel.right = duelist_from(opponent, duel.opponent)
        # Coin toss for the opening move — going first is a real advantage and
        # should not be a reward for issuing the challenge.
        duel.turn = duel.rng.choice([duel.challenger, duel.opponent])

    def end(self, duel: Duel) -> None:
        self.by_key.pop(duel.key, None)


def resolve(attacker: Duelist, defender: Duelist,
            ability: Ability, rng: random.Random) -> list[str]:
    """One duellist's move against the other. Mirrors combat.player_turn."""
    lines: list[str] = []

    if ability.cost:
        attacker.focus -= ability.cost
    if ability.uses is not None:
        attacker.uses[ability.key] = attacker.uses.get(ability.key, 0) - 1

    if ability.kind == "attack":
        bonus = 1.0 + attacker.next_attack_bonus
        raw = roll(attacker.power * ability.multiplier * bonus, rng)
        guard = defender.pending_guard
        dealt = max(1, round(raw * (guard if guard is not None else 1.0)))
        defender.hp -= dealt
        lines.append(f"**{ability.name}** — {attacker.name} hits "
                     f"{defender.name} for **{dealt}**.")
        if attacker.next_attack_bonus:
            lines.append("_The honed edge bites deeper._")
            attacker.next_attack_bonus = 0.0
        if guard is not None:
            lines.append(f"_{defender.name}'s guard absorbs {raw - dealt}._")
        defender.pending_guard = None

    elif ability.kind == "guard":
        attacker.pending_guard = ability.guard_reduction
        if ability.focus_gain:
            attacker.focus = min(attacker.max_focus,
                                 attacker.focus + ability.focus_gain)
        lines.append(f"**{ability.name}** — {attacker.name} braces."
                     + (f" _(+{ability.focus_gain} focus)_"
                        if ability.focus_gain else ""))

    elif ability.kind == "heal":
        healed = min(ability.heal, attacker.max_hp - attacker.hp)
        attacker.hp += healed
        left = attacker.uses.get(ability.key, 0)
        lines.append(f"**{ability.name}** — {attacker.name} recovers "
                     f"**{healed}** HP. _({left} left)_")

    attacker.focus = min(attacker.max_focus,
                         attacker.focus + attacker.focus_regen)
    return lines


def is_legal(duelist: Duelist, ability: Ability) -> tuple[bool, str]:
    if ability.cost and duelist.focus < ability.cost:
        return False, (f"{ability.name} needs {ability.cost} focus — "
                       f"you have {duelist.focus}.")
    if ability.uses is not None and duelist.uses.get(ability.key, 0) <= 0:
        return False, f"No {ability.name} left this duel."
    return True, ""
