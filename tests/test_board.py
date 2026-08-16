"""Contract rolling: variety, modifiers, and the story hook."""

from __future__ import annotations

import random

from core import combat
from core.content import (
    MODIFIERS,
    MONSTERS,
    QUESTS,
    STORY_CHANCE,
    plain_contract,
    quests_for_rank,
    roll_contract,
    scaled_monster,
    story_quests,
)
from core.game import BOARD_SIZE, handle, roll_board

from .conftest import make_char

ORDINARY = [q for q in QUESTS if not q.story]


def boards(char, count: int, seed: int = 0):
    for i in range(count):
        roll_board(char, random.Random(seed + i))
        yield char.board


# --- variety ---------------------------------------------------------------

def test_the_same_template_rolls_differently():
    quest = ORDINARY[1]
    rng = random.Random(5)
    rolled = [roll_contract(quest, rng) for _ in range(60)]
    assert len({c.gold for c in rolled}) > 1, "rewards never varied"
    assert len({c.stages for c in rolled}) > 1, "length never varied"
    assert len({c.flavor for c in rolled}) > 1, "opening line never varied"


def test_modifiers_are_sometimes_rolled_and_sometimes_not():
    rng = random.Random(11)
    rolled = [roll_contract(q, rng) for q in ORDINARY for _ in range(30)]
    counts = {len(c.modifiers) for c in rolled}
    assert 0 in counts, "every contract had a modifier"
    assert counts - {0}, "no contract ever had a modifier"


def test_modifiers_are_never_duplicated_on_one_contract():
    rng = random.Random(3)
    for _ in range(400):
        contract = roll_contract(random.choice(ORDINARY), rng)
        keys = [m.key for m in contract.modifiers]
        assert len(keys) == len(set(keys))


def test_rewards_stay_positive_however_they_roll():
    rng = random.Random(9)
    for _ in range(600):
        contract = roll_contract(random.choice(ORDINARY), rng)
        assert contract.gold >= 1
        assert contract.renown >= 1
        assert contract.stages >= 1, "a contract with no encounters is unplayable"


def test_plain_contract_uses_exact_template_numbers():
    for quest in QUESTS:
        contract = plain_contract(quest)
        assert (contract.stages, contract.gold, contract.renown) == (
            quest.stages, quest.gold, quest.renown)
        assert contract.modifiers == ()


# --- modifiers change the fight -------------------------------------------

def test_stage_modifiers_actually_change_the_length():
    """roll_contract applies stages_delta; nothing else does."""
    quest = next(q for q in ORDINARY if q.stages >= 3)
    rng = random.Random(0)
    seen: dict[str, set[int]] = {"swarming": set(), "urgent": set(), "none": set()}
    for _ in range(2000):
        contract = roll_contract(quest, rng)
        keys = {m.key for m in contract.modifiers}
        if keys == {"swarming"}:
            seen["swarming"].add(contract.stages)
        elif keys == {"urgent"}:
            seen["urgent"].add(contract.stages)
        elif not keys:
            seen["none"].add(contract.stages)

    assert seen["none"] == {quest.stages}
    assert seen["swarming"] and min(seen["swarming"]) > quest.stages
    assert seen["urgent"] and max(seen["urgent"]) < quest.stages


def test_every_modifier_is_reachable_and_well_formed():
    for m in MODIFIERS:
        assert m.name and m.blurb
        assert m.gold_mult > 0 and m.renown_mult > 0


def test_fortified_actually_adds_armour():
    quest = ORDINARY[0]
    base = plain_contract(quest)
    fortified = base.__class__(**{**base.__dict__,
                                  "modifiers": (_mod("fortified"),)})
    monster = MONSTERS["kobold"]
    assert scaled_monster(monster, fortified).armor > monster.armor
    assert scaled_monster(monster, base).armor == monster.armor


def test_teeming_and_savage_scale_the_monster():
    base = plain_contract(ORDINARY[0])
    monster = MONSTERS["kobold"]
    teeming = base.__class__(**{**base.__dict__, "modifiers": (_mod("teeming"),)})
    savage = base.__class__(**{**base.__dict__, "modifiers": (_mod("savage"),)})
    assert scaled_monster(monster, teeming).max_hp > monster.max_hp
    assert scaled_monster(monster, savage).power > monster.power


def test_spawned_encounters_carry_the_modifier():
    base = plain_contract(ORDINARY[0])
    fortified = base.__class__(**{**base.__dict__,
                                  "modifiers": (_mod("fortified"),)})
    rng = random.Random(1)
    plain_enc = combat.spawn("kobold", rng, base)
    hard_enc = combat.spawn("kobold", rng, fortified)
    assert hard_enc.monster.armor > plain_enc.monster.armor


def test_scaling_never_produces_a_zero_hp_monster():
    base = plain_contract(ORDINARY[0])
    for m in MODIFIERS:
        contract = base.__class__(**{**base.__dict__, "modifiers": (m,)})
        for monster in MONSTERS.values():
            scaled = scaled_monster(monster, contract)
            assert scaled.max_hp >= 1 and scaled.power >= 1 and scaled.armor >= 0


def _mod(key: str):
    return next(m for m in MODIFIERS if m.key == key)


# --- the board -------------------------------------------------------------

def test_board_is_always_the_same_size():
    player = make_char()
    player.character.renown = 200
    for board in boards(player.character, 30):
        assert len(board) == BOARD_SIZE


def test_board_respects_rank_for_ordinary_work():
    player = make_char()
    char = player.character
    for board in boards(char, 30):
        for contract in board:
            if not contract.story:
                assert contract.tier <= char.rank


def test_board_regenerates_between_reads():
    """Two boards in a row should not be identical, or variety is invisible."""
    player = make_char()
    char = player.character
    seen = set()
    for board in boards(char, 20, seed=100):
        seen.add(tuple((c.name, c.gold, c.stages,
                        tuple(m.key for m in c.modifiers)) for c in board))
    assert len(seen) > 1


# --- story contracts -------------------------------------------------------

def test_story_quests_exist_and_are_gated():
    story = [q for q in QUESTS if q.story]
    assert story, "no story contracts defined"
    for quest in story:
        assert quest.min_renown > 0, f"{quest.key} is not gated on renown"


def test_story_quests_are_not_offered_below_their_renown():
    assert story_quests(0) == []
    lowest = min(q.min_renown for q in QUESTS if q.story)
    assert story_quests(lowest - 1) == []
    assert story_quests(lowest)


def test_story_never_appears_on_a_low_renown_board():
    player = make_char()
    char = player.character
    char.renown = 0
    for board in boards(char, 60, seed=500):
        assert not any(c.story for c in board)


def test_story_does_appear_once_renown_is_high_enough():
    player = make_char()
    char = player.character
    char.renown = max(q.min_renown for q in QUESTS if q.story)
    assert any(any(c.story for c in board) for board in boards(char, 80, seed=7))


def test_story_is_occasional_not_constant():
    """It should feel like something turning up, not a permanent fixture."""
    player = make_char()
    char = player.character
    char.renown = max(q.min_renown for q in QUESTS if q.story)
    with_story = sum(any(c.story for c in board)
                     for board in boards(char, 200, seed=31))
    assert 0 < with_story < 200
    assert with_story / 200 < STORY_CHANCE * 2.5


def test_story_quests_are_excluded_from_ordinary_rank_draws():
    for rank in (1, 2, 3):
        assert all(not q.story for q in quests_for_rank(rank))


def test_a_story_contract_is_playable():
    player = make_char()
    char = player.character
    char.renown = max(q.min_renown for q in QUESTS if q.story)
    story = next(q for q in QUESTS if q.story)
    char.board = [roll_contract(story, random.Random(2))]

    reply = handle(player, "!accept 1")
    assert reply is not None
    assert char.run is not None
    assert char.run.encounter is not None
