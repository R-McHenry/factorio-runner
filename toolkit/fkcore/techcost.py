"""fkcore.techcost: what an item costs in science to unlock, read from the game's own tech tree
(2.0.66).

Rory, 2026-10-29: "a tech tree scanner that can generically pick up initial cost behind stuff like
fluoroketone. So basically we would calculate the total cost in science to unlock it."

**Unlocking an item** means having a way to make it. That is the cheapest of:
- **a recipe** that makes it, enabled or unlocked by a technology, with each of its ingredients
  unlocked too (so a barrel of fluoroketone costs what fluoroketone costs);
- **a resource** that yields it, mined or pumped, on Nauvis or on a planet a technology unlocks.

**A technology** costs its own packs (units x ingredients) and those of every prerequisite not yet
researched. A trigger technology costs what its trigger item costs to make. Infinite and repeating
technologies are left out.

The cost is a **set of technologies**, and its packs are their sum: shared prerequisites count
once. `Tree.need(item, researched)` -> {pack: count}, or None when nothing makes it.

The tree comes from the game (``LUA_DUMP``, a prototype read that changes nothing), so it follows
whatever mods the game runs: Space Age's planets included. Stdlib only."""
from __future__ import annotations

import json

__all__ = ["LUA_DUMP", "Tree"]

#: the tech tree, the recipes and where the raw resources are, from the game's prototypes; written
#: to ``fk5/<tag>.json`` (the reply is 1)
LUA_DUMP = """
local techs, recipes, res, planets = {}, {}, {}, {}
for name, t in pairs(prototypes.technology) do
  local row = {pre = {}, packs = {}, unlocks = {}, places = {}, researched = false}
  for p in pairs(t.prerequisites) do row.pre[#row.pre + 1] = p end
  if t.research_unit_count_formula == nil or t.max_level == 1 then
    for _, i in pairs(t.research_unit_ingredients or {}) do
      row.packs[i.name] = (row.packs[i.name] or 0) + i.amount * (t.research_unit_count or 0)
    end
  else
    row.infinite = true
  end
  for _, e in pairs(t.effects or {}) do
    if e.type == 'unlock-recipe' then row.unlocks[#row.unlocks + 1] = e.recipe end
    if e.type == 'unlock-space-location' then row.places[#row.places + 1] = e.space_location end
  end
  local tr = t.research_trigger
  if tr then
    row.trigger = tr.type
    if tr.item then row.trigger_item = type(tr.item) == 'table' and tr.item.name or tr.item end
    if tr.fluid then row.trigger_item = type(tr.fluid) == 'table' and tr.fluid.name or tr.fluid end
    if tr.entity then row.trigger_item = type(tr.entity) == 'table' and tr.entity.name or tr.entity end
  end
  row.researched = game.forces['player'].technologies[name].researched
  techs[name] = row
end
for name, r in pairs(prototypes.recipe) do
  if not r.hidden and r.category ~= 'recycling' and r.category ~= 'parameters' then
    local row = {ins = {}, outs = {}, ina = {}, outa = {},
                 enabled = game.forces['player'].recipes[name].enabled, default = r.enabled}
    for _, i in pairs(r.ingredients) do
      row.ins[#row.ins + 1] = i.name
      row.ina[i.name] = (row.ina[i.name] or 0) + i.amount
    end
    for _, p in pairs(r.products) do
      row.outs[#row.outs + 1] = p.name
      local n = p.amount or ((p.amount_min or 0) + (p.amount_max or 0)) / 2
      row.outa[p.name] = (row.outa[p.name] or 0) + n * (p.probability or 1)
    end
    recipes[name] = row
  end
end
local function yields(e)
  local outs = {}
  local ok, m = pcall(function() return e.mineable_properties end)
  if ok and m and m.products then for _, p in pairs(m.products) do outs[#outs + 1] = p.name end end
  return outs
end
for name, e in pairs(prototypes.get_entity_filtered{{filter = 'type', type = 'resource'}}) do
  res[name] = yields(e)
end
-- what grows or stands on a planet (Gleba's plants, rocks) yields what mining it gives
for _, p in pairs(prototypes.space_location) do
  local mg = p.map_gen_settings
  local ap = mg and mg.autoplace_settings
  if ap and ap.entity then
    for n in pairs(ap.entity.settings or {}) do
      local e = prototypes.entity[n]
      if e and not res[n] then res[n] = yields(e) end
    end
  end
end
-- trees and plants are placed by an autoplace control a planet's map has (trees, gleba_plants)
local by_control = {}
for _, ty in pairs({'tree', 'plant', 'simple-entity', 'fish'}) do
  for name, e in pairs(prototypes.get_entity_filtered{{filter = 'type', type = ty}}) do
    local ok, c = pcall(function() return e.autoplace_specification and e.autoplace_specification.control end)
    if ok and c then
      by_control[c] = by_control[c] or {}
      table.insert(by_control[c], name)
      if not res[name] then res[name] = yields(e) end
    end
  end
end
-- space: an asteroid collector gathers the chunks
local chunks, collectors = {}, {}
for name, c in pairs(prototypes.asteroid_chunk) do
  local ok, m = pcall(function() return c.mineable_properties end)
  if ok and m and m.products then for _, p in pairs(m.products) do chunks[#chunks + 1] = p.name end end
end
local turns = {}
for name, it in pairs(prototypes.item) do
  local pr = it.place_result
  if pr and pr.type == 'asteroid-collector' then collectors[#collectors + 1] = name end
  if it.spoil_result then turns[#turns + 1] = {name, it.spoil_result.name} end
  if it.burnt_result then turns[#turns + 1] = {name, it.burnt_result.name} end
end
local tiles = {}
for name, t in pairs(prototypes.tile) do
  local ok, f = pcall(function() return t.fluid end)
  if ok and f then tiles[name] = f.name end
end
for name, p in pairs(prototypes.space_location) do
  local row = {entities = {}, tiles = {}}
  local mg = p.map_gen_settings
  local ap = mg and mg.autoplace_settings
  if ap and ap.entity then for n in pairs(ap.entity.settings or {}) do row.entities[#row.entities + 1] = n end end
  if ap and ap.tile then for n in pairs(ap.tile.settings or {}) do row.tiles[#row.tiles + 1] = n end end
  for c in pairs(mg and mg.autoplace_controls or {}) do
    for _, n in pairs(by_control[c] or {}) do row.entities[#row.entities + 1] = n end
  end
  planets[name] = row
end
local fluids = {}
for name in pairs(prototypes.fluid) do fluids[#fluids + 1] = name end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({techs = techs, recipes = recipes,
  resources = res, tile_fluids = tiles, planets = planets, chunks = chunks,
  collectors = collectors, turns = turns, fluids = fluids}), false)
rcon.print(1)
"""

#: where the game starts: its resources cost nothing
HOME = "nauvis"


class Tree:
    """The tech tree of one game, as ``LUA_DUMP`` read it."""

    def __init__(self, data):
        self.techs = data.get("techs") or {}
        self.recipes = data.get("recipes") or {}
        self.resources = data.get("resources") or {}
        self.tile_fluids = data.get("tile_fluids") or {}
        self.planets = data.get("planets") or {}
        lst = lambda v: list(v.values()) if isinstance(v, dict) else list(v or ())   # noqa: E731
        self.chunks = lst(data.get("chunks"))           # what asteroid collectors gather
        self.collectors = lst(data.get("collectors"))   # the items that place one
        self.turns = [tuple(lst(t)) for t in lst(data.get("turns"))]   # (item, what it becomes)
        self.fluids = set(lst(data.get("fluids")))
        for d in (self.techs, self.recipes, self.resources, self.tile_fluids, self.planets):
            for k, v in list(d.items()):
                if isinstance(v, dict):
                    for f in ("pre", "unlocks", "places", "ins", "outs", "entities", "tiles"):
                        if isinstance(v.get(f), dict):          # an empty Lua table is {} in JSON
                            v[f] = list(v[f].values())
                    if isinstance(v.get("packs"), list):
                        v["packs"] = {}
                elif isinstance(v, list) and not v:
                    d[k] = []
        self._unlocked_by = {}
        self._place_tech = {}
        for name, t in self.techs.items():
            if t.get("infinite"):
                continue
            for r in t.get("unlocks", ()):
                self._unlocked_by.setdefault(r, []).append(name)
            for p in t.get("places", ()):
                self._place_tech.setdefault(p, []).append(name)
        self._makers = {}
        for name, r in self.recipes.items():
            for o in r.get("outs", ()):
                self._makers.setdefault(o, []).append(name)
        self._sources = {}                      # item/fluid -> [planets it is mined or pumped on]
        for planet, row in self.planets.items():
            for e in row.get("entities", ()):
                for o in self.resources.get(e, ()):
                    self._sources.setdefault(o, set()).add(planet)
            for t in row.get("tiles", ()):
                if t in self.tile_fluids:
                    self._sources.setdefault(self.tile_fluids[t], set()).add(planet)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as fh:
            return cls(json.load(fh))

    # -- the cost ---------------------------------------------------------------------------------
    def items(self):
        """Every item and fluid something makes or yields."""
        return sorted(set(self._makers) | set(self._sources))

    def closure(self, tech, researched):
        """The technologies ``tech`` needs, itself included, that ``researched`` lacks."""
        out, todo = set(), [tech]
        while todo:
            t = todo.pop()
            if t in out or t in researched or t not in self.techs:
                continue
            out.add(t)
            todo += self.techs[t].get("pre", ())
        return out

    def packs(self, techs):
        out = {}
        for t in techs:
            for p, n in (self.techs[t].get("packs") or {}).items():
                out[p] = out.get(p, 0) + n
        return out

    def _size(self, techs):
        return sum(self.packs(techs).values())

    def costs(self, researched):
        """{item: frozenset of technologies} for every item something can make, given the
        technologies ``researched``: the cheapest way, by packs, found by relaxing to a fixed
        point (a cycle, a barrel filled and emptied, never makes a way cheaper)."""
        researched = set(researched)
        best = {}
        for item, planets in self._sources.items():
            if HOME in planets:
                best[item] = frozenset()
            else:
                for planet in planets:
                    for t in self._place_tech.get(planet, ()):
                        c = frozenset(self.closure(t, researched))
                        if item not in best or self._size(c) < self._size(best[item]):
                            best[item] = c
        tech_cost = {}

        def tech(t):
            if t in tech_cost:
                return tech_cost[t]
            c = set(self.closure(t, researched))
            # a trigger technology (craft an item, mine a resource) costs that item too
            for u in list(c):
                trig = self.techs[u].get("trigger_item")
                if not trig:
                    continue
                if trig in self.resources:          # mine one: what it yields
                    trig = next(iter(self.resources[trig]), None)
                if trig in best:
                    c |= best[trig]
                elif trig in self._makers or trig in self._sources:
                    return None                     # makeable, not reachable yet
                # else an entity no recipe names (a hub, a spawner): no cost of its own
            tech_cost[t] = frozenset(c)
            return tech_cost[t]

        def offer(item, c):
            if item not in best or self._size(c) < self._size(best[item]):
                best[item] = c
                return True
            return False

        changed = True
        while changed:
            changed = False
            tech_cost.clear()
            # space: the chunks, once an asteroid collector can be made
            ways = [best[c] for c in self.collectors if c in best]
            if ways:
                way = min(ways, key=self._size)
                for ch in self.chunks:
                    changed |= offer(ch, way)
            # an item that spoils or burns out becomes another
            for a, b in self.turns:
                if a in best:
                    changed |= offer(b, best[a])
            for name, r in self.recipes.items():
                if r.get("default") or any(t in researched
                                           for t in self._unlocked_by.get(name, ())):
                    need = set()
                else:
                    ways = [tech(t) for t in self._unlocked_by.get(name, ())]
                    ways = [w for w in ways if w is not None]
                    if not ways:
                        continue
                    need = set(min(ways, key=self._size))
                ok = True
                outs = set(r.get("outs", ()))
                for i in r.get("ins", ()):
                    if i in outs:
                        # it breeds or catalyses it (pentapod eggs, Kovarex): the game seeds the
                        # first ones elsewhere (loot, a stock), so it waits on nothing here
                        continue
                    if i not in best:
                        ok = False
                        break
                    need |= best[i]
                if not ok:
                    continue
                c = frozenset(need)
                for o in r.get("outs", ()):
                    if o not in best or self._size(c) < self._size(best[o]):
                        best[o] = c
                        changed = True
        return best

    def researched(self):
        """The technologies the dump found researched."""
        return {n for n, t in self.techs.items() if t.get("researched")}

    # -- worth (2.0.67) ---------------------------------------------------------------------------
    #: what a unit of a raw thing is worth: an ore, a chunk, a fruit, and a unit of fluid
    RAW, RAW_FLUID = 1.0, 0.1

    def values(self):
        """{item: its worth in raw units}: a raw thing (mined, pumped, grown, gathered) is RAW (a
        fluid RAW_FLUID); anything made is the cheapest recipe's ingredients over its products,
        net of what the recipe gives back (a catalyst), its by-products at the same unit worth.
        Found by relaxing to a fixed point; something nothing reaches has none."""
        if getattr(self, "_values", None) is not None:
            return self._values
        val, frozen = {}, set()
        raw = lambda item: self.RAW_FLUID if item in self.fluids else self.RAW   # noqa: E731
        # first from home: what Nauvis yields, and everything made of it. Then the rest from
        # off-world raws and asteroid chunks, never cheapening what home already priced: an ore
        # crushed from asteroids or a plate cast from lava is cheaper in raw units, and pricing
        # by it made every early count worth almost nothing
        for item, planets in self._sources.items():
            if HOME in planets:
                val[item] = raw(item)
        self._relax(val, frozen)
        frozen = set(val)
        for item in self._sources:
            val.setdefault(item, raw(item))
        for c in self.chunks:
            val.setdefault(c, self.RAW)
        self._relax(val, frozen)
        self._values = val
        return val

    def _relax(self, val, frozen):
        for _ in range(60):
            changed = False
            for r in self.recipes.values():
                ina = r.get("ina") if isinstance(r.get("ina"), dict) else {}
                outa = r.get("outa") if isinstance(r.get("outa"), dict) else {}
                if not outa:
                    continue
                net_in = {i: a - outa.get(i, 0) for i, a in ina.items() if a - outa.get(i, 0) > 0}
                net_out = {o: a - ina.get(o, 0) for o, a in outa.items() if a - ina.get(o, 0) > 0}
                if not net_out or any(i not in val for i in net_in):
                    continue
                each = sum(a * val[i] for i, a in net_in.items()) / sum(net_out.values())
                for o in net_out:
                    if o not in frozen and (o not in val or each < val[o] - 1e-12):
                        val[o] = each
                        changed = True
            for a, b in self.turns:
                if a in val and b not in frozen and (b not in val or val[a] < val[b] - 1e-12):
                    val[b] = val[a]
                    changed = True
            if not changed:
                break

    def unlock_packs(self):
        """{item: frozenset of technologies} from nothing researched: the research making it
        implies (``Tree.packs`` of it is its science)."""
        if getattr(self, "_fresh", None) is None:
            self._fresh = self.costs(set())
        return self._fresh
