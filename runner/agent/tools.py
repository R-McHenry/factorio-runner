"""The agent's tools, by profile, and the lab referee.

Lab rules: reading the game is free; changing it goes through the game (ghosts that robots build,
deconstruction orders, research, settings a player could set). The score is game ticks, so Lua
that creates, moves or deletes things directly, or touches the clock, is refused before it runs.

Profile "bare": lua, paste, deconstruct, research, wait, read_file, write_file, list_files, finish.
"""
from __future__ import annotations

import json
import os
import re

from .. import cell as cells
from ..node import read_state

#: Lua the referee refuses in a measured episode: (pattern, why)
FORBIDDEN = [
    (r"\.create_entity\s*\{(?![^}]*['\"](entity|tile)-ghost['\"])", "create_entity of anything but a ghost (use name = 'entity-ghost')"),
    (r"\.revive\s*\(", "revive builds a ghost for free"),
    (r"\.insert\s*[\({]", "insert creates items"),
    (r"\.insert_fluid\s*\(", "insert_fluid creates fluid"),
    (r"\.set_stack\s*\(|\.set_item\s*\(|\.transfer_stack\s*\(", "moves or creates items"),
    (r"\.remove_item\s*\(|\.clear_items_inside\s*\(|\.clear\s*\(\s*\)", "deletes items"),
    (r"\.destroy\s*\(|\.die\s*\(|\.damage\s*\(", "destroys entities (order deconstruction instead)"),
    (r"\.mine\s*\{|\.mine\s*\(", "mines by script"),
    (r"\.set_tiles\s*\(", "sets tiles by script (tile ghosts are fine)"),
    (r"\.teleport\s*\(", "moves entities by script"),
    (r"researched\s*=|research_progress\s*=|\.research_all_technologies|\.enable_all", "sets research directly (use force.add_research or the research tool)"),
    (r"game\.speed|ticks_to_run|tick_paused", "the clock belongs to the runner (use wait)"),
    (r"\.amount\s*=|\.energy\s*=|\.temperature\s*=|\.health\s*=|\.fluidbox\s*\[", "sets entity state directly"),
    (r"server_save|game\.reload|helpers\.write_file|game\.write_file", "server and file operations"),
    (r"\.create_inventory\s*\(", "script inventories"),
]
_FORBIDDEN = [(re.compile(p), why) for p, why in FORBIDDEN]


def referee(code: str) -> str | None:
    """None if the Lua may run, else why not."""
    flat = re.sub(r"--[^\n]*", "", code)
    for rx, why in _FORBIDDEN:
        if rx.search(flat):
            return why
    return None


def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required)}}}


BARE = [
    _fn("lua", "Run Lua in the game (Factorio 2.0 API, /sc). Reads are free. Output is what you "
        "rcon.print(). Writes must be things a player could do: ghosts (create_entity{name="
        "'entity-ghost', inner_name=..., position=..., direction=..., force='player'}), "
        "order_deconstruction, settings, logistic requests, add_research. The referee refuses "
        "anything that creates, moves or deletes items or entities directly, or touches the clock.",
        {"code": {"type": "string"}}, ["code"]),
    _fn("paste", "Paste a blueprint string as ghosts at x,y (robots build them from the logistic "
        "network). Obstacles in the way are marked for deconstruction.",
        {"blueprint": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"}},
        ["blueprint", "x", "y"]),
    _fn("deconstruct", "Order everything in the area deconstructed (robots take it up).",
        {"x0": {"type": "number"}, "y0": {"type": "number"}, "x1": {"type": "number"},
         "y1": {"type": "number"}}, ["x0", "y0", "x1", "y1"]),
    _fn("research", "Add a technology to the end of the research queue. -> the queue.",
        {"tech": {"type": "string"}}, ["tech"]),
    _fn("wait", "Let the game run for `ticks` ticks (60 ticks = 1 game second), then stand still "
        "again. Only game time counts, not your thinking time. Stops early when the goal is met. "
        "-> the tick and the goal's progress.",
        {"ticks": {"type": "integer"}}, ["ticks"]),
    _fn("read_file", "Read a file in your work dir (NOTES.md and your own files).",
        {"path": {"type": "string"}}, ["path"]),
    _fn("write_file", "Write a file in your work dir (whole content).",
        {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _fn("list_files", "List the files in your work dir.", {}, []),
    _fn("finish", "End the episode (when the goal is met, or you are stuck). NOTES.md should be "
        "current first: the next player starts from it.",
        {"summary": {"type": "string"}}, ["summary"]),
]

PROFILES = {"bare": BARE}

LUA_PASTE = """
local s = game.surfaces[1]
local inv = game.create_inventory(1)
inv.insert{name = 'blueprint'}
local stack = inv[1]
local r = stack.import_stack(%s)
if r ~= 0 then inv.destroy() rcon.print('import failed ' .. r) return end
local built = stack.build_blueprint{surface = s, force = 'player', position = {%s, %s}, build_mode = defines.build_mode.superforced}
inv.destroy()
rcon.print('ghosts placed: ' .. #built)
"""

LUA_DECON = """
local s = game.surfaces[1]
local a = {{%s, %s}, {%s, %s}}
s.deconstruct_area{area = a, force = 'player'}
local n = 0
for _, e in pairs(s.find_entities_filtered{area = a}) do if e.to_be_deconstructed() then n = n + 1 end end
rcon.print('marked for deconstruction: ' .. n)
"""

LUA_RESEARCH = """
local f = game.forces.player
local ok = f.add_research(%s)
local q = {}
for _, t in pairs(f.research_queue or {}) do q[#q + 1] = t.name end
rcon.print((ok and 'queued' or 'not queued (unknown, done, or not yet available)') .. '; queue: ' .. table.concat(q, ', '))
"""


class Toolbox:
    """Runs tool calls for one episode. `ctx` is the episode (goal, budgets, snapshot hook)."""

    def __init__(self, server, workdir: str, ctx, out_limit: int = 8000):
        self.server = server
        self.workdir = os.path.abspath(workdir)
        self.ctx = ctx
        self.out_limit = out_limit
        self.finished: str | None = None
        self.refused = 0

    def _path(self, p: str) -> str:
        full = os.path.normpath(os.path.join(self.workdir, p))
        if full != self.workdir and not full.startswith(self.workdir + os.sep):
            raise ValueError("outside the work dir")
        return full

    def _cut(self, s: str) -> str:
        return s if len(s) <= self.out_limit else s[: self.out_limit] + f"\n... [{len(s) - self.out_limit} more chars cut]"

    def call(self, name: str, args: dict) -> str:
        try:
            return self._cut(getattr(self, "t_" + name)(**args))
        except AttributeError:
            return f"error: no tool {name}"
        except TypeError as e:
            return f"error: bad arguments for {name}: {e}"
        except Exception as e:  # the game or the file system said no; the agent should see it
            return f"error: {type(e).__name__}: {e}"

    # -- tools ----------------------------------------------------------------------------------
    def t_lua(self, code: str) -> str:
        why = referee(code)
        if why:
            self.refused += 1
            return f"refused by the lab referee: {why}"
        return self.server.lua(code) or "(no output: use rcon.print)"

    def t_paste(self, blueprint: str, x: float, y: float) -> str:
        return self.server.lua(LUA_PASTE % (json.dumps(blueprint), float(x), float(y)))

    def t_deconstruct(self, x0, y0, x1, y1) -> str:
        return self.server.lua(LUA_DECON % (float(x0), float(y0), float(x1), float(y1)))

    def t_research(self, tech: str) -> str:
        return self.server.lua(LUA_RESEARCH % json.dumps(tech))

    def t_wait(self, ticks: int) -> str:
        return self.ctx.wait(int(ticks))

    def t_read_file(self, path: str) -> str:
        with open(self._path(path), encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def t_write_file(self, path: str, content: str) -> str:
        full = self._path(path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(content)
        return f"wrote {path} ({len(content)} chars)"

    def t_list_files(self) -> str:
        out = []
        for dirpath, _, files in os.walk(self.workdir):
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                out.append(f"{os.path.relpath(full, self.workdir)}  {os.path.getsize(full)} B")
        return "\n".join(sorted(out)) or "(empty)"

    def t_finish(self, summary: str) -> str:
        self.finished = summary
        return "episode ending"


def goal_text(goal: dict, made: dict) -> str:
    rows = []
    for item, lvl in sorted(goal.items()):
        need = cells.count_for(lvl)
        have = int(made.get(item, 0))
        rows.append(f"{item} {have}/{need}{' ok' if have >= need else ''}")
    return ", ".join(rows)


def state_text(server, goal: dict) -> str:
    st = read_state(server)
    return f"tick {st['tick']}; goal (items produced, total): {goal_text(goal, st['made'])}"
