"""Snapshots of the save files, with rotation. Must never raise."""

from __future__ import annotations

from core import backup
from core.backup import BACKUP_DIR, snapshot


def seed(tmp_path, players="{}", guild='{"renown": 1}'):
    (tmp_path / "players.json").write_text(players)
    (tmp_path / "guild.json").write_text(guild)


def names(tmp_path):
    root = tmp_path / BACKUP_DIR
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def test_a_snapshot_copies_both_files_under_a_utc_stamp(tmp_path):
    seed(tmp_path, players='{"a": 1}')
    dest = snapshot(tmp_path, now=1_800_000_000)
    assert dest is not None and dest.parent == tmp_path / BACKUP_DIR
    assert (dest / "players.json").read_text() == '{"a": 1}'
    assert (dest / "guild.json").read_text() == '{"renown": 1}'
    assert dest.name.endswith("Z") and "T" in dest.name


def test_the_sync_token_is_never_copied(tmp_path):
    seed(tmp_path)
    (tmp_path / "sync_token").write_text("s123")
    dest = snapshot(tmp_path)
    assert not (dest / "sync_token").exists()


def test_nothing_to_back_up_is_not_an_error(tmp_path):
    assert snapshot(tmp_path) is None
    assert names(tmp_path) == []


def test_an_unchanged_state_is_not_backed_up_twice(tmp_path):
    seed(tmp_path)
    assert snapshot(tmp_path, now=1_800_000_000) is not None
    assert snapshot(tmp_path, now=1_800_000_900) is None
    assert len(names(tmp_path)) == 1


def test_a_change_makes_a_new_snapshot(tmp_path):
    seed(tmp_path)
    snapshot(tmp_path, now=1_800_000_000)
    (tmp_path / "players.json").write_text('{"changed": true}')
    assert snapshot(tmp_path, now=1_800_000_900) is not None
    assert len(names(tmp_path)) == 2


def test_a_file_appearing_or_vanishing_counts_as_a_change(tmp_path):
    (tmp_path / "players.json").write_text("{}")
    snapshot(tmp_path, now=1_800_000_000)
    (tmp_path / "guild.json").write_text("{}")
    assert snapshot(tmp_path, now=1_800_000_900) is not None


def test_only_the_newest_keep_snapshots_survive(tmp_path):
    for i in range(6):
        seed(tmp_path, players=f'{{"n": {i}}}')
        snapshot(tmp_path, keep=3, now=1_800_000_000 + i * 60)
    kept = names(tmp_path)
    assert len(kept) == 3
    newest = tmp_path / BACKUP_DIR / kept[-1] / "players.json"
    assert newest.read_text() == '{"n": 5}'


def test_keep_zero_disables_backups(tmp_path):
    seed(tmp_path)
    assert snapshot(tmp_path, keep=0) is None
    assert names(tmp_path) == []


def test_a_failing_filesystem_is_logged_not_raised(tmp_path, monkeypatch):
    seed(tmp_path)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(backup.shutil, "copy2", boom)
    assert snapshot(tmp_path) is None
