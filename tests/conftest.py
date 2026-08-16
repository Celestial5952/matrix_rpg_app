"""Shared builders.

Characters are built by driving the real creation flow rather than constructing
a Character directly, so the tests break if creation stops producing a usable
character.
"""

from __future__ import annotations

import random

import pytest

from core import combat
from core.content import QUESTS, plain_contract
from core.game import handle, start_run
from core.state import Ability, Character, Player

QUESTS_BY_KEY = {q.key: q for q in QUESTS}
# start_run takes a posted Contract; tests want the exact template numbers.
CONTRACTS_BY_KEY = {q.key: plain_contract(q) for q in QUESTS}


def make_player(mxid: str = "@a:srv", display_name: str = "A") -> Player:
    return Player(mxid=mxid, display_name=display_name)


def make_char(
    race: str = "human",
    char_class: str = "fighter",
    name: str = "Tester",
    mxid: str = "@a:srv",
) -> Player:
    """A Player with a live character, built through the register."""
    player = make_player(mxid)
    handle(player, "!create")
    handle(player, f"!{name}")
    handle(player, f"!{race}")
    handle(player, f"!{char_class}")
    assert player.character is not None, "creation flow failed to make a character"
    return player


def fighting(
    char_class: str = "fighter",
    race: str = "human",
    monster: str = "kobold",
    quest: str = "cellar_rats",
    seed: int = 1,
) -> Character:
    """A character mid-encounter against a chosen monster."""
    char = Character(name="Tester", race_key=race, class_key=char_class)
    start_run(char, CONTRACTS_BY_KEY[quest], seed=seed)
    char.run.encounter = combat.spawn(monster, char.run.rng)
    return char


def ability(char: Character, key: str) -> Ability:
    for ab in char.abilities:
        if ab.key == key:
            return ab
    raise KeyError(f"{char.class_key} has no ability {key!r}")


def slot(char: Character, index: int) -> Ability:
    """Menu slot by 0-based index: 0 basic, 1 signature, 2 defence, 3 recovery."""
    return char.abilities[index]


@pytest.fixture
def rng() -> random.Random:
    return random.Random(1234)
