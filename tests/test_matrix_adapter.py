"""Adapter logic, exercised without a homeserver.

These cover the rules that are easy to regress and expensive to discover live:
ignoring our own events, dropping backfill on a cold start, surviving a
throwing handle(), and honouring a 429.

asyncio.run() is used directly rather than pytest-asyncio to keep the test
dependencies at just pytest.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from nio import RoomSendError

import adapters.matrix as adapter
from adapters.matrix import Bot, _to_html
from core.state import Character, Player


# --- markdown -> HTML ------------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    ("**Strike**", "<strong>Strike</strong>"),
    ("_quiet_", "<em>quiet</em>"),
    ("`accept 1`", "<code>accept 1</code>"),
    ("plain text", "plain text"),
])
def test_markdown_conversion(line: str, expected: str) -> None:
    assert _to_html(line) == expected


def test_html_is_escaped_before_formatting() -> None:
    """Player-supplied text reaches these lines; it must not become markup."""
    assert "<script>" not in _to_html("<script>alert(1)</script>")
    assert "&lt;script&gt;" in _to_html("<script>alert(1)</script>")


def test_underscores_inside_words_are_not_italics() -> None:
    """MXIDs and display names contain underscores and must survive intact."""
    assert _to_html("@cool_guy_name:srv") == "@cool_guy_name:srv"


def test_hp_bar_emoji_pass_through_unharmed() -> None:
    """Escaping must not mangle the bar — it is the most-looked-at thing here."""
    from core.combat import hp_bar

    bar = hp_bar(20, 34)
    assert bar in _to_html(f"**You**  {bar} 20/34")


# --- fakes -----------------------------------------------------------------

class FakeRoom:
    def __init__(self, room_id: str) -> None:
        self.room_id = room_id

    def user_name(self, user_id: str) -> str:
        return "Tester"


class FakeEvent:
    def __init__(self, sender: str, body: str, ts: int | None = None) -> None:
        self.sender = sender
        self.body = body
        self.server_timestamp = int(time.time() * 1000) + 1000 if ts is None else ts


class FakeResponse:
    def __init__(self, event_id: str) -> None:
        self.event_id = event_id


class FakeClient:
    """Stands in for AsyncClient — records sends, never touches the network."""

    def __init__(self, user_id: str = "@bot:srv") -> None:
        self.user_id = user_id
        self.sent: list[dict] = []
        self.responses: list[object] = []
        self._n = 0

    async def room_send(self, room_id, message_type, content):
        self.sent.append(content)
        if self.responses:
            return self.responses.pop(0)
        self._n += 1
        return FakeResponse(f"$evt{self._n}")

    async def get_displayname(self, mxid):
        return FakeResponse("")


def relation(content: dict) -> dict:
    return content.get("m.relates_to") or {}


def is_edit(content: dict) -> bool:
    return relation(content).get("rel_type") == "m.replace"


def is_threaded(content: dict) -> bool:
    return relation(content).get("rel_type") == "m.thread"


def start_a_fight(bot: Bot, sender: str = "@player:srv") -> None:
    enrol(bot, sender)
    deliver(bot, "!board", sender=sender)
    deliver(bot, "!accept 1", sender=sender)


ROOM = "!game:srv"


def _limit_exceeded() -> RoomSendError:
    return RoomSendError.from_dict(
        {"errcode": "M_LIMIT_EXCEEDED", "error": "slow down", "retry_after_ms": 1},
        ROOM,
    )


@pytest.fixture
def bot(tmp_path, monkeypatch) -> Bot:
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://srv")
    monkeypatch.setenv("MATRIX_USER", "@bot:srv")
    monkeypatch.setenv("MATRIX_ROOM_ID", ROOM)
    monkeypatch.setenv("MATRIX_PASSWORD", "hunter2")
    monkeypatch.setenv("MATRIX_STATE_DIR", str(tmp_path))
    b = Bot()
    b.client = FakeClient()
    b._resumed = True  # skip the cold-start backfill guard unless a test wants it
    return b


def deliver(bot: Bot, body: str, sender: str = "@player:srv", ts: int | None = None):
    asyncio.run(bot.on_message(FakeRoom(ROOM), FakeEvent(sender, body, ts)))


def enrol(bot: Bot, sender: str = "@player:srv", name: str = "Tester") -> None:
    """Drive the register so `sender` has a living character, then reset the
    outbox — most routing tests care about what happens *after* creation."""
    for line in ("create", name, "human", "fighter"):
        deliver(bot, f"!{line}", sender=sender)
    bot.client.sent.clear()


# --- routing ---------------------------------------------------------------

def test_command_gets_a_reply(bot: Bot) -> None:
    enrol(bot)
    deliver(bot, "!board")
    assert len(bot.client.sent) == 1
    assert "Quest Board" in bot.client.sent[0]["body"]


def test_a_character_is_required_before_the_board(bot: Bot) -> None:
    deliver(bot, "!board")
    assert "no character" in bot.client.sent[0]["body"].lower()


def test_chatter_gets_no_reply(bot: Bot) -> None:
    deliver(bot, "hey has anyone seen the patch notes")
    assert bot.client.sent == []


def test_own_events_are_ignored(bot: Bot) -> None:
    """Bot output contains `board` and `accept 1`; replying to itself would loop."""
    deliver(bot, "!board", sender="@bot:srv")
    assert bot.client.sent == []


def test_other_rooms_are_ignored(bot: Bot) -> None:
    asyncio.run(bot.on_message(FakeRoom("!other:srv"), FakeEvent("@p:srv", "!board")))
    assert bot.client.sent == []


def test_cold_start_drops_backfill(bot: Bot) -> None:
    bot._resumed = False
    deliver(bot, "!board", ts=bot.start_ms - 60_000)
    assert bot.client.sent == [], "history older than startup must not re-execute"


def test_cold_start_still_answers_live_messages(bot: Bot) -> None:
    bot._resumed = False
    deliver(bot, "!board", ts=bot.start_ms + 1_000)
    assert len(bot.client.sent) == 1


def test_resumed_start_trusts_the_sync_token(bot: Bot) -> None:
    """With a token, anything delivered is by definition new."""
    bot._resumed = True
    deliver(bot, "!board", ts=bot.start_ms - 60_000)
    assert len(bot.client.sent) == 1


def test_replies_carry_html(bot: Bot) -> None:
    deliver(bot, "!create")
    content = bot.client.sent[0]
    assert content["msgtype"] == "m.notice"
    assert content["format"] == "org.matrix.custom.html"
    assert "<strong>" in content["formatted_body"]


# --- identity + persistence ------------------------------------------------

def test_players_are_keyed_by_mxid(bot: Bot) -> None:
    deliver(bot, "!board", sender="@a:srv")
    deliver(bot, "!board", sender="@b:srv")
    assert set(bot.players) == {"@a:srv", "@b:srv"}


def test_progress_persists_across_a_restart(bot: Bot) -> None:
    bot.players["@a:srv"] = Player(
        mxid="@a:srv",
        display_name="A",
        character=Character(name="Bruni", race_key="dwarf", class_key="cleric",
                            renown=25, gold=99),
    )
    deliver(bot, "!board", sender="@a:srv")  # any command triggers a save

    revived = Bot()  # same MATRIX_STATE_DIR, so this is a restart
    char = revived.players["@a:srv"].character
    assert char is not None and char.name == "Bruni"
    assert char.renown == 25
    assert char.gold == 99


def test_display_name_changes_are_tracked(bot: Bot) -> None:
    deliver(bot, "!board", sender="@a:srv")

    class Renamed(FakeRoom):
        def user_name(self, user_id: str) -> str:
            return "NewName"

    asyncio.run(bot.on_message(Renamed(ROOM), FakeEvent("@a:srv", "!board")))
    assert bot.players["@a:srv"].display_name == "NewName"


# --- resilience ------------------------------------------------------------

def test_a_throwing_handler_does_not_kill_the_loop(bot: Bot, monkeypatch) -> None:
    """One bad command must not take the bot down for everyone."""
    def boom(player, text):
        raise RuntimeError("bad content table")

    monkeypatch.setattr(adapter, "handle", boom)
    deliver(bot, "!board")  # must not raise
    assert len(bot.client.sent) == 1
    assert "went wrong" in bot.client.sent[0]["body"]


def test_rate_limit_is_retried(bot: Bot) -> None:
    err = _limit_exceeded()
    bot.client.responses = [err, object()]
    deliver(bot, "!board")
    assert len(bot.client.sent) == 2, "should have retried once and succeeded"


def test_rate_limit_gives_up_eventually(bot: Bot) -> None:
    err = _limit_exceeded()
    bot.client.responses = [err] * 10
    deliver(bot, "!board")
    assert len(bot.client.sent) == adapter.MAX_SEND_ATTEMPTS


def test_non_rate_limit_errors_are_not_retried(bot: Bot) -> None:
    err = RoomSendError.from_dict(
        {"errcode": "M_FORBIDDEN", "error": "nope"}, ROOM
    )
    bot.client.responses = [err, object()]
    deliver(bot, "!board")
    assert len(bot.client.sent) == 1, "a permission error will not fix itself"


# --- live combat frames ----------------------------------------------------

def test_ordinary_replies_are_plain_messages(bot: Bot) -> None:
    enrol(bot)
    deliver(bot, "!board")
    assert not is_edit(bot.client.sent[-1])
    assert not is_threaded(bot.client.sent[-1])


def test_accepting_a_contract_opens_a_frame(bot: Bot) -> None:
    start_a_fight(bot)
    assert not is_edit(bot.client.sent[-1]), "the first frame is a new message"
    assert "@player:srv" in bot.fights


def test_combat_turns_edit_the_frame_instead_of_posting(bot: Bot) -> None:
    """A 20-turn fight used to be 20 messages in the room."""
    start_a_fight(bot)
    bot.client.sent.clear()

    for _ in range(5):
        if not bot.players["@player:srv"].in_combat:
            break
        deliver(bot, "!1")

    assert bot.client.sent, "nothing was sent"
    edits = [c for c in bot.client.sent if is_edit(c)]
    assert len(edits) >= 3, "combat turns should edit, not accumulate messages"


def test_an_edit_carries_new_content_and_targets_the_frame(bot: Bot) -> None:
    start_a_fight(bot)
    frame = bot.fights["@player:srv"]["frame"]
    bot.client.sent.clear()
    deliver(bot, "!1")

    edit = bot.client.sent[0]
    assert relation(edit)["event_id"] == frame
    assert edit["m.new_content"]["msgtype"] == "m.notice"
    # The fallback is the new content with an edit marker prepended -- asserting
    # "doesn't start with *" would be wrong, since **bold** legitimately does.
    assert edit["body"] == "* " + edit["m.new_content"]["body"]
    assert edit["formatted_body"] == "* " + edit["m.new_content"]["formatted_body"]


def test_the_end_of_a_fight_is_posted_in_the_thread(bot: Bot) -> None:
    start_a_fight(bot)
    root = bot.fights["@player:srv"]["root"]
    bot.client.sent.clear()

    deliver(bot, "!flee")
    last = bot.client.sent[-1]
    assert is_threaded(last)
    assert relation(last)["event_id"] == root
    assert "@player:srv" not in bot.fights, "the frame should be released"


def test_a_failed_edit_falls_back_to_a_new_message(bot: Bot) -> None:
    """Losing an edit must never cost the player their turn."""
    start_a_fight(bot)
    bot.client.sent.clear()
    bot.client.responses = [RoomSendError.from_dict(
        {"errcode": "M_FORBIDDEN", "error": "nope"}, ROOM)]

    deliver(bot, "!1")
    assert len(bot.client.sent) == 2, "should retry as a fresh message"
    assert is_edit(bot.client.sent[0])
    assert not is_edit(bot.client.sent[1])


def test_two_players_get_their_own_frames(bot: Bot) -> None:
    start_a_fight(bot, "@a:srv")
    start_a_fight(bot, "@b:srv")
    assert bot.fights["@a:srv"]["frame"] != bot.fights["@b:srv"]["frame"]


def test_a_display_name_is_never_stored_as_a_raw_mxid(bot: Bot) -> None:
    class Anonymous(FakeRoom):
        def user_name(self, user_id: str) -> str:
            return user_id

    asyncio.run(bot.on_message(Anonymous(ROOM), FakeEvent("@a:srv", "!help")))
    assert bot.players["@a:srv"].display_name == "a"
