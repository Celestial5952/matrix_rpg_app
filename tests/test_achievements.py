"""Achievements, titles and the leaderboard: cosmetic, account-level, permanent."""

from __future__ import annotations

import json

from core import achievements
from core.achievements import ACHIEVEMENTS, ACHIEVEMENTS_BY_KEY, bump, sweep
from core.game import handle
from core.guild import Guild
from core.persist import load_all, save_all
from core.state import Player, Tombstone

from .conftest import clear_contract, make_char, make_player


def tier3_quest() -> str:
    from core.content import QUESTS
    return next(q.key for q in QUESTS if q.tier == 3 and not q.story)


# --- the table -------------------------------------------------------------

def test_keys_and_titles_are_unique_and_titles_fit_after_a_name():
    keys = [a.key for a in ACHIEVEMENTS]
    titles = [a.title for a in ACHIEVEMENTS]
    assert len(set(keys)) == len(keys)
    assert len(set(titles)) == len(titles)
    assert all(a.title and a.hint and a.name for a in ACHIEVEMENTS)


def test_nothing_is_earned_by_a_new_player():
    assert sweep(make_player()) == []


# --- unlocking -------------------------------------------------------------

def test_the_first_cleared_contract_unlocks_the_first_achievement():
    player = make_char()
    clear_contract(player, Guild())
    assert player.stats["contracts"] == 1
    assert "first_contract" in player.achievements


def test_the_unlock_is_announced_once_in_the_reply():
    player = make_char()
    bump(player, "contracts")
    reply = "\n".join(handle(player, "!status"))
    assert "Achievement unlocked: Paperwork Filed" in reply
    assert "Achievement unlocked" not in "\n".join(handle(player, "!status"))


def test_sweep_is_idempotent_and_keeps_the_first_unlock_time():
    player = make_player()
    bump(player, "contracts", 10)
    assert len(sweep(player, now=100.0)) >= 2
    assert sweep(player, now=999.0) == []
    assert player.achievements["seasoned"] == 100.0


def test_tier_three_clears_are_counted_separately():
    player = make_char()
    clear_contract(player, Guild(), tier3_quest())
    assert player.stats["tier3"] == 1 and player.stats["tier2"] == 1
    assert "into_the_deep" in player.achievements


def test_a_nearly_dead_finish_counts_as_a_close_call():
    player = make_player()
    from core.game import _record_completion
    _record_completion(player, 1, adventure=False, hp_left=3)
    _record_completion(player, 1, adventure=False, hp_left=0)  # downed, not close
    assert player.stats["close_calls"] == 1
    sweep(player)
    assert "hairs_breadth" in player.achievements


def test_deaths_count_from_the_graveyard():
    player = make_player()
    for i in range(3):
        player.graveyard.append(Tombstone("x", "Elf", "Wizard", 1, 0, str(i)))
    sweep(player)
    assert "old_friend" in player.achievements


def test_gold_and_dress_achievements_read_the_live_character():
    player = make_char()
    player.character.gold = 500
    player.character.equipment = {"weapon": "ratcatchers_cudgel",
                                  "armour": "quilted_jerkin",
                                  "charm": "lucky_button"}
    sweep(player)
    assert {"dragon_on_a_budget", "dressed_for_it"} <= set(player.achievements)


def test_portals_and_duels_feed_their_counters():
    player = make_char()
    handle(player, "!board")
    handle(player, "!accept 1")
    handle(player, "!portal")
    assert player.stats["portals"] == 1


def test_achievements_outlive_the_character():
    player = make_char()
    bump(player, "contracts")
    sweep(player)
    player.character = None
    assert "first_contract" in player.achievements


# --- titles ----------------------------------------------------------------

def unlocked(n: int = 10):
    player = make_char()
    bump(player, "contracts", n)
    sweep(player)
    return player


def test_wearing_a_title_shows_it_on_status_and_the_roster():
    player = unlocked()
    reply = "\n".join(handle(player, "!title Seasoned"))
    assert "the Seasoned" in reply
    assert "the Seasoned" in "\n".join(handle(player, "!status"))
    assert "the Seasoned" in "\n".join(
        handle(player, "!who", {player.mxid: player}))


def test_a_title_can_be_chosen_by_number_and_cleared():
    player = unlocked()
    idx = [a.key for a in ACHIEVEMENTS].index("seasoned") + 1
    handle(player, f"!title {idx}")
    assert player.title_key == "seasoned"
    handle(player, "!title none")
    assert player.title_key == ""
    assert achievements.title_of(player) == ""


def test_you_cannot_wear_a_title_you_have_not_earned():
    player = make_char()
    reply = "\n".join(handle(player, "!title Seasoned"))
    assert "isn't yours yet" in reply
    assert player.title_key == ""


def test_an_unknown_title_shows_the_list():
    player = unlocked()
    reply = "\n".join(handle(player, "!title wombat"))
    assert "No achievement matches" in reply and "Achievements" in reply


def test_a_stale_title_key_is_ignored():
    player = make_player()
    player.title_key = "seasoned"           # never unlocked
    assert achievements.title_of(player) == ""
    player.title_key = "removed_from_game"
    assert achievements.title_of(player) == ""


def test_the_titles_screen_marks_locked_and_worn():
    player = unlocked()
    handle(player, "!title Seasoned")
    screen = "\n".join(handle(player, "!titles"))
    assert "wearing" in screen and "🔒" in screen and "✅" in screen


def test_titles_work_without_a_character():
    player = make_player()
    assert "Achievements" in "\n".join(handle(player, "!titles"))


# --- leaderboard -----------------------------------------------------------

def crowd():
    a = make_char(name="Doc", mxid="@a:srv")
    b = make_char(name="Wren", char_class="ranger", mxid="@b:srv")
    c = make_char(name="Ash", mxid="@c:srv")
    a.character.renown, b.character.renown, c.character.renown = 30, 90, 10
    a.character.runs_completed = 9
    b.character.duels_won = 2
    return a, b, c, {p.mxid: p for p in (a, b, c)}


def test_the_leaderboard_ranks_by_renown_with_medals():
    a, b, c, roster = crowd()
    board = "\n".join(handle(a, "!top", roster))
    assert board.index("Wren") < board.index("Doc") < board.index("Ash")
    assert "🥇" in board and "🥈" in board and "🥉" in board


def test_the_leaderboard_hides_categories_nobody_scores_in():
    a, b, c, roster = crowd()
    board = "\n".join(handle(a, "!top", roster))
    assert "Duellists" in board
    assert "Deep delvers" not in board, "nobody has a tier 3 clear"


def test_the_dead_have_a_hall_of_fame():
    a, b, c, roster = crowd()
    a.graveyard.append(Tombstone("Old Bram", "Dwarf", "Fighter", 250, 12, "Wight"))
    board = "\n".join(handle(b, "!top", roster))
    assert "Hall of fame" in board and "Old Bram" in board


def test_leaderboard_is_capped_at_five_rows():
    roster = {}
    for i in range(8):
        p = make_char(name=f"Hero{i}", mxid=f"@h{i}:srv")
        p.character.renown = 10 + i
        roster[p.mxid] = p
    board = handle(roster["@h0:srv"], "!top", roster)
    renown = next(i for i, ln in enumerate(board) if "Renown" in ln)
    rows = [ln for ln in board[renown + 1:] if ln.startswith("  ")]
    assert len(rows) == 5


def test_leaderboard_works_for_someone_with_no_character():
    a, b, c, roster = crowd()
    ghost = make_player("@g:srv")
    assert "Leaderboard" in "\n".join(handle(ghost, "!top", roster))


def test_an_empty_hall_gets_a_joke_not_a_crash():
    ghost = make_player()
    assert handle(ghost, "!top", {}) is not None
    assert handle(ghost, "!top", {ghost.mxid: ghost}) is not None


# --- persistence -----------------------------------------------------------

def test_stats_titles_and_unlocks_survive_a_restart(tmp_path):
    path = tmp_path / "players.json"
    player = unlocked()
    handle(player, "!title Seasoned")
    save_all(path, {player.mxid: player})

    loaded = load_all(path)[player.mxid]
    assert loaded.stats == player.stats
    assert loaded.achievements == player.achievements
    assert loaded.title_key == "seasoned"


def test_corrupt_progress_fields_are_sanitised(tmp_path):
    path = tmp_path / "players.json"
    player = unlocked()
    save_all(path, {player.mxid: player})
    raw = json.loads(path.read_text())
    row = raw[player.mxid]
    row["stats"] = {"contracts": "lots", "tier3": -4, "ok": 3, "flag": True}
    row["achievements"] = {"seasoned": "yesterday", "ghost_ach": 5.0,
                           "first_contract": 12.5}
    row["title_key"] = 17
    path.write_text(json.dumps(raw))

    loaded = load_all(path)[player.mxid]
    assert loaded.stats == {"ok": 3}
    assert loaded.achievements == {"first_contract": 12.5}
    assert loaded.title_key == ""


def test_old_saves_without_progress_fields_load(tmp_path):
    path = tmp_path / "players.json"
    player = make_char()
    save_all(path, {player.mxid: player})
    raw = json.loads(path.read_text())
    for key in ("stats", "achievements", "title_key"):
        del raw[player.mxid][key]
    path.write_text(json.dumps(raw))
    loaded = load_all(path)[player.mxid]
    assert (loaded.stats, loaded.achievements, loaded.title_key) == ({}, {}, "")


def test_every_listed_key_is_known():
    assert set(ACHIEVEMENTS_BY_KEY) == {a.key for a in ACHIEVEMENTS}
    assert isinstance(make_player(), Player)


# --- parties ---------------------------------------------------------------

def test_a_party_victory_counts_for_every_member_and_the_bounty_once():
    from core import bounty
    from core.party import Parties

    from .test_party import form, start, table, turn

    roster, guild, parties = table("Doc", "Wren")
    bounty.refresh(guild)
    guild.bounty_key, guild.bounty_goal = "any", 99
    form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)
    party = parties.for_member("@p0:srv")
    assert isinstance(parties, Parties)

    for _ in range(800):
        if party.contract is None:
            break
        actor = turn(party, roster)
        for p in roster.values():
            p.character.run.hp = p.character.run.max_hp
        handle(roster[actor], "!1", roster, guild, parties)

    assert party.contract is None, "the party never finished"
    assert all(p.stats.get("contracts") == 1 for p in roster.values())
    assert guild.bounty_progress == 1, "one contract, however many were in it"


def test_a_worn_title_does_not_stack_two_thes_on_the_sheet():
    player = unlocked()
    handle(player, "!title Seasoned")
    header = handle(player, "!status")[0]
    assert "Tester the Seasoned, Human Fighter" in header
    assert "Fighter the" not in header
