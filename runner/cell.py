"""Go-Explore cells: what the force has produced, at log resolution.

Adapted from factorio-playable's toolkit (fkcore/explore.py: LUA_MADE, cell, key, levels_of,
distance, reached). A cell is {item: floor(log_base(count produced))} over every item produced at
least once. Stdlib only.
"""
from __future__ import annotations

import math
import random

#: production totals, what the force can make now, research done
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
local done = 0
for _, t in pairs(f.technologies) do if t.researched then done = done + 1 end end
local silos = 0
for _, s in pairs(game.surfaces) do
  silos = silos + s.count_entities_filtered{name = 'rocket-silo', force = 'player'}
end
rcon.print(helpers.table_to_json({tick = game.tick, made = made, can = list, researched = done,
                                  silos = silos, rockets = f.rockets_launched or 0}))
"""


def cell(made: dict, base: float = 2.0) -> dict:
    out = {}
    for item, n in made.items():
        n = float(n)
        if n >= 1:
            out[item] = int(math.floor(math.log(n, base) + 1e-9))
    return out


def key(levels: dict) -> str:
    return ",".join(f"{i}={levels[i]}" for i in sorted(levels))


def levels_of(k: str) -> dict:
    return {p.split("=")[0]: int(p.split("=")[1]) for p in k.split(",") if p}


def distance(a: dict, b: dict) -> int:
    return sum(abs(a.get(i, -1) - b.get(i, -1)) for i in set(a) | set(b))


def reached(now: dict, goal: dict) -> bool:
    return all(now.get(i, -1) >= lvl for i, lvl in goal.items())


def shortfall(now: dict, goal: dict) -> dict:
    return {i: lvl for i, lvl in goal.items() if now.get(i, -1) < lvl}


def progress(levels: dict) -> int:
    """A cell's height: the sum of (level + 1) over items made. Crude, monotone in production."""
    return sum(lvl + 1 for lvl in levels.values())


def make_goal(levels: dict, can: list, dist: int, rng: random.Random,
              skip: tuple = ()) -> dict:
    """`dist` steps of +1 level from `levels`, each on an item drawn uniformly from those made or
    makeable. -> {item: goal level} for the items that moved."""
    pool = sorted((set(levels) | set(can)) - set(skip))
    goal = dict(levels)
    moved = {}
    for _ in range(dist):
        if not pool:
            break
        item = rng.choice(pool)
        goal[item] = goal.get(item, -1) + 1
        moved[item] = goal[item]
    return moved


def count_for(level: int, base: float = 2.0) -> int:
    """The production count that first reaches `level`."""
    return int(math.ceil(base ** level)) if level >= 0 else 0
