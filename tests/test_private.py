"""One-to-one play: the same game, minus the parts that need other people."""

from __future__ import annotations

from core.duel import Duels
from core.game import handle
from core.guild import Guild
from core.party import Parties

from .conftest import make_char


def solo():
    player = make_char(name="Doc", mxid="@a:srv")
    other = make_char(name="Wren", mxid="@b:srv")
    return player, other, {player.mxid: player, other.mxid: other}


def dm(player, text, roster, guild=None, parties=None, duels=None):
    return handle(player, text, roster, guild or Guild(), parties or Parties(),
                  duels or Duels(), None, True)


def hall(player, text, roster, guild=None, parties=None, duels=None):
    return handle(player, text, roster, guild or Guild(), parties or Parties(),
                  duels or Duels(), None, False)


# --- what works alone ------------------------------------------------------

def test_the_solo_game_works_in_private():
    player, _other, roster = solo()
    for command in ("!board", "!shop", "!status", "!spellbook", "!inventory",
                    "!graveyard", "!help", "!guild", "!who"):
        assert dm(player, command, roster) is not None, command


def test_you_can_run_a_whole_contract_in_private():
    player, _other, roster = solo()
    char = player.character
    dm(player, "!board", roster)
    dm(player, "!accept 1", roster)
    assert char.run is not None

    for _ in range(400):
        if char.run is None or player.character is None:
            break
        if char.run.pending_event:
            dm(player, "!1", roster)
            continue
        char.run.hp = char.run.max_hp
        dm(player, "!1", roster)
    assert char.runs_completed >= 1


def test_the_guild_still_counts_private_work():
    """Playing alone still builds the shared charter."""
    player, _other, roster = solo()
    guild = Guild()
    char = player.character
    dm(player, "!board", roster, guild)
    dm(player, "!accept 1", roster, guild)

    for _ in range(400):
        if char.run is None or player.character is None:
            break
        if char.run.pending_event:
            dm(player, "!1", roster, guild)
            continue
        char.run.hp = char.run.max_hp
        dm(player, "!1", roster, guild)
    assert guild.renown > 0


def test_it_is_the_same_character_either_way():
    player, _other, roster = solo()
    dm(player, "!board", roster)
    before = player.character.name
    assert hall(player, "!status", roster) is not None
    assert player.character.name == before


# --- what belongs in the hall ---------------------------------------------

def test_social_commands_are_sent_back_to_the_hall():
    player, _other, roster = solo()
    for command in ("!party", "!invite Wren", "!join Wren", "!disband",
                    "!duel Wren", "!give Wren potion"):
        reply = dm(player, command, roster)
        assert "hall" in " ".join(reply).lower(), command


def test_a_party_contract_is_not_narrated_into_a_private_chat():
    """Half the party cannot see a DM, so the fight has to stay put."""
    player, other, roster = solo()
    parties, guild = Parties(), Guild()
    hall(player, "!party", roster, guild, parties)
    hall(player, "!invite Wren", roster, guild, parties)
    hall(other, "!join Doc", roster, guild, parties)
    hall(player, "!board", roster, guild, parties)
    hall(player, "!accept 1", roster, guild, parties)

    reply = dm(player, "!1", roster, guild, parties)
    assert "hall" in " ".join(reply).lower()


def test_a_duel_cannot_be_fought_from_a_private_chat():
    player, other, roster = solo()
    duels = Duels()
    hall(player, "!duel Wren", roster, None, None, duels)
    hall(other, "!duel accept", roster, None, None, duels)

    reply = dm(player, "!1", roster, None, None, duels)
    assert "hall" in " ".join(reply).lower()


def test_reading_the_roster_is_still_allowed():
    """Looking at other people is fine; involving them is not."""
    player, _other, roster = solo()
    assert "Wren" in " ".join(dm(player, "!who", roster))


def test_chatter_is_still_ignored_in_private():
    player, _other, roster = solo()
    assert dm(player, "hello?", roster) is None
    assert dm(player, "board", roster) is None
