#!/usr/bin/env python3
"""Balance harness — play thousands of runs headlessly and report the numbers.

    python3 -m tools.balance
    python3 -m tools.balance --runs 5000 --quest barrow_door

The point is to answer questions you cannot answer by playing five fights by
hand: is a contract winnable, is Fireball worth casting, does Guard matter,
and is the difficulty curve between tiers actually a curve.

Strategies are deliberately dumb. They are not how a human plays — they are
reference points. If `always_strike` clears a tier-3 contract most of the time,
the tactical layer isn't doing any work.
"""

from __future__ import annotations

import argparse
import random
import statistics
from collections import Counter
from dataclasses import dataclass

from core import combat
from core.content import QUESTS
from core.state import (
    BASE_MAX_FOCUS,
    BASE_MAX_HP,
    POTION_CHARGES,
    Quest,
    Run,
)

# --- strategies ------------------------------------------------------------
# Each takes a Run and returns an action key. Only legal actions, please.


def always_strike(run: Run) -> str:
    return "strike"


def fireball_on_cooldown(run: Run) -> str:
    if run.focus >= combat.FIREBALL_COST:
        return "fireball"
    return "strike"


def cautious(run: Run) -> str:
    """Heal when low, guard the telegraphed heavy hit, otherwise hit back."""
    if run.hp <= run.max_hp * 0.35 and run.potions > 0:
        return "potion"
    if run.encounter.next_move.kind == "heavy":
        return "guard"
    if run.focus >= combat.FIREBALL_COST:
        return "fireball"
    return "strike"


def reads_telegraphs(run: Run) -> str:
    """`cautious`, plus spending focus on armour rather than on cooldown."""
    if run.hp <= run.max_hp * 0.35 and run.potions > 0:
        return "potion"
    if run.encounter.next_move.kind == "heavy":
        return "guard"
    # Fireball ignores armour, so save it for the targets armour actually hurts.
    if run.focus >= combat.FIREBALL_COST and run.encounter.monster.armor >= 2:
        return "fireball"
    if run.focus >= run.max_focus:
        return "fireball" if run.focus >= combat.FIREBALL_COST else "strike"
    return "strike"


STRATEGIES = {
    "always_strike": always_strike,
    "fireball_on_cooldown": fireball_on_cooldown,
    "cautious": cautious,
    "reads_telegraphs": reads_telegraphs,
}

MAX_TURNS = 500  # a run this long is a stall bug, not a hard fight


@dataclass
class Result:
    won: bool
    turns: int
    hp_left: int
    potions_left: int
    stalled: bool = False


def play_one(quest: Quest, strategy, seed: int) -> Result:
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

    turns = 0
    while turns < MAX_TURNS:
        turns += 1
        action = strategy(run)
        ok, _ = combat.action_is_legal(run, action)
        if not ok:
            action = "strike"  # strategy asked for something illegal; fall back

        combat.player_turn(run, action)

        if not run.encounter.alive:
            run.stage += 1
            if run.stage >= quest.stages:
                return Result(True, turns, run.hp, run.potions)
            run.encounter = combat.spawn(rng.choice(quest.pool), rng)
            continue

        combat.monster_turn(run)
        if not run.alive:
            return Result(False, turns, 0, run.potions)

    return Result(False, turns, run.hp, run.potions, stalled=True)


def bar(pct: float, width: int = 24) -> str:
    filled = round(width * pct / 100)
    return "█" * filled + "░" * (width - filled)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=2000, help="runs per quest/strategy")
    ap.add_argument("--quest", help="only this quest key")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    quests = [q for q in QUESTS if not args.quest or q.key == args.quest]
    if not quests:
        raise SystemExit(f"no such quest: {args.quest}. "
                         f"Try one of: {', '.join(q.key for q in QUESTS)}")

    print(f"{args.runs} runs per cell. Win rate, then median turns / median HP left.\n")

    for quest in quests:
        print(f"── {quest.name}  (tier {quest.tier}, {quest.stages} encounters, "
              f"{quest.gold}g / {quest.renown}r)")
        for name, strategy in STRATEGIES.items():
            results = [
                play_one(quest, strategy, seed=args.seed + i)
                for i in range(args.runs)
            ]
            wins = [r for r in results if r.won]
            rate = 100 * len(wins) / len(results)
            stalls = sum(r.stalled for r in results)

            turns = statistics.median(r.turns for r in wins) if wins else 0
            hp = statistics.median(r.hp_left for r in wins) if wins else 0
            pot = statistics.mean(r.potions_left for r in wins) if wins else 0

            line = (f"   {name:<22} {bar(rate)} {rate:5.1f}%   "
                    f"{turns:>3.0f} turns  {hp:>3.0f} hp  {pot:.1f} potions left")
            if stalls:
                line += f"  ⚠ {stalls} stalled"
            print(line)
        print()

    # Per-monster lethality, independent of quest structure.
    print("── Monster lethality (solo, from full HP, `reads_telegraphs`)")
    from core.content import MONSTERS
    for key in sorted(MONSTERS):
        solo = Quest(key="solo", name=key, tier=1, flavor="",
                     pool=(key,), stages=1, gold=1, renown=1)
        results = [play_one(solo, reads_telegraphs, seed=args.seed + i)
                   for i in range(args.runs)]
        wins = [r for r in results if r.won]
        rate = 100 * len(wins) / len(results)
        cost = BASE_MAX_HP - statistics.median(r.hp_left for r in wins) if wins else 0
        print(f"   {MONSTERS[key].name:<20} {bar(rate)} {rate:5.1f}%   "
              f"costs {cost:>2.0f} hp")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
