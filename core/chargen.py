"""Races, classes, ability pools, and levelling.

Race is flavour only — see state.Race for why. Class owns mechanical identity:
base stats and the pool of abilities you draw a loadout from.

Every kit is four typed slots:

    basic      free attack, always usable
    signature  costs focus, the class's real damage
    defence    guard, buys a turn
    recovery   limited-use healing

Slots are typed rather than free-form so a player cannot equip four signatures
and soft-lock a fight the moment they run out of focus. You choose *which*
basic, not *whether* you have one.

Levelling runs off renown, so there is one currency instead of two. Higher
levels raise stats a little and unlock more options per slot — the choice
widens faster than the numbers grow.
"""

from __future__ import annotations

from .state import Ability, CharClass, Race

# ---------------------------------------------------------------------------
# races — cosmetic, deliberately
# ---------------------------------------------------------------------------

RACES: tuple[Race, ...] = (
    Race("human", "Human",
         "Adaptable and quick to learn. Everywhere, in every trade."),
    Race("elf", "Elf",
         "Long-lived and arcane-attuned. Remembers things nobody wrote down."),
    Race("dwarf", "Dwarf",
         "Stone-stubborn. Holds grudges the way mountains hold snow."),
    Race("halfling", "Halfling",
         "Small, lucky, and consistently underestimated."),
    Race("half_orc", "Half-Orc",
         "Built like a door. Subtlety is somebody else's problem."),
    Race("gnome", "Gnome",
         "Clever to a fault. Has opinions about your equipment."),
)

# ---------------------------------------------------------------------------
# levelling
#
# Renown thresholds. Kept close to the old rank pacing so existing characters
# land where they already were: rank 2 near 12 renown, rank 3 near 40.
# ---------------------------------------------------------------------------

LEVEL_RENOWN: tuple[int, ...] = (0, 6, 14, 26, 42, 62, 88, 120)
MAX_LEVEL = len(LEVEL_RENOWN)


def level_for(renown: int) -> int:
    level = 1
    for i, needed in enumerate(LEVEL_RENOWN, 1):
        if renown >= needed:
            level = i
    return level


def renown_for_next(renown: int) -> int | None:
    """Renown still needed for the next level, or None at max level."""
    level = level_for(renown)
    if level >= MAX_LEVEL:
        return None
    return LEVEL_RENOWN[level] - renown


def rank_for_level(level: int) -> int:
    """Which quest tiers the board will offer."""
    if level >= 5:
        return 3
    if level >= 3:
        return 2
    return 1


# Stat growth per level, on top of the class base. Deliberately gentle — the
# interesting part of levelling is the widening choice, not the bigger numbers.
def hp_bonus(level: int) -> int:
    return 3 * (level - 1)


def power_bonus(level: int) -> int:
    return (level - 1) // 2


def focus_bonus(level: int) -> int:
    return (level - 1) // 3


# ---------------------------------------------------------------------------
# ability pools
# ---------------------------------------------------------------------------

CLASSES: tuple[CharClass, ...] = (
    CharClass(
        key="fighter",
        name="Fighter",
        hp_mod=6, power_mod=2, focus_mod=-1,
        blurb="Front line. Big numbers, no tricks, hard to put down.",
        pool=(
            Ability("strike", "Strike", kind="attack", slot="basic",
                    multiplier=1.0, blurb="Your reliable swing. Free, always available."),
            Ability("cleave", "Cleave", kind="attack", slot="signature",
                    cost=2, multiplier=1.7,
                    blurb="A heavy two-handed blow. Hits far harder than a Strike."),
            Ability("shield_wall", "Shield Wall", kind="guard", slot="defence",
                    guard_reduction=0.3, focus_gain=2,
                    blurb="Brace behind the shield. Most of the next hit is absorbed, and focus returns."),
            Ability("second_wind", "Second Wind", kind="heal", slot="recovery",
                    heal=14, uses=2, blurb="Catch your breath and recover health."),

            Ability("sunder", "Sunder", kind="attack", slot="signature",
                    cost=3, multiplier=1.9, ignores_armor=True, unlock_level=3,
                    blurb="Aim for the straps, not the plate. Ignores armour completely."),
            Ability("bulwark", "Bulwark", kind="guard", slot="defence",
                    guard_reduction=0.15, focus_gain=1, unlock_level=5,
                    blurb="Full guard. Almost nothing gets through the next hit."),
            Ability("rally", "Rally", kind="heal", slot="recovery",
                    heal=18, focus_gain=3, uses=2, unlock_level=6,
                    blurb="A shout that restores both health and focus at once."),
            Ability("executioner", "Executioner's Blow", kind="attack",
                    slot="signature", cost=4, multiplier=2.6, unlock_level=8,
                    blurb="Everything you have, in one swing. The heaviest blow a Fighter can throw."),
        ),
    ),
    CharClass(
        key="wizard",
        name="Wizard",
        hp_mod=-3, power_mod=-1, focus_mod=4,
        blurb="Glass and fire. Ends fights fast or dies trying.",
        pool=(
            Ability("staff_jab", "Staff Jab", kind="attack", slot="basic",
                    multiplier=0.9, blurb="A weak poke. Free — what you fall back on when focus runs out."),
            Ability("fireball", "Fireball", kind="attack", slot="signature",
                    cost=3, multiplier=2.6, ignores_armor=True,
                    blurb="Heavy fire damage that ignores armour entirely."),
            Ability("frost_ward", "Frost Ward", kind="guard", slot="defence",
                    guard_reduction=0.45, focus_gain=3,
                    blurb="A shell of ice. Absorbs most of the next hit and restores focus."),
            Ability("draught", "Arcane Draught", kind="heal", slot="recovery",
                    heal=12, uses=2, blurb="A bitter drink that restores health."),

            Ability("arcane_bolt", "Arcane Bolt", kind="attack", slot="basic",
                    multiplier=1.15, unlock_level=3,
                    blurb="A stronger free attack than the staff. Still costs nothing."),
            Ability("chain_lightning", "Chain Lightning", kind="attack",
                    slot="signature", cost=2, multiplier=1.95, unlock_level=5,
                    blurb="Cheaper and faster than Fireball. Less damage, but armour still applies."),
            Ability("mirror_image", "Mirror Image", kind="guard", slot="defence",
                    guard_reduction=0.2, focus_gain=1, unlock_level=6,
                    blurb="Decoys. Very little of the next hit finds the real you."),
            Ability("meteor", "Meteor", kind="attack", slot="signature",
                    cost=5, multiplier=3.4, ignores_armor=True, unlock_level=8,
                    blurb="The heaviest strike in the game. Ignores armour, and empties your focus."),
        ),
    ),
    CharClass(
        key="rogue",
        name="Rogue",
        hp_mod=0, power_mod=1, focus_mod=2,
        blurb="Patient. Punishes anything that telegraphs.",
        pool=(
            Ability("stab", "Stab", kind="attack", slot="basic",
                    multiplier=1.05, blurb="Quick and free."),
            Ability("backstab", "Backstab", kind="attack", slot="signature",
                    cost=2, multiplier=2.0, ignores_armor=True,
                    blurb="Find the gap in the armour. Heavy damage, armour ignored."),
            Ability("dodge", "Dodge", kind="guard", slot="defence",
                    guard_reduction=0.25, focus_gain=1,
                    blurb="Slip aside. Very little of the next hit lands."),
            Ability("bandage", "Bandage", kind="heal", slot="recovery",
                    heal=10, uses=3, blurb="Rough field dressing. Restores a little health, three times a contract."),

            Ability("envenom", "Envenomed Blade", kind="attack",
                    slot="signature", cost=1, multiplier=1.5,
                    ignores_armor=True, unlock_level=3,
                    blurb="Poison on the blade. Ignores armour for a single point of focus."),
            Ability("smoke", "Smoke", kind="guard", slot="defence",
                    guard_reduction=0.1, unlock_level=5,
                    blurb="Vanish. Almost nothing touches you next turn."),
            Ability("tonic", "Vitality Tonic", kind="heal", slot="recovery",
                    heal=16, uses=2, unlock_level=6,
                    blurb="A stronger draught than a bandage."),
            Ability("assassinate", "Assassinate", kind="attack",
                    slot="signature", cost=4, multiplier=2.9,
                    ignores_armor=True, unlock_level=8,
                    blurb="The killing blow. Enormous damage, armour ignored."),
        ),
    ),
    CharClass(
        key="cleric",
        name="Cleric",
        hp_mod=4, power_mod=0, focus_mod=2,
        blurb="Attrition incarnate. Outlasts what it cannot outhit.",
        pool=(
            Ability("mace", "Mace", kind="attack", slot="basic",
                    multiplier=1.0, blurb="Blunt, honest, free."),
            Ability("smite", "Smite", kind="attack", slot="signature",
                    cost=2, multiplier=1.7, ignores_armor=True,
                    blurb="Holy force. Ignores armour."),
            Ability("sanctuary", "Sanctuary", kind="guard", slot="defence",
                    guard_reduction=0.28, focus_gain=2,
                    blurb="A ward. Absorbs most of the next hit and restores focus."),
            Ability("lay_on_hands", "Lay on Hands", kind="heal",
                    slot="recovery", heal=20, uses=2,
                    blurb="Restores a large amount of health."),

            Ability("blessed_hammer", "Blessed Hammer", kind="attack",
                    slot="basic", multiplier=1.15, unlock_level=3,
                    blurb="A stronger free attack than the mace."),
            Ability("divine_wrath", "Divine Wrath", kind="attack",
                    slot="signature", cost=4, multiplier=2.4,
                    ignores_armor=True, unlock_level=5,
                    blurb="Heavy holy damage. Ignores armour."),
            Ability("aegis", "Aegis", kind="guard", slot="defence",
                    guard_reduction=0.2, focus_gain=3, unlock_level=6,
                    blurb="A greater ward. Very little gets through, and focus comes back."),
            Ability("renewal", "Renewal", kind="heal", slot="recovery",
                    heal=26, uses=2, unlock_level=7,
                    blurb="Restores a great deal of health."),
        ),
    ),
    CharClass(
        key="ranger",
        name="Ranger",
        hp_mod=3, power_mod=1, focus_mod=1,
        blurb="Steady. Good at everything, best at nothing.",
        pool=(
            Ability("shortbow", "Shortbow", kind="attack", slot="basic",
                    multiplier=1.05, blurb="A quick shot. Free."),
            Ability("volley", "Volley", kind="attack", slot="signature",
                    cost=2, multiplier=1.95,
                    blurb="Three arrows at once. Solid damage, but armour still applies."),
            Ability("evade", "Evade", kind="guard", slot="defence",
                    guard_reduction=0.35, focus_gain=2,
                    blurb="Give ground. Much of the next hit misses."),
            Ability("salve", "Herbal Salve", kind="heal", slot="recovery",
                    heal=13, uses=2,
                    blurb="Field-picked herbs. Restores health."),

            Ability("hunters_mark", "Hunter's Mark", kind="attack",
                    slot="signature", cost=1, multiplier=1.5,
                    ignores_armor=True, unlock_level=3,
                    blurb="A precise shot for a single point of focus. Ignores armour."),
            Ability("longbow", "Longbow", kind="attack", slot="basic",
                    multiplier=1.2, unlock_level=4,
                    blurb="A stronger free shot than the shortbow."),
            Ability("camouflage", "Camouflage", kind="guard", slot="defence",
                    guard_reduction=0.2, focus_gain=2, unlock_level=6,
                    blurb="Melt into cover. Little of the next hit lands."),
            Ability("rations", "Trail Rations", kind="heal", slot="recovery",
                    heal=18, uses=3, unlock_level=7,
                    blurb="Restores health, three times a contract."),
        ),
    ),
)

RACES_BY_KEY = {r.key: r for r in RACES}
CLASSES_BY_KEY = {c.key: c for c in CLASSES}

SLOTS: tuple[str, ...] = ("basic", "signature", "defence", "recovery")
SLOT_LABELS = {
    "basic": "Basic attack",
    "signature": "Signature",
    "defence": "Defence",
    "recovery": "Recovery",
}


def default_loadout(class_key: str) -> dict[str, str]:
    """The level-1 kit — the first ability defined for each slot."""
    pool = CLASSES_BY_KEY[class_key].pool
    loadout = {}
    for slot in SLOTS:
        for ability in pool:
            if ability.slot == slot and ability.unlock_level <= 1:
                loadout[slot] = ability.key
                break
    return loadout


def spellbook_order(class_key: str, level: int) -> list[Ability]:
    """Unlocked abilities grouped by slot — the order the spellbook prints.

    `!equip <n>` resolves against this exact list. If the display and the
    resolver ever disagree, a player equips something they did not pick.
    """
    pool = CLASSES_BY_KEY[class_key].pool
    out: list[Ability] = []
    for slot in SLOTS:
        out += [a for a in pool if a.slot == slot and a.unlock_level <= level]
    return out


def known_abilities(class_key: str, level: int, slot: str | None = None):
    """Everything this class has unlocked by `level`, optionally one slot."""
    return [a for a in CLASSES_BY_KEY[class_key].pool
            if a.unlock_level <= level and (slot is None or a.slot == slot)]


def find_race(text: str) -> Race | None:
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


def find_ability(text: str, among) -> list[Ability]:
    """Ability candidates matching what the player typed."""
    token = text.strip().lower().replace("-", "_").replace(" ", "_").replace("'", "")
    if not token:
        return []
    if token.isdigit():
        idx = int(token) - 1
        return [among[idx]] if 0 <= idx < len(among) else []

    def norm(a: Ability) -> str:
        return a.name.lower().replace(" ", "_").replace("'", "")

    exact = [a for a in among if token == a.key or token == norm(a)]
    if exact:
        return exact
    prefix = [a for a in among if norm(a).startswith(token)]
    if prefix:
        return prefix
    return [a for a in among if token in norm(a).split("_")]
