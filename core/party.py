"""Parties: several characters sharing one monster.

The model, and why it is shaped this way:

- A **Party** owns the contract, the stage, the shared Encounter and the RNG.
- Each member keeps their **own** Run — their own HP, focus and ability uses.
  Only the monster is shared, because a shared health bar would make a party
  strictly a bigger solo character rather than a group.
- Members act in turn order. The monster acts once per **round**, after
  everyone has moved, and picks its target from whoever is still standing.

The shield the design calls for is structural rather than a check we remember
to write: commands are routed by MXID to the sender's own state, so a player
outside the party has no path to the party's Encounter at all. `Parties`
enforces one party per player, which is what makes that guarantee hold.

Downing is not death. A member on 0 HP is out of the fight and comes back at
the end of it. Only a **total wipe** kills, and it kills everyone — so a party
is genuinely safer than going alone, which is the point of forming one, while
permadeath keeps its teeth for the case where the whole plan fails.
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field

from .state import Contract, Encounter, Monster

MAX_PARTY = 4

_ids = itertools.count(1)


@dataclass
class Party:
    key: str
    leader: str                                   # mxid
    members: list[str] = field(default_factory=list)   # mxids, turn order
    invited: set[str] = field(default_factory=set)

    contract: Contract | None = None
    encounter: Encounter | None = None
    stage: int = 0
    # Who has already moved this round. Rounds are tracked by membership
    # rather than by a wrapping index: skipping a downed member used to reset
    # the index before it could wrap, so the monster never got a turn and a
    # party could farm forever with one member down.
    acted: set[str] = field(default_factory=set)
    rng: random.Random = field(default_factory=random.Random, repr=False)

    @property
    def on_contract(self) -> bool:
        return self.contract is not None

    @property
    def size(self) -> int:
        return len(self.members)

    @property
    def is_full(self) -> bool:
        return self.size >= MAX_PARTY

    def next_actor(self, standing: set[str]) -> str | None:
        """The next member still standing who has not moved this round."""
        for mxid in self.members:
            if mxid in standing and mxid not in self.acted:
                return mxid
        return None

    def record_action(self, mxid: str, standing: set[str]) -> bool:
        """Note that `mxid` moved. Returns True if the round is now complete."""
        self.acted.add(mxid)
        return self.next_actor(standing) is None

    def begin_round(self) -> None:
        self.acted.clear()


@dataclass
class Parties:
    """Every party on the server. Multiple parties run independently."""

    by_key: dict[str, Party] = field(default_factory=dict)

    def for_member(self, mxid: str) -> Party | None:
        for party in self.by_key.values():
            if mxid in party.members:
                return party
        return None

    def invitations_for(self, mxid: str) -> list[Party]:
        return [p for p in self.by_key.values() if mxid in p.invited]

    def create(self, leader: str, seed: int | None = None) -> Party:
        key = f"party-{next(_ids)}"
        party = Party(key=key, leader=leader, members=[leader],
                      rng=random.Random(seed))
        self.by_key[key] = party
        return party

    def disband(self, party: Party) -> None:
        self.by_key.pop(party.key, None)

    def remove_member(self, party: Party, mxid: str) -> None:
        """Drop a member, promoting or disbanding as needed."""
        if mxid in party.members:
            party.members.remove(mxid)
        party.invited.discard(mxid)
        party.acted.discard(mxid)

        if not party.members:
            self.disband(party)
            return
        if party.leader == mxid:
            party.leader = party.members[0]


def scaled_for_party(monster: Monster, size: int) -> Monster:
    """Toughen a monster for a group.

    Health scales close to linearly because a party lands roughly `size`
    attacks per monster turn. Power scales far more gently: a monster that hit
    `size` times harder would simply delete whoever it looked at, and being
    one-shot on somebody else's turn is not a fight you are playing.
    """
    if size <= 1:
        return monster
    from dataclasses import replace
    return replace(
        monster,
        max_hp=max(1, round(monster.max_hp * (1 + 0.85 * (size - 1)))),
        power=max(1, round(monster.power * (1 + 0.15 * (size - 1)))),
    )
