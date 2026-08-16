"""Consumables, the shop, and loot tables.

Everything here is single-use on purpose. Permanent equipment would compete
with class identity for the same design space, and with permadeath it would
mean a character's power came mostly from how long it had been lucky. Spend it
or lose it is the whole economy.

Like content.py, this file is meant to grow. Nothing else should need editing
to add an item.
"""

from __future__ import annotations

import random

from .state import Item

ITEMS: dict[str, Item] = {
    "lesser_potion": Item(
        key="lesser_potion",
        name="Lesser Healing Potion",
        kind="heal",
        price=12,
        heal=18,
        blurb="Cheap, bitter, reliable.",
    ),
    "greater_potion": Item(
        key="greater_potion",
        name="Greater Healing Potion",
        kind="heal",
        price=32,
        heal=34,
        blurb="The good stuff. Tastes of copper and lilies.",
    ),
    "focus_draught": Item(
        key="focus_draught",
        name="Focus Draught",
        kind="focus",
        price=14,
        focus=5,
        blurb="Clears the head. Briefly.",
    ),
    "alchemists_fire": Item(
        key="alchemists_fire",
        name="Alchemist's Fire",
        kind="damage",
        price=22,
        damage=24,
        ignores_armor=True,
        blurb="A sealed flask you throw and then stop holding.",
    ),
    "whetstone": Item(
        key="whetstone",
        name="Whetstone",
        kind="buff",
        price=16,
        attack_bonus=0.6,
        blurb="One pass down the edge. Your next hit remembers it.",
    ),
}

# What the guild hall stocks, in display order.
SHOP_STOCK: tuple[str, ...] = (
    "lesser_potion",
    "greater_potion",
    "focus_draught",
    "alchemists_fire",
    "whetstone",
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


def roll_loot(tier: int, rng: random.Random) -> list[str]:
    """Item keys dropped by a completed contract of this tier."""
    rolls, table = LOOT_TABLES.get(tier, LOOT_TABLES[1])
    keys = [k for k, _ in table]
    weights = [w for _, w in table]
    drops = []
    for _ in range(rolls):
        pick = rng.choices(keys, weights=weights, k=1)[0]
        if pick is not None:
            drops.append(pick)
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
