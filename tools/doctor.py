#!/usr/bin/env python3
"""Diagnose a bot that connects but doesn't answer.

    python3 -m tools.doctor

Uses the same environment variables as the adapter. Read-only: it logs in,
inspects the room, watches events for a few seconds and reports. It never
sends a message and never writes to the state dir.

Every check here exists because it is a way for the bot to look online and
still be deaf.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

from nio import (
    AsyncClient,
    JoinedRoomsError,
    LoginResponse,
    MegolmEvent,
    RoomMessageText,
    RoomResolveAliasResponse,
    SyncResponse,
    UnknownEncryptedEvent,
    WhoamiResponse,
)

OK = "  \033[32m✓\033[0m"
BAD = "  \033[31m✗\033[0m"
WARN = "  \033[33m!\033[0m"

WATCH_SECONDS = 20


class Doctor:
    def __init__(self) -> None:
        self.homeserver = os.environ.get("MATRIX_HOMESERVER", "")
        self.user = os.environ.get("MATRIX_USER", "")
        self.room_id = os.environ.get("MATRIX_ROOM_ID", "")
        self.password = os.environ.get("MATRIX_PASSWORD")
        self.token = os.environ.get("MATRIX_TOKEN")
        self.seen: list[str] = []
        self.text_events = 0
        self.encrypted_events = 0
        self.problems: list[str] = []

    def fail(self, msg: str) -> None:
        self.problems.append(msg)

    async def on_text(self, room, event) -> None:
        self.text_events += 1
        ts = event.server_timestamp
        skew = int(time.time() * 1000) - ts
        self.seen.append(
            f"    text  {room.room_id}  {event.sender}: {event.body[:50]!r} "
            f"(clock skew {skew/1000:+.1f}s)"
        )

    async def on_encrypted(self, room, event) -> None:
        self.encrypted_events += 1
        self.seen.append(f"    ENCRYPTED  {room.room_id}  from {event.sender}")

    async def run(self) -> int:
        print("\n=== guildhall doctor ===\n")

        # --- config -------------------------------------------------------
        print("Environment")
        for name, val in (
            ("MATRIX_HOMESERVER", self.homeserver),
            ("MATRIX_USER", self.user),
            ("MATRIX_ROOM_ID", self.room_id),
        ):
            if val:
                print(f"{OK} {name}={val}")
            else:
                print(f"{BAD} {name} is not set")
                self.fail(f"{name} is not set")
        if not (self.password or self.token):
            print(f"{BAD} neither MATRIX_PASSWORD nor MATRIX_TOKEN is set")
            self.fail("no credentials")
        else:
            which = "MATRIX_TOKEN" if self.token else "MATRIX_PASSWORD"
            print(f"{OK} credentials via {which}")
        if self.problems:
            return self.report()

        client = AsyncClient(self.homeserver, self.user)

        # --- login --------------------------------------------------------
        print("\nLogin")
        try:
            if self.token:
                client.access_token = self.token
                client.user_id = self.user
                who = await client.whoami()
                if not isinstance(who, WhoamiResponse):
                    print(f"{BAD} token rejected: {who}")
                    self.fail("token rejected")
                    await client.close()
                    return self.report()
                client.user_id = who.user_id
            else:
                resp = await client.login(self.password)
                if not isinstance(resp, LoginResponse):
                    print(f"{BAD} login failed: {resp}")
                    self.fail(f"login failed: {resp}")
                    await client.close()
                    return self.report()
            print(f"{OK} logged in as {client.user_id}")
        except Exception as exc:
            print(f"{BAD} could not reach {self.homeserver}: {exc}")
            self.fail(f"cannot reach homeserver: {exc}")
            await client.close()
            return self.report()

        # --- room id ------------------------------------------------------
        print("\nRoom")
        target = self.room_id
        if target.startswith("#"):
            resolved = await client.room_resolve_alias(target)
            if not isinstance(resolved, RoomResolveAliasResponse):
                print(f"{BAD} alias {target} does not resolve: {resolved.message}")
                self.fail("alias does not resolve")
                await client.close()
                return self.report()
            print(f"{OK} {target} resolves to {resolved.room_id}")
            target = resolved.room_id
        elif not target.startswith("!"):
            print(f"{BAD} {target!r} is neither a #alias nor a !room_id")
            self.fail("malformed MATRIX_ROOM_ID")

        joined = await client.joined_rooms()
        if isinstance(joined, JoinedRoomsError):
            print(f"{BAD} could not list joined rooms: {joined.message}")
            self.fail("cannot list joined rooms")
        else:
            if target in joined.rooms:
                print(f"{OK} bot is joined to {target}")
            else:
                print(f"{BAD} bot is NOT in {target}")
                print(f"       it is in {len(joined.rooms)} room(s):")
                for r in joined.rooms[:15]:
                    print(f"         {r}")
                self.fail(
                    "the bot is not in the room you configured — invite it, and "
                    "make sure MATRIX_ROOM_ID is a room people talk in, not a space"
                )

        # --- sync + inspect ----------------------------------------------
        print("\nSyncing once to inspect room state…")
        sync = await client.sync(timeout=8000, full_state=True)
        if not isinstance(sync, SyncResponse):
            print(f"{BAD} sync failed: {sync}")
            self.fail("sync failed")
            await client.close()
            return self.report()
        print(f"{OK} sync ok")

        room = client.rooms.get(target)
        if room is None:
            print(f"{WARN} {target} not present in synced rooms")
        else:
            print(f"{OK} room name: {room.display_name!r}")
            print(f"{OK} members: {len(room.users)}")

            if room.encrypted:
                print(f"{BAD} THIS ROOM IS ENCRYPTED")
                self.fail(
                    "the room is end-to-end encrypted. The bot reads plaintext "
                    "m.room.message events only, so it receives nothing and looks "
                    "deaf. Encryption cannot be turned off on an existing room — "
                    "make a NEW room with encryption disabled at creation."
                )
            else:
                print(f"{OK} room is unencrypted (as the design requires)")

            # Can the bot actually speak?
            try:
                required = room.power_levels.defaults.events_default
                mine = room.power_levels.users.get(client.user_id,
                        room.power_levels.defaults.users_default)
                if mine >= required:
                    print(f"{OK} bot can send messages (power {mine} >= {required})")
                else:
                    print(f"{BAD} bot CANNOT send messages (power {mine} < {required})")
                    self.fail("bot lacks permission to send messages in this room")
            except Exception:
                print(f"{WARN} could not read power levels")

        # --- watch --------------------------------------------------------
        client.add_event_callback(self.on_text, RoomMessageText)
        client.add_event_callback(self.on_encrypted, MegolmEvent)
        client.add_event_callback(self.on_encrypted, UnknownEncryptedEvent)

        print(f"\nWatching for {WATCH_SECONDS}s — type something in the room now.")
        try:
            await asyncio.wait_for(
                client.sync_forever(timeout=5000, since=sync.next_batch),
                timeout=WATCH_SECONDS,
            )
        except asyncio.TimeoutError:
            pass

        print(f"\n{len(self.seen)} event(s) seen:")
        for line in self.seen[-20:]:
            print(line)
        if not self.seen:
            print("    (nothing)")
            self.fail(
                "no messages arrived at all. Either nobody typed, the bot is in a "
                "different room, or the homeserver isn't delivering to this user."
            )
        if self.encrypted_events:
            print(f"\n{BAD} {self.encrypted_events} message(s) arrived ENCRYPTED — "
                  "the bot cannot read these.")
            self.fail("encrypted messages are arriving and cannot be decrypted")

        await client.close()
        return self.report()

    def report(self) -> int:
        print("\n=== verdict ===")
        if not self.problems:
            print(f"{OK} no problems found. If the bot still ignores you, check "
                  "that you're typing in the room above, and remember a bare "
                  "number is only a command mid-fight — try `!board`.")
            return 0
        for p in self.problems:
            print(f"{BAD} {p}")
        return 1


def main() -> int:
    return asyncio.run(Doctor().run())


if __name__ == "__main__":
    sys.exit(main())
