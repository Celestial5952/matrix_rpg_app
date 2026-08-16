"""Play an adventure headlessly to check its difficulty curve.

    python3 -m tools.adventure --runs 300 --level 5 --class fighter
    python3 -m tools.adventure --key sunless_ziggurat --verbose

A ten-chapter gauntlet on one health bar is easy to get wrong in either
direction, and finding that out by playing it in Matrix costs half an hour a
try. The per-chapter table is the useful output: it shows where the run
actually ends, which is where the curve needs work.
"""

from __future__ import annotations

import argparse
import statistics
from collections import Counter

from core import combat
from core.adventures import ADVENTURES, contract_for
from core.chargen import CLASSES_BY_KEY, LEVEL_RENOWN, MAX_LEVEL
from core.game import _spawn_chapter, start_run
from core.items import ITEMS
from core.state import Character
from tools.balance import RACE, bar, reads_telegraphs

MAX_TURNS = 800


def play(adventure, level: int, char_class: str, seed: int,
         consumables: dict[str, int] | None = None) -> tuple[bool, int, int]:
    """Returns (cleared, chapters_survived, turns)."""
    char = Character(name="Sim", race_key=RACE, class_key=char_class,
                     renown=LEVEL_RENOWN[level - 1])
    char.inventory = dict(consumables or {})
    start_run(char, contract_for(adventure), seed=seed)
    run = char.run

    turns = 0
    while turns < MAX_TURNS:
        turns += 1

        # Drink before acting if things are dire and something is carried.
        if run.hp <= run.max_hp * 0.3:
            for key, count in list(char.inventory.items()):
                item = ITEMS.get(key)
                if item and item.kind == "heal" and count > 0:
                    combat.use_item(char, item)
                    break

        ability = reads_telegraphs(char)
        if not combat.ability_is_legal(char, ability)[0]:
            ability = char.abilities[0]
        combat.player_turn(char, ability)

        if not run.encounter.alive:
            run.stage += 1
            if run.stage >= run.quest.stages:
                return True, run.stage, turns
            chapter = run.quest.chapters[run.stage]
            if chapter.rest:
                run.hp = min(run.max_hp, run.hp + chapter.rest)
            run.encounter = _spawn_chapter(run, run.stage)
            continue

        combat.monster_turn(char)
        if not run.alive:
            return False, run.stage, turns

    return False, run.stage, turns


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--key", default=next(iter(ADVENTURES)))
    ap.add_argument("--runs", type=int, default=300)
    ap.add_argument("--level", type=int, default=5)
    ap.add_argument("--class", dest="char_class", default="fighter")
    ap.add_argument("--potions", type=int, default=3,
                    help="greater potions carried in (the real budget)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--all-classes", action="store_true")
    args = ap.parse_args()

    adventure = ADVENTURES.get(args.key)
    if adventure is None:
        raise SystemExit(f"no such adventure: {args.key}. "
                         f"Try: {', '.join(ADVENTURES)}")
    if not 1 <= args.level <= MAX_LEVEL:
        raise SystemExit(f"level must be 1-{MAX_LEVEL}")

    carried = {"greater_potion": args.potions} if args.potions else {}
    classes = list(CLASSES_BY_KEY) if args.all_classes else [args.char_class]

    print(f"{adventure.title} — {adventure.length} chapters, "
          f"min level {adventure.min_level}")
    print(f"{args.runs} runs at level {args.level}, "
          f"carrying {args.potions} greater potions\n")

    for cls in classes:
        results = [play(adventure, args.level, cls, args.seed + i, carried)
                   for i in range(args.runs)]
        cleared = [r for r in results if r[0]]
        rate = 100 * len(cleared) / len(results)
        turns = statistics.median(r[2] for r in cleared) if cleared else 0
        print(f"{CLASSES_BY_KEY[cls].name:<9} {bar(rate)} {rate:5.1f}%   "
              f"{turns:>3.0f} turns when cleared")

        if not args.all_classes:
            print("\n   where runs ended:")
            ends = Counter(r[1] for r in results if not r[0])
            for chapter in range(adventure.length):
                died = ends.get(chapter, 0)
                if died:
                    name = adventure.chapters[chapter].monster
                    pct = 100 * died / len(results)
                    print(f"     ch {chapter + 1:>2} ({name:<16}) "
                          f"{bar(pct, 16)} {pct:4.1f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
