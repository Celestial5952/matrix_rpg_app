"""Headless balance harness.

Plays thousands of runs with a few reference strategies and reports win rate,
length and HP left. The strategies are deliberately dumb — they are yardsticks,
not how a human plays. If `always_basic` clears a tier-3 contract, the tactical
layer is not doing any work.

    python3 -m tools.balance --runs 2000
    python3 -m tools.balance --class wizard --quest barrow_door

Numbers move whenever content.py or chargen.py does, so read them fresh rather
than trusting a figure written down somewhere.
"""

from __future__ import annotations

import argparse
import random
import statistics
from dataclasses import dataclass

from core import combat
from core.chargen import CLASSES, CLASSES_BY_KEY
from core.content import MONSTERS, QUESTS
from core.game import start_run
from core.state import Ability, Character, Quest

# Race is cosmetic, so it cannot move any number here. Fixed for reproducibility.
RACE = "human"

MAX_TURNS = 500  # a run this long is a stall bug, not a hard fight


# --- strategies ------------------------------------------------------------
# Each takes the character and returns an Ability. Slots are fixed by contract:
# 0 basic, 1 signature, 2 defence, 3 recovery.

def always_basic(char: Character) -> Ability:
    return char.abilities[0]


def signature_on_cooldown(char: Character) -> Ability:
    signature = char.abilities[1]
    usable, _ = combat.ability_is_legal(char, signature)
    return signature if usable else char.abilities[0]


def cautious(char: Character) -> Ability:
    """Heal when low, guard the telegraphed heavy, otherwise hit back."""
    run = char.run
    heal, guard = char.abilities[3], char.abilities[2]
    if run.hp <= run.max_hp * 0.35 and combat.ability_is_legal(char, heal)[0]:
        return heal
    if run.encounter.next_move.kind == "heavy":
        return guard
    return signature_on_cooldown(char)


def reads_telegraphs(char: Character) -> Ability:
    """As cautious, but also banks focus behind a monster's own guard turn."""
    run = char.run
    heal, guard = char.abilities[3], char.abilities[2]
    if run.hp <= run.max_hp * 0.35 and combat.ability_is_legal(char, heal)[0]:
        return heal
    if run.encounter.next_move.kind == "heavy":
        return guard
    if run.encounter.guarding:
        return guard  # don't waste a big hit into a raised guard
    return signature_on_cooldown(char)


STRATEGIES = {
    "always_basic": always_basic,
    "signature_on_cooldown": signature_on_cooldown,
    "cautious": cautious,
    "reads_telegraphs": reads_telegraphs,
}


@dataclass
class Result:
    won: bool
    turns: int
    hp_left: int
    stalled: bool = False


def play_one(quest: Quest, strategy, seed: int, race: str, char_class: str) -> Result:
    char = Character(name="Sim", race_key=race, class_key=char_class)
    start_run(char, quest, seed=seed)
    rng = char.run.rng

    turns = 0
    while turns < MAX_TURNS:
        turns += 1
        ability = strategy(char)
        if not combat.ability_is_legal(char, ability)[0]:
            ability = char.abilities[0]  # basic attack is never gated

        combat.player_turn(char, ability)

        if not char.run.encounter.alive:
            char.run.stage += 1
            if char.run.stage >= quest.stages:
                return Result(True, turns, char.run.hp)
            char.run.encounter = combat.spawn(rng.choice(quest.pool), rng)
            continue

        combat.monster_turn(char)
        if not char.run.alive:
            return Result(False, turns, 0)

    return Result(False, turns, char.run.hp, stalled=True)


def bar(pct: float, width: int = 24) -> str:
    filled = round(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


def summarise(results: list[Result]) -> tuple[float, float, float, int]:
    wins = [r for r in results if r.won]
    rate = 100 * len(wins) / len(results)
    turns = statistics.median(r.turns for r in wins) if wins else 0
    hp = statistics.median(r.hp_left for r in wins) if wins else 0
    return rate, turns, hp, sum(r.stalled for r in results)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=2000, help="runs per cell")
    ap.add_argument("--quest", help="only this quest key")
    ap.add_argument("--class", dest="char_class", default="fighter")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if args.char_class not in CLASSES_BY_KEY:
        raise SystemExit(f"no such class: {args.char_class}. "
                         f"Try: {', '.join(CLASSES_BY_KEY)}")

    quests = [q for q in QUESTS if not args.quest or q.key == args.quest]
    if not quests:
        raise SystemExit(f"no such quest: {args.quest}. "
                         f"Try: {', '.join(q.key for q in QUESTS)}")

    reference = CLASSES_BY_KEY[args.char_class].name
    print(f"{args.runs} runs per cell. Win rate, then median turns / median HP left.")
    print(f"Reference character: {reference}\n")

    for quest in quests:
        print(f"── {quest.name}  (tier {quest.tier}, {quest.stages} encounters, "
              f"{quest.gold}g / {quest.renown}r)")
        for name, strategy in STRATEGIES.items():
            rate, turns, hp, stalls = summarise([
                play_one(quest, strategy, args.seed + i, RACE, args.char_class)
                for i in range(args.runs)
            ])
            line = (f"   {name:<22} {bar(rate)} {rate:5.1f}%   "
                    f"{turns:>3.0f} turns  {hp:>3.0f} hp left")
            if stalls:
                line += f"  ⚠ {stalls} stalled"
            print(line)
        print()

    # The axis that character creation added: does class choice actually matter?
    print("── Class comparison  (reads_telegraphs)")
    for quest in quests:
        print(f"   {quest.name}")
        for cls in CLASSES:
            rate, turns, hp, _ = summarise([
                play_one(quest, reads_telegraphs, args.seed + i, RACE, cls.key)
                for i in range(args.runs)
            ])
            print(f"      {cls.name:<10} {bar(rate)} {rate:5.1f}%   "
                  f"{turns:>3.0f} turns  {hp:>3.0f} hp left")
    print()

    print(f"── Monster lethality  (solo, full HP, {reference}, reads_telegraphs)")
    for key in sorted(MONSTERS):
        solo = Quest(key="solo", name=key, tier=1, flavor="",
                     pool=(key,), stages=1, gold=1, renown=1)
        results = [play_one(solo, reads_telegraphs, args.seed + i,
                            RACE, args.char_class)
                   for i in range(args.runs)]
        rate, _, hp, _ = summarise(results)
        max_hp = Character(name="s", race_key=RACE,
                           class_key=args.char_class).max_hp
        print(f"   {MONSTERS[key].name:<20} {bar(rate)} {rate:5.1f}%   "
              f"costs {max_hp - hp:>2.0f} hp")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
