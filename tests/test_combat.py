"""Combat invariants.

These are the rules the tactical layer rests on: seeded runs replay, damage is
never zero, armour-piercing actually pierces, guarding pays for the turn it
costs, and the monster always telegraphs.
"""

from __future__ import annotations

import random

from core import combat
from core.content import MONSTERS
from core.game import start_run
from core.state import Character

from .conftest import QUESTS_BY_KEY, ability, fighting, slot


def test_same_seed_replays_identically():
    def play(seed: int) -> list[str]:
        char = fighting(seed=seed)
        out = []
        for _ in range(6):
            if char.run.encounter is None or not char.run.encounter.alive:
                break
            out += combat.player_turn(char, slot(char, 0))
            out += combat.monster_turn(char)
        return out

    assert play(99) == play(99)
    assert play(99) != play(100)


def test_damage_is_never_zero():
    """A 1-power character against the most armoured monster still scratches."""
    char = fighting(char_class="wizard", race="gnome", monster="wight")
    char.run.power = 1
    for _ in range(20):
        before = char.run.encounter.hp
        combat.player_turn(char, slot(char, 0))
        assert char.run.encounter.hp < before
        char.run.encounter.hp = MONSTERS["wight"].max_hp  # reset, keep hitting


def test_signature_that_ignores_armour_ignores_armour():
    armoured = fighting(char_class="wizard", monster="wight", seed=7)
    fireball = ability(armoured, "fireball")
    assert fireball.ignores_armor
    assert MONSTERS["wight"].armor > 0

    armoured.run.focus = fireball.cost
    before = armoured.run.encounter.hp
    combat.player_turn(armoured, fireball)
    pierced = before - armoured.run.encounter.hp

    # Same roll sequence, same multiplier, but armour applied.
    plain = fighting(char_class="wizard", monster="wight", seed=7)
    plain.run.focus = fireball.cost
    unpierced_ability = type(fireball)(
        key="test", name="Test", kind="attack", cost=fireball.cost,
        multiplier=fireball.multiplier, ignores_armor=False,
    )
    before = plain.run.encounter.hp
    combat.player_turn(plain, unpierced_ability)
    blunted = before - plain.run.encounter.hp

    assert pierced > blunted


def test_signature_costs_focus():
    char = fighting(char_class="wizard")
    fireball = ability(char, "fireball")
    char.run.focus = char.run.max_focus
    before = char.run.focus
    combat.player_turn(char, fireball)
    assert char.run.focus == before - fireball.cost


def test_signature_is_illegal_without_focus():
    char = fighting(char_class="wizard")
    fireball = ability(char, "fireball")
    char.run.focus = fireball.cost - 1
    usable, why = combat.ability_is_legal(char, fireball)
    assert not usable and "focus" in why.lower()


def test_basic_attack_is_always_legal():
    """Slot 1 must never be gated, or a player can be left with no move."""
    for cls in ("fighter", "wizard", "rogue", "cleric", "ranger"):
        char = fighting(char_class=cls)
        char.run.focus = 0
        char.run.uses = {k: 0 for k in char.run.uses}
        usable, _ = combat.ability_is_legal(char, slot(char, 0))
        assert usable, f"{cls}'s basic attack was gated"


def test_guard_reduces_incoming_damage():
    guarded = fighting(seed=3)
    guard = slot(guarded, 2)
    combat.player_turn(guarded, guard)
    before = guarded.run.hp
    combat.monster_turn(guarded)
    with_guard = before - guarded.run.hp

    exposed = fighting(seed=3)
    before = exposed.run.hp
    combat.monster_turn(exposed)
    without_guard = before - exposed.run.hp

    assert with_guard < without_guard


def test_guard_grants_focus():
    char = fighting()
    guard = slot(char, 2)
    char.run.focus = 0
    combat.player_turn(char, guard)
    assert char.run.focus == guard.focus_gain


def test_guard_expires_after_one_monster_turn():
    char = fighting(seed=3)
    combat.player_turn(char, slot(char, 2))
    assert char.run.pending_guard is not None
    combat.monster_turn(char)
    assert char.run.pending_guard is None


def test_focus_never_exceeds_max():
    char = fighting()
    char.run.focus = char.run.max_focus
    combat.player_turn(char, slot(char, 2))  # guard grants focus
    assert char.run.focus == char.run.max_focus


def test_recovery_never_overheals():
    char = fighting()
    heal = slot(char, 3)
    char.run.hp = char.run.max_hp - 1
    combat.player_turn(char, heal)
    assert char.run.hp == char.run.max_hp


def test_recovery_is_limited_and_then_illegal():
    char = fighting()
    heal = slot(char, 3)
    assert heal.uses and heal.uses > 0
    for _ in range(heal.uses):
        char.run.hp = 1
        usable, _ = combat.ability_is_legal(char, heal)
        assert usable
        combat.player_turn(char, heal)
    usable, why = combat.ability_is_legal(char, heal)
    assert not usable and "left" in why.lower()


def test_monster_always_telegraphs_its_next_move():
    char = fighting()
    assert char.run.encounter.next_move is not None
    for _ in range(10):
        combat.monster_turn(char)
        assert char.run.encounter.next_move in char.run.encounter.monster.moves


def test_drain_heals_the_monster():
    char = fighting(monster="wight", seed=11)
    enc = char.run.encounter
    drain = next(m for m in enc.monster.moves if m.kind == "drain")
    enc.next_move = drain
    enc.hp = 10
    combat.monster_turn(char)
    assert enc.hp > 10


def test_monster_guard_blunts_the_next_player_hit():
    guarded = fighting(monster="mire_toad", seed=5)
    guarded.run.encounter.guarding = True
    before = guarded.run.encounter.hp
    combat.player_turn(guarded, slot(guarded, 0))
    blunted = before - guarded.run.encounter.hp

    exposed = fighting(monster="mire_toad", seed=5)
    before = exposed.run.encounter.hp
    combat.player_turn(exposed, slot(exposed, 0))
    full = before - exposed.run.encounter.hp

    assert blunted < full


def test_monster_guard_expires_after_one_hit():
    char = fighting(monster="mire_toad")
    char.run.encounter.guarding = True
    combat.player_turn(char, slot(char, 0))
    assert not char.run.encounter.guarding


def test_every_class_kit_has_the_same_four_slots():
    """Muscle memory transfers between characters only if the shape is fixed."""
    for cls in ("fighter", "wizard", "rogue", "cleric", "ranger"):
        char = Character(name="T", race_key="human", class_key=cls)
        kinds = [a.kind for a in char.abilities]
        assert kinds == ["attack", "attack", "guard", "heal"], cls
        assert char.abilities[0].cost == 0, f"{cls}'s basic attack costs focus"


def test_run_snapshots_character_stats():
    """Stat changes mid-run would let a player rebuild their sheet mid-fight."""
    char = Character(name="T", race_key="dwarf", class_key="fighter")
    start_run(char, QUESTS_BY_KEY["cellar_rats"], seed=1)
    assert char.run.max_hp == char.max_hp
    assert char.run.power == char.power
    assert char.run.max_focus == char.max_focus
