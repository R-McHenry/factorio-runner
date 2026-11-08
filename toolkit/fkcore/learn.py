"""fkcore.learn: ``fk catalog learn``, a machine read from the running game into the run's overlay.

``fk catalog learn foundry electromagnetic-plant`` (entity or item names), ``fk catalog learn
--mod space-age`` (every building that mod adds) or ``fk catalog learn --all-mods`` (every
building no base-game file made) reads the game's prototypes of those buildings and writes what
the toolkit plans with into ``learned.json`` beside the design (:mod:`fkcore.learned`), on top of
what earlier learns put there:

- the catalog row: type, size, collision box, crafting speed, the items that place it, energy,
  circuit wire reach, and per type a belt's speed, an underground's reach, a drill's mining speed
  and radius, a pole's supply and wire reach, an inserter's speeds, module slots;
- its fluid boxes, as ``fluidboxes.json`` holds them: each box's connections per direction (a
  recipe's live boxes come from the catalog's recipe rule, :meth:`Catalog.fluid_boxes_at`);
- its facts (``facts.json``'s row): energy in W, drain, burner and fuel categories, crafting
  categories, module slots and allowed effects, a built-in effect (a foundry's +50%
  productivity), and where it puts its output down itself (``drop_vector``);
- the recipes it can craft (every recipe of its crafting categories), with their allowed effects;
  the fluids they use; the resources a drill mines; the fuels a burner takes.

Reads only: prototypes, never an entity or a surface (the toolkit's one game write is a named
paste), one game call per 40 buildings. What only a built entity shows is taken from the base
catalog's rows of the same type: its circuit wire connector ids (``connectors: by type``). Each
call stays within the link's time. The design steps then plan with it like any machine:
``Machine<entity=foundry, recipe=casting-iron-gear-wheel>``, or wrapped as a Literal.

``fk catalog show [NAME]`` says what the overlay holds, or what the catalog knows of NAME and
where from; ``fk catalog forget NAME`` takes NAME out of the overlay. Stdlib only.
"""
from __future__ import annotations

import json
import os
from collections import Counter

from . import learned as learnedmod

__all__ = ["lua_select", "lua_entities", "lua_recipes", "rows", "learn", "show", "forget"]

#: buildings read in one game call
BATCH = 40
#: J a tick -> W
W = 60


def _q(obj):
    return json.dumps(json.dumps(obj))


#: Lua shared by every call: a guarded read, a set's keys
_PRE = """
local function g(o, k) local ok, v = pcall(function() return o[k] end) if ok then return v end end
local function m(o, k, ...) local a = {...} local ok, v = pcall(function() return o[k](table.unpack(a)) end)
  if ok and not (type(v) == 'number' and (v ~= v or v == math.huge or v == -math.huge)) then return v end end
local function keys(t) local out = {} if type(t) == 'table' then for k, v in pairs(t) do
  if v then out[#out + 1] = type(k) == 'number' and v or k end end end table.sort(out) return out end
local P = helpers.json_to_table(%s)
"""


def lua_select(names, mod=None, all_mods=False, tag="learn"):
    """Lua: the placeable buildings to learn (names, or what ``mod`` / any mod created) into
    ``fk5/<tag>.json``: {want: [name], unknown: [name], mods: [..], version}."""
    p = {"names": list(names), "mod": mod, "all": bool(all_mods)}
    return _PRE % _q(p) + """
local want, seen, unknown = {}, {}, {}
local function add(n) if not seen[n] then seen[n] = true want[#want + 1] = n end end
local function placeable(p) local it = g(p, 'items_to_place_this') return it and #it > 0 end
for _, n in pairs(P.names) do
  local p = prototypes.entity[n]
  if not p and prototypes.item[n] and prototypes.item[n].place_result then p = prototypes.item[n].place_result end
  if p and placeable(p) then add(p.name) else unknown[#unknown + 1] = n end
end
if P.mod or P.all then
  for name, p in pairs(prototypes.entity) do
    if placeable(p) then
      local h = m(prototypes, 'get_history', p.type, name)
      local by = h and h.created
      if (P.mod and by == P.mod) or (P.all and by and by ~= 'base' and by ~= 'core') then add(name) end
    end
  end
end
table.sort(want)
local mods = {}
for k, v in pairs(script.active_mods) do mods[#mods + 1] = k .. ' ' .. v end
table.sort(mods)
helpers.write_file('fk5/""" + tag + """.json', helpers.table_to_json({want = want, unknown = unknown,
  mods = mods, version = script.active_mods.base}), false)
rcon.print(#want)
"""


def lua_entities(names, tag="learn"):
    """Lua: each named prototype's fields (energy, sizes, speeds, fluid boxes, effects) into
    ``fk5/<tag>.json``: {rows: [..]}. Prototypes only."""
    return _PRE % _q({"names": list(names)}) + """
local rows = {}
for _, name in pairs(P.names) do
  local p = prototypes.entity[name]
  local cb = p.collision_box
  local r = {name = name, type = p.type, w = p.tile_width, h = p.tile_height,
    box = {cb.left_top.x, cb.left_top.y, cb.right_bottom.x, cb.right_bottom.y}, placed_by = {}}
  for _, it in pairs(g(p, 'items_to_place_this') or {}) do r.placed_by[#r.placed_by + 1] = it.name end
  local e = {}
  local es = g(p, 'electric_energy_source_prototype')
  local bp = g(p, 'burner_prototype')
  if es and not bp then e.source = 'electric' e.priority = g(es, 'usage_priority') e.drain = g(es, 'drain')
    e.buffer = g(es, 'buffer_capacity')
  elseif bp then e.source = 'burner' e.fuel_categories = keys(g(bp, 'fuel_categories')) e.effectivity = g(bp, 'effectivity')
  elseif g(p, 'heat_energy_source_prototype') then e.source = 'heat'
  elseif g(p, 'fluid_energy_source_prototype') then e.source = 'fluid'
  elseif g(p, 'void_energy_source_prototype') then e.source = 'void' end
  e.usage = g(p, 'energy_usage') e.max_usage = m(p, 'get_max_energy_usage')
  e.max_production = m(p, 'get_max_energy_production') e.power_output = m(p, 'get_max_power_output')
  r.energy = e
  r.wire_reach = m(p, 'get_max_circuit_wire_distance')
  r.crafting_speed = m(p, 'get_crafting_speed')
  if g(p, 'crafting_categories') then r.crafting_categories = keys(p.crafting_categories) end
  r.mining_speed = g(p, 'mining_speed')
  r.mining_radius = m(p, 'get_mining_drill_radius') or g(p, 'mining_drill_radius')
  if g(p, 'resource_categories') then r.resource_categories = keys(p.resource_categories) end
  local dv = g(p, 'vector_to_place_result') if dv then r.drop_vector = {dv.x or dv[1], dv.y or dv[2]} end
  r.belt_speed = g(p, 'belt_speed') r.max_distance = g(p, 'max_underground_distance')
  if p.type == 'electric-pole' then r.supply = m(p, 'get_supply_area_distance') or g(p, 'supply_area_distance')
    r.wire = m(p, 'get_max_wire_distance') or g(p, 'max_wire_distance') end
  if p.type == 'inserter' then r.rotation_speed = m(p, 'get_inserter_rotation_speed') or g(p, 'inserter_rotation_speed')
    r.extension_speed = m(p, 'get_inserter_extension_speed') or g(p, 'inserter_extension_speed')
    r.bulk = g(p, 'bulk') r.stack_size_bonus = g(p, 'inserter_stack_size_bonus') r.filter_count = g(p, 'filter_count') end
  if p.type == 'roboport' then r.logistic_radius = g(p, 'logistic_radius') r.construction_radius = g(p, 'construction_radius') end
  r.module_slots = g(p, 'module_inventory_size')
  if g(p, 'allowed_effects') then r.allowed_effects = keys(p.allowed_effects) end
  local er = g(p, 'effect_receiver')
  if er and er.base_effect and next(er.base_effect) then r.base_effect = er.base_effect end
  r.target_temperature = g(p, 'target_temperature') r.fluid_usage_per_tick = g(p, 'fluid_usage_per_tick')
  r.neighbour_bonus = g(p, 'neighbour_bonus')
  r.researching_speed = g(p, 'researching_speed')
  r.boxes_off = g(p, 'fluid_boxes_off_when_no_fluid_recipe')
  local fbs = g(p, 'fluidbox_prototypes') or {}
  if #fbs > 0 then
    local boxes = {}
    for _, fb in pairs(fbs) do
      local b = {index = fb.index, production = fb.production_type, connections = {}}
      if fb.filter then b.filter = fb.filter.name end
      for _, c in pairs(fb.pipe_connections) do
        local at = {}
        for k = 1, 4 do local pos = c.positions and c.positions[k] at[k] = pos and {pos.x, pos.y} or {0, 0} end
        local cats = g(c, 'connection_category')
        if type(cats) == 'string' then cats = {cats} end
        if cats and #cats == 1 and cats[1] == 'default' then cats = nil end
        b.connections[#b.connections + 1] = {at = at, dir = c.direction or 0, flow = c.flow_direction,
          type = c.connection_type, max_distance = (c.max_underground_distance or 0) ~= 0 and c.max_underground_distance or nil,
          categories = cats}
      end
      boxes[#boxes + 1] = b
    end
    table.sort(boxes, function(a, b) return a.index < b.index end)
    r.boxes = boxes
    for _, rule in pairs(g(p, 'tile_buildability_rules') or {}) do
      local req = rule.required_tiles and rule.required_tiles.layers
      if req and req.water_tile then local a = rule.area
        r.water_area = {a.left_top.x, a.left_top.y, a.right_bottom.x, a.right_bottom.y} end
    end
  end
  rows[#rows + 1] = r
end
helpers.write_file('fk5/""" + tag + """.json', helpers.table_to_json({rows = rows}), false)
rcon.print(#rows)
"""


def lua_recipes(categories, products=(), mod=None, all_mods=False, resource_cats=(),
                fuel_cats=(), tag="learn"):
    """Lua: the recipes of ``categories`` (and those making ``products``, and those ``mod`` / any
    mod created), the fluids they use, the resources of ``resource_cats`` and the fuels of
    ``fuel_cats``, into ``fk5/<tag>.json``."""
    p = {"cats": list(categories), "products": list(products), "mod": mod, "all": bool(all_mods),
         "res": list(resource_cats), "fuel": list(fuel_cats)}
    return _PRE % _q(p) + """
local function set(t) local s = {} for _, v in pairs(t or {}) do s[v] = true end return s end
local cats, makes, rc, fc = set(P.cats), set(P.products), set(P.res), set(P.fuel)
local function stack(list)
  local out = {}
  for _, x in pairs(list or {}) do
    local row = {name = x.name, type = x.type, amount = x.amount}
    if x.amount_min then row.amount_min = x.amount_min row.amount_max = x.amount_max end
    if x.probability and x.probability ~= 1 then row.probability = x.probability end
    if x.extra_count_fraction and x.extra_count_fraction ~= 0 then row.extra_count_fraction = x.extra_count_fraction end
    if x.fluidbox_index and x.fluidbox_index ~= 0 then row.fluidbox_index = x.fluidbox_index end
    if x.temperature then row.temperature = x.temperature end
    out[#out + 1] = row
  end
  return out
end
local recipes, fluids = {}, {}
local force = game.forces.player
for name, r in pairs(prototypes.recipe) do
  if not g(r, 'parameter') then
    local take = cats[r.category]
    if not take then for _, x in pairs(r.products or {}) do if makes[x.name] then take = true end end end
    if not take and (P.mod or P.all) then
      local h = m(prototypes, 'get_history', 'recipe', name)
      local by = h and h.created
      take = (P.mod and by == P.mod) or (P.all and by and by ~= 'base' and by ~= 'core')
    end
    if take then
      local row = {name = name, category = r.category, energy = r.energy,
        enabled = force.recipes[name] and force.recipes[name].enabled or false, hidden = r.hidden or nil,
        ingredients = stack(r.ingredients), products = stack(r.products),
        effects = keys(g(r, 'allowed_effects')), max_productivity = g(r, 'maximum_productivity')}
      recipes[#recipes + 1] = row
      for _, x in pairs(row.ingredients) do if x.type == 'fluid' then fluids[x.name] = true end end
      for _, x in pairs(row.products) do if x.type == 'fluid' then fluids[x.name] = true end end
    end
  end
end
table.sort(recipes, function(a, b) return a.name < b.name end)
local fl = {}
for n in pairs(fluids) do local p = prototypes.fluid[n]
  if p then fl[n] = {heat_capacity = p.heat_capacity, default_temperature = p.default_temperature,
    max_temperature = p.max_temperature, fuel_value = p.fuel_value} end end
local res = {}
for name, p in pairs(prototypes.get_entity_filtered{{filter = 'type', type = 'resource'}}) do
  if rc[g(p, 'resource_category') or 'basic-solid'] then
    local mp = p.mineable_properties
    local out = {}
    for _, x in pairs(mp.products or {}) do out[#out + 1] = {name = x.name, type = x.type} end
    res[name] = {mining_time = mp.mining_time, category = g(p, 'resource_category'), infinite = g(p, 'infinite_resource'),
      required_fluid = mp.required_fluid, fluid_amount = mp.fluid_amount, results = out}
  end
end
local fuels = {}
for name, p in pairs(prototypes.item) do
  local fv = g(p, 'fuel_value')
  if fv and fv > 0 and fc[g(p, 'fuel_category') or ''] then
    fuels[name] = {fuel_value = fv, category = p.fuel_category, burnt_result = g(p, 'burnt_result') and p.burnt_result.name or nil}
  end
end
helpers.write_file('fk5/""" + tag + """.json', helpers.table_to_json({recipes = recipes, fluids = fl,
  resources = res, fuels = fuels}), false)
rcon.print(#recipes)
"""


# -- the game's answers as the toolkit's rows -------------------------------------------------------
def _clean(v):
    if isinstance(v, float):
        v = float(f"{v:.8g}")                      # a float32's digits, as the fixture has them
        return int(v) if v.is_integer() else v
    return v


def _list(v):
    """Lua's JSON writes an empty table as {}."""
    if isinstance(v, dict):
        return [] if not v else [v[k] for k in sorted(v, key=lambda k: int(k) if str(k).isdigit()
                                                         else k)]
    return list(v or ())


def _connectors(base, kind):
    """The circuit wire connector ids a building of ``kind`` has: the base catalog's most common
    for that type (prototypes don't say; only a built entity does)."""
    seen = Counter(tuple((r.get("circuit") or {}).get("connectors") or ())
                   for r in base._entities.values() if r.get("type") == kind)
    return list(seen.most_common(1)[0][0]) if seen else []


def rows(raw, base):
    """One read building -> (catalog row, fluid-box row or None, facts row)."""
    en = raw.get("energy") or {}
    use = en.get("usage") if en.get("usage") else en.get("max_usage")
    cat = {"name": raw["name"], "type": raw["type"], "tile_width": raw["w"],
           "tile_height": raw["h"], "collision_box": [_clean(v) for v in _list(raw["box"])],
           "placed_by": _list(raw.get("placed_by"))}
    facts = {"type": raw["type"]}
    energy = {}
    if en.get("source"):
        energy["source"] = facts["energy_source"] = en["source"]
    if use:
        energy["usage_w"] = facts["energy_w"] = _clean(use * W)
    if en.get("source") == "electric":
        energy["drain_w"] = facts["drain_w"] = _clean((en.get("drain") or 0) * W)
        energy["priority"] = facts["priority"] = en.get("priority")
        if en.get("buffer"):
            facts["buffer_j"] = _clean(en["buffer"])
    if en.get("source") == "burner":
        facts["burner"] = True
        energy["fuel_categories"] = facts["fuel_categories"] = _list(en.get("fuel_categories"))
        facts["effectivity"] = _clean(en.get("effectivity"))
    out_w = en.get("power_output") or en.get("max_production")
    if out_w:
        energy["output_w"] = facts["power_output_w"] = _clean(out_w * W)
    cat["energy"] = energy
    reach = raw.get("wire_reach")
    cat["circuit"] = {"connectors": _connectors(base, raw["type"])}
    if reach:
        cat["circuit"]["wire_reach"] = _clean(reach)
    for k_raw, k_cat in (("crafting_speed", "crafting_speed"), ("belt_speed", "belt_speed"),
                         ("mining_speed", "mining_speed"), ("rotation_speed", "rotation_speed"),
                         ("extension_speed", "extension_speed"), ("supply", "supply_area_distance"),
                         ("wire", "max_wire_distance"), ("logistic_radius", "logistic_radius"),
                         ("construction_radius", "construction_radius"),
                         ("module_slots", "module_slots")):
        if raw.get(k_raw) is not None and not (k_raw == "module_slots" and not raw[k_raw]):
            cat[k_cat] = _clean(raw[k_raw])
    if raw["type"] == "inserter":
        cat["bulk"] = bool(raw.get("bulk"))
        cat["stack_size_bonus"] = raw.get("stack_size_bonus") or 0
        cat["filter_count"] = raw.get("filter_count") or 0
    if raw.get("max_distance"):
        # a pipe-to-ground's measured pair span is one less than the prototype's (2.0.77)
        cat["max_distance"] = raw["max_distance"] - (1 if raw["type"] == "pipe-to-ground" else 0)
    if raw.get("resource_categories") is not None:
        cat["resource_categories"] = facts["resource_categories"] = _list(raw["resource_categories"])
    for k in ("crafting_speed", "mining_speed", "researching_speed", "target_temperature",
              "neighbour_bonus"):
        if raw.get(k) is not None:
            facts[k] = _clean(raw[k])
    if raw.get("mining_radius") is not None:
        facts["mining_drill_radius"] = _clean(raw["mining_radius"])
    if raw.get("crafting_categories") is not None:
        facts["crafting_categories"] = _list(raw["crafting_categories"])
    if raw.get("module_slots"):
        facts["module_slots"] = raw["module_slots"]
        facts["allowed_effects"] = _list(raw.get("allowed_effects"))
    if raw.get("base_effect"):
        facts["base_effect"] = {k: _clean(v) for k, v in sorted(raw["base_effect"].items())}
    if raw.get("fluid_usage_per_tick"):
        facts["fluid_per_s"] = _clean(raw["fluid_usage_per_tick"] * 60)
    if raw.get("drop_vector"):
        dv = [_clean(v) for v in _list(raw["drop_vector"])]
        if any(dv):
            facts["drop_vector"] = dv
    fb = None
    if raw.get("boxes"):
        boxes = []
        for b in _list(raw["boxes"]):
            conns = []
            for c in _list(b.get("connections")):
                row = {"at": [[_clean(v) for v in _list(a)] for a in _list(c["at"])],
                       "dir": c.get("dir") or 0, "flow": c.get("flow"), "type": c.get("type")}
                if c.get("max_distance"):
                    row["max_distance"] = c["max_distance"] - (
                        1 if raw["type"] == "pipe-to-ground" else 0)
                if c.get("categories"):
                    row["categories"] = _list(c["categories"])
                conns.append(row)
            box = {"index": b["index"], "production": b.get("production"), "connections": conns}
            if b.get("filter"):
                box["filter"] = b["filter"]
            boxes.append(box)
        fb = {"type": raw["type"], "size": [raw["w"], raw["h"]], "boxes": boxes}
        if raw.get("boxes_off") and raw.get("crafting_categories") is not None:
            fb["boxes_need_fluid_recipe"] = True
        if raw.get("water_area"):
            fb["water_area"] = [_clean(v) for v in _list(raw["water_area"])]
    return cat, fb, facts


def _recipe_rows(got):
    """The recipes read -> (catalog rows, facts rows)."""
    cat, facts = [], {}
    for r in _list(got.get("recipes")):
        row = {"name": r["name"], "category": r["category"], "energy": _clean(r["energy"]),
               "enabled": bool(r.get("enabled")),
               "ingredients": [_stack(x) for x in _list(r.get("ingredients"))],
               "products": [_stack(x) for x in _list(r.get("products"))]}
        if r.get("hidden"):
            row["hidden"] = True
        cat.append(row)
        facts[r["name"]] = {"effects": _list(r.get("effects"))}
        if r.get("max_productivity") is not None:
            facts[r["name"]]["max_productivity"] = _clean(r["max_productivity"])
    return cat, facts


def _stack(x):
    out = {k: _clean(v) for k, v in x.items()}
    out.setdefault("type", "item")
    return out


def _things(got):
    """The fluids, resources and fuels read -> facts sections."""
    fluids = {n: {"heat_capacity_j": _clean(f.get("heat_capacity")),
                  "default_temperature": _clean(f.get("default_temperature")),
                  "max_temperature": _clean(f.get("max_temperature"))}
              for n, f in sorted((got.get("fluids") or {}).items())}
    for n, f in (got.get("fluids") or {}).items():
        if f.get("fuel_value"):
            fluids[n]["fuel_value_j"] = _clean(f["fuel_value"])
    res = {}
    for n, r in sorted((got.get("resources") or {}).items()):
        results = _list(r.get("results"))
        row = {"mining_time": _clean(r["mining_time"]), "category": r.get("category"),
               "results": [x["name"] for x in results],
               "fluid_results": any(x.get("type") == "fluid" for x in results)}
        if r.get("infinite"):
            row["infinite"] = True
        if r.get("required_fluid"):
            row["required_fluid"] = r["required_fluid"]
            row["fluid_amount"] = _clean(r.get("fluid_amount"))
        res[n] = row
    fuels = {}
    for n, f in sorted((got.get("fuels") or {}).items()):
        fuels[n] = {"fuel_value_j": _clean(f["fuel_value"]), "category": f.get("category")}
        if f.get("burnt_result"):
            fuels[n]["burnt_result"] = f["burnt_result"]
    return fluids, res, fuels


# -- the command ------------------------------------------------------------------------------------
def learn(r, names=(), mod=None, all_mods=False, base=None, how=None):
    """Read the buildings from the game over ``r`` (a link.Rcon) -> (overlay dict, report
    lines). ``base``: the catalog without the overlay (for what it already knows)."""
    from . import link
    from .catalog import Catalog
    base = base or Catalog.load(_base_path())
    tag = f"learn-{os.getpid()}"
    sel = link.read_output(r, lua_select(names, mod, all_mods, tag), tag, "learn: select",
                           keep=False)
    want, unknown = _list(sel.get("want")), _list(sel.get("unknown"))
    lines = []
    if unknown:
        lines.append("not a building the game places: " + ", ".join(unknown))
    if not want:
        return None, lines + ["learned nothing"]
    raw = []
    for i in range(0, len(want), BATCH):
        got = link.read_output(r, lua_entities(want[i:i + BATCH], tag), tag,
                               "learn: buildings", keep=False)
        raw += _list(got.get("rows"))
    ov = {"version": sel.get("version"), "mods": _list(sel.get("mods")),
          "source": "fk catalog learn: the game's prototypes over RCON (fkcore.learn)",
          "learned": {}, "entities": [], "recipes": [],
          "fluidboxes": {"entities": {}, "recipes": {}},
          "facts": {"entities": {}, "resources": {}, "fuels": {}, "fluids": {}, "recipes": {}}}
    cats, products, rcats, fcats = set(), set(), set(), set()
    for x in raw:
        crow, fb, frow = rows(x, base)
        ov["entities"].append(crow)
        ov["facts"]["entities"][x["name"]] = frow
        if fb:
            ov["fluidboxes"]["entities"][x["name"]] = fb
        ov["learned"][x["name"]] = how or "fk catalog learn"
        cats.update(frow.get("crafting_categories") or ())
        products.update(crow.get("placed_by") or ())
        rcats.update(frow.get("resource_categories") or ())
        fcats.update(frow.get("fuel_categories") or ())
    got = link.read_output(r, lua_recipes(sorted(cats), sorted(products), mod, all_mods,
                                          sorted(rcats), sorted(fcats), tag),
                           tag, "learn: recipes", keep=False)
    ov["recipes"], ov["facts"]["recipes"] = _recipe_rows(got)
    fl, res, fuels = _things(got)
    ov["facts"]["fluids"], ov["facts"]["resources"], ov["facts"]["fuels"] = fl, res, fuels
    lines += summary(ov, base)
    return ov, lines


def _base_path():
    from .catalog import DEFAULT_PATH
    return DEFAULT_PATH


def summary(ov, base):
    """What an overlay holds, one line a building, and what was new to the catalog."""
    out = []
    fbs = (ov.get("fluidboxes") or {}).get("entities") or {}
    fx = (ov.get("facts") or {}).get("entities") or {}
    for row in ov.get("entities") or ():
        n = row["name"]
        f = fx.get(n) or {}
        bits = [row["type"], f"{row['tile_width']}x{row['tile_height']}"]
        if row.get("crafting_speed") is not None:
            bits.append(f"speed {row['crafting_speed']:g}")
        if f.get("energy_w"):
            bits.append(f"{f['energy_w'] / 1000:g} kW {f.get('energy_source', '')}".rstrip())
        if n in fbs:
            bits.append(f"{len(fbs[n]['boxes'])} fluid boxes")
        if f.get("drop_vector"):
            bits.append(f"drops at {f['drop_vector'][0]:g},{f['drop_vector'][1]:g}")
        if f.get("base_effect"):
            bits.append("built-in " + ", ".join(f"{k} +{v:.0%}" for k, v in
                                                f["base_effect"].items()))
        if f.get("burner"):
            bits.append("burns " + "/".join(f.get("fuel_categories") or ()))
        new = "new" if not base.has_entity(n) else "updated"
        out.append(f"  {n}: {', '.join(bits)} ({new})")
    recs = ov.get("recipes") or ()
    new_r = sum(1 for r in recs if not base.has_recipe(r["name"]))
    out.insert(0, f"learned {len(ov.get('entities') or ())} building(s), {len(recs)} recipes "
                  f"({new_r} new), {len((ov.get('facts') or {}).get('resources') or {})} "
                  f"resources, {len((ov.get('facts') or {}).get('fuels') or {})} fuels")
    return out


def write(ov, path=None):
    """Merge ``ov`` into the overlay file (``learned.json`` here) -> its path."""
    path = path or learnedmod.path() or os.path.abspath(learnedmod.NAME)
    old = learnedmod.load(path)
    data = learnedmod.merge(old, ov)
    data["entities"] = sorted(data.get("entities") or (), key=lambda r: r["name"])
    data["recipes"] = sorted(data.get("recipes") or (), key=lambda r: r["name"])
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return path


def show(name=None, path=None):
    """``fk catalog show [NAME]`` -> lines."""
    from .catalog import Catalog
    path = path or learnedmod.path()
    ov = learnedmod.load(path) if path else None
    base = Catalog.load(_base_path())
    if not name:
        if not ov:
            return ["no learned.json here: the catalog is the base game's"
                    + (f" (pack {os.environ['FK_PACK']})" if os.environ.get("FK_PACK") else "")
                    + "; fk catalog learn NAME reads a building from the game"]
        return [f"{path}: {', '.join(ov.get('mods') or ())}"] + summary(ov, base)
    cat = Catalog.load()
    from . import facts as factsmod
    if not cat.has_entity(name):
        return [f"{name}: not in the catalog; fk catalog learn {name} reads it from the game"]
    row, f = cat.entity(name), factsmod.entity(name)
    where = "learned" if ov and name in (ov.get("learned") or {}) else "the base catalog"
    out = [f"{name}: {row['type']} {row['tile_width']}x{row['tile_height']} ({where})"]
    if row.get("crafting_speed") is not None:
        cats = f.get("crafting_categories") or ()
        n = sum(1 for r in cat.recipes() if r.get("category") in cats)
        out.append(f"  crafts at {row['crafting_speed']:g}: {', '.join(cats) or '?'} "
                   f"({n} recipes)")
    out.append(f"  energy: {f.get('energy_source', '?')} {(f.get('energy_w') or 0) / 1000:g} kW"
               + (", burns " + "/".join(f.get("fuel_categories") or ()) if f.get("burner")
                  else ""))
    boxes = cat.fluid_boxes(name)
    out.append(f"  fluid boxes: {len(boxes)}" + ("" if boxes or not _wants_fluid(cat, f) else
                                                  "  (its recipes use fluids: learn it)"))
    if f.get("drop_vector"):
        out.append(f"  drops its output at {f['drop_vector'][0]:g},{f['drop_vector'][1]:g}")
    if f.get("base_effect"):
        out.append("  built-in effect: " + ", ".join(f"{k} +{v:.0%}" for k, v in
                                                     f["base_effect"].items()))
    return out


def _wants_fluid(cat, f):
    cats = set(f.get("crafting_categories") or ())
    return any(x.get("type") == "fluid" for r in cat.recipes() if r.get("category") in cats
               for x in list(r.get("ingredients") or ()) + list(r.get("products") or ()))


def forget(name, path=None):
    """``fk catalog forget NAME``: NAME out of the overlay -> lines."""
    path = path or learnedmod.path()
    ov = learnedmod.load(path) if path else None
    if not ov or name not in (ov.get("learned") or {}):
        return [f"{name}: not learned here"]
    ov = json.loads(json.dumps(ov))
    ov["learned"].pop(name)
    ov["entities"] = [r for r in ov.get("entities") or () if r["name"] != name]
    for sec in (ov.get("fluidboxes") or {}, (ov.get("facts") or {})):
        (sec.get("entities") or {}).pop(name, None)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(ov, fh, indent=1, sort_keys=True)
        fh.write("\n")
    return [f"{name}: forgotten (its recipes stay in {os.path.basename(path)})"]
