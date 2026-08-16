"""Naming another player: mentions, display names, and character names.

An Element mention pill puts the *display name* in the message body, not the
MXID, so these paths are easy to get subtly wrong and hard to notice.
"""

from __future__ import annotations

from core.duel import Duels
from core.game import handle
from core.guild import Guild
from core.party import Parties

from .conftest import make_char

MXID = "@duckbill7317:chat.services.snchomelab.com"
PILL = "Duckbill7317 ☭"          # what a pill actually inserts into the body


def two():
    a = make_char(name="Doc", mxid="@a:srv")
    b = make_char(name="Wren", mxid=MXID)
    b.display_name = PILL
    return a, b, {a.mxid: a, b.mxid: b}


def act(player, text, roster, mentions=None, duels=None):
    return handle(player, text, roster, Guild(), Parties(),
                  duels or Duels(), mentions)


def test_a_mention_names_the_player_exactly():
    a, b, roster = two()
    duels = Duels()
    act(a, f"!duel {PILL}", roster, [MXID], duels)
    assert duels.pending_for(b.mxid), "the pill should resolve to the MXID"


def test_a_mention_survives_decoration_and_a_wager():
    a, b, roster = two()
    a.character.gold = 100
    duels = Duels()
    act(a, f"!duel {PILL} 20", roster, [MXID], duels)

    pending = duels.pending_for(b.mxid)
    assert pending, "a decorated display name must not eat the wager"
    assert pending[0].wager == 20


def test_the_sender_being_mentioned_is_ignored():
    """Some clients list the sender; it must not target them at themselves."""
    a, b, roster = two()
    duels = Duels()
    act(a, f"!duel {PILL}", roster, [a.mxid, MXID], duels)
    assert duels.pending_for(b.mxid)


def test_display_names_work_without_mention_metadata():
    a, b, roster = two()
    duels = Duels()
    act(a, f"!duel {PILL}", roster, None, duels)
    assert duels.pending_for(b.mxid), "fallback matching should still find them"


def test_character_name_still_works():
    a, b, roster = two()
    duels = Duels()
    act(a, "!duel Wren", roster, None, duels)
    assert duels.pending_for(b.mxid)


def test_a_full_mxid_still_works():
    a, b, roster = two()
    duels = Duels()
    act(a, f"!duel {MXID}", roster, None, duels)
    assert duels.pending_for(b.mxid)


def test_a_wager_is_found_wherever_it_lands():
    a, b, roster = two()
    a.character.gold = 100
    duels = Duels()
    act(a, f"!duel {PILL} 35", roster, [MXID], duels)
    assert duels.pending_for(b.mxid)[0].wager == 35


def test_mentions_work_for_giving_too():
    a, b, roster = two()
    a.character.inventory["greater_potion"] = 1
    handle(a, f"!give {PILL} greater", roster, Guild(), Parties(), Duels(),
           [MXID])
    assert b.character.inventory.get("greater_potion") == 1


def test_mentions_work_for_party_invites():
    a, b, roster = two()
    parties = Parties()
    handle(a, "!party", roster, Guild(), parties, Duels())
    handle(a, f"!invite {PILL}", roster, Guild(), parties, Duels(), [MXID])
    assert b.mxid in parties.for_member(a.mxid).invited


def test_an_unknown_mention_does_not_target_somebody_else():
    a, _b, roster = two()
    duels = Duels()
    reply = act(a, "!duel @stranger:elsewhere", roster,
                ["@stranger:elsewhere"], duels)
    assert duels.by_key == {}
    assert "nobody here" in " ".join(reply).lower()
