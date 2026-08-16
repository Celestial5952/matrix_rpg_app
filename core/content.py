"""Monsters, quest templates, modifiers, and contract rolling.

This is the file that should grow to thousands of lines. Everything else in
core/ should stay roughly the size it is now.

The board never posts a Quest directly. It rolls a Contract: a template plus
varied stages and rewards, an opening line picked from several, and zero to two
modifiers that change how the fight actually plays. Two runs of "Rats in the
Cellar" should not feel like the same errand twice.
"""

from __future__ import annotations

import random

from .state import Contract, Modifier, Monster, MonsterMove, Quest

# ---------------------------------------------------------------------------
# monsters
# ---------------------------------------------------------------------------

MONSTERS: dict[str, Monster] = {
    "cave_rat": Monster(
        key="cave_rat", name="Cave Rat", max_hp=14, power=4, armor=0,
        moves=(
            MonsterMove("Bite", "The rat bares its teeth. All of them. Enthusiastically."),
            MonsterMove("Scurry", "The rat skitters off, deeply pleased with itself.", kind="guard"),
        ),
    ),
    "kobold": Monster(
        key="kobold", name="Kobold Skirmisher", max_hp=22, power=5, armor=1,
        moves=(
            MonsterMove("Jab", "The kobold shifts its grip and mutters something rude."),
            MonsterMove("Overhand Smash",
                        "The kobold raises the spear high in both hands. It is telegraphing. It does not care.",
                        kind="heavy", multiplier=1.8),
        ),
    ),
    "mire_toad": Monster(
        key="mire_toad", name="Mire Toad", max_hp=28, power=5, armor=2,
        moves=(
            MonsterMove("Tongue Lash", "The toad's throat swells like a wet balloon."),
            MonsterMove("Hunker", "The toad squats into the muck, smug as a duchess.", kind="guard"),
            MonsterMove("Body Slam", "The toad coils, legs bunching under it.",
                        kind="heavy", multiplier=1.7),
        ),
    ),
    "bandit": Monster(
        key="bandit", name="Bandit Cutthroat", max_hp=26, power=6, armor=1,
        moves=(
            MonsterMove("Slash", "The bandit circles, knife low, monologue loading."),
            MonsterMove("Backstab",
                        "The bandit feints left and drops out of your light.",
                        kind="heavy", multiplier=2.0),
        ),
    ),
    "wolf": Monster(
        key="wolf", name="Winter Wolf", max_hp=24, power=6, armor=0,
        moves=(
            MonsterMove("Snap", "The wolf paces, watching your feet, judging your boots."),
            MonsterMove("Lunge", "The wolf drops its shoulders and gathers itself.",
                        kind="heavy", multiplier=1.9),
        ),
    ),
    "brigand_captain": Monster(
        key="brigand_captain", name="Brigand Captain", max_hp=38, power=7, armor=3,
        moves=(
            MonsterMove("Sabre", "The captain salutes you with the tip of the blade. Show-off."),
            MonsterMove("Parry", "The captain settles into a guard.", kind="guard"),
            MonsterMove("Riposte", "The captain steps in, blade level with your eyes.",
                        kind="heavy", multiplier=2.0),
        ),
    ),
    "wight": Monster(
        key="wight", name="Barrow Wight", max_hp=34, power=6, armor=2,
        moves=(
            MonsterMove("Chill Touch",
                        "The wight extends one grey hand, almost politely.", kind="drain"),
            MonsterMove("Grave Wail",
                        "The wight's jaw unhinges. The air goes cold. Somebody screams — it is you.",
                        kind="heavy", multiplier=1.9),
            MonsterMove("Shroud", "The wight draws its tatters close.", kind="guard"),
        ),
    ),
    "revenant": Monster(
        key="revenant", name="Barrow Revenant", max_hp=52, power=8, armor=3,
        moves=(
            MonsterMove("Grasp", "The revenant turns its head towards you.",
                        kind="drain"),
            MonsterMove("Deathward", "Old wrappings knit tighter.", kind="guard"),
            MonsterMove("Sepulchre",
                        "The revenant raises both arms and the barrow answers.",
                        kind="heavy", multiplier=2.1),
        ),
    ),
    # --- adventure beasts ---------------------------------------------------
    # Tuned for the fixed sequences in adventures.py, not for the random board.
    "tomb_spider": Monster(
        key="tomb_spider", name="Tomb Spider", max_hp=30, power=7, armor=1,
        moves=(
            MonsterMove("Bite", "The spider tastes the air with a foreleg."),
            MonsterMove("Web", "The spider draws silk between the pillars.",
                        kind="guard"),
            MonsterMove("Pounce", "Every leg tenses at once.",
                        kind="heavy", multiplier=1.9),
        ),
    ),
    "grave_ooze": Monster(
        key="grave_ooze", name="Grave Ooze", max_hp=46, power=6, armor=0,
        moves=(
            MonsterMove("Engulf", "The ooze slides forward, unhurried.",
                        kind="drain"),
            MonsterMove("Congeal", "The ooze thickens until it shines.",
                        kind="guard"),
            MonsterMove("Break", "The ooze rears into a column above you.",
                        kind="heavy", multiplier=1.8),
        ),
    ),
    "stone_sentinel": Monster(
        key="stone_sentinel", name="Stone Sentinel", max_hp=42, power=7, armor=3,
        moves=(
            MonsterMove("Backhand", "The statue's head turns. Only the head."),
            MonsterMove("Brace", "The sentinel plants itself and waits.",
                        kind="guard"),
            MonsterMove("Hammerfall", "It raises both fists above its head.",
                        kind="heavy", multiplier=2.1),
        ),
    ),
    "bone_choir": Monster(
        key="bone_choir", name="Bone Choir", max_hp=40, power=8, armor=1,
        moves=(
            MonsterMove("Descant", "A dozen jaws open on the same note."),
            MonsterMove("Antiphon", "The singing doubles back on itself.",
                        kind="drain"),
            MonsterMove("Crescendo", "The note climbs past what bone should hold.",
                        kind="heavy", multiplier=2.0),
        ),
    ),
    "basilisk": Monster(
        key="basilisk", name="Basilisk", max_hp=48, power=8, armor=3,
        moves=(
            MonsterMove("Rake", "The basilisk drags one claw across the flagstones."),
            MonsterMove("Coil", "It folds itself behind its own plates.",
                        kind="guard"),
            MonsterMove("Regard", "The basilisk turns its head to look at you properly.",
                        kind="heavy", multiplier=2.2),
        ),
    ),
    "wyvern": Monster(
        key="wyvern", name="Cavern Wyvern", max_hp=52, power=9, armor=2,
        moves=(
            MonsterMove("Snap", "The wyvern shifts its wings for balance."),
            MonsterMove("Sting", "The tail comes up over the shoulder, dripping.",
                        kind="heavy", multiplier=2.1),
            MonsterMove("Mantle", "It pulls both wings across itself like a cloak.",
                        kind="guard"),
        ),
    ),
    "ziggurat_warden": Monster(
        key="ziggurat_warden", name="Warden of the Ziggurat", max_hp=58,
        power=9, armor=2,
        moves=(
            MonsterMove("Judgement", "The warden lifts its blade to the vertical."),
            MonsterMove("Sanctify", "Old wards flare along its arms.", kind="guard"),
            MonsterMove("Sentence", "It speaks a name. You are almost certain it is yours.",
                        kind="heavy", multiplier=2.3),
        ),
    ),
    "dread_wyrm": Monster(
        key="dread_wyrm", name="The Sleeper", max_hp=78, power=10, armor=2,
        moves=(
            MonsterMove("Claw", "Something vast adjusts its weight in the dark."),
            MonsterMove("Siphon", "The air pulls towards it, and so does your warmth.",
                        kind="drain"),
            MonsterMove("Coil", "Scales grind closed over the old wound.",
                        kind="guard"),
            MonsterMove("Ruin", "It inhales. The whole chamber leans in with it.",
                        kind="heavy", multiplier=2.4),
        ),
    ),
}

# ---------------------------------------------------------------------------
# quest templates
# ---------------------------------------------------------------------------

QUESTS: tuple[Quest, ...] = (
    Quest(
        key="cellar_rats", name="Rats in the Cellar", tier=1,
        flavor="The innkeeper says something's been at the barrels. Again.",
        flavors=(
            "The innkeeper says something's been at the barrels. Again.",
            "Two casks bled out overnight. The cellar door was bolted.",
            "It's rats. It's almost certainly rats. She'd like to be sure.",
        ),
        pool=("cave_rat",), stages=2, gold=12, renown=2,
    ),
    Quest(
        key="mill_kobolds", name="Kobolds at the Mill", tier=1,
        flavor="Three of them, maybe four. They took the miller's dog.",
        flavors=(
            "Three of them, maybe four. They took the miller's dog.",
            "The mill wheel's been jammed with something that used to bark.",
            "They've been throwing stones at the miller's boy. It escalated.",
        ),
        pool=("cave_rat", "kobold"), stages=3, gold=20, renown=4,
    ),
    Quest(
        key="winter_road", name="The Winter Road", tier=1,
        flavor="The carters won't run it any more. Wolves, they say.",
        flavors=(
            "The carters won't run it any more. Wolves, they say.",
            "Something has been following the mail rider. Only following.",
            "Four sheep gone, and the tracks come back to the road each time.",
        ),
        pool=("wolf", "cave_rat"), stages=3, gold=22, renown=4,
    ),
    Quest(
        key="mire_road", name="The Mire Road", tier=2,
        flavor="The causeway is out and the toads have gotten bold.",
        flavors=(
            "The causeway is out and the toads have gotten bold.",
            "Three days of rain and the fen has moved closer to the village.",
            "The ferryman won't say what he saw. He will not go back.",
        ),
        pool=("mire_toad", "cave_rat"), stages=3, gold=34, renown=7,
    ),
    Quest(
        key="bandit_toll", name="The Bandit Toll", tier=2,
        flavor="They've been charging silver at the ford. It stops now.",
        flavors=(
            "They've been charging silver at the ford. It stops now.",
            "A toll gate nobody built, manned by men nobody hired.",
            "The last collector came back without his horse or his thumbs.",
        ),
        pool=("bandit", "kobold"), stages=3, gold=40, renown=8,
    ),
    Quest(
        key="captains_camp", name="The Captain's Camp", tier=2,
        flavor="They have a leader now, and a banner. That's worse.",
        flavors=(
            "They have a leader now, and a banner. That's worse.",
            "Someone has taught them to post sentries.",
            "The camp has a cook fire and a gallows. Only one is in use.",
        ),
        pool=("bandit", "wolf", "brigand_captain"), stages=3, gold=48, renown=10,
    ),
    Quest(
        key="barrow_door", name="The Barrow Door", tier=3,
        flavor="It was sealed for a reason. It is not sealed now.",
        flavors=(
            "It was sealed for a reason. It is not sealed now.",
            "The seal is intact. The door is open. Both are true.",
            "Shepherds won't graze the hill. Their grandfathers wouldn't either.",
        ),
        pool=("bandit", "mire_toad", "wight"), stages=4, gold=90, renown=18,
    ),
    Quest(
        key="deep_barrow", name="The Deep Barrow", tier=3,
        flavor="The first chamber was the shallow one.",
        flavors=(
            "The first chamber was the shallow one.",
            "There is a second stair. Nobody surveyed a second stair.",
            "Whatever was buried here was buried carefully, and deep.",
        ),
        pool=("wight", "revenant", "mire_toad"), stages=4, gold=110, renown=22,
    ),

    # --- story ---------------------------------------------------------------
    # Gated on renown and offered by chance rather than by rank. This is the
    # hook the campaign hangs off: add a Quest with story=True and a
    # min_renown, and it will start appearing once a character is established.
    Quest(
        key="the_sealed_name", name="The Sealed Name", tier=3,
        flavor="A guild courier brings a letter with no sender and your name on it.",
        flavors=(
            "A guild courier brings a letter with no sender and your name on it.",
            "The letter has been in the guild's keeping for longer than you have.",
        ),
        pool=("revenant", "wight"), stages=3, gold=140, renown=30,
        story=True, min_renown=30,
    ),
)


QUESTS_BY_KEY = {q.key: q for q in QUESTS}


def quests_for_rank(rank: int) -> list[Quest]:
    """Ordinary contracts a character of this guild rank may be offered."""
    return [q for q in QUESTS if not q.story and q.tier <= rank]


def story_quests(renown: int) -> list[Quest]:
    """Story contracts unlocked by renown, regardless of rank."""
    return [q for q in QUESTS if q.story and renown >= q.min_renown]


# ---------------------------------------------------------------------------
# modifiers
# ---------------------------------------------------------------------------

MODIFIERS: tuple[Modifier, ...] = (
    Modifier("swarming", "Swarming",
             "More of them than the client admitted.",
             stages_delta=1, gold_mult=1.25, renown_mult=1.15),
    Modifier("fortified", "Fortified",
             "They have dug in and found armour somewhere.",
             monster_armor_delta=4, gold_mult=1.3),
    Modifier("savage", "Savage",
             "Something has made them bold and mean.",
             monster_power_mult=1.15, renown_mult=1.25),
    Modifier("teeming", "Teeming",
             "Fat, well fed, and hard to put down.",
             monster_hp_mult=1.35, gold_mult=1.25),
    Modifier("urgent", "Urgent",
             "It has to be tonight. The guild is paying for the hurry.",
             stages_delta=-1, gold_mult=1.5),
    Modifier("bountiful", "Bountiful",
             "Whatever they've been stealing, they still have it.",
             extra_loot=1),
    Modifier("thankless", "Thankless",
             "No coin in it. The guild will remember that you went.",
             gold_mult=0.6, renown_mult=1.8),
)

MODIFIERS_BY_KEY = {m.key: m for m in MODIFIERS}

# Chance of rolling 0, 1 or 2 modifiers, by tier. Higher tiers are messier.
_MODIFIER_WEIGHTS: dict[int, tuple[int, int, int]] = {
    1: (60, 35, 5),
    2: (40, 45, 15),
    3: (25, 50, 25),
}

# Odds a story contract appears on a board that could carry one.
STORY_CHANCE = 0.25


def roll_contract(quest: Quest, rng: random.Random) -> Contract:
    """Turn a template into a posted contract."""
    counts = _MODIFIER_WEIGHTS.get(quest.tier, _MODIFIER_WEIGHTS[3])
    how_many = rng.choices((0, 1, 2), weights=counts, k=1)[0]
    modifiers = tuple(rng.sample(MODIFIERS, how_many)) if how_many else ()

    stages = quest.stages + sum(m.stages_delta for m in modifiers)
    # Reward variance is applied before modifiers so a "×1.5 gold" contract
    # still reads as meaningfully richer than a lucky ordinary one.
    gold = round(quest.gold * rng.uniform(0.85, 1.15))
    renown = round(quest.renown * rng.uniform(0.9, 1.1))
    for m in modifiers:
        gold = round(gold * m.gold_mult)
        renown = round(renown * m.renown_mult)

    return Contract(
        quest=quest,
        name=quest.name,
        tier=quest.tier,
        flavor=rng.choice(quest.flavors) if quest.flavors else quest.flavor,
        pool=quest.pool,
        stages=max(1, stages),
        gold=max(1, gold),
        renown=max(1, renown),
        modifiers=modifiers,
        story=quest.story,
    )


def plain_contract(quest: Quest) -> Contract:
    """An unrolled contract — exact template numbers, no modifiers.

    For tests and the balance harness, where rolled variance would just add
    noise to the thing being measured.
    """
    return Contract(
        quest=quest, name=quest.name, tier=quest.tier, flavor=quest.flavor,
        pool=quest.pool, stages=quest.stages, gold=quest.gold,
        renown=quest.renown, story=quest.story,
    )


def scaled_monster(monster: Monster, contract: Contract | None) -> Monster:
    """Apply a contract's modifiers to a monster's stats."""
    if contract is None or not contract.modifiers:
        return monster
    from dataclasses import replace
    return replace(
        monster,
        max_hp=max(1, round(monster.max_hp * contract.monster_hp_mult)),
        power=max(1, round(monster.power * contract.monster_power_mult)),
        armor=max(0, monster.armor + contract.monster_armor_delta),
    )
