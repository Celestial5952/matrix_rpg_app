"""Shared guild progress and giving items to other players."""

from __future__ import annotations

import json

from core.guild import GUILD_TIERS, Guild, contribution
from core.game import handle
from core.persist import load_guild, save_guild

from .conftest import make_char


def two_players(guild: Guild | None = None):
    a = make_char(name="Doc", mxid="@a:srv")
    b = make_char(name="Wren", char_class="ranger", mxid="@b:srv")
    return a, b, {a.mxid: a, b.mxid: b}


# --- tiers -----------------------------------------------------------------

def test_tiers_are_ordered_and_start_at_zero():
    assert GUILD_TIERS[0].renown == 0
    thresholds = [t.renown for t in GUILD_TIERS]
    assert thresholds == sorted(thresholds)


def test_perks_never_go_backwards():
    """A guild getting bigger must never make anything worse."""
    for earlier, later in zip(GUILD_TIERS, GUILD_TIERS[1:]):
        assert later.starting_gold >= earlier.starting_gold
        assert later.board_size >= earlier.board_size
        assert later.scroll_bonus >= earlier.scroll_bonus


def test_tier_tracks_renown():
    guild = Guild()
    assert guild.tier is GUILD_TIERS[0]
    guild.renown = GUILD_TIERS[1].renown
    assert guild.tier is GUILD_TIERS[1]
    guild.renown = 10 ** 9
    assert guild.tier is GUILD_TIERS[-1]
    assert guild.next_tier is None
    assert guild.renown_to_next is None


def test_contribution_is_a_share_and_adventures_pay_more():
    assert contribution(20) < 20, "the guild takes a share, not the lot"
    assert contribution(20, adventure=True) > contribution(20)
    assert contribution(1) >= 1, "a contract should always be worth something"


# --- perks reach the game --------------------------------------------------

def test_a_richer_guild_starts_new_characters_with_gold():
    poor = Guild(renown=0)
    rich = Guild(renown=GUILD_TIERS[-1].renown)

    from core.state import Player
    for guild, expected in ((poor, GUILD_TIERS[0].starting_gold),
                            (rich, GUILD_TIERS[-1].starting_gold)):
        player = Player(mxid="@n:srv", display_name="N")
        for line in ("!create", "!Newbie", "!human", "!fighter"):
            handle(player, line, None, guild)
        assert player.character.gold == expected


def test_a_richer_guild_posts_a_wider_board():
    from core.state import Player

    boards = {}
    for guild in (Guild(renown=0), Guild(renown=GUILD_TIERS[-1].renown)):
        player = Player(mxid="@n:srv", display_name="N")
        for line in ("!create", "!Newbie", "!human", "!fighter"):
            handle(player, line, None, guild)
        handle(player, "!board", None, guild)
        boards[guild.tier.board_size] = len(player.character.board)

    assert boards[GUILD_TIERS[-1].board_size] > boards[GUILD_TIERS[0].board_size]


def test_completing_a_contract_feeds_the_guild():
    guild = Guild()
    player = make_char()
    char = player.character
    handle(player, "!board", None, guild)
    handle(player, "!accept 1", None, guild)

    for _ in range(400):
        if char.run is None or player.character is None:
            break
        char.run.hp = char.run.max_hp
        handle(player, "!1", None, guild)

    assert guild.renown > 0
    assert guild.contracts_completed == 1


def test_guild_renown_survives_a_death():
    """The whole point: a wipe cannot undo what the guild has banked."""
    guild = Guild(renown=300)
    player = make_char()

    for _ in range(80):
        if player.character is None:
            break
        if player.character.run is None:
            handle(player, "!board", None, guild)
            handle(player, "!accept 1", None, guild)
        player.character.run.hp = 1
        handle(player, "!1", None, guild)

    assert player.character is None
    assert guild.renown >= 300


def test_guild_command_reports_the_charter():
    guild = Guild(renown=GUILD_TIERS[2].renown)
    player = make_char()
    text = " ".join(handle(player, "!guild", None, guild))
    assert GUILD_TIERS[2].name in text
    assert "renown" in text.lower()


def test_who_and_guild_are_different_commands():
    a, _b, roster = two_players()
    guild = Guild(renown=100)
    who = " ".join(handle(a, "!who", roster, guild))
    charter = " ".join(handle(a, "!guild", roster, guild))
    assert "Wren" in who
    assert "charter" in charter.lower() or "guild renown" in charter.lower()


# --- persistence -----------------------------------------------------------

def test_guild_round_trips(tmp_path):
    path = tmp_path / "guild.json"
    save_guild(path, Guild(renown=742, contracts_completed=30,
                           adventures_completed=3, members=2))
    loaded = load_guild(path)
    assert (loaded.renown, loaded.contracts_completed,
            loaded.adventures_completed) == (742, 30, 3)


def test_a_missing_guild_file_starts_a_charter(tmp_path):
    assert load_guild(tmp_path / "nope.json").renown == 0


def test_a_corrupt_guild_file_does_not_raise(tmp_path):
    path = tmp_path / "guild.json"
    path.write_text("{not json")
    assert load_guild(path).renown == 0
    assert path.with_suffix(".json.corrupt").exists()


def test_nonsense_guild_values_are_ignored(tmp_path):
    path = tmp_path / "guild.json"
    path.write_text(json.dumps({"renown": -50, "contracts_completed": "lots"}))
    guild = load_guild(path)
    assert guild.renown == 0 and guild.contracts_completed == 0


# --- giving ----------------------------------------------------------------

def test_giving_moves_an_item_between_players():
    a, b, roster = two_players()
    a.character.inventory["greater_potion"] = 2

    reply = handle(a, "!give wren greater", roster)
    assert a.character.inventory["greater_potion"] == 1
    assert b.character.inventory["greater_potion"] == 1
    assert "Wren" in " ".join(reply)


def test_giving_a_scroll_is_the_point():
    a, b, roster = two_players()
    a.character.inventory["scroll_sunless_ziggurat"] = 1

    handle(a, "!give wren scroll", roster)
    assert "scroll_sunless_ziggurat" not in a.character.inventory
    assert b.character.inventory["scroll_sunless_ziggurat"] == 1


def test_giving_warns_when_the_recipient_is_too_low_level():
    a, b, roster = two_players()
    a.character.inventory["scroll_sunless_ziggurat"] = 1
    reply = handle(a, "!give wren scroll", roster)
    assert "needs" in " ".join(reply).lower()
    assert b.character.inventory["scroll_sunless_ziggurat"] == 1, "still given"


def test_you_cannot_give_to_yourself():
    a, _b, roster = two_players()
    a.character.inventory["greater_potion"] = 1
    reply = handle(a, "!give doc greater", roster)
    assert "Nobody here" in reply[0]
    assert a.character.inventory["greater_potion"] == 1


def test_you_cannot_give_what_you_do_not_have():
    a, _b, roster = two_players()
    reply = handle(a, "!give wren potion", roster)
    assert "empty" in " ".join(reply).lower()


def test_you_cannot_give_to_someone_with_no_character():
    from core.state import Player

    a, _b, roster = two_players()
    ghost = Player(mxid="@ghost:srv", display_name="Ghost")
    roster[ghost.mxid] = ghost
    a.character.inventory["greater_potion"] = 1

    reply = handle(a, "!give ghost greater", roster)
    assert "Nobody here" in reply[0]
    assert a.character.inventory["greater_potion"] == 1


def test_giving_is_refused_mid_fight():
    a, _b, roster = two_players()
    a.character.inventory["greater_potion"] = 1
    handle(a, "!board")
    handle(a, "!accept 1")

    reply = handle(a, "!give wren greater", roster)
    assert "mid-fight" in " ".join(reply).lower()
    assert a.character.inventory["greater_potion"] == 1


def test_an_ambiguous_recipient_asks_which():
    from core.state import Player

    a, _b, roster = two_players()
    for mxid, name in (("@c:srv", "Wrenley"), ("@d:srv", "Wrendal")):
        other = Player(mxid=mxid, display_name=name)
        for line in ("!create", f"!{name}", "!human", "!fighter"):
            handle(other, line)
        roster[mxid] = other

    a.character.inventory["greater_potion"] = 1

    # "wren" is an exact match for Wren, so it must NOT be ambiguous --
    # an exact name always beats a prefix.
    handle(a, "!give wren greater", roster)
    assert a.character.inventory.get("greater_potion", 0) == 0

    # "wr" matches all three by prefix and none exactly.
    a.character.inventory["greater_potion"] = 1
    reply = handle(a, "!give wr greater", roster)
    assert "Which one" in reply[0]
    assert a.character.inventory["greater_potion"] == 1
