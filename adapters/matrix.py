#!/usr/bin/env python3
"""Matrix adapter — the only file that knows Matrix exists.

Bridges one Matrix room to core.game.handle(). Everything Matrix-specific
(the sync loop, MXID identity, sync-token persistence, ignoring our own
messages, ignoring backfill on a cold start) lives here so core/ stays
testable without a homeserver in the loop.

Environment:
    MATRIX_HOMESERVER   e.g. https://matrix.example.org
    MATRIX_USER         full MXID, e.g. @guildbot:example.org
    MATRIX_PASSWORD     one of these two —
    MATRIX_TOKEN        an existing access token skips interactive login
    MATRIX_ROOM_ID      !abc123:example.org — the guild hall room
    MATRIX_STATE_DIR    where players.json + the sync token live (default: store)

Run from the repo root, as a module (so `core` resolves):

    python3 -m adapters.matrix
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from html import escape
from pathlib import Path

from nio import (
    AsyncClient,
    AsyncClientConfig,
    JoinError,
    LoginResponse,
    MatrixRoom,
    RoomMessageText,
    RoomResolveAliasResponse,
    RoomSendError,
    SyncError,
    SyncResponse,
    WhoamiResponse,
)

from core.game import handle
from core.persist import load_all, save_all
from core.state import Player

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("guildhall.matrix")

# Minimal markdown -> HTML for the subset core/ actually emits: **bold**,
# _italic_, `code`. Not a general markdown renderer on purpose — the day the
# game text needs more than this, reach for a real library instead.
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\w)_(.+?)_(?!\w)")
_CODE = re.compile(r"`(.+?)`")

# A 429 is the server telling us to slow down, so honour its retry_after_ms
# rather than dropping the reply. This is not the same as adding sleeps to
# throttle ourselves (see README) — the fix for sustained throttling is still
# raising the bot user's rate limit, and the log line below says so.
MAX_SEND_ATTEMPTS = 3
MAX_RETRY_WAIT = 10.0  # seconds; ignore an absurd retry_after and give up


def _to_html(line: str) -> str:
    html = escape(line)
    html = _CODE.sub(r"<code>\1</code>", html)
    html = _BOLD.sub(r"<strong>\1</strong>", html)
    html = _ITALIC.sub(r"<em>\1</em>", html)
    return html


def _env(name: str, *, required: bool = True) -> str | None:
    val = os.environ.get(name)
    if required and not val:
        raise SystemExit(f"missing required environment variable: {name}")
    return val


class Bot:
    def __init__(self) -> None:
        self.homeserver = _env("MATRIX_HOMESERVER")
        self.user = _env("MATRIX_USER")
        self.room_id = _env("MATRIX_ROOM_ID")
        self.password = os.environ.get("MATRIX_PASSWORD")
        self.token = os.environ.get("MATRIX_TOKEN")
        if not self.password and not self.token:
            raise SystemExit("set MATRIX_PASSWORD or MATRIX_TOKEN")

        state_dir = Path(os.environ.get("MATRIX_STATE_DIR", "store"))
        state_dir.mkdir(parents=True, exist_ok=True)
        self.players_path = state_dir / "players.json"
        self.sync_token_path = state_dir / "sync_token"

        self.players: dict[str, Player] = load_all(self.players_path)
        self.start_ms = int(time.time() * 1000)
        self._resumed = False

        config = AsyncClientConfig(request_timeout=30, max_timeout_retry_wait_time=30)
        self.client = AsyncClient(self.homeserver, self.user, config=config)
        self.client.add_event_callback(self.on_message, RoomMessageText)
        self.client.add_response_callback(self.on_sync, SyncResponse)
        self.client.add_response_callback(self.on_sync_error, SyncError)

    def _player(self, mxid: str, display_name: str) -> Player:
        player = self.players.get(mxid)
        if player is None:
            player = Player(mxid=mxid, display_name=display_name)
            self.players[mxid] = player
        elif display_name and player.display_name != display_name:
            # Identity is the MXID; the Matrix display name is cosmetic and is
            # not the character's name — renaming in Element must not rename
            # the character you rolled.
            player.display_name = display_name
        return player

    async def on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        if room.room_id != self.room_id:
            return
        if event.sender == self.client.user_id:
            return  # never react to our own output — avoids command-string loops

        # A cold start (no persisted sync token) can hand us a page of recent
        # history on the first sync. A resumed start only ever gets events
        # newer than the last token, so this guard only applies cold.
        if not self._resumed and event.server_timestamp < self.start_ms:
            return

        name = room.user_name(event.sender) or event.sender
        player = self._player(event.sender, name)

        # One malformed command must not kill the sync loop. Without this, an
        # unhandled exception in game logic takes the bot down for everyone.
        try:
            reply = handle(player, event.body)
        except Exception:
            log.exception("handle() raised on %r from %s", event.body, event.sender)
            await self._send(["Something went wrong resolving that. "
                              "The error is in the bot log."])
            return

        if reply is None:
            return

        try:
            save_all(self.players_path, self.players)
        except OSError:
            # Losing a save is survivable; refusing to reply is not.
            log.exception("could not persist players to %s", self.players_path)

        await self._send(reply)

    async def _send(self, lines: list[str]) -> None:
        """Send one message, retrying only when the server asks us to."""
        content = {
            "msgtype": "m.notice",
            "body": "\n".join(lines),
            "format": "org.matrix.custom.html",
            "formatted_body": "<br>".join(_to_html(line) for line in lines),
        }
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            resp = await self.client.room_send(
                room_id=self.room_id,
                message_type="m.room.message",
                content=content,
            )
            if not isinstance(resp, RoomSendError):
                return

            if resp.status_code != "M_LIMIT_EXCEEDED":
                log.error("send failed (%s): %s", resp.status_code, resp.message)
                return

            wait = min((resp.retry_after_ms or 1000) / 1000, MAX_RETRY_WAIT)
            log.warning(
                "rate limited (attempt %d/%d), waiting %.1fs — raise the rate "
                "limit for %s on the homeserver if this is frequent",
                attempt, MAX_SEND_ATTEMPTS, wait, self.client.user_id,
            )
            if attempt < MAX_SEND_ATTEMPTS:
                await asyncio.sleep(wait)

        log.error("giving up on a reply after %d rate-limited attempts",
                  MAX_SEND_ATTEMPTS)

    async def on_sync(self, response: SyncResponse) -> None:
        try:
            self.sync_token_path.write_text(response.next_batch)
        except OSError:
            # Non-fatal, but worth shouting about: without a persisted token the
            # next cold start falls back to the timestamp guard for backfill.
            log.exception("could not persist sync token to %s", self.sync_token_path)

    async def on_sync_error(self, response: SyncError) -> None:
        log.error("sync error (%s): %s", response.status_code, response.message)

    async def login(self) -> None:
        if self.token:
            self.client.access_token = self.token
            self.client.user_id = self.user
            whoami = await self.client.whoami()
            if not isinstance(whoami, WhoamiResponse):
                raise SystemExit(f"MATRIX_TOKEN rejected: {whoami}")
            self.client.user_id = whoami.user_id
        else:
            resp = await self.client.login(self.password)
            if not isinstance(resp, LoginResponse):
                raise SystemExit(f"login failed: {resp}")

        # Accept a #alias:server as well as a !roomid:server — the alias is what
        # a human can actually read off their client.
        if self.room_id.startswith("#"):
            resolved = await self.client.room_resolve_alias(self.room_id)
            if not isinstance(resolved, RoomResolveAliasResponse):
                raise SystemExit(
                    f"could not resolve alias {self.room_id}: {resolved.message}"
                )
            log.info("resolved %s -> %s", self.room_id, resolved.room_id)
            self.room_id = resolved.room_id

        # Idempotent — succeeds quietly if the bot is already in the room. A
        # failure here is fatal on purpose: an unjoined bot syncs happily and
        # receives nothing, which looks like "online but ignoring me".
        joined = await self.client.join(self.room_id)
        if isinstance(joined, JoinError):
            raise SystemExit(
                f"could not join {self.room_id}: {joined.message}\n"
                "Invite the bot to the room first, and check MATRIX_ROOM_ID is "
                "the internal ID (!abc:server), not the #alias:server."
            )

    async def run(self) -> None:
        await self.login()
        since = None
        if self.sync_token_path.exists():
            since = self.sync_token_path.read_text().strip() or None
        self._resumed = since is not None

        log.info(
            "guildhall bot online as %s in %s (%s)",
            self.client.user_id,
            self.room_id,
            "resumed" if self._resumed else "cold start",
        )
        try:
            await self.client.sync_forever(
                timeout=30000, since=since, full_state=not self._resumed
            )
        finally:
            await self.client.close()


def main() -> int:
    bot = Bot()
    asyncio.run(bot.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
