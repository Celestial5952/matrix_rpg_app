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
    InviteMemberEvent,
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

from core.game import arrive, handle, set_travel_pace
from core.guild import Guild
from core.duel import Duels
from core.party import Parties
from core.persist import load_all, load_guild, save_all, save_guild
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
        self.guild_path = state_dir / "guild.json"
        self.sync_token_path = state_dir / "sync_token"

        # "post" -- every turn is its own message, nothing removed, everything
        # in the main timeline. Chosen because the edited frame drifts upward
        # on mobile as your own commands stack below it, so you end up
        # scrolling back to read the result of what you just typed.
        # "edit" -- one message per fight, rewritten in place via m.replace,
        # with outcomes threaded off it. Quieter in a shared room.
        self.combat_style = os.environ.get("MATRIX_COMBAT_STYLE", "post").lower()
        if self.combat_style not in ("post", "edit"):
            log.warning("unknown MATRIX_COMBAT_STYLE %r, using 'post'",
                        self.combat_style)
            self.combat_style = "post"

        self.players: dict[str, Player] = load_all(self.players_path)
        # Shared and never reset by a death — kept in its own file so a
        # corrupt player save cannot cost the whole server's progress.
        self.guild: Guild = load_guild(self.guild_path)
        # Parties are in-memory only. A party run is shared state, and half of
        # it cannot be restored per-member without silently turning one party
        # fight into several identical solo ones -- so a restart ends party
        # contracts, and core.persist drops any run carrying a party_key.
        self.parties: Parties = Parties()
        # Duels are in-memory for the same reason as parties: the state is
        # shared between two players and cannot be restored per-player.
        self.duels: Duels = Duels()

        # Real seconds spent travelling between encounters. 0 keeps play
        # instant, which is what every test and the offline REPL want.
        set_travel_pace(int(os.environ.get("MATRIX_TRAVEL_SECONDS", "0")))
        self.travel_tick = max(5, int(os.environ.get("MATRIX_TRAVEL_TICK", "20")))

        # Rooms other than the hall that we answer in: one-to-one chats, for
        # playing alone. Where each player last spoke, so an arrival is
        # delivered wherever they actually are.
        self.dm_rooms: set[str] = set()
        self.player_rooms: dict[str, str] = {}
        self.warned_encrypted: set[str] = set()
        # mxid -> {"root": event_id, "frame": event_id}. A fight is one message
        # in the room that we keep editing; outcomes go in a thread off it.
        # Deliberately not persisted: after a restart the event ids may no
        # longer be editable, so the next turn simply opens a fresh frame.
        self.fights: dict[str, dict[str, str]] = {}
        self.start_ms = int(time.time() * 1000)
        self._resumed = False

        config = AsyncClientConfig(request_timeout=30, max_timeout_retry_wait_time=30)
        self.client = AsyncClient(self.homeserver, self.user, config=config)
        self.client.add_event_callback(self.on_message, RoomMessageText)
        self.client.add_event_callback(self.on_invite, InviteMemberEvent)
        self.client.add_response_callback(self.on_sync, SyncResponse)
        self.client.add_response_callback(self.on_sync_error, SyncError)

    def _content(self, lines: list[str], *, thread_root: str | None = None,
                 edits: str | None = None, mention: str | None = None) -> dict:
        """Build an m.room.message, optionally threaded and/or an edit."""
        body = "\n".join(lines)
        html = "<br>".join(_to_html(line) for line in lines)
        if mention:
            # A real pill, so the arrival actually reaches their phone. An
            # async contract nobody is notified about is just a slow one.
            name = mention.split(":")[0].lstrip("@")
            body = f"{name}: {body}"
            html = (f'<a href="https://matrix.to/#/{mention}">{name}</a>: {html}')
        content: dict = {
            "msgtype": "m.notice",
            "body": body,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
        }
        if mention:
            content["m.mentions"] = {"user_ids": [mention]}
        if edits:
            # The top-level body is the fallback older clients show; m.new_content
            # is what everything modern renders.
            content["body"] = f"* {body}"
            content["formatted_body"] = f"* {html}"
            content["m.new_content"] = {
                "msgtype": "m.notice",
                "body": body,
                "format": "org.matrix.custom.html",
                "formatted_body": html,
            }
            # An event carries exactly one rel_type, so an edit to a threaded
            # message relates to the target, not the thread. Clients place it
            # in the thread because the *target* is there.
            content["m.relates_to"] = {"rel_type": "m.replace", "event_id": edits}
        elif thread_root:
            content["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": thread_root,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": thread_root},
            }
        return content

    async def _display_name(self, room: MatrixRoom, mxid: str) -> str:
        """Member state is often unloaded right after a join, which is how a
        raw MXID ends up stored as somebody's name. Ask the server instead."""
        name = room.user_name(mxid)
        if name and name != mxid:
            return name
        try:
            resp = await self.client.get_displayname(mxid)
            fetched = getattr(resp, "displayname", None)
            if fetched:
                return fetched
        except Exception:  # noqa: BLE001 - cosmetic only, never worth failing on
            log.debug("could not resolve a display name for %s", mxid, exc_info=True)
        return mxid.split(":")[0].lstrip("@")

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

    @staticmethod
    def _mentions(event: RoomMessageText) -> list[str]:
        """User ids the sender actually pilled, per MSC3952 `m.mentions`.

        An Element mention puts the *display name* in the message body, which
        can carry spaces and decoration and need not resemble the MXID at all,
        so text matching alone mis-parses it. This is the exact answer when the
        client sends it, and harmlessly empty when it does not.
        """
        try:
            content = getattr(event, "source", {}).get("content", {})
            ids = content.get("m.mentions", {}).get("user_ids", [])
        except AttributeError:
            return []
        return [str(i) for i in ids] if isinstance(ids, list) else []

    def _is_dm(self, room: MatrixRoom) -> bool:
        """Two people in it and it is not the hall — that is a DM."""
        if room.room_id == self.room_id:
            return False
        try:
            return room.member_count <= 2
        except Exception:  # noqa: BLE001 - membership may not be loaded yet
            return False

    async def on_invite(self, room: MatrixRoom, event: InviteMemberEvent) -> None:
        """Accept invites to the configured room and to one-to-one chats.

        Group rooms are declined by silence: answering in an arbitrary room
        somebody dragged us into is how a bot becomes a nuisance.
        """
        if event.state_key != self.client.user_id:
            return
        if room.room_id != self.room_id and not self._is_dm(room):
            log.info("ignoring invite to group room %s", room.room_id)
            return

        result = await self.client.join(room.room_id)
        if hasattr(result, "message"):
            log.error("could not join %s: %s", room.room_id, result.message)
            return
        log.info("joined %s", room.room_id)
        if room.room_id != self.room_id:
            self.dm_rooms.add(room.room_id)
            await self._greet(room)

    async def _greet(self, room: MatrixRoom) -> None:
        if getattr(room, "encrypted", False):
            await self._warn_encrypted(room)
            return
        await self._send([
            "🏰 **The guild keeps a side door.**",
            "_You can play here on your own — same character, same guild, "
            "same everything. It is the hall that is busy, not you._",
            "",
            "`!help` for the list · `!create` if you haven't yet.",
            "_Parties and duels still happen in the hall, with everyone else._",
        ], room_id=room.room_id)

    async def _warn_encrypted(self, room: MatrixRoom) -> None:
        """Say so out loud. A bot that silently ignores you looks broken."""
        if room.room_id in self.warned_encrypted:
            return
        self.warned_encrypted.add(room.room_id)
        log.warning("cannot read encrypted room %s", room.room_id)
        await self._send([
            "🔒 **I can't read this room — it's encrypted.**",
            "_I have no encryption keys, so every message you send here is "
            "noise to me. This one is going out unencrypted, which is why you "
            "can see it at all._",
            "",
            "**To play one-to-one:** make a new room with encryption turned "
            "**off** and invite me to that. Element only offers the choice "
            "when the room is created — it cannot be undone afterwards.",
            "",
            "_Or just use the guild hall._",
        ], room_id=room.room_id)

    async def on_message(self, room: MatrixRoom, event: RoomMessageText) -> None:
        if room.room_id != self.room_id and room.room_id not in self.dm_rooms:
            if not self._is_dm(room):
                return
            self.dm_rooms.add(room.room_id)
        if event.sender == self.client.user_id:
            return  # never react to our own output — avoids command-string loops

        # A cold start (no persisted sync token) can hand us a page of recent
        # history on the first sync. A resumed start only ever gets events
        # newer than the last token, so this guard only applies cold.
        if not self._resumed and event.server_timestamp < self.start_ms:
            return

        if getattr(room, "encrypted", False):
            await self._warn_encrypted(room)
            return

        name = await self._display_name(room, event.sender)
        player = self._player(event.sender, name)
        self.player_rooms[event.sender] = room.room_id
        private = room.room_id != self.room_id
        was_fighting = player.in_combat

        # One malformed command must not kill the sync loop. Without this, an
        # unhandled exception in game logic takes the bot down for everyone.
        try:
            reply = handle(player, event.body, self.players, self.guild,
                           self.parties, self.duels, self._mentions(event),
                           private)
        except Exception:
            log.exception("handle() raised on %r from %s", event.body, event.sender)
            await self._send(["Something went wrong resolving that. "
                              "The error is in the bot log."],
                             room_id=room.room_id)
            return

        if reply is None:
            return

        self.guild.members = sum(1 for p in self.players.values()
                                 if p.character is not None)
        try:
            save_all(self.players_path, self.players)
            save_guild(self.guild_path, self.guild)
        except OSError:
            # Losing a save is survivable; refusing to reply is not.
            log.exception("could not persist players to %s", self.players_path)

        await self._deliver(player, reply, was_fighting, room.room_id)

    async def _deliver(self, player: Player, lines: list[str],
                       was_fighting: bool, room_id: str | None = None) -> None:
        """Decide whether this reply opens, updates, or closes a fight frame."""
        room_id = room_id or self.room_id
        if self.combat_style == "post" or room_id != self.room_id:
            # Frames and threads are a shared-room concern. In a one-to-one
            # chat there is nobody else's output to keep out of the way of.
            await self._send(lines, room_id=room_id)
            return

        mxid = player.mxid
        fighting = player.in_combat
        fight = self.fights.get(mxid)

        if fighting and not was_fighting:
            # A contract just started: this message becomes the live frame and
            # the root of the thread its outcomes will hang off.
            event_id = await self._send(lines)
            if event_id:
                self.fights[mxid] = {"root": event_id, "frame": event_id}
            return

        if fighting and fight:
            # Mid-fight: rewrite the frame in place rather than adding a
            # message per turn. A 20-turn fight was 20 messages.
            edited = await self._send(lines, edits=fight["frame"])
            if edited is None:
                # The edit failed (event gone, or a restart lost the id). Fall
                # back to a fresh frame instead of dropping the player's turn.
                event_id = await self._send(lines)
                if event_id:
                    self.fights[mxid] = {"root": event_id, "frame": event_id}
            return

        if fighting:
            event_id = await self._send(lines)
            if event_id:
                self.fights[mxid] = {"root": event_id, "frame": event_id}
            return

        if was_fighting and fight:
            # The fight ended. Outcomes are the part worth keeping, so they go
            # in the thread as their own message rather than overwriting.
            await self._send(lines, thread_root=fight["root"])
            self.fights.pop(mxid, None)
            return

        await self._send(lines)

    async def _send(self, lines: list[str], *, thread_root: str | None = None,
                    edits: str | None = None,
                    mention: str | None = None,
                    room_id: str | None = None) -> str | None:
        """Send one message, retrying only when the server asks us to.

        Returns the event id, or None if it could not be sent.
        """
        content = self._content(lines, thread_root=thread_root, edits=edits,
                                mention=mention)
        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            resp = await self.client.room_send(
                room_id=room_id or self.room_id,
                message_type="m.room.message",
                content=content,
            )
            if not isinstance(resp, RoomSendError):
                return getattr(resp, "event_id", None) or "sent"

            if resp.status_code != "M_LIMIT_EXCEEDED":
                log.error("send failed (%s): %s", resp.status_code, resp.message)
                return None

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
        return None

    async def _travel_ticker(self) -> None:
        """Deliver arrivals. The only place the bot speaks unprompted.

        One misbehaving player must not stop everyone else's contracts, and an
        exception here would otherwise kill the task silently and leave every
        traveller stranded on the road forever.
        """
        while True:
            await asyncio.sleep(self.travel_tick)
            try:
                arrived = False
                for player in list(self.players.values()):
                    try:
                        lines = arrive(player)
                    except Exception:
                        log.exception("arrival failed for %s", player.mxid)
                        continue
                    if not lines:
                        continue
                    arrived = True
                    await self._send(
                        lines, mention=player.mxid,
                        room_id=self.player_rooms.get(player.mxid))
                if arrived:
                    save_all(self.players_path, self.players)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("travel ticker stumbled; continuing")

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
        for room_id, room in self.client.rooms.items():
            if room_id != self.room_id and self._is_dm(room):
                self.dm_rooms.add(room_id)
        if self.dm_rooms:
            log.info("also answering in %d one-to-one room(s)", len(self.dm_rooms))

        ticker = asyncio.create_task(self._travel_ticker())
        try:
            await self.client.sync_forever(
                timeout=30000, since=since, full_state=not self._resumed
            )
        finally:
            ticker.cancel()
            await self.client.close()


def main() -> int:
    bot = Bot()
    asyncio.run(bot.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
