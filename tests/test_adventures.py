"""Scroll-delivered adventures: authored sequences, gating, and rewards."""

from __future__ import annotations

import random

from core.adventures import ADVENTURES, contract_for, scroll_for
from core.chargen import LEVEL_RENOWN
from core.content import MONSTERS
from core.game import handle
from core.items import ITEMS, SCROLL_KEYS, SCROLL_CHANCE, roll_loot
from core.persist import load_all, save_all

from .conftest import make_char

ADVENTURE = ADVENTURES["sunless_ziggurat"]


def explorer(level: int = 5, char_class: str = "fighter", scrolls: int = 1):
    player = make_char(char_class=char_class)
    char = player.character
    char.renown = LEVEL_RENOWN[level - 1]
    if scrolls:
        char.inventory[scroll_for(ADVENTURE.key)] = scrolls
    return player


# --- the content itself ----------------------------------------------------

def test_every_adventure_is_well_formed():
    for key, adventure in ADVENTURES.items():
        assert adventure.key == key
        assert adventure.chapters, f"{key} has no chapters"
        assert adventure.intro and adventure.epilogue
        assert adventure.gold > 0 and adventure.renown > 0
        assert adventure.min_level >= 1


def test_every_chapter_names_a_real_monster():
    for adventure in ADVENTURES.values():
        for i, chapter in enumerate(adventure.chapters, 1):
            assert chapter.monster in MONSTERS, f"{adventure.key} ch{i}"
            assert chapter.beat, f"{adventure.key} ch{i} has no story"


def test_every_reward_is_a_real_item():
    for adventure in ADVENTURES.values():
        for key in adventure.rewards:
            assert key in ITEMS, f"{adventure.key} rewards unknown {key}"


def test_each_adventure_has_exactly_one_scroll():
    for key, adventure in ADVENTURES.items():
        scroll = ITEMS.get(scroll_for(key))
        assert scroll is not None, f"{key} has no scroll"
        assert scroll.kind == "scroll"
        assert scroll.adventure == key
        assert scroll.name == adventure.scroll_name


def test_scrolls_are_not_purchasable():
    """A scroll is something you find, not something you shop for."""
    from core.items import SHOP_STOCK

    for key in SCROLL_KEYS:
        assert key not in SHOP_STOCK


# --- drops -----------------------------------------------------------------

def test_tier_one_never_drops_a_scroll():
    rng = random.Random(4)
    for _ in range(3000):
        assert not any(k in SCROLL_KEYS for k in roll_loot(1, rng))


def test_scroll_drop_rate_is_roughly_as_configured():
    for tier, expected in SCROLL_CHANCE.items():
        rng = random.Random(11)
        n = 4000
        hits = sum(1 for _ in range(n)
                   if any(k in SCROLL_KEYS for k in roll_loot(tier, rng)))
        rate = hits / n
        assert expected * 0.6 < rate < expected * 1.6, f"tier {tier}: {rate:.3f}"


# --- using a scroll --------------------------------------------------------

def test_a_scroll_below_the_level_gate_is_refused_and_not_consumed():
    player = explorer(level=1)
    reply = handle(player, "!use scroll")
    assert "needs level" in " ".join(reply)
    assert player.character.inventory[scroll_for(ADVENTURE.key)] == 1
    assert player.character.run is None


def test_a_scroll_starts_the_adventure_and_is_consumed():
    player = explorer(level=ADVENTURE.min_level)
    reply = handle(player, "!use scroll")
    char = player.character

    assert char.run is not None
    assert char.run.quest.is_adventure
    assert char.run.quest.stages == ADVENTURE.length
    assert scroll_for(ADVENTURE.key) not in char.inventory
    assert ADVENTURE.title in " ".join(reply)
    assert ADVENTURE.chapters[0].beat in " ".join(reply)


def test_a_scroll_cannot_be_read_mid_fight():
    player = explorer(level=5, scrolls=2)
    handle(player, "!board")
    handle(player, "!accept 1")
    reply = handle(player, "!use scroll")
    assert "mid-fight" in " ".join(reply).lower()
    assert player.character.inventory[scroll_for(ADVENTURE.key)] == 2


def test_ordinary_items_still_cannot_be_used_in_the_hall():
    player = explorer(level=5)
    player.character.gold = 50
    handle(player, "!buy 1")
    reply = handle(player, "!use lesser")
    assert "guild hall" in " ".join(reply).lower()
    assert player.character.inventory["lesser_potion"] == 1


# --- the sequence ----------------------------------------------------------

def test_chapters_are_fought_in_the_authored_order():
    player = explorer(level=8)
    handle(player, "!use scroll")
    char = player.character

    seen = []
    for _ in range(4000):
        if char.run is None or player.character is None:
            break
        char.run.hp = char.run.max_hp  # immortal: we're testing the sequence
        if char.run.pending_event:
            handle(player, "!1")       # a decision, not a fight
            continue
        seen.append((char.run.stage, char.run.encounter.monster.key))
        handle(player, "!1")

    order = []
    for stage, monster in seen:
        if not order or order[-1][0] != stage:
            order.append((stage, monster))
    assert [m for _, m in order] == [c.monster for c in ADVENTURE.chapters]


def test_a_rest_chapter_restores_health():
    """Tested directly: arriving at a specific chapter through ten fights is
    a much less reliable way to assert one line of arithmetic."""
    from core.game import _chapter_opening

    rested = [i for i, c in enumerate(ADVENTURE.chapters) if c.rest]
    assert rested, "this adventure has no breathers"

    player = explorer(level=8)
    handle(player, "!use scroll")
    run = player.character.run
    chapter = rested[0]

    run.hp = 1
    lines = _chapter_opening(run, chapter)
    assert run.hp == 1 + ADVENTURE.chapters[chapter].rest
    assert any("HP" in line for line in lines)


def test_a_rest_never_overheals():
    from core.game import _chapter_opening

    chapter = next(i for i, c in enumerate(ADVENTURE.chapters) if c.rest)
    player = explorer(level=8)
    handle(player, "!use scroll")
    run = player.character.run

    run.hp = run.max_hp
    _chapter_opening(run, chapter)
    assert run.hp == run.max_hp


def test_finishing_pays_the_authored_rewards():
    player = explorer(level=8)
    char = player.character
    char.gold, char.renown = 0, LEVEL_RENOWN[7]
    before_renown = char.renown
    handle(player, "!use scroll")

    lines = []
    for _ in range(4000):
        if char.run is None or player.character is None:
            break
        char.run.hp = char.run.max_hp
        lines = handle(player, "!1") or lines

    assert char.gold >= ADVENTURE.gold
    assert char.renown >= before_renown + ADVENTURE.renown
    for key in ADVENTURE.rewards:
        assert key in char.inventory
    assert ADVENTURE.epilogue in " ".join(lines)


def test_bailing_out_of_an_adventure_loses_the_scroll():
    """The scroll is spent on arrival — portalling out does not refund it."""
    player = explorer(level=5)
    handle(player, "!use scroll")
    handle(player, "!portal")
    char = player.character
    assert char.run is None
    assert scroll_for(ADVENTURE.key) not in char.inventory


# --- persistence -----------------------------------------------------------

def test_an_adventure_survives_a_restart(tmp_path):
    path = tmp_path / "players.json"
    player = explorer(level=6)
    handle(player, "!use scroll")
    handle(player, "!1")
    char = player.character

    save_all(path, {player.mxid: player})
    loaded = load_all(path)[player.mxid].character

    assert loaded.run is not None
    assert loaded.run.quest.is_adventure
    assert loaded.run.quest.adventure_key == ADVENTURE.key
    assert len(loaded.run.quest.chapters) == ADVENTURE.length
    assert loaded.run.stage == char.run.stage
    assert loaded.run.encounter.monster.key == char.run.encounter.monster.key


def test_a_run_in_a_deleted_adventure_is_dropped_not_fatal(tmp_path):
    import json

    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@a:srv": {"display_name": "A", "character": {
            "name": "X", "race_key": "elf", "class_key": "rogue",
            "run": {"quest": {"adventure": "an_adventure_that_was_removed"}},
        }},
    }))
    char = load_all(path)["@a:srv"].character
    assert char is not None and char.run is None


def test_contract_for_is_stable():
    a, b = contract_for(ADVENTURE), contract_for(ADVENTURE)
    assert a.chapters == b.chapters
    assert (a.gold, a.renown, a.stages) == (b.gold, b.renown, b.stages)
