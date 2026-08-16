"""Game state. Pure data — no Matrix imports, no I/O, no printing.

Ownership model, which the permadeath rule depends on:

    Player     the Matrix account. Survives forever. Holds a graveyard.
    Character  what you create and what dies. Owns renown, gold, and the board.
    Run        one contract attempt. Destroyed on death *or* on character death.

Everything of value hangs off Character, so deleting it is the whole of
permadeath — there is no second place progress could hide.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

# Baseline before race/class modifiers.
BASE_MAX_HP = 34
BASE_POWER = 8
BASE_MAX_FOCUS = 6
FOCUS_REGEN = 1


def focus_regen_for(max_focus: int) -> int:
    """Regen scales with the pool.

    With a flat regen, a bigger pool only ever bought a slightly better opening
    burst, so focus was a near-worthless stat and every HP race dominated every
    focus race — including at Wizard. Tying regen to the pool is what makes
    "+3 focus" a real trade against "+6 HP".
    """
    return FOCUS_REGEN + max(0, (max_focus - BASE_MAX_FOCUS) // 3)

MIN_NAME = 2
MAX_NAME = 24


# ---------------------------------------------------------------------------
# character building blocks
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ability:
    """One entry in a class's pool.

    kind drives resolution ('attack', 'guard', 'heal'); slot decides which
    loadout position it can occupy ('basic', 'signature', 'defence',
    'recovery').
    """

    key: str
    name: str
    kind: str
    slot: str = "signature"
    unlock_level: int = 1
    cost: int = 0
    multiplier: float = 1.0
    ignores_armor: bool = False
    heal: int = 0
    focus_gain: int = 0
    guard_reduction: float = 0.4  # incoming damage multiplier while guarding
    uses: int | None = None       # None = unlimited; otherwise per-run charges
    blurb: str = ""


@dataclass(frozen=True)
class Item:
    """A consumable. kind is 'heal', 'focus', 'damage' or 'buff'."""

    key: str
    name: str
    kind: str
    price: int
    blurb: str = ""
    heal: int = 0
    focus: int = 0
    damage: int = 0
    ignores_armor: bool = False
    attack_bonus: float = 0.0


@dataclass(frozen=True)
class Race:
    """Flavour only.

    Race carries no stats on purpose. When it did, HP races strictly dominated
    focus races at every class, which made the choice a trap rather than a
    character. Class is where mechanical identity lives.
    """

    key: str
    name: str
    blurb: str


@dataclass(frozen=True)
class CharClass:
    key: str
    name: str
    hp_mod: int
    power_mod: int
    focus_mod: int
    blurb: str
    pool: tuple[Ability, ...]


# ---------------------------------------------------------------------------
# content
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MonsterMove:
    name: str
    telegraph: str
    kind: str = "attack"
    multiplier: float = 1.0


@dataclass(frozen=True)
class Monster:
    key: str
    name: str
    max_hp: int
    power: int
    armor: int
    moves: tuple[MonsterMove, ...]


@dataclass(frozen=True)
class Modifier:
    """A rolled complication. Changes the fight and what it pays."""

    key: str
    name: str
    blurb: str
    stages_delta: int = 0
    gold_mult: float = 1.0
    renown_mult: float = 1.0
    monster_hp_mult: float = 1.0
    monster_power_mult: float = 1.0
    monster_armor_delta: int = 0
    extra_loot: int = 0


@dataclass(frozen=True)
class Quest:
    """A template. The board never posts one of these directly."""

    key: str
    name: str
    tier: int
    flavor: str
    pool: tuple[str, ...]
    stages: int
    gold: int
    renown: int
    # Alternative openings, picked at roll time so a repeat contract still
    # reads differently.
    flavors: tuple[str, ...] = ()
    # Story contracts are gated on renown and appear by chance, not by rank.
    story: bool = False
    min_renown: int = 0


@dataclass(frozen=True)
class Contract:
    """One rolled instance of a Quest, as posted on the board.

    Carries the same attributes a Quest does, so everything downstream reads
    `contract.stages` / `.gold` / `.pool` without caring that the numbers were
    rolled rather than authored.
    """

    quest: Quest
    name: str
    tier: int
    flavor: str
    pool: tuple[str, ...]
    stages: int
    gold: int
    renown: int
    modifiers: tuple[Modifier, ...] = ()
    story: bool = False

    @property
    def key(self) -> str:
        return self.quest.key

    @property
    def monster_hp_mult(self) -> float:
        mult = 1.0
        for m in self.modifiers:
            mult *= m.monster_hp_mult
        return mult

    @property
    def monster_power_mult(self) -> float:
        mult = 1.0
        for m in self.modifiers:
            mult *= m.monster_power_mult
        return mult

    @property
    def monster_armor_delta(self) -> int:
        return sum(m.monster_armor_delta for m in self.modifiers)

    @property
    def extra_loot(self) -> int:
        return sum(m.extra_loot for m in self.modifiers)


# ---------------------------------------------------------------------------
# live state
# ---------------------------------------------------------------------------

@dataclass
class Encounter:
    monster: Monster
    hp: int
    next_move: MonsterMove
    guarding: bool = False

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class Run:
    """One attempt at a contract."""

    quest: Quest
    hp: int
    max_hp: int
    focus: int
    max_focus: int
    power: int
    focus_regen: int = FOCUS_REGEN
    uses: dict[str, int] = field(default_factory=dict)
    stage: int = 0
    encounter: Encounter | None = None
    # Set by a guard ability, consumed by the monster's turn.
    pending_guard: float | None = None
    # Set by a buff item, consumed by the next attack.
    next_attack_bonus: float = 0.0
    rng: random.Random = field(default_factory=random.Random, repr=False)

    @property
    def alive(self) -> bool:
        return self.hp > 0


@dataclass
class Tombstone:
    """A dead character. Flavour only — carries no mechanical benefit."""

    name: str
    race: str
    char_class: str
    renown: int
    runs_completed: int
    killed_by: str
    died_at: float = field(default_factory=time.time)


@dataclass
class Character:
    name: str
    race_key: str
    class_key: str
    renown: int = 0
    gold: int = 0
    runs_completed: int = 0
    # item key -> count. Dies with the character, like everything else.
    inventory: dict[str, int] = field(default_factory=dict)
    # slot -> ability key. Empty means "use the class defaults".
    loadout: dict[str, str] = field(default_factory=dict)
    board: list[Quest] = field(default_factory=list)
    run: Run | None = None
    created_at: float = field(default_factory=time.time)

    # Resolved lazily against the chargen tables so that editing a race's
    # numbers reshapes existing characters instead of stranding them.
    @property
    def race(self) -> Race:
        from .chargen import RACES_BY_KEY
        return RACES_BY_KEY[self.race_key]

    @property
    def char_class(self) -> CharClass:
        from .chargen import CLASSES_BY_KEY
        return CLASSES_BY_KEY[self.class_key]

    @property
    def level(self) -> int:
        from .chargen import level_for
        return level_for(self.renown)

    @property
    def max_hp(self) -> int:
        from .chargen import hp_bonus
        return max(1, BASE_MAX_HP + self.char_class.hp_mod + hp_bonus(self.level))

    @property
    def power(self) -> int:
        from .chargen import power_bonus
        return max(1, BASE_POWER + self.char_class.power_mod
                   + power_bonus(self.level))

    @property
    def max_focus(self) -> int:
        from .chargen import focus_bonus
        return max(0, BASE_MAX_FOCUS + self.char_class.focus_mod
                   + focus_bonus(self.level))

    @property
    def abilities(self) -> tuple[Ability, ...]:
        """The equipped kit, in slot order.

        Self-healing: an entry that is unknown, locked, or in the wrong slot
        falls back to the class default rather than raising. A save written
        before an ability was renamed must not brick the character.
        """
        from .chargen import SLOTS, default_loadout
        by_key = {a.key: a for a in self.char_class.pool}
        defaults = default_loadout(self.class_key)
        level = self.level

        equipped = []
        for slot in SLOTS:
            ability = by_key.get(self.loadout.get(slot, ""))
            if (ability is None or ability.slot != slot
                    or ability.unlock_level > level):
                ability = by_key[defaults[slot]]
            equipped.append(ability)
        return tuple(equipped)

    @property
    def rank(self) -> int:
        from .chargen import rank_for_level
        return rank_for_level(self.level)

    @property
    def in_combat(self) -> bool:
        return self.run is not None and self.run.encounter is not None

    @property
    def carried(self) -> int:
        return sum(self.inventory.values())

    @property
    def title(self) -> str:
        return f"{self.name} the {self.race.name} {self.char_class.name}"


@dataclass
class Pending:
    """A half-finished character. step is 'name', 'race' or 'class'."""

    step: str = "name"
    name: str = ""
    race_key: str = ""


@dataclass
class Player:
    """The Matrix account. Outlives every character it creates."""

    mxid: str
    display_name: str
    character: Character | None = None
    pending: Pending | None = None
    graveyard: list[Tombstone] = field(default_factory=list)

    @property
    def deaths(self) -> int:
        return len(self.graveyard)

    @property
    def in_combat(self) -> bool:
        return self.character is not None and self.character.in_combat
