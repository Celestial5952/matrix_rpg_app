"""Routing rules — the ones that decide whether the bot speaks at all.

The guild hall is a room people also chat in, so "stays silent" is a real
requirement and gets tested as hard as the commands do.
"""

from __future__ import annotations

import random

import pytest

from core.content import QUESTS
from core.game import handle, roll_board, start_run
from core.state import Player


@pytest.fixture
def player() -> Player:
    p = Player(mxid="@tester:local", name="Tester")
    roll_board(p, random.Random(1))
    return p


# --- silence ---------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "",
    "   ",
    "hello everyone",
    "what do you all think about the new patch",
    "lol",
    "!",
])
def test_non_commands_are_ignored(player: Player, text: str) -> None:
    assert handle(player, text) is None


@pytest.mark.parametrize("text", ["1", "2", "3", "4", "99"])
def test_bare_numbers_are_silent_outside_combat(player: Player, text: str) -> None:
    """The load-bearing rule: a number is only a command mid-fight."""
    assert not player.in_combat
    assert handle(player, text) is None


def test_bare_numbers_are_commands_during_combat(player: Player) -> None:
    start_run(player, QUESTS[0], seed=7)
    assert player.in_combat
    assert handle(player, "1") is not None


def test_combat_still_ignores_ordinary_chat(player: Player) -> None:
    start_run(player, QUESTS[0], seed=7)
    assert handle(player, "brb making tea") is None


def test_out_of_range_menu_number_is_silent(player: Player) -> None:
    """`9` mid-fight is chatter, not a mis-click — staying quiet is correct."""
    start_run(player, QUESTS[0], seed=7)
    assert handle(player, "9") is None


# --- commands --------------------------------------------------------------

@pytest.mark.parametrize("text", ["board", "!board", "BOARD", "  board  ", "quests"])
def test_board_variants(player: Player, text: str) -> None:
    reply = handle(player, text)
    assert reply is not None
    assert "Quest Board" in "\n".join(reply)


def test_bang_prefix_works_for_every_command(player: Player) -> None:
    assert handle(player, "!status") is not None
    assert handle(player, "!help") is not None
    assert handle(player, "!board") is not None


def test_accept_starts_a_run(player: Player) -> None:
    handle(player, "board")
    reply = handle(player, "accept 1")
    assert reply is not None
    assert player.run is not None
    assert player.in_combat


def test_accept_needs_an_index(player: Player) -> None:
    handle(player, "board")
    reply = handle(player, "accept")
    assert reply is not None
    assert "Which one" in reply[0]
    assert player.run is None


def test_accept_rejects_out_of_range(player: Player) -> None:
    handle(player, "board")
    reply = handle(player, "accept 99")
    assert reply is not None
    assert "no contract" in reply[0].lower()
    assert player.run is None


def test_action_aliases_match_menu_numbers(player: Player) -> None:
    """`strike` and `1` must resolve to the same action."""
    a = Player(mxid="@a:local", name="A")
    b = Player(mxid="@b:local", name="B")
    start_run(a, QUESTS[0], seed=42)
    start_run(b, QUESTS[0], seed=42)

    handle(a, "1")
    handle(b, "strike")
    assert a.run.encounter.hp == b.run.encounter.hp


def test_flee_abandons_the_run(player: Player) -> None:
    start_run(player, QUESTS[0], seed=7)
    reply = handle(player, "flee")
    assert reply is not None
    assert player.run is None
    assert player.runs_completed == 0


def test_flee_is_silent_outside_combat(player: Player) -> None:
    assert handle(player, "flee") is None


# --- progression -----------------------------------------------------------

def test_meta_state_survives_death(player: Player) -> None:
    player.renown = 20
    player.gold = 100
    start_run(player, QUESTS[0], seed=7)
    player.run.hp = 1

    for _ in range(60):
        if not player.in_combat:
            break
        handle(player, "1")

    assert player.run is None, "run should have ended"
    if player.deaths:
        assert player.renown == 20, "renown is meta state and must survive"
        assert player.gold == 100, "gold is meta state and must survive"


def test_rank_gates_board_contents() -> None:
    low = Player(mxid="@low:local", name="Low")
    high = Player(mxid="@high:local", name="High", renown=100)
    assert low.rank == 1
    assert high.rank == 3

    roll_board(low, random.Random(3))
    roll_board(high, random.Random(3))
    assert all(q.tier <= 1 for q in low.board)
    assert any(q.tier > 1 for q in high.board) or len(high.board) < 3


def test_completing_a_contract_pays_out(player: Player) -> None:
    quest = QUESTS[0]
    start_run(player, quest, seed=7)
    player.run.hp = 999
    player.run.max_hp = 999

    for _ in range(400):
        if not player.in_combat:
            break
        player.run.hp = 999  # immortal: we are testing payout, not balance
        handle(player, "1")

    assert player.run is None
    assert player.runs_completed == 1
    assert player.gold == quest.gold
    assert player.renown == quest.renown
