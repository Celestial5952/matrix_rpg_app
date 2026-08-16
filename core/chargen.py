"""Races, classes and ability kits.

Race shifts the numbers; class decides what you can actually *do*. That split is
deliberate — if race picked your abilities too, most race/class pairs would be
noise, and the board would need twice the content to stay interesting.

Baseline before modifiers is 34 HP / 8 power / 6 focus (see state.BASE_*).
"""

from __future__ import annotations

from .state import Ability, CharClass, Race

# ---------------------------------------------------------------------------
# races
# ---------------------------------------------------------------------------

RACES: tuple[Race, ...] = (
    Race(
        key="human",
        name="Human",
        blurb="Adaptable and quick to learn. No weaknesses, no spikes.",
    ),
    Race(
        key="elf",
        name="Elf",
        blurb="Long-lived and arcane-attuned. Frail, but the focus rarely runs dry.",
    ),
    Race(
        key="dwarf",
        name="Dwarf",
        blurb="Stone-stubborn. Soaks punishment that would fell anyone else.",
    ),
    Race(
        key="halfling",
        name="Halfling",
        blurb="Small, lucky, and harder to hit than the size suggests.",
    ),
    Race(
        key="half_orc",
        name="Half-Orc",
        blurb="Brutal and enduring. Subtlety is somebody else's problem.",
    ),
    Race(
        key="gnome",
        name="Gnome",
        blurb="Clever to a fault. Wins fights before the swinging starts.",
    ),
)

# ---------------------------------------------------------------------------
# abilities
#
# Slot order matters — it is the numbered menu the player sees, and every kit
# follows the same shape so muscle memory transfers between characters:
#   1 basic attack (free)  2 signature (costs focus)  3 defence  4 recovery
# ---------------------------------------------------------------------------

def _kit(basic: Ability, signature: Ability, defence: Ability, recovery: Ability):
    return (basic, signature, defence, recovery)


CLASSES: tuple[CharClass, ...] = (
    CharClass(
        key="fighter",
        name="Fighter",
        hp_mod=6, power_mod=2, focus_mod=-1,
        blurb="Front line. Big numbers, no tricks, hard to put down.",
        abilities=_kit(
            Ability("strike", "Strike", kind="attack", multiplier=1.0,
                    blurb="A clean swing."),
            Ability("cleave", "Cleave", kind="attack", cost=2, multiplier=1.7,
                    blurb="A committed two-handed blow."),
            Ability("shield_wall", "Shield Wall", kind="guard",
                    guard_reduction=0.3, focus_gain=2,
                    blurb="Set your feet. Very little gets through."),
            Ability("second_wind", "Second Wind", kind="heal", heal=14, uses=2,
                    blurb="Dig deep and keep going."),
        ),
    ),
    CharClass(
        key="wizard",
        name="Wizard",
        hp_mod=-3, power_mod=-1, focus_mod=4,
        blurb="Glass and fire. Ends fights fast or dies trying.",
        abilities=_kit(
            Ability("staff_jab", "Staff Jab", kind="attack", multiplier=0.9,
                    blurb="Not what you trained for."),
            Ability("fireball", "Fireball", kind="attack", cost=3, multiplier=2.6,
                    ignores_armor=True,
                    blurb="Armour is not a defence against being on fire."),
            Ability("frost_ward", "Frost Ward", kind="guard",
                    guard_reduction=0.45, focus_gain=3,
                    blurb="A rime shell. Buys a turn and refills the well."),
            Ability("draught", "Arcane Draught", kind="heal", heal=12, uses=2,
                    blurb="Tastes of pennies and regret."),
        ),
    ),
    CharClass(
        key="rogue",
        name="Rogue",
        hp_mod=0, power_mod=1, focus_mod=2,
        blurb="Patient. Punishes anything that telegraphs.",
        abilities=_kit(
            Ability("stab", "Stab", kind="attack", multiplier=1.05,
                    blurb="Quick and low."),
            Ability("backstab", "Backstab", kind="attack", cost=2, multiplier=2.0,
                    ignores_armor=True,
                    blurb="Find the gap in the plate."),
            Ability("dodge", "Dodge", kind="guard", guard_reduction=0.25,
                    focus_gain=1,
                    blurb="Not there any more."),
            Ability("bandage", "Bandage", kind="heal", heal=10, uses=3,
                    blurb="Rough field work, but it holds."),
        ),
    ),
    CharClass(
        key="cleric",
        name="Cleric",
        hp_mod=4, power_mod=0, focus_mod=2,
        blurb="Attrition incarnate. Outlasts what it cannot outhit.",
        abilities=_kit(
            Ability("mace", "Mace", kind="attack", multiplier=1.0,
                    blurb="Blunt and honest."),
            Ability("smite", "Smite", kind="attack", cost=3, multiplier=1.7,
                    ignores_armor=True,
                    blurb="Borrowed authority, delivered at speed."),
            Ability("sanctuary", "Sanctuary", kind="guard", guard_reduction=0.4,
                    focus_gain=2,
                    blurb="A held breath and a held ward."),
            Ability("lay_on_hands", "Lay on Hands", kind="heal", heal=20, uses=2,
                    blurb="Knits flesh. Hurts more than the wound did."),
        ),
    ),
    CharClass(
        key="ranger",
        name="Ranger",
        hp_mod=3, power_mod=1, focus_mod=1,
        blurb="Steady. Good at everything, best at nothing.",
        abilities=_kit(
            Ability("shortbow", "Shortbow", kind="attack", multiplier=1.05,
                    blurb="Nocked, drawn, loosed."),
            Ability("volley", "Volley", kind="attack", cost=2, multiplier=1.95,
                    blurb="Three arrows in the air at once."),
            Ability("evade", "Evade", kind="guard", guard_reduction=0.35,
                    focus_gain=2,
                    blurb="Give ground and make it cost."),
            Ability("salve", "Herbal Salve", kind="heal", heal=13, uses=2,
                    blurb="Field-picked, foul-smelling, effective."),
        ),
    ),
)

RACES_BY_KEY = {r.key: r for r in RACES}
CLASSES_BY_KEY = {c.key: c for c in CLASSES}


def find_race(text: str) -> Race | None:
    """Match a race by menu number, key, or name — players type all three."""
    return _match(text, RACES)


def find_class(text: str) -> CharClass | None:
    return _match(text, CLASSES)


def _match(text: str, table):
    token = text.strip().lower().replace("-", "_").replace(" ", "_")
    if token.isdigit():
        idx = int(token) - 1
        return table[idx] if 0 <= idx < len(table) else None
    for entry in table:
        if token == entry.key or token == entry.name.lower().replace("-", "_"):
            return entry
    return None
