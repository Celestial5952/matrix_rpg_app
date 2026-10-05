"""Wearable gear: small, slot-limited, and gone when the character is."""

from __future__ import annotations

import random

from core.game import handle
from core.items import (
    GEAR,
    GEAR_DROPS,
    GEAR_KEYS,
    GEAR_SLOTS,
    ITEMS,
    SHOP_STOCK,
    roll_gear,
    roll_loot,
)
from core.persist import load_all, save_all
from core.state import Item

from .conftest import make_char


def bag(player, *keys):
    for key in keys:
        player.character.inventory[key] = player.character.inventory.get(key, 0) + 1


# --- the table -------------------------------------------------------------

def test_every_piece_has_a_real_slot_and_a_bonus():
    for item in GEAR:
        assert item.kind == "gear"
        assert item.slot in GEAR_SLOTS
        assert item.bonus_power or item.bonus_hp or item.bonus_focus, item.key


def test_every_slot_has_a_shop_piece_and_a_drop_pool():
    shop = {ITEMS[k].slot for k in SHOP_STOCK if ITEMS[k].kind == "gear"}
    assert shop == set(GEAR_SLOTS)
    for tier, (chance, pool) in GEAR_DROPS.items():
        assert 0 < chance < 1
        assert all(key in GEAR_KEYS for key, _ in pool), tier


def test_only_common_gear_is_for_sale():
    for key in SHOP_STOCK:
        if ITEMS[key].kind == "gear":
            assert ITEMS[key].price > 0
    for key in GEAR_KEYS:
        if key not in SHOP_STOCK:
            assert ITEMS[key].price == 0, "better kit has to be found"


def test_gear_stays_modest():
    """Gear must not out-scale a level: a rare weapon is a few points, not a
    second class."""
    assert max(i.bonus_power for i in GEAR) <= 3
    assert max(i.bonus_hp for i in GEAR) <= 12
    assert max(i.bonus_focus for i in GEAR) <= 2


def test_higher_tiers_drop_gear_more_often_and_better():
    assert GEAR_DROPS[1][0] < GEAR_DROPS[2][0] < GEAR_DROPS[3][0]
    rare = {"wightbane_edge", "revenant_plate", "star_of_the_deep"}
    assert not rare & {k for k, _ in GEAR_DROPS[1][1]}
    assert not rare & {k for k, _ in GEAR_DROPS[2][1]}
    assert rare <= {k for k, _ in GEAR_DROPS[3][1]}


def test_gear_actually_drops_at_roughly_the_stated_rate():
    rng = random.Random(7)
    hits = sum(roll_gear(2, rng) is not None for _ in range(4000))
    assert 0.09 < hits / 4000 < 0.15


def test_loot_rolls_can_include_gear():
    rng = random.Random(3)
    seen = {k for _ in range(3000) for k in roll_loot(3, rng) if k in GEAR_KEYS}
    assert seen, "gear never dropped from tier 3 loot"


# --- wearing ---------------------------------------------------------------

def test_wearing_gear_raises_the_stats_and_empties_the_bag_slot():
    player = make_char()
    char = player.character
    base = (char.max_hp, char.power, char.max_focus)
    bag(player, "duelists_blade")

    reply = "\n".join(handle(player, "!wear blade"))

    assert char.power == base[1] + 2
    assert (char.max_hp, char.max_focus) == (base[0], base[2])
    assert char.equipment == {"weapon": "duelists_blade"}
    assert "duelists_blade" not in char.inventory, "worn gear leaves the bag"
    assert "Duelist's Blade" in reply


def test_a_piece_can_add_several_stats():
    player = make_char()
    base = player.character.max_hp
    bag(player, "wightbane_edge")
    handle(player, "!wear wightbane")
    assert player.character.max_hp == base + 2


def test_swapping_returns_the_old_piece_to_the_bag():
    player = make_char()
    bag(player, "ratcatchers_cudgel", "duelists_blade")
    handle(player, "!wear cudgel")
    handle(player, "!wear blade")

    char = player.character
    assert char.equipment["weapon"] == "duelists_blade"
    assert char.inventory == {"ratcatchers_cudgel": 1}


def test_wear_by_number_matches_the_gear_screen():
    player = make_char()
    bag(player, "quilted_jerkin", "ratcatchers_cudgel")
    # `!gear` numbers spare pieces alphabetically by key, so 1 is the jerkin.
    screen = "\n".join(handle(player, "!gear"))
    assert screen.index("Quilted Jerkin") < screen.index("Ratcatcher's Cudgel")
    handle(player, "!wear 1")
    assert player.character.equipment == {"armour": "quilted_jerkin"}


def test_remove_by_slot_name_or_item():
    player = make_char()
    bag(player, "quilted_jerkin")
    handle(player, "!wear jerkin")
    base = player.character.max_hp

    handle(player, "!remove arm")  # prefix of "armour"
    assert player.character.equipment == {}
    assert player.character.inventory == {"quilted_jerkin": 1}
    assert player.character.max_hp == base - 4


def test_removing_an_empty_slot_says_so():
    player = make_char()
    assert "aren't wearing" in "\n".join(handle(player, "!remove weapon"))


def test_wearing_nothing_you_own_is_refused():
    player = make_char()
    assert "Nothing in your bag" in "\n".join(handle(player, "!wear sword"))
    bag(player, "lucky_button")
    assert "not carrying" in "\n".join(handle(player, "!wear sword"))


def test_gear_cannot_be_changed_mid_contract():
    player = make_char()
    bag(player, "duelists_blade")
    handle(player, "!board")
    handle(player, "!accept 1")

    reply = "\n".join(handle(player, "!wear blade"))
    assert "back at the hall" in reply
    assert player.character.equipment == {}

    reply = "\n".join(handle(player, "!remove weapon"))
    assert "back at the hall" in reply


def test_the_gear_screen_is_readable_mid_contract():
    player = make_char()
    handle(player, "!board")
    handle(player, "!accept 1")
    assert "kit" in "\n".join(handle(player, "!gear"))


def test_use_on_gear_points_at_wear_and_consumes_nothing():
    player = make_char()
    bag(player, "lucky_button")
    reply = "\n".join(handle(player, "!use button"))
    assert "worn, not used" in reply
    assert player.character.inventory == {"lucky_button": 1}


def test_use_on_gear_mid_fight_is_refused_without_burning_it():
    player = make_char()
    bag(player, "lucky_button")
    handle(player, "!board")
    handle(player, "!accept 1")
    reply = "\n".join(handle(player, "!use button"))
    assert "worn, not used" in reply
    assert player.character.inventory == {"lucky_button": 1}


def test_worn_gear_flows_into_the_run():
    player = make_char()
    bag(player, "boiled_leather", "duelists_blade")
    handle(player, "!wear leather")
    handle(player, "!wear blade")
    char = player.character
    handle(player, "!board")
    handle(player, "!accept 1")
    assert char.run.max_hp == char.max_hp
    assert char.run.power == char.power


def test_status_shows_the_kit():
    player = make_char()
    bag(player, "lucky_button")
    handle(player, "!wear button")
    assert "Lucky Button" in "\n".join(handle(player, "!status"))


# --- shopping --------------------------------------------------------------

def test_buying_gear_goes_to_the_bag_and_costs_gold():
    player = make_char()
    player.character.gold = 100
    n = SHOP_STOCK.index("quilted_jerkin") + 1
    handle(player, f"!buy {n}")
    assert player.character.inventory["quilted_jerkin"] == 1
    assert player.character.gold == 100 - ITEMS["quilted_jerkin"].price


def test_you_cannot_buy_a_second_one_or_a_stack():
    player = make_char()
    player.character.gold = 500
    n = SHOP_STOCK.index("quilted_jerkin") + 1
    handle(player, f"!buy {n}")
    assert "already have" in "\n".join(handle(player, f"!buy {n}"))
    handle(player, "!wear jerkin")
    assert "already have" in "\n".join(handle(player, f"!buy {n}")), (
        "a worn piece counts"
    )
    assert "plenty" in "\n".join(handle(player, f"!buy {n + 1} 3"))
    assert player.character.gold == 500 - ITEMS["quilted_jerkin"].price


def test_the_shop_lists_gear_with_its_effect():
    player = make_char()
    shop = "\n".join(handle(player, "!shop"))
    assert "Ratcatcher's Cudgel" in shop
    assert "+1 power" in shop


# --- death and trade -------------------------------------------------------

def test_gear_can_be_given_but_a_worn_piece_cannot():
    a = make_char(name="Doc", mxid="@a:srv")
    b = make_char(name="Wren", mxid="@b:srv")
    roster = {a.mxid: a, b.mxid: b}
    bag(a, "lucky_button", "quilted_jerkin")
    handle(a, "!wear jerkin")

    handle(a, "!give Wren button", roster)
    assert b.character.inventory == {"lucky_button": 1}

    reply = "\n".join(handle(a, "!give Wren jerkin", roster))
    assert "bag is empty" in reply, "a worn piece is not in the bag"
    assert b.character.inventory == {"lucky_button": 1}
    assert a.character.equipment == {"armour": "quilted_jerkin"}


def test_gear_dies_with_the_character():
    player = make_char()
    bag(player, "duelists_blade")
    handle(player, "!wear blade")
    handle(player, "!board")
    handle(player, "!accept 1")
    for _ in range(600):
        if player.character is None:
            break
        player.character.run.hp = 1
        player.character.run.encounter.hp = 10 ** 6
        handle(player, "!1")
    assert player.character is None
    handle(player, "!create")
    handle(player, "!Next")
    handle(player, "!human")
    handle(player, "!fighter")
    assert player.character.equipment == {}
    assert not any(k in GEAR_KEYS for k in player.character.inventory)


# --- persistence -----------------------------------------------------------

def test_worn_gear_survives_a_restart(tmp_path):
    path = tmp_path / "players.json"
    player = make_char()
    bag(player, "boiled_leather", "ratcatchers_cudgel")
    handle(player, "!wear leather")
    save_all(path, {player.mxid: player})

    loaded = load_all(path)[player.mxid].character
    assert loaded.equipment == {"armour": "boiled_leather"}
    assert loaded.max_hp == player.character.max_hp
    assert loaded.inventory == {"ratcatchers_cudgel": 1}


def test_a_bad_equipment_entry_is_dropped_not_fatal(tmp_path):
    import json
    path = tmp_path / "players.json"
    player = make_char()
    save_all(path, {player.mxid: player})
    raw = json.loads(path.read_text())
    raw[player.mxid]["character"]["equipment"] = {
        "weapon": "does_not_exist",
        "armour": "ratcatchers_cudgel",   # real item, wrong slot
        "hat": "lucky_button",            # no such slot
        "charm": "lucky_button",          # the only valid one
    }
    path.write_text(json.dumps(raw))
    assert load_all(path)[player.mxid].character.equipment == {
        "charm": "lucky_button"}


def test_old_saves_without_equipment_still_load(tmp_path):
    import json
    path = tmp_path / "players.json"
    player = make_char()
    save_all(path, {player.mxid: player})
    raw = json.loads(path.read_text())
    del raw[player.mxid]["character"]["equipment"]
    path.write_text(json.dumps(raw))
    assert load_all(path)[player.mxid].character.equipment == {}


def test_unknown_item_in_a_slot_cannot_raise():
    player = make_char()
    player.character.equipment = {"weapon": "vanished"}
    assert player.character.worn() == []
    assert player.character.power > 0
    assert isinstance(ITEMS["lucky_button"], Item)
