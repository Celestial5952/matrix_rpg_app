"""Async contracts: time between encounters, and the bot speaking first."""

from __future__ import annotations

import time

import pytest

from core.game import arrive, handle, render_travelling, set_travel_pace

from .conftest import make_char


@pytest.fixture
def slow():
    """A pace fast enough to test, slow enough to observe."""
    set_travel_pace(60)
    yield
    set_travel_pace(0)


def on_the_road(player):
    """Fight until the first march begins.

    Keeps taking contracts until one is long enough to have a road in it: a
    single-encounter contract (the Urgent modifier shortens them) finishes
    before anyone travels anywhere.
    """
    for _ in range(40):
        if player.character.run is None:
            handle(player, "!board")
            handle(player, "!accept 1")
        for _ in range(200):
            run = player.character.run
            if run is None or run.travelling:
                break
            if run.pending_event:
                handle(player, "!1")
                continue
            run.hp = run.max_hp
            handle(player, "!1")
        run = player.character.run
        if run is not None and run.travelling:
            return run
    raise AssertionError("never reached a march")


# --- pacing ----------------------------------------------------------------

def test_the_default_pace_is_instant():
    """Every test and the offline REPL want play to happen at typing speed."""
    player = make_char()
    handle(player, "!board")
    handle(player, "!accept 1")
    for _ in range(60):
        run = player.character.run
        if run is None:
            break
        assert not run.travelling, "instant pace should never send them walking"
        if run.pending_event:
            handle(player, "!1")
            continue
        run.hp = run.max_hp
        handle(player, "!1")


def test_winning_an_encounter_starts_a_march(slow):
    player = make_char()
    run = on_the_road(player)
    assert run is not None and run.travelling
    assert run.encounter is None, "nothing to fight on the road"


def test_the_road_reports_how_far(slow):
    player = make_char()
    on_the_road(player)
    text = " ".join(render_travelling(player.character))
    assert "on the road" in text.lower()
    assert "m" in text, "should say how long is left"


# --- what you can do while walking ----------------------------------------

def test_fighting_is_refused_on_the_road(slow):
    player = make_char()
    run = on_the_road(player)
    before = run.hp
    reply = handle(player, "!1")
    assert "on the road" in " ".join(reply).lower()
    assert run.hp == before


def test_you_can_still_look_at_yourself(slow):
    player = make_char()
    on_the_road(player)
    for command in ("!status", "!bag", "!spellbook", "!help"):
        assert handle(player, command) is not None, command
    assert player.character.run.travelling


def test_you_can_come_home_early(slow):
    player = make_char()
    on_the_road(player)
    handle(player, "!portal")
    assert player.character.run is None


def test_the_sheet_says_where_you_are(slow):
    player = make_char()
    on_the_road(player)
    assert "on the road" in " ".join(handle(player, "!status")).lower()


# --- arriving --------------------------------------------------------------

def test_nothing_arrives_early(slow):
    player = make_char()
    on_the_road(player)
    assert arrive(player) is None, "the road should take the time it says"


def test_arrival_delivers_the_next_beat(slow):
    player = make_char()
    run = on_the_road(player)
    run.travel_until = time.time() - 1

    lines = arrive(player)
    assert lines is not None
    assert "arrives" in " ".join(lines).lower()
    assert run.pending_event or run.encounter is not None


def test_arriving_twice_does_nothing(slow):
    player = make_char()
    run = on_the_road(player)
    run.travel_until = time.time() - 1

    assert arrive(player) is not None
    assert arrive(player) is None, "arrival should fire once"


def test_arrival_is_silent_for_players_who_are_not_travelling():
    player = make_char()
    assert arrive(player) is None
    handle(player, "!board")
    handle(player, "!accept 1")
    assert arrive(player) is None


def test_arrival_is_silent_for_the_characterless():
    from core.state import Player

    assert arrive(Player(mxid="@a:srv", display_name="A")) is None


# --- persistence -----------------------------------------------------------

def test_a_march_survives_a_restart(slow, tmp_path):
    from core.persist import load_all, save_all

    player = make_char()
    run = on_the_road(player)
    path = tmp_path / "players.json"
    save_all(path, {player.mxid: player})

    restored = load_all(path)[player.mxid].character
    assert restored.run is not None
    assert restored.run.travelling, "you cannot skip the road by restarting"
    assert abs(restored.run.travel_until - run.travel_until) < 1


def test_a_march_that_finished_while_the_bot_was_down_arrives(slow, tmp_path):
    from core.persist import load_all, save_all

    player = make_char()
    run = on_the_road(player)
    run.travel_until = time.time() - 3600  # the bot was off for an hour

    path = tmp_path / "players.json"
    save_all(path, {player.mxid: player})
    restored = load_all(path)[player.mxid]

    assert arrive(restored) is not None, "downtime must not strand anyone"
