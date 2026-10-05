#!/usr/bin/env python3
"""Interactive first-run setup: get the bot connected to Matrix.

    python3 -m tools.setup            # writes .env in the current directory
    ./setup.sh                        # same thing, inside Docker

Walks through everything that otherwise has to be done by hand and gotten right
the first time: finding the homeserver, logging the bot in, getting it into a
room it can actually read, and writing a correct `.env`. Each step checks its
own work, so a mistake is reported where it happens rather than as a bot that
connects and never answers.

It can also *create* the guild hall for you — an unencrypted private room with
you invited — which removes the two easiest mistakes (an encrypted room, or a
space instead of a room) by construction.

Never sends a message into an existing room. Rerunnable: an existing `.env` is
backed up to `.env.bak` and its other settings are kept.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from nio import (
    AsyncClient,
    DiscoveryInfoResponse,
    JoinedRoomsResponse,
    JoinError,
    LoginInfoResponse,
    LoginResponse,
    RoomCreateResponse,
    RoomPreset,
    RoomResolveAliasResponse,
    SyncResponse,
    WhoamiResponse,
)

TTY = sys.stdout.isatty()
OK = "  \033[32m✓\033[0m" if TTY else "  ok"
BAD = "  \033[31m✗\033[0m" if TTY else "  !!"
INFO = "  \033[36m·\033[0m" if TTY else "  -"

MAX_TRIES = 4
DEFAULT_ROOM_NAME = "Guild Hall"
HALL_TOPIC = "Guildhall — a text RPG. Type !help to begin."

# Values that can sit in an env file bare. Anything else gets single quotes, and
# a single quote itself cannot be represented at all (see env_value).
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_@.:/!+=,-][A-Za-z0-9_@.:/!#+=,-]*$")
_MXID = re.compile(r"^@[^:\s@]+:\S+$")


class SetupError(Exception):
    """Something the user needs to fix; the message says what."""


class Aborted(Exception):
    """The user gave up (Ctrl-C, Ctrl-D, or ran out of tries)."""


# ---------------------------------------------------------------------------
# pure helpers — no network, no prompts, all unit-tested
# ---------------------------------------------------------------------------

def normalise_homeserver(raw: str) -> tuple[str, str] | None:
    """`matrix.example.org` or a full URL -> (base url, server name guess).

    Returns None when there is nothing usable. Pasting a whole MXID is
    forgiven: `@bot:example.org` becomes `example.org`.
    """
    text = raw.strip()
    if text.startswith("@") and ":" in text:
        text = text.split(":", 1)[1]
    if not text:
        return None
    if "://" not in text:
        text = f"https://{text}"
    parsed = urlparse(text)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    if "@" in parsed.netloc:  # user@host is never a homeserver address
        return None
    return text.rstrip("/"), parsed.netloc


def normalise_mxid(raw: str, server_name: str = "") -> str | None:
    """`@bot:example.org`, or just `bot` when the server name is known."""
    text = raw.strip()
    if not text:
        return None
    if not text.startswith("@"):
        if ":" in text or not server_name:
            return None
        text = f"@{text}:{server_name}"
    return text if _MXID.match(text) else None


def parse_mxid_list(raw: str, server_name: str = "") -> tuple[list[str], list[str]]:
    """Comma-separated MXIDs -> (valid, rejected)."""
    good: list[str] = []
    bad: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        mxid = normalise_mxid(part, server_name)
        (good if mxid else bad).append(mxid or part)
    return good, bad


def env_value(value: str) -> str:
    """Render a value for an env file that both `docker compose` and a shell
    `export` will read back unchanged.

    Compose interpolates `$` and treats ` #` as a comment, so anything with
    spaces or punctuation is single-quoted. A single quote cannot be escaped
    portably, so refuse it rather than write a file that silently corrupts the
    password.
    """
    if "\n" in value or "\r" in value or "'" in value:
        raise ValueError("value contains a newline or single quote")
    return value if _SAFE_VALUE.match(value) else f"'{value}'"


def read_env(text: str) -> dict[str, str]:
    """Active `KEY=value` lines of an env file, with quotes removed."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if not match:
            continue
        value = match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        out[match.group(1)] = value
    return out


def render_env(template: str, values: dict[str, str | None]) -> str:
    """Set each key in `template`, keeping every comment and other setting.

    A key is replaced in place whether it is active or commented out (so the
    template's explanatory comments stay next to it). A value of None turns an
    active key back into a bare commented placeholder, which is how switching
    from a password to a token removes the password from disk.
    """
    lines = template.splitlines()
    for key, value in values.items():
        pattern = re.compile(rf"^\s*(#\s*)?{re.escape(key)}=")
        index = next((i for i, ln in enumerate(lines) if pattern.match(ln)), None)
        if value is None:
            if index is not None and not lines[index].lstrip().startswith("#"):
                lines[index] = f"# {key}="
            continue
        rendered = f"{key}={env_value(value)}"
        if index is None:
            lines.append(rendered)
        else:
            lines[index] = rendered
    return "\n".join(lines).rstrip("\n") + "\n"


def explain_login_error(status_code: str | None, message: str) -> str:
    """Turn a Matrix error into something a person can act on."""
    hints = {
        "M_FORBIDDEN": "Wrong username or password — or this account doesn't "
                       "exist yet. The bot needs its own account; create it "
                       "first (the README shows how).",
        "M_USER_DEACTIVATED": "That account has been deactivated.",
        "M_LIMIT_EXCEEDED": "The server is rate-limiting logins. Wait a minute "
                            "and try again.",
        "M_UNKNOWN_TOKEN": "The server doesn't recognise that access token. "
                           "It may have been revoked.",
    }
    hint = hints.get(status_code or "")
    return f"{hint}\n       (server said: {message})" if hint else message


# ---------------------------------------------------------------------------
# prompting — a seam so tests can script the conversation
# ---------------------------------------------------------------------------

class Console:
    def say(self, text: str = "") -> None:
        print(text)

    def ask(self, prompt: str, default: str = "") -> str:
        suffix = f" [{default}]" if default else ""
        try:
            answer = input(f"{prompt}{suffix}: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise Aborted from None
        return answer or default

    def secret(self, prompt: str) -> str:
        try:
            return getpass.getpass(f"{prompt}: ").strip()
        except (EOFError, KeyboardInterrupt):
            raise Aborted from None

    def confirm(self, prompt: str, default: bool = True) -> bool:
        hint = "Y/n" if default else "y/N"
        answer = self.ask(f"{prompt} ({hint})").lower()
        if not answer:
            return default
        return answer.startswith("y")


@dataclass
class Result:
    homeserver: str
    server_name: str
    user: str
    token: str | None
    password: str | None
    room_id: str
    admins: list[str]


# ---------------------------------------------------------------------------
# the wizard
# ---------------------------------------------------------------------------

class Wizard:
    def __init__(self, ui: Console, env_path: Path,
                 client_factory=AsyncClient, template_path: Path | None = None):
        self.ui = ui
        self.env_path = env_path
        self.make_client = client_factory
        self.template_path = template_path
        self.existing = (read_env(env_path.read_text())
                         if env_path.exists() else {})
        self.client: AsyncClient | None = None
        self.server_name = ""
        self.homeserver = ""
        self.creator_mxid = ""

    # -- steps ----------------------------------------------------------------

    async def choose_homeserver(self) -> list[str]:
        """Find and verify the homeserver. Returns its login flows."""
        ui = self.ui
        ui.say("\n\033[1mStep 1 of 4 — the homeserver\033[0m" if TTY
               else "\nStep 1 of 4 - the homeserver")
        ui.say("The address you log in to Element with, e.g. matrix.example.org")
        default = self.existing.get("MATRIX_HOMESERVER", "")
        for _ in range(MAX_TRIES):
            raw = ui.ask("Homeserver", default)
            parsed = normalise_homeserver(raw)
            if parsed is None:
                ui.say(f"{BAD} That doesn't look like an address.")
                continue
            url, name = parsed
            # Only a bare domain is worth a .well-known lookup: someone who
            # typed a full URL has already told us where the server is.
            bare = "://" not in raw
            client = self.make_client(url)
            try:
                if bare:
                    found = await client.discovery_info()
                    if isinstance(found, DiscoveryInfoResponse):
                        real = found.homeserver_url.rstrip("/")
                        if real != url:
                            ui.say(f"{OK} {name} delegates to {real}")
                            await client.close()
                            url = real
                            client = self.make_client(url)
                info = await client.login_info()
            except Exception as exc:  # noqa: BLE001 - any network failure reads the same
                await client.close()
                ui.say(f"{BAD} Couldn't reach {url}: {exc}")
                ui.say("       Check the spelling, and that this machine can "
                       "reach it.")
                continue
            if not isinstance(info, LoginInfoResponse):
                await client.close()
                ui.say(f"{BAD} {url} didn't answer like a Matrix server: {info}")
                continue
            ui.say(f"{OK} Matrix server at {url}")
            self.client, self.homeserver, self.server_name = client, url, name
            return list(info.flows)
        raise Aborted

    async def log_in(self, flows: list[str]) -> tuple[str, str | None, str | None]:
        """Returns (user_id, token, password) — exactly one of the last two."""
        ui = self.ui
        client = self.client
        assert client is not None
        ui.say("\n\033[1mStep 2 of 4 — the bot's account\033[0m" if TTY
               else "\nStep 2 of 4 - the bot's account")
        ui.say("The bot needs an account of its own, not yours. Not made one "
               "yet? See\n'Making the bot an account' in the README, then come "
               "back.")

        use_password = "m.login.password" in flows
        if not use_password:
            ui.say(f"{INFO} This server doesn't offer password login (it "
                   "probably uses single sign-on).")
            ui.say("       Log in as the bot in Element, then copy its access "
                   "token\n       (Settings → Help & About → Access Token).")

        default_user = self.existing.get("MATRIX_USER", "")
        for _ in range(MAX_TRIES):
            user = normalise_mxid(
                ui.ask("Bot account (e.g. @guildbot:" + self.server_name + ")",
                       default_user), self.server_name)
            if user is None:
                ui.say(f"{BAD} Use the full form, like @guildbot:{self.server_name}")
                continue

            if use_password:
                password = ui.secret(f"Password for {user} (hidden)")
                resp = await client.login(password, device_name="guildhall-setup")
                if isinstance(resp, LoginResponse):
                    ui.say(f"{OK} Logged in as {resp.user_id}")
                    return await self._choose_credential(resp, password)
                ui.say(f"{BAD} {explain_login_error(resp.status_code, resp.message)}")
                client.user_id = ""
                continue

            token = ui.secret("Access token (hidden)")
            client.access_token = token
            client.user_id = user
            who = await client.whoami()
            if isinstance(who, WhoamiResponse):
                client.user_id = who.user_id
                ui.say(f"{OK} Token works — you are {who.user_id}")
                return who.user_id, token, None
            ui.say(f"{BAD} {explain_login_error(who.status_code, who.message)}")
            client.access_token = ""
        raise Aborted

    async def _choose_credential(self, resp: LoginResponse,
                                 password: str) -> tuple[str, str | None, str | None]:
        ui = self.ui
        ui.say("\nStore an access token instead of the password?")
        ui.say("  A token keeps the password off disk, and means the bot reuses "
               "one login\n  instead of registering a new device every restart.")
        if not ui.confirm("Use a token (recommended)", True):
            if "'" in password:
                raise SetupError(
                    "That password contains a single quote, which can't be "
                    "stored safely in an env file. Use a token instead."
                )
            assert self.client is not None
            await self.client.logout()  # don't leave an unused device behind
            return resp.user_id, None, password
        return resp.user_id, resp.access_token, None

    async def resolve_room(self, bot_user: str) -> tuple[str, str]:
        """Returns (room_id, creator_or_empty)."""
        ui = self.ui
        ui.say("\n\033[1mStep 3 of 4 — the guild hall\033[0m" if TTY
               else "\nStep 3 of 4 - the guild hall")
        ui.say("The room where everyone plays. It must be a normal room with "
               "encryption OFF\n(not a space) — the bot can't read encrypted "
               "messages.")

        existing_room = self.existing.get("MATRIX_ROOM_ID", "")
        default_choice = "1" if existing_room else "2"
        while True:
            ui.say("\n  1. Use a room I already have")
            ui.say("  2. Create a new one for me (recommended — it gets the "
                   "settings right)")
            choice = ui.ask("Choose", default_choice)
            if choice == "2":
                created = await self._create_room(bot_user)
                if created:
                    return created
            elif choice == "1":
                found = await self._use_existing(bot_user, existing_room)
                if found:
                    return found, ""
            else:
                ui.say(f"{BAD} Type 1 or 2.")

    async def _create_room(self, bot_user: str) -> tuple[str, str] | None:
        ui = self.ui
        client = self.client
        assert client is not None
        me = None
        for _ in range(MAX_TRIES):
            me = normalise_mxid(
                ui.ask("Your own Matrix ID, so I can invite you "
                       f"(e.g. @you:{self.server_name})"), self.server_name)
            if me:
                break
            ui.say(f"{BAD} Use the full form, like @you:{self.server_name}")
        if not me:
            return None
        name = ui.ask("Room name", DEFAULT_ROOM_NAME)
        resp = await client.room_create(
            name=name,
            topic=HALL_TOPIC,
            invite=[me],
            preset=RoomPreset.private_chat,   # no encryption unless asked for
            power_level_override={"users": {bot_user: 100, me: 100}},
        )
        if not isinstance(resp, RoomCreateResponse):
            ui.say(f"{BAD} Couldn't create the room: {resp.message}")
            return None
        ui.say(f"{OK} Created “{name}” ({resp.room_id}) and invited {me}")
        ui.say("       Accept the invite in Element to see it. It's private and "
               "unencrypted;\n       invite your friends the same way.")
        return resp.room_id, me

    async def _use_existing(self, bot_user: str, default: str) -> str | None:
        ui = self.ui
        client = self.client
        assert client is not None
        target = ui.ask("Room address (#alias:server) or room ID (!id:server)",
                        default)
        if target.startswith("#"):
            resolved = await client.room_resolve_alias(target)
            if not isinstance(resolved, RoomResolveAliasResponse):
                ui.say(f"{BAD} {target} doesn't resolve: {resolved.message}")
                ui.say("       Check the alias, or use the room's internal ID "
                       "(Room settings → Advanced).")
                return None
            ui.say(f"{OK} {target} is {resolved.room_id}")
            target = resolved.room_id
        elif not target.startswith("!"):
            ui.say(f"{BAD} That should start with # (an alias) or ! (a room ID).")
            return None

        if not await self._get_into(target, bot_user):
            return None
        problem = await self._inspect(target)
        if problem:
            ui.say(f"{BAD} {problem}")
            return None
        return target

    async def _get_into(self, room_id: str, bot_user: str) -> bool:
        ui = self.ui
        client = self.client
        assert client is not None
        for attempt in range(MAX_TRIES):
            joined = await client.joined_rooms()
            if isinstance(joined, JoinedRoomsResponse) and room_id in joined.rooms:
                ui.say(f"{OK} {bot_user} is in the room")
                return True
            result = await client.join(room_id)
            if not isinstance(result, JoinError):
                ui.say(f"{OK} {bot_user} joined the room")
                return True
            if attempt == MAX_TRIES - 1:
                break
            ui.say(f"{BAD} {bot_user} isn't in that room yet ({result.message}).")
            ui.say(f"       In Element, invite {bot_user} to the room.")
            if not ui.confirm("Invited? Try again", True):
                return False
        ui.say(f"{BAD} Couldn't get into the room.")
        return False

    async def _inspect(self, room_id: str) -> str | None:
        """A reason this room won't work, or None if it will."""
        client = self.client
        assert client is not None
        sync = await client.sync(timeout=8000, full_state=True)
        if not isinstance(sync, SyncResponse):
            return f"Couldn't read the room: {sync}"
        room = client.rooms.get(room_id)
        if room is None:
            return None  # can't tell; the doctor will say more
        if getattr(room, "encrypted", False):
            return ("That room is encrypted, so the bot would receive nothing "
                    "and look deaf.\n       Encryption can't be turned off "
                    "afterwards — choose option 2 to make a fresh room.")
        try:
            kind = await client.room_get_state_event(room_id, "m.room.create")
            content = getattr(kind, "content", None) or {}
            if content.get("type") == "m.space":
                return ("That's a space, not a room — nobody talks in a space. "
                        "Pick (or create) a room.")
        except Exception:  # noqa: BLE001 - best effort; encryption was the big one
            pass
        try:
            levels = room.power_levels
            mine = levels.users.get(client.user_id, levels.defaults.users_default)
            if mine < levels.defaults.events_default:
                return ("The bot isn't allowed to send messages in that room. "
                        "Give it permission (or a moderator role) and retry.")
        except Exception:  # noqa: BLE001
            pass
        self.ui.say(f"{OK} Room is unencrypted and the bot can speak in it")
        return None

    def choose_admins(self, suggested: str) -> list[str]:
        ui = self.ui
        ui.say("\n\033[1mStep 4 of 4 — who runs it\033[0m" if TTY
               else "\nStep 4 of 4 - who runs it")
        ui.say("Operators can use !admin (reset characters, grant items, "
               "announce, back up).")
        default = self.existing.get("MATRIX_ADMINS", suggested)
        for _ in range(MAX_TRIES):
            good, bad = parse_mxid_list(
                ui.ask("Operator Matrix IDs, comma-separated (blank for none)",
                       default), self.server_name)
            if not bad:
                return good
            ui.say(f"{BAD} Not valid Matrix IDs: {', '.join(bad)}")
        raise Aborted

    # -- writing ----------------------------------------------------------------

    def _template(self) -> str:
        if self.env_path.exists():
            return self.env_path.read_text()
        for candidate in (self.template_path,
                          Path(__file__).resolve().parent.parent / ".env.example"):
            if candidate is not None and candidate.exists():
                return candidate.read_text()
        return ""

    def write(self, result: Result) -> None:
        values: dict[str, str | None] = {
            "MATRIX_HOMESERVER": result.homeserver,
            "MATRIX_USER": result.user,
            "MATRIX_TOKEN": result.token,
            "MATRIX_PASSWORD": result.password,
            "MATRIX_ROOM_ID": result.room_id,
            "MATRIX_ADMINS": ",".join(result.admins) if result.admins else None,
        }
        try:
            text = render_env(self._template(), values)
        except ValueError as exc:
            raise SetupError(f"Can't store one of the values safely: {exc}") from exc

        if self.env_path.exists():
            backup = self.env_path.with_name(self.env_path.name + ".bak")
            shutil.copy2(self.env_path, backup)
            os.chmod(backup, 0o600)
            self.ui.say(f"{INFO} Existing settings saved to {backup.name}")

        # Owner-only before any secret lands in it.
        fd = os.open(self.env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
        os.chmod(self.env_path, 0o600)
        (self.env_path.parent / "store").mkdir(exist_ok=True)

    # -- driver -----------------------------------------------------------------

    async def run(self) -> int:
        ui = self.ui
        ui.say("\n=== Guildhall setup ===")
        ui.say("About five minutes. Nothing is written until the end, and Ctrl-C "
               "is always safe.")
        try:
            flows = await self.choose_homeserver()
            user, token, password = await self.log_in(flows)
            room_id, creator = await self.resolve_room(user)
            admins = self.choose_admins(creator)
            result = Result(self.homeserver, self.server_name, user, token,
                            password, room_id, admins)
            self.write(result)
        except Aborted:
            ui.say("\nStopped — nothing was written.")
            return 1
        except SetupError as exc:
            ui.say(f"\n{BAD} {exc}")
            return 1
        finally:
            if self.client is not None:
                await self.client.close()

        ui.say(f"\n{OK} Wrote {self.env_path} (owner-only)")
        ui.say("\nNext:")
        ui.say("  docker compose up -d --build     # or: python3 -m adapters.matrix")
        ui.say("  docker compose logs -f           # wait for “online … listening”")
        ui.say("Then say !create in the hall. If the bot stays quiet: "
               "python3 -m tools.doctor")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Connect Guildhall to Matrix.")
    parser.add_argument("--env-file", default=".env",
                        help="where to write settings (default: ./.env)")
    args = parser.parse_args(argv)
    wizard = Wizard(Console(), Path(args.env_file))
    return asyncio.run(wizard.run())


if __name__ == "__main__":
    sys.exit(main())
