"""Routing rules, character creation, and permadeath.

The routing tests matter as much as the combat ones: the bot shares a room with
human conversation, and a bot that answers chatter is worse than no bot.
"""

from __future__ import annotations

from core.chargen import CLASSES, RACES
from core.game import handle
from core.state import MAX_NAME, Player

from .conftest import make_char, make_player

# ---------------------------------------------------------------------------
# staying quiet
# ---------------------------------------------------------------------------

def test_non_commands_are_ignored():
    player = make_char()
    for chatter in ("hey", "has anyone seen my cat", "lol", "brb"):
        assert handle(player, chatter) is None


def test_bare_numbers_are_silent_outside_combat():
    player = make_char()
    assert handle(player, "1") is None


def test_prefixed_numbers_are_commands_during_combat():
    player = make_char()
    handle(player, "!board")
    handle(player, "!accept 1")
    assert handle(player, "!1") is not None


def test_unprefixed_numbers_stay_silent_even_during_combat():
    player = make_char()
    handle(player, "!board")
    handle(player, "!accept 1")
    assert handle(player, "1") is None
    assert handle(player, "strike") is None


def test_nothing_without_the_prefix_is_ever_a_command():
    """The whole point of the rule: no state makes bare text meaningful."""
    player = make_char()
    for text in ("board", "status", "help", "accept 1", "create", "flee", "1"):
        assert handle(player, text) is None, text


def test_creation_also_requires_the_prefix():
    """A half-finished register must not swallow ordinary conversation."""
    player = make_player()
    handle(player, "!create")
    assert handle(player, "just chatting in here") is None
    assert player.pending.step == "name", "chatter became a character name"
    handle(player, "!Grimwald")
    assert player.pending.step == "race"


def test_a_lone_bang_is_ignored():
    player = make_char()
    assert handle(player, "!") is None
    assert handle(player, "!   ") is None


def test_combat_still_ignores_ordinary_chat():
    player = make_char()
    handle(player, "!board")
    handle(player, "!accept 1")
    assert handle(player, "anyone want pizza") is None


def test_out_of_range_menu_number_is_explained_not_silent():
    """They typed `!`, so they meant something. Silence reads as 'bot is down'."""
    player = make_char()
    handle(player, "!board")
    handle(player, "!accept 1")
    reply = handle(player, "!9")
    assert reply is not None and "no action 9" in reply[0].lower()


def test_unknown_prefixed_commands_get_a_suggestion():
    player = make_char()
    reply = handle(player, "!bord")
    assert "don't know" in reply[0]
    assert "!board" in " ".join(reply)


def test_unknown_prefixed_commands_still_answer_without_a_suggestion():
    player = make_char()
    reply = handle(player, "!zzzzzzzz")
    assert reply is not None
    assert "!help" in " ".join(reply)


def test_typos_never_break_the_silence_rule_for_chat():
    """Only `!` earns a reply. Bare near-misses stay chat."""
    player = make_char()
    for text in ("bord", "inventroy", "board", "help"):
        assert handle(player, text) is None


def test_empty_input_is_ignored():
    player = make_char()
    assert handle(player, "   ") is None


# ---------------------------------------------------------------------------
# character creation
# ---------------------------------------------------------------------------

def test_nothing_works_before_a_character_exists():
    player = make_player()
    for cmd in ("!board", "!accept 1", "!status"):
        reply = handle(player, cmd)
        assert reply is not None
        assert "no character" in reply[0].lower()


def test_help_lists_commands_in_every_state():
    """A player who cannot see a command cannot learn it exists."""
    expected = ("!create", "!board", "!accept", "!status", "!inventory",
                "!shop", "!buy", "!use", "!portal", "!graveyard", "!help")

    # no character
    player = make_player()
    text = " ".join(handle(player, "!help"))
    for cmd in expected:
        assert cmd in text, f"{cmd} missing before character creation"

    # in the hall
    player = make_char(name="Helpy")
    text = " ".join(handle(player, "!help"))
    for cmd in expected:
        assert cmd in text, f"{cmd} missing in the hall"
    assert "Helpy" in text, "help should say who you are"

    # mid-fight
    handle(player, "!board")
    handle(player, "!accept 1")
    lines = handle(player, "!help")
    text = " ".join(lines)
    for cmd in expected:
        assert cmd in text, f"{cmd} missing in combat"
    for ability in player.character.abilities:
        assert ability.name in text, f"{ability.name} missing from combat help"


def test_chatter_is_still_ignored_before_a_character_exists():
    player = make_player()
    assert handle(player, "hello room") is None


def test_creation_walks_name_then_race_then_class():
    player = make_player()
    assert player.character is None

    handle(player, "!create")
    assert player.pending.step == "name"

    handle(player, "!Grimwald")
    assert player.pending.step == "race"

    handle(player, "!dwarf")
    assert player.pending.step == "class"

    handle(player, "!fighter")
    assert player.pending is None
    assert player.character.name == "Grimwald"
    assert player.character.race_key == "dwarf"
    assert player.character.class_key == "fighter"


def test_creation_accepts_numbers_or_names():
    by_number = make_player("@n:srv")
    handle(by_number, "!create"); handle(by_number, "!Ana")
    handle(by_number, "!3"); handle(by_number, "!2")

    by_name = make_player("@w:srv")
    handle(by_name, "!create"); handle(by_name, "!Ana")
    handle(by_name, f"!{RACES[2].key}"); handle(by_name, f"!{CLASSES[1].key}")

    assert by_number.character.race_key == by_name.character.race_key
    assert by_number.character.class_key == by_name.character.class_key


def test_creation_rejects_unknown_race_and_stays_on_the_step():
    player = make_player()
    handle(player, "!create"); handle(player, "!Ana")
    reply = handle(player, "!wombat")
    assert "don't know that race" in reply[0]
    assert player.pending.step == "race"


def test_creation_rejects_bad_names():
    player = make_player()
    handle(player, "!create")
    assert "short" in handle(player, "!a")[0].lower()
    assert "long" in handle(player, "!" + "x" * (MAX_NAME + 1))[0].lower()
    assert "letter" in handle(player, "!1234")[0].lower()
    assert player.pending.step == "name"


def test_creation_can_be_cancelled():
    player = make_player()
    handle(player, "!create")
    handle(player, "!cancel")
    assert player.pending is None
    assert player.character is None


def test_only_one_living_character_at_a_time():
    player = make_char(name="First")
    reply = handle(player, "!create")
    assert "still alive" in " ".join(reply).lower()
    assert player.character.name == "First"


def test_class_changes_the_numbers():
    fighter = make_char(char_class="fighter", mxid="@f:srv").character
    wizard = make_char(char_class="wizard", mxid="@w:srv").character
    assert fighter.max_hp > wizard.max_hp
    assert wizard.max_focus > fighter.max_focus
    assert fighter.abilities != wizard.abilities


def test_race_is_purely_cosmetic():
    """Race must never touch a stat — otherwise it becomes a trap choice."""
    from core.chargen import RACES
    stats = set()
    for race in RACES:
        char = make_char(race=race.key, char_class="fighter",
                         mxid=f"@{race.key}:srv").character
        stats.add((char.max_hp, char.power, char.max_focus, char.abilities))
    assert len(stats) == 1, "a race changed the numbers"


def test_race_still_shows_up_in_the_title():
    char = make_char(race="dwarf", char_class="cleric").character
    assert "Dwarf" in char.title and "Cleric" in char.title


# ---------------------------------------------------------------------------
# permadeath
# ---------------------------------------------------------------------------

def test_death_destroys_the_character_and_everything_on_it():
    player = make_char(name="Doomed")
    char = player.character
    char.renown, char.gold = 99, 250
    handle(player, "!board")
    handle(player, "!accept 1")
    # Re-apply each turn: at 1 HP the character can still *win* the encounter
    # and walk out of combat, which would never exercise the death path.
    for _ in range(80):
        if player.character is None:
            break
        if player.character.run is None:
            handle(player, "!board")
            handle(player, "!accept 1")
        player.character.run.hp = 1
        handle(player, "!1")

    assert player.character is None, "character survived a lethal fight"
    assert player.deaths == 1


def test_death_leaves_a_tombstone_but_no_advantage():
    player = make_char(name="Doomed")
    player.character.renown = 42
    handle(player, "!board"); handle(player, "!accept 1")
    for _ in range(80):
        if player.character is None:
            break
        if player.character.run is None:
            handle(player, "!board")
            handle(player, "!accept 1")
        player.character.run.hp = 1
        handle(player, "!1")

    grave = player.graveyard[-1]
    assert grave.name == "Doomed"
    assert grave.renown >= 42

    # The replacement starts from nothing.
    handle(player, "!create"); handle(player, "!Next")
    handle(player, "!human"); handle(player, "!fighter")
    assert player.character.renown == 0
    assert player.character.gold == 0
    assert player.character.runs_completed == 0


def test_graveyard_is_readable_with_no_character():
    player = make_player()
    reply = handle(player, "!graveyard")
    assert reply is not None and "died yet" in reply[0]


# ---------------------------------------------------------------------------
# the hall
# ---------------------------------------------------------------------------

def test_board_variants():
    player = make_char()
    for word in ("!board", "!quests", "!quest"):
        assert handle(player, word) is not None


def test_bang_prefix_works_for_every_command():
    player = make_char()
    assert handle(player, "!board") is not None
    assert handle(player, "!status") is not None
    assert handle(player, "!help") is not None


def test_accept_starts_a_run():
    player = make_char()
    handle(player, "!board")
    assert player.character.run is None
    handle(player, "!accept 1")
    assert player.character.run is not None
    assert player.character.in_combat


def test_accept_needs_an_index():
    player = make_char()
    handle(player, "!board")
    assert "Which one" in handle(player, "!accept")[0]


def test_accept_rejects_out_of_range():
    player = make_char()
    handle(player, "!board")
    assert "no contract" in handle(player, "!accept 99")[0]


def test_ability_names_match_menu_numbers():
    """`2` and `fireball` must select the same ability, for every class."""
    from core.game import _resolve_ability

    for cls in ("fighter", "wizard", "rogue", "cleric", "ranger"):
        char = make_char(char_class=cls, mxid=f"@{cls}:srv").character
        for i, ab in enumerate(char.abilities, 1):
            first_word = ab.name.lower().split()[0]
            assert _resolve_ability(char, str(i)) is ab, f"{cls} slot {i}"
            assert _resolve_ability(char, first_word) is ab, f"{cls} {first_word}"
            assert _resolve_ability(char, ab.key) is ab, f"{cls} {ab.key}"


def test_portal_abandons_the_run_and_pays_nothing():
    player = make_char()
    char = player.character
    char.gold, char.renown = 40, 10
    handle(player, "!board")
    handle(player, "!accept 1")

    reply = handle(player, "!portal")
    assert char.run is None
    assert player.character is not None, "bailing out must not kill the character"
    assert (char.gold, char.renown) == (40, 10), "no rewards for leaving"
    assert char.inventory == {}
    assert "abandoned" in " ".join(reply)


def test_portal_is_counted_and_the_clerk_escalates():
    """The counter is the joke; nothing mechanical reads it."""
    player = make_char()
    char = player.character
    seen = set()
    for expected in range(1, 10):
        handle(player, "!board")
        handle(player, "!accept 1")
        reply = handle(player, "!portal")
        assert char.portals_used == expected
        seen.add("\n".join(reply))
    assert len(seen) > 3, "the clerk should not repeat herself constantly"


def test_portal_aliases_all_work():
    for word in ("portal", "townportal", "tp", "escape", "flee", "run"):
        player = make_char(mxid=f"@{word}:srv")
        handle(player, "!board")
        handle(player, "!accept 1")
        assert handle(player, f"!{word}") is not None
        assert player.character.run is None, word


def test_portal_in_the_hall_is_a_joke_not_an_error():
    player = make_char()
    reply = handle(player, "!portal")
    assert reply is not None
    assert "already were" in " ".join(reply)
    assert player.character.portals_used == 0, "a no-op must not count"


def test_portals_die_with_the_character():
    player = make_char()
    handle(player, "!board")
    handle(player, "!accept 1")
    handle(player, "!portal")
    assert player.character.portals_used == 1

    for _ in range(80):
        if player.character is None:
            break
        if player.character.run is None:
            handle(player, "!board")
            handle(player, "!accept 1")
        player.character.run.hp = 1
        handle(player, "!1")

    handle(player, "!create"); handle(player, "!Next")
    handle(player, "!human"); handle(player, "!fighter")
    assert player.character.portals_used == 0


def test_flee_abandons_the_run():
    player = make_char()
    handle(player, "!board")
    handle(player, "!accept 1")
    reply = handle(player, "!flee")
    assert player.character.run is None
    assert player.character is not None, "fleeing must not kill the character"
    assert "abandoned" in " ".join(reply)


def test_flee_outside_combat_is_explained_not_silent():
    player = make_char()
    reply = handle(player, "!flee")
    assert reply is not None


def test_rank_gates_board_contents():
    low = make_char(mxid="@low:srv").character
    high = make_char(mxid="@high:srv").character
    high.renown = 100

    from core.game import roll_board
    roll_board(low)
    roll_board(high)

    assert low.rank < high.rank
    assert max(q.tier for q in low.board) <= low.rank
    assert max(q.tier for q in high.board) <= high.rank


def test_completing_a_contract_pays_out():
    player = make_char()
    char = player.character
    handle(player, "!board")
    quest = char.board[0]
    handle(player, "!accept 1")

    for _ in range(400):
        if char.run is None or player.character is None:
            break
        char.run.hp = char.run.max_hp  # immortal: we're testing the payout
        handle(player, "!1")

    if player.character is not None and char.runs_completed:
        assert char.gold >= quest.gold
        assert char.renown >= quest.renown


# --- refreshing the board --------------------------------------------------

def test_refresh_is_refused_when_broke():
    from core.game import REROLL_COST

    player = make_char()
    player.character.gold = REROLL_COST - 1
    before = list(player.character.board)
    reply = handle(player, "!refresh")

    assert "costs" in " ".join(reply).lower()
    assert player.character.board == before


def test_refresh_charges_and_reposts():
    from core.game import REROLL_COST

    player = make_char()
    player.character.gold = 100
    handle(player, "!board")

    reply = handle(player, "!refresh")
    assert player.character.gold == 100 - REROLL_COST
    assert "Quest Board" in " ".join(reply)


def test_refresh_works_with_a_roster_and_a_guild():
    """Regression: !refresh raised NameError reaching for a roster it lacked."""
    from core.guild import Guild

    player = make_char()
    player.character.gold = 50
    roster = {player.mxid: player}
    reply = handle(player, "!refresh", roster, Guild())
    assert reply is not None
    assert "Quest Board" in " ".join(reply)


def test_refresh_works_in_a_private_room():
    from core.duel import Duels
    from core.guild import Guild
    from core.party import Parties

    player = make_char()
    player.character.gold = 50
    reply = handle(player, "!refresh", {player.mxid: player}, Guild(),
                   Parties(), Duels(), None, True)
    assert reply is not None
    assert "Quest Board" in " ".join(reply)
