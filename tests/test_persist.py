"""Persistence round-trips and failure modes.

The load path must never raise: a crash here takes the bot down on every
restart, which is strictly worse than starting fresh.
"""

from __future__ import annotations

import json

from core.game import handle
from core.persist import load_all, save_all
from core.state import Player, Tombstone

from .conftest import make_char, make_player


def test_round_trip(tmp_path):
    path = tmp_path / "players.json"
    player = make_char(race="dwarf", char_class="cleric", name="Bruni")
    player.character.renown = 25
    player.character.gold = 99
    player.character.runs_completed = 4

    save_all(path, {player.mxid: player})
    loaded = load_all(path)[player.mxid]

    assert loaded.character.name == "Bruni"
    assert loaded.character.race_key == "dwarf"
    assert loaded.character.class_key == "cleric"
    assert loaded.character.renown == 25
    assert loaded.character.gold == 99
    assert loaded.character.runs_completed == 4
    assert loaded.character.abilities == player.character.abilities


def test_graveyard_survives(tmp_path):
    path = tmp_path / "players.json"
    player = make_player()
    player.graveyard.append(Tombstone(
        name="Old", race="Elf", char_class="Wizard",
        renown=7, runs_completed=2, killed_by="Cave Rat",
    ))
    save_all(path, {player.mxid: player})
    loaded = load_all(path)[player.mxid]

    assert loaded.deaths == 1
    assert loaded.graveyard[0].name == "Old"
    assert loaded.graveyard[0].killed_by == "Cave Rat"


def test_characterless_player_round_trips(tmp_path):
    path = tmp_path / "players.json"
    player = make_player()
    save_all(path, {player.mxid: player})
    assert load_all(path)[player.mxid].character is None


def test_missing_file_is_empty_not_an_error(tmp_path):
    assert load_all(tmp_path / "nope.json") == {}


def test_run_state_is_not_persisted(tmp_path):
    """Documented tradeoff: a restart costs the contract, not the character."""
    path = tmp_path / "players.json"
    player = make_char()
    handle(player, "!board")
    handle(player, "!accept 1")
    assert player.character.run is not None

    save_all(path, {player.mxid: player})
    loaded = load_all(path)[player.mxid]
    assert loaded.character is not None, "the character must survive"
    assert loaded.character.run is None


def test_pending_creation_is_not_persisted(tmp_path):
    """A half-filled register mid-restart would strand the player."""
    path = tmp_path / "players.json"
    player = make_player()
    handle(player, "!create")
    handle(player, "!Halfway")
    save_all(path, {player.mxid: player})
    assert load_all(path)[player.mxid].pending is None


def test_derived_stats_are_not_stored(tmp_path):
    path = tmp_path / "players.json"
    player = make_char(race="dwarf", char_class="fighter")
    save_all(path, {player.mxid: player})
    row = json.loads(path.read_text())[player.mxid]["character"]
    for derived in ("max_hp", "power", "max_focus", "rank"):
        assert derived not in row


def test_old_rows_missing_a_field_still_load(tmp_path):
    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@a:srv": {"display_name": "A", "character": {
            "name": "Legacy", "race_key": "human", "class_key": "fighter",
        }},
    }))
    char = load_all(path)["@a:srv"].character
    assert char.name == "Legacy"
    assert char.renown == 0


def test_pre_character_schema_still_loads(tmp_path):
    """Rows written before characters existed used a flat 'name' field."""
    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@a:srv": {"name": "A", "renown": 50, "gold": 10},
    }))
    player = load_all(path)["@a:srv"]
    assert player.display_name == "A"
    assert player.character is None  # old progress has no character to attach to


def test_unknown_fields_are_ignored(tmp_path):
    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@a:srv": {"display_name": "A", "wat": True, "character": {
            "name": "X", "race_key": "elf", "class_key": "rogue", "nonsense": 1,
        }},
    }))
    assert load_all(path)["@a:srv"].character.name == "X"


def test_character_with_deleted_race_is_dropped_not_fatal(tmp_path):
    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@a:srv": {"display_name": "A", "character": {
            "name": "X", "race_key": "sasquatch", "class_key": "rogue",
        }},
    }))
    player = load_all(path)["@a:srv"]
    assert player.character is None
    assert player.display_name == "A"


def test_mxid_key_wins_over_stored_field(tmp_path):
    path = tmp_path / "players.json"
    path.write_text(json.dumps({"@real:srv": {"mxid": "@liar:srv", "name": "A"}}))
    assert load_all(path)["@real:srv"].mxid == "@real:srv"


def test_corrupt_file_does_not_raise(tmp_path):
    path = tmp_path / "players.json"
    path.write_text("{not json")
    assert load_all(path) == {}
    assert path.with_suffix(".json.corrupt").exists()


def test_non_object_json_does_not_raise(tmp_path):
    path = tmp_path / "players.json"
    path.write_text("[1, 2, 3]")
    assert load_all(path) == {}


def test_malformed_row_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@bad:srv": "not a dict",
        "@good:srv": {"display_name": "G"},
    }))
    loaded = load_all(path)
    assert "@bad:srv" not in loaded
    assert loaded["@good:srv"].display_name == "G"


def test_malformed_graveyard_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "players.json"
    path.write_text(json.dumps({
        "@a:srv": {"display_name": "A", "graveyard": ["nope", {"name": "Real"}]},
    }))
    grave = load_all(path)["@a:srv"].graveyard
    assert len(grave) == 1 and grave[0].name == "Real"


def test_save_is_atomic(tmp_path):
    path = tmp_path / "players.json"
    save_all(path, {"@a:srv": Player(mxid="@a:srv", display_name="A")})
    assert path.exists()
    assert not path.with_suffix(".json.tmp").exists()
