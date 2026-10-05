"""Consumables, gear, the shop, and loot tables.

Consumables are single-use on purpose: spend it or lose it is the heart of the
economy. Gear is the one permanent exception, and it is kept deliberately small
so it cannot compete with class identity for the same design space. One piece
per slot, modest flat bonuses, and no gear gives a class something its pool
does not already have — +power, +HP or +focus, nothing else. Worn or carried,
it dies with the character, so a long run of luck still costs you the lot.

Like content.py, this file is meant to grow. Nothing else should need editing
to add an item.
"""

from __future__ import annotations

import random

from .adventures import ADVENTURES
from .state import Item

ITEMS: dict[str, Item] = {
    "lesser_potion": Item(
        key="lesser_potion",
        name="Lesser Healing Potion",
        kind="heal",
        price=12,
        heal=18,
        blurb="Cheap, bitter, reliable. Tastes like a dare.",
    ),
    "greater_potion": Item(
        key="greater_potion",
        name="Greater Healing Potion",
        kind="heal",
        price=32,
        heal=34,
        blurb="The good stuff! Copper, lilies, and a faint hum of guilt.",
    ),
    "focus_draught": Item(
        key="focus_draught",
        name="Focus Draught",
        kind="focus",
        price=14,
        focus=5,
        blurb="Clears the head. Briefly. Gloriously.",
    ),
    "alchemists_fire": Item(
        key="alchemists_fire",
        name="Alchemist's Fire",
        kind="damage",
        price=22,
        damage=24,
        ignores_armor=True,
        blurb="A sealed flask you throw and then — crucially — stop holding.",
    ),
    "whetstone": Item(
        key="whetstone",
        name="Whetstone",
        kind="buff",
        price=16,
        attack_bonus=0.6,
        blurb="One long, loving pass down the edge. Your next hit remembers.",
    ),
}

# ---------------------------------------------------------------------------
# gear
# ---------------------------------------------------------------------------

GEAR_SLOTS: tuple[str, ...] = ("weapon", "armour", "charm")
GEAR_SLOT_LABELS = {"weapon": "Weapon", "armour": "Armour", "charm": "Charm"}

# Rarity is a property of where it comes from, not a field: "common" is stocked
# by Bramblewick, "fine" drops from tier 2+, "rare" only from tier 3.
GEAR: tuple[Item, ...] = (
    Item(key="ratcatchers_cudgel", name="Ratcatcher's Cudgel", kind="gear",
         slot="weapon", price=60, bonus_power=1,
         blurb="Lumpy, well-loved, and technically a table leg."),
    Item(key="quilted_jerkin", name="Quilted Jerkin", kind="gear",
         slot="armour", price=60, bonus_hp=4,
         blurb="Mostly pillow. Surprisingly effective."),
    Item(key="lucky_button", name="Lucky Button", kind="gear",
         slot="charm", price=60, bonus_focus=1,
         blurb="Off somebody's good coat. They do not need it back."),

    Item(key="duelists_blade", name="Duelist's Blade", kind="gear",
         slot="weapon", price=0, bonus_power=2,
         blurb="Balanced for showing off, which is a kind of balance."),
    Item(key="boiled_leather", name="Boiled Leather", kind="gear",
         slot="armour", price=0, bonus_hp=8,
         blurb="Smells like a tannery's worst day. Stops a blade."),
    Item(key="moonstone_charm", name="Moonstone Charm", kind="gear",
         slot="charm", price=0, bonus_focus=2,
         blurb="Cool to the touch. Quiets the part of you that panics."),

    Item(key="wightbane_edge", name="Wightbane Edge", kind="gear",
         slot="weapon", price=0, bonus_power=3, bonus_hp=2,
         blurb="The dead do not like it. This is the best review it could get."),
    Item(key="revenant_plate", name="Revenant Plate", kind="gear",
         slot="armour", price=0, bonus_hp=12,
         blurb="Taken from something that no longer needs a chest."),
    Item(key="star_of_the_deep", name="Star of the Deep", kind="gear",
         slot="charm", price=0, bonus_focus=2, bonus_hp=4,
         blurb="A light you were not supposed to bring back up."),
)

for _gear in GEAR:
    ITEMS[_gear.key] = _gear

GEAR_KEYS: tuple[str, ...] = tuple(g.key for g in GEAR)

ITEMS["summoning_horn"] = Item(
    key="summoning_horn",
    name="Horn of the Absent Friend",
    kind="summon",
    price=0,
    blurb="Blow it and somebody you know arrives, extremely confused.",
)

# One scroll per adventure, generated so adding an adventure adds its scroll.
# Deliberately not stocked by the shop: a scroll is something you find.
for _adv in ADVENTURES.values():
    ITEMS[f"scroll_{_adv.key}"] = Item(
        key=f"scroll_{_adv.key}",
        name=_adv.scroll_name,
        kind="scroll",
        price=0,
        blurb=_adv.scroll_blurb,
        adventure=_adv.key,
    )

SCROLL_KEYS: tuple[str, ...] = tuple(
    k for k, v in ITEMS.items() if v.kind == "scroll"
)

# What the guild hall stocks, in display order.
SHOP_STOCK: tuple[str, ...] = (
    "lesser_potion",
    "greater_potion",
    "focus_draught",
    "alchemists_fire",
    "whetstone",
    # Common gear only. Anything better has to be found.
    "ratcatchers_cudgel",
    "quilted_jerkin",
    "lucky_button",
)

# Loot by contract tier: (rolls, [(item_key or None, weight), ...]).
# A None entry is a dud roll — loot should feel like a result, not a wage.
LOOT_TABLES: dict[int, tuple[int, tuple[tuple[str | None, int], ...]]] = {
    1: (1, (
        (None, 5),
        ("lesser_potion", 6),
        ("focus_draught", 3),
        ("whetstone", 2),
    )),
    2: (2, (
        (None, 4),
        ("lesser_potion", 5),
        ("focus_draught", 4),
        ("whetstone", 3),
        ("alchemists_fire", 3),
        ("greater_potion", 1),
    )),
    3: (2, (
        ("lesser_potion", 3),
        ("focus_draught", 3),
        ("whetstone", 3),
        ("alchemists_fire", 4),
        ("greater_potion", 3),
    )),
}


# Scrolls roll independently of the ordinary loot table, so the rate stays
# legible instead of drifting every time an item is added to a tier. Tier 1
# never drops one: an adventure should arrive as a reward for real work.
SCROLL_CHANCE: dict[int, float] = {2: 0.04, 3: 0.08}


# Gear rolls independently of the ordinary table for the same reason scrolls
# do: the rate stays legible. tier -> (chance per contract, weighted pool).
GEAR_DROPS: dict[int, tuple[float, tuple[tuple[str, int], ...]]] = {
    1: (0.06, (("ratcatchers_cudgel", 1), ("quilted_jerkin", 1),
               ("lucky_button", 1))),
    2: (0.12, (("ratcatchers_cudgel", 2), ("quilted_jerkin", 2),
               ("lucky_button", 2), ("duelists_blade", 1),
               ("boiled_leather", 1), ("moonstone_charm", 1))),
    3: (0.20, (("duelists_blade", 3), ("boiled_leather", 3),
               ("moonstone_charm", 3), ("wightbane_edge", 1),
               ("revenant_plate", 1), ("star_of_the_deep", 1))),
}


def roll_gear(tier: int, rng: random.Random) -> str | None:
    chance, pool = GEAR_DROPS.get(tier, GEAR_DROPS[1])
    if rng.random() >= chance:
        return None
    keys = [k for k, _ in pool]
    weights = [w for _, w in pool]
    return rng.choices(keys, weights=weights, k=1)[0]


def roll_scroll(tier: int, rng: random.Random,
                bonus: float = 1.0) -> str | None:
    chance = SCROLL_CHANCE.get(tier, 0.0) * bonus
    if not SCROLL_KEYS or rng.random() >= chance:
        return None
    return rng.choice(SCROLL_KEYS)


def roll_loot(tier: int, rng: random.Random,
              scroll_bonus: float = 1.0) -> list[str]:
    """Item keys dropped by a completed contract of this tier."""
    rolls, table = LOOT_TABLES.get(tier, LOOT_TABLES[1])
    keys = [k for k, _ in table]
    weights = [w for _, w in table]
    drops = []
    for _ in range(rolls):
        pick = rng.choices(keys, weights=weights, k=1)[0]
        if pick is not None:
            drops.append(pick)

    scroll = roll_scroll(tier, rng, scroll_bonus)
    if scroll is not None:
        drops.append(scroll)

    gear = roll_gear(tier, rng)
    if gear is not None:
        drops.append(gear)
    return drops


def _normalise(text: str) -> str:
    return text.strip().lower().replace("-", "_").replace(" ", "_").replace("'", "")


def match_items(text: str, among: tuple[str, ...] | list[str]) -> list[Item]:
    """Candidates matching what the player typed, best-specificity first.

    Returns a list rather than one item because "potion" legitimately matches
    two things; the caller decides whether to act or ask which.
    """
    token = _normalise(text)
    if not token:
        return []

    if token.isdigit():
        idx = int(token) - 1
        return [ITEMS[among[idx]]] if 0 <= idx < len(among) else []

    exact = [ITEMS[k] for k in among
             if token == k or token == _normalise(ITEMS[k].name)]
    if exact:
        return exact

    prefix = [ITEMS[k] for k in among if _normalise(ITEMS[k].name).startswith(token)]
    if prefix:
        return prefix

    # Any whole word: "potion" -> "Lesser Healing Potion".
    return [ITEMS[k] for k in among
            if token in _normalise(ITEMS[k].name).split("_")]


def find_item(text: str, among: tuple[str, ...] | list[str]) -> Item | None:
    """One unambiguous match, or None."""
    matches = match_items(text, among)
    return matches[0] if len(matches) == 1 else None
