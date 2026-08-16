"""Persistence. The failure mode that matters is 'bot won't start', so the
loader is tested against garbage as much as against good input."""

from __future__ import annotations

import json

from core.persist import load_all, save_all
from core.state import Player


def test_round_trip(tmp_path) -> None:
    path = tmp_path / "players.json"
    players = {
        "@a:srv": Player(mxid="@a:srv", name="A", renown=5, gold=10,
                         runs_completed=2, deaths=1),
        "@b:srv": Player(mxid="@b:srv", name="B"),
    }
    save_all(path, players)
    loaded = load_all(path)

    assert set(loaded) == {"@a:srv", "@b:srv"}
    assert loaded["@a:srv"].renown == 5
    assert loaded["@a:srv"].gold == 10
    assert loaded["@a:srv"].runs_completed == 2
    assert loaded["@a:srv"].deaths == 1


def test_missing_file_is_empty_not_an_error(tmp_path) -> None:
    assert load_all(tmp_path / "nope.json") == {}


def test_run_state_is_not_persisted(tmp_path) -> None:
    """Only meta state survives — that's the roguelite split."""
    path = tmp_path / "players.json"
    p = Player(mxid="@a:srv", name="A", renown=3)
    save_all(path, {"@a:srv": p})
    assert load_all(path)["@a:srv"].run is None
    assert load_all(path)["@a:srv"].board == []


def test_rank_is_derived_not_stored(tmp_path) -> None:
    path = tmp_path / "players.json"
    save_all(path, {"@a:srv": Player(mxid="@a:srv", name="A", renown=50)})
    assert load_all(path)["@a:srv"].rank == 3


def test_old_rows_missing_a_field_still_load(tmp_path) -> None:
    """Forwards compatibility: adding a field must not brick an existing file."""
    path = tmp_path / "players.json"
    path.write_text(json.dumps({"@a:srv": {"name": "A", "renown": 7}}))
    loaded = load_all(path)
    assert loaded["@a:srv"].renown == 7
    assert loaded["@a:srv"].gold == 0
    assert loaded["@a:srv"].deaths == 0


def test_unknown_fields_are_ignored(tmp_path) -> None:
    """A row written by a newer version must not crash an older one."""
    path = tmp_path / "players.json"
    path.write_text(json.dumps(
        {"@a:srv": {"name": "A", "renown": 7, "future_stat": 99}}
    ))
    assert load_all(path)["@a:srv"].renown == 7


def test_mxid_key_wins_over_stored_field(tmp_path) -> None:
    path = tmp_path / "players.json"
    path.write_text(json.dumps({"@real:srv": {"mxid": "@stale:srv", "name": "A"}}))
    assert load_all(path)["@real:srv"].mxid == "@real:srv"


def test_corrupt_file_does_not_raise(tmp_path) -> None:
    """A truncated file must cost progress, not uptime."""
    path = tmp_path / "players.json"
    path.write_text('{"@a:srv": {"name": "A", "renow')
    assert load_all(path) == {}
    assert (tmp_path / "players.json.corrupt").exists(), "should be kept for recovery"


def test_non_object_json_does_not_raise(tmp_path) -> None:
    path = tmp_path / "players.json"
    path.write_text("[1, 2, 3]")
    assert load_all(path) == {}


def test_malformed_row_is_skipped_not_fatal(tmp_path) -> None:
    path = tmp_path / "players.json"
    path.write_text(json.dumps({"@a:srv": "not a dict", "@b:srv": {"name": "B"}}))
    loaded = load_all(path)
    assert "@a:srv" not in loaded
    assert "@b:srv" in loaded


def test_save_is_atomic(tmp_path) -> None:
    """No .tmp left behind, and the live file is always complete JSON."""
    path = tmp_path / "players.json"
    save_all(path, {"@a:srv": Player(mxid="@a:srv", name="A")})
    assert json.loads(path.read_text())
    assert not list(tmp_path.glob("*.tmp"))
