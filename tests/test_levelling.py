"""Levelling, ability unlocks, and the editable loadout."""

from __future__ import annotations

import json

from core.chargen import (
    CLASSES,
    LEVEL_RENOWN,
    MAX_LEVEL,
    SLOTS,
    default_loadout,
    level_for,
    renown_for_next,
    spellbook_order,
)
from core.game import handle
from core.persist import load_all, save_all
from core.state import Character

from .conftest import make_char


def at_level(level: int, char_class: str = "cleric", mxid: str = "@lv:srv"):
    player = make_char(char_class=char_class, mxid=mxid)
    player.character.renown = LEVEL_RENOWN[level - 1]
    assert player.character.level == level
    return player


# --- levels ----------------------------------------------------------------

def test_level_thresholds_are_monotonic():
    assert list(LEVEL_RENOWN) == sorted(LEVEL_RENOWN)
    assert LEVEL_RENOWN[0] == 0


def test_level_for_renown():
    assert level_for(0) == 1
    assert level_for(LEVEL_RENOWN[1] - 1) == 1
    assert level_for(LEVEL_RENOWN[1]) == 2
    assert level_for(10 ** 6) == MAX_LEVEL


def test_renown_for_next_runs_out_at_max():
    assert renown_for_next(0) == LEVEL_RENOWN[1]
    assert renown_for_next(LEVEL_RENOWN[-1]) is None


def test_stats_grow_with_level():
    low = at_level(1, mxid="@a:srv").character
    high = at_level(MAX_LEVEL, mxid="@b:srv").character
    assert high.max_hp > low.max_hp
    assert high.power >= low.power
    assert high.max_focus >= low.max_focus


def test_rank_follows_level_so_the_board_opens_up():
    assert at_level(1, mxid="@a:srv").character.rank == 1
    assert at_level(3, mxid="@b:srv").character.rank == 2
    assert at_level(5, mxid="@c:srv").character.rank == 3


def test_levelling_is_announced_on_payout():
    player = make_char()
    char = player.character
    char.renown = LEVEL_RENOWN[1] - 1  # one contract short of level 2
    handle(player, "!board")
    handle(player, "!accept 1")
    lines = []
    for _ in range(400):
        if player.character is None or char.runs_completed:
            break
        char.run.hp = char.run.max_hp
        lines = handle(player, "!1") or lines
    assert char.level >= 2
    assert any("Level" in line for line in lines)


# --- unlocks ---------------------------------------------------------------

def test_every_class_has_more_to_learn():
    for cls in CLASSES:
        later = [a for a in cls.pool if a.unlock_level > 1]
        assert later, f"{cls.name} unlocks nothing by levelling"


def test_every_class_starts_with_all_four_slots():
    for cls in CLASSES:
        starting = {a.slot for a in cls.pool if a.unlock_level <= 1}
        assert starting == set(SLOTS), f"{cls.name} is missing a starting slot"


def test_every_pool_ability_has_a_valid_slot():
    for cls in CLASSES:
        for ability in cls.pool:
            assert ability.slot in SLOTS, f"{cls.name}/{ability.key}"


def test_basic_slot_abilities_are_always_free():
    """The basic slot is the no-focus fallback; charging for it could soft-lock."""
    for cls in CLASSES:
        for ability in cls.pool:
            if ability.slot == "basic":
                assert ability.cost == 0, f"{cls.name}/{ability.key} costs focus"


def test_spellbook_lists_locked_abilities_as_locked():
    player = at_level(1)
    text = " ".join(handle(player, "!spellbook"))
    assert "unlocks at level" in text


def test_spellbook_numbering_matches_the_equip_resolver():
    """If display and resolver disagree, players equip what they didn't pick."""
    for cls in CLASSES:
        player = at_level(MAX_LEVEL, char_class=cls.key, mxid=f"@{cls.key}:srv")
        char = player.character
        order = spellbook_order(char.class_key, char.level)
        for i, ability in enumerate(order, 1):
            handle(player, f"!equip {i}")
            equipped = {a.key for a in char.abilities}
            assert ability.key in equipped, f"{cls.name}: !equip {i} != {ability.name}"


# --- the loadout -----------------------------------------------------------

def test_default_loadout_is_the_level_one_kit():
    for cls in CLASSES:
        loadout = default_loadout(cls.key)
        assert set(loadout) == set(SLOTS)
        for slot, key in loadout.items():
            ability = next(a for a in cls.pool if a.key == key)
            assert ability.unlock_level == 1
            assert ability.slot == slot


def test_equipping_swaps_within_the_slot_only():
    player = at_level(MAX_LEVEL, char_class="fighter")
    char = player.character
    before = {a.slot: a.key for a in char.abilities}
    handle(player, "!equip sunder")
    after = {a.slot: a.key for a in char.abilities}

    assert after["signature"] == "sunder"
    for slot in SLOTS:
        if slot != "signature":
            assert after[slot] == before[slot], f"{slot} changed unexpectedly"


def test_the_kit_is_always_four_typed_slots_however_you_equip():
    player = at_level(MAX_LEVEL, char_class="wizard")
    char = player.character
    for i in range(1, len(spellbook_order("wizard", char.level)) + 1):
        handle(player, f"!equip {i}")
        assert [a.slot for a in char.abilities] == list(SLOTS)
        assert char.abilities[0].cost == 0, "lost the free basic attack"


def test_cannot_equip_something_still_locked():
    player = at_level(1, char_class="fighter")
    reply = handle(player, "!equip executioner")
    assert "unlocks at level" in " ".join(reply)
    assert "executioner" not in {a.key for a in player.character.abilities}


def test_equip_rejects_nonsense():
    player = at_level(MAX_LEVEL)
    assert "No such ability" in handle(player, "!equip banjo")[0]


def test_equip_is_refused_mid_fight():
    player = at_level(MAX_LEVEL, char_class="fighter")
    handle(player, "!board")
    handle(player, "!accept 1")
    before = {a.key for a in player.character.abilities}
    reply = handle(player, "!equip sunder")
    assert "hall" in " ".join(reply).lower()
    assert {a.key for a in player.character.abilities} == before


def test_equipping_what_is_already_equipped_says_so():
    player = at_level(MAX_LEVEL, char_class="fighter")
    handle(player, "!equip sunder")
    assert "already" in handle(player, "!equip sunder")[0]


# --- persistence -----------------------------------------------------------

def test_loadout_round_trips(tmp_path):
    path = tmp_path / "players.json"
    player = at_level(MAX_LEVEL, char_class="fighter")
    handle(player, "!equip sunder")
    handle(player, "!equip bulwark")

    save_all(path, {player.mxid: player})
    loaded = load_all(path)[player.mxid].character
    assert [a.key for a in loaded.abilities] == [
        a.key for a in player.character.abilities
    ]


def test_a_loadout_naming_an_unknown_ability_falls_back(tmp_path):
    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@a:srv": {"display_name": "A", "character": {
            "name": "X", "race_key": "elf", "class_key": "fighter", "renown": 200,
            "loadout": {"signature": "ability_that_was_deleted",
                        "defence": "bulwark"},
        }},
    }))
    char = load_all(path)["@a:srv"].character
    keys = [a.key for a in char.abilities]
    assert "bulwark" in keys, "valid entries should survive"
    assert "ability_that_was_deleted" not in keys
    assert len(keys) == 4


def test_a_loadout_entry_in_the_wrong_slot_is_dropped(tmp_path):
    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@a:srv": {"display_name": "A", "character": {
            "name": "X", "race_key": "elf", "class_key": "fighter", "renown": 200,
            "loadout": {"basic": "cleave"},  # cleave is a signature
        }},
    }))
    char = load_all(path)["@a:srv"].character
    assert char.abilities[0].key == "strike"
    assert char.abilities[0].cost == 0


def test_losing_a_level_downgrades_gracefully():
    """Nothing takes renown away today, but the kit must not break if it did."""
    char = Character(name="X", race_key="human", class_key="fighter", renown=200)
    char.loadout["signature"] = "executioner"
    assert char.abilities[1].key == "executioner"

    char.renown = 0
    assert char.level == 1
    assert char.abilities[1].key == "cleave", "locked ability should fall back"
