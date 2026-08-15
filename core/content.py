"""Monster and quest tables.

This is the file that should grow to thousands of lines. Everything else in
core/ should stay roughly the size it is now.
"""

from __future__ import annotations

from .state import Monster, MonsterMove, Quest

MONSTERS: dict[str, Monster] = {
    "cave_rat": Monster(
        key="cave_rat",
        name="Cave Rat",
        max_hp=14,
        power=4,
        armor=0,
        moves=(
            MonsterMove("Bite", "The rat bares its teeth."),
            MonsterMove("Scurry", "The rat skitters out of reach.", kind="guard"),
        ),
    ),
    "kobold": Monster(
        key="kobold",
        name="Kobold Skirmisher",
        max_hp=22,
        power=5,
        armor=1,
        moves=(
            MonsterMove("Jab", "The kobold shifts its grip on the spear."),
            MonsterMove(
                "Overhand Smash",
                "The kobold raises the spear high in both hands.",
                kind="heavy",
                multiplier=1.8,
            ),
        ),
    ),
    "mire_toad": Monster(
        key="mire_toad",
        name="Mire Toad",
        max_hp=28,
        power=5,
        armor=2,
        moves=(
            MonsterMove("Tongue Lash", "The toad's throat swells."),
            MonsterMove("Hunker", "The toad squats low in the muck.", kind="guard"),
            MonsterMove(
                "Body Slam",
                "The toad coils, legs bunching under it.",
                kind="heavy",
                multiplier=1.7,
            ),
        ),
    ),
    "bandit": Monster(
        key="bandit",
        name="Bandit Cutthroat",
        max_hp=26,
        power=6,
        armor=1,
        moves=(
            MonsterMove("Slash", "The bandit circles, knife low."),
            MonsterMove(
                "Backstab",
                "The bandit feints left and drops out of your light.",
                kind="heavy",
                multiplier=2.0,
            ),
        ),
    ),
    "wight": Monster(
        key="wight",
        name="Barrow Wight",
        max_hp=34,
        power=6,
        armor=2,
        moves=(
            MonsterMove(
                "Chill Touch",
                "The wight reaches out with one grey hand.",
                kind="drain",
            ),
            MonsterMove(
                "Grave Wail",
                "The wight's jaw unhinges and the air goes cold.",
                kind="heavy",
                multiplier=1.9,
            ),
            MonsterMove("Shroud", "The wight draws its tatters close.", kind="guard"),
        ),
    ),
}

QUESTS: tuple[Quest, ...] = (
    Quest(
        key="cellar_rats",
        name="Rats in the Cellar",
        tier=1,
        flavor="The innkeeper says something's been at the barrels. Again.",
        pool=("cave_rat",),
        stages=2,
        gold=12,
        renown=2,
    ),
    Quest(
        key="mill_kobolds",
        name="Kobolds at the Mill",
        tier=1,
        flavor="Three of them, maybe four. They took the miller's dog.",
        pool=("cave_rat", "kobold"),
        stages=3,
        gold=20,
        renown=4,
    ),
    Quest(
        key="mire_road",
        name="The Mire Road",
        tier=2,
        flavor="The causeway is out and the toads have gotten bold.",
        pool=("mire_toad", "cave_rat"),
        stages=3,
        gold=34,
        renown=7,
    ),
    Quest(
        key="bandit_toll",
        name="The Bandit Toll",
        tier=2,
        flavor="They've been charging silver at the ford. It stops now.",
        pool=("bandit", "kobold"),
        stages=3,
        gold=40,
        renown=8,
    ),
    Quest(
        key="barrow_door",
        name="The Barrow Door",
        tier=3,
        flavor="It was sealed for a reason. It is not sealed now.",
        pool=("bandit", "mire_toad", "wight"),
        stages=4,
        gold=90,
        renown=18,
    ),
)


def quests_for_rank(rank: int) -> list[Quest]:
    """Quests a player of this guild rank is allowed to see."""
    return [q for q in QUESTS if q.tier <= rank]
