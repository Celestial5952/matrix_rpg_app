"""Intent dispatch and the guild-hall state machine.

This is the entire API the Matrix adapter needs:

    reply = handle(player, "accept 2")

`handle` returns a list of markdown lines, or None meaning "this wasn't for us,
stay out of the conversation". Returning None matters — the guild hall is a real
room that people also chat in.
"""

from __future__ import annotations

import random

from . import combat
from .content import quests_for_rank
from .state import (
    BASE_MAX_FOCUS,
    BASE_MAX_HP,
    POTION_CHARGES,
    Player,
    Run,
)

BOARD_SIZE = 3

# Menu index -> action key. Order must match combat.available_actions().
ACTION_ALIASES = {
    "strike": "strike",
    "hit": "strike",
    "attack": "strike",
    "fireball": "fireball",
    "fire": "fireball",
    "cast": "fireball",
    "burn": "fireball",
    "guard": "guard",
    "block": "guard",
    "defend": "guard",
    "potion": "potion",
    "drink": "potion",
    "heal": "potion",
}


def _resolve_action(run: Run, word: str) -> str | None:
    """Map a player's word or menu number onto an action key."""
    if word.isdigit():
        actions = combat.available_actions(run)
        idx = int(word) - 1
        return actions[idx][0] if 0 <= idx < len(actions) else None
    return ACTION_ALIASES.get(word)


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def render_board(player: Player) -> list[str]:
    lines = [
        f"**The Quest Board** — Guild Rank {player.rank} · "
        f"{player.renown} renown · {player.gold} gold",
        "",
    ]
    for i, q in enumerate(player.board, 1):
        lines.append(f"**{i}. {q.name}** _(tier {q.tier}, {q.stages} encounters)_")
        lines.append(f"   {q.flavor}")
        lines.append(f"   Reward: {q.gold} gold, {q.renown} renown")
    lines.append("")
    lines.append("`accept <n>` to take a contract. `board` to re-read it.")
    return lines


def render_combat(run: Run) -> list[str]:
    enc = run.encounter
    assert enc is not None
    lines = [
        f"**{enc.monster.name}**  {combat.hp_bar(enc.hp, enc.monster.max_hp)} "
        f"{enc.hp}/{enc.monster.max_hp}",
        f"_{enc.next_move.telegraph}_",
        "",
        f"**You**  {combat.hp_bar(run.hp, run.max_hp)} {run.hp}/{run.max_hp} · "
        f"focus {run.focus}/{run.max_focus}",
        "",
    ]
    for i, (key, label) in enumerate(combat.available_actions(run), 1):
        lines.append(f"  **{i}.** {label}")
    return lines


def render_status(player: Player) -> list[str]:
    lines = [
        f"**{player.name}** — Guild Rank {player.rank}",
        f"Renown {player.renown} · Gold {player.gold} · "
        f"{player.runs_completed} contracts completed · {player.deaths} deaths",
    ]
    if player.run:
        run = player.run
        lines.append("")
        lines.append(
            f"On contract: **{run.quest.name}**, encounter "
            f"{run.stage + 1}/{run.quest.stages}"
        )
    return lines


# --------------------------------------------------------------------------
# transitions
# --------------------------------------------------------------------------

def roll_board(player: Player, rng: random.Random | None = None) -> None:
    rng = rng or random.Random()
    pool = quests_for_rank(player.rank)
    player.board = rng.sample(pool, min(BOARD_SIZE, len(pool)))


def start_run(player: Player, quest, seed: int | None = None) -> list[str]:
    rng = random.Random(seed)
    run = Run(
        quest=quest,
        hp=BASE_MAX_HP,
        max_hp=BASE_MAX_HP,
        focus=BASE_MAX_FOCUS,
        max_focus=BASE_MAX_FOCUS,
        potions=POTION_CHARGES,
        rng=rng,
    )
    run.encounter = combat.spawn(rng.choice(quest.pool), rng)
    player.run = run
    return [
        f"**{quest.name}**",
        f"_{quest.flavor}_",
        "",
        f"You set out. Encounter 1 of {quest.stages}.",
        "",
        *render_combat(run),
    ]


def _advance_after_kill(player: Player) -> list[str]:
    """Monster died: next encounter, or finish the contract."""
    run = player.run
    assert run is not None
    run.stage += 1

    if run.stage >= run.quest.stages:
        player.gold += run.quest.gold
        player.renown += run.quest.renown
        player.runs_completed += 1
        old_rank = player.rank
        player.run = None
        lines = [
            "",
            f"**Contract complete — {run.quest.name}**",
            f"+{run.quest.gold} gold, +{run.quest.renown} renown.",
        ]
        if player.rank > old_rank:
            lines.append(f"**You are promoted to Guild Rank {player.rank}.** "
                         "Harder contracts are on the board.")
        roll_board(player, run.rng)
        lines.append("")
        lines.append("You walk back into the hall. `board` to see what's up.")
        return lines

    run.encounter = combat.spawn(run.rng.choice(run.quest.pool), run.rng)
    return [
        "",
        f"Encounter {run.stage + 1} of {run.quest.stages}. "
        f"_(HP and focus carry over — no rest between fights.)_",
        "",
        *render_combat(run),
    ]


def _handle_death(player: Player) -> list[str]:
    run = player.run
    assert run is not None
    player.deaths += 1
    player.run = None
    roll_board(player, run.rng)
    return [
        "",
        "**You go down.**",
        f"You wake in the guild infirmary. The {run.quest.name} contract is "
        "forfeit — someone else will take it.",
        "",
        f"You keep your renown ({player.renown}) and your gold ({player.gold}). "
        "`board` when you're ready.",
    ]


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def handle(player: Player, text: str) -> list[str] | None:
    """Route one line of player input. None means 'not a command, ignore it'."""
    raw = text.strip()
    if not raw:
        return None

    explicit = raw.startswith("!")
    body = raw[1:].strip() if explicit else raw
    parts = body.split()
    if not parts:
        return None
    word = parts[0].lower()

    # --- combat input -----------------------------------------------------
    if player.in_combat:
        run = player.run
        assert run is not None
        # A bare number is only a command because this player has a live fight.
        # Anyone not fighting can type "1" in the room and we stay quiet.
        action = _resolve_action(run, word)
        if action:
            ok, why = combat.action_is_legal(run, action)
            if not ok:
                return [why, "", *render_combat(run)]

            lines = combat.player_turn(run, action)
            if not run.encounter.alive:
                lines.append(f"The {run.encounter.monster.name} falls.")
                return lines + _advance_after_kill(player)

            lines += combat.monster_turn(run)
            if not run.alive:
                return lines + _handle_death(player)

            return lines + ["", *render_combat(run)]

        if word in ("flee", "run"):
            return _handle_flee(player)
        if word in ("status", "hp"):
            return render_status(player)
        if word == "help":
            return _render_help(player)
        return None

    # --- guild hall -------------------------------------------------------
    if word in ("board", "quests", "quest"):
        if not player.board:
            roll_board(player)
        return render_board(player)

    if word == "accept" or word == "take":
        if not player.board:
            roll_board(player)
            return ["You haven't read the board yet.", "", *render_board(player)]
        if len(parts) < 2 or not parts[1].isdigit():
            return ["Which one? `accept 1`, `accept 2`…"]
        idx = int(parts[1]) - 1
        if not 0 <= idx < len(player.board):
            return [f"There's no contract {parts[1]} on the board."]
        return start_run(player, player.board[idx])

    if word in ("status", "me"):
        return render_status(player)

    if word == "help":
        return _render_help(player)

    return None


def _handle_flee(player: Player) -> list[str]:
    run = player.run
    assert run is not None
    player.run = None
    roll_board(player, run.rng)
    return [
        f"You break off and run. The {run.quest.name} contract is abandoned — "
        "no pay, no renown, but you keep your skin.",
        "",
        "`board` to pick up something else.",
    ]


def _render_help(player: Player) -> list[str]:
    if player.in_combat:
        return [
            "**In combat** — reply with the number of your action, or its name:",
            "`1` / `strike` · `2` / `fireball` · `3` / `guard` · `4` / `potion`",
            "`flee` to abandon the contract. `status` for your sheet.",
        ]
    return [
        "**Guild Hall**",
        "`board` — read the quest board",
        "`accept <n>` — take a contract",
        "`status` — your sheet",
        "",
        "Prefix anything with `!` if the room is busy (`!board`).",
    ]
