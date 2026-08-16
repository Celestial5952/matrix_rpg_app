#!/usr/bin/env python3
"""Offline playtest REPL — the same core the Matrix adapter will drive.

    python3 play.py

Use this to balance combat. Restarting a bot and typing into Element to test a
damage tweak is how these projects die.
"""

from __future__ import annotations

import sys

from core.game import handle
from core.state import Player


def main() -> int:
    player = Player(mxid="@you:local", display_name="Playtester")

    print("=== Guild Hall (offline playtest) ===")
    print("Every command starts with ! — e.g. !create, !board. Ctrl-D to quit.\n")
    for line in handle(player, "!create") or []:
        print(line)

    while True:
        try:
            text = input("\n> ")
        except (EOFError, KeyboardInterrupt):
            print("\nSee you.")
            return 0

        reply = handle(player, text)
        if reply is None:
            print("_(not a command — the bot would stay silent here)_")
            continue
        print()
        for line in reply:
            print(line)


if __name__ == "__main__":
    sys.exit(main())
