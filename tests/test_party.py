"""Co-op parties: formation, shared monsters, turn order, and the shield."""

from __future__ import annotations

from core.game import handle
from core.guild import Guild
from core.party import MAX_PARTY, Parties, scaled_for_party
from core.content import MONSTERS
from core.state import Player

from .conftest import make_char


def table(*names: str):
    """Players with characters, plus a roster, guild and party registry."""
    players = {}
    for i, name in enumerate(names):
        mxid = f"@p{i}:srv"
        players[mxid] = make_char(name=name, mxid=mxid)
    return players, Guild(), Parties()


def act(player, text, roster, guild, parties):
    return handle(player, text, roster, guild, parties)


def turn(party, roster):
    """Whose move it is — needs the roster to know who is still standing."""
    from core.game import _standing

    return party.next_actor(_standing(party, roster))


def form(roster, guild, parties, leader_key="@p0:srv", *guest_keys):
    leader = roster[leader_key]
    act(leader, "!party", roster, guild, parties)
    for key in guest_keys:
        guest = roster[key]
        act(leader, f"!invite {guest.character.name}", roster, guild, parties)
        act(guest, f"!join {leader.character.name}", roster, guild, parties)
    return parties.for_member(leader_key)


def start(roster, guild, parties, leader_key="@p0:srv"):
    leader = roster[leader_key]
    act(leader, "!board", roster, guild, parties)
    return act(leader, "!accept 1", roster, guild, parties)


# --- formation -------------------------------------------------------------

def test_a_player_starts_with_no_party():
    roster, guild, parties = table("Doc")
    reply = act(roster["@p0:srv"], "!party", roster, guild, parties)
    assert "not in a party" in " ".join(reply).lower()


def test_forming_and_joining():
    roster, guild, parties = table("Doc", "Wren")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    assert party.members == ["@p0:srv", "@p1:srv"]
    assert party.leader == "@p0:srv"


def test_one_party_per_player():
    roster, guild, parties = table("Doc", "Wren")
    form(roster, guild, parties, "@p0:srv", "@p1:srv")
    reply = act(roster["@p1:srv"], "!party create", roster, guild, parties)
    assert "already in a party" in " ".join(reply).lower()


def test_multiple_parties_run_independently():
    roster, guild, parties = table("Doc", "Wren", "Bram", "Nix")
    first = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    second = form(roster, guild, parties, "@p2:srv", "@p3:srv")

    assert first.key != second.key
    assert len(parties.by_key) == 2

    start(roster, guild, parties, "@p0:srv")
    assert first.on_contract
    assert not second.on_contract, "one party's contract must not start another's"


def test_a_party_cannot_exceed_its_limit():
    roster, guild, parties = table(*[f"P{i}" for i in range(MAX_PARTY + 1)])
    keys = list(roster)
    form(roster, guild, parties, keys[0], *keys[1:MAX_PARTY])
    party = parties.for_member(keys[0])
    assert party.size == MAX_PARTY

    reply = act(roster[keys[0]], f"!invite {roster[keys[-1]].character.name}",
                roster, guild, parties)
    assert "full" in " ".join(reply).lower()


def test_only_the_leader_invites():
    roster, guild, parties = table("Doc", "Wren", "Bram")
    form(roster, guild, parties, "@p0:srv", "@p1:srv")
    reply = act(roster["@p1:srv"], "!invite Bram", roster, guild, parties)
    assert "only" in " ".join(reply).lower()


def test_leaving_promotes_a_new_leader():
    roster, guild, parties = table("Doc", "Wren")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    act(roster["@p0:srv"], "!leave", roster, guild, parties)
    assert party.leader == "@p1:srv"
    assert parties.for_member("@p0:srv") is None


def test_the_last_member_leaving_disbands_it():
    roster, guild, parties = table("Doc")
    act(roster["@p0:srv"], "!party create", roster, guild, parties)
    act(roster["@p0:srv"], "!leave", roster, guild, parties)
    assert parties.by_key == {}


# --- shared monster --------------------------------------------------------

def test_the_party_shares_one_monster():
    roster, guild, parties = table("Doc", "Wren")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    a, b = roster["@p0:srv"].character, roster["@p1:srv"].character
    assert a.run.encounter is b.run.encounter is party.encounter


def test_health_bars_are_not_shared():
    roster, guild, parties = table("Doc", "Wren")
    form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    a, b = roster["@p0:srv"].character, roster["@p1:srv"].character
    a.run.hp = 5
    assert b.run.hp > 5, "a shared bar would make a party one big character"


def test_monsters_are_scaled_for_the_group():
    solo = MONSTERS["kobold"]
    for size in (2, 3, 4):
        scaled = scaled_for_party(solo, size)
        assert scaled.max_hp > solo.max_hp
        assert scaled.power >= solo.power
        assert scaled.power < solo.power * size, "power must scale gently"
    assert scaled_for_party(solo, 1) is solo


# --- turn order and the shield --------------------------------------------

def test_only_the_active_member_may_act():
    roster, guild, parties = table("Doc", "Wren")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    waiting = roster["@p1:srv"] if turn(party, roster) == "@p0:srv" else roster["@p0:srv"]
    before = party.encounter.hp
    reply = act(waiting, "!1", roster, guild, parties)
    assert "wait your turn" in " ".join(reply).lower()
    assert party.encounter.hp == before, "an out-of-turn action must not land"


def test_an_outsider_cannot_touch_the_party_monster():
    """The shield: routing is per-MXID, so there is no path to reach it."""
    roster, guild, parties = table("Doc", "Wren", "Nosy")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    before = party.encounter.hp
    reply = act(roster["@p2:srv"], "!2", roster, guild, parties)
    assert party.encounter.hp == before
    assert "not in that party" in " ".join(reply).lower()


def test_an_outsider_can_still_play_their_own_game():
    roster, guild, parties = table("Doc", "Wren", "Nosy")
    form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    outsider = roster["@p2:srv"]
    act(outsider, "!board", roster, guild, parties)
    act(outsider, "!accept 1", roster, guild, parties)
    assert outsider.character.run is not None
    assert outsider.character.run.party_key == ""


def test_turn_advances_and_the_monster_answers_once_a_round():
    roster, guild, parties = table("Doc", "Wren")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    first = turn(party, roster)
    act(roster[first], "!1", roster, guild, parties)
    assert turn(party, roster) != first, "turn should pass"

    # The monster acting is the assertion — not damage. Its telegraphed move
    # is sometimes a guard, which deals none, and testing for HP loss made
    # this fail about one run in three.
    reply = act(roster[turn(party, roster)], "!1", roster, guild, parties)
    text = " ".join(reply)
    assert any(word in text for word in ("hits you", "braces", "draws")), text
    assert party.acted == set(), "a completed round should reset"


# --- downing, wipes and payouts -------------------------------------------

def test_a_downed_member_is_skipped_not_killed():
    roster, guild, parties = table("Doc", "Wren")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    downed = roster["@p0:srv"]
    downed.character.run.hp = 0

    assert turn(party, roster) == "@p1:srv", "a downed member must be skipped"
    assert downed.character is not None, "downed is not dead"

    reply = act(downed, "!1", roster, guild, parties)
    assert "down" in " ".join(reply).lower()


def test_a_total_wipe_kills_everyone():
    roster, guild, parties = table("Doc", "Wren")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    for _ in range(300):
        if not party.on_contract or parties.by_key == {}:
            break
        # Unkillable monster: otherwise the party sometimes wins the fight
        # before the wipe path is ever exercised.
        if party.encounter is not None:
            party.encounter.hp = 9999
        for mxid in party.members:
            character = roster[mxid].character
            # Top up only those still standing — resetting a downed member
            # would revive them and the party could never actually wipe.
            if character and character.run and character.run.hp > 0:
                character.run.hp = 1
        current = turn(party, roster)
        if current is None:
            break
        act(roster[current], "!1", roster, guild, parties)

    assert all(roster[k].character is None for k in ("@p0:srv", "@p1:srv"))
    assert all(roster[k].deaths == 1 for k in ("@p0:srv", "@p1:srv"))


def test_finishing_pays_every_member():
    roster, guild, parties = table("Doc", "Wren")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    for _ in range(400):
        if not party.on_contract:
            break
        for mxid in party.members:
            roster[mxid].character.run.hp = roster[mxid].character.run.max_hp
        act(roster[turn(party, roster)], "!1", roster, guild, parties)

    for key in ("@p0:srv", "@p1:srv"):
        assert roster[key].character.gold > 0
        assert roster[key].character.renown > 0
    assert guild.renown > 0


def test_portalling_takes_the_whole_party_home():
    roster, guild, parties = table("Doc", "Wren")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    act(roster["@p1:srv"], "!portal", roster, guild, parties)
    assert not party.on_contract
    for key in ("@p0:srv", "@p1:srv"):
        assert roster[key].character.run is None
        assert roster[key].character.gold == 0
    assert parties.for_member("@p0:srv") is party, "the party stays together"


# --- the horn --------------------------------------------------------------

def test_the_horn_pulls_someone_into_a_solo_fight():
    roster, guild, parties = table("Doc", "Wren")
    doc = roster["@p0:srv"]
    doc.character.inventory["summoning_horn"] = 1
    act(doc, "!board", roster, guild, parties)
    act(doc, "!accept 1", roster, guild, parties)
    assert parties.for_member(doc.mxid) is None

    reply = act(doc, "!use horn Wren", roster, guild, parties)
    party = parties.for_member(doc.mxid)

    assert party is not None and party.size == 2
    assert roster["@p1:srv"].character.run is not None
    assert roster["@p1:srv"].character.run.encounter is party.encounter
    assert "summoning_horn" not in doc.character.inventory
    assert "HORN" in " ".join(reply)


def test_the_horn_will_not_take_someone_already_busy():
    roster, guild, parties = table("Doc", "Wren")
    doc, wren = roster["@p0:srv"], roster["@p1:srv"]
    doc.character.inventory["summoning_horn"] = 1
    for player in (doc, wren):
        act(player, "!board", roster, guild, parties)
        act(player, "!accept 1", roster, guild, parties)

    reply = act(doc, "!use horn Wren", roster, guild, parties)
    assert "their own contract" in " ".join(reply).lower()
    assert doc.character.inventory["summoning_horn"] == 1


def test_the_horn_adds_to_an_existing_party():
    roster, guild, parties = table("Doc", "Wren", "Bram")
    party = form(roster, guild, parties, "@p0:srv", "@p1:srv")
    roster["@p0:srv"].character.inventory["summoning_horn"] = 1
    start(roster, guild, parties)

    act(roster["@p0:srv"], "!use horn Bram", roster, guild, parties)
    assert party.size == 3
    assert roster["@p2:srv"].character.run.encounter is party.encounter


# --- persistence -----------------------------------------------------------

def test_party_runs_are_not_restored_per_member(tmp_path):
    """Restoring shared state per-member would fork one fight into several."""
    from core.persist import load_all, save_all

    roster, guild, parties = table("Doc", "Wren")
    form(roster, guild, parties, "@p0:srv", "@p1:srv")
    start(roster, guild, parties)

    path = tmp_path / "players.json"
    save_all(path, roster)
    loaded = load_all(path)

    for player in loaded.values():
        assert player.character is not None, "characters survive"
        assert player.character.run is None, "the shared run does not"
