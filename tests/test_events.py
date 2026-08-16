"""Choice events — the parts of a contract that are not a fight."""

from __future__ import annotations

import random

from core.events import EVENTS, EVENT_CHANCE, resolve, roll_event
from core.game import handle
from core.items import ITEMS

from .conftest import make_char


def adventuring(gold: int = 100):
    player = make_char()
    player.character.gold = gold
    handle(player, "!board")
    handle(player, "!accept 1")
    return player


def force_event(player, key: str = "wayside_shrine"):
    """Put a specific decision in front of the player."""
    run = player.character.run
    run.pending_event = key
    run.encounter = None
    return run


# --- the content -----------------------------------------------------------

def test_every_event_is_well_formed():
    for event in EVENTS:
        assert event.prompt and event.choices, event.key
        assert event.tiers, event.key
        for choice in event.choices:
            assert choice.label, event.key
            assert choice.outcomes, f"{event.key}/{choice.label}"
            for outcome in choice.outcomes:
                assert outcome.text, event.key
                assert outcome.weight > 0, event.key


def test_every_event_offers_a_real_decision():
    """One choice is not a decision, it is a delay with prose attached."""
    for event in EVENTS:
        assert len(event.choices) >= 2, event.key


def test_no_event_grants_an_unknown_item():
    for event in EVENTS:
        for choice in event.choices:
            for outcome in choice.outcomes:
                for key in outcome.items:
                    assert key in ITEMS, f"{event.key} grants unknown {key}"


def test_risky_choices_can_go_either_way():
    """If a choice has one outcome it should be the safe one, not a gamble."""
    for event in EVENTS:
        gambles = [c for c in event.choices if len(c.outcomes) > 1]
        assert gambles, f"{event.key} has no choice that can go wrong"


def test_outcomes_are_weighted_not_uniform():
    rng = random.Random(3)
    choice = next(c for e in EVENTS for c in e.choices if len(c.outcomes) > 1)
    picked = {resolve(choice, rng).text for _ in range(200)}
    assert len(picked) > 1, "weighting should still reach every outcome"


# --- rolling ---------------------------------------------------------------

def test_events_respect_contract_tier():
    rng = random.Random(1)
    for tier in (1, 2, 3, 4):
        for _ in range(300):
            event = roll_event(tier, rng)
            if event is not None:
                assert tier in event.tiers


def test_events_are_occasional_not_constant():
    rng = random.Random(9)
    hits = sum(1 for _ in range(4000) if roll_event(1, rng) is not None)
    rate = hits / 4000
    assert EVENT_CHANCE * 0.85 < rate < EVENT_CHANCE * 1.15


# --- playing them ----------------------------------------------------------

def test_a_pending_event_takes_over_the_numbers():
    player = adventuring()
    run = force_event(player)
    assert run.encounter is None, "an event pauses the fighting"

    reply = handle(player, "!1")
    assert reply is not None
    assert player.character.run.pending_event == "", "the choice should settle"


def test_choosing_applies_the_outcome():
    player = adventuring(gold=100)
    force_event(player, "wayside_shrine")
    before = player.character.gold

    handle(player, "!1")  # leave a coin — costs gold either way
    assert player.character.gold < before


def test_an_out_of_range_option_is_explained():
    player = adventuring()
    force_event(player)
    reply = handle(player, "!9")
    assert "no option 9" in " ".join(reply).lower()
    assert player.character.run.pending_event, "still waiting"


def test_the_next_encounter_follows_the_choice():
    player = adventuring()
    force_event(player)
    handle(player, "!3")  # walk on
    run = player.character.run
    assert run is None or run.encounter is not None


def test_gold_never_goes_negative():
    player = adventuring(gold=2)
    force_event(player, "wayside_shrine")
    handle(player, "!1")
    assert player.character.gold >= 0


def test_an_event_can_kill_you():
    player = adventuring()
    run = force_event(player, "hedge_witch")
    run.hp = 1

    for _ in range(40):
        if player.character is None:
            break
        if player.character.run is None:
            handle(player, "!board")
            handle(player, "!accept 1")
        force_event(player, "hedge_witch")
        player.character.run.hp = 1
        handle(player, "!1")  # the mushrooms, which are sometimes not supper

    assert player.character is None, "a bad outcome should be able to finish you"


def test_you_can_still_check_yourself_mid_decision():
    player = adventuring()
    force_event(player)
    assert handle(player, "!status") is not None
    assert handle(player, "!bag") is not None
    assert player.character.run.pending_event, "looking is not choosing"


def test_you_can_portal_out_of_a_decision():
    player = adventuring()
    force_event(player)
    handle(player, "!portal")
    assert player.character.run is None


def test_a_pending_event_survives_a_restart(tmp_path):
    from core.persist import load_all, save_all

    player = adventuring()
    force_event(player, "old_soldier")
    path = tmp_path / "players.json"
    save_all(path, {player.mxid: player})

    restored = load_all(path)[player.mxid].character
    assert restored.run.pending_event == "old_soldier"


def test_an_event_deleted_from_content_is_dropped(tmp_path):
    from core.persist import load_all, save_all

    player = adventuring()
    force_event(player, "old_soldier")
    player.character.run.pending_event = "an_event_that_was_removed"

    path = tmp_path / "players.json"
    save_all(path, {player.mxid: player})
    restored = load_all(path)[player.mxid].character
    assert restored.run is not None, "the run should survive"
    assert restored.run.pending_event == ""
