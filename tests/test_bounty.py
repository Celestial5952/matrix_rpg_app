"""The guild's daily bounty: shared, deterministic by date, and claimed once."""

from __future__ import annotations

import json

from core import bounty
from core.bounty import BOUNTIES, BOUNTIES_BY_KEY, goal_for, record, refresh
from core.game import handle
from core.guild import GUILD_TIERS, Guild
from core.persist import load_guild, save_guild

from .conftest import clear_contract, make_char

DAY1 = 1_800_000_000.0           # an arbitrary instant
DAY2 = DAY1 + 86400


def with_bounty(key: str, goal: int = 3, members: int = 1) -> Guild:
    guild = Guild(members=members)
    refresh(guild, DAY1)
    guild.bounty_key = key
    guild.bounty_goal = goal
    return guild


# --- rolling ---------------------------------------------------------------

def test_the_same_day_always_gives_the_same_bounty():
    picks = {refresh(Guild(), DAY1).key for _ in range(20)}
    assert len(picks) == 1


def test_refresh_does_not_reset_progress_within_a_day():
    guild = with_bounty("any")
    guild.bounty_progress = 2
    refresh(guild, DAY1 + 3600)
    assert (guild.bounty_key, guild.bounty_progress) == ("any", 2)


def test_a_new_day_posts_a_fresh_bounty_and_clears_progress():
    guild = with_bounty("any")
    guild.bounty_progress = 2
    guild.bounty_claimed = 1
    refresh(guild, DAY2)
    assert guild.bounty_day == bounty.utc_day(DAY2)
    assert guild.bounty_progress == 0 and guild.bounty_claimed == 0


def test_days_do_vary_over_a_month():
    keys = {refresh(Guild(), DAY1 + i * 86400).key for i in range(60)}
    assert len(keys) > 1


def test_an_unknown_saved_key_is_replaced():
    guild = Guild(bounty_day=bounty.utc_day(DAY1), bounty_key="removed")
    assert refresh(guild, DAY1).key in BOUNTIES_BY_KEY


def test_goals_scale_with_the_guild_but_are_capped():
    base = BOUNTIES[0]
    assert goal_for(base, 1) == base.goal
    assert goal_for(base, 5) > goal_for(base, 2) > base.goal
    assert goal_for(base, 500) == round(base.goal * bounty.MAX_GOAL_MULTIPLIER)
    assert goal_for(base, 0) == base.goal


def test_the_goal_is_fixed_when_the_day_rolls():
    guild = Guild(members=1)
    refresh(guild, DAY1)
    goal = guild.bounty_goal
    guild.members = 12
    refresh(guild, DAY1 + 60)
    assert guild.bounty_goal == goal


# --- progress --------------------------------------------------------------

def test_matching_completions_advance_it_and_say_so():
    guild = with_bounty("any", goal=3)
    lines = record(guild, 1, now=DAY1)
    assert guild.bounty_progress == 1
    assert "1/3" in "\n".join(lines)


def test_only_matching_contracts_count():
    guild = with_bounty("tier3", goal=2)
    assert record(guild, 1, now=DAY1) == []
    assert record(guild, 2, now=DAY1) == []
    assert guild.bounty_progress == 0
    record(guild, 3, now=DAY1)
    assert guild.bounty_progress == 1

    guild = with_bounty("tier2", goal=2)
    record(guild, 1, now=DAY1)
    record(guild, 2, now=DAY1)
    record(guild, 3, now=DAY1)
    assert guild.bounty_progress == 2, "tiers 2 and 3 both count, tier 1 doesn't"
    assert guild.bounty_claimed == 1

    guild = with_bounty("adventure", goal=1)
    assert record(guild, 3, adventure=False, now=DAY1) == []
    assert record(guild, 3, adventure=True, now=DAY1)


def test_meeting_the_goal_pays_guild_renown_once():
    guild = with_bounty("any", goal=2)
    guild.renown = 5
    record(guild, 1, now=DAY1)
    lines = record(guild, 1, now=DAY1)

    reward = bounty.reward_for(BOUNTIES_BY_KEY["any"], guild)
    assert guild.renown == 5 + reward
    assert guild.bounty_claimed == 1
    assert "BOUNTY CLAIMED" in "\n".join(lines)

    assert record(guild, 1, now=DAY1) == [], "no second payout"
    assert guild.renown == 5 + reward


def test_the_reward_can_level_the_guild_up_and_says_so():
    guild = with_bounty("any", goal=1)
    guild.renown = GUILD_TIERS[1].renown - 1
    lines = "\n".join(record(guild, 1, now=DAY1))
    assert guild.level == 2
    assert "THE GUILD IS NOW" in lines


def test_a_higher_guild_level_pays_more():
    low, high = Guild(), Guild(renown=GUILD_TIERS[-1].renown)
    b = BOUNTIES[0]
    assert bounty.reward_for(b, high) > bounty.reward_for(b, low)


# --- through the real game -------------------------------------------------

def test_clearing_a_contract_moves_the_bounty():
    guild = Guild()
    player = make_char()
    refresh(guild)
    guild.bounty_key = "any"
    guild.bounty_goal = 99
    clear_contract(player, guild)
    assert guild.bounty_progress == 1


def test_the_bounty_command_and_guild_screen_show_it():
    guild = Guild()
    player = make_char()
    assert "Bounty of the day" in "\n".join(handle(player, "!bounty", None, guild))
    assert "Bounty of the day" in "\n".join(handle(player, "!guild", None, guild))


def test_a_claimed_bounty_says_when_the_next_one_comes():
    guild = with_bounty("any", goal=1)
    record(guild, 1, now=DAY1)
    screen = "\n".join(bounty.render(guild, DAY1))
    assert "Claimed" in screen and "A new one is posted in" in screen


def test_bounty_without_a_guild_is_polite():
    player = make_char()
    assert handle(player, "!bounty") is not None


def test_seconds_until_reset_is_bounded_by_a_day():
    assert 0 < bounty.seconds_until_reset(DAY1) <= 86400
    assert bounty.seconds_until_reset(0) == 86400


# --- persistence -----------------------------------------------------------

def test_bounty_state_survives_a_restart(tmp_path):
    path = tmp_path / "guild.json"
    guild = with_bounty("tier2", goal=4, members=3)
    guild.bounty_progress = 2
    guild.bounty_claimed = 0
    save_guild(path, guild)

    loaded = load_guild(path)
    for field in ("bounty_day", "bounty_key", "bounty_goal",
                  "bounty_progress", "bounty_claimed"):
        assert getattr(loaded, field) == getattr(guild, field), field


def test_a_guild_file_from_before_bounties_loads(tmp_path):
    path = tmp_path / "guild.json"
    path.write_text(json.dumps({"renown": 40, "contracts_completed": 5,
                                "adventures_completed": 1, "members": 2}))
    loaded = load_guild(path)
    assert loaded.renown == 40 and loaded.bounty_key == ""
    assert refresh(loaded, DAY1).key in BOUNTIES_BY_KEY


def test_garbage_bounty_fields_are_reset_not_trusted(tmp_path):
    path = tmp_path / "guild.json"
    path.write_text(json.dumps({"renown": 1, "bounty_day": 5, "bounty_key": None,
                                "bounty_goal": "x", "bounty_progress": -2,
                                "bounty_claimed": True}))
    loaded = load_guild(path)
    assert (loaded.bounty_day, loaded.bounty_key) == ("", "")
    assert (loaded.bounty_goal, loaded.bounty_progress,
            loaded.bounty_claimed) == (0, 0, 0)
