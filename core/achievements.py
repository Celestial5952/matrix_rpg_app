"""Achievements and titles. Account-level, cosmetic, and permanent.

A character's power must never outlive the character, or permadeath stops
meaning anything — so nothing here changes a single number in a fight. What it
remembers is the *player*: the contracts they have cleared across every
character, the number of times they have died, how often they have run for the
portal. Unlocks grant a title to wear beside your name, and nothing else.

Progress is counted by `bump()` at the handful of moments that matter; unlocks
are decided in one place, `sweep()`, which the entry point runs after every
command. Adding an achievement is one table row.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .state import Player


@dataclass(frozen=True)
class Achievement:
    key: str
    name: str
    hint: str      # what you did, in plain words — shown whether or not it's earned
    title: str     # worn after your name, e.g. "Doc the Seasoned"
    check: Callable[[Player], bool]


def bump(player: Player, stat: str, amount: int = 1) -> None:
    player.stats[stat] = player.stats.get(stat, 0) + amount


def stat(player: Player, key: str) -> int:
    return player.stats.get(key, 0)


def _at_least(key: str, n: int) -> Callable[[Player], bool]:
    return lambda p: stat(p, key) >= n


def _deaths(n: int) -> Callable[[Player], bool]:
    return lambda p: p.deaths >= n


def _level(n: int) -> Callable[[Player], bool]:
    return lambda p: p.character is not None and p.character.level >= n


def _wealthy(p: Player) -> bool:
    return p.character is not None and p.character.gold >= 500


def _fully_dressed(p: Player) -> bool:
    from .items import GEAR_SLOTS
    char = p.character
    return char is not None and all(char.equipment.get(s) for s in GEAR_SLOTS)


ACHIEVEMENTS: tuple[Achievement, ...] = (
    Achievement("first_contract", "Paperwork Filed",
                "Clear your first contract.", "the Newly Inked",
                _at_least("contracts", 1)),
    Achievement("seasoned", "Seasoned Hand",
                "Clear 10 contracts.", "the Seasoned",
                _at_least("contracts", 10)),
    Achievement("contract_machine", "Contract Machine",
                "Clear 50 contracts.", "the Tireless",
                _at_least("contracts", 50)),
    Achievement("into_the_deep", "Into the Deep",
                "Clear a tier 3 contract.", "Deepwalker",
                _at_least("tier3", 1)),
    Achievement("deep_delver", "Deep Delver",
                "Clear 10 tier 3 contracts.", "the Unflinching",
                _at_least("tier3", 10)),
    Achievement("storyteller", "Storyteller",
                "Finish an adventure.", "the Storied",
                _at_least("adventures", 1)),
    Achievement("hairs_breadth", "Hair's Breadth",
                "Finish a contract on 3 HP or fewer.", "the Lucky",
                _at_least("close_calls", 1)),
    Achievement("yard_champion", "Yard Champion",
                "Win a duel.", "the Bruiser",
                _at_least("duels_won", 1)),
    Achievement("undefeated_pest", "Undefeated Pest",
                "Win 5 duels.", "the Insufferable",
                _at_least("duels_won", 5)),
    Achievement("frequent_flyer", "Frequent Flyer",
                "Take the town portal 5 times.", "the Flighty",
                _at_least("portals", 5)),
    Achievement("old_friend", "Old Friend of the Graveyard",
                "Lose 3 characters.", "the Reincarnated",
                _deaths(3)),
    Achievement("rising_star", "Rising Star",
                "Reach level 5.", "the Rising",
                _level(5)),
    Achievement("dragon_on_a_budget", "Dragon on a Budget",
                "Hold 500 gold at once.", "Goldbeard",
                _wealthy),
    Achievement("dressed_for_it", "Dressed for It",
                "Wear a weapon, armour and a charm together.", "the Well-Dressed",
                _fully_dressed),
)

ACHIEVEMENTS_BY_KEY: dict[str, Achievement] = {a.key: a for a in ACHIEVEMENTS}


def sweep(player: Player, now: float | None = None) -> list[str]:
    """Grant anything newly earned and say so. Cheap; safe to run every command."""
    stamp = time.time() if now is None else now
    lines: list[str] = []
    for ach in ACHIEVEMENTS:
        if ach.key in player.achievements or not ach.check(player):
            continue
        player.achievements[ach.key] = stamp
        lines.append(f"🏆 **Achievement unlocked: {ach.name}** — {ach.hint} "
                     f"_(title: {ach.title})_")
    if lines:
        lines.append("`!titles` to wear one.")
    return lines


def title_of(player: Player) -> str:
    """The title currently worn, or '' if none (or it is no longer earned)."""
    key = player.title_key
    if key and key in player.achievements and key in ACHIEVEMENTS_BY_KEY:
        return ACHIEVEMENTS_BY_KEY[key].title
    return ""


def find(text: str) -> Achievement | None:
    """Match by 1-based list position, key, or name/title prefix."""
    token = text.strip().lower()
    if not token:
        return None
    if token.isdigit():
        idx = int(token) - 1
        return ACHIEVEMENTS[idx] if 0 <= idx < len(ACHIEVEMENTS) else None
    for ach in ACHIEVEMENTS:
        if token in (ach.key, ach.name.lower(), ach.title.lower()):
            return ach
    hits = [a for a in ACHIEVEMENTS
            if a.name.lower().startswith(token) or a.title.lower().startswith(token)]
    return hits[0] if len(hits) == 1 else None
