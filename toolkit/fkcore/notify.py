"""fkcore.notify: the push channel of ``notify all`` (CORE-V1 §6.4): game chat and the owner's inbox.

``push(r, game_dir, surface, lines, tick, source)`` appends each line to the game codebase's
inbox, ``<game>/out/inbox.txt``, as ``<tick> <source>: <line>``, then prints it in game chat
through the link, one ``game.print`` per line (``link.lua_say``), as ``[fk <surface>] <line>``.
The inbox goes first, so a chat call that fails loses no line.

Who pushes: a look (``fkcore.look``) pushes its "changes here" lines only when the surface's
notify is ``all`` (the lab default); with ``nearby`` (play) they stay in that look's report, and
``off`` pushes nothing. The alert tracker (``fkcore.alerts``) pushes the game's own alerts to chat
the same way when notify is ``all``. A chat line is one line of ASCII (a newline or a non-ASCII
character could not travel as one Lua 5.2 string literal through ``link._q``).
"""
from __future__ import annotations

import os

from . import link

__all__ = ["push", "inbox_path", "chat_text"]


def inbox_path(game_dir):
    return os.path.join(game_dir, "out", "inbox.txt")


def chat_text(surface, line):
    """The chat line for ``line``: one line, ASCII, prefixed ``[fk <surface>]``."""
    flat = " ".join(str(line).split("\n")).strip()
    return f"[fk {surface}] " + flat.encode("ascii", "replace").decode("ascii")


def push(r, game_dir, surface, lines, tick=None, source="look"):
    """Append ``lines`` to the inbox and print each in game chat; -> the lines pushed."""
    lines = [str(l) for l in lines if str(l).strip()]
    if not lines:
        return []
    path = inbox_path(game_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for line in lines:
            fh.write(f"{tick if tick is not None else '-'} {source}: {line}\n")
    for line in lines:
        r.lua(link.lua_say(chat_text(surface, line)))
    return lines
