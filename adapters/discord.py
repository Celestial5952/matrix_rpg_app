#!/usr/bin/env python3
"""Discord adapter — bridges one Discord channel (and DMs) to core.game.handle().

Same shape as adapters/matrix.py, and for the same reason: everything
platform-specific lives here so core/ stays testable with no network.

A Discord user is the player `@<user id>:discord`. The `@...:...` shape is
deliberate — core/ and the persistence layer already treat a player key as an
MXID, and this satisfies every one of those assumptions without touching them.

Discord renders the **bold**, _italic_ and `code` that core/ emits natively, so
unlike Matrix there is no HTML conversion; the work here is splitting long
replies under Discord's 2000-character cap, and making sure player-supplied
text (a character called "@everyone") can never ping anyone.

Environment:
    DISCORD_TOKEN         the bot token (Developer Portal → Bot → Reset Token)
    DISCORD_CHANNEL_ID    the guild hall: right-click the channel → Copy Channel ID
    DISCORD_ADMINS        comma-separated user IDs allowed to use !admin
    DISCORD_ALLOW_DMS     "false" to ignore direct messages (default: true)
    DISCORD_STATE_DIR     where players.json + guild.json live (default: store/discord)
    DISCORD_TRAVEL_SECONDS  seconds spent marching between encounters (default 300)
    DISCORD_TRAVEL_TICK     how often arrivals are checked, minimum 5 (default 20)
    DISCORD_BACKUP_HOURS / DISCORD_BACKUP_KEEP   as for Matrix (24 / 14)

The bot needs the **Message Content** privileged intent switched on in the
Developer Portal, or it receives empty messages and looks deaf. Run

    python3 -m adapters.discord --check

to verify the token, the intent and the channel, and to print the invite link.
This is a separate process from the Matrix bot with its own state: the two
worlds do not share characters.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from pathlib import Path

import discord

from core.backup import snapshot
from core.duel import Duels
from core.game import arrive, handle, set_travel_pace
from core.guild import Guild
from core.party import Parties
from core.persist import load_all, load_guild, save_all, save_guild
from core.state import Player

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("guildhall.discord")
# discord.py warns on every start that voice is unsupported. This bot is text
# only, so that line is noise that makes real warnings harder to spot.
logging.getLogger("discord.client").setLevel(logging.ERROR)

# Discord's hard cap is 2000 characters; stay clear of it.
MAX_CHUNK = 1900

SETUP_HINT = ("\nNot sure what's wrong? `python3 -m adapters.discord --check` "
              "tells you.")

INTENT_HELP = (
    "Discord refused the Message Content intent.\n"
    "Developer Portal → your application → Bot → Privileged Gateway Intents → "
    "turn on MESSAGE CONTENT INTENT → Save. Without it the bot would receive "
    "empty messages and never answer."
)


def player_key(user_id: int | str) -> str:
    """The game's identity for a Discord user. See the module docstring."""
    return f"@{user_id}:discord"


def user_id_of(key: str) -> int | None:
    """Inverse of player_key, or None for a key that isn't one of ours."""
    match = re.fullmatch(r"@(\d+):discord", key)
    return int(match.group(1)) if match else None


def split_message(lines: list[str], limit: int = MAX_CHUNK) -> list[str]:
    """Pack lines into messages no longer than `limit`, never mid-line unless a
    single line is itself too long."""
    chunks: list[str] = []
    current = ""
    for line in lines:
        while len(line) > limit:
            if current:
                chunks.append(current)
                current = ""
            cut = line.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            chunks.append(line[:cut])
            line = line[cut:].lstrip(" ")
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


def _env(name: str, *, required: bool = True) -> str | None:
    val = os.environ.get(name)
    if required and not val:
        raise SystemExit(f"missing required environment variable: {name}{SETUP_HINT}")
    return val


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


class Bot:
    def __init__(self) -> None:
        self.token = _env("DISCORD_TOKEN")
        raw_channel = _env("DISCORD_CHANNEL_ID")
        if not raw_channel.strip().isdigit():
            raise SystemExit(
                f"DISCORD_CHANNEL_ID must be the channel's numeric ID, not "
                f"{raw_channel!r}. Enable Developer Mode (User Settings → "
                f"Advanced), then right-click the channel → Copy Channel ID."
                f"{SETUP_HINT}")
        self.channel_id = int(raw_channel)
        self.allow_dms = _flag("DISCORD_ALLOW_DMS", True)

        self.admins = {a.strip() for a in
                       os.environ.get("DISCORD_ADMINS", "").split(",") if a.strip()}
        for admin in self.admins:
            if not admin.isdigit():
                log.warning("DISCORD_ADMINS entry %r is not a numeric user ID "
                            "and will never match", admin)

        state_dir = Path(os.environ.get("DISCORD_STATE_DIR", "store/discord"))
        state_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir = state_dir
        self.players_path = state_dir / "players.json"
        self.guild_path = state_dir / "guild.json"

        self.backup_hours = max(0.0, float(os.environ.get("DISCORD_BACKUP_HOURS", "24")))
        self.backup_keep = max(1, int(os.environ.get("DISCORD_BACKUP_KEEP", "14")))

        self.players: dict[str, Player] = load_all(self.players_path)
        self.guild: Guild = load_guild(self.guild_path)
        # In memory only, for the same reasons as on Matrix: party and duel
        # state is shared between players and can't be restored per-player.
        self.parties = Parties()
        self.duels = Duels()

        set_travel_pace(int(os.environ.get("DISCORD_TRAVEL_SECONDS", "300")))
        self.travel_tick = max(5, int(os.environ.get("DISCORD_TRAVEL_TICK", "20")))

        # Where each player last spoke, so an arrival finds them wherever they
        # are. The channel object itself, because a DM channel often isn't in
        # the client's cache to look up by ID.
        self.player_channels: dict[str, object] = {}

        # Background tasks start from on_ready, not before: discord.Client
        # refuses wait_until_ready() until login has initialised it, and on_ready
        # also fires again after every reconnect, hence the guard.
        self._tasks: list[asyncio.Task] = []
        self.checking = False   # --check connects but must not start the game
        self._ready: asyncio.Event | None = None

        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    # -- sending ------------------------------------------------------------------

    async def _hall(self):
        channel = self.client.get_channel(self.channel_id)
        if channel is None:
            try:
                channel = await self.client.fetch_channel(self.channel_id)
            except discord.DiscordException:
                log.exception("cannot reach the hall channel %s", self.channel_id)
        return channel

    async def _send(self, channel, lines: list[str], *,
                    mention: int | None = None) -> bool:
        """Post `lines`, split to fit. Returns False if Discord refused.

        Nobody gets pinged by accident: player-supplied names reach these lines,
        so mentions are switched off entirely — except the single, deliberate
        one for an arrival.
        """
        if channel is None:
            return False
        # AllowedMentions(users=[...]) alone leaves @everyone and roles ON, so
        # every other kind of mention is switched off explicitly.
        allowed = (discord.AllowedMentions(everyone=False, roles=False,
                                           replied_user=False,
                                           users=[discord.Object(id=mention)])
                   if mention else discord.AllowedMentions.none())
        chunks = split_message(lines)
        if mention:
            chunks[0] = f"<@{mention}> {chunks[0]}"
        try:
            for chunk in chunks:
                await channel.send(chunk, allowed_mentions=allowed)
            return True
        except discord.DiscordException:
            # A failed send must never take the bot down; the log says why.
            log.exception("could not send to %s", getattr(channel, "id", channel))
            return False

    # -- state --------------------------------------------------------------------

    def _player(self, key: str, display_name: str) -> Player:
        player = self.players.get(key)
        if player is None:
            player = Player(mxid=key, display_name=display_name)
            self.players[key] = player
        elif display_name and player.display_name != display_name:
            player.display_name = display_name   # cosmetic; identity is the ID
        return player

    def _save(self) -> bool:
        self.guild.members = sum(1 for p in self.players.values()
                                 if p.character is not None)
        try:
            save_all(self.players_path, self.players)
            save_guild(self.guild_path, self.guild)
            return True
        except OSError:
            log.exception("could not persist players to %s", self.players_path)
            return False

    # -- events -------------------------------------------------------------------

    def _ready_event(self) -> asyncio.Event:
        if self._ready is None:
            self._ready = asyncio.Event()
        return self._ready

    async def on_ready(self) -> None:
        log.info("guildhall bot online as %s — listening in channel %s%s",
                 self.client.user, self.channel_id,
                 " and DMs" if self.allow_dms else "")
        self._ready_event().set()
        if not self._tasks and not self.checking:
            self._tasks.append(asyncio.create_task(self._travel_ticker()))
            if self.backup_hours > 0:
                self._tasks.append(asyncio.create_task(self._backup_loop()))

    async def on_message(self, message) -> None:
        if message.author.bot:
            return   # ourselves, and every other bot — never trade commands

        private = message.guild is None
        if private:
            if not self.allow_dms:
                return
        elif message.channel.id != self.channel_id:
            return   # other `!` bots live in other channels; stay out of them

        text = message.content or ""
        if not text.startswith("!"):
            return   # cheap exit before any state is touched

        key = player_key(message.author.id)
        player = self._player(key, message.author.display_name)
        self.player_channels[key] = message.channel

        is_admin = str(message.author.id) in self.admins
        if is_admin:
            handled = await self._admin_command(text)
            if handled is not None:
                await self._send(message.channel, handled)
                return

        bot_id = getattr(self.client.user, "id", None)
        mentions = [player_key(u.id) for u in getattr(message, "mentions", [])
                    if u.id != bot_id]

        try:
            reply = handle(player, text, self.players, self.guild, self.parties,
                           self.duels, mentions, private, is_admin)
        except Exception:
            log.exception("handle() raised on %r from %s", text, key)
            await self._send(message.channel, [
                "Something went wrong resolving that. The error is in the bot log."])
            return

        if reply is None:
            return
        self._save()
        await self._send(message.channel, reply)

    async def _admin_command(self, body: str) -> list[str] | None:
        """Operator commands that need Discord. None = let core/ handle it."""
        parts = body.strip().split(None, 2)
        if len(parts) < 2 or parts[0].lower() != "!admin":
            return None
        sub = parts[1].lower()

        if sub == "announce":
            text = parts[2].strip() if len(parts) > 2 else ""
            if not text:
                return ["`!admin announce <text>` — posts a notice in the hall."]
            log.info("admin announcement: %s", text)
            sent = await self._send(await self._hall(),
                                    ["📣 **Notice from the guild**", text])
            return (["📣 Posted to the hall."] if sent
                    else ["Couldn't post to the hall — see the bot log."])

        if sub == "backup":
            self._save()
            path = await asyncio.to_thread(snapshot, self.state_dir, self.backup_keep)
            if path is None:
                return ["Nothing new to back up — the newest snapshot already "
                        "matches the live files."]
            return [f"💾 Snapshot written: `{path.name}`."]
        return None

    # -- background tasks ---------------------------------------------------------

    async def _travel_ticker(self) -> None:
        """Deliver arrivals — the only time the bot speaks unprompted."""
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
                    channel = self.player_channels.get(player.mxid) or await self._hall()
                    await self._send(channel, lines, mention=user_id_of(player.mxid))
                if arrived:
                    save_all(self.players_path, self.players)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("travel ticker stumbled; continuing")

    async def _backup_loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(snapshot, self.state_dir, self.backup_keep)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("backup stumbled; continuing")
            await asyncio.sleep(self.backup_hours * 3600)

    # -- running ------------------------------------------------------------------

    async def run(self) -> None:
        try:
            await self.client.start(self.token)
        except discord.LoginFailure:
            raise SystemExit("Discord rejected the bot token. Developer Portal → "
                             "Bot → Reset Token, and update DISCORD_TOKEN."
                             f"{SETUP_HINT}") from None
        except discord.PrivilegedIntentsRequired:
            raise SystemExit(INTENT_HELP) from None
        finally:
            for task in self._tasks:
                task.cancel()
            await self.client.close()

    async def check(self) -> int:
        """Verify the whole setup without playing: the onboarding doctor."""
        problems: list[str] = []
        self.checking = True
        starter = asyncio.create_task(self.client.start(self.token))
        ready = asyncio.create_task(self._ready_event().wait())
        done, _ = await asyncio.wait({starter, ready},
                                     return_when=asyncio.FIRST_COMPLETED)
        if starter in done:
            exc = starter.exception()
            ready.cancel()
            if isinstance(exc, discord.LoginFailure):
                print("  ✗ Discord rejected the bot token. Developer Portal → "
                      "Bot → Reset Token.")
            elif isinstance(exc, discord.PrivilegedIntentsRequired):
                print(f"  ✗ {INTENT_HELP}")
            else:
                print(f"  ✗ Could not connect: {exc}")
            await self.client.close()
            return 1

        print(f"  ✓ Logged in as {self.client.user}")
        print("  ✓ Message Content intent is enabled")

        app_id = self.client.application_id or getattr(self.client.user, "id", None)
        invite = discord.utils.oauth_url(
            app_id,
            permissions=discord.Permissions(view_channel=True, send_messages=True,
                                            read_message_history=True),
            scopes=("bot",))
        guilds = list(self.client.guilds)
        if not guilds:
            print("  ✗ The bot isn't in any server yet. Open this link and add it:")
            print(f"      {invite}")
            problems.append("not in a server")
        else:
            print(f"  ✓ In {len(guilds)} server(s): "
                  f"{', '.join(g.name for g in guilds)}")

        channel = await self._hall()
        if channel is None:
            print(f"  ✗ Can't see channel {self.channel_id}. Check "
                  "DISCORD_CHANNEL_ID, and that the bot can view that channel.")
            problems.append("hall channel not visible")
        else:
            me = getattr(getattr(channel, "guild", None), "me", None)
            perms = channel.permissions_for(me) if me is not None else None
            if perms is not None and not (perms.send_messages and perms.view_channel):
                print(f"  ✗ The bot can see #{channel} but isn't allowed to "
                      "send messages there.")
                problems.append("cannot send in hall")
            else:
                print(f"  ✓ Hall channel: #{channel}")
                if perms is not None and not perms.read_message_history:
                    print("  ! Missing 'Read Message History' — replies still "
                          "work, but grant it to be safe.")

        print("\nInvite link (if you need to add the bot elsewhere):")
        print(f"  {invite}")
        await self.client.close()
        await asyncio.gather(starter, ready, return_exceptions=True)
        if problems:
            print("\nFix the above and run --check again.")
            return 1
        print("\nAll good. Start the bot, then say !create in the hall.")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    bot = Bot()
    if "--check" in args:
        print("\n=== guildhall discord check ===\n")
        return asyncio.run(bot.check())
    asyncio.run(bot.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
