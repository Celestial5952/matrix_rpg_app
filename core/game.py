"""Intent dispatch, character creation, and the guild-hall state machine.

The adapter's entire API:

    reply = handle(player, "accept 2")

Returns a list of markdown lines, or None meaning "not for us, stay quiet" —
the guild hall is a room people also chat in.

**Every** message the bot acts on must start with `!`, with no exceptions —
including the name you type during character creation. A mode where the bot
silently swallows an ordinary sentence (because you happened to be mid-register)
is precisely the confusion the prefix exists to prevent.

Three gates, checked in order: mid-creation input, no-character-yet, and the
normal hall/combat commands.
"""

from __future__ import annotations

import difflib
import random
import time

from . import combat
from .adventures import ADVENTURES, contract_for
from .chargen import (
    CLASSES,
    MAX_LEVEL,
    RACES,
    SLOT_LABELS,
    SLOTS,
    find_ability,
    find_class,
    find_race,
    known_abilities,
    spellbook_order,
    renown_for_next,
)
from .events import EVENTS_BY_KEY, resolve as resolve_choice, roll_event
from .guild import Guild, contribution
from .duel import BAR_DUTY_HOURS, WIN_RENOWN, Duel, Duels
from .duel import is_legal as duel_legal
from .duel import resolve as duel_resolve
from .party import MAX_PARTY, Parties, Party, scaled_for_party
from .content import (
    STORY_CHANCE,
    plain_contract,
    quests_for_rank,
    roll_contract,
    story_quests,
)
from dataclasses import replace as _replace

from .items import ITEMS, SHOP_STOCK, match_items, roll_loot
from .state import (
    MAX_NAME,
    focus_regen_for,
    MIN_NAME,
    Character,
    Pending,
    Player,
    Run,
    Tombstone,
)

BOARD_SIZE = 3
REROLL_COST = 5

# Decoration only. Kept as lookups here rather than fields on the dataclasses
# so content tables stay about mechanics and the theatre lives in the view.
# Real time between encounters. The point of an async contract is that it
# happens in the background of your day rather than at the speed you can type,
# so the bot comes back to you rather than the other way round. Seconds, and
# configurable because a sensible value for playing is a terrible one for
# testing.
TRAVEL_SECONDS = 0

SLOT_ICONS = {"basic": "⚔️", "signature": "🔥", "defence": "🛡️", "recovery": "💚"}
ITEM_ICONS = {"heal": "🧪", "focus": "🔮", "damage": "💥", "buff": "🪓",
              "scroll": "📜", "summon": "📯"}
RACE_ICONS = {"human": "🧑", "elf": "🧝", "dwarf": "🧔", "halfling": "🍄",
              "half_orc": "👹", "gnome": "🎩"}
MONSTER_ICONS = {"cave_rat": "🐀", "kobold": "👺", "mire_toad": "🐸",
                 "bandit": "🗡️", "wolf": "🐺", "brigand_captain": "🎖️",
                 "wight": "💀", "revenant": "☠️"}
CLASS_ICONS = {"fighter": "🗡️", "wizard": "🪄", "rogue": "🗝️", "cleric": "✨",
               "ranger": "🏹"}
MODIFIER_ICON = "⚠️"

# The clerk keeps score. Tiers by how many times this character has bailed —
# she starts sympathetic and does not stay that way.
PORTAL_TAUNTS: dict[str, tuple[str, ...]] = {
    "first": (
        "_'Oh, back so soon!'_ she says kindly. _'Nobody minds. Nobody at all.'_",
        "_'Home safe!'_ she beams. _'That's the important thing. Allegedly.'_",
        "_'A tactical withdrawal,'_ she agrees, writing **ran away** in the ledger.",
    ),
    "few": (
        "_'Ah,'_ she says. _'The portal again. Warm in here, isn't it.'_",
        "_'Do you know,'_ she muses, _'you're very good at the leaving part.'_",
        "_'I've stopped writing the contract name,'_ she says. _'I just write "
        "**left**.'_",
        "_'Bramblewick owes me a copper,'_ she says, not explaining.",
    ),
    "many": (
        "_She has your portal arrival written down before you finish "
        "materialising._ _'Efficient!'_",
        "_'I've given you your own column,'_ she says, showing you. It is a "
        "long column.",
        "_'The wolves have started calling it your song,'_ she says. "
        "_'That shimmering noise. Very distinctive.'_",
        "_'One day,'_ she says fondly, _'you'll come back with the dog.'_",
    ),
    "legendary": (
        "_She rings a small bell. Somewhere in the hall, someone groans and "
        "hands over money._",
        "_'The guild has named a manoeuvre after you,'_ she says. _'It's not "
        "a compliment, but it is an honour.'_",
        "_'Bards have been asking about you,'_ she says. _'I've told them "
        "everything.'_",
        "_'Would you like the portal moved closer to the door?'_ she asks. "
        "_'Save you the walk.'_",
    ),
}


def _portal_taunt(count: int) -> str:
    if count <= 1:
        tier = "first"
    elif count <= 3:
        tier = "few"
    elif count <= 7:
        tier = "many"
    else:
        tier = "legendary"
    return random.choice(PORTAL_TAUNTS[tier])

# Every command word the bot answers to. Used for "did you mean" — a typo
# behind a `!` is intent, and answering silence is indistinguishable from the
# bot being down.
COMMANDS: tuple[str, ...] = (
    "help", "create", "status", "me", "char", "sheet", "board", "quests",
    "quest", "accept", "take", "refresh", "reroll", "shop", "store", "buy",
    "bag", "inventory", "items", "use", "spellbook", "spells", "book",
    "abilities", "equip", "swap", "graveyard", "who", "guild", "flee", "run",
    "portal", "townportal", "tp", "escape", "give", "gift", "hand",
    "duel", "challenge", "party", "invite", "join", "leave", "disband",
)


def _unknown(word: str, extra: list[str] | None = None) -> list[str]:
    """A prefixed command we don't recognise. Never silence — they used `!`."""
    close = difflib.get_close_matches(word, COMMANDS, n=2, cutoff=0.6)
    lines = [f"I don't know `!{word}`."]
    if close:
        lines.append("Did you mean " + " or ".join(f"`!{c}`" for c in close) + "?")
    lines += extra or []
    lines.append("`!help` lists everything.")
    return lines


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_races() -> list[str]:
    lines = ["🎭 **Choose a race.** _Purely a fashion decision._", ""]
    for i, r in enumerate(RACES, 1):
        icon = RACE_ICONS.get(r.key, "🎭")
        lines.append(f"**{i}. {icon} {r.name}** — _{r.blurb}_")
    lines.append("")
    lines.append("Race is who you are, not what you can do — pick the one you "
                 "like. Your class decides the numbers.")
    lines.append("Reply with `!` and a number or a name — e.g. `!2`.")
    return lines


def render_classes() -> list[str]:
    lines = ["⚔️ **Choose a class.** _This one actually matters._", ""]
    for i, c in enumerate(CLASSES, 1):
        mods = _mods(c.hp_mod, c.power_mod, c.focus_mod)
        kit = " · ".join(a.name for a in c.pool if a.unlock_level <= 1)
        icon = CLASS_ICONS.get(c.key, "⚔️")
        lines.append(f"**{i}. {icon} {c.name}** — {mods}")
        lines.append(f"   _{c.blurb}_")
        lines.append(f"   {kit}")
        later = sum(1 for a in c.pool if a.unlock_level > 1)
        if later:
            lines.append(f"   _+{later} more unlocked by levelling._")
    lines.append("")
    lines.append("Reply with `!` and a number or a name — e.g. `!2`.")
    return lines


def _mods(hp: int, power: int, focus: int) -> str:
    parts = []
    for label, value in (("HP", hp), ("power", power), ("focus", focus)):
        if value:
            parts.append(f"{value:+d} {label}")
    return ", ".join(parts) if parts else "no modifiers"


def render_character(char: Character) -> list[str]:
    nxt = renown_for_next(char.renown)
    progress = "max level" if nxt is None else f"{nxt} renown to level {char.level + 1}"
    return [
        f"{RACE_ICONS.get(char.race_key, '🎭')}"
        f"{CLASS_ICONS.get(char.class_key, '⚔️')} **{char.title}** — "
        f"Level {char.level} · Guild Rank {char.rank}",
        f"❤️ {char.max_hp} · 💪 {char.power} · ✨ {char.max_focus}",
        f"_{progress}_",
        f"🏅 {char.renown} renown · 💰 {char.gold} gold · "
        f"📜 {char.runs_completed} contracts completed"
        + (f" · ⚔️ {char.duels_won}W/{char.duels_lost}L"
           if char.duels_won or char.duels_lost else ""),
        "",
        "**Abilities**",
        *[line for i, a in enumerate(char.abilities, 1) for line in (
            f"  **{i}.** {SLOT_ICONS.get(a.slot, '✦')} **{a.name}** "
            f"_({_ability_detail(a)})_",
            f"       _{a.blurb}_",
        )],
        *_current_contract_lines(char),
    ]


def _current_contract_lines(char: Character) -> list[str]:
    run = char.run
    if run is None:
        return ["", f"_In the guild hall. Carrying {char.carried} items._"]
    if run.travelling:
        return ["", f"**On the road** — {run.travel_remaining} to the next "
                    f"waypoint of {run.quest.name}."]
    lines = ["", f"**On contract: {run.quest.name}** — encounter "
                 f"{run.stage + 1} of {run.quest.stages}"]
    if run.quest.modifiers:
        lines.append("  " + " · ".join(m.name for m in run.quest.modifiers))
    if run.encounter is not None:
        icon = MONSTER_ICONS.get(run.encounter.monster.key, "👹")
        lines.append(f"  ❤️ {run.hp}/{run.max_hp} · ✨ {run.focus}/{run.max_focus}"
                     f" · facing {icon} a {run.encounter.monster.name}")
    return lines


def render_board(char: Character,
                 roster: dict[str, Player] | None = None) -> list[str]:
    lines = [
        f"📜 **The Quest Board** — {char.name}, Guild Rank {char.rank} · "
        f"{char.renown} renown · {char.gold} gold",
        "",
    ]
    for i, q in enumerate(char.board, 1):
        tag = " ✦ **STORY** ✦" if q.story else ""
        lines.append(f"**{i}. {q.name}**{tag} "
                     f"_({'⭐' * q.tier}, {q.stages} encounters)_")
        lines.append(f"   {q.flavor}")
        for m in q.modifiers:
            lines.append(f"   {MODIFIER_ICON} **{m.name}** — _{m.blurb}_")
        lines.append(f"   💰 {q.gold} gold · 🏅 {q.renown} renown")
    lines.append("")
    lines.append("`!accept <n>` to take a contract · `!refresh` for new work.")

    on_duty = [p.character for p in (roster or {}).values()
               if p.character is not None and p.character.on_bar_duty]
    if on_duty:
        lines.append("")
        lines.append("🍺 **Behind the bar** _(lost a duel, not taking work)_")
        for other in sorted(on_duty, key=lambda c: -c.barmaid_until):
            lines.append(f"  **{other.name}** — {other.bar_duty_remaining} left")
    if char.on_bar_duty:
        lines.append("")
        lines.append(f"_Including you, for another {char.bar_duty_remaining}._")
    return lines


def render_combat(char: Character) -> list[str]:
    run = char.run
    assert run is not None and run.encounter is not None
    enc = run.encounter
    lines = [
        f"{MONSTER_ICONS.get(enc.monster.key, '👹')} **{enc.monster.name}**  "
        f"{combat.hp_bar(enc.hp, enc.monster.max_hp)} "
        f"{enc.hp}/{enc.monster.max_hp}",
        f"_{enc.next_move.telegraph}_",
        "",
        f"❤️ **{char.name}**  {combat.hp_bar(run.hp, run.max_hp)} "
        f"{run.hp}/{run.max_hp} · ✨ {run.focus}/{run.max_focus}",
        "",
    ]
    for i, (ab, usable, _why) in enumerate(combat.available_actions(char), 1):
        mark = f"**!{i}**" if usable else f"~~!{i}~~"
        icon = SLOT_ICONS.get(ab.slot, "✦")
        lines.append(f"  {mark} {icon} {ab.name}{_combat_detail(ab, run)}")
    if char.inventory:
        lines.append("")
        # Numbering matches `!use <n>`, which resolves against sorted bag order.
        for i, key in enumerate(sorted(char.inventory), 1):
            if key not in ITEMS:
                continue
            item = ITEMS[key]
            lines.append(f"  **!use {i}** {ITEM_ICONS.get(item.kind, '🎒')} "
                         f"{item.name} ×{char.inventory[key]}")
    return lines


def _ability_detail(ab) -> str:
    """The numbers, in the same order everywhere they appear.

    Blurbs say what an ability is for; this says what it costs and does. Both
    are needed — the sheet used to show flavour and no numbers at all, which
    made `!status` useless for deciding anything.
    """
    bits = []
    if ab.kind == "attack":
        # Without this, two basic attacks both read "(free)" and nothing tells
        # you which hits harder.
        bits.append(f"×{ab.multiplier:g} damage")
    if ab.cost:
        bits.append(f"{ab.cost} focus")
    elif ab.kind == "attack":
        bits.append("free")
    if ab.ignores_armor:
        bits.append("ignores armour")
    if ab.kind == "guard":
        bits.append(f"{int((1 - ab.guard_reduction) * 100)}% less damage taken")
    if ab.focus_gain:
        bits.append(f"+{ab.focus_gain} focus")
    if ab.heal:
        bits.append(f"heals {ab.heal}")
    if ab.uses:
        bits.append(f"{ab.uses} per contract")
    return ", ".join(bits)


def _combat_detail(ab, run) -> str:
    """The compact version for the fight menu — cost, charges, armour only."""
    bits = []
    if ab.cost:
        bits.append(f"{ab.cost} focus")
    if ab.uses is not None:
        bits.append(f"{run.uses.get(ab.key, 0)} left")
    if ab.ignores_armor:
        bits.append("ignores armour")
    if ab.kind == "guard":
        bits.append(f"−{int((1 - ab.guard_reduction) * 100)}% damage taken")
    if ab.heal:
        bits.append(f"heals {ab.heal}")
    return f" _({', '.join(bits)})_" if bits else ""


def _ability_line(ab, equipped: bool, locked: bool, index: int | None) -> str:
    detail = f" _({_ability_detail(ab)})_"

    if locked:
        return (f"  🔒 {ab.name} — _unlocks at level {ab.unlock_level}_\n"
                f"       _{ab.blurb}_")
    suffix = "  ← **equipped**" if equipped else ""
    return (f"  **{index}.** {ab.name}{detail}{suffix}\n"
            f"       _{ab.blurb}_")


def render_spellbook(char: Character) -> list[str]:
    """Everything the class can learn, what's equipped, and what's still locked."""
    nxt = renown_for_next(char.renown)
    heading = f"📖 **{char.name}'s Spellbook** — Level {char.level}"
    if nxt is None:
        heading += " _(max)_"
    else:
        heading += f" · {nxt} renown to level {char.level + 1}"

    lines = [heading, ""]
    equipped = {a.key for a in char.abilities}
    numbering = {a.key: i for i, a in
                 enumerate(spellbook_order(char.class_key, char.level), 1)}
    for slot in SLOTS:
        lines.append(f"{SLOT_ICONS.get(slot, '✦')} **{SLOT_LABELS[slot]}**")
        for ab in char.char_class.pool:
            if ab.slot != slot:
                continue
            locked = ab.unlock_level > char.level
            lines.append(_ability_line(ab, ab.key in equipped, locked,
                                       numbering.get(ab.key)))
        lines.append("")
    lines.append("`!equip <name>` or `!equip <n>` to swap one in. "
                 "The slot is decided by the ability.")
    return lines


def render_shop(char: Character) -> list[str]:
    lines = [f"🧪 **Bramblewick's Sundries** — you have **{char.gold}** gold", ""]
    for i, key in enumerate(SHOP_STOCK, 1):
        item = ITEMS[key]
        afford = "" if char.gold >= item.price else "  _(can't afford)_"
        icon = ITEM_ICONS.get(item.kind, "🎒")
        lines.append(f"**{i}. {icon} {item.name}** — {item.price}g{afford}")
        lines.append(f"   _{item.blurb}_ {_effect_of(item)}")
    lines.append("")
    lines.append("`!buy <n>` or `!buy <n> <qty>`. _'All single use!'_ chirps "
                 "Bramblewick. _'And it all dies with you! Isn\'t that fun?'_")
    return lines


def _effect_of(item) -> str:
    if item.kind == "heal":
        return f"(+{item.heal} HP)"
    if item.kind == "focus":
        return f"(+{item.focus} focus)"
    if item.kind == "damage":
        armour = ", ignores armour" if item.ignores_armor else ""
        return f"({item.damage} damage{armour})"
    if item.kind == "buff":
        return f"(next attack +{int(item.attack_bonus * 100)}%)"
    return ""


def render_inventory(char: Character) -> list[str]:
    if not char.inventory:
        lines = ["🎒 Your bag is empty. Tragic. Cavernous. Echoing."]
    else:
        lines = [f"🎒 **{char.name}'s bag** — {char.carried} carried", ""]
        for i, (key, count) in enumerate(sorted(char.inventory.items()), 1):
            item = ITEMS.get(key)
            if item is None:
                continue
            icon = ITEM_ICONS.get(item.kind, "🎒")
            lines.append(f"  **{i}.** {icon} {item.name} ×{count} "
                         f"{_effect_of(item)}")
    lines.append("")
    lines.append(f"**{char.gold}** gold. `!shop` to spend it, `!use <item>` in a fight.")
    return lines


def render_graveyard(player: Player) -> list[str]:
    if not player.graveyard:
        return ["🪦 No one of yours has died yet! _Give it time, sweetpea._"]
    lines = [f"🪦 **The Graveyard** — {len(player.graveyard)} fallen, all beloved", ""]
    for t in reversed(player.graveyard[-10:]):
        lines.append(
            f"  **{t.name}** the {t.race} {t.char_class} — {t.renown} renown, "
            f"{t.runs_completed} contracts. Killed by {t.killed_by}."
        )
    return lines


# ---------------------------------------------------------------------------
# character creation
# ---------------------------------------------------------------------------

def begin_creation(player: Player) -> list[str]:
    player.pending = Pending(step="name")
    return [
        "✨ **A NEW ADVENTURER APPROACHES** ✨",
        "",
        f"The clerk licks her quill, delighted. _'Name, darling?'_ — reply with "
        f"`!` and the name, e.g. `!Doc Weed`. _({MIN_NAME}–{MAX_NAME} characters.)_",
        "",
        "`!cancel` if you've come to your senses.",
    ]


def _validate_name(raw: str) -> tuple[str | None, str]:
    name = " ".join(raw.split())
    if len(name) < MIN_NAME:
        return None, f"Too short — at least {MIN_NAME} characters."
    if len(name) > MAX_NAME:
        return None, f"Too long — at most {MAX_NAME} characters."
    if not any(ch.isalpha() for ch in name):
        return None, "Needs at least one letter."
    return name, ""


def _creation_input(player: Player, text: str,
                    guild: Guild | None = None) -> list[str]:
    pending = player.pending
    assert pending is not None

    if text.strip().lower() in ("cancel", "abort", "stop"):
        player.pending = None
        return ["_The clerk closes the ledger with a sigh._ `!create` when you're ready."]

    if pending.step == "name":
        name, why = _validate_name(text)
        if name is None:
            return [why]
        pending.name = name
        pending.step = "race"
        return [f"_'**{name}**,'_ she repeats, writing it far too large. "
                f"_'Ooh, that\'ll look lovely on a headstone.'_", "", *render_races()]

    if pending.step == "race":
        race = find_race(text)
        if race is None:
            return ["_She squints._ 'I don\'t know that race, dear.'", "", *render_races()]
        pending.race_key = race.key
        pending.step = "class"
        return [f"**{RACE_ICONS.get(race.key, '🎭')} {race.name}.** {race.blurb}",
                "", *render_classes()]

    if pending.step == "class":
        cls = find_class(text)
        if cls is None:
            return ["_She taps the ledger._ 'Not a calling I know, poppet.'", "", *render_classes()]
        char = Character(
            name=pending.name, race_key=pending.race_key, class_key=cls.key,
            gold=guild.tier.starting_gold if guild else 0,
        )
        roll_board(char, size=_board_size(guild))
        player.character = char
        player.pending = None
        return [
            "🖋️ **THE REGISTER IS SIGNED** 🖋️",
            "",
            *render_character(char),
            "",
            "_'Do try to come back,'_ she says brightly. _'They mostly don\'t.'_",
            "Death is permanent. Everything above dies with you.",
            "",
            "`!board` — go on, have a look. 📜"
        ]

    player.pending = None  # unreachable, but never strand a player
    return ["Something went wrong with the register. `!create` to start over."]


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

def _board_size(guild: Guild | None) -> int:
    return guild.tier.board_size if guild else BOARD_SIZE


def _scroll_bonus(guild: Guild | None) -> float:
    return guild.tier.scroll_bonus if guild else 1.0


def roll_board(char: Character, rng: random.Random | None = None,
               size: int | None = None) -> None:
    """Post a fresh set of contracts.

    Ordinary work is drawn by guild rank. Story contracts are gated on renown
    instead and turn up by chance, so an established character occasionally
    finds something waiting for them rather than earning it on a schedule.
    """
    rng = rng or random.Random()
    wanted = BOARD_SIZE if size is None else size
    ordinary = quests_for_rank(char.rank)
    picks = rng.sample(ordinary, min(wanted, len(ordinary)))

    available_story = story_quests(char.renown)
    if available_story and rng.random() < STORY_CHANCE:
        # Replaces the last ordinary posting rather than adding a slot, so the
        # board stays a fixed size and the story job displaces real work.
        picks[-1] = rng.choice(available_story)

    char.board = [roll_contract(q, rng) for q in picks]


def render_event(char: Character) -> list[str]:
    run = char.run
    event = EVENTS_BY_KEY[run.pending_event]
    lines = [event.prompt, ""]
    for i, choice in enumerate(event.choices, 1):
        lines.append(f"  **!{i}** {choice.label}")
    lines.append("")
    lines.append(f"_{char.name} · ❤️ {run.hp}/{run.max_hp} · "
                 f"✨ {run.focus}/{run.max_focus}_")
    return lines


def _apply_outcome(run, char: Character, outcome) -> list[str]:
    """Apply one outcome's effects and describe only what actually changed."""
    lines = [outcome.text]
    if outcome.hp:
        before = run.hp
        run.hp = min(run.max_hp, run.hp + outcome.hp)
        moved = run.hp - before
        if moved > 0:
            lines.append(f"_**+{moved}** HP._")
        elif moved < 0:
            lines.append(f"_**{moved}** HP._")
    if outcome.focus:
        before = run.focus
        run.focus = max(0, min(run.max_focus, run.focus + outcome.focus))
        if run.focus != before:
            lines.append(f"_**{run.focus - before:+d}** focus._")
    if outcome.gold:
        # Never take more coin than they have — a negative purse is a bug
        # people notice immediately.
        change = max(outcome.gold, -char.gold)
        char.gold += change
        if change:
            lines.append(f"_**{change:+d}** gold._")
    for key in outcome.items:
        if key in ITEMS:
            char.inventory[key] = char.inventory.get(key, 0) + 1
            lines.append(f"_You gain **{ITEMS[key].name}**._")
    return lines


def set_travel_pace(seconds: int) -> None:
    """How long the march between encounters takes. 0 keeps play instant."""
    global TRAVEL_SECONDS
    TRAVEL_SECONDS = max(0, int(seconds))


def _begin_travel(run) -> bool:
    """Send them walking. False when the pace is instant."""
    if TRAVEL_SECONDS <= 0:
        return False
    run.travel_until = time.time() + TRAVEL_SECONDS
    run.encounter = None
    return True


# What the character says when they get where they were going. The guild is not
# a switchboard — the person you sent up the road is the one who writes to you,
# so these are all first person and all sound like someone tired and fond.
ARRIVAL_WORD = (
    "Made it. Boots are ruined, spirits intact.",
    "I'm here. Don't ask about the shortcut.",
    "Arrived. The road was long and the company was me.",
    "Here. Something howled twice and thought better of it.",
    "Made it, and I only got lost the once.",
    "Arrived — wet, cross, and entirely alive.",
)


def render_travelling(char: Character) -> list[str]:
    run = char.run
    return [
        f"🥾 **{char.name} is on the road** — {run.travel_remaining} to the "
        "next waypoint.",
        f"_❤️ {run.hp}/{run.max_hp} · ✨ {run.focus}/{run.max_focus} · "
        f"{run.quest.name}, {run.stage + 1} of {run.quest.stages}_",
        "",
        f"_{char.name} will write when they get in. `!portal` if you'd rather "
        "they came home._",
    ]


def arrive(player: Player) -> list[str] | None:
    """Deliver whatever was waiting at the end of the road.

    Called by the adapter's ticker rather than by a player command — this is
    the one path where the bot speaks first.
    """
    char = player.character
    if char is None or char.run is None:
        return None
    run = char.run
    if not run.travel_until or run.travelling:
        return None

    run.travel_until = 0.0
    # Word comes from the character, not from the guild. Drawn off the run's
    # own rng so a march replayed after a restart reports the same way.
    lines = [f"🥾 **{char.name}:** _“{run.rng.choice(ARRIVAL_WORD)}”_"]
    if _offer_event(run, run.quest.tier):
        return lines + ["", *render_event(char)]
    return lines + _resume_after_event(char)


def _offer_event(run, tier: int) -> bool:
    """Maybe interrupt the march with a decision. Clears the encounter so
    numbers select an option rather than an ability."""
    event = roll_event(tier, run.rng)
    if event is None:
        return False
    run.pending_event = event.key
    run.encounter = None
    return True


def _spawn_chapter(run, index: int):
    """Spawn the monster for chapter `index`, with that chapter's modifiers.

    Chapter modifiers stack on top of the contract's, so a chapter can be made
    harder without the whole adventure carrying the modifier.
    """
    chapter = run.quest.chapters[index]
    scaled = _replace(run.quest,
                      modifiers=run.quest.modifiers + chapter.modifiers)
    return combat.spawn(chapter.monster, run.rng, scaled)


def _chapter_opening(run, index: int) -> list[str]:
    """Story before a chapter's fight, plus any breather it grants."""
    chapter = run.quest.chapters[index]
    lines = ["", chapter.beat]
    if chapter.rest:
        healed = min(chapter.rest, run.max_hp - run.hp)
        run.hp += healed
        if healed:
            lines.append(f"_You take a moment. **+{healed}** HP._")
    return lines


def start_run(char: Character, quest, seed: int | None = None) -> list[str]:
    rng = random.Random(seed)
    run = Run(
        quest=quest,
        hp=char.max_hp,
        max_hp=char.max_hp,
        focus=char.max_focus,
        max_focus=char.max_focus,
        power=char.power,
        focus_regen=focus_regen_for(char.max_focus),
        uses={a.key: a.uses for a in char.abilities if a.uses is not None},
        rng=rng,
    )
    char.run = run

    if quest.is_adventure:
        run.encounter = _spawn_chapter(run, 0)
        return [
            f"🌀 **{quest.name}**",
            "",
            quest.flavor,
            *_chapter_opening(run, 0),
            "",
            f"_Chapter 1 of {quest.stages}._",
            "",
            *render_combat(char),
        ]

    run.encounter = combat.spawn(rng.choice(quest.pool), rng, quest)
    return [
        f"**{quest.name}**",
        f"_{quest.flavor}_",
        "",
        f"_{char.name} sets out, cape optional._ Encounter 1 of {quest.stages}.",
        "",
        *render_combat(char),
    ]


def _advance_after_kill(char: Character,
                        guild: Guild | None = None) -> list[str]:
    run = char.run
    assert run is not None
    run.stage += 1

    if run.stage >= run.quest.stages:
        old_rank = char.rank
        old_level = char.level
        char.gold += run.quest.gold
        char.renown += run.quest.renown
        char.runs_completed += 1
        char.run = None
        lines = [
            "",
            f"🎉 **CONTRACT COMPLETE — {run.quest.name}** 🎉",
            f"**+{run.quest.gold}** gold · **+{run.quest.renown}** renown. _Not bad, hero._",
        ]
        adventure = ADVENTURES.get(run.quest.adventure_key)
        if adventure is not None:
            drops = list(adventure.rewards)
            final = run.quest.chapters[-1]
            if final.aftermath:
                lines[1:1] = ["", final.aftermath]
            lines += ["", adventure.epilogue]
        else:
            bonus = _scroll_bonus(guild)
            drops = roll_loot(run.quest.tier, run.rng, bonus)
            for _ in range(run.quest.extra_loot):
                drops += roll_loot(run.quest.tier, run.rng, bonus)
        for key in drops:
            char.inventory[key] = char.inventory.get(key, 0) + 1
        if drops:
            names = " · ".join(ITEMS[k].name for k in drops if k in ITEMS)
            if names:
                lines.append(f"💰 _Rifled from the fallen:_ **{names}**.")
        else:
            lines.append("_Nothing worth carrying home. Not even a nice button._")
        if char.level > old_level:
            lines.append("")
            lines.append(f"✨ **DING! {char.name.upper()} REACHES LEVEL "
                         f"{char.level}!** ✨")
            lines.append(f"HP **{char.max_hp}** · power **{char.power}** · "
                         f"focus **{char.max_focus}**")
            learned = [a for a in char.char_class.pool
                       if old_level < a.unlock_level <= char.level]
            if learned:
                names = " · ".join(a.name for a in learned)
                lines.append(f"📖 **You have learned {names}!** "
                             f"`!spellbook` to slot it in.")
        if char.rank > old_rank:
            lines.append(f"🏅 **GUILD RANK {char.rank}!** The clerk pins up "
                         "nastier work with unsettling enthusiasm.")
        if guild is not None:
            gained = contribution(run.quest.renown,
                                  adventure=bool(run.quest.adventure_key))
            before = guild.level
            guild.renown += gained
            guild.contracts_completed += 1
            if run.quest.adventure_key:
                guild.adventures_completed += 1
            lines.append(f"🏰 _The guild is **{gained}** renown richer for it._")
            if guild.level > before:
                lines.append("")
                lines.append(f"🎊 **THE GUILD IS NOW {guild.tier.name.upper()}!** 🎊")
                lines.append(f"_{guild.tier.blurb}_")
                lines.append("`!guild` to see what that changes.")

        roll_board(char, run.rng, _board_size(guild))
        lines.append("")
        lines.append("_Back to the hall, boots muddy, story ready._ `!board`")
        return lines

    # The march comes first: arriving is when the next thing happens, whether
    # that is a decision or a monster.
    if _begin_travel(run):
        return ["", *render_travelling(char)]

    if _offer_event(run, run.quest.tier):
        return ["", *render_event(char)]

    if run.quest.is_adventure:
        previous = run.quest.chapters[run.stage - 1]
        run.encounter = _spawn_chapter(run, run.stage)
        return [
            *(["", previous.aftermath] if previous.aftermath else []),
            *_chapter_opening(run, run.stage),
            "",
            f"_Chapter {run.stage + 1} of {run.quest.stages}._",
            "",
            *render_combat(char),
        ]

    run.encounter = combat.spawn(run.rng.choice(run.quest.pool), run.rng,
                                 run.quest)
    return [
        "",
        f"⚔️ Encounter {run.stage + 1} of {run.quest.stages}. "
        "_No rest, no rebuff, no time to redo your hair._",
        "",
        *render_combat(char),
    ]


def _handle_death(player: Player) -> list[str]:
    """Permadeath. The character is destroyed; only a tombstone remains."""
    char = player.character
    assert char is not None and char.run is not None
    killer = char.run.encounter.monster.name if char.run.encounter else "the road"

    player.graveyard.append(Tombstone(
        name=char.name,
        race=char.race.name,
        char_class=char.char_class.name,
        renown=char.renown,
        runs_completed=char.runs_completed,
        killed_by=killer,
    ))
    player.character = None

    return [
        "",
        f"💀 **{char.title.upper()} IS DEAD** 💀",
        f"_Felled by a {killer}_ on {char.run.quest.name} — {char.renown} renown "
        f"and {char.gold} gold, and no pockets in a shroud.",
        "",
        "_The hall goes quiet. Somebody's soup gets cold. Bramblewick lowers "
        "the awning._",
        "It is all gone. That is the bargain this guild offers, and you took it.",
        "",
        "`!create` to sign again. `!graveyard` to visit. 🕯️",
    ]


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def render_roster(roster: dict[str, Player] | None) -> list[str]:
    """Everyone the bot has seen, living and dead."""
    if not roster:
        return ["Nobody else has signed the register yet."]

    living = [p for p in roster.values() if p.character is not None]
    lines = [f"🏰 **The Guild** — {len(living)} on the books, more or less alive", ""]
    for p in sorted(living, key=lambda p: -p.character.renown):
        c = p.character
        where = "on contract" if c.run else "in the hall"
        lines.append(f"  **{c.name}** the {c.race.name} {c.char_class.name} — "
                     f"L{c.level}, {c.renown} renown, {where}")

    fallen = sum(p.deaths for p in roster.values())
    if fallen:
        lines.append("")
        lines.append(f"_{fallen} dead so far._")
    return lines


# ---------------------------------------------------------------------------
# parties
# ---------------------------------------------------------------------------

def _name_of(mxid: str, roster: dict[str, Player] | None) -> str:
    player = (roster or {}).get(mxid)
    if player and player.character:
        return player.character.name
    return mxid.split(":")[0].lstrip("@")


def render_party(party: Party | None, roster: dict[str, Player] | None,
                 viewer: str) -> list[str]:
    if party is None:
        return [
            "🧭 You're not in a party.",
            "",
            f"`!party` starts one (up to {MAX_PARTY}) · `!invite <who>` to fill it",
            "_Parties share the monster, not the health bar. If everyone goes "
            "down, everyone dies._",
        ]

    lines = [f"🧭 **Party** — {party.size}/{MAX_PARTY}", ""]
    for i, mxid in enumerate(party.members):
        name = _name_of(mxid, roster)
        tags = []
        if mxid == party.leader:
            tags.append("leader")
        if mxid == viewer:
            tags.append("you")
        member = (roster or {}).get(mxid)
        if member and member.character and member.character.run:
            run = member.character.run
            tags.append("**down**" if run.hp <= 0 else f"{run.hp}/{run.max_hp}")
        marker = "▶" if party.on_contract and party.next_actor(_standing(party, roster)) == mxid else " "
        suffix = f" _({', '.join(tags)})_" if tags else ""
        lines.append(f"  {marker} **{i + 1}. {name}**{suffix}")

    if party.invited:
        lines.append("")
        lines.append("_Invited: " + " · ".join(
            _name_of(m, roster) for m in sorted(party.invited)) + "_")

    lines.append("")
    if party.on_contract:
        lines.append(f"On **{party.contract.name}** — encounter "
                     f"{party.stage + 1} of {party.contract.stages}")
        lines.append(f"_It is {_name_of(party.next_actor(_standing(party, roster)), roster)}'s turn._")
    else:
        lines.append("_In the hall._ The leader takes the contract for everyone.")
        lines.append("`!leave` to go your own way · `!disband` to break it up")
    return lines


def _party_commands(player: Player, word: str, parts: list[str],
                    roster: dict[str, Player] | None,
                    parties: Parties | None,
                    mention: str | None = None) -> list[str] | None:
    """Formation only. Nothing here works once a contract has started."""
    if parties is None:
        return ["Parties aren't available right now."]

    char = player.character
    assert char is not None
    party = parties.for_member(player.mxid)

    if word == "party":
        if parts and parts[0].lower() in ("create", "new", "start"):
            word = "create"
        else:
            return render_party(party, roster, player.mxid)

    if word in ("create", "party"):
        if party is not None:
            return ["You're already in a party.", "",
                    *render_party(party, roster, player.mxid)]
        parties.create(player.mxid)
        return [f"🧭 **A party forms around {char.name}.**",
                f"_Up to {MAX_PARTY}._ `!invite <who>` to bring somebody along."]

    if word in ("invite", "ask"):
        if party is None:
            party = parties.create(player.mxid)
        if party.on_contract:
            return ["Not mid-contract. _You cannot recruit through a wall._"]
        if party.leader != player.mxid:
            return [f"Only {_name_of(party.leader, roster)} can invite."]
        if party.is_full:
            return [f"The party is full at {MAX_PARTY}."]
        if not parts:
            return ["Invite who? `!invite wren` · `!who` lists everyone."]

        people = _match_players(parts[0], roster or {}, player.mxid, mention)
        if not people:
            return [f"Nobody here called '{parts[0]}'."]
        if len(people) > 1:
            return ["Which one? " + " · ".join(p.character.name for p in people)]

        guest = people[0]
        if parties.for_member(guest.mxid) is not None:
            return [f"**{guest.character.name}** is already in a party."]
        party.invited.add(guest.mxid)
        return [f"📨 **{guest.character.name}** is invited to "
                f"{char.name}'s party.",
                f"_They accept with_ `!join {char.name.split()[0].lower()}`."]

    if word in ("join", "accept"):
        if party is not None:
            return ["You're already in a party. `!leave` first."]
        pending = parties.invitations_for(player.mxid)
        if not pending:
            return ["Nobody has invited you anywhere. _Awkward._"]
        if parts:
            wanted = [p for p in pending
                      if _name_of(p.leader, roster).lower().startswith(
                          parts[0].strip().lower())]
            pending = wanted or pending
        if len(pending) > 1:
            return ["Which party? " + " · ".join(
                _name_of(p.leader, roster) for p in pending)]

        target = pending[0]
        if target.is_full:
            return ["That party filled up while you were deciding."]
        if target.on_contract:
            return ["They've already set out without you."]
        target.invited.discard(player.mxid)
        target.members.append(player.mxid)
        return [f"🧭 **{char.name}** joins "
                f"{_name_of(target.leader, roster)}'s party.",
                "", *render_party(target, roster, player.mxid)]

    if word == "leave":
        if party is None:
            return ["You're not in a party."]
        if party.on_contract:
            return ["Not mid-contract — `!portal` takes the whole party home."]
        parties.remove_member(party, player.mxid)
        return [f"🧭 **{char.name}** leaves the party."]

    if word == "disband":
        if party is None:
            return ["You're not in a party."]
        if party.leader != player.mxid:
            return [f"Only {_name_of(party.leader, roster)} can disband it."]
        if party.on_contract:
            return ["Not mid-contract — `!portal` takes everyone home."]
        parties.disband(party)
        return ["🧭 _The party breaks up. No hard feelings._"]

    return None


def _members_of(party: Party, roster: dict[str, Player] | None) -> list[Player]:
    return [p for p in ((roster or {}).get(m) for m in party.members)
            if p is not None and p.character is not None]


def _standing(party: Party, roster: dict[str, Player] | None) -> set[str]:
    return {p.mxid for p in _members_of(party, roster)
            if p.character.run is not None and p.character.run.hp > 0}


def _spawn_for_party(party: Party, monster_key: str):
    """One Encounter, shared by reference across every member's Run."""
    encounter = combat.spawn(monster_key, party.rng, party.contract)
    encounter.monster = scaled_for_party(encounter.monster, party.size)
    encounter.hp = encounter.monster.max_hp
    return encounter


def _sync_party_encounter(party: Party, roster: dict[str, Player] | None) -> None:
    for member in _members_of(party, roster):
        run = member.character.run
        if run is not None:
            run.encounter = party.encounter
            run.stage = party.stage


def _next_party_monster(party: Party) -> str:
    contract = party.contract
    if contract.is_adventure:
        return contract.chapters[party.stage].monster
    return party.rng.choice(contract.pool)


def start_party_run(party: Party, roster: dict[str, Player] | None,
                    contract) -> list[str]:
    """Everyone sets out together on the leader's contract."""
    party.contract = contract
    party.stage = 0
    party.begin_round()

    for member in _members_of(party, roster):
        char = member.character
        char.run = Run(
            quest=contract,
            hp=char.max_hp, max_hp=char.max_hp,
            focus=char.max_focus, max_focus=char.max_focus,
            power=char.power,
            focus_regen=focus_regen_for(char.max_focus),
            uses={a.key: a.uses for a in char.abilities if a.uses is not None},
            rng=party.rng,
            party_key=party.key,
        )

    party.encounter = _spawn_for_party(party, _next_party_monster(party))
    _sync_party_encounter(party, roster)

    names = " · ".join(m.character.name for m in _members_of(party, roster))
    return [
        f"🧭 **{contract.name}** — a party of {party.size} sets out",
        f"_{names}_",
        "",
        contract.flavor if contract.is_adventure else f"_{contract.flavor}_",
        "",
        f"_The {party.encounter.monster.name} has been sized up accordingly._",
        "",
        *render_party_combat(party, roster),
    ]


def render_party_combat(party: Party,
                        roster: dict[str, Player] | None) -> list[str]:
    enc = party.encounter
    assert enc is not None
    lines = [
        f"{MONSTER_ICONS.get(enc.monster.key, '👹')} **{enc.monster.name}**  "
        f"{combat.hp_bar(enc.hp, enc.monster.max_hp)} "
        f"{enc.hp}/{enc.monster.max_hp}",
        f"_{enc.next_move.telegraph}_",
        "",
    ]
    turn = party.next_actor(_standing(party, roster))
    for member in _members_of(party, roster):
        run = member.character.run
        if run is None:
            continue
        marker = "▶" if member.mxid == turn else " "
        if run.hp <= 0:
            lines.append(f"  {marker} 💀 **{member.character.name}** — _down_")
        else:
            lines.append(
                f"  {marker} **{member.character.name}**  "
                f"{combat.hp_bar(run.hp, run.max_hp)} {run.hp}/{run.max_hp} · "
                f"✨ {run.focus}/{run.max_focus}")

    current = (roster or {}).get(turn)
    if current and current.character:
        lines.append("")
        lines.append(f"**{current.character.name}'s turn** — "
                     + " · ".join(f"`!{i}` {a.name}" for i, a
                                  in enumerate(current.character.abilities, 1)))
    return lines


def _party_wipe(party: Party, roster: dict[str, Player] | None,
                parties: Parties) -> list[str]:
    """Everyone is down. Everyone dies — this is the price of going together."""
    lines = ["", "💀💀 **THE WHOLE PARTY GOES DOWN** 💀💀", ""]
    for member in list(_members_of(party, roster)):
        lines += _handle_death(member)
    parties.disband(party)
    return lines


def _party_victory(party: Party, roster: dict[str, Player] | None,
                   guild: Guild | None, parties: Parties) -> list[str]:
    """Contract complete. Downed members are picked up, everyone is paid."""
    contract = party.contract
    lines = ["", f"🎉 **CONTRACT COMPLETE — {contract.name}** 🎉"]

    revived = []
    for member in _members_of(party, roster):
        char = member.character
        run = char.run
        if run is not None and run.hp <= 0:
            revived.append(char.name)
        char.run = None
        char.gold += contract.gold
        char.renown += contract.renown
        char.runs_completed += 1
        for key in roll_loot(contract.tier, party.rng, _scroll_bonus(guild)):
            char.inventory[key] = char.inventory.get(key, 0) + 1
        roll_board(char, party.rng, _board_size(guild))

    if revived:
        lines.append(f"_{' and '.join(revived)} are carried out and will be "
                     "fine, mostly._")
    lines.append(f"**Each** of you: +{contract.gold} gold, "
                 f"+{contract.renown} renown.")

    if guild is not None:
        gained = contribution(contract.renown,
                              adventure=bool(contract.adventure_key))
        before = guild.level
        guild.renown += gained
        guild.contracts_completed += 1
        if contract.adventure_key:
            guild.adventures_completed += 1
        lines.append(f"🏰 _The guild is **{gained}** renown richer._")
        if guild.level > before:
            lines.append(f"🎊 **THE GUILD IS NOW {guild.tier.name.upper()}!** 🎊")

    party.contract = None
    party.encounter = None
    party.stage = 0
    party.begin_round()
    lines.append("")
    lines.append("_The party stands down. Still together._ `!party`")
    return lines


def _party_end_of_turn(player: Player, party: Party,
                       roster: dict[str, Player] | None) -> list[str]:
    """Mark the move, and let the monster answer once everyone has gone."""
    lines: list[str] = []
    standing = _standing(party, roster)
    if not party.record_action(player.mxid, standing):
        return lines

    party.begin_round()
    if standing:
        target = (roster or {})[party.rng.choice(sorted(standing))]
        lines.append("")
        lines += combat.monster_turn(target.character)
        if target.character.run.hp <= 0:
            target.character.run.hp = 0
            lines.append(f"💀 **{target.character.name} goes down.**")
    return lines


def _party_after_action(player: Player, party: Party,
                        roster: dict[str, Player] | None) -> list[str]:
    lines: list[str] = []
    if party.encounter.alive:
        lines += _party_end_of_turn(player, party, roster)
    return lines + ["", *render_party_combat(party, roster)]


def _party_action(player: Player, party: Party, ability,
                  roster: dict[str, Player] | None, guild: Guild | None,
                  parties: Parties) -> list[str]:
    """One member's turn against the shared monster."""
    char = player.character
    lines = combat.player_turn(char, ability)

    if not party.encounter.alive:
        lines.append(f"The {party.encounter.monster.name} falls.")
        party.stage += 1
        if party.stage >= party.contract.stages:
            return lines + _party_victory(party, roster, guild, parties)

        if party.contract.is_adventure:
            chapter = party.contract.chapters[party.stage]
            previous = party.contract.chapters[party.stage - 1]
            if previous.aftermath:
                lines += ["", previous.aftermath]
            lines += ["", chapter.beat]
            if chapter.rest:
                for member in _members_of(party, roster):
                    member_run = member.character.run
                    if member_run and member_run.hp > 0:
                        member_run.hp = min(member_run.max_hp,
                                            member_run.hp + chapter.rest)
                lines.append(f"_The party takes a moment. **+{chapter.rest}** HP "
                             "to everyone still standing._")

        party.encounter = _spawn_for_party(party, _next_party_monster(party))
        _sync_party_encounter(party, roster)
        party.begin_round()
        return lines + ["", *render_party_combat(party, roster)]

    lines += _party_end_of_turn(player, party, roster)
    if not _standing(party, roster):
        return lines + _party_wipe(party, roster, parties)
    return lines + ["", *render_party_combat(party, roster)]


# ---------------------------------------------------------------------------
# duels
# ---------------------------------------------------------------------------

def render_duel(duel: Duel, viewer: str) -> list[str]:
    left, right = duel.combatants
    lines = ["⚔️ **DUEL** ⚔️", ""]
    for side in (left, right):
        marker = "▶" if duel.turn == side.mxid else " "
        you = " _(you)_" if side.mxid == viewer else ""
        if side.standing:
            lines.append(f"  {marker} **{side.name}**{you}  "
                         f"{combat.hp_bar(side.hp, side.max_hp)} "
                         f"{side.hp}/{side.max_hp} · ✨ {side.focus}/{side.max_focus}")
        else:
            lines.append(f"  {marker} 💀 **{side.name}**{you} — _beaten_")
    if duel.wager:
        lines.append("")
        lines.append(f"💰 _{duel.wager} gold on the outcome._")
    return lines


def _duel_finish(duel: Duel, winner_mxid: str, roster: dict[str, Player] | None,
                 duels: Duels) -> list[str]:
    """Settle up. Nobody dies; the loser is beaten and the wager moves."""
    winner = (roster or {}).get(winner_mxid)
    loser_mxid = duel.other(winner_mxid).mxid
    loser = (roster or {}).get(loser_mxid)

    lines = ["", f"🏆 **{duel.duelist(winner_mxid).name} WINS** 🏆"]
    if winner and winner.character:
        winner.character.duels_won += 1
        winner.character.renown += WIN_RENOWN
    if loser and loser.character:
        loser.character.duels_lost += 1
        loser.character.barmaid_until = time.time() + BAR_DUTY_HOURS * 3600

    if duel.wager and winner and loser and winner.character and loser.character:
        paid = min(duel.wager, loser.character.gold)
        loser.character.gold -= paid
        winner.character.gold += paid
        if paid < duel.wager:
            lines.append(f"_{loser.character.name} could only cover **{paid}** "
                         f"of the {duel.wager}. Embarrassing for everyone._")
        else:
            lines.append(f"💰 **{paid}** gold changes hands.")

    lines += [
        f"_{duel.duelist(loser_mxid).name} is helped up, mostly intact. "
        "Nobody dies in the yard — that is what contracts are for._",
        "",
        f"🍺 **{duel.duelist(loser_mxid).name} is put on bar duty for "
        f"{BAR_DUTY_HOURS} hours.**",
        "_An apron is produced. It is not optional. The board will say so._",
        "",
        f"_Record: {duel.duelist(winner_mxid).name} "
        f"{winner.character.duels_won if winner and winner.character else 0}W, "
        f"{duel.duelist(loser_mxid).name} "
        f"{loser.character.duels_lost if loser and loser.character else 0}L._",
    ]
    duels.end(duel)
    return lines


def _duel_action(player: Player, duel: Duel, ability,
                 roster: dict[str, Player] | None, duels: Duels) -> list[str]:
    me = duel.duelist(player.mxid)
    them = duel.other(player.mxid)

    lines = duel_resolve(me, them, ability, duel.rng)
    if not them.standing:
        them.hp = 0
        return lines + _duel_finish(duel, player.mxid, roster, duels)

    duel.turn = them.mxid
    return lines + ["", *render_duel(duel, player.mxid)]


def _duel_commands(player: Player, parts: list[str],
                   roster: dict[str, Player] | None, duels: Duels | None,
                   parties: Parties | None,
                   mention: str | None = None) -> list[str]:
    if duels is None:
        return ["Duelling isn't available right now."]

    char = player.character
    assert char is not None
    live = duels.for_player(player.mxid)
    if live is not None:
        return ["You're already in a duel. _There is no leaving one._", "",
                *render_duel(live, player.mxid)]

    action = parts[0].lower() if parts else ""

    if action in ("accept", "yes", "fight"):
        pending = duels.pending_for(player.mxid)
        if not pending:
            return ["Nobody has challenged you. _Yet._"]
        duel = pending[0]
        challenger = (roster or {}).get(duel.challenger)
        if challenger is None or challenger.character is None:
            duels.end(duel)
            return ["Your challenger seems to have stopped existing."]
        if challenger.character.run is not None or char.run is not None:
            return ["One of you is out on a contract. _Settle that first._"]

        duels.begin(duel, challenger.character, char)
        first = duel.duelist(duel.turn).name
        return [
            "⚔️ **THE CHALLENGE IS ACCEPTED** ⚔️",
            f"_{challenger.character.name} and {char.name} take the yard. "
            "The clerk puts down her quill and comes to the window._",
            "",
            "**There is no withdrawing now.** No portal, no contracts, no "
            "leaving until one of you is on the floor.",
            f"_{first} moves first._",
            "",
            *render_duel(duel, player.mxid),
        ]

    if action in ("decline", "no", "refuse"):
        pending = duels.pending_for(player.mxid)
        if not pending:
            return ["Nobody has challenged you."]
        for duel in pending:
            duels.end(duel)
        return ["_You decline. That is your right, right up until you accept._"]

    if not parts:
        pending = duels.pending_for(player.mxid)
        if pending:
            names = " · ".join(
                _name_of(d.challenger, roster) +
                (f" _(for {d.wager}g)_" if d.wager else "") for d in pending)
            return [f"⚔️ **You have been challenged by** {names}.",
                    "`!duel accept` — binding, no way out · `!duel decline`"]
        return ["`!duel <who>` to challenge someone · "
                "`!duel <who> <gold>` to put coin on it."]

    if char.run is not None:
        return ["You're out on a contract. _Finish it first._"]
    if parties and (party := parties.for_member(player.mxid)) and party.on_contract:
        return ["Your party is mid-contract. _Finish it first._"]

    people = _match_players(parts[0], roster or {}, player.mxid, mention)
    if not people:
        return [f"Nobody here called '{parts[0]}'."]
    if len(people) > 1:
        return ["Which one? " + " · ".join(p.character.name for p in people)]
    target = people[0]

    if duels.for_player(target.mxid) is not None:
        return [f"**{target.character.name}** is already fighting somebody."]
    if target.character.run is not None:
        return [f"**{target.character.name}** is out on a contract."]

    # Scan for the number rather than trusting its position: a mention pill
    # can expand to several words ("Duckbill7317 ☭") and push it along.
    numbers = [int(part) for part in parts[1:] if part.isdigit()]
    wager = numbers[0] if numbers else 0
    if wager:
        if wager > char.gold:
            return [f"You have {char.gold} gold. _Bold, though._"]

    if any(d.opponent == target.mxid for d in duels.issued_by(player.mxid)):
        return [f"You've already challenged **{target.character.name}**. "
                "_Let them answer._"]

    duels.challenge(player.mxid, target.mxid, wager)
    stake = f" with **{wager}** gold on it" if wager else ""
    return [
        f"⚔️ **{char.name} calls out {target.character.name}**{stake}.",
        f"_{target.character.name} answers with_ `!duel accept` "
        "_— which cannot be taken back._",
    ]


def render_guild(guild: Guild | None,
                 roster: dict[str, Player] | None) -> list[str]:
    if guild is None:
        return ["The guild's books are not open right now."]

    tier = guild.tier
    lines = [
        f"🏰 **The Guild — {tier.name}** _(level {guild.level})_",
        f"_{tier.blurb}_",
        "",
        f"🏅 **{guild.renown}** guild renown · 📜 {guild.contracts_completed} "
        f"contracts · 🌀 {guild.adventures_completed} adventures",
    ]
    nxt = guild.next_tier
    if nxt is None:
        lines.append("_The guild has nothing left to prove._")
    else:
        lines.append(f"_{guild.renown_to_next} renown to **{nxt.name}**._")

    lines += [
        "",
        "**What the charter buys everyone**",
        f"  💰 New characters start with **{tier.starting_gold}** gold",
        f"  📜 **{tier.board_size}** contracts on the board",
        f"  🌀 Scrolls turn up **×{tier.scroll_bonus}** as often",
        "",
        "_Guild renown is shared, and death cannot take it._",
    ]
    if roster:
        living = sum(1 for p in roster.values() if p.character is not None)
        fallen = sum(p.deaths for p in roster.values())
        lines.append(f"_{living} on the books · {fallen} in the ground._ `!who`")
    return lines


def handle(player: Player, text: str,
           roster: dict[str, Player] | None = None,
           guild: Guild | None = None,
           parties: Parties | None = None,
           duels: Duels | None = None,
           mentions: list[str] | None = None,
           private: bool = False) -> list[str] | None:
    # A mention names a player exactly; text matching is the fallback.
    mention = next((m for m in (mentions or []) if m != player.mxid), None)

    raw = text.strip()
    # The only gate that matters: no `!`, no reaction. Applies in every state,
    # so ordinary conversation can never be mistaken for input.
    if not raw.startswith("!"):
        return None
    body = raw[1:].strip()
    if not body:
        return None

    # 1. Mid-creation: the register is waiting on this player's next `!` line.
    if player.pending is not None:
        return _creation_input(player, body, guild)

    parts = body.split()
    word = parts[0].lower()

    # 2. No character: almost nothing works until you make one.
    if player.character is None:
        if word in ("create", "new", "start", "register"):
            return begin_creation(player)
        if word == "graveyard":
            return render_graveyard(player)
        if word == "help":
            return _help(player)
        if word in ("board", "quests", "status", "accept", "me", "shop",
                    "bag", "inventory", "spellbook", "spells", "who"):
            return [
                "You have no character. `!create` to make one.",
                "",
                f"_{player.deaths} of yours "
                f"{'has' if player.deaths == 1 else 'have'} died so far._"
                if player.deaths
                else "_Death here is permanent, so choose carefully._",
            ]
        return _unknown(word, ["You have no character yet — `!create` first."])

    char = player.character
    party = parties.for_member(player.mxid) if parties else None
    duel = duels.for_player(player.mxid) if duels else None

    # A one-to-one chat is for playing alone. Anything involving other people
    # stays in the hall, where those people can actually see it — a party
    # fight narrated into somebody's private chat is invisible to half the
    # party.
    if private:
        if party is not None and party.on_contract:
            return ["🏰 _Your party is out on a contract._",
                    "That's happening in the hall, where the rest of them "
                    "can see it."]
        if duel is not None:
            return ["⚔️ _You're in the middle of a duel._",
                    "Somebody is waiting for you in the hall."]
        if word in ("party", "invite", "ask", "join", "disband", "duel",
                    "challenge", "give", "gift", "hand"):
            return [f"`!{word}` needs other people, so it happens in the hall.",
                    "_This is the side door — contracts, adventures, shopping "
                    "and the spellbook all work fine here._"]

    # 2b. A live duel is exclusive and binding. Nothing else runs until it
    #     is settled — that is the whole point of accepting one.
    if duel is not None:
        ability = _resolve_ability(char, word)
        if ability is not None:
            if duel.turn != player.mxid:
                return [f"_Wait._ It's **{duel.other(player.mxid).name}**'s move.",
                        "", *render_duel(duel, player.mxid)]
            usable, why = duel_legal(duel.duelist(player.mxid), ability)
            if not usable:
                return [why, "", *render_duel(duel, player.mxid)]
            return _duel_action(player, duel, ability, roster, duels)

        if word == "duel":
            return render_duel(duel, player.mxid)
        if word in ("portal", "townportal", "tp", "escape", "flee", "run",
                    "leave", "disband"):
            return [
                "🚪 _There is no door._",
                f"You agreed to this. **{duel.other(player.mxid).name}** is "
                "still standing, and so are you.",
                "", *render_duel(duel, player.mxid),
            ]
        if word in ("accept", "take", "board", "quests", "quest", "party",
                    "invite", "join", "use", "shop", "buy", "give", "equip"):
            return ["Not in the middle of a duel.", "",
                    *render_duel(duel, player.mxid)]
        if word in ("status", "me", "char", "sheet"):
            return render_character(char)
        if word == "help":
            return _help(player)
        if word.isdigit():
            return [f"There's no action {word}."]
        return _unknown(word, ["You're in a duel — `!1`–`!4`."])

    # 3a. Party combat. The shared encounter is reachable only through here,
    #     and only by members whose turn it is.
    if party is not None and party.on_contract:
        ability = _resolve_ability(char, word)
        if ability is not None:
            if char.run is None or char.run.hp <= 0:
                return ["💀 You're down. The others are still fighting.",
                        "", *render_party_combat(party, roster)]
            if party.next_actor(_standing(party, roster)) != player.mxid:
                return [f"_Wait your turn._ It's "
                        f"**{_name_of(party.next_actor(_standing(party, roster)), roster)}**'s move.",
                        "", *render_party_combat(party, roster)]
            usable, why = combat.ability_is_legal(char, ability)
            if not usable:
                return [why, "", *render_party_combat(party, roster)]
            return _party_action(player, party, ability, roster, guild, parties)

        if word in ("portal", "townportal", "tp", "escape", "flee", "run"):
            return _party_portal(player, party, roster, parties)
        if word == "party":
            return render_party(party, roster, player.mxid)
        if word in ("status", "me", "char", "sheet"):
            return render_character(char)
        if word in ("bag", "inventory", "items"):
            return render_inventory(char)
        if word == "use":
            return _use_in_party(player, party, parts[1:], roster, parties)
        if word == "help":
            return _help(player)
        if word.isdigit():
            return [f"There's no action {word}."]
        return _unknown(word, ["You're in a party fight — `!1`–`!4`, `!portal`."])

    # 3a2. On the road. Nothing to fight until they arrive.
    if char.run is not None and char.run.travelling:
        if word in ("portal", "townportal", "tp", "escape", "flee", "run"):
            return _portal(char)
        if word in ("status", "me", "char", "sheet"):
            return render_character(char)
        if word in ("bag", "inventory", "items"):
            return render_inventory(char)
        if word in ("spellbook", "spells", "book", "abilities"):
            return render_spellbook(char)
        if word == "help":
            return _help(player)
        if word.isdigit() or _resolve_ability(char, word) is not None:
            return render_travelling(char)
        return _unknown(word, ["You're on the road — nothing to fight yet."])

    # 3b. A decision is waiting. Numbers pick an option, not an ability.
    if char.run is not None and char.run.pending_event:
        chosen = _event_choice(player, word)
        if chosen is not None:
            return chosen
        if word in ("portal", "townportal", "tp", "escape", "flee", "run"):
            return _portal(char)
        if word in ("status", "me", "char", "sheet"):
            return render_character(char)
        if word in ("bag", "inventory", "items"):
            return render_inventory(char)
        if word == "help":
            return _help(player)
        return _unknown(word, ["Something is waiting on you — pick an option."])

    # 3. In combat: a bare number is a command only because this player has a
    #    live encounter. Everyone else typing "1" is just chatting.
    if char.in_combat:
        ab = _resolve_ability(char, word)
        if ab is not None:
            usable, why = combat.ability_is_legal(char, ab)
            if not usable:
                return [why, "", *render_combat(char)]

            lines = combat.player_turn(char, ab)
            if not char.run.encounter.alive:
                lines.append(f"The {char.run.encounter.monster.name} falls.")
                return lines + _advance_after_kill(char, guild)

            lines += combat.monster_turn(char)
            if not char.run.alive:
                return lines + _handle_death(player)

            return lines + ["", *render_combat(char)]

        if word == "use":
            return _use(char, parts[1:], player, guild, roster, parties)

        if word in ("bag", "inventory", "items"):
            return render_inventory(char)

        if word in ("give", "gift", "hand"):
            return _give(player, parts[1:], roster, mention)

        if word in ("equip", "swap", "learn"):
            return ["Not mid-fight — you rewrite the book back at the hall.",
                    "", *render_combat(char)]

        if word in ("spellbook", "spells", "book", "abilities"):
            return render_spellbook(char)

        if word in ("duel", "challenge"):
            return ["You're out on a contract. _Finish it first._"]

        if word in ("portal", "townportal", "tp", "escape", "flee", "run"):
            return _portal(char)
        if word in ("status", "me", "char"):
            return render_character(char)
        if word == "help":
            return _help(player)

        if word.isdigit():
            return [f"There's no action {word}. You have "
                    f"{len(char.abilities)}.", "", *render_combat(char)]

        return _unknown(word, ["You're mid-fight — `!1`–`!4`, `!use`, `!portal`."])

    # 4. Guild hall.
    if word in ("board", "quests", "quest"):
        if not char.board or len(char.board) != _board_size(guild):
            roll_board(char, size=_board_size(guild))
        return render_board(char, roster)

    if word in ("accept", "take"):
        if not char.board:
            roll_board(char, size=_board_size(guild))
            return ["You haven't read the board yet.", "", *render_board(char, roster)]
        if len(parts) < 2 or not parts[1].isdigit():
            return ["Which one? `!accept 1`, `!accept 2`…"]
        idx = int(parts[1]) - 1
        if not 0 <= idx < len(char.board):
            return [f"There's no contract {parts[1]} on the board."]

        if char.on_bar_duty:
            return _bar_duty_refusal(char)

        if duels is not None and duels.pending_for(player.mxid):
            return ["⚔️ Somebody has called you out. _Answer it first._",
                    "`!duel accept` or `!duel decline`."]

        if party is not None:
            if party.leader != player.mxid:
                return [f"**{_name_of(party.leader, roster)}** takes the "
                        "contracts for this party."]
            if party.size > 1:
                return start_party_run(party, roster, char.board[idx])
        return start_run(char, char.board[idx])

    if word in ("spellbook", "spells", "book", "abilities"):
        return render_spellbook(char)

    if word in ("equip", "swap", "learn"):
        return _equip(char, parts[1:])

    if word in ("portal", "townportal", "tp", "escape", "flee", "run"):
        return ["🌀 _You cast Town Portal._",
                "_It deposits you in the guild hall, where you already were._",
                "_The clerk does not look up._ _'Impressive.'_"]

    if word in ("refresh", "reroll"):
        return _refresh_board(char, guild, roster)

    # Note: `create` is deliberately absent — it belongs to character
    # creation. A party is started with `!party`.
    if word in ("duel", "challenge", "fight"):
        return _duel_commands(player, parts[1:], roster, duels, parties,
                              mention)

    if word in ("party", "invite", "ask", "join", "leave", "disband"):
        handled = _party_commands(player, word, parts[1:], roster, parties,
                                  mention)
        if handled is not None:
            return handled

    if word in ("shop", "quartermaster", "store"):
        return render_shop(char)

    if word == "buy":
        return _buy(char, parts[1:])

    if word in ("bag", "inventory", "items"):
        return render_inventory(char)

    if word in ("give", "gift", "hand"):
        return _give(player, parts[1:], roster, mention)

    if word == "use":
        return _use_in_hall(char, parts[1:])

    if word in ("status", "me", "char", "sheet"):
        return render_character(char)

    if word == "graveyard":
        return render_graveyard(player)

    if word in ("who", "roster", "members"):
        return render_roster(roster)

    if word in ("guild", "hall", "charter"):
        return render_guild(guild, roster)

    if word in ("create", "new"):
        return [
            f"**{char.title}** is still alive and still working.",
            "You only get one at a time. The register opens when they fall.",
        ]

    if word == "help":
        return _help(player)

    if word.isdigit():
        others = [p for p in (parties.by_key.values() if parties else [])
                  if p.on_contract]
        if others:
            return [
                "🛡️ _Your spell splashes harmlessly against somebody else's "
                "fight._",
                "You're not in that party, so nothing you do reaches it.",
                "`!party` to start your own · `!board` for your own work.",
            ]
        return ["Numbers are for fights and menus. "
                "`!accept <n>` to take a contract."]

    return _unknown(word)


def _resolve_ability(char: Character, word: str):
    abilities = char.abilities
    if word.isdigit():
        idx = int(word) - 1
        return abilities[idx] if 0 <= idx < len(abilities) else None
    token = word.lower()
    for ab in abilities:
        if token == ab.key or token == ab.name.lower().split()[0]:
            return ab
    return None


def _equip(char: Character, args: list[str]) -> list[str]:
    """Swap an ability into its slot. The slot is implied by the ability."""
    unlocked = spellbook_order(char.class_key, char.level)
    if not args:
        return ["Equip what? `!equip sunder`, or `!equip 5` by number.",
                "", *render_spellbook(char)]

    matches = find_ability(args[0], unlocked)
    if not matches:
        # Distinguish "not a thing" from "not yet".
        everything = char.char_class.pool
        locked = find_ability(args[0], [a for a in everything
                                        if a.unlock_level > char.level])
        if locked:
            ab = locked[0]
            return [f"**{ab.name}** unlocks at level {ab.unlock_level}. "
                    f"You are level {char.level}."]
        return [f"No such ability: '{args[0]}'.", "", *render_spellbook(char)]

    if len(matches) > 1:
        return ["Which one? " + " · ".join(m.name for m in matches)]

    ability = matches[0]
    current = {a.slot: a for a in char.abilities}[ability.slot]
    if current.key == ability.key:
        return [f"**{ability.name}** is already in your {SLOT_LABELS[ability.slot].lower()} slot."]

    char.loadout[ability.slot] = ability.key
    return [
        f"**{ability.name}** replaces **{current.name}** "
        f"in the {SLOT_LABELS[ability.slot].lower()} slot.",
        "",
        "**Your kit** — " + " · ".join(
            f"`!{i}` {a.name}" for i, a in enumerate(char.abilities, 1)),
    ]


def _match_players(token: str, roster: dict[str, Player], exclude: str,
                   mention: str | None = None) -> list[Player]:
    """Find a player by mention, character name, display name, or MXID.

    A real Matrix mention wins outright: an Element pill puts the *display
    name* in the message body, which may contain spaces and decoration and may
    not resemble the MXID at all. `m.mentions` carries the actual user id, so
    when it is there we should not be guessing from text.
    """
    candidates = [p for p in roster.values()
                  if p.mxid != exclude and p.character is not None]

    if mention:
        exact = [p for p in candidates if p.mxid == mention]
        if exact:
            return exact

    want = token.strip().lower().lstrip("@")
    if not want:
        return []

    def names(p: Player) -> set[str]:
        return {
            p.character.name.lower(),
            p.character.name.lower().replace(" ", ""),
            p.mxid.lower().lstrip("@"),
            p.mxid.split(":")[0].lower().lstrip("@"),
            (p.display_name or "").lower(),
            (p.display_name or "").lower().replace(" ", ""),
        }

    hit = [p for p in candidates if want in names(p)]
    if hit:
        return hit
    return [p for p in candidates
            if any(n.startswith(want) for n in names(p) if n)]


def _give(player: Player, args: list[str],
          roster: dict[str, Player] | None,
          mention: str | None = None) -> list[str]:
    """Hand an item to another player. The whole point of scrolls being items."""
    char = player.character
    assert char is not None

    if char.in_combat:
        return ["Not mid-fight. _Rummaging in your bag to pass someone a "
                "potion is how both of you end up in the graveyard._"]
    if len(args) < 2:
        return ["`!give <who> <item>` — e.g. `!give doc scroll`.",
                "`!who` lists everyone on the books."]
    if not char.inventory:
        return ["🎒 Your bag is empty. Generous of you, though."]

    people = _match_players(args[0], roster or {}, player.mxid, mention)
    if not people:
        return [f"Nobody here called '{args[0]}'.",
                "_They need a living character to receive anything._ `!who`"]
    if len(people) > 1:
        names = " · ".join(p.character.name for p in people)
        return [f"Which one? {names}"]
    recipient = people[0]

    # Search the remaining words for the item rather than trusting position:
    # a mention pill expands to several words ("Duckbill7317 ☭") and pushes
    # the item name along.
    carried = sorted(char.inventory)
    matches: list = []
    for token in args[1:]:
        found = match_items(token, carried)
        if found:
            matches = found
            break
    if not matches:
        return [f"You're not carrying '{args[-1]}'.", "",
                *render_inventory(char)]
    if len(matches) > 1:
        return ["Which one? " + " · ".join(m.name for m in matches)]
    item = matches[0]

    char.inventory[item.key] -= 1
    if char.inventory[item.key] <= 0:
        del char.inventory[item.key]
    other = recipient.character
    other.inventory[item.key] = other.inventory.get(item.key, 0) + 1

    icon = ITEM_ICONS.get(item.kind, "🎒")
    lines = [
        f"🤝 **{char.name}** hands **{other.name}** {icon} **{item.name}**.",
    ]
    if item.kind == "scroll":
        lines.append("_The scroll changes hands and goes quiet, the way they "
                     "do when they have found somebody new to bother._")
        adventure = ADVENTURES.get(item.adventure)
        if adventure and other.level < adventure.min_level:
            lines.append(f"_{other.name} is level {other.level}; that one "
                         f"needs {adventure.min_level}. Something to grow into._")
    left = char.carried
    lines.append(f"_You have {left} item{'' if left == 1 else 's'} left._")
    return lines


def _summon(player: Player, args: list[str], roster: dict[str, Player] | None,
            parties: Parties | None, party: Party | None,
            mention: str | None = None) -> list[str]:
    """Blow the horn: drag somebody into a fight already in progress.

    Works from a solo fight too, which forms a party around the two of you —
    the horn is the one thing that can change a party's roster mid-contract.
    """
    char = player.character
    if parties is None:
        return ["Parties aren't available right now."]
    if not args:
        return ["Summon who? `!use horn wren`"]

    people = _match_players(args[0], roster or {}, player.mxid, mention)
    if not people:
        return [f"Nobody here called '{args[0]}'."]
    if len(people) > 1:
        return ["Which one? " + " · ".join(p.character.name for p in people)]

    guest = people[0]
    if parties.for_member(guest.mxid) is not None:
        return [f"**{guest.character.name}** is already in a party."]
    if guest.character.run is not None:
        return [f"**{guest.character.name}** is in the middle of their own "
                "contract. _The horn is loud, not rude._"]

    if party is None:
        party = parties.create(player.mxid)
        party.contract = char.run.quest
        party.stage = char.run.stage
        party.encounter = char.run.encounter
        party.rng = char.run.rng
        char.run.party_key = party.key
    if party.is_full:
        return [f"The party is already {MAX_PARTY} strong."]

    party.members.append(guest.mxid)
    other = guest.character
    other.run = Run(
        quest=party.contract,
        hp=other.max_hp, max_hp=other.max_hp,
        focus=other.max_focus, max_focus=other.max_focus,
        power=other.power,
        focus_regen=focus_regen_for(other.max_focus),
        uses={a.key: a.uses for a in other.abilities if a.uses is not None},
        rng=party.rng,
        party_key=party.key,
    )
    other.run.encounter = party.encounter
    other.run.stage = party.stage

    # The monster does not get tougher retroactively — arriving late is the
    # whole advantage of the horn, and scaling it up would erase that.
    char.inventory["summoning_horn"] = char.inventory.get("summoning_horn", 0) - 1
    if char.inventory["summoning_horn"] <= 0:
        del char.inventory["summoning_horn"]

    return [
        "📯 **THE HORN SOUNDS** 📯",
        f"_A note like a door opening in a wall that had no door. "
        f"**{other.name}** arrives mid-swing, holding a cup of something, "
        "extremely confused._",
        "",
        f"**{other.name}** joins the fight at full strength.",
        "",
        *render_party_combat(party, roster),
    ]


def _use_in_party(player: Player, party: Party, args: list[str],
                  roster: dict[str, Player] | None,
                  parties: Parties | None) -> list[str]:
    char = player.character
    if not args:
        return ["Use what?", "", *render_inventory(char)]

    matches = match_items(args[0], sorted(char.inventory))
    if not matches:
        return [f"You're not carrying '{args[0]}'.", "", *render_inventory(char)]
    if len(matches) > 1:
        return ["Which one? " + " · ".join(m.name for m in matches)]

    item = matches[0]
    if item.kind == "summon":
        return _summon(player, args[1:], roster, parties, party)
    if item.kind == "scroll":
        return ["Not mid-fight."]
    if party.next_actor(_standing(party, roster)) != player.mxid:
        return [f"_Wait your turn._ It's "
                f"**{_name_of(party.next_actor(_standing(party, roster)), roster)}**'s move."]

    run = char.run
    if item.kind == "heal" and run.hp >= run.max_hp:
        return [f"You're at full health — {item.name} would be wasted."]
    if item.kind == "focus" and run.focus >= run.max_focus:
        return [f"Your focus is already full — {item.name} would be wasted."]

    lines = combat.use_item(char, item)
    return lines + _party_after_action(player, party, roster)


def _party_portal(player: Player, party: Party,
                  roster: dict[str, Player] | None,
                  parties: Parties) -> list[str]:
    """One member bails and the whole party goes with them."""
    contract = party.contract
    names = []
    for member in _members_of(party, roster):
        char = member.character
        char.portals_used += 1
        char.run = None
        roll_board(char, party.rng)
        names.append(char.name)

    party.contract = None
    party.encounter = None
    party.stage = 0
    party.begin_round()

    puller = player.character.name
    return [
        "🌀 **TOWN PORTAL** 🌀",
        f"_{puller} opens it and the whole party goes through, which is the "
        "agreement, whatever anyone says afterwards._",
        "",
        f"**{contract.name}** is abandoned. No gold, no renown, no loot — "
        f"for any of {len(names)} of you.",
        "",
        _portal_taunt(player.character.portals_used),
        "",
        "_The party is still together._ `!party`",
    ]


def _bar_duty_refusal(char: Character) -> list[str]:
    return [
        f"🍺 **You are behind the bar for another {char.bar_duty_remaining}.**",
        "_You lost a duel. The apron stays on. Somebody wants a second ale "
        "and it is, regrettably, your problem._",
        "",
        "_You can still spend, browse, and be challenged — just not work._",
    ]


def _refresh_board(char: Character, guild: Guild | None = None,
                   roster: dict[str, Player] | None = None) -> list[str]:
    """Pay to have the board rewritten. Gives gold a second sink."""
    if char.gold < REROLL_COST:
        return [f"Rewriting the board costs {REROLL_COST}g and you have "
                f"{char.gold}g."]
    char.gold -= REROLL_COST
    roll_board(char, size=_board_size(guild))
    return [f"_The clerk takes your {REROLL_COST}g, tears everything down, and "
            f"pins up fresh work with a flourish._ ✂️",
            "", *render_board(char, roster)]


def _buy(char: Character, args: list[str]) -> list[str]:
    if not args:
        return ["Buy what? `!buy 1`, or `!buy 1 3` for three.", "", *render_shop(char)]

    matches = match_items(args[0], SHOP_STOCK)
    if not matches:
        return [f"The quartermaster doesn't stock '{args[0]}'.", "", *render_shop(char)]
    if len(matches) > 1:
        return ["Which one? " + " · ".join(m.name for m in matches)]
    item = matches[0]

    qty = 1
    if len(args) > 1:
        if not args[1].isdigit() or int(args[1]) < 1:
            return ["How many? `!buy 1 3` buys three."]
        qty = int(args[1])

    total = item.price * qty
    if total > char.gold:
        affordable = char.gold // item.price
        if affordable == 0:
            return [f"{item.name} costs {item.price}g and you have {char.gold}g."]
        return [f"{qty} × {item.name} is {total}g — you have {char.gold}g. "
                f"You could afford {affordable}."]

    char.gold -= total
    char.inventory[item.key] = char.inventory.get(item.key, 0) + qty
    return [
        f"**Bought {qty} × {item.name}** for {total}g.",
        f"{char.gold} gold left · {char.inventory[item.key]} in the bag.",
    ]


def _use_in_hall(char: Character, args: list[str]) -> list[str]:
    """Only scrolls work outside a fight — everything else is for emergencies."""
    if not args:
        return ["Use what? Scrolls work here; everything else is for a fight.",
                "", *render_inventory(char)]

    carried = sorted(char.inventory)
    matches = match_items(args[0], carried)
    if not matches:
        return [f"You're not carrying '{args[0]}'.", "", *render_inventory(char)]
    if len(matches) > 1:
        return ["Which one? " + " · ".join(m.name for m in matches)]

    item = matches[0]
    if item.kind == "scroll" and char.on_bar_duty:
        return _bar_duty_refusal(char)
    if item.kind != "scroll":
        return [f"**{item.name}** is for when it's going badly, not for "
                "standing about in a guild hall.",
                "_Take a contract first._ `!board`"]

    adventure = ADVENTURES.get(item.adventure)
    if adventure is None:
        return [f"**{item.name}** unrolls into nonsense. Whatever it led to "
                "isn't there any more."]

    if char.level < adventure.min_level:
        return [
            f"📜 _You unroll the **{item.name}**. The script squirms away from "
            "your eye and will not hold still._",
            "",
            f"**{adventure.title}** needs level **{adventure.min_level}**. "
            f"You are level **{char.level}**.",
            "_Come back when you've grown into it._",
        ]

    char.inventory[item.key] = char.inventory.get(item.key, 0) - 1
    if char.inventory[item.key] <= 0:
        del char.inventory[item.key]

    return start_run(char, contract_for(adventure))


def _use(char: Character, args: list[str], player: Player,
         guild: Guild | None = None,
         roster: dict[str, Player] | None = None,
         parties: Parties | None = None) -> list[str]:
    """Spend an item. Costs the turn, so the monster answers."""
    if not char.inventory:
        return ["Your bag is empty.", "", *render_combat(char)]

    carried = sorted(char.inventory)
    if not args:
        return ["Use what? `!use potion`, or `!use 1` by bag position.",
                "", *render_inventory(char)]

    matches = match_items(args[0], carried)
    if not matches:
        return [f"You're not carrying '{args[0]}'.", "", *render_inventory(char)]
    if len(matches) > 1:
        return ["Which one? " + " · ".join(m.name for m in matches)]
    item = matches[0]
    if item.kind == "summon":
        return _summon(player, args[1:], roster, parties, None)
    if item.kind == "scroll":
        return ["Not mid-fight. _Reading a teleport scroll with something "
                "chewing on you is how people end up inside walls._",
                "", *render_combat(char)]

    # Refuse rather than silently burn a single-use item for no effect.
    run = char.run
    if item.kind == "heal" and run.hp >= run.max_hp:
        return [f"You're at full health — {item.name} would be wasted.",
                "", *render_combat(char)]
    if item.kind == "focus" and run.focus >= run.max_focus:
        return [f"Your focus is already full — {item.name} would be wasted.",
                "", *render_combat(char)]
    if item.kind == "buff" and run.next_attack_bonus:
        return ["Your edge is already honed — it would be wasted.",
                "", *render_combat(char)]

    lines = combat.use_item(char, item)
    if not char.run.encounter.alive:
        lines.append(f"The {char.run.encounter.monster.name} falls.")
        return lines + _advance_after_kill(char, guild)

    lines += combat.monster_turn(char)
    if not char.run.alive:
        return lines + _handle_death(player)

    return lines + ["", *render_combat(char)]


def _resume_after_event(char: Character) -> list[str]:
    """Put the next encounter in front of them once a decision is settled."""
    run = char.run
    run.pending_event = ""
    if run.quest.is_adventure:
        chapter = run.quest.chapters[run.stage]
        run.encounter = _spawn_chapter(run, run.stage)
        return ["", chapter.beat, "",
                f"_Chapter {run.stage + 1} of {run.quest.stages}._",
                "", *render_combat(char)]

    run.encounter = combat.spawn(run.rng.choice(run.quest.pool), run.rng,
                                 run.quest)
    return ["",
            f"⚔️ Encounter {run.stage + 1} of {run.quest.stages}.",
            "", *render_combat(char)]


def _event_choice(player: Player, word: str) -> list[str] | None:
    """Route a number to the waiting decision. None if it wasn't one."""
    char = player.character
    run = char.run
    event = EVENTS_BY_KEY[run.pending_event]

    if not word.isdigit():
        return None
    index = int(word) - 1
    if not 0 <= index < len(event.choices):
        return [f"There's no option {word}. There are {len(event.choices)}.",
                "", *render_event(char)]

    choice = event.choices[index]
    outcome = resolve_choice(choice, run.rng)
    lines = [f"**{choice.label}**", ""]
    lines += _apply_outcome(run, char, outcome)

    if run.hp <= 0:
        return lines + _handle_death(player)
    return lines + _resume_after_event(char)


def _portal(char: Character) -> list[str]:
    """Bail out. No gold, no renown, no loot — and the clerk keeps score."""
    run = char.run
    assert run is not None
    char.portals_used += 1
    char.run = None
    roll_board(char, run.rng)

    return [
        "🌀 **TOWN PORTAL** 🌀",
        f"_A shimmering hole opens. {char.name} steps through it with more "
        "haste than dignity and lands in the guild hall, smelling faintly of "
        "somewhere else._",
        "",
        f"The **{run.quest.name}** contract is abandoned. No gold. No renown. "
        "No loot. You keep your skin and nothing else.",
        "",
        _portal_taunt(char.portals_used),
        "",
        f"_Portals taken: {char.portals_used}._ `!board` when you've recovered.",
    ]


def _help(player: Player) -> list[str]:
    """The full command surface, with the current context called out first.

    Everything is listed in every state rather than hidden when unavailable —
    a player who cannot see a command cannot learn it exists.
    """
    char = player.character
    lines = ["**Commands** — every one starts with `!`", ""]

    if char is None:
        lines.append("_You have no character. `!create` is the only thing that "
                     "will do anything._")
    elif char.in_combat:
        slots = " · ".join(f"`!{i}` {ab.name}"
                           for i, ab in enumerate(char.abilities, 1))
        lines.append(f"_In a fight. Your moves: {slots}_")
    else:
        lines.append(f"_In the guild hall as {char.name}._")

    lines += [
        "",
        "**Character**",
        "  `!create` — roll a new one _(only when you have none)_",
        "  `!status` — your sheet · also `!me` `!char` `!sheet`",
        "  `!spellbook` — abilities known, equipped and locked · also `!spells`",
        "  `!equip <name>` — swap an ability into its slot _(hall only)_",
        "  `!inventory` — what you're carrying · also `!bag` `!items`",
        "  `!graveyard` — your fallen",
        "",
        "**Guild hall**",
        "  `!board` — read the quest board · also `!quests`",
        "  `!accept <n>` — take a contract",
        f"  `!refresh` — new contracts on the board ({REROLL_COST}g)",
        "  `!shop` — the quartermaster's stock",
        "  `!buy <n>` — buy one · `!buy <n> <qty>` for more",
        "  `!give <who> <item>` — hand something over",
        "  `!who` — everyone on the books · `!guild` — the charter",
        "",
        "**Party** _(share a monster, not a health bar)_",
        f"  `!party` — start one, or see yours _(up to {MAX_PARTY})_",
        "  `!invite <who>` — leader only · `!join <leader>` to accept",
        "  `!leave` · `!disband` — hall only, not mid-contract",
        "",
        "**In a fight**",
        "  `!1` `!2` `!3` `!4` — your four abilities, or type their names",
        "  `!use <item>` — spend a consumable, costs your turn",
        "  `!use <scroll>` — open an adventure _(hall only)_",
        "  `!portal` — bail out. No rewards, and she will remember",
        "",
        "**Duels** _(consensual, binding, non-lethal)_",
        "  `!duel <who>` — challenge · `!duel <who> <gold>` to put coin on it",
        "  `!duel accept` — take it. There is no backing out",
        "  `!duel decline` — refuse, before it starts",
        f"  _Lose and you're on bar duty for {BAR_DUTY_HOURS}h — no contracts,",
        "  and the board tells everybody._",
        "",
        "  `!help` — this list",
    ]
    return lines
    return lines
