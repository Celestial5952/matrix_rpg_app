# Guildhall

A text-based roguelite RPG bot for Matrix. Guild hall → quest board → turn-based
text combat → die → keep your renown → go again.

Inspired by [Crownicles](https://github.com/Crownicles/Crownicles), but built for
Matrix rather than Discord.

## Status

Playable offline core, plus a Matrix adapter that has been run against a real
homeserver. Character creation and permadeath are in.

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

## Balance

```bash
python3 -m tools.balance --runs 2000
```

Plays thousands of headless runs with a few reference strategies and reports
win rate, length and HP left per contract. The strategies are deliberately
dumb — they're yardsticks, not how a human plays. If `always_strike` clears a
tier-3 contract, the tactical layer isn't doing any work.

Numbers move whenever `content.py` does, so read them fresh rather than
trusting a figure written down here.

## Design decisions already settled

**Plaintext replies, not reactions.** Matrix has no real button primitive.
Reactions (`m.reaction`) are the closest analogue and were considered, but plain
text replies work in every client including terminal ones, and are simpler to
reason about. Players type `1` or `strike`.

**The game room is unencrypted.** Deliberate. Encryption buys nothing for a quest
log and costs a lot: device verification, "unable to decrypt" after every bot
restart, and no ability to read room history when debugging a bad fight.

**Every command starts with `!`, with no exceptions.** The guild hall is a real
room people also chat in, and `!` is what separates input from conversation.
`board` does nothing; `!board` reads the board. `1` does nothing; `!1` is your
move.

The rule holds *during character creation too* — your name is `!Doc Weed`, not
`Doc Weed`. An earlier version let the register capture your next raw line,
which meant saying anything at the wrong moment silently named your character.
A mode where ordinary sentences become input is exactly what the prefix is for.

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
  game.py      command routing, character creation, guild hall state machine
  chargen.py   races (cosmetic) and classes (mechanical) + ability kits
  persist.py   JSON persistence for Player, Character and graveyard
play.py        offline playtest REPL
adapters/
  matrix.py    sync loop, event -> handle(), lines -> m.room.message — the
               only file that knows Matrix exists
tests/         pytest suite; no homeserver required
```

## Items, gold and loot

`!shop` sells consumables, `!buy <n>` purchases them, `!inventory` (or `!bag`)
lists what you carry, `!use <item>` spends one mid-fight.

Everything is **single use**. Permanent equipment would compete with class for
the same design space, and under permadeath it would mean a character's power
came mostly from how long it had been lucky. Spend it or lose it is the economy.

Two rules make items a decision rather than a free win:

- **Using an item costs your turn.** The monster's telegraphed move still lands.
  Free healing would flatten every fight.
- **An item that would do nothing is refused, not consumed.** Drinking a potion
  at full HP used to silently burn it for 0 healing.

Loot drops on contract completion, weighted by tier. Tier 1 can roll a dud on
purpose — loot should feel like a result, not a wage. Tier 3 always drops.

The bag dies with the character, like everything else.

## Combat model

Monsters telegraph next turn's move (`The kobold raises the spear high in both
hands`), so Guard is a read rather than a coinflip. Fireball ignores armour, so
it has a role beyond bigger numbers. Focus regenerates 1/turn and Guard grants
+2, so the fireball cadence is a resource decision.

HP and focus carry across encounters within a contract, with no rest between —
attrition is the tension. Potions are limited per run.

## Characters and permadeath

You cannot do anything until you `!create` a character: name, then race, then
class. The character is bound to your MXID, and you only ever have one alive.

**Death is permanent and total.** The character is destroyed along with its
renown, gold, and rank. All that survives is a tombstone in your `!graveyard`,
which is pure flavour and confers no mechanical advantage. This is a roguelike,
not a roguelite — the ownership model in `state.py` exists to make that true by
construction:

    Player     the Matrix account. Survives forever. Holds the graveyard.
    Character  what you create and what dies. Owns renown, gold, the board.
    Run        one contract attempt. Destroyed on death, flee, or restart.

Everything of value hangs off Character, so deleting it *is* permadeath — there
is no second place progress could hide.

**Race is cosmetic. Class is mechanical.** Race carries no stats at all, and a
test enforces it. When race had stat modifiers, HP races strictly dominated
focus races at every single class — Dwarf Wizard beat Elf Wizard — which made
the choice a trap rather than a character. Class decides HP, power, focus and
the four-ability kit.

Guild rank gates which quest tiers appear on the board. Progression has to be
legible as *text* — "Rank 3 → Barrow contracts unlocked" reads fine in a chat
client; a 40-node skill constellation does not.

## Known gaps

- **Never run against a real homeserver yet.** The adapter is covered by tests
  against a stub client, which is not the same as proven.
- Persistence covers the Player, Character and graveyard — an in-progress run
  doesn't survive a restart, which lands you where `flee` would have
- **Wizard is the weakest class** (34% on tier 3 vs Fighter's 72%). Intended to
  be the high-risk pick, but that gap wants human playtesting, not more
  simulation
- Balance is first-draft and unplaytested beyond a few fights
- The adapter's markdown → HTML rendering only understands `**bold**`,
  `_italic_`, `` `code` `` — enough for current game text, not a general
  renderer
- One room per process; no multi-room or per-space routing
- No equipment, only consumables — deliberate for now, see above

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
