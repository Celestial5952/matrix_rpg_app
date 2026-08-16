"""Combat invariants. Balance numbers will change; these rules shouldn't."""

from __future__ import annotations

import random

import pytest

from core import combat
from core.content import MONSTERS
from core.state import BASE_MAX_FOCUS, BASE_MAX_HP, POTION_CHARGES, Run
from core.content import QUESTS


def make_run(seed: int = 1, **overrides) -> Run:
    kwargs = dict(
        quest=QUESTS[0],
        hp=BASE_MAX_HP,
        max_hp=BASE_MAX_HP,
        focus=BASE_MAX_FOCUS,
        max_focus=BASE_MAX_FOCUS,
        potions=POTION_CHARGES,
        rng=random.Random(seed),
    )
    kwargs.update(overrides)
    run = Run(**kwargs)
    run.encounter = combat.spawn("kobold", run.rng)
    return run


def test_same_seed_replays_identically() -> None:
    """Persisting rng.getstate() is only worth doing if this holds."""
    a, b = make_run(seed=99), make_run(seed=99)
    for _ in range(5):
        combat.player_turn(a, "strike")
        combat.monster_turn(a)
        combat.player_turn(b, "strike")
        combat.monster_turn(b)
    assert (a.hp, a.encounter.hp, a.focus) == (b.hp, b.encounter.hp, b.focus)


def test_damage_is_never_zero() -> None:
    """A turn that does nothing reads as a bug to the player."""
    run = make_run(seed=5)
    for _ in range(50):
        before = run.encounter.hp
        combat.player_turn(run, "strike")
        assert run.encounter.hp < before
        run.encounter.hp = run.encounter.monster.max_hp


def test_fireball_ignores_armour() -> None:
    """Fireball's whole reason to exist. Compared against the armoured toad."""
    strikes, fireballs = [], []
    for seed in range(60):
        r = make_run(seed=seed)
        r.encounter = combat.spawn("mire_toad", r.rng)
        before = r.encounter.hp
        combat.player_turn(r, "strike")
        strikes.append(before - r.encounter.hp)

        r = make_run(seed=seed)
        r.encounter = combat.spawn("mire_toad", r.rng)
        before = r.encounter.hp
        combat.player_turn(r, "fireball")
        fireballs.append(before - r.encounter.hp)

    assert sum(fireballs) / len(fireballs) > sum(strikes) / len(strikes)


def test_fireball_costs_focus() -> None:
    run = make_run()
    before = run.focus
    combat.player_turn(run, "fireball")
    assert run.focus == before - combat.FIREBALL_COST


def test_fireball_is_illegal_without_focus() -> None:
    run = make_run(focus=0)
    ok, why = combat.action_is_legal(run, "fireball")
    assert not ok and "focus" in why.lower()


def test_guard_reduces_incoming_damage() -> None:
    """Averaged over seeds — a single roll can overlap by variance."""
    guarded, unguarded = [], []
    for seed in range(60):
        r = make_run(seed=seed)
        r.guard_active = True
        before = r.hp
        combat.monster_turn(r)
        guarded.append(before - r.hp)

        r = make_run(seed=seed)
        before = r.hp
        combat.monster_turn(r)
        unguarded.append(before - r.hp)

    assert sum(guarded) / len(guarded) < sum(unguarded) / len(unguarded)


def test_guard_grants_focus() -> None:
    run = make_run(focus=0)
    combat.player_turn(run, "guard")
    assert run.focus == combat.GUARD_FOCUS_GAIN


def test_focus_never_exceeds_max() -> None:
    run = make_run(focus=BASE_MAX_FOCUS)
    combat.player_turn(run, "guard")
    assert run.focus <= run.max_focus


def test_potion_never_overheals() -> None:
    run = make_run(hp=BASE_MAX_HP - 1)
    combat.player_turn(run, "potion")
    assert run.hp == BASE_MAX_HP
    assert run.potions == POTION_CHARGES - 1


def test_potion_is_illegal_when_empty() -> None:
    run = make_run(potions=0)
    ok, why = combat.action_is_legal(run, "potion")
    assert not ok and "potion" in why.lower()


def test_monster_always_telegraphs_its_next_move() -> None:
    """Guard has to be a read, not a coinflip — so a telegraph must always exist."""
    run = make_run(seed=11)
    for _ in range(40):
        assert run.encounter.next_move.telegraph
        combat.monster_turn(run)
        run.hp = BASE_MAX_HP


def test_drain_heals_the_monster() -> None:
    run = make_run(seed=3)
    run.encounter = combat.spawn("wight", run.rng)
    run.encounter.hp = 10
    drain = next(m for m in run.encounter.monster.moves if m.kind == "drain")
    run.encounter.next_move = drain
    combat.monster_turn(run)
    assert run.encounter.hp > 10


def test_monster_guard_blunts_the_next_player_hit() -> None:
    blunted, normal = [], []
    for seed in range(60):
        r = make_run(seed=seed)
        r.encounter.guarding = True
        before = r.encounter.hp
        combat.player_turn(r, "strike")
        blunted.append(before - r.encounter.hp)

        r = make_run(seed=seed)
        before = r.encounter.hp
        combat.player_turn(r, "strike")
        normal.append(before - r.encounter.hp)

    assert sum(blunted) / len(blunted) < sum(normal) / len(normal)


def test_monster_guard_expires_after_one_hit() -> None:
    run = make_run()
    run.encounter.guarding = True
    combat.player_turn(run, "strike")
    assert not run.encounter.guarding


@pytest.mark.parametrize("key", sorted(MONSTERS))
def test_every_monster_is_well_formed(key: str) -> None:
    m = MONSTERS[key]
    assert m.max_hp > 0 and m.power > 0 and m.armor >= 0
    assert m.moves, f"{key} has no moves"
    assert all(mv.telegraph for mv in m.moves), f"{key} has an untelegraphed move"


@pytest.mark.parametrize("quest", QUESTS, ids=lambda q: q.key)
def test_every_quest_references_real_monsters(quest) -> None:
    assert quest.pool, f"{quest.key} has an empty pool"
    for key in quest.pool:
        assert key in MONSTERS, f"{quest.key} references unknown monster {key}"
    assert quest.stages > 0 and quest.gold > 0 and quest.renown > 0


def test_hp_bar_stays_in_bounds() -> None:
    for cur in (-5, 0, 7, 34, 999):
        bar = combat.hp_bar(cur, 34)
        assert len(bar) == 10
