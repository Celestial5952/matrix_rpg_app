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

## How a fight looks in the room

`MATRIX_COMBAT_STYLE` picks between two displays.

**`post` (default)** — every turn is its own message, nothing removed,
everything in the main timeline. The full blow-by-blow scrolls like a normal
conversation and the newest state is always at the bottom.

**`edit`** — one message per fight, rewritten in place via `m.replace`, with
outcomes threaded off it. Far quieter in a shared room: a 20-turn contract is
one message instead of twenty.

`edit` was the original default and lost a playtest. The frame sits *above* the
player's own commands, so every `!1` pushes it further up the screen — on a
phone you end up scrolling back to read the result of the thing you just typed.
Tidiness in the room is not worth that. It is kept as an option because in a
busy shared hall the tradeoff may flip.

In `edit` mode, a failed edit falls back to posting a fresh frame — losing an
edit must never cost a player their turn — and frame ids are deliberately not
persisted, so a restart simply opens a new frame.

## Nothing prefixed is ever answered with silence

Unprefixed text is ignored, always. But once someone types `!` they have
declared intent, so an unrecognised command gets a reply with a `difflib`
suggestion — `!bord` offers `!board`. Silence in response to a typo is
indistinguishable from the bot being down.

## Voice

Camp, warm, a bit theatrical. The guild clerk is delighted you might die,
Bramblewick runs the shop, contracts complete with confetti and levels arrive
shouting. Emoji are decoration only — the icon lookups live in `game.py`'s view
layer, never on the content dataclasses, so `content.py` stays about mechanics.

Two places stay deliberately un-cute: numbers, and death. Stats read plainly
because you make decisions with them, and while a death message is theatrical
it never softens what happened. Everything really is gone.

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

## The board

The board never posts a quest template directly. It **rolls a Contract**: the
template plus varied rewards (±15%), a length that can shift, an opening line
picked from several, and zero to two modifiers. Two runs of "Rats in the Cellar"
should not feel like the same errand twice.

Modifiers change the fight and what it pays — Fortified adds armour, Savage
raises monster power, Teeming raises monster HP, Swarming adds an encounter,
Urgent removes one and pays for the hurry, Thankless trades gold for renown.
Higher tiers roll them more often.

Measured at level 3 on a tier-2 contract, an unmodified run wins 96.6% and the
combat modifiers pull that to 90 / 85 / 84. That spread took tuning: Fortified
and Teeming originally cost under 3 points while paying *more* gold, which made
them free money.

**Story contracts** are gated on renown rather than rank and appear by chance
(`STORY_CHANCE`), displacing an ordinary posting rather than adding a slot — so
the board stays a fixed size and a story job costs you real work. Add a `Quest`
with `story=True` and a `min_renown` and it enters the rotation; nothing else
needs changing. `The Sealed Name` at 30 renown is the first one.

## Levelling and the spellbook

Renown doubles as XP — one currency, not two. Levels 1–8 raise stats gently
(+3 HP each, +1 power every other, +1 focus every third) and, more importantly,
unlock more abilities. Guild rank is derived from level, so the board opens up
as you grow: tier 2 at level 3, tier 3 at level 5.

Each class has a pool of eight abilities. Four are yours at level 1; the rest
unlock as you go. `!spellbook` shows what you know, what's equipped, and what's
still locked. `!equip <name>` or `!equip <n>` swaps one in — **in the hall only**,
never mid-fight.

**Slots are typed**: basic, signature, defence, recovery. You choose *which*
basic attack, not *whether* you have one. Free-form slots would let a player
equip four signatures and soft-lock the moment they ran out of focus, and the
whole combat model assumes slot 1 is always usable.

The spellbook's numbering and `!equip <n>`'s resolver share one ordering
function on purpose. When they disagreed, `!equip 2` equipped a different
ability than the one printed next to "2".

The kit self-heals on load: an entry naming an ability that is unknown, locked,
or in the wrong slot silently falls back to the class default rather than
raising. A save written before a rename must not brick the character.

## Adventures

The long-form content. An **adventure** is an authored sequence of encounters
with story between them — ten chapters for the first one — opened by a **scroll**
that drops from ordinary board work. Nothing about it is rolled: the monsters,
their order and the prose are all written.

Scrolls never drop from tier 1, appear on about **4%** of tier 2 contracts and
**8%** of tier 3, and cannot be bought. Finding one is the board handing you a
reason to go shopping, because consumables are the real budget across ten
fights on one health bar.

Adventures ride the ordinary `Contract` machinery — combat, persistence and
rendering need no special cases. The only difference is that encounters come
from `chapters` in order rather than being drawn from a pool. `Chapter.rest`
grants HP before a fight so the curve has shape; ten encounters on one bar with
no pacing is arithmetic, not difficulty.

**Adding one is a data edit**, documented at the top of `adventures.py`: an
`Adventure` with chapters, an intro and an epilogue. The scroll item and its
drop entry are generated from the table.

`tools/adventure.py` plays one headlessly and reports where runs actually end,
which is the number worth reading:

```bash
python3 -m tools.adventure --runs 300 --level 5 --all-classes
```

The Sunless Ziggurat currently clears 35–88% at level 5 and 76–98% at level 7.
Note the harness fights with **default loadouts**, so it understates every class
whose armour-piercing option unlocks later — a real player will do better than
these numbers.

## Bailing out

`!portal` (also `!tp`, `!escape`, `!flee`) abandons a contract instantly. No
gold, no renown, no loot — you keep your character and nothing else.

There is no mechanical penalty and there should not be one: the point of an
escape hatch is that it is always available when a fight has gone wrong. The
cost is social. `Character.portals_used` counts every bail, the guild clerk
reads it, and she escalates from sympathetic to keeping a column with your name
on it. Nothing else in the game reads that counter, and it dies with the
character like everything else.

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

## Multiplayer

Everything is routed by MXID. `handle(player, text, roster, guild)` only ever
touches the sender's own state, and matrix-nio dispatches callbacks
sequentially inside one event loop — each is awaited to completion before the
next — so two players typing at once need no locking. All mutation happens
synchronously before any network await.

Per-player: character, board, run, inventory, graveyard. Two people on the same
contract fight separate instances of the same monster.

Shared: the room, `!who`, and the guild.

**Guild renown** is the counterweight to permadeath. Every completed contract
contributes a share of its renown to a server-wide pool that no death can
touch, and the tiers it unlocks — starting gold for new characters, a wider
board, more scrolls — belong to everyone.

None of those perks make a *character* stronger, deliberately. A guild handing
out +HP would quietly undo permadeath by making later characters better than
earlier ones. What it buys is preparation and choice, so the guild's progress
shows up before a fight rather than during one.

`!give <who> <item>` hands anything to another player, matched by character
name first because that is what people see in the room. It is the reason
adventures are delivered as items: someone finds a scroll they are too low to
read and can pass it to someone who isn't.

Not built: parties, co-op, PvP, trading for gold. `Run` is 1:1 with
`Character`, so shared-monster co-op needs a party that owns the encounter —
a deliberate rewrite, not an extension.

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
- Persistence covers the Player, Character, graveyard **and any in-progress
  run**, including RNG state — a resumed fight rolls what it would have rolled
- **Wizard is the weakest class** (36% on tier 3 at level 1 vs Fighter's 70%).
  Intended to be the high-risk pick, but that gap wants human playtesting, not
  more simulation
- One adventure exists. There is no chain between them, no state carried from
  one to the next, and no campaign arc
- Rogue is the strongest class in the adventure (88% at level 5 vs Wizard's
  36%) — Backstab piercing armour at 2 focus is a very strong level-1 kit
- **Content runs out above level 5.** A level-5 party clears tier 3 at 77–97%,
  so the Barrow Door stops being frightening well before level 8. That is a
  content gap, not a systems one — `content.py` needs tier 4+
- Balance is first-draft and unplaytested beyond a few fights
- The adapter's markdown → HTML rendering only understands `**bold**`,
  `_italic_`, `` `code` `` — enough for current game text, not a general
  renderer
- One room per process; no multi-room or per-space routing
- The live frame shows the last exchange, not a scrollback — earlier turns in a
  fight are overwritten
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
