"""The guild's bounty of the day — one shared goal for the whole hall.

Everybody's contracts count towards a single target, and when it is met the
guild is paid a lump of renown that belongs to everyone. Like all guild
progress it gives nobody a stronger character; it just makes the hall feel like
a place where people are working on the same thing.

Rolled lazily and deterministically from the UTC date, so a restart mid-day
lands on the same bounty rather than a fresh one, and nothing needs a timer.
The goal is fixed when the day rolls, scaled to how many people are on the
books at that moment.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass

from .guild import Guild


@dataclass(frozen=True)
class Bounty:
    key: str
    text: str      # "{goal}" is filled in
    kind: str      # what counts: 'contract', 'tier2', 'tier3' or 'adventure'
    goal: int      # for a one-person guild
    reward: int    # guild renown at guild level 1; scales with level


BOUNTIES: tuple[Bounty, ...] = (
    Bounty("any", "Clear {goal} contracts", "contract", 5, 30),
    Bounty("tier2", "Clear {goal} contracts of tier 2 or higher", "tier2", 3, 40),
    Bounty("tier3", "Clear {goal} tier 3 contracts", "tier3", 2, 50),
    Bounty("adventure", "Finish {goal} adventure", "adventure", 1, 60),
)
BOUNTIES_BY_KEY = {b.key: b for b in BOUNTIES}

# Each extra person adds a quarter of the base goal, capped so a crowded hall
# is never asked for the impossible.
GOAL_PER_EXTRA_MEMBER = 0.25
MAX_GOAL_MULTIPLIER = 3.0


def utc_day(now: float | None = None) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))


def seconds_until_reset(now: float | None = None) -> int:
    stamp = time.time() if now is None else now
    return int(86400 - stamp % 86400)


def goal_for(bounty: Bounty, members: int) -> int:
    scale = 1 + GOAL_PER_EXTRA_MEMBER * max(0, members - 1)
    return max(1, round(bounty.goal * min(scale, MAX_GOAL_MULTIPLIER)))


def reward_for(bounty: Bounty, guild: Guild) -> int:
    return bounty.reward * guild.level


def _roll(guild: Guild, day: str, rng: random.Random) -> None:
    bounty = rng.choice(BOUNTIES)
    guild.bounty_day = day
    guild.bounty_key = bounty.key
    guild.bounty_goal = goal_for(bounty, guild.members)
    guild.bounty_progress = 0
    guild.bounty_claimed = 0


def refresh(guild: Guild, now: float | None = None) -> Bounty:
    """Make sure the guild holds today's bounty, and return it."""
    day = utc_day(now)
    if guild.bounty_day != day or guild.bounty_key not in BOUNTIES_BY_KEY:
        _roll(guild, day, random.Random(day))
    return BOUNTIES_BY_KEY[guild.bounty_key]


def reroll(guild: Guild, now: float | None = None) -> Bounty:
    """Throw today's bounty away and draw another. Admin tool."""
    previous = guild.bounty_key
    rng = random.Random()
    for _ in range(10):
        _roll(guild, utc_day(now), rng)
        if guild.bounty_key != previous:
            break
    return BOUNTIES_BY_KEY[guild.bounty_key]


def _counts(bounty: Bounty, tier: int, adventure: bool) -> bool:
    if bounty.kind == "contract":
        return True
    if bounty.kind == "tier2":
        return tier >= 2
    if bounty.kind == "tier3":
        return tier >= 3
    if bounty.kind == "adventure":
        return adventure
    return False


def record(guild: Guild, tier: int, adventure: bool = False,
           now: float | None = None) -> list[str]:
    """One contract was completed. Returns lines to show, if there's news."""
    bounty = refresh(guild, now)
    if guild.bounty_claimed or not _counts(bounty, tier, adventure):
        return []

    guild.bounty_progress += 1
    if guild.bounty_progress < guild.bounty_goal:
        return [f"📋 _Bounty: {guild.bounty_progress}/{guild.bounty_goal} — "
                f"{bounty.text.format(goal=guild.bounty_goal).lower()}._"]

    guild.bounty_claimed = 1
    reward = reward_for(bounty, guild)
    before = guild.level
    guild.renown += reward
    lines = [
        f"📋 **BOUNTY CLAIMED — {bounty.text.format(goal=guild.bounty_goal)}!**",
        f"🏰 _The guild is **{reward}** renown richer, and everybody gets to "
        "feel smug about it._",
    ]
    if guild.level > before:
        lines += ["", f"🎊 **THE GUILD IS NOW {guild.tier.name.upper()}!** 🎊",
                  f"_{guild.tier.blurb}_"]
    return lines


def render(guild: Guild | None, now: float | None = None) -> list[str]:
    if guild is None:
        return ["The guild's books are not open right now."]
    bounty = refresh(guild, now)
    goal = guild.bounty_goal
    hours, rest = divmod(seconds_until_reset(now), 3600)
    resets = f"{hours}h {rest // 60:02d}m"

    lines = [f"📋 **Bounty of the day** — {bounty.text.format(goal=goal)}"]
    if guild.bounty_claimed:
        lines.append("✅ **Claimed.** _The clerk has stamped it, twice, with "
                     "feeling._")
        lines.append(f"_A new one is posted in {resets}._")
    else:
        done = min(guild.bounty_progress, goal)
        filled = round(8 * done / goal)
        lines.append("🟩" * filled + "⬛" * (8 - filled) + f"  **{done}/{goal}**")
        lines.append(f"_Everyone's contracts count. Pays **{reward_for(bounty, guild)}** "
                     f"guild renown · resets in {resets} (UTC midnight)._")
    return lines
