"""Operator commands: gated by the adapter's say-so, never by the message."""

from __future__ import annotations

from core.duel import Duels
from core.game import handle
from core.guild import Guild
from core.items import ITEMS
from core.party import Parties

from .conftest import make_char, make_player


def hall():
    boss = make_char(name="Boss", mxid="@boss:srv")
    doc = make_char(name="Doc", mxid="@doc:srv")
    roster = {boss.mxid: boss, doc.mxid: doc}
    return boss, doc, roster, Guild(), Parties(), Duels()


def admin(player, text, roster, guild, parties=None, duels=None, mentions=None):
    return "\n".join(handle(player, text, roster, guild, parties, duels,
                            mentions, False, True))


# --- gating ----------------------------------------------------------------

def test_non_admins_get_the_ordinary_unknown_command_reply():
    boss, doc, roster, guild, *_ = hall()
    reply = "\n".join(handle(doc, "!admin reset Boss", roster, guild))
    assert "I don't know `!admin`" in reply
    assert boss.character is not None


def test_admin_is_never_offered_as_a_suggestion():
    from core.game import COMMANDS
    assert "admin" not in COMMANDS


def test_admin_works_with_no_character_and_mid_creation():
    boss, doc, roster, guild, *_ = hall()
    nobody = make_player("@n:srv")
    assert "Operator commands" in admin(nobody, "!admin", roster, guild)
    handle(nobody, "!create")
    assert nobody.pending is not None
    assert "Operator commands" in admin(nobody, "!admin help", roster, guild)
    assert nobody.pending is not None, "the register is left alone"


def test_an_unknown_subcommand_shows_the_help():
    boss, doc, roster, guild, *_ = hall()
    reply = admin(boss, "!admin frobnicate", roster, guild)
    assert "Unknown operator command" in reply and "!admin reset" in reply


# --- players ---------------------------------------------------------------

def test_players_lists_everyone_with_ids():
    boss, doc, roster, guild, *_ = hall()
    reply = admin(boss, "!admin players", roster, guild)
    assert "@boss:srv" in reply and "@doc:srv" in reply and "Doc" in reply


# --- reset -----------------------------------------------------------------

def test_reset_deletes_the_character_without_a_tombstone():
    boss, doc, roster, guild, *_ = hall()
    reply = admin(boss, "!admin reset Doc", roster, guild)
    assert doc.character is None
    assert doc.graveyard == []
    assert "no tombstone" in reply
    assert "create" in "\n".join(handle(doc, "!board", roster, guild)).lower()


def test_reset_can_target_a_mention():
    boss, doc, roster, guild, *_ = hall()
    admin(boss, "!admin reset whoever", roster, guild, mentions=[doc.mxid])
    assert doc.character is None


def test_reset_needs_a_target_and_an_unambiguous_match():
    boss, doc, roster, guild, *_ = hall()
    assert "!admin reset <who>" in admin(boss, "!admin reset", roster, guild)
    assert "Nobody with a character" in admin(
        boss, "!admin reset wombat", roster, guild)
    assert doc.character is not None


def test_reset_refuses_someone_in_a_party():
    boss, doc, roster, guild, parties, duels = hall()
    handle(boss, "!party", roster, guild, parties, duels)
    handle(boss, "!invite Doc", roster, guild, parties, duels)
    handle(doc, "!join Boss", roster, guild, parties, duels)
    assert parties.for_member(doc.mxid)

    reply = admin(boss, "!admin reset Doc", roster, guild, parties, duels)
    assert "in a party" in reply
    assert doc.character is not None


def test_reset_refuses_someone_in_a_duel():
    boss, doc, roster, guild, parties, duels = hall()
    handle(boss, "!duel Doc", roster, guild, parties, duels)
    handle(doc, "!duel accept", roster, guild, parties, duels)
    reply = admin(boss, "!admin reset Doc", roster, guild, parties, duels)
    assert "in a duel" in reply
    assert doc.character is not None


# --- grant -----------------------------------------------------------------

def test_grant_gold_adds_and_a_minus_takes_but_never_below_zero():
    boss, doc, roster, guild, *_ = hall()
    doc.character.gold = 10
    admin(boss, "!admin grant Doc gold 50", roster, guild)
    assert doc.character.gold == 60
    admin(boss, "!admin grant Doc gold -25", roster, guild)
    assert doc.character.gold == 35
    admin(boss, "!admin grant Doc gold -9999", roster, guild)
    assert doc.character.gold == 0


def test_grant_gold_rejects_nonsense():
    boss, doc, roster, guild, *_ = hall()
    assert "whole number" in admin(boss, "!admin grant Doc gold lots", roster, guild)


def test_grant_item_with_and_without_a_quantity():
    boss, doc, roster, guild, *_ = hall()
    admin(boss, "!admin grant Doc item lesser potion 3", roster, guild)
    assert doc.character.inventory["lesser_potion"] == 3
    admin(boss, "!admin grant Doc item whetstone", roster, guild)
    assert doc.character.inventory["whetstone"] == 1


def test_grant_item_quantity_is_capped():
    boss, doc, roster, guild, *_ = hall()
    admin(boss, "!admin grant Doc item whetstone 100000", roster, guild)
    assert doc.character.inventory["whetstone"] == 99


def test_grant_can_hand_out_gear_and_scrolls():
    boss, doc, roster, guild, *_ = hall()
    admin(boss, "!admin grant Doc item wightbane_edge", roster, guild)
    assert doc.character.inventory["wightbane_edge"] == 1
    scroll = next(k for k in ITEMS if k.startswith("scroll_"))
    admin(boss, f"!admin grant Doc item {scroll}", roster, guild)
    assert doc.character.inventory[scroll] == 1


def test_grant_usage_and_unknown_items():
    boss, doc, roster, guild, *_ = hall()
    assert "!admin grant" in admin(boss, "!admin grant Doc", roster, guild)
    assert "No item matches" in admin(
        boss, "!admin grant Doc item zzzz", roster, guild)
    assert "`gold` or `item`" in admin(
        boss, "!admin grant Doc cake 3", roster, guild)


# --- boards and bounty -----------------------------------------------------

def test_boards_reposts_for_everyone_in_the_hall_but_not_on_a_contract():
    boss, doc, roster, guild, *_ = hall()
    handle(boss, "!board", roster, guild)
    handle(doc, "!board", roster, guild)
    before = list(boss.character.board)
    handle(doc, "!accept 1", roster, guild)         # Doc is on a contract

    reply = admin(boss, "!admin boards", roster, guild)
    assert "**1** character" in reply
    assert doc.character.run is not None
    assert boss.character.board is not before


def test_bounty_shows_and_rerolls():
    boss, doc, roster, guild, *_ = hall()
    first = admin(boss, "!admin bounty", roster, guild)
    assert "Bounty of the day" in first
    guild.bounty_progress = 2
    admin(boss, "!admin bounty reroll", roster, guild)
    assert guild.bounty_progress == 0


def test_bounty_without_a_guild_is_polite():
    boss, doc, roster, _, *_ = hall()
    assert "No guild" in admin(boss, "!admin bounty", roster, None)
