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

import random

from . import combat
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
from .content import (
    STORY_CHANCE,
    plain_contract,
    quests_for_rank,
    roll_contract,
    story_quests,
)
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


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render_races() -> list[str]:
    lines = ["**Choose a race.**", ""]
    for i, r in enumerate(RACES, 1):
        lines.append(f"**{i}. {r.name}** — _{r.blurb}_")
    lines.append("")
    lines.append("Race is who you are, not what you can do — pick the one you "
                 "like. Your class decides the numbers.")
    lines.append("Reply with `!` and a number or a name — e.g. `!2`.")
    return lines


def render_classes() -> list[str]:
    lines = ["**Choose a class.**", ""]
    for i, c in enumerate(CLASSES, 1):
        mods = _mods(c.hp_mod, c.power_mod, c.focus_mod)
        kit = " · ".join(a.name for a in c.pool if a.unlock_level <= 1)
        lines.append(f"**{i}. {c.name}** — {mods}")
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
        f"**{char.title}** — Level {char.level} · Guild Rank {char.rank}",
        f"HP {char.max_hp} · power {char.power} · focus {char.max_focus}",
        f"_{progress}_",
        f"Renown {char.renown} · Gold {char.gold} · "
        f"{char.runs_completed} contracts completed",
        "",
        "**Abilities**",
        *[f"  **{i}.** {a.name} — _{a.blurb}_"
          for i, a in enumerate(char.abilities, 1)],
    ]


def render_board(char: Character) -> list[str]:
    lines = [
        f"**The Quest Board** — {char.name}, Guild Rank {char.rank} · "
        f"{char.renown} renown · {char.gold} gold",
        "",
    ]
    for i, q in enumerate(char.board, 1):
        tag = " ✦ **STORY**" if q.story else ""
        lines.append(f"**{i}. {q.name}**{tag} "
                     f"_(tier {q.tier}, {q.stages} encounters)_")
        lines.append(f"   {q.flavor}")
        for m in q.modifiers:
            lines.append(f"   **{m.name}** — _{m.blurb}_")
        lines.append(f"   Reward: {q.gold} gold, {q.renown} renown")
    lines.append("")
    lines.append("`!accept <n>` to take a contract.")
    return lines


def render_combat(char: Character) -> list[str]:
    run = char.run
    assert run is not None and run.encounter is not None
    enc = run.encounter
    lines = [
        f"**{enc.monster.name}**  {combat.hp_bar(enc.hp, enc.monster.max_hp)} "
        f"{enc.hp}/{enc.monster.max_hp}",
        f"_{enc.next_move.telegraph}_",
        "",
        f"**{char.name}**  {combat.hp_bar(run.hp, run.max_hp)} {run.hp}/{run.max_hp} · "
        f"focus {run.focus}/{run.max_focus}",
        "",
    ]
    for i, (ab, usable, _why) in enumerate(combat.available_actions(char), 1):
        suffix = ""
        if ab.cost:
            suffix = f" _({ab.cost} focus)_"
        elif ab.uses is not None:
            suffix = f" _({run.uses.get(ab.key, 0)} left)_"
        mark = f"**!{i}**" if usable else f"~~!{i}~~"
        lines.append(f"  {mark} {ab.name}{suffix}")
    if char.inventory:
        carried = " · ".join(
            f"{ITEMS[k].name} ×{c}" for k, c in sorted(char.inventory.items())
            if k in ITEMS
        )
        lines.append("")
        lines.append(f"  **!use** — {carried}")
    return lines


def _ability_line(ab, equipped: bool, locked: bool, index: int | None) -> str:
    bits = []
    if ab.cost:
        bits.append(f"{ab.cost} focus")
    if ab.ignores_armor:
        bits.append("ignores armour")
    if ab.heal:
        bits.append(f"heals {ab.heal}")
    if ab.uses:
        bits.append(f"{ab.uses}/contract")
    if ab.focus_gain:
        bits.append(f"+{ab.focus_gain} focus")
    if ab.kind == "guard":
        bits.append(f"{int((1 - ab.guard_reduction) * 100)}% less damage")
    detail = f" _({', '.join(bits)})_" if bits else ""

    if locked:
        return f"  🔒 {ab.name} — _unlocks at level {ab.unlock_level}_"
    suffix = "  ← **equipped**" if equipped else ""
    return f"  **{index}.** {ab.name}{detail}{suffix}"


def render_spellbook(char: Character) -> list[str]:
    """Everything the class can learn, what's equipped, and what's still locked."""
    nxt = renown_for_next(char.renown)
    heading = f"**{char.name}'s Spellbook** — Level {char.level}"
    if nxt is None:
        heading += " _(max)_"
    else:
        heading += f" · {nxt} renown to level {char.level + 1}"

    lines = [heading, ""]
    equipped = {a.key for a in char.abilities}
    numbering = {a.key: i for i, a in
                 enumerate(spellbook_order(char.class_key, char.level), 1)}
    for slot in SLOTS:
        lines.append(f"**{SLOT_LABELS[slot]}**")
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
    lines = [f"**The Quartermaster** — you have **{char.gold}** gold", ""]
    for i, key in enumerate(SHOP_STOCK, 1):
        item = ITEMS[key]
        afford = "" if char.gold >= item.price else "  _(can't afford)_"
        lines.append(f"**{i}. {item.name}** — {item.price}g{afford}")
        lines.append(f"   _{item.blurb}_ {_effect_of(item)}")
    lines.append("")
    lines.append("`!buy <n>` or `!buy <n> <qty>`. Everything is single-use, and "
                 "it all dies with you.")
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
        lines = ["Your bag is empty."]
    else:
        lines = [f"**{char.name}'s bag** — {char.carried} carried", ""]
        for i, (key, count) in enumerate(sorted(char.inventory.items()), 1):
            item = ITEMS.get(key)
            if item is None:
                continue
            lines.append(f"  **{i}.** {item.name} ×{count} {_effect_of(item)}")
    lines.append("")
    lines.append(f"**{char.gold}** gold. `!shop` to spend it, `!use <item>` in a fight.")
    return lines


def render_graveyard(player: Player) -> list[str]:
    if not player.graveyard:
        return ["No one of yours has died yet. Give it time."]
    lines = [f"**The Graveyard** — {len(player.graveyard)} fallen", ""]
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
        "**A new adventurer signs the guild register.**",
        "",
        f"What's your name? Reply with `!` and the name — e.g. `!Doc Weed`."
        f" _({MIN_NAME}–{MAX_NAME} characters.)_",
        "",
        "`!cancel` to back out.",
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


def _creation_input(player: Player, text: str) -> list[str]:
    pending = player.pending
    assert pending is not None

    if text.strip().lower() in ("cancel", "abort", "stop"):
        player.pending = None
        return ["Register closed. `!create` when you're ready."]

    if pending.step == "name":
        name, why = _validate_name(text)
        if name is None:
            return [why]
        pending.name = name
        pending.step = "race"
        return [f"Well met, **{name}**.", "", *render_races()]

    if pending.step == "race":
        race = find_race(text)
        if race is None:
            return ["I don't know that race.", "", *render_races()]
        pending.race_key = race.key
        pending.step = "class"
        return [f"**{race.name}.** {race.blurb}", "", *render_classes()]

    if pending.step == "class":
        cls = find_class(text)
        if cls is None:
            return ["I don't know that calling.", "", *render_classes()]
        char = Character(
            name=pending.name, race_key=pending.race_key, class_key=cls.key
        )
        roll_board(char)
        player.character = char
        player.pending = None
        return [
            "**The register is signed.**",
            "",
            *render_character(char),
            "",
            "Death is permanent here — when you fall, everything above is lost "
            "and you start again from nothing.",
            "",
            "`!board` to see what work there is.",
        ]

    player.pending = None  # unreachable, but never strand a player
    return ["Something went wrong with the register. `!create` to start over."]


# ---------------------------------------------------------------------------
# runs
# ---------------------------------------------------------------------------

def roll_board(char: Character, rng: random.Random | None = None) -> None:
    """Post a fresh set of contracts.

    Ordinary work is drawn by guild rank. Story contracts are gated on renown
    instead and turn up by chance, so an established character occasionally
    finds something waiting for them rather than earning it on a schedule.
    """
    rng = rng or random.Random()
    ordinary = quests_for_rank(char.rank)
    picks = rng.sample(ordinary, min(BOARD_SIZE, len(ordinary)))

    available_story = story_quests(char.renown)
    if available_story and rng.random() < STORY_CHANCE:
        # Replaces the last ordinary posting rather than adding a slot, so the
        # board stays a fixed size and the story job displaces real work.
        picks[-1] = rng.choice(available_story)

    char.board = [roll_contract(q, rng) for q in picks]


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
    run.encounter = combat.spawn(rng.choice(quest.pool), rng, quest)
    char.run = run
    return [
        f"**{quest.name}**",
        f"_{quest.flavor}_",
        "",
        f"{char.name} sets out. Encounter 1 of {quest.stages}.",
        "",
        *render_combat(char),
    ]


def _advance_after_kill(char: Character) -> list[str]:
    run = char.run
    assert run is not None
    run.stage += 1

    if run.stage >= run.quest.stages:
        old_rank = char.rank
        old_level = char.level
        old_abilities = {a.key for a in char.abilities}
        char.gold += run.quest.gold
        char.renown += run.quest.renown
        char.runs_completed += 1
        char.run = None
        lines = [
            "",
            f"**Contract complete — {run.quest.name}**",
            f"+{run.quest.gold} gold, +{run.quest.renown} renown.",
        ]
        drops = roll_loot(run.quest.tier, run.rng)
        for _ in range(run.quest.extra_loot):
            drops += roll_loot(run.quest.tier, run.rng)
        for key in drops:
            char.inventory[key] = char.inventory.get(key, 0) + 1
        if drops:
            names = " · ".join(ITEMS[k].name for k in drops)
            lines.append(f"Salvaged from the bodies: **{names}**.")
        else:
            lines.append("_Nothing worth carrying home._")
        if char.level > old_level:
            lines.append("")
            lines.append(f"**{char.name} reaches Level {char.level}.** "
                         f"HP {char.max_hp} · power {char.power} · "
                         f"focus {char.max_focus}.")
            learned = [a for a in char.char_class.pool
                       if old_level < a.unlock_level <= char.level]
            if learned:
                names = " · ".join(a.name for a in learned)
                lines.append(f"**Learned: {names}.** `!spellbook` to equip.")
        if char.rank > old_rank:
            lines.append(f"**Guild Rank {char.rank}.** "
                         "Harder contracts are on the board.")
        roll_board(char, run.rng)
        lines.append("")
        lines.append("Back to the hall. `!board` to see what's up.")
        return lines

    run.encounter = combat.spawn(run.rng.choice(run.quest.pool), run.rng,
                                 run.quest)
    return [
        "",
        f"Encounter {run.stage + 1} of {run.quest.stages}. "
        "_(HP and focus carry over — no rest between fights.)_",
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
        f"**{char.title} is dead.**",
        f"Killed by a {killer} on {char.run.quest.name}, "
        f"with {char.renown} renown and {char.gold} gold to their name.",
        "",
        "It is all gone — the renown, the gold, the rank. That is the bargain "
        "this guild offers.",
        "",
        "`!create` to sign the register again. `!graveyard` to remember.",
    ]


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def handle(player: Player, text: str) -> list[str] | None:
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
        return _creation_input(player, body)

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
        if word in ("board", "quests", "status", "accept", "me"):
            return [
                "You have no character. `!create` to make one.",
                "",
                f"_{player.deaths} of yours "
                f"{'has' if player.deaths == 1 else 'have'} died so far._"
                if player.deaths
                else "_Death here is permanent, so choose carefully._",
            ]
        return None

    char = player.character

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
                return lines + _advance_after_kill(char)

            lines += combat.monster_turn(char)
            if not char.run.alive:
                return lines + _handle_death(player)

            return lines + ["", *render_combat(char)]

        if word == "use":
            return _use(char, parts[1:], player)

        if word in ("bag", "inventory", "items"):
            return render_inventory(char)

        if word in ("equip", "swap", "learn"):
            return ["Not mid-fight — you rewrite the book back at the hall.",
                    "", *render_combat(char)]

        if word in ("spellbook", "spells", "book", "abilities"):
            return render_spellbook(char)

        if word in ("flee", "run"):
            return _flee(char)
        if word in ("status", "me", "char"):
            return render_character(char)
        if word == "help":
            return _help(player)
        return None

    # 4. Guild hall.
    if word in ("board", "quests", "quest"):
        if not char.board:
            roll_board(char)
        return render_board(char)

    if word in ("accept", "take"):
        if not char.board:
            roll_board(char)
            return ["You haven't read the board yet.", "", *render_board(char)]
        if len(parts) < 2 or not parts[1].isdigit():
            return ["Which one? `!accept 1`, `!accept 2`…"]
        idx = int(parts[1]) - 1
        if not 0 <= idx < len(char.board):
            return [f"There's no contract {parts[1]} on the board."]
        return start_run(char, char.board[idx])

    if word in ("spellbook", "spells", "book", "abilities"):
        return render_spellbook(char)

    if word in ("equip", "swap", "learn"):
        return _equip(char, parts[1:])

    if word in ("shop", "quartermaster", "store"):
        return render_shop(char)

    if word == "buy":
        return _buy(char, parts[1:])

    if word in ("bag", "inventory", "items"):
        return render_inventory(char)

    if word == "use":
        return ["Nothing to use — you're not in a fight.",
                "Items are for when it's going badly. `!bag` to see what you have."]

    if word in ("status", "me", "char", "sheet"):
        return render_character(char)

    if word == "graveyard":
        return render_graveyard(player)

    if word in ("create", "new"):
        return [
            f"**{char.title}** is still alive and still working.",
            "You only get one at a time. The register opens when they fall.",
        ]

    if word == "help":
        return _help(player)

    return None


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


def _buy(char: Character, args: list[str]) -> list[str]:
    if not args:
        return ["Buy what? `!buy 1`, or `!buy 1 3` for three.", "", *render_shop(char)]

    matches = match_items(args[0], SHOP_STOCK)
    if not matches:
        return [f"The quartermaster doesn't stock '{args[0]}'.", "", *render_shop(char)]
    if len(matches) > 1:
        return [f"Which one? " + " · ".join(m.name for m in matches)]
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


def _use(char: Character, args: list[str], player: Player) -> list[str]:
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
        return [f"Which one? " + " · ".join(m.name for m in matches)]
    item = matches[0]

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
        return lines + _advance_after_kill(char)

    lines += combat.monster_turn(char)
    if not char.run.alive:
        return lines + _handle_death(player)

    return lines + ["", *render_combat(char)]


def _flee(char: Character) -> list[str]:
    run = char.run
    assert run is not None
    char.run = None
    roll_board(char, run.rng)
    return [
        f"{char.name} breaks off and runs. The {run.quest.name} contract is "
        "abandoned — no pay, no renown, but you live.",
        "",
        "`!board` to pick up something else.",
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
        "  `!inventory` — what you're carrying · also `!bag` `!items`",
        "  `!spellbook` — abilities known and equipped · also `!spells`",
        "  `!equip <name>` — swap an ability into its slot",
        "  `!graveyard` — your fallen",
        "",
        "**Guild hall**",
        "  `!board` — read the quest board · also `!quests`",
        "  `!accept <n>` — take a contract",
        "  `!shop` — the quartermaster's stock",
        "  `!buy <n>` — buy one · `!buy <n> <qty>` for more",
        "",
        "**In a fight**",
        "  `!1` `!2` `!3` `!4` — your four abilities, or type their names",
        "  `!use <item>` — spend a consumable, costs your turn",
        "  `!flee` — abandon the contract, keep your life",
        "",
        "  `!help` — this list",
    ]
    return lines
