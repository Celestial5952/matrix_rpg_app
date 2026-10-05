"""The setup wizard: pure helpers, then whole conversations against a fake server."""

from __future__ import annotations

import asyncio
import os
import stat

import pytest
from nio import (
    DiscoveryInfoError,
    DiscoveryInfoResponse,
    JoinedRoomsResponse,
    JoinError,
    JoinResponse,
    LoginError,
    LoginInfoError,
    LoginInfoResponse,
    LoginResponse,
    RoomCreateError,
    RoomCreateResponse,
    RoomResolveAliasError,
    RoomResolveAliasResponse,
    WhoamiError,
    WhoamiResponse,
)

from tools import setup as wiz
from tools.setup import (
    Aborted,
    Wizard,
    env_value,
    normalise_homeserver,
    normalise_mxid,
    parse_mxid_list,
    read_env,
    render_env,
)

# --- pure helpers ----------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("matrix.example.org", ("https://matrix.example.org", "matrix.example.org")),
    ("  example.org/ ", ("https://example.org", "example.org")),
    ("http://localhost:8008", ("http://localhost:8008", "localhost:8008")),
    ("https://m.example.org:8448/", ("https://m.example.org:8448",
                                     "m.example.org:8448")),
    ("@bot:example.org", ("https://example.org", "example.org")),
])
def test_homeserver_forms_are_accepted(raw, expected):
    assert normalise_homeserver(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "ftp://x.org", "://", "@nocolon"])
def test_nonsense_homeservers_are_rejected(raw):
    assert normalise_homeserver(raw) is None


def test_mxid_forms():
    assert normalise_mxid("@bot:example.org") == "@bot:example.org"
    assert normalise_mxid("bot", "example.org") == "@bot:example.org"
    assert normalise_mxid("bot") is None, "no server name to complete it with"
    assert normalise_mxid("bot:example.org", "example.org") is None
    assert normalise_mxid("@bot", "example.org") is None
    assert normalise_mxid("@a b:example.org") is None
    assert normalise_mxid("") is None


def test_mxid_lists_split_valid_from_rejected():
    good, bad = parse_mxid_list("@a:s.org, b ,, @c , @d:s.org", "s.org")
    assert good == ["@a:s.org", "@b:s.org", "@d:s.org"]
    assert bad == ["@c"]


def test_env_values_are_quoted_only_when_they_must_be():
    assert env_value("syt_abc.DEF-123") == "syt_abc.DEF-123"
    assert env_value("!abc:srv") == "!abc:srv"
    assert env_value("https://m.example.org") == "https://m.example.org"
    assert env_value("@a:s,@b:s") == "@a:s,@b:s"
    assert env_value("pass word") == "'pass word'"
    assert env_value("pa$$word") == "'pa$$word'"
    assert env_value("#leading") == "'#leading'"
    assert env_value("a#b") == "a#b"


def test_values_that_cannot_be_stored_safely_are_refused():
    for bad in ("it's", "two\nlines"):
        with pytest.raises(ValueError):
            env_value(bad)


def test_env_round_trips_through_read_env():
    for value in ("plain", "pa$$ word", "!id:srv", "x#y"):
        assert read_env(f"K={env_value(value)}\n")["K"] == value


def test_read_env_ignores_comments_and_blank_lines():
    text = "# MATRIX_TOKEN=nope\n\nA=1\n  B = 2\nC=\n"
    assert read_env(text) == {"A": "1", "C": ""}


TEMPLATE = """# header
MATRIX_HOMESERVER=https://matrix.example.org
MATRIX_USER=@guildbot:example.org
MATRIX_PASSWORD=change-me
# ...or use a token instead of a password:
# MATRIX_TOKEN=syt_...
MATRIX_ROOM_ID=#guildhall:example.org
MATRIX_TRAVEL_SECONDS=300
# MATRIX_ADMINS=@you:example.org
"""


def test_render_replaces_in_place_and_keeps_everything_else():
    out = render_env(TEMPLATE, {
        "MATRIX_HOMESERVER": "https://m.x.org",
        "MATRIX_TOKEN": "syt_real",
        "MATRIX_PASSWORD": None,
        "MATRIX_ADMINS": "@me:x.org",
    })
    env = read_env(out)
    assert env["MATRIX_HOMESERVER"] == "https://m.x.org"
    assert env["MATRIX_TOKEN"] == "syt_real"
    assert env["MATRIX_ADMINS"] == "@me:x.org"
    assert "MATRIX_PASSWORD" not in env, "the password must leave the file"
    assert "change-me" not in out
    assert env["MATRIX_TRAVEL_SECONDS"] == "300"
    assert "# header" in out and "# ...or use a token" in out
    assert out.index("MATRIX_TOKEN=") > out.index("# ...or use a token")


def test_render_appends_keys_the_template_lacks():
    out = render_env("A=1\n", {"MATRIX_ADMINS": "@a:s"})
    assert read_env(out) == {"A": "1", "MATRIX_ADMINS": "@a:s"}


def test_none_for_a_missing_key_adds_nothing():
    assert render_env("A=1\n", {"B": None}) == "A=1\n"


def test_key_prefixes_do_not_collide():
    out = render_env("MATRIX_TRAVEL_SECONDS=300\nMATRIX_TRAVEL_TICK=20\n",
                     {"MATRIX_TRAVEL_TICK": "5"})
    assert read_env(out) == {"MATRIX_TRAVEL_SECONDS": "300",
                             "MATRIX_TRAVEL_TICK": "5"}


def test_the_shipped_template_has_every_key_the_wizard_sets():
    from pathlib import Path
    text = (Path(wiz.__file__).resolve().parent.parent / ".env.example").read_text()
    for key in ("MATRIX_HOMESERVER", "MATRIX_USER", "MATRIX_PASSWORD",
                "MATRIX_TOKEN", "MATRIX_ROOM_ID", "MATRIX_ADMINS"):
        assert key in text, key


def test_login_errors_get_a_human_explanation():
    assert "doesn't exist yet" in wiz.explain_login_error("M_FORBIDDEN", "bad")
    assert wiz.explain_login_error("M_WEIRD", "raw text") == "raw text"


# --- a scripted conversation against a fake server -------------------------

class Script:
    """Stands in for the console: feeds canned answers, records what was said."""

    def __init__(self, answers, secrets=(), confirms=()):
        self.answers = list(answers)
        self.secrets = list(secrets)
        self.confirms = list(confirms)
        self.said: list[str] = []
        self.prompts: list[str] = []

    def say(self, text=""):
        self.said.append(text)

    def ask(self, prompt, default=""):
        self.prompts.append(prompt)
        if not self.answers:
            raise Aborted
        return self.answers.pop(0) or default

    def secret(self, prompt):
        if not self.secrets:
            raise Aborted
        return self.secrets.pop(0)

    def confirm(self, prompt, default=True):
        return self.confirms.pop(0) if self.confirms else default

    @property
    def transcript(self):
        return "\n".join(self.said)


class FakeRoomState:
    def __init__(self, encrypted=False):
        self.encrypted = encrypted
        self.power_levels = type("PL", (), {
            "users": {},
            "defaults": type("D", (), {"users_default": 0, "events_default": 0})(),
        })()


class FakeClient:
    """Just enough AsyncClient for the wizard. Behaviour is set per test."""

    registry: list[FakeClient] = []
    config: dict = {}

    def __init__(self, homeserver, user=""):
        self.homeserver = homeserver
        self.user_id = user
        self.access_token = ""
        self.rooms: dict = {}
        self.calls: list[tuple] = []
        self.closed = False
        self.logged_out = False
        FakeClient.registry.append(self)

    # behaviour switches, read from the class-level config
    def _c(self, key, default=None):
        return FakeClient.config.get(key, default)

    async def discovery_info(self):
        self.calls.append(("discovery",))
        found = self._c("discovery")
        return found or DiscoveryInfoError("no well-known", "M_NOT_FOUND")

    async def login_info(self):
        self.calls.append(("login_info", self.homeserver))
        if self._c("unreachable"):
            raise OSError("connection refused")
        if self._c("not_matrix"):
            return LoginInfoError("404", "M_UNRECOGNIZED")
        return LoginInfoResponse(self._c("flows", ["m.login.password"]))

    async def login(self, password, device_name=""):
        self.calls.append(("login", password))
        accepted = self._c("password", "hunter2")
        if password != accepted:
            return LoginError("Invalid password", "M_FORBIDDEN")
        self.access_token = "syt_tok"
        return LoginResponse("@guildbot:example.org", "DEV", "syt_tok")

    async def whoami(self):
        if self.access_token != self._c("valid_token", "syt_good"):
            return WhoamiError("bad token", "M_UNKNOWN_TOKEN")
        return WhoamiResponse("@guildbot:example.org", "DEV", False)

    async def logout(self):
        self.logged_out = True

    async def room_resolve_alias(self, alias):
        known = self._c("aliases", {"#hall:example.org": "!hall:example.org"})
        if alias in known:
            return RoomResolveAliasResponse(alias, known[alias], [])
        return RoomResolveAliasError("not found", "M_NOT_FOUND")

    async def joined_rooms(self):
        return JoinedRoomsResponse(list(self._c("in_rooms", [])))

    async def join(self, room_id):
        self.calls.append(("join", room_id))
        invited_after = self._c("invited_after_attempts", 0)
        attempts = sum(1 for c in FakeClient.registry for x in c.calls
                       if x[0] == "join")
        if attempts <= invited_after:
            return JoinError("not invited", "M_FORBIDDEN")
        FakeClient.config.setdefault("in_rooms", []).append(room_id)
        return JoinResponse(room_id)

    async def sync(self, timeout=0, full_state=False):
        from nio import SyncResponse
        rid = self._c("sync_room", "!hall:example.org")
        self.rooms[rid] = FakeRoomState(self._c("encrypted", False))
        return SyncResponse.__new__(SyncResponse)

    async def room_get_state_event(self, room_id, event_type):
        return type("R", (), {"content": {"type": self._c("room_type", "")}})()

    async def room_create(self, **kwargs):
        self.calls.append(("room_create", kwargs))
        if self._c("create_fails"):
            return RoomCreateError("not allowed", "M_FORBIDDEN")
        return RoomCreateResponse("!new:example.org")

    async def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def fake_server(monkeypatch):
    FakeClient.registry = []
    FakeClient.config = {}
    monkeypatch.setattr(wiz, "SyncResponse", __import__("nio").SyncResponse)
    yield FakeClient.config


def run_wizard(tmp_path, script, env_name=".env", preexisting=None):
    env = tmp_path / env_name
    if preexisting is not None:
        env.write_text(preexisting)
    template = tmp_path / ".env.example"
    template.write_text(TEMPLATE)
    wizard = Wizard(script, env, client_factory=FakeClient, template_path=template)
    code = asyncio.run(wizard.run())
    return code, env


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_happy_path_existing_room_by_alias_stores_a_token(tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "#hall:example.org",
                 "@me:example.org"],
        secrets=["hunter2"], confirms=[True])
    code, env = run_wizard(tmp_path, script)

    assert code == 0
    values = read_env(env.read_text())
    assert values["MATRIX_HOMESERVER"] == "https://example.org"
    assert values["MATRIX_USER"] == "@guildbot:example.org"
    assert values["MATRIX_TOKEN"] == "syt_tok"
    assert "MATRIX_PASSWORD" not in values
    assert values["MATRIX_ROOM_ID"] == "!hall:example.org", "alias stored as the id"
    assert values["MATRIX_ADMINS"] == "@me:example.org"
    assert "hunter2" not in env.read_text()
    assert mode(env) == 0o600
    assert (tmp_path / "store").is_dir()
    assert all(c.closed for c in FakeClient.registry)


def test_choosing_a_password_logs_out_the_setup_session(tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    script = Script(
        answers=["example.org", "guildbot", "1", "!hall:example.org", ""],
        secrets=["hunter2"], confirms=[False])
    code, env = run_wizard(tmp_path, script)

    values = read_env(env.read_text())
    assert code == 0
    assert values["MATRIX_PASSWORD"] == "hunter2"
    assert "MATRIX_TOKEN" not in values
    assert any(c.logged_out for c in FakeClient.registry), "no stray device"
    assert "MATRIX_ADMINS" not in values


def test_a_localpart_is_completed_with_the_server_name(tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    code, env = run_wizard(tmp_path, Script(
        answers=["example.org", "guildbot", "1", "!hall:example.org", ""],
        secrets=["hunter2"]))
    assert read_env(env.read_text())["MATRIX_USER"] == "@guildbot:example.org"


def test_well_known_delegation_is_followed(tmp_path, fake_server):
    fake_server["discovery"] = DiscoveryInfoResponse("https://matrix.example.org/")
    fake_server["in_rooms"] = ["!hall:example.org"]
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "!hall:example.org", ""],
        secrets=["hunter2"])
    code, env = run_wizard(tmp_path, script)
    assert code == 0
    assert read_env(env.read_text())["MATRIX_HOMESERVER"] == \
        "https://matrix.example.org"
    assert "delegates to" in script.transcript


def test_a_full_url_skips_discovery(tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    script = Script(
        answers=["https://m.example.org", "@guildbot:example.org", "1",
                 "!hall:example.org", ""], secrets=["hunter2"])
    run_wizard(tmp_path, script)
    assert not any(call == ("discovery",)
                   for c in FakeClient.registry for call in c.calls)


def test_an_unreachable_server_is_retried_then_gives_up(tmp_path, fake_server):
    fake_server["unreachable"] = True
    script = Script(answers=["nowhere.example"] * 10)
    code, env = run_wizard(tmp_path, script)
    assert code == 1 and not env.exists()
    assert "Couldn't reach" in script.transcript
    assert "nothing was written" in script.transcript


def test_a_non_matrix_address_is_reported(tmp_path, fake_server):
    fake_server["not_matrix"] = True
    script = Script(answers=["example.com"])
    code, _ = run_wizard(tmp_path, script)
    assert code == 1
    assert "didn't answer like a Matrix server" in script.transcript


def test_a_wrong_password_is_explained_and_retried(tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    script = Script(
        answers=["example.org", "@guildbot:example.org", "@guildbot:example.org",
                 "1", "!hall:example.org", ""],
        secrets=["nope", "hunter2"])
    code, env = run_wizard(tmp_path, script)
    assert code == 0
    assert "doesn't exist yet" in script.transcript
    assert read_env(env.read_text())["MATRIX_TOKEN"] == "syt_tok"


def test_endless_wrong_passwords_abort_without_writing(tmp_path, fake_server):
    script = Script(answers=["example.org"] + ["@guildbot:example.org"] * 10,
                    secrets=["x"] * 10)
    code, env = run_wizard(tmp_path, script)
    assert code == 1 and not env.exists()


def test_sso_only_servers_ask_for_a_pasted_token(tmp_path, fake_server):
    fake_server["flows"] = ["m.login.sso"]
    fake_server["in_rooms"] = ["!hall:example.org"]
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "!hall:example.org", ""],
        secrets=["syt_good"])
    code, env = run_wizard(tmp_path, script)
    assert code == 0
    assert read_env(env.read_text())["MATRIX_TOKEN"] == "syt_good"
    assert "single sign-on" in script.transcript


def test_a_bad_pasted_token_is_rejected(tmp_path, fake_server):
    fake_server["flows"] = ["m.login.sso"]
    script = Script(answers=["example.org"] + ["@guildbot:example.org"] * 6,
                    secrets=["wrong"] * 6)
    code, env = run_wizard(tmp_path, script)
    assert code == 1 and not env.exists()
    assert "doesn't recognise that access token" in script.transcript


def test_a_room_the_bot_has_not_been_invited_to_waits_for_the_invite(
        tmp_path, fake_server):
    fake_server["invited_after_attempts"] = 1       # first join refused
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "!hall:example.org", ""],
        secrets=["hunter2"], confirms=[True, True])  # token; "invited? try again"
    code, env = run_wizard(tmp_path, script)
    assert code == 0
    assert "invite @guildbot:example.org" in script.transcript
    assert read_env(env.read_text())["MATRIX_ROOM_ID"] == "!hall:example.org"


def test_refusing_to_retry_the_invite_returns_to_the_menu(tmp_path, fake_server):
    fake_server["invited_after_attempts"] = 99
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "!hall:example.org"],
        secrets=["hunter2"], confirms=[True, False])
    code, env = run_wizard(tmp_path, script)
    assert code == 1 and not env.exists()    # ran out of answers at the menu


def test_an_encrypted_room_is_refused_and_creation_offered(tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    fake_server["encrypted"] = True
    script = Script(
        answers=["example.org", "@guildbot:example.org",
                 "1", "!hall:example.org",          # encrypted -> refused
                 "2", "@me:example.org", "", "@me:example.org"],
        secrets=["hunter2"])
    code, env = run_wizard(tmp_path, script)
    assert code == 0
    assert "encrypted" in script.transcript
    assert read_env(env.read_text())["MATRIX_ROOM_ID"] == "!new:example.org"


def test_a_space_is_refused(tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    fake_server["room_type"] = "m.space"
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "!hall:example.org"],
        secrets=["hunter2"])
    code, env = run_wizard(tmp_path, script)
    assert code == 1 and not env.exists()
    assert "That's a space" in script.transcript


def test_an_unknown_alias_is_reported(tmp_path, fake_server):
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "#nope:example.org"],
        secrets=["hunter2"])
    run_wizard(tmp_path, script)
    assert "doesn't resolve" in script.transcript


def test_a_malformed_room_target_is_reported(tmp_path, fake_server):
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "hall"],
        secrets=["hunter2"])
    run_wizard(tmp_path, script)
    assert "should start with #" in script.transcript


def test_creating_a_room_makes_it_private_unencrypted_and_invites_you(
        tmp_path, fake_server):
    script = Script(
        answers=["example.org", "@guildbot:example.org", "2", "me", "Our Hall", ""],
        secrets=["hunter2"])
    code, env = run_wizard(tmp_path, script)

    assert code == 0
    created = next(call[1] for c in FakeClient.registry for call in c.calls
                   if call[0] == "room_create")
    assert created["invite"] == ["@me:example.org"], "bare name completed"
    assert created["name"] == "Our Hall"
    assert "initial_state" not in created, "nothing may switch encryption on"
    assert created["power_level_override"]["users"]["@me:example.org"] == 100

    values = read_env(env.read_text())
    assert values["MATRIX_ROOM_ID"] == "!new:example.org"
    assert values["MATRIX_ADMINS"] == "@me:example.org", "creator is the default admin"


def test_a_failed_room_creation_is_reported_and_retried(tmp_path, fake_server):
    fake_server["create_fails"] = True
    script = Script(
        answers=["example.org", "@guildbot:example.org", "2", "@me:example.org", "",
                 "2", "@me:example.org", ""],
        secrets=["hunter2"])
    code, _ = run_wizard(tmp_path, script)
    assert code == 1
    assert "Couldn't create the room" in script.transcript


def test_bad_admin_ids_are_rejected_then_accepted(tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "!hall:example.org",
                 "@broken", "@ok:example.org, friend"],
        secrets=["hunter2"])
    code, env = run_wizard(tmp_path, script)
    assert code == 0
    assert "Not valid Matrix IDs" in script.transcript
    assert read_env(env.read_text())["MATRIX_ADMINS"] == \
        "@ok:example.org,@friend:example.org"


# --- an existing .env ------------------------------------------------------

def test_rerunning_keeps_other_settings_and_backs_up_the_old_file(
        tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    old = "MATRIX_PASSWORD=oldsecret\nMATRIX_TRAVEL_SECONDS=0\n# my note\n"
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "!hall:example.org", ""],
        secrets=["hunter2"])
    code, env = run_wizard(tmp_path, script, preexisting=old)

    assert code == 0
    new = env.read_text()
    assert read_env(new)["MATRIX_TRAVEL_SECONDS"] == "0", "user setting survives"
    assert "# my note" in new
    assert "oldsecret" not in new, "the old password leaves the live file"
    backup = tmp_path / ".env.bak"
    assert backup.read_text() == old
    assert mode(backup) == 0o600


def test_existing_values_are_offered_as_defaults(tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    old = ("MATRIX_HOMESERVER=https://example.org\n"
           "MATRIX_USER=@guildbot:example.org\n"
           "MATRIX_ROOM_ID=!hall:example.org\n"
           "MATRIX_ADMINS=@me:example.org\n")
    # every prompt answered with "just press Enter"
    script = Script(answers=["", "", "", "", ""], secrets=["hunter2"])
    code, env = run_wizard(tmp_path, script, preexisting=old)
    assert code == 0
    values = read_env(env.read_text())
    assert values["MATRIX_HOMESERVER"] == "https://example.org"
    assert values["MATRIX_ROOM_ID"] == "!hall:example.org"
    assert values["MATRIX_ADMINS"] == "@me:example.org"


def test_a_password_that_cannot_be_stored_is_refused_cleanly(tmp_path, fake_server):
    fake_server["password"] = "it's"
    fake_server["in_rooms"] = ["!hall:example.org"]
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "!hall:example.org", ""],
        secrets=["it's"], confirms=[False])
    code, env = run_wizard(tmp_path, script)
    assert code == 1 and not env.exists()
    assert "single quote" in script.transcript


def test_ctrl_c_writes_nothing(tmp_path, fake_server):
    script = Script(answers=[])          # first prompt raises Aborted
    code, env = run_wizard(tmp_path, script)
    assert code == 1 and not env.exists()
    assert not (tmp_path / "store").exists()


def test_the_secret_never_appears_in_the_transcript(tmp_path, fake_server):
    fake_server["in_rooms"] = ["!hall:example.org"]
    script = Script(
        answers=["example.org", "@guildbot:example.org", "1", "!hall:example.org", ""],
        secrets=["hunter2"])
    run_wizard(tmp_path, script)
    assert "hunter2" not in script.transcript
    assert "syt_tok" not in script.transcript


def test_main_accepts_an_env_file_argument(tmp_path, monkeypatch):
    seen = {}

    class Stub:
        def __init__(self, ui, path, **kw):
            seen["path"] = path

        async def run(self):
            return 0

    monkeypatch.setattr(wiz, "Wizard", Stub)
    assert wiz.main(["--env-file", str(tmp_path / "x.env")]) == 0
    assert seen["path"] == tmp_path / "x.env"
