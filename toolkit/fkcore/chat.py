"""fkcore.chat: the game chat as the player's channel to the agent (CORE-V1.md 6.5).

The fk-play scenario keeps every chat line in ``storage.fk_chat`` (``scenario/fk-play/fk_chat.lua``:
registered in scenario code, never through RCON). ``read(r, since)`` is one RCON read of the lines
after a tick.

A message is not "delivered" by being printed once: it is **unanswered until the player dismisses
it**. ``pending(r, game_dir)`` prints every message after the player's ack watermark
(``<game>/out/chat-ack.json``, host-made) at the end of every command, so a chat line keeps showing
up until ``ack(r, game_dir)`` moves the watermark to now. With no ack file yet, the first command
sets it to the current tick: history is never replayed.

2026-10-25, run 1133: the old cursor moved on *read*, not on answer. 42 of that run's 201 fk
results lost their footer to a pipe (``| Select-Object -First N``); every message read there was
consumed unseen, and both supervisor hints were among them. ``say(r, text)`` writes one line to the
game chat. A map made from another scenario has no journal: ``read`` says so instead of failing.
"""
from __future__ import annotations

import json
import os

from . import link

__all__ = ["lua_read", "read", "pending", "ack", "say", "CURSOR", "ACK"]

CURSOR = os.path.join("out", "chat-cursor.json")   # 2.0.25: older runs' file, no longer read
ACK = os.path.join("out", "chat-ack.json")
HEAD = "UNANSWERED CHAT ({n}): answer with `fk say <reply>`, then dismiss with `fk ack`"


def lua_read(since):
    return f"""
local out = {{}}
for _, m in ipairs(storage.fk_chat or {{}}) do
  if m.tick > {int(since)} then out[#out + 1] = m end
end
rcon.print(helpers.table_to_json({{tick = game.tick, journal = storage.fk_chat ~= nil, messages = out}}))
"""


def read(r, since=0):
    """-> {tick, journal, messages: [{tick, name, message}]}."""
    text = r.lua(lua_read(since))
    try:
        got = json.loads(text)
    except ValueError:
        raise link.LinkError(f"chat read: not JSON: {text[:200]}")
    msgs = got.get("messages") or []
    if isinstance(msgs, dict):                   # an empty Lua table comes back as {}
        msgs = list(msgs.values())
    got["messages"] = sorted(msgs, key=lambda m: m.get("tick", 0))
    return got


def _ack(game_dir):
    try:
        with open(os.path.join(game_dir, ACK), encoding="utf-8") as fh:
            return int(json.load(fh).get("tick"))
    except (OSError, ValueError, TypeError, AttributeError):
        return None


def _save_ack(game_dir, tick):
    path = os.path.join(game_dir, ACK)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"tick": int(tick)}, fh)


def _age(tick, now):
    s = max(0.0, (int(now) - int(tick)) / 60.0)
    return f"{int(s)}s" if s < 90 else f"{int(round(s / 60))} min"


def _line(m, now):
    return (f"  chat {m.get('name') or '?'} ({_age(m.get('tick', 0), now)} ago): "
            f"{m.get('message', '')}")


def pending(r, game_dir):
    """The chat messages the player has not dismissed, as lines for the end of a command.

    Printed on every command (not once): a message stands until ``ack``. Empty when all are
    dismissed, so an answered channel stays quiet."""
    got = read(r, 0)
    if not got.get("journal"):
        return ["chat: this map keeps no chat journal (its scenario script predates fk_chat.lua; "
                "a map made from today's fk-play scenario has one)"]
    now = int(got.get("tick") or 0)
    ack = _ack(game_dir)
    if ack is None:                              # first use: never replay history
        _save_ack(game_dir, now)
        return []
    msgs = [m for m in got["messages"] if int(m.get("tick", 0)) > ack]
    if not msgs:
        return []
    return [HEAD.format(n=len(msgs))] + [_line(m, now) for m in msgs]


def ack(r, game_dir):
    """Dismiss every message up to now; the one way to clear the queue. -> the new watermark."""
    now = int(read(r, 0).get("tick") or 0)
    _save_ack(game_dir, now)
    return now


def say(r, text):
    return r.lua(link.lua_say(f"[agent] {text}"))
