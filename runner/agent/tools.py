"""The agent's tools, by profile, and the lab referee.

Lab rules: reading the game is free; changing it goes through the game (ghosts that robots build,
deconstruction orders, research, settings a player could set). The score is game ticks of a legit
lineage, so Lua that creates, moves or deletes things directly, or touches the clock, makes the run
stop counting: the referee saves the state just before it as last-valid-N and lets it run. Loading a
legit state makes the run count again.

Profile "bare": lua, paste, deconstruct, research, evaluate, save_state, load_state, list_states,
read_file, write_file, list_files, finish.
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
    (r"(?<!table)\.insert\s*[\({]", "insert creates items"),
    (r"\.insert_fluid\s*\(", "insert_fluid creates fluid"),
    (r"\.set_stack\s*\(|\.set_item\s*\(|\.transfer_stack\s*\(", "moves or creates items"),
    (r"\.remove_item\s*\(|\.clear_items_inside\s*\(|\.clear\s*\(\s*\)", "deletes items"),
    (r"\.destroy\s*\(|\.die\s*\(|\.damage\s*\(", "destroys entities (order deconstruction instead)"),
    (r"\.mine\s*\{|\.mine\s*\(", "mines by script"),
    (r"\.set_tiles\s*\(", "sets tiles by script (tile ghosts are fine)"),
    (r"\.teleport\s*\(", "moves entities by script"),
    (r"researched\s*=(?!=)|research_progress\s*=(?!=)|\.research_all_technologies|\.enable_all", "sets research directly (use force.add_research or the research tool)"),
    (r"game\.speed\s*=(?!=)|ticks_to_run\s*=(?!=)|tick_paused\s*=(?!=)", "the clock belongs to the runner (use evaluate)"),
    (r"\.amount\s*=(?!=)|\.energy\s*=(?!=)|\.temperature\s*=(?!=)|\.health\s*=(?!=)|\.fluidbox\s*\[[^\]]*\]\s*=(?!=)", "sets entity state directly"),
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
        "rcon.print(). Changes a player could make keep the run legit: ghosts (create_entity{name="
        "'entity-ghost', inner_name=..., position=..., direction=..., force='player'}), "
        "order_deconstruction, settings, logistic requests, add_research. Lua that creates, moves "
        "or deletes items or entities directly, or touches the clock, still runs, but the run stops "
        "counting from there (a saved state from just before stays legit).",
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
    _fn("evaluate", "Run the game forward `ticks` ticks (60 ticks = 1 game second), then pause "
        "again. Game time only moves here; your thinking costs none. Stops early when the "
        "episode's goal is met. -> the tick and the goal's progress.",
        {"ticks": {"type": "integer"}}, ["ticks"]),
    _fn("save_state", "Save the game as a named state, to come back to (undo, or try several "
        "ideas from one point). Your work dir files are not part of it.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("load_state", "Load a named state (yours, or one the runner saved, like start or "
        "last-valid-N). The game goes back to that tick.",
        {"name": {"type": "string"}}, ["name"]),
    _fn("list_states", "List the saved states: name, tick, and whether each is legit.", {}, []),
    _fn("read_file", "Read a file in your work dir (NOTES.md and your own files).",
        {"path": {"type": "string"}}, ["path"]),
    _fn("write_file", "Write a file in your work dir (whole content).",
        {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
    _fn("list_files", "List the files in your work dir.", {}, []),
    _fn("finish", "End this episode (when the goal is met, or you are stuck). NOTES.md should be "
        "current first: whoever continues from here starts from it.",
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
    """Runs tool calls. `ctx` (the session) owns the clock, the saved states and the run's
    validity: lua goes through ctx.lua, and every result ends with ctx.status_tag()."""

    def __init__(self, server, workdir: str, ctx, out_limit: int = 8000):
        self.server = server
        self.workdir = os.path.abspath(workdir)
        self.ctx = ctx
        self.out_limit = out_limit
        self.finished: str | None = None

    def _path(self, p: str) -> str:
        full = os.path.normpath(os.path.join(self.workdir, p))
        if full != self.workdir and not full.startswith(self.workdir + os.sep):
            raise ValueError("outside the work dir")
        return full

    def _cut(self, s: str) -> str:
        return s if len(s) <= self.out_limit else s[: self.out_limit] + f"\n... [{len(s) - self.out_limit} more chars cut]"

    def call(self, name: str, args: dict) -> str:
        fn = getattr(self, "t_" + name, None)
        if fn is None:
            out = f"error: no tool {name}"
        else:
            try:
                out = self._cut(fn(**args))
            except TypeError as e:
                out = f"error: bad arguments for {name}: {e}"
            except Exception as e:  # the game or the file system said no; the agent should see it
                out = f"error: {type(e).__name__}: {e}"
        return f"{out}\n{self.ctx.status_tag()}"

    # -- tools ----------------------------------------------------------------------------------
    def t_lua(self, code: str) -> str:
        return self.ctx.lua(code) or "(no output: use rcon.print)"

    def t_paste(self, blueprint: str, x: float, y: float) -> str:
        return self.ctx.server.lua(LUA_PASTE % (json.dumps(blueprint), float(x), float(y)))

    def t_deconstruct(self, x0, y0, x1, y1) -> str:
        return self.ctx.server.lua(LUA_DECON % (float(x0), float(y0), float(x1), float(y1)))

    def t_research(self, tech: str) -> str:
        return self.ctx.server.lua(LUA_RESEARCH % json.dumps(tech))

    def t_evaluate(self, ticks: int) -> str:
        return self.ctx.evaluate(int(ticks))

    def t_save_state(self, name: str) -> str:
        return self.ctx.save_state(str(name))

    def t_load_state(self, name: str) -> str:
        return self.ctx.load_state(str(name))

    def t_list_states(self) -> str:
        return self.ctx.list_states()

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


