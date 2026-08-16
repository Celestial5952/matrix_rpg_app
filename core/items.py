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


def roll_scroll(tier: int, rng: random.Random) -> str | None:
    if not SCROLL_KEYS or rng.random() >= SCROLL_CHANCE.get(tier, 0.0):
        return None
    return rng.choice(SCROLL_KEYS)


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

    scroll = roll_scroll(tier, rng)
    if scroll is not None:
        drops.append(scroll)
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
