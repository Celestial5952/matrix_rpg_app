"""Game state. Pure data — no Matrix imports, no I/O, no printing.

Everything the adapter needs to persist lives here. The only field that is not
trivially serialisable is Run.rng; store `rng.getstate()` alongside the row and
restore it with `rng.setstate()` so a resumed run rolls the same numbers it
would have.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

# Player baseline. A meta-progression system would scale these off Player.rank;
# for now every run starts from the same kit so combat balance is readable.
BASE_MAX_HP = 34
BASE_POWER = 8
BASE_MAX_FOCUS = 6
FOCUS_REGEN = 1
POTION_CHARGES = 2
POTION_HEAL = 14


@dataclass(frozen=True)
class MonsterMove:
    """One telegraphed monster action.

    kind:
        attack  - ordinary hit
        heavy   - big hit, worth guarding through
        guard   - monster braces, takes reduced damage next turn
        drain   - hits and heals the monster for half the damage dealt
    """

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
class Quest:
    key: str
    name: str
    tier: int
    flavor: str
    pool: tuple[str, ...]  # monster keys this quest can roll
    stages: int
    gold: int
    renown: int


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
    """One attempt at a quest. Destroyed on death — this is the roguelite half."""

    quest: Quest
    hp: int
    max_hp: int
    focus: int
    max_focus: int
    potions: int
    stage: int = 0
    encounter: Encounter | None = None
    guard_active: bool = False
    gold_earned: int = 0
    rng: random.Random = field(default_factory=random.Random, repr=False)

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def stages_left(self) -> int:
        return self.quest.stages - self.stage


@dataclass
class Player:
    """Meta state. Survives death — this is what brings people back."""

    mxid: str
    name: str
    renown: int = 0
    gold: int = 0
    runs_completed: int = 0
    deaths: int = 0
    board: list[Quest] = field(default_factory=list)
    run: Run | None = None

    @property
    def rank(self) -> int:
        """Guild rank gates which quest tiers appear on the board."""
        if self.renown >= 40:
            return 3
        if self.renown >= 12:
            return 2
        return 1

    @property
    def in_combat(self) -> bool:
        return self.run is not None and self.run.encounter is not None
