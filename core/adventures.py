"""Authored adventures — the long-form content, delivered by scroll.

An adventure is a fixed sequence of encounters with story between them, opened
by a scroll that drops from ordinary board work. Unlike a contract, nothing
here is rolled: the monsters, their order, and the prose are all authored.

Adding one is a data edit and nothing else:

    ADVENTURES["your_key"] = Adventure(
        key="your_key",
        title="The Something Something",
        scroll_name="Scroll of the Something",   # the item players find
        scroll_blurb="What it feels like to hold.",
        min_level=4,                              # gate; refuses below this
        intro="Two or three sentences. Where they are, why it matters.",
        chapters=(
            Chapter(
                beat="Story shown before the fight.",
                monster="tomb_spider",            # a key from content.MONSTERS
                aftermath="Story shown after winning it.",
                rest=8,                           # optional HP back first
                modifiers=(),                     # optional, from content
            ),
            ...
        ),
        epilogue="How it ends.",
        gold=400, renown=60,
        rewards=("greater_potion", "greater_potion"),
    )

Then add the scroll to items.ITEMS and to a loot table. `tools/adventure.py`
plays one headlessly if you want to check the difficulty curve.

Pacing notes, learned from the first one:

- Ten encounters on a single health bar with no pacing is arithmetic, not
  difficulty. `rest` beats exist so the curve has shape.
- The player has one recovery ability with two or three uses for the whole
  descent. Consumables are the real budget, which is what makes finding a
  scroll and *then* shopping feel like preparation.
"""

from __future__ import annotations

from .content import MODIFIERS_BY_KEY
from .state import Adventure, Chapter, Contract, Quest

_FORTIFIED = MODIFIERS_BY_KEY["fortified"]
_SAVAGE = MODIFIERS_BY_KEY["savage"]
_TEEMING = MODIFIERS_BY_KEY["teeming"]


SUNLESS_ZIGGURAT = Adventure(
    key="sunless_ziggurat",
    title="The Sunless Ziggurat",
    scroll_name="Scroll of the Sunless Stair",
    scroll_blurb="Warm to the touch, and it should not be.",
    min_level=3,
    intro=(
        "The scroll burns cold in your hand and then you are elsewhere.\n\n"
        "_A step pyramid, buried point-down, its summit somewhere far below "
        "you in the dark. The stair spirals inward. Someone has swept it "
        "recently — you can see the broom-marks in the dust, and no broom, "
        "and no one._"
    ),
    chapters=(
        Chapter(
            beat=(
                "**I — The Antechamber**\n"
                "_Offerings line the walls: bowls of grain gone to powder, "
                "coins fused into lumps. Something has strung the doorway "
                "at head height._"
            ),
            monster="tomb_spider",
            aftermath="_You cut the silk down. There is a great deal of it, "
                      "and it goes all the way down the stair._",
        ),
        Chapter(
            beat=(
                "**II — The Singing Gallery**\n"
                "_Alcoves, hundreds of them, each one holding a skull turned "
                "to face the stair. As you pass, they begin — softly, and "
                "then not softly — to sing._"
            ),
            monster="bone_choir",
            aftermath="_The singing stops mid-phrase. The unbroken skulls "
                      "keep the note going. They are not finished with it._",
        ),
        Chapter(
            beat=(
                "**III — The Flooded Landing**\n"
                "_Black water to the knee, and warm. Something in it has been "
                "digesting the offerings for a very long time and has begun, "
                "recently, on the priests._"
            ),
            monster="grave_ooze",
            aftermath="_It comes apart reluctantly. In the residue: a signet "
                      "ring, a knuckle bone, and half a map of this place._",
            rest=10,
        ),
        Chapter(
            beat=(
                "**IV — The Warden's Landing**\n"
                "_A statue stands at the turn of the stair with its back to "
                "you, which is worse than facing you. The map calls this "
                "level THE POLITE REQUEST._"
            ),
            monster="stone_sentinel",
            aftermath="_The rubble settles. Below, something enormous shifts "
                      "in its sleep, and the whole ziggurat leans with it._",
        ),
        Chapter(
            beat=(
                "**V — The Brood Terrace**\n"
                "_The silk from the antechamber ends here, in a nursery. The "
                "mother is larger than the doorway she came through, which "
                "raises a question you decide not to pursue._"
            ),
            monster="tomb_spider",
            modifiers=(_TEEMING,),
            aftermath="_You do not look closely at the egg sacs. You are "
                      "quite proud of this._",
        ),
        Chapter(
            beat=(
                "**VI — The Garden of Attendants**\n"
                "_Forty stone figures kneel facing inward, superbly detailed. "
                "One of them is holding a lantern that is still lit. They "
                "were not carved._"
            ),
            monster="basilisk",
            aftermath="_You take the lantern. The hand does not want to let "
                      "go, and then does._",
            rest=8,
        ),
        Chapter(
            beat=(
                "**VII — The Open Shaft**\n"
                "_The stair gives out onto nothing: a shaft dropping past "
                "eleven more levels into black. Something is circling in it, "
                "and has been for centuries, and is delighted to see you._"
            ),
            monster="wyvern",
            aftermath="_It falls a long way. You do not hear it land._",
        ),
        Chapter(
            beat=(
                "**VIII — The Reprise**\n"
                "_The singing again, below you now — the same phrase, finally "
                "finished. Every skull you left intact has come down the "
                "stair to hear how it ends._"
            ),
            monster="bone_choir",
            modifiers=(_SAVAGE,),
            aftermath="_Silence. Genuine silence, for the first time since "
                      "you arrived. You find you liked the singing better._",
            rest=12,
        ),
        Chapter(
            beat=(
                "**IX — The Seal**\n"
                "_A door of banded iron, and before it the last of the order "
                "that built this place: armoured, patient, and still on duty "
                "eight hundred years after the last of its officers died._\n\n"
                "_'It sleeps,' it says. 'You will let it sleep.'_"
            ),
            monster="ziggurat_warden",
            rest=16,
            aftermath="_'Please,' it says, going down. It is the only word it "
                      "has said that was not an order._",
        ),
        Chapter(
            beat=(
                "**X — The Sleeper**\n"
                "_The chamber is the size of a town square and every inch of "
                "it is occupied. It has been fed on this ziggurat's dead for "
                "centuries — every offering, every priest, every one of the "
                "forty kneeling attendants._\n\n"
                "_It opens one eye. It is not surprised. It has watched you "
                "come down all ten levels._"
            ),
            monster="dread_wyrm",
            rest=20,
            aftermath="_It goes still. Somewhere far above, a broom stops "
                      "sweeping._",
        ),
    ),
    epilogue=(
        "🏆 **THE SUNLESS ZIGGURAT IS SILENT**\n\n"
        "_You climb out through eleven levels of your own handiwork. At the "
        "top, the offering bowls have been refilled with fresh grain._\n\n"
        "_The clerk takes one look at you, puts down her quill, and pours you "
        "something from the bottle she does not offer to anybody._"
    ),
    gold=450,
    renown=70,
    rewards=("greater_potion", "greater_potion", "alchemists_fire",
             "whetstone", "summoning_horn"),
)


ADVENTURES: dict[str, Adventure] = {
    SUNLESS_ZIGGURAT.key: SUNLESS_ZIGGURAT,
}


def scroll_for(adventure_key: str) -> str:
    """The item key of the scroll that opens this adventure."""
    return f"scroll_{adventure_key}"


# Adventures ride the ordinary Contract machinery so combat, persistence and
# rendering need no special cases -- the only difference is that encounters
# come from `chapters` in order instead of being drawn from `pool`.
ADVENTURE_TIER = 4


def contract_for(adventure: Adventure) -> Contract:
    quest = Quest(
        key=adventure.key,
        name=adventure.title,
        tier=ADVENTURE_TIER,
        flavor=adventure.intro,
        pool=tuple(c.monster for c in adventure.chapters),
        stages=adventure.length,
        gold=adventure.gold,
        renown=adventure.renown,
        story=True,
    )
    return Contract(
        quest=quest,
        name=adventure.title,
        tier=ADVENTURE_TIER,
        flavor=adventure.intro,
        pool=quest.pool,
        stages=adventure.length,
        gold=adventure.gold,
        renown=adventure.renown,
        story=True,
        chapters=adventure.chapters,
        adventure_key=adventure.key,
    )
