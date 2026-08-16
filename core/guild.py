"""The guild itself — the one thing death cannot take.

Every character is temporary by design. That makes permadeath sharp, but it
also means a run of bad luck erases everything a player has to show for an
evening. Guild renown is the counterweight: it is contributed by everyone,
shared by everyone, and never lost.

Deliberately, none of its perks make a *character* stronger. A guild that
handed out +HP would quietly undo permadeath by making later characters
better than earlier ones. What it buys instead is preparation and choice —
more coin to kit out with, a wider board, more scrolls turning up — so the
guild's progress shows up before a fight rather than during one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GuildTier:
    renown: int          # total guild renown needed
    name: str
    blurb: str
    starting_gold: int   # what a newly rolled character begins with
    board_size: int      # contracts posted at once
    scroll_bonus: float  # multiplier on scroll drop chance


GUILD_TIERS: tuple[GuildTier, ...] = (
    GuildTier(0, "Chartered",
              "A room above a tavern, a board, and a clerk who is far too "
              "cheerful about the mortality rate.",
              starting_gold=0, board_size=3, scroll_bonus=1.0),
    GuildTier(120, "Established",
              "The landlord has stopped asking about the noise. Somebody has "
              "donated chairs.",
              starting_gold=15, board_size=3, scroll_bonus=1.25),
    GuildTier(400, "Respected",
              "Work comes to you now. Bramblewick has expanded into the back "
              "room and become insufferable about it.",
              starting_gold=40, board_size=4, scroll_bonus=1.5),
    GuildTier(1000, "Renowned",
              "There is a sign. There is a waiting list. There is, alarmingly, "
              "a ledger of people who owe the guild favours.",
              starting_gold=80, board_size=4, scroll_bonus=1.9),
    GuildTier(2200, "Storied",
              "People name children after your dead. The clerk has taken on "
              "an assistant, purely to help with the filing.",
              starting_gold=140, board_size=5, scroll_bonus=2.4),
)


@dataclass
class Guild:
    """Shared, server-wide, and never reset by a death."""

    renown: int = 0
    contracts_completed: int = 0
    adventures_completed: int = 0
    members: int = 0

    @property
    def tier(self) -> GuildTier:
        current = GUILD_TIERS[0]
        for tier in GUILD_TIERS:
            if self.renown >= tier.renown:
                current = tier
        return current

    @property
    def level(self) -> int:
        return GUILD_TIERS.index(self.tier) + 1

    @property
    def next_tier(self) -> GuildTier | None:
        index = GUILD_TIERS.index(self.tier)
        return GUILD_TIERS[index + 1] if index + 1 < len(GUILD_TIERS) else None

    @property
    def renown_to_next(self) -> int | None:
        nxt = self.next_tier
        return None if nxt is None else nxt.renown - self.renown


# A character's own renown counts once for them and once for the guild. The
# guild's share is smaller so that a solo player still feels their own
# progress dominate, and a full guild moves it noticeably faster.
GUILD_SHARE = 0.5
ADVENTURE_MULTIPLIER = 2.0


def contribution(character_renown: int, *, adventure: bool = False) -> int:
    """What a completed contract adds to the guild."""
    share = character_renown * GUILD_SHARE
    if adventure:
        share *= ADVENTURE_MULTIPLIER
    return max(1, round(share))
