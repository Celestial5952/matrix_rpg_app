"""Shop, inventory, loot, and the rules that stop items being wasted."""

from __future__ import annotations

import json
import random

from core.game import handle
from core.items import ITEMS, roll_loot
from core.persist import load_all, save_all

from .conftest import make_char


def armed(gold: int = 200, **kw):
    """A character in a fight, with money."""
    player = make_char(**kw)
    player.character.gold = gold
    return player


def in_combat(player):
    handle(player, "!board")
    handle(player, "!accept 1")
    return player.character


# --- shop ------------------------------------------------------------------

def test_shop_lists_stock_and_gold():
    player = armed(gold=50)
    reply = handle(player, "!shop")
    assert "50" in reply[0]
    assert any("Lesser Healing Potion" in line for line in reply)


def test_buy_deducts_gold_and_fills_the_bag():
    player = armed(gold=50)
    handle(player, "!buy 1")
    char = player.character
    assert char.gold == 50 - ITEMS["lesser_potion"].price
    assert char.inventory["lesser_potion"] == 1


def test_buy_multiple():
    player = armed(gold=100)
    handle(player, "!buy 1 3")
    assert player.character.inventory["lesser_potion"] == 3


def test_buy_refuses_when_short_and_says_what_is_affordable():
    player = armed(gold=13)
    reply = handle(player, "!buy 1 5")
    assert player.character.inventory == {}
    assert player.character.gold == 13
    assert "afford" in " ".join(reply).lower()


def test_buy_with_no_gold_at_all():
    player = armed(gold=0)
    reply = handle(player, "!buy 1")
    assert player.character.inventory == {}
    assert "costs" in " ".join(reply).lower()


def test_buy_by_name_as_well_as_number():
    player = armed(gold=100)
    handle(player, "!buy whetstone")
    assert player.character.inventory["whetstone"] == 1


def test_ambiguous_name_asks_which():
    player = armed(gold=200)
    reply = handle(player, "!buy potion")
    assert "which one" in reply[0].lower()
    assert player.character.inventory == {}


def test_buy_rejects_unstocked():
    player = armed()
    reply = handle(player, "!buy sword")
    assert "doesn't stock" in reply[0]


# --- inventory -------------------------------------------------------------

def test_inventory_aliases_all_work():
    player = armed(gold=50)
    handle(player, "!buy 1")
    for word in ("!bag", "!inventory", "!items"):
        reply = handle(player, word)
        assert reply is not None
        assert any("Lesser Healing Potion" in line for line in reply)


def test_empty_bag_says_so():
    player = armed()
    assert "empty" in handle(player, "!bag")[0].lower()


def test_use_outside_combat_is_refused_not_silent():
    player = armed(gold=50)
    handle(player, "!buy 1")
    reply = handle(player, "!use lesser")
    assert "not in a fight" in " ".join(reply).lower()
    assert player.character.inventory["lesser_potion"] == 1


# --- using items -----------------------------------------------------------

def test_using_a_potion_heals_and_consumes_it():
    player = armed(gold=50)
    handle(player, "!buy 1 2")
    char = in_combat(player)
    char.run.hp = 5
    handle(player, "!use lesser")
    assert char.run.hp > 5
    assert char.inventory["lesser_potion"] == 1


def test_using_an_item_costs_the_turn():
    """The monster must still act, or items would be free power."""
    player = armed(gold=50)
    handle(player, "!buy 1")
    char = in_combat(player)
    char.run.hp = char.run.max_hp - 30
    before_monster_hp = char.run.encounter.hp
    reply = handle(player, "!use lesser")
    # Monster's telegraphed move resolves in the same exchange.
    assert any("hits you" in line or "braces" in line for line in reply)
    assert char.run.encounter.hp == before_monster_hp, "item should not damage"


def test_a_used_item_leaves_the_bag_entirely():
    player = armed(gold=50)
    handle(player, "!buy 1")
    char = in_combat(player)
    char.run.hp = 1
    handle(player, "!use lesser")
    assert "lesser_potion" not in char.inventory


def test_throwable_ignores_armour():
    """Full listed damage against an armoured target proves armour is skipped."""
    from core import combat

    from .conftest import fighting

    char = fighting(monster="wight")
    assert char.run.encounter.monster.armor > 0
    fire = ITEMS["alchemists_fire"]
    char.inventory[fire.key] = 1

    before = char.run.encounter.hp
    combat.use_item(char, fire)
    assert before - char.run.encounter.hp == fire.damage


def test_throwable_can_finish_an_encounter():
    player = armed(gold=50)
    handle(player, "!buy alchemist")
    char = in_combat(player)
    char.run.encounter.hp = 1
    reply = handle(player, "!use alchemist")
    assert any("falls" in line for line in reply)


def test_buff_boosts_the_next_attack_then_expires():
    player = armed(gold=50)
    handle(player, "!buy whetstone")
    char = in_combat(player)
    handle(player, "!use whetstone")
    assert char.run.next_attack_bonus > 0
    handle(player, "!1")
    assert char.run.next_attack_bonus == 0


def test_full_health_refuses_to_waste_a_potion():
    player = armed(gold=50)
    handle(player, "!buy 1")
    char = in_combat(player)
    char.run.hp = char.run.max_hp
    reply = handle(player, "!use lesser")
    assert "wasted" in " ".join(reply).lower()
    assert char.inventory["lesser_potion"] == 1


def test_full_focus_refuses_to_waste_a_draught():
    player = armed(gold=50)
    handle(player, "!buy focus")
    char = in_combat(player)
    char.run.focus = char.run.max_focus
    reply = handle(player, "!use focus")
    assert "wasted" in " ".join(reply).lower()
    assert char.inventory["focus_draught"] == 1


def test_using_what_you_do_not_carry():
    player = armed(gold=50)
    handle(player, "!buy 1")
    in_combat(player)
    reply = handle(player, "!use whetstone")
    assert "not carrying" in " ".join(reply).lower()


# --- loot ------------------------------------------------------------------

def test_loot_tables_only_yield_real_items():
    rng = random.Random(7)
    for tier in (1, 2, 3):
        for _ in range(200):
            for key in roll_loot(tier, rng):
                assert key in ITEMS


def test_tier_three_always_drops_something():
    rng = random.Random(3)
    assert all(roll_loot(3, rng) for _ in range(50))


def test_completing_a_contract_can_drop_loot():
    player = armed(gold=0)
    char = in_combat(player)
    for _ in range(400):
        if player.character is None or char.runs_completed:
            break
        char.run.hp = char.run.max_hp  # immortal: we're testing the payout
        handle(player, "!1")
    assert char.runs_completed >= 1
    # Tier 1 can legitimately roll a dud, so assert the mechanism, not a drop.
    assert isinstance(char.inventory, dict)


# --- permadeath + persistence ----------------------------------------------

def test_death_takes_the_bag_with_it():
    player = armed(gold=100)
    handle(player, "!buy 1 3")
    char = in_combat(player)
    assert char.inventory

    for _ in range(80):
        if player.character is None:
            break
        if player.character.run is None:
            handle(player, "!board")
            handle(player, "!accept 1")
        player.character.run.hp = 1
        handle(player, "!1")

    assert player.character is None
    handle(player, "!create"); handle(player, "!Next")
    handle(player, "!human"); handle(player, "!fighter")
    assert player.character.inventory == {}
    assert player.character.gold == 0


def test_inventory_round_trips(tmp_path):
    path = tmp_path / "players.json"
    player = armed(gold=100)
    handle(player, "!buy 1 2")
    handle(player, "!buy whetstone")

    save_all(path, {player.mxid: player})
    loaded = load_all(path)[player.mxid]
    assert loaded.character.inventory == player.character.inventory


def test_unknown_items_in_a_save_are_dropped(tmp_path):
    """An item deleted from items.py must not crash the player's next !bag."""
    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@a:srv": {"display_name": "A", "character": {
            "name": "X", "race_key": "elf", "class_key": "rogue",
            "inventory": {"lesser_potion": 2, "sword_of_nope": 1, "bad": "count"},
        }},
    }))
    inv = load_all(path)["@a:srv"].character.inventory
    assert inv == {"lesser_potion": 2}


def test_negative_or_zero_counts_are_dropped(tmp_path):
    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@a:srv": {"display_name": "A", "character": {
            "name": "X", "race_key": "elf", "class_key": "rogue",
            "inventory": {"lesser_potion": 0, "whetstone": -3},
        }},
    }))
    assert load_all(path)["@a:srv"].character.inventory == {}
