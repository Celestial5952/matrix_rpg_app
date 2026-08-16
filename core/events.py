"""Choice events — the parts of a contract that are not a fight.

Every encounter so far has been something trying to kill you. These are the
bits in between: a decision with consequences that may hurt, heal, pay, or
hand you something. They are the cheapest content in the game per line written
and the best place for the guild's voice.

Adding one is a data edit:

    Event(
        key="your_key",
        prompt="Two or three sentences. What you are looking at.",
        choices=(
            Choice("What the player does", (
                Outcome("What happens.", weight=3, gold=15),
                Outcome("What else might happen.", weight=1, hp=-8),
            )),
            ...
        ),
        tiers=(1, 2),          # which contract tiers may roll it
    )

Design rules learned writing the first set:

- **Every choice must be able to go badly and able to go well.** A choice with
  one guaranteed outcome is not a choice, it is a delay with prose attached.
- **No option is strictly dominant.** If one is always right, the others are
  decoration.
- **Walking away is always available and never free of regret** — it is the
  safe option, not the correct one.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Outcome:
    text: str
    weight: int = 1
    hp: int = 0                       # negative hurts, positive heals
    focus: int = 0
    gold: int = 0
    items: tuple[str, ...] = ()


@dataclass(frozen=True)
class Choice:
    label: str
    outcomes: tuple[Outcome, ...]


@dataclass(frozen=True)
class Event:
    key: str
    prompt: str
    choices: tuple[Choice, ...]
    tiers: tuple[int, ...] = (1, 2, 3, 4)


EVENTS: tuple[Event, ...] = (
    Event(
        key="wayside_shrine",
        prompt=(
            "🕯️ **A wayside shrine**\n"
            "_Someone has kept the candles lit out here, miles from anywhere. "
            "There are coins in the bowl. There is nobody watching._"
        ),
        choices=(
            Choice("Leave a coin", (
                Outcome("_You leave a coin. The candles gutter, then stand up "
                        "straight. You feel oddly well._", weight=3,
                        gold=-8, hp=14),
                Outcome("_You leave a coin. Nothing happens. It was a bowl._",
                        weight=2, gold=-8),
            )),
            Choice("Take the coins", (
                Outcome("_You pocket the lot. Nobody stops you. You spend the "
                        "next mile listening very hard._", weight=3, gold=22),
                Outcome("_You pocket the lot and immediately walk into a "
                        "low branch you would swear was not there._",
                        weight=2, gold=22, hp=-9),
            )),
            Choice("Walk on", (
                Outcome("_You walk on. The candles are still burning when you "
                        "look back, which is a long way to look._", weight=1),
            )),
        ),
    ),
    Event(
        key="hedge_witch",
        prompt=(
            "🌿 **A hedge witch**\n"
            "_An old woman is sorting mushrooms into two piles by the path. "
            "She does not look up._ 'One pile's supper,' she says. 'Want to "
            "guess which?'"
        ),
        choices=(
            Choice("Take the left pile", (
                Outcome("_Supper. Genuinely excellent supper._", weight=2, hp=16),
                Outcome("_Not supper. You are unwell for an hour and she "
                        "watches with interest._", weight=2, hp=-12),
            )),
            Choice("Buy something instead", (
                Outcome("_She sells you a small bottle without saying what it "
                        "is. It is a good one._", weight=3,
                        gold=-14, items=("greater_potion",)),
                Outcome("_She sells you a small bottle without saying what it "
                        "is. It is not a good one, but it is something._",
                        weight=2, gold=-14, items=("lesser_potion",)),
            )),
            Choice("Decline politely", (
                Outcome("_'Sensible,' she says, sounding disappointed._",
                        weight=1),
            )),
        ),
    ),
    Event(
        key="collapsed_cart",
        prompt=(
            "🛞 **A collapsed cart**\n"
            "_A trader's cart has gone into the ditch, axle-deep. The trader "
            "is still trying to lift it, alone, and has clearly been at it a "
            "while._"
        ),
        choices=(
            Choice("Help lift it", (
                Outcome("_It takes an hour and most of your back. He pays what "
                        "he can, which is more than he can afford._",
                        weight=3, gold=26, hp=-6),
                Outcome("_The axle goes completely. He thanks you anyway, "
                        "which somehow makes it worse._", weight=2, hp=-6),
            )),
            Choice("Take a look in the cart", (
                Outcome("_While he heaves, you appraise the load and quietly "
                        "improve your own._", weight=3,
                        items=("alchemists_fire",)),
                Outcome("_He catches you at it. There is no fight, which is "
                        "worse — he just stops asking for help._",
                        weight=2, gold=-10),
            )),
            Choice("Walk on", (
                Outcome("_You walk on. He is still lifting when you round the "
                        "bend._", weight=1),
            )),
        ),
    ),
    Event(
        key="old_soldier",
        prompt=(
            "🍺 **An old soldier**\n"
            "_He has a fire, a bottle, and the look of somebody who has been "
            "waiting all day for a stranger to talk at._"
        ),
        choices=(
            Choice("Share the bottle", (
                Outcome("_You share it. He talks until the fire dies, and by "
                        "morning you feel human again._", weight=3, hp=18),
                Outcome("_You share it. He talks until the fire dies. You "
                        "regret every part of this._", weight=2, hp=-5, focus=-2),
            )),
            Choice("Ask what he knows", (
                Outcome("_He tells you exactly what is up the road. It is "
                        "useful, and you go in clear-headed._", weight=3, focus=4),
                Outcome("_He tells you about a war nobody else remembers. It "
                        "is not useful. It is very long._", weight=2),
            )),
            Choice("Keep moving", (
                Outcome("_'Suit yourself,' he says, to the fire._", weight=1),
            )),
        ),
    ),
    Event(
        key="hanged_mans_purse",
        prompt=(
            "🪢 **A body at the crossroads**\n"
            "_Somebody has been hanging here a while, in guild colours you "
            "half recognise. There is still a purse on the belt, and a "
            "perfectly good blade._"
        ),
        tiers=(2, 3, 4),
        choices=(
            Choice("Cut them down and bury them", (
                Outcome("_It takes the afternoon. You find the guild badge and "
                        "pocket it to hand in. It feels like the right kind of "
                        "tired._", weight=3, hp=-4, gold=18),
                Outcome("_It takes the afternoon and most of your strength, "
                        "and nobody will ever know you did it._",
                        weight=2, hp=-10),
            )),
            Choice("Take the purse and the blade", (
                Outcome("_They have no further use for either. You tell "
                        "yourself this twice._", weight=3, gold=40,
                        items=("whetstone",)),
                Outcome("_The purse is full of stones. Somebody has been here "
                        "before you and had the same conversation with "
                        "themselves._", weight=2),
            )),
            Choice("Leave it exactly as it is", (
                Outcome("_You leave it. It is somebody's job to come for them, "
                        "and it is not yours._", weight=1),
            )),
        ),
    ),
    Event(
        key="fairy_ring",
        prompt=(
            "🍄 **A ring of mushrooms**\n"
            "_Perfectly circular, in the middle of a path that goes around it "
            "rather than through. Everyone who made this path decided the same "
            "thing._"
        ),
        tiers=(2, 3, 4),
        choices=(
            Choice("Step inside it", (
                Outcome("_You step in. You step out. It is four hours later "
                        "and you feel wonderful and have no idea why._",
                        weight=2, hp=22, focus=3),
                Outcome("_You step in. You step out. Something in your pocket "
                        "is missing and you cannot remember what it was._",
                        weight=3, gold=-25),
            )),
            Choice("Go around, like everyone else", (
                Outcome("_You go around. The path is a path for a reason._",
                        weight=1),
            )),
        ),
    ),
)

EVENTS_BY_KEY = {e.key: e for e in EVENTS}

# How often a contract offers one, between encounters.
EVENT_CHANCE = 0.35


def roll_event(tier: int, rng: random.Random) -> Event | None:
    if rng.random() >= EVENT_CHANCE:
        return None
    candidates = [e for e in EVENTS if tier in e.tiers]
    return rng.choice(candidates) if candidates else None


def resolve(choice: Choice, rng: random.Random) -> Outcome:
    return rng.choices(choice.outcomes,
                       weights=[o.weight for o in choice.outcomes], k=1)[0]
