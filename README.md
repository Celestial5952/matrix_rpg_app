# Guildhall

A text-based roguelite RPG bot for Matrix. Guild hall → quest board → turn-based
text combat → die → keep your renown → go again.

Inspired by [Crownicles](https://github.com/Crownicles/Crownicles), but built for
Matrix rather than Discord.

## Status

Playable offline core, plus a working Matrix adapter.

```bash
python3 play.py
```

`core/` has no dependencies — standard library only. The Matrix adapter
(`adapters/matrix.py`) needs `matrix-nio`:

```bash
pip install -r requirements.txt

export MATRIX_HOMESERVER=https://matrix.example.org
export MATRIX_USER=@guildbot:example.org
export MATRIX_PASSWORD=...        # or MATRIX_TOKEN=... for an existing access token
export MATRIX_ROOM_ID='#guildhall:example.org'   # alias or !internal:id both work

python3 -m adapters.matrix
```

Invite the bot to the room first — an unjoined bot syncs happily and receives
nothing, so the adapter treats a failed join as fatal rather than letting it
look like "online but ignoring me". Point it at a **room**, not a space: a
space is a room, but nobody talks in it.

Player meta-state (renown, gold, rank, deaths) and the sync token persist to
`MATRIX_STATE_DIR` (default `store/`, already gitignored). An in-progress
fight is not persisted — restart mid-encounter and that one run is lost, same
as a `flee`.

## Tests

```bash
pip install pytest && python3 -m pytest
```

87 tests, no homeserver required — the adapter is exercised against a stub
client. They cover combat invariants (fireball ignores armour, guard reduces
damage, seeded runs replay identically), the routing rules that decide whether
the bot speaks at all, persistence round-trips and corrupt-file recovery, and
the adapter's own resilience paths.

## Design decisions already settled

**Plaintext replies, not reactions.** Matrix has no real button primitive.
Reactions (`m.reaction`) are the closest analogue and were considered, but plain
text replies work in every client including terminal ones, and are simpler to
reason about. Players type `1` or `strike`.

**The game room is unencrypted.** Deliberate. Encryption buys nothing for a quest
log and costs a lot: device verification, "unable to decrypt" after every bot
restart, and no ability to read room history when debugging a bad fight.

**Bare numbers are only commands when the sender has a live encounter.** The
guild hall is a real room people also chat in. If you aren't fighting, typing
`1` does nothing. `!`-prefixed commands always work.

**`core/` never imports a Matrix SDK.** This is the load-bearing rule. Game logic
takes an intent and returns a list of markdown lines. That means the whole game
is testable and playtestable without a homeserver in the loop — restarting a bot
and typing into Element to test a damage tweak is how these projects die.

## Layout

```
core/
  state.py     Player (meta, survives death) vs Run (wiped on death)
  content.py   monsters + quests — the file meant to grow to thousands of lines
  combat.py    turn resolution, damage, telegraphed monster moves
  game.py      command routing + guild hall state machine
  persist.py   JSON persistence for Player meta-state
play.py        offline playtest REPL
adapters/
  matrix.py    sync loop, event -> handle(), lines -> m.room.message — the
               only file that knows Matrix exists
tests/         pytest suite; no homeserver required
```

## Combat model

Monsters telegraph next turn's move (`The kobold raises the spear high in both
hands`), so Guard is a read rather than a coinflip. Fireball ignores armour, so
it has a role beyond bigger numbers. Focus regenerates 1/turn and Guard grants
+2, so the fireball cadence is a resource decision.

HP and focus carry across encounters within a contract, with no rest between —
attrition is the tension. Potions are limited per run.

## Roguelite split

- **Run state** — HP, focus, potions. Destroyed on death.
- **Meta state** — renown, gold, guild rank, completed contracts. Survives.

Guild rank gates which quest tiers appear on the board. Meta progression has to
be legible as *text* — "Rank 4 → Bronze contracts unlocked" reads fine in a chat
client; a 40-node skill constellation does not.

## Known gaps

- **Never run against a real homeserver yet.** The adapter is covered by tests
  against a stub client, which is not the same as proven.
- Persistence covers Player meta-state only — an in-progress run doesn't
  survive a restart
- Balance is first-draft and unplaytested beyond a few fights
- The adapter's markdown → HTML rendering only understands `**bold**`,
  `_italic_`, `` `code` `` — enough for current game text, not a general
  renderer
- One room per process; no multi-room or per-space routing

## Matrix gotchas the adapter handles

- **Backfill replay on restart** — the sync token persists to
  `MATRIX_STATE_DIR/sync_token`; a cold start (no token) also drops any event
  older than process startup, so a page of room history on first sync doesn't
  replay every `!accept` from the last three days.
- **Ignore your own events** — `adapters/matrix.py` skips anything sent by the
  bot's own MXID, so command-shaped bot output can't loop.
- **Key everything on MXID**, never display name — `Player` is keyed and
  persisted by MXID; display name is cosmetic only.
- **Rate limits** — Tuwunel will throttle a chatty bot. The adapter adds no
  sleeps of its own, but it does honour a 429's `retry_after_ms` for up to
  three attempts rather than silently dropping a reply, and logs a line
  telling you to raise the limit for the bot user when it happens.
- **A thrown exception mid-command** would otherwise kill the sync loop for
  everyone in the room, so `handle()` is wrapped: the player gets told
  something broke, the traceback goes to the log, the bot stays up.
