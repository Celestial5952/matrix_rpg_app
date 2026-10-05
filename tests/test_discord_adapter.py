"""The Discord adapter, exercised without Discord.

Messages, channels and the client are duck-typed fakes, as in the Matrix
adapter tests. asyncio.run() is used directly to keep test dependencies at
just pytest.
"""

from __future__ import annotations

import asyncio

import discord
import pytest

import adapters.discord as dc
from adapters.discord import (
    MAX_CHUNK,
    Bot,
    player_key,
    split_message,
    user_id_of,
)

HALL = 111
OTHER = 222
A_SERVER = object()   # any non-None guild means "not a DM"


# --- split_message ----------------------------------------------------------

def test_short_replies_are_one_message():
    assert split_message(["a", "b", "c"]) == ["a\nb\nc"]


def test_an_empty_reply_is_still_one_message():
    assert split_message([]) == [""]


def test_long_replies_split_on_line_boundaries_under_the_cap():
    lines = [f"line {i} " + "x" * 80 for i in range(100)]
    chunks = split_message(lines)
    assert len(chunks) > 1
    assert all(len(c) <= MAX_CHUNK for c in chunks)
    assert "\n".join(chunks).split("\n") == lines, "nothing lost, nothing cut"


def test_one_enormous_line_is_split_not_dropped():
    line = "word " * 1000
    chunks = split_message([line])
    assert all(len(c) <= MAX_CHUNK for c in chunks)
    assert " ".join(chunks).split() == line.split()


def test_an_unbreakable_line_is_hard_cut():
    chunks = split_message(["x" * (MAX_CHUNK * 2 + 5)])
    assert [len(c) for c in chunks] == [MAX_CHUNK, MAX_CHUNK, 5]


def test_the_cap_leaves_headroom_below_discords_limit():
    assert MAX_CHUNK <= 1950


# --- identity ---------------------------------------------------------------

def test_player_keys_look_like_mxids_and_round_trip():
    key = player_key(123456789)
    assert key == "@123456789:discord"
    assert user_id_of(key) == 123456789
    assert user_id_of("@alice:matrix.org") is None
    assert user_id_of("@12x:discord") is None


# --- fakes ------------------------------------------------------------------

class FakeChannel:
    def __init__(self, channel_id: int, fail: bool = False) -> None:
        self.id = channel_id
        self.sent: list[tuple[str, discord.AllowedMentions]] = []
        self.fail = fail

    async def send(self, content, allowed_mentions=None):
        if self.fail:
            raise discord.DiscordException("forbidden")
        self.sent.append((content, allowed_mentions))

    @property
    def text(self) -> str:
        return "\n".join(c for c, _ in self.sent)


class FakeUser:
    def __init__(self, user_id: int, name: str = "Tester", bot: bool = False) -> None:
        self.id = user_id
        self.display_name = name
        self.bot = bot


class FakeMessage:
    def __init__(self, author, channel, content, guild=A_SERVER, mentions=()):
        self.author = author
        self.channel = channel
        self.content = content
        self.guild = guild            # None means a DM
        self.mentions = list(mentions)


class FakeClient:
    user = FakeUser(999, "GuildBot", bot=True)

    def __init__(self, channels) -> None:
        self.channels = {c.id: c for c in channels}

    def get_channel(self, channel_id):
        return self.channels.get(channel_id)

    async def fetch_channel(self, channel_id):
        if channel_id in self.channels:
            return self.channels[channel_id]
        raise discord.DiscordException("unknown channel")

    async def wait_until_ready(self):
        return None


@pytest.fixture
def hall() -> FakeChannel:
    return FakeChannel(HALL)


@pytest.fixture
def bot(tmp_path, monkeypatch, hall) -> Bot:
    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", str(HALL))
    monkeypatch.setenv("DISCORD_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DISCORD_TRAVEL_SECONDS", "0")   # no real marching in tests
    b = Bot()
    b.client = FakeClient([hall, FakeChannel(OTHER)])
    return b


def say(bot: Bot, text: str, user_id: int = 1, channel=None, name: str = "Tester",
        guild=A_SERVER, mentions=()):
    channel = channel or bot.client.channels[HALL]
    asyncio.run(bot.on_message(FakeMessage(
        FakeUser(user_id, name), channel, text, guild, mentions)))


def enrol(bot: Bot, user_id: int = 1, name: str = "Hero") -> None:
    for line in ("create", name, "human", "fighter"):
        say(bot, f"!{line}", user_id)
    bot.client.channels[HALL].sent.clear()


# --- configuration ----------------------------------------------------------

def test_token_and_channel_are_required(tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_CHANNEL_ID", raising=False)
    with pytest.raises(SystemExit, match="DISCORD_TOKEN"):
        Bot()
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    with pytest.raises(SystemExit, match="DISCORD_CHANNEL_ID"):
        Bot()


def test_a_channel_name_instead_of_an_id_explains_how_to_find_it(
        tmp_path, monkeypatch):
    monkeypatch.setenv("DISCORD_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", "#general")
    with pytest.raises(SystemExit, match="Copy Channel ID"):
        Bot()


def test_defaults(bot: Bot):
    assert bot.allow_dms is True
    assert bot.admins == set()
    assert bot.backup_hours == 24 and bot.backup_keep == 14


@pytest.mark.parametrize("raw,expected", [
    ("false", False), ("0", False), ("No", False), ("off", False),
    ("true", True), ("1", True), ("yes", True),
])
def test_dm_flag_parsing(tmp_path, monkeypatch, raw, expected):
    monkeypatch.setenv("DISCORD_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", str(HALL))
    monkeypatch.setenv("DISCORD_ALLOW_DMS", raw)
    assert Bot().allow_dms is expected


def test_the_client_asks_for_the_message_content_intent(bot: Bot):
    assert Bot().client.intents.message_content is True


# --- routing ----------------------------------------------------------------

def test_a_command_in_the_hall_gets_a_reply(bot: Bot, hall):
    enrol(bot)
    say(bot, "!board")
    assert "Quest Board" in hall.text


def test_chatter_gets_no_reply(bot: Bot, hall):
    say(bot, "has anyone seen the patch notes")
    assert hall.sent == []
    assert bot.players == {}, "chatter must not even register a player"


def test_other_channels_are_ignored(bot: Bot):
    other = bot.client.channels[OTHER]
    say(bot, "!board", channel=other)
    assert other.sent == [] and bot.players == {}


def test_bots_are_ignored_including_ourselves(bot: Bot, hall):
    asyncio.run(bot.on_message(FakeMessage(FakeUser(5, "OtherBot", bot=True),
                                           hall, "!board")))
    asyncio.run(bot.on_message(FakeMessage(FakeClient.user, hall, "!board")))
    assert hall.sent == []


def test_a_character_is_required_before_the_board(bot: Bot, hall):
    say(bot, "!board")
    assert "no character" in hall.text.lower()


def test_a_prefixed_typo_is_never_met_with_silence(bot: Bot, hall):
    enrol(bot)
    say(bot, "!bord")
    assert "Did you mean" in hall.text and "!board" in hall.text


def test_players_are_separate_by_user_id(bot: Bot):
    enrol(bot, 1, "Doc")
    enrol(bot, 2, "Wren")
    assert set(bot.players) == {"@1:discord", "@2:discord"}
    assert bot.players["@1:discord"].character.name == "Doc"
    assert bot.players["@2:discord"].character.name == "Wren"


def test_the_display_name_follows_discord_but_the_character_does_not(bot: Bot):
    enrol(bot, 1, "Doc")
    say(bot, "!status", 1, name="Renamed In Discord")
    player = bot.players["@1:discord"]
    assert player.display_name == "Renamed In Discord"
    assert player.character.name == "Doc"


def test_a_full_contract_can_be_played_through_the_adapter(bot: Bot, hall):
    enrol(bot)
    say(bot, "!board")
    say(bot, "!accept 1")
    for _ in range(600):
        player = bot.players["@1:discord"]
        if player.character is None or player.character.run is None:
            break
        player.character.run.hp = player.character.run.max_hp
        say(bot, "!1")
    assert "CONTRACT COMPLETE" in hall.text


# --- direct messages --------------------------------------------------------

def test_dms_work_and_are_marked_private(bot: Bot):
    dm = FakeChannel(333)
    say(bot, "!create", channel=dm, guild=None)
    for line in ("!Solo", "!human", "!fighter"):
        say(bot, line, channel=dm, guild=None)
    dm.sent.clear()
    say(bot, "!party", channel=dm, guild=None)
    assert "needs other people" in dm.text, "party stays in the hall"


def test_dms_can_be_switched_off(bot: Bot):
    bot.allow_dms = False
    dm = FakeChannel(333)
    say(bot, "!create", channel=dm, guild=None)
    assert dm.sent == [] and bot.players == {}


def test_the_hall_and_a_dm_share_one_character(bot: Bot, hall):
    enrol(bot, 1, "Doc")
    dm = FakeChannel(333)
    say(bot, "!status", channel=dm, guild=None)
    assert "Doc" in dm.text


# --- nobody gets pinged by accident -----------------------------------------

def test_replies_never_ping_anyone(bot: Bot, hall):
    enrol(bot, 1, "Doc")
    say(bot, "!status")
    assert hall.sent
    for _, allowed in hall.sent:
        assert allowed.everyone is False
        assert allowed.users is False and allowed.roles is False


def test_an_everyone_name_is_neutralised(bot: Bot, hall):
    for line in ("create", "@everyone", "human", "fighter"):
        say(bot, f"!{line}")
    assert hall.sent
    assert all(a.everyone is False and a.roles is False for _, a in hall.sent)


# --- sending ----------------------------------------------------------------

def test_long_replies_are_split_across_messages(bot: Bot, hall):
    asyncio.run(bot._send(hall, [("x" * 90)] * 60))
    assert len(hall.sent) > 1
    assert all(len(c) <= MAX_CHUNK for c, _ in hall.sent)


def test_a_failed_send_is_logged_not_raised(bot: Bot):
    broken = FakeChannel(HALL, fail=True)
    assert asyncio.run(bot._send(broken, ["hi"])) is False


def test_sending_to_a_missing_channel_is_harmless(bot: Bot):
    assert asyncio.run(bot._send(None, ["hi"])) is False


def test_an_arrival_mentions_exactly_that_player(bot: Bot, hall):
    asyncio.run(bot._send(hall, ["The road ends."], mention=42))
    content, allowed = hall.sent[0]
    assert content.startswith("<@42> ")
    assert [u.id for u in allowed.users] == [42]
    assert allowed.everyone is False and allowed.roles is False


def test_handle_raising_replies_with_an_apology_not_a_crash(bot: Bot, hall,
                                                            monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr(dc, "handle", boom)
    say(bot, "!board")
    assert "Something went wrong" in hall.text


# --- mentions ---------------------------------------------------------------

def test_discord_mentions_resolve_to_the_right_player(bot: Bot, hall):
    enrol(bot, 1, "Doc")
    enrol(bot, 2, "Wren")
    bot.players["@1:discord"].character.inventory["lesser_potion"] = 1
    target = FakeUser(2, "Wren")
    say(bot, "!give <@2> potion", 1, mentions=[target])
    assert bot.players["@2:discord"].character.inventory == {"lesser_potion": 1}


def test_mentioning_the_bot_is_not_a_target(bot: Bot, hall):
    enrol(bot, 1, "Doc")
    bot.players["@1:discord"].character.inventory["lesser_potion"] = 1
    say(bot, "!give <@999> potion", 1, mentions=[FakeClient.user])
    assert "Nobody here" in hall.text


# --- persistence ------------------------------------------------------------

def test_state_survives_a_restart(bot: Bot, tmp_path, monkeypatch):
    enrol(bot, 1, "Doc")
    bot.players["@1:discord"].character.gold = 77
    bot._save()

    monkeypatch.setenv("DISCORD_TOKEN", "tok")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", str(HALL))
    monkeypatch.setenv("DISCORD_STATE_DIR", str(tmp_path))
    again = Bot()
    assert again.players["@1:discord"].character.gold == 77
    assert again.players["@1:discord"].character.name == "Doc"


def test_every_reply_is_saved(bot: Bot, tmp_path):
    enrol(bot, 1, "Doc")
    assert (tmp_path / "players.json").exists()
    assert (tmp_path / "guild.json").exists()


def test_state_is_separate_from_the_matrix_default(tmp_path, monkeypatch):
    monkeypatch.delenv("DISCORD_STATE_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", str(HALL))
    assert Bot().state_dir.as_posix() == "store/discord"


# --- operators --------------------------------------------------------------

def test_admins_use_numeric_ids(bot: Bot, monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", str(HALL))
    monkeypatch.setenv("DISCORD_STATE_DIR", str(tmp_path / "x"))
    monkeypatch.setenv("DISCORD_ADMINS", " 7 , 8 ,, notanid")
    assert Bot().admins == {"7", "8", "notanid"}


def test_an_admin_gets_operator_commands_and_others_do_not(bot: Bot, hall):
    bot.admins = {"1"}
    enrol(bot, 1, "Boss")
    enrol(bot, 2, "Doc")
    say(bot, "!admin players", 1)
    assert "@2:discord" in hall.text
    hall.sent.clear()
    say(bot, "!admin players", 2)
    assert "I don't know `!admin`" in hall.text


def test_announce_posts_to_the_hall_even_from_a_dm(bot: Bot, hall):
    bot.admins = {"1"}
    dm = FakeChannel(333)
    say(bot, "!admin announce Maintenance at 9pm!", 1, channel=dm, guild=None)
    assert "Maintenance at 9pm!" in hall.text
    assert "Posted to the hall" in dm.text


def test_announce_needs_text(bot: Bot, hall):
    bot.admins = {"1"}
    say(bot, "!admin announce", 1)
    assert "posts a notice" in hall.text


def test_admin_backup_snapshots_live_state(bot: Bot, hall, tmp_path):
    bot.admins = {"1"}
    enrol(bot, 1, "Boss")
    say(bot, "!admin backup", 1)
    assert "Snapshot written" in hall.text
    assert len(list((tmp_path / "backups").iterdir())) == 1
    hall.sent.clear()
    say(bot, "!admin backup", 1)
    assert "Nothing new" in hall.text


# --- background tasks -------------------------------------------------------

def test_arrivals_are_delivered_to_where_the_player_last_spoke(bot: Bot, hall,
                                                               monkeypatch):
    dm = FakeChannel(333)
    enrol(bot, 1, "Doc")
    say(bot, "!status", 1, channel=dm, guild=None)
    monkeypatch.setattr(dc, "arrive", lambda p: ["🐺 A wolf!"]
                        if p.mxid == "@1:discord" else None)

    monkeypatch.setattr(dc.asyncio, "sleep", _fast_sleep())
    asyncio.run(_let_the_ticker_run(bot))
    assert "A wolf!" in dm.text and "<@1>" in dm.text
    assert "A wolf!" not in hall.text


def test_arrivals_fall_back_to_the_hall_after_a_restart(bot: Bot, hall,
                                                        monkeypatch):
    enrol(bot, 1, "Doc")
    bot.player_channels.clear()
    monkeypatch.setattr(dc, "arrive", lambda p: ["🐺 A wolf!"])
    monkeypatch.setattr(dc.asyncio, "sleep", _fast_sleep())

    asyncio.run(_let_the_ticker_run(bot))
    assert "A wolf!" in hall.text


def test_one_failing_arrival_does_not_stop_the_others(bot: Bot, hall, monkeypatch):
    enrol(bot, 1, "Doc")
    enrol(bot, 2, "Wren")

    def flaky(player):
        if player.mxid == "@1:discord":
            raise RuntimeError("boom")
        return ["🐺 A wolf!"]

    monkeypatch.setattr(dc, "arrive", flaky)
    monkeypatch.setattr(dc.asyncio, "sleep", _fast_sleep())

    asyncio.run(_let_the_ticker_run(bot))
    assert "A wolf!" in hall.text


_REAL_SLEEP = asyncio.sleep


def _fast_sleep():
    async def fast(delay, *a, **k):
        await _REAL_SLEEP(0.001)

    return fast


async def _let_the_ticker_run(bot) -> None:
    """Run the ticker briefly. The adapter's sleeps are patched to be instant,
    so the test waits on the real clock."""
    task = asyncio.create_task(bot._travel_ticker())
    await _REAL_SLEEP(0.1)
    task.cancel()


def test_the_backup_loop_survives_errors(bot: Bot, monkeypatch):
    calls = []

    def flaky(state_dir, keep):
        calls.append(state_dir)
        if len(calls) == 1:
            raise RuntimeError("boom")

    monkeypatch.setattr(dc, "snapshot", flaky)
    bot.backup_hours = 0.00001

    async def run():
        task = asyncio.create_task(bot._backup_loop())
        await asyncio.sleep(0.2)
        task.cancel()

    asyncio.run(run())
    assert len(calls) >= 2


def test_on_ready_starts_tasks_once_and_not_during_a_check(bot: Bot):
    async def go():
        await bot.on_ready()
        first = list(bot._tasks)
        await bot.on_ready()          # a reconnect fires it again
        assert bot._tasks == first
        for t in bot._tasks:
            t.cancel()

    asyncio.run(go())
    assert len(bot._tasks) == 2

    quiet = Bot.__new__(Bot)
    quiet.__dict__.update(bot.__dict__)
    quiet._tasks, quiet._ready, quiet.checking = [], None, True
    asyncio.run(quiet.on_ready())
    assert quiet._tasks == []


# --- the --check onboarding doctor ------------------------------------------

class CheckClient(FakeClient):
    """Pretends to log in; behaviour chosen per test."""

    def __init__(self, bot, channels, outcome="ok", guilds=("Guildhall",)):
        super().__init__(channels)
        self.bot = bot
        self.outcome = outcome
        self.guilds = [type("G", (), {"name": n})() for n in guilds]
        self.application_id = 4242
        self.closed = False

    async def start(self, token):
        if self.outcome == "bad_token":
            raise discord.LoginFailure("nope")
        if self.outcome == "no_intent":
            raise discord.PrivilegedIntentsRequired(None)
        await self.bot.on_ready()
        while not self.closed:
            await asyncio.sleep(0.01)

    async def close(self):
        self.closed = True


def run_check(bot, capsys, **kw):
    bot.client = CheckClient(bot, list(bot.client.channels.values()), **kw)
    code = asyncio.run(bot.check())
    return code, capsys.readouterr().out


def test_check_passes_on_a_healthy_setup(bot: Bot, capsys):
    code, out = run_check(bot, capsys)
    assert code == 0
    assert "Logged in" in out and "Message Content intent" in out
    assert "Hall channel" in out
    assert "oauth2/authorize" in out and "4242" in out
    assert bot._tasks == [], "checking must not start the game loops"


def test_check_explains_a_bad_token(bot: Bot, capsys):
    code, out = run_check(bot, capsys, outcome="bad_token")
    assert code == 1 and "rejected the bot token" in out


def test_check_explains_the_missing_intent(bot: Bot, capsys):
    code, out = run_check(bot, capsys, outcome="no_intent")
    assert code == 1 and "MESSAGE CONTENT INTENT" in out


def test_check_says_when_the_bot_is_in_no_server(bot: Bot, capsys):
    code, out = run_check(bot, capsys, guilds=())
    assert code == 1 and "isn't in any server" in out and "oauth2/authorize" in out


def test_check_says_when_the_hall_is_not_visible(bot: Bot, capsys):
    bot.channel_id = 5555
    code, out = run_check(bot, capsys)
    assert code == 1 and "Can't see channel 5555" in out


def test_the_real_client_has_our_handlers_registered(tmp_path, monkeypatch):
    """client.event() wires handlers by function name; a typo would mean a bot
    that logs in and silently never sees a message."""
    monkeypatch.setenv("DISCORD_TOKEN", "t")
    monkeypatch.setenv("DISCORD_CHANNEL_ID", str(HALL))
    monkeypatch.setenv("DISCORD_STATE_DIR", str(tmp_path))
    real = Bot()
    assert real.client.on_message == real.on_message
    assert real.client.on_ready == real.on_ready
