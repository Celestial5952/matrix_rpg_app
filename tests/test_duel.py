"""Duels: consensual, binding, and non-lethal."""

from __future__ import annotations

from core.duel import Duels
from core.game import handle
from core.guild import Guild
from core.party import Parties

from .conftest import make_char


def yard(*names: str):
    roster = {}
    for i, name in enumerate(names or ("Doc", "Wren")):
        mxid = f"@p{i}:srv"
        roster[mxid] = make_char(name=name, mxid=mxid)
    return roster, Guild(), Parties(), Duels()


def act(player, text, roster, guild, parties, duels):
    return handle(player, text, roster, guild, parties, duels)


def fighting(roster, guild, parties, duels, wager: int = 0):
    a, b = roster["@p0:srv"], roster["@p1:srv"]
    act(a, f"!duel {b.character.name}" + (f" {wager}" if wager else ""),
        roster, guild, parties, duels)
    act(b, "!duel accept", roster, guild, parties, duels)
    return duels.for_player(a.mxid)


# --- consent ---------------------------------------------------------------

def test_a_challenge_does_not_start_a_duel():
    roster, guild, parties, duels = yard()
    a, b = roster["@p0:srv"], roster["@p1:srv"]
    act(a, "!duel Wren", roster, guild, parties, duels)

    assert duels.for_player(a.mxid) is None, "consent is required"
    assert duels.pending_for(b.mxid)


def test_accepting_starts_it():
    roster, guild, parties, duels = yard()
    duel = fighting(roster, guild, parties, duels)
    assert duel is not None and duel.accepted
    assert duel.turn in (duel.challenger, duel.opponent)


def test_declining_ends_the_challenge():
    roster, guild, parties, duels = yard()
    a, b = roster["@p0:srv"], roster["@p1:srv"]
    act(a, "!duel Wren", roster, guild, parties, duels)
    act(b, "!duel decline", roster, guild, parties, duels)

    assert duels.pending_for(b.mxid) == []
    assert duels.for_player(b.mxid) is None


def test_you_cannot_challenge_the_same_person_twice():
    roster, guild, parties, duels = yard()
    a = roster["@p0:srv"]
    act(a, "!duel Wren", roster, guild, parties, duels)
    reply = act(a, "!duel Wren", roster, guild, parties, duels)
    assert "already challenged" in " ".join(reply).lower()
    assert len(duels.by_key) == 1


def test_a_pending_challenge_blocks_wandering_off():
    """You cannot dodge a challenge by taking a contract."""
    roster, guild, parties, duels = yard()
    a, b = roster["@p0:srv"], roster["@p1:srv"]
    act(a, "!duel Wren", roster, guild, parties, duels)

    act(b, "!board", roster, guild, parties, duels)
    reply = act(b, "!accept 1", roster, guild, parties, duels)
    assert "called you out" in " ".join(reply).lower()
    assert b.character.run is None


# --- binding ---------------------------------------------------------------

def test_there_is_no_escaping_an_accepted_duel():
    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)
    a = roster["@p0:srv"]

    for escape in ("!portal", "!flee", "!leave", "!tp", "!escape"):
        reply = act(a, escape, roster, guild, parties, duels)
        assert "no door" in " ".join(reply).lower(), escape
    assert duels.for_player(a.mxid) is not None


def test_nothing_else_works_during_a_duel():
    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)
    a = roster["@p0:srv"]

    for blocked in ("!board", "!accept 1", "!shop", "!party", "!use potion"):
        reply = act(a, blocked, roster, guild, parties, duels)
        assert "duel" in " ".join(reply).lower(), blocked
    assert a.character.run is None


def test_only_the_active_duellist_may_act():
    roster, guild, parties, duels = yard()
    duel = fighting(roster, guild, parties, duels)
    waiting = roster[duel.other(duel.turn).mxid]

    before = duel.duelist(duel.turn).hp
    reply = act(waiting, "!1", roster, guild, parties, duels)
    assert "wait" in " ".join(reply).lower()
    assert duel.duelist(duel.turn).hp == before


def test_turn_passes_after_a_move():
    roster, guild, parties, duels = yard()
    duel = fighting(roster, guild, parties, duels)
    first = duel.turn
    act(roster[first], "!1", roster, guild, parties, duels)
    assert duel.turn != first


# --- fighting --------------------------------------------------------------

def test_attacks_land_on_the_other_duellist():
    roster, guild, parties, duels = yard()
    duel = fighting(roster, guild, parties, duels)
    attacker, defender = duel.turn, duel.other(duel.turn)
    before = defender.hp

    act(roster[attacker], "!1", roster, guild, parties, duels)
    assert defender.hp < before


def test_a_duel_does_not_touch_contract_state():
    """Duellists fight from a fresh kit; the character's own run is untouched."""
    roster, guild, parties, duels = yard()
    a = roster["@p0:srv"]
    a.character.inventory["greater_potion"] = 2
    fighting(roster, guild, parties, duels)

    assert a.character.run is None
    assert a.character.inventory["greater_potion"] == 2


def test_losing_is_not_dying():
    roster, guild, parties, duels = yard()
    duel = fighting(roster, guild, parties, duels)

    for _ in range(200):
        live = duels.for_player("@p0:srv")
        if live is None:
            break
        act(roster[live.turn], "!1", roster, guild, parties, duels)

    assert duels.for_player("@p0:srv") is None, "the duel should settle"
    for key in ("@p0:srv", "@p1:srv"):
        assert roster[key].character is not None, "nobody dies in the yard"
        assert roster[key].deaths == 0


def test_the_winner_and_loser_are_recorded():
    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)

    for _ in range(200):
        live = duels.for_player("@p0:srv")
        if live is None:
            break
        act(roster[live.turn], "!1", roster, guild, parties, duels)

    wins = sum(roster[k].character.duels_won for k in roster)
    losses = sum(roster[k].character.duels_lost for k in roster)
    assert wins == 1 and losses == 1


# --- wagers ----------------------------------------------------------------

def test_a_wager_moves_from_loser_to_winner():
    roster, guild, parties, duels = yard()
    for key in roster:
        roster[key].character.gold = 100
    fighting(roster, guild, parties, duels, wager=25)

    for _ in range(200):
        live = duels.for_player("@p0:srv")
        if live is None:
            break
        act(roster[live.turn], "!1", roster, guild, parties, duels)

    golds = sorted(roster[k].character.gold for k in roster)
    assert golds == [75, 125]


def test_you_cannot_wager_what_you_do_not_have():
    roster, guild, parties, duels = yard()
    roster["@p0:srv"].character.gold = 5
    reply = act(roster["@p0:srv"], "!duel Wren 500", roster, guild, parties, duels)
    assert "bold" in " ".join(reply).lower()
    assert duels.by_key == {}


def test_a_broke_loser_pays_what_they_can():
    roster, guild, parties, duels = yard()
    roster["@p0:srv"].character.gold = 60
    roster["@p1:srv"].character.gold = 3
    fighting(roster, guild, parties, duels, wager=50)

    for _ in range(200):
        live = duels.for_player("@p0:srv")
        if live is None:
            break
        act(roster[live.turn], "!1", roster, guild, parties, duels)

    total = sum(roster[k].character.gold for k in roster)
    assert total == 63, "gold is moved, never created"
    assert all(roster[k].character.gold >= 0 for k in roster)


# --- interaction with the rest of the game --------------------------------

def test_you_cannot_duel_while_on_a_contract():
    roster, guild, parties, duels = yard()
    a = roster["@p0:srv"]
    act(a, "!board", roster, guild, parties, duels)
    act(a, "!accept 1", roster, guild, parties, duels)

    reply = act(a, "!duel Wren", roster, guild, parties, duels)
    assert "contract" in " ".join(reply).lower()
    assert duels.by_key == {}


def test_you_cannot_duel_someone_who_is_out_working():
    roster, guild, parties, duels = yard()
    b = roster["@p1:srv"]
    act(b, "!board", roster, guild, parties, duels)
    act(b, "!accept 1", roster, guild, parties, duels)

    reply = act(roster["@p0:srv"], "!duel Wren", roster, guild, parties, duels)
    assert "contract" in " ".join(reply).lower()


def test_a_third_party_is_unaffected():
    roster, guild, parties, duels = yard("Doc", "Wren", "Bram")
    fighting(roster, guild, parties, duels)
    outsider = roster["@p2:srv"]

    act(outsider, "!board", roster, guild, parties, duels)
    act(outsider, "!accept 1", roster, guild, parties, duels)
    assert outsider.character.run is not None
    assert duels.for_player(outsider.mxid) is None


# --- bar duty --------------------------------------------------------------

def settle(roster, guild, parties, duels):
    for _ in range(200):
        live = duels.for_player("@p0:srv")
        if live is None:
            break
        act(roster[live.turn], "!1", roster, guild, parties, duels)
    losers = [p for p in roster.values() if p.character.duels_lost]
    winners = [p for p in roster.values() if p.character.duels_won]
    return winners[0], losers[0]


def test_losing_puts_you_behind_the_bar():
    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)
    winner, loser = settle(roster, guild, parties, duels)

    assert loser.character.on_bar_duty
    assert not winner.character.on_bar_duty
    assert "h" in loser.character.bar_duty_remaining


def test_bar_duty_blocks_taking_contracts():
    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)
    _winner, loser = settle(roster, guild, parties, duels)

    act(loser, "!board", roster, guild, parties, duels)
    reply = act(loser, "!accept 1", roster, guild, parties, duels)
    assert "behind the bar" in " ".join(reply).lower()
    assert loser.character.run is None


def test_bar_duty_blocks_adventures():
    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)
    _winner, loser = settle(roster, guild, parties, duels)

    loser.character.renown = 100
    loser.character.inventory["scroll_sunless_ziggurat"] = 1
    reply = act(loser, "!use scroll", roster, guild, parties, duels)
    assert "behind the bar" in " ".join(reply).lower()
    assert loser.character.inventory["scroll_sunless_ziggurat"] == 1


def test_bar_duty_is_published_on_the_board():
    """The humiliation is the point; it has to be visible to everyone."""
    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)
    winner, loser = settle(roster, guild, parties, duels)

    board = " ".join(act(winner, "!board", roster, guild, parties, duels))
    assert "Behind the bar" in board
    assert loser.character.name in board


def test_bar_duty_still_lets_you_shop_and_be_challenged():
    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)
    winner, loser = settle(roster, guild, parties, duels)
    loser.character.gold = 100

    assert act(loser, "!shop", roster, guild, parties, duels) is not None
    act(loser, "!buy 1", roster, guild, parties, duels)
    assert loser.character.inventory.get("lesser_potion") == 1

    act(winner, f"!duel {loser.character.name}", roster, guild, parties, duels)
    assert duels.pending_for(loser.mxid), "an apron is not a shield"


def test_bar_duty_expires():
    import time

    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)
    _winner, loser = settle(roster, guild, parties, duels)

    loser.character.barmaid_until = time.time() - 1
    assert not loser.character.on_bar_duty

    act(loser, "!board", roster, guild, parties, duels)
    act(loser, "!accept 1", roster, guild, parties, duels)
    assert loser.character.run is not None


def test_bar_duty_survives_a_restart(tmp_path):
    from core.persist import load_all, save_all

    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)
    _winner, loser = settle(roster, guild, parties, duels)

    path = tmp_path / "players.json"
    save_all(path, roster)
    restored = load_all(path)[loser.mxid].character
    assert restored.on_bar_duty, "you cannot wait out an apron by restarting"


def test_bar_duty_dies_with_the_character():
    roster, guild, parties, duels = yard()
    fighting(roster, guild, parties, duels)
    _winner, loser = settle(roster, guild, parties, duels)
    assert loser.character.on_bar_duty

    loser.character.barmaid_until = 0  # let them work, then get them killed
    for _ in range(80):
        if loser.character is None:
            break
        if loser.character.run is None:
            act(loser, "!board", roster, guild, parties, duels)
            act(loser, "!accept 1", roster, guild, parties, duels)
        loser.character.run.hp = 1
        act(loser, "!1", roster, guild, parties, duels)

    for line in ("!create", "!Fresh", "!human", "!fighter"):
        act(loser, line, roster, guild, parties, duels)
    assert not loser.character.on_bar_duty
    assert loser.character.duels_lost == 0
