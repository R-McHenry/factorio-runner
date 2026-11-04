"""fkcore.explore: Go-Explore on the speedrun planner (2.0.64).

Rory, 2026-10-29: "define 'cells' as low resolution snapshot of a game state such as
floor(log(item)) foreach item in total items produced. Then each time you reach a new cell, you can
save the trajectory of actions that got there. The go explore loop is to return to a cell state,
explore to seek new cells, and when a new cell is reached save again, or if an existing cell is
reached more quickly, then the previous trajectory is replaced with the new way." And: "the
simplest way to implement the agent is its just a mode of the sim where ... there is just a switch
that the agent turns on and the sim picks [the state] by itself and gives the agent a goal cell",
the goal "at least some N distance in manhattan", go positions and targets impartial, and a prior,
if any, "based on the cells visited by our most successful runs".

The pieces:

- **A cell.** ``{item: floor(log_base(produced))}`` over every item the force has produced (the
  game's production statistics, cumulative). ``base`` is the resolution: 2 by default, smaller is
  finer. An item never produced has no entry, and counts as level -1 in a distance.
- **The archive** (``sim\\explore\\archive.json``). One entry per cell: its levels, the first tick it
  was reached at, the save state that holds it, and the trajectory that got there.
  - **The trajectory.** The actions from the start, each at its tick: a ``paste``, a ``decon``, or
    a ``game`` command (research, lua, decon).
  - **A faster way.** A cell reached at an earlier tick has its trajectory and its save replaced.
- **An episode** (``explore on`` / ``next``).
  - **Go.** A cell picked uniformly from the archive (or from the cells on the way to the best
    ones, with ``prior``), loaded from its save.
  - **The goal.** That cell plus ``dist`` steps of +1 level, each on an item picked uniformly from
    those the archive has seen produced and those the force can make there.
  - **Reaching it.** Reached when every one of those items stands at its goal level or above. The
    cells passed on the way go into the archive too.

The sim records the actions and checks the cell as the game runs (``fkcore.sim``). Stdlib only.
"""
from __future__ import annotations

import json
import math
import os
import random
import time

__all__ = ["cell", "key", "distance", "Archive", "LUA_MADE", "ExploreError"]

#: the production statistics (cumulative, every item made) and the items the force can make now
LUA_MADE = """
local f = game.forces['player']
local st = f.get_item_production_statistics('nauvis')
local made = {}
for name, n in pairs(st.input_counts) do made[name] = n end
local can = {}
for name, r in pairs(f.recipes) do
  if r.enabled and not r.hidden then
    for _, p in pairs(r.products) do if p.type == 'item' then can[p.name] = true end end
  end
end
local list = {}
for name in pairs(can) do list[#list + 1] = name end
local done = {}
for name, t in pairs(f.technologies) do if t.researched then done[#done + 1] = name end end
rcon.print(helpers.table_to_json({tick = game.tick, made = made, can = list, researched = done}))
"""


class ExploreError(RuntimeError):
    pass


def cell(made, base=2.0):
    """{item: count produced} -> {item: floor(log_base(count))}, every item made at least once."""
    out = {}
    for item, n in made.items():
        n = float(n)
        if n >= 1:
            # a hair over, so an exact power (1024 = 2^10) is its own level despite float rounding
            out[item] = int(math.floor(math.log(n, base) + 1e-9))
    return out


def key(levels):
    """A cell's archive key: ``item=level`` sorted, joined by commas."""
    return ",".join(f"{i}={levels[i]}" for i in sorted(levels))


def levels_of(k):
    return {p.split("=")[0]: int(p.split("=")[1]) for p in k.split(",") if p}


def distance(a, b):
    """Manhattan distance between two cells (an item missing from one is level -1 there)."""
    return sum(abs(a.get(i, -1) - b.get(i, -1)) for i in set(a) | set(b))


def deficit(levels, made, need, base):
    """The science-pack levels ``levels`` (a cell) lacks to have unlocked something that needs
    ``need`` ({pack: count}, ``fkcore.techcost``) more packs than ``made`` has made: for each pack,
    floor(log(made + need)) less the cell's level of it, when that is short (2.0.66)."""
    out = 0
    for p, n in (need or {}).items():
        if n <= 0:
            continue
        want = int(math.floor(math.log(float(made.get(p, 0)) + n, base) + 1e-9))
        out += max(0, want - levels.get(p, -1))
    return out


def step_cost(levels, item, need, made, base):
    """What one +1 step on ``item`` costs in the metric (2.0.66, Rory: "alter the manhattan metric
    space to penalize any connections between cells with fewer corresponding science than is
    required"): 1, plus the science levels the cell lacks for it when the step is the item's
    first (an item already made is unlocked). None when nothing can make it."""
    if levels.get(item, -1) >= 0:
        return 1
    if need is None:
        return None
    return 1 + deficit(levels, made, need, base)


def vertical(levels, tree, base):
    """How far up the game a cell is (2.0.67, Rory: "make sure that the positive material axis
    and the research axis fold into the same dimension, so getting more of an item is always
    still counting as pushing the game forward far past the last research").

    It's one currency, raw units (``Tree.values``): the worth of what the cell has made
    (each item's count, its level's lower bound, times its worth), plus the worth of the science
    packs that what it has made implies (the research behind each item, from nothing researched)
    beyond the packs already counted as made. -> log2 of that worth (0 for nothing)."""
    values = tree.values()
    fresh = tree.unlock_packs()
    techs = set()
    for item, lvl in levels.items():
        if lvl >= 0 and item in fresh:
            techs |= fresh[item]
    implied = tree.packs(techs)
    counts = {i: need_count(lvl, base) for i, lvl in levels.items() if lvl >= 0}
    worth = sum(values.get(i, tree.RAW) * n for i, n in counts.items())
    worth += sum(values.get(p, tree.RAW) * max(0, n - counts.get(p, 0)) for p, n in implied.items())
    return math.log2(worth) if worth > 1 else 0.0


class Worth:
    """``vertical``, kept up step by step (2.0.68): a goal walk tries hundreds of +1 steps, and
    each costs only what it changes (one item's count, the research a new item adds)."""

    def __init__(self, tree, base, levels):
        self.tree, self.base = tree, float(base)
        self.values, self.fresh = tree.values(), tree.unlock_packs()
        self.levels = {i: lv for i, lv in levels.items() if lv >= 0}
        self.counts = {i: need_count(lv, self.base) for i, lv in self.levels.items()}
        self.techs = set()
        for i in self.levels:
            self.techs |= self.fresh.get(i, frozenset())
        self.implied = tree.packs(self.techs)
        self.made = sum(self._val(i) * n for i, n in self.counts.items())
        self.research = sum(self._term(p) for p in self.implied)

    def _val(self, item):
        return self.values.get(item, self.tree.RAW)

    def _term(self, p, implied=None, counts=None):
        implied = self.implied if implied is None else implied
        counts = self.counts if counts is None else counts
        return self._val(p) * max(0, implied.get(p, 0) - counts.get(p, 0))

    @staticmethod
    def v(worth):
        return math.log2(worth) if worth > 1 else 0.0

    @property
    def now(self):
        return self.v(self.made + self.research)

    def _step(self, item):
        """(made, research, new techs, new count) after +1 on ``item``, or None: nothing makes it."""
        lv = self.levels.get(item, -1) + 1
        n = need_count(lv, self.base)
        made = self.made + self._val(item) * (n - self.counts.get(item, 0))
        new = frozenset()
        if item not in self.levels:
            if item not in self.fresh:
                return None
            new = self.fresh[item] - self.techs
        add = self.tree.packs(new) if new else {}
        touched = set(add) | ({item} if item in self.implied or item in add else set())
        if not touched:
            return made, self.research, new, n
        implied = dict(self.implied)
        for p, k in add.items():
            implied[p] = implied.get(p, 0) + k
        counts = dict(self.counts)
        counts[item] = n
        research = self.research + sum(self._term(p, implied, counts) - self._term(p)
                                       for p in touched)
        return made, research, new, n

    def after(self, item):
        """V after +1 on ``item``, or None."""
        got = self._step(item)
        return None if got is None else self.v(got[0] + got[1])

    def apply(self, item):
        made, research, new, n = self._step(item)
        if new:
            add = self.tree.packs(new)
            self.techs |= new
            for p, k in add.items():
                self.implied[p] = self.implied.get(p, 0) + k
        self.levels[item] = self.levels.get(item, -1) + 1
        self.counts[item] = n
        self.made, self.research = made, research


#: H bands, by their low ends: on the path, 1 off, 2-3, 4-7, 8-15, 16 and more
H_BANDS = (0, 1, 2, 4, 8, 16)


def h_band(h):
    return max(i for i, lo in enumerate(H_BANDS) if h >= lo)


def reached(now, goal):
    """True when ``now`` stands at or above ``goal`` on every item of the goal."""
    return all(now.get(i, -1) >= lvl for i, lvl in goal.items())


def shortfall(now, goal):
    """{item: (now, goal)} for the goal's items not reached yet."""
    return {i: (now.get(i, -1), lvl) for i, lvl in sorted(goal.items()) if now.get(i, -1) < lvl}


def need_count(level, base):
    """The count a level starts at: level 10 at base 2 is 1024; level 0 is 1."""
    return int(math.ceil(base ** level - 1e-9))


def manifest(goal, made, base):
    """The goal as the player sees it (2.0.65, Rory: "the player shouldn't be exposed to cells
    beyond the goal its given which is just a material manifest"): [(item, total to have made,
    made so far)], the items still short first."""
    rows = [(i, need_count(lvl, base), int(made.get(i, 0))) for i, lvl in sorted(goal.items())]
    return sorted(rows, key=lambda r: (r[2] >= r[1], r[0]))


def manifest_text(goal, made, base):
    rows = manifest(goal, made, base)
    return "; ".join(f"{i} {have:,} of {need:,}" + (" (done)" if have >= need else "")
                     for i, need, have in rows)


def round_end(ep, st, tick, wall):
    """Why the round is over, or None (2.0.65, Rory: "an early stop mode that tells the agent its
    worked on the cell goal long enough and its time for a new round"): its manifest met, its
    budget of ticks since its start spent, or its minutes of the agent's own time."""
    if ep.get("reached") is not None:
        return f"the manifest is met (tick {ep['reached']}, {ep['reached'] - ep['start']:,} ticks in)"
    budget = int(st.get("budget") or 0)
    if budget and tick - int(ep["start"]) >= budget:
        return f"its budget of {budget:,} ticks is spent"
    minutes = float(st.get("minutes") or 0)
    if minutes and wall - float(ep.get("wall", wall)) >= minutes * 60:
        return f"its {minutes:g} minutes are up"
    return None


def ticks_left(ep, st, tick):
    """The ticks the round may still run (its budget), or None for no limit."""
    budget = int(st.get("budget") or 0)
    return max(0, int(ep["start"]) + budget - tick) if budget else None


def count_text(level, base):
    """A level as the count it stands for: level 10 at base 2 is 1024+, level -1 is none."""
    if level < 0:
        return "none"
    n = base ** level
    return f"{int(math.ceil(n - 1e-9)):,}+"


class Archive:
    """``sim\\explore\\archive.json``: the settings, the cells and the current episode."""

    def __init__(self, sim_root):
        self.root = os.path.join(sim_root, "explore")
        self.path = os.path.join(self.root, "archive.json")
        self.files = os.path.join(self.root, "files")
        self.data = {"settings": {}, "cells": {}, "episode": None, "episodes": 0, "next_id": 0,
                     "on": False}
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as fh:
                self.data.update(json.load(fh))

    def write(self):
        os.makedirs(self.root, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.data, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    # -- settings ---------------------------------------------------------------------------------
    @property
    def on(self):
        return bool(self.data.get("on"))

    @property
    def settings(self):
        s = {"base": 2.0, "dist": 4, "budget": 36000, "chunk": 600, "prior": 0, "minutes": 0,
             "auto": 1, "pick": "grid", "rise": 0.1, "steps": 12}
        s.update(self.data.get("settings") or {})
        return s

    @property
    def cells(self):
        return self.data["cells"]

    @property
    def episode(self):
        return self.data.get("episode")

    def new_id(self):
        self.data["next_id"] = int(self.data.get("next_id", 0)) + 1
        return f"x{self.data['next_id']}"

    # -- cells --------------------------------------------------------------------------------------
    def offer(self, levels, tick, traj, save_name_for, path=None):
        """A cell reached at ``tick`` by ``traj``: -> "new", "faster" or None (known, not faster).
        ``save_name_for(name)`` saves the game as that save state; it is called only when the
        cell is new or faster. ``path``: the cells passed on the way, from the root (2.0.67)."""
        k = key(levels)
        have = self.cells.get(k)
        ep = self.episode or {}
        if have is not None and int(have["tick"]) <= tick:
            return None
        root = self.data.get("root", "x0")
        name = have["save"] if have is not None and have["save"] != root else self.new_id()
        save_name_for(name)
        self.cells[k] = {"levels": levels, "tick": tick, "save": name, "traj": list(traj),
                         "path": list(path or ()) + [k],
                         "found": time.strftime("%Y-%m-%dT%H:%M:%S"),
                         "episode": ep.get("n"), "parent": ep.get("go"),
                         "visits": (have or {}).get("visits", 0),
                         "beaten": (have or {}).get("beaten", 0) + (1 if have else 0)}
        return "faster" if have is not None else "new"

    def best(self, k=1):
        """The ``k`` furthest cells: most levels in all, then the earliest tick."""
        ranked = sorted(self.cells.items(),
                        key=lambda kv: (-sum(kv[1]["levels"].values()), int(kv[1]["tick"])))
        return ranked[:k]

    def ancestry(self, k):
        """The cells on the way to ``k``: it, its parent (the go cell of the episode that found
        it), that one's, and so on."""
        out, seen = [], set()
        while k and k in self.cells and k not in seen:
            seen.add(k)
            out.append(k)
            k = self.cells[k].get("parent")
        return out

    # -- positions (2.0.67) ---------------------------------------------------------------------
    def tree(self):
        """The tech tree the planner read into the archive, or None before the first round."""
        from . import techcost
        path = os.path.join(self.root, "techtree.json")
        if getattr(self, "_tree", None) is None and os.path.exists(path):
            self._tree = techcost.Tree.load(path)
        return getattr(self, "_tree", None)

    def positions(self):
        """{key: (V, H)} and the best path. V: ``vertical``. The best path: the cells passed on
        the way to the cell of the highest V, the earliest at a tie. H: the Manhattan distance
        to the nearest cell of the best path no higher than the cell itself (0 on it).
        -> (positions, best path keys)."""
        tree, base = self.tree(), float(self.settings["base"])
        if tree is None or not self.cells:
            return {}, []
        v = {k: vertical(row["levels"], tree, base) for k, row in self.cells.items()}
        top = max(self.cells, key=lambda k: (v[k], -int(self.cells[k]["tick"])))
        path = [k for k in self.cells[top].get("path") or self.ancestry(top)[::-1]
                if k in self.cells]
        out = {}
        for k, row in self.cells.items():
            near = [p for p in path if v[p] <= v[k] + 1e-9] or path
            h = min(distance(row["levels"], self.cells[p]["levels"]) for p in near) if near else 0
            out[k] = (v[k], h)
        return out, path

    def pick_go(self, rng):
        """The go cell: uniform over the archive, or with ``prior`` K over the cells on the way to
        the K furthest (Rory: a prior "based on the cells visited by our most successful runs").
        With ``pick`` track (2.0.67): a band of V picked uniformly (eight between the lowest and
        the highest), then a cell in it, the nearer the best path (low H) the likelier."""
        keys = sorted(self.cells)
        if not keys:
            raise ExploreError("the archive is empty (fk sim explore on starts it)")
        if self.settings.get("pick", "grid") == "grid":
            # 2.0.68 (Rory: "make both the go and explore actions be based on selecting cells
            # using these coordinates"): uniform over the occupied bins of V (eight bands) x H
            # (``H_BANDS``), then over the cells in one, so a crowded corner of the map doesn't
            # outweigh its edges
            pos, _path = self.positions()
            if pos:
                lo = min(p[0] for p in pos.values())
                hi = max(p[0] for p in pos.values())
                width = (hi - lo) / 8 or 1.0
                bins = {}
                for k in keys:
                    bins.setdefault((min(7, int((pos[k][0] - lo) / width)), h_band(pos[k][1])),
                                    []).append(k)
                return rng.choice(bins[rng.choice(sorted(bins))])
        if self.settings.get("pick") == "track":
            pos, _path = self.positions()
            if pos:
                lo = min(p[0] for p in pos.values())
                hi = max(p[0] for p in pos.values())
                width = (hi - lo) / 8 or 1.0
                bands = {}
                for k in keys:
                    bands.setdefault(min(7, int((pos[k][0] - lo) / width)), []).append(k)
                band = bands[rng.choice(sorted(bands))]
                weights = [1.0 / (1.0 + pos[k][1]) for k in band]
                return rng.choices(band, weights=weights)[0]
        prior = int(self.settings["prior"] or 0)
        if prior > 0:
            pool = sorted({c for k, _row in self.best(prior) for c in self.ancestry(k)})
            if pool:
                keys = pool
        return rng.choice(keys)

    def pick_goal(self, go_levels, can, dist, rng, needs=None, made=None):
        """The goal: a walk from ``go_levels`` of +1 steps within ``dist``. Each step is picked
        uniformly from the steps the budget left still affords.

        - **With ``needs``** ({item: {pack: count}} for every item something can make, 2.0.66):
          the pool is every such item, and a step costs ``step_cost``. An item far up the tech
          tree costs the science levels it lacks, so it comes into reach only once those are
          near, or the walk has taken them.
        - **Without it**: the items the archive has seen made and those the force can make now
          (``can``), a step each. -> {item: level}, the goal's items only."""
        base = float(self.settings["base"])
        made = made or {}
        if needs is not None:
            pool = sorted(set(needs) | {i for i, lv in go_levels.items() if lv >= 0})
        else:
            seen = set()
            for row in self.cells.values():
                seen |= set(row["levels"])
            pool = sorted(seen | set(can))
        if not pool:
            raise ExploreError("no item to aim for: nothing made yet and no recipe enabled")
        levels, goal, left = dict(go_levels), {}, int(dist)
        while left > 0:
            options = []
            for item in pool:
                c = (step_cost(levels, item, needs.get(item), made, base)
                     if needs is not None else 1)
                if c is not None and c <= left:
                    options.append((item, c))
            if not options:
                break
            item, c = rng.choice(options)
            levels[item] = levels.get(item, -1) + 1
            goal[item] = levels[item]
            left -= c
        return goal

    def pick_goal_vh(self, go_levels, needs, made, rng, tries=40):
        """The goal, in coordinates (2.0.68): ``tries`` candidate cells, each a walk of +1 steps
        from the go cell until its V has risen by ``rise``. A step is picked uniformly from those
        that keep V at most 2 x ``rise`` above the go cell (an item far up the tech tree overshoots
        by itself, so it never comes up for a small rise), at most ``steps`` of them. Then an H
        band picked uniformly among the candidates', and a candidate in it. -> {item: level}, the
        goal's items only; {} when no walk rises at all."""
        tree, base, st = self.tree(), float(self.settings["base"]), self.settings
        rise, steps = float(st["rise"]), int(st["steps"])
        pos, path = self.positions()
        start = Worth(tree, base, go_levels)
        v0 = start.now
        # items only: the cells count items made, so a fluid in a manifest could never be met
        pool = sorted((set(needs) | set(start.levels)) - set(tree.fluids))
        cands = []
        for _ in range(int(tries)):
            w = Worth(tree, base, go_levels)
            goal = {}
            for _s in range(steps):
                if w.now >= v0 + rise:
                    break
                now, ok = w.now, []
                for i in pool:
                    v = w.after(i)
                    if v is not None and now < v <= v0 + 2 * rise:
                        ok.append(i)
                if not ok:
                    break
                item = rng.choice(ok)
                w.apply(item)
                goal[item] = w.levels[item]
            if goal:
                cands.append((w.now >= v0 + rise, w.now, goal, w.levels))
        if not cands:
            return {}
        full = [c for c in cands if c[0]] or sorted(cands, key=lambda c: -c[1])[:1]
        near = [p for p in path if p in self.cells]

        def h(levels, v):
            on = [p for p in near if pos.get(p, (0, 0))[0] <= v + 1e-9] or near
            return min(distance(levels, self.cells[p]["levels"]) for p in on) if on else 0
        bands = {}
        for _ok, v, goal, levels in full:
            bands.setdefault(h_band(h(levels, v)), []).append(goal)
        return rng.choice(bands[rng.choice(sorted(bands))])

    def start_episode(self, go, goal, tick):
        self.data["episodes"] = int(self.data.get("episodes", 0)) + 1
        self.cells[go]["visits"] = int(self.cells[go].get("visits", 0)) + 1
        self.data["episode"] = {"n": self.data["episodes"], "go": go, "goal": goal,
                                "start": tick, "traj": list(self.cells[go]["traj"]),
                                "path": list(self.cells[go].get("path") or [go]),
                                "found": [], "faster": [], "reached": None,
                                "wall": time.time(), "ended": None}
        return self.data["episode"]

    def record(self, tick, verb, args):
        ep = self.episode
        if ep is None:
            raise ExploreError("no episode (fk sim explore on)")
        ep["traj"].append({"tick": int(tick), "verb": verb, "args": list(args)})

    def keep_file(self, path):
        """A copy of a file an action names, under ``explore\\files`` (by content), so a trajectory
        still plays when the player's file has changed. -> the copy's path."""
        import hashlib
        with open(path, "rb") as fh:
            data = fh.read()
        ext = os.path.splitext(path)[1]
        name = hashlib.sha1(data).hexdigest()[:12] + ext
        os.makedirs(self.files, exist_ok=True)
        dest = os.path.join(self.files, name)
        if not os.path.exists(dest):
            with open(dest, "wb") as fh:
                fh.write(data)
        return dest

    # -- reports ------------------------------------------------------------------------------------
    def plan(self, k, end_tick=None):
        """The trajectory of cell ``k`` as a playback script (``fkcore.sim.play_lines``)."""
        row = self.cells[k]
        lines = [f"# fk sim explore: the way to cell {row['save']} (tick {row['tick']}, "
                 f"{sum(row['levels'].values())} levels)",
                 f"from {self.data.get('root', 'x0')}", "speed 100"]
        for a in row["traj"]:
            lines.append(f"at {a['tick']}")
            if a["verb"] == "paste":
                lines.append(f"paste {a['args'][0]} @{a['args'][1]},{a['args'][2]}")
            elif a["verb"] == "decon":
                lines.append("decon " + ",".join(str(v) for v in a["args"]))
            elif a["verb"] == "game":
                args = [str(v) for v in a["args"]]
                if args[:1] == ["lua"] and len(args) == 2:
                    # a chunk of Lua is a file of its own: a playback line is one line of words
                    path = self._lua_file(args[1])
                    args = ["lua", "--file", path]
                lines.append("game " + " ".join(args))
        lines.append(f"at {end_tick if end_tick is not None else row['tick']}")
        return "\n".join(lines) + "\n"

    def _lua_file(self, text):
        import hashlib
        os.makedirs(self.files, exist_ok=True)
        path = os.path.join(self.files, hashlib.sha1(text.encode()).hexdigest()[:12] + ".lua")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
        return path

    def goal_text(self):
        """The round's manifest: each item, made so far of the total to have made."""
        ep = self.episode
        if ep is None:
            return "no round"
        return manifest_text(ep["goal"], ep.get("made") or {}, float(self.settings["base"]))
