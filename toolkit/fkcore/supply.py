"""fkcore.supply: can it be built now? Network stock, recipes and research (free reads).

The first V1 run (2026-10-20) waited on 295 ghosts. The logistic network held none of their items
(belts, fast inserters, electric drills, ...), half their recipes were not researched, nothing was
researching, and nothing the agent read said so. A player sees all of this anyway (the research
bar, the network's contents, the tech tree): CORE-V1 decision 11 makes them free reads. They are
read, never turned into alerts.

The game (each one RCON call; big replies through script-output, ``link.read_output``):

- ``read(r, force, surface, entities, recipes)`` -> ``Supply``:
  - per entity name: the item that places it (``items_to_place_this``) and how many of it the
    force's logistic networks on the surface hold;
  - per entity and per machine recipe: whether the item's or the recipe's recipe is enabled,
    and for one that is not, the technology that unlocks it (cost or trigger, prerequisites);
  - the research state: current research and progress, queue, labs on the surface, count researched.
- ``tech(r, force, names)``: each name as a technology, or (a recipe or item) the technologies
  that unlock it.
- ``recipes(r, force, names)``: the enabled flag of each recipe (every recipe without names) and
  what unlocks it.
- ``add_research(r, force, names)``: queue technologies. This is the one write here, the research
  screen's; it changes no entity.

Pure, from a read:
- ``research_line``: the footer of every command, loud when nothing researches;
- ``ghost_state``: SHORT (the network holds none, or too few, of the item), needs-research (its recipe is
  not researched), or None (robots can build it);
- ``bom``: short-stock and needs-research check lines and one summary line;
- ``tech_lines`` and ``recipe_lines``.

The Lua follows v4's ``fk/tactics.py`` (``technologies``, ``recipes``, ``research``; copied, not
imported).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

from . import link

__all__ = ["Supply", "read", "tech", "recipes", "add_research", "available", "research_line",
           "ghost_state", "mark_ghosts", "bom", "cost_text", "tech_lines", "recipe_lines",
           "available_lines", "lua_read"]

_J = json.dumps


def _q(s):
    return json.dumps(str(s))


#: Lua: the technology row (name, researched, prerequisites, cost or trigger); ``unlock`` maps a
#: recipe to the first technology whose unlock-recipe effect opens it
_TECH_LUA = """
local function rname(v) if type(v) == 'table' then return v.name end return v end
local unlock = {}
local function index()
  for tname, t in pairs(f.technologies) do
    for _, e in pairs((t.prototype and t.prototype.effects) or {}) do
      if e.type == 'unlock-recipe' then
        local rn = rname(e.recipe)
        if rn then unlock[rn] = unlock[rn] or {} table.insert(unlock[rn], tname) end
      end
    end
  end
end
local function techrow(name)
  local t = f.technologies[name]
  if not t then return nil end
  local proto = t.prototype
  local r = {name = name, researched = t.researched == true, enabled = t.enabled ~= false}
  local pre = {}
  for _, p in pairs(t.prerequisites or {}) do pre[#pre + 1] = {name = p.name, researched = p.researched == true} end
  r.prerequisites = pre
  local ing = {}
  for _, i in pairs((proto and proto.research_unit_ingredients) or {}) do ing[#ing + 1] = {name = i.name, amount = i.amount} end
  r.ingredients = ing
  r.count = (proto and proto.research_unit_count) or 0
  local ok, tr = pcall(function() return proto and proto.research_trigger end)
  if ok and tr then
    r.trigger = {type = tr.type, count = tr.count, item = rname(tr.item), entity = rname(tr.entity)}
  end
  local unl = {}
  for _, e in pairs((proto and proto.effects) or {}) do
    if e.type == 'unlock-recipe' then unl[#unl + 1] = rname(e.recipe) end
  end
  r.unlocks = unl
  return r
end
"""

_STATE_LUA = """
local q = {}
for _, t in pairs(f.research_queue or {}) do q[#q + 1] = t.name end
local done = 0
for _, t in pairs(f.technologies) do if t.researched then done = done + 1 end end
local research = {current = f.current_research and f.current_research.name or nil,
  progress = f.research_progress, queue = q, researched = done,
  labs = s.count_entities_filtered{type = 'lab', force = f}}
local cr = f.current_research
if cr then
  local ps = f.get_item_production_statistics(s)
  local labs = s.find_entities_filtered{type = 'lab', force = f}
  local sn = {}
  for k, v in pairs(defines.entity_status) do sn[v] = k end
  local ing, all, states = {}, 0, {}
  for _, i in pairs(cr.research_unit_ingredients) do
    local function used(p) return ps.get_flow_count{name = i.name, category = 'output', precision_index = p, count = true} end
    ing[#ing + 1] = {name = i.name, amount = i.amount, held = 0,
      min1 = used(defines.flow_precision_index.one_minute), min10 = used(defines.flow_precision_index.ten_minutes)}
  end
  for _, l in pairs(labs) do
    local inv = l.get_inventory(defines.inventory.lab_input)
    local every = inv ~= nil
    for _, i in pairs(ing) do
      if inv and inv.get_item_count(i.name) > 0 then i.held = i.held + 1 else every = false end
    end
    if every then all = all + 1 end
    local k = sn[l.status] or 'none'
    states[k] = (states[k] or 0) + 1
  end
  research.tick = game.tick
  research.units = cr.research_unit_count
  research.ingredients = ing
  research.labs_all = all
  research.lab_states = states
end
"""


def lua_read(force, surface, entities, recipe_names, tag):
    ents, recs = _J(_J(sorted(set(entities)))), _J(_J(sorted(set(recipe_names))))
    return f"""
local f = game.forces[{_q(force)}]
local s = game.get_surface({_q(surface)})
if not f or not s then rcon.print('no force or surface') return end
{_TECH_LUA}
local E = helpers.json_to_table({ents})
local RC = helpers.json_to_table({recs})
if #E > 0 or #RC > 0 then index() end
local nets = f.logistic_networks[s.name] or {{}}
local stock = {{}}
local function count(item)
  if stock[item] == nil then
    local n = 0
    for _, net in pairs(nets) do n = n + net.get_item_count(item) end
    stock[item] = n
  end
  return stock[item]
end
local function locked(name)
  local rec = f.recipes[name]
  local row = {{known = rec ~= nil, enabled = rec ~= nil and rec.enabled}}
  if rec and not rec.enabled and unlock[name] then row.tech = techrow(unlock[name][1]) end
  return row
end
local ents = {{}}
for _, n in pairs(E) do
  local proto = prototypes.entity[n]
  local item = n
  if proto and proto.items_to_place_this and proto.items_to_place_this[1] then
    item = rname(proto.items_to_place_this[1].name) or n
  end
  local row = locked(item)
  row.entity = n
  row.item = item
  row.stock = count(item)
  ents[#ents + 1] = row
end
local recs = {{}}
for _, rn in pairs(RC) do
  local row = locked(rn)
  row.recipe = rn
  recs[#recs + 1] = row
end
{_STATE_LUA}
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({{tick = game.tick, force = f.name,
  surface = s.name, networks = #nets, entities = ents, recipes = recs, research = research}}), false)
rcon.print(#ents + #recs)
"""


def lua_tech(force, names, tag):
    return f"""
local f = game.forces[{_q(force)}]
if not f then rcon.print('no force') return end
{_TECH_LUA}
index()
local asks = {{}}
for _, n in pairs(helpers.json_to_table({_J(_J(list(names)))})) do
  if f.technologies[n] then
    asks[#asks + 1] = {{asked = n, kind = 'tech', techs = {{techrow(n)}}}}
  else
    local item = n
    local proto = prototypes.entity[n]
    if not f.recipes[n] and proto and proto.items_to_place_this and proto.items_to_place_this[1] then
      item = rname(proto.items_to_place_this[1].name) or n
    end
    local rec = f.recipes[item]
    if rec then
      local rows = {{}}
      for _, tn in pairs(unlock[item] or {{}}) do rows[#rows + 1] = techrow(tn) end
      asks[#asks + 1] = {{asked = n, kind = 'recipe', recipe = item, enabled = rec.enabled, techs = rows}}
    else
      asks[#asks + 1] = {{asked = n, kind = 'unknown'}}
    end
  end
end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({{tick = game.tick, asks = asks}}), false)
rcon.print(#asks)
"""


def lua_recipes(force, names, tag):
    return f"""
local f = game.forces[{_q(force)}]
if not f then rcon.print('no force') return end
{_TECH_LUA}
index()
local W = helpers.json_to_table({_J(_J(list(names)))})
local rows = {{}}
local function add(n, r)
  local by = unlock[n] and unlock[n][1] or nil
  rows[#rows + 1] = {{name = n, enabled = r.enabled, category = r.category, by = by,
    by_researched = by and f.technologies[by].researched or nil}}
end
if #W > 0 then
  for _, n in pairs(W) do local r = f.recipes[n] if r then add(n, r) else rows[#rows + 1] = {{name = n, missing = true}} end end
else
  for n, r in pairs(f.recipes) do add(n, r) end
end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({{tick = game.tick, rows = rows}}), false)
rcon.print(#rows)
"""


def lua_available(force, surface, tag):
    """Every technology that can be researched now: not researched, enabled, its prerequisites
    done (v4's research --available)."""
    return f"""
local f = game.forces[{_q(force)}]
local s = game.get_surface({_q(surface)})
if not f or not s then rcon.print('no force or surface') return end
{_TECH_LUA}
local rows = {{}}
for name, t in pairs(f.technologies) do
  if not t.researched and t.enabled ~= false then
    local ready = true
    for _, p in pairs(t.prerequisites or {{}}) do if not p.researched then ready = false end end
    if ready then rows[#rows + 1] = techrow(name) end
  end
end
{_STATE_LUA}
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({{tick = game.tick, techs = rows, research = research}}), false)
rcon.print(#rows)
"""


def lua_add_research(force, surface, names, first=False):
    """Queue ``names``, each after its prerequisites that aren't researched yet (queued ones count
    as on their way, and the missing ones are queued with it, in order); ``first``: the named
    technologies and their prerequisites go to the front of the queue. The first lead run
    (2026-10-21): `fk research A B` refused B while A was only queued, and putting plastics
    first took a Lua ``research_queue = {...}``."""
    return f"""
local f = game.forces[{_q(force)}]
local s = game.get_surface({_q(surface)})
if not f or not s then rcon.print('no force or surface') return end
local added, failed, front, inq, taken = {{}}, {{}}, {{}}, {{}}, {{}}
for _, t in pairs(f.research_queue or {{}}) do inq[t.name] = true end
local function what(v) if type(v) == 'table' then return v.name or '?' end return tostring(v or '') end
local function trigger(tr) return tr.type .. ' ' .. what(tr.entity or tr.item or tr.fluid) end
local function close(t, order, seen)
  if t.researched or seen[t.name] or taken[t.name] then return nil end
  seen[t.name] = true
  for _, p in pairs(t.prerequisites or {{}}) do
    local e = close(p, order, seen)
    if e then return e end
  end
  local tr = t.prototype.research_trigger
  if tr then return t.name .. ' first, a trigger technology: ' .. trigger(tr) end
  order[#order + 1] = t.name
  return nil
end
for _, n in pairs(helpers.json_to_table({_J(_J(list(names)))})) do
  local t = f.technologies[n]
  if not t then failed[#failed + 1] = n .. ' (no such technology)'
  elseif t.researched then failed[#failed + 1] = n .. ' (already researched)'
  else
    local order = {{}}
    local e = close(t, order, {{}})
    if e then failed[#failed + 1] = n .. ' (' .. e .. ')'
    else
      for _, m in pairs(order) do
        taken[m] = true
        front[#front + 1] = m
        if not inq[m] then
          local ok, res = pcall(function() return f.add_research(m) end)
          if ok and res then added[#added + 1] = m; inq[m] = true
          else failed[#failed + 1] = m .. ' (the game refused it: the queue is full)' end
        end
      end
    end
  end
end
local moved = 0
if {"true" if first else "false"} and #front > 0 then
  local q = {{}}
  for _, m in pairs(front) do if inq[m] then q[#q + 1] = m end end
  for _, t in pairs(f.research_queue or {{}}) do if not taken[t.name] then q[#q + 1] = t.name end end
  local ok = pcall(function() f.research_queue = q end)
  if ok then moved = #front else failed[#failed + 1] = 'the queue could not be reordered' end
end
{_STATE_LUA}
rcon.print(helpers.table_to_json({{added = added, failed = failed, moved = moved, front = front, research = research}}))
"""


# -- the reads ------------------------------------------------------------------------------------
@dataclass
class Supply:
    tick: int
    force: str
    surface: str
    networks: int
    entities: dict = field(default_factory=dict)     # entity -> {item, stock, known, enabled, tech}
    recipes: dict = field(default_factory=dict)      # recipe -> {known, enabled, tech}
    research: dict = field(default_factory=dict)


def _list(v):
    if isinstance(v, dict):
        return list(v.values())
    return list(v or [])


def from_raw(raw) -> Supply:
    res = dict(raw.get("research") or {})
    res["queue"] = _list(res.get("queue"))
    sup = Supply(int(raw.get("tick") or 0), str(raw.get("force") or ""),
                 str(raw.get("surface") or ""), int(raw.get("networks") or 0), research=res)
    for row in _list(raw.get("entities")):
        sup.entities[row["entity"]] = row
    for row in _list(raw.get("recipes")):
        sup.recipes[row["recipe"]] = row
    return sup


def read(r, force, surface, entities=(), recipe_names=(), tag="supply") -> Supply:
    tag = f"{tag}-{os.getpid()}"
    return from_raw(link.read_output(r, lua_read(force, surface, entities, recipe_names, tag), tag,
                                     "supply read", keep=False))


def tech(r, force, names, tag="tech"):
    tag = f"{tag}-{os.getpid()}"
    return link.read_output(r, lua_tech(force, names, tag), tag, "tech read", keep=False)


def recipes(r, force, names=(), tag="recipes"):
    tag = f"{tag}-{os.getpid()}"
    return link.read_output(r, lua_recipes(force, names, tag), tag, "recipes read", keep=False)


def available(r, force, surface, tag="research"):
    tag = f"{tag}-{os.getpid()}"
    return link.read_output(r, lua_available(force, surface, tag), tag, "research read", keep=False)


def add_research(r, force, surface, names, first=False):
    text = r.lua(lua_add_research(force, surface, names, first))
    try:
        return json.loads(text)
    except ValueError:
        raise link.LinkError(f"research: not JSON: {text[:300]}")


# -- pure ----------------------------------------------------------------------------------------
def _n(v):
    v = float(v or 0)
    return str(int(v)) if v.is_integer() else f"{v:g}"


def cost_text(t):
    """'25 automation-science-pack', '40 automation + 40 logistic science', 'craft lab x1'."""
    if not t:
        return "?"
    trig = t.get("trigger")
    if trig:
        kind = trig.get("type") or "?"
        what = trig.get("item") or trig.get("entity") or ""
        verb = {"craft-item": "craft", "mine-entity": "mine", "build-entity": "build"}.get(kind, kind)
        return f"{verb} {what} x{_n(trig.get('count') or 1)}".strip()
    units = float(t.get("count") or 0)
    parts = [f"{_n(units * float(i.get('amount') or 0))} {i.get('name')}"
             for i in _list(t.get("ingredients"))]
    return " + ".join(parts) or "no cost"


def _missing_prereqs(t):
    return [p["name"] for p in _list(t.get("prerequisites")) if not p.get("researched")]


def tech_brief(t):
    """'electric-mining-drill (25 automation-science-pack; first: automation-science-pack)'."""
    return _tech_brief(t)


def _tech_brief(t):
    """'electric-mining-drill (25 automation-science-pack; first: automation-science-pack)'."""
    first = _missing_prereqs(t)
    tail = f"; first: {', '.join(first)}" if first else ""
    return f"{t.get('name')} ({cost_text(t)}{tail})"


def _minutes(m):
    if m < 1:
        return "under a minute left"
    if m < 120:
        return f"~{m:.0f} min left"
    return f"~{m / 60:.1f} h left"


def _stall_cause(st, ing):
    """What stops research whose labs used no pack in the last minute, when the read can tell."""
    labs = int(st.get("labs") or 0)
    states = st.get("lab_states") if isinstance(st.get("lab_states"), dict) else {}
    if labs == 0:
        return "no labs"
    dark = int(states.get("no_power") or 0)
    if dark >= labs:
        return f"the {labs} lab{'s have' if labs != 1 else ' has'} no power"
    none = [i["name"] for i in ing if not int(i.get("held") or 0)]
    if none:
        return "no lab holds " + ", ".join(none)
    if not int(st.get("labs_all") or 0) and len(ing) > 1:
        worst = max(ing, key=lambda i: labs - int(i.get("held") or 0))
        return (f"no lab holds all {len(ing)} packs ({worst['name']} missing in "
                f"{labs - int(worst.get('held') or 0)} of {labs} labs)")
    if dark:
        return f"{dark} of {labs} labs without power"
    busy = ", ".join(f"{n} {k.replace('_', ' ')}" for k, n in
                     sorted(states.items(), key=lambda kv: -kv[1]))
    return f"labs: {busy}" if busy else ""


def research_rate(state):
    """-> (packs per minute in the last minute, minutes left or None), or None when the read
    has no pack counts (an older read). The rate counts the current research's packs used in the
    last game minute; the time left is the units still to go at that pace."""
    st = state or {}
    ing = [i for i in _list(st.get("ingredients")) if isinstance(i, dict) and i.get("name")]
    if not st.get("current") or not ing:
        return None
    per_min = sum(float(i.get("min1") or 0) for i in ing)
    per_unit = sum(float(i.get("amount") or 1) for i in ing)
    units = float(st.get("units") or 0)
    left = None
    if per_min > 0 and units > 0:
        left = (1 - float(st.get("progress") or 0)) * units / (per_min / per_unit)
    return per_min, left


def research_line(state):
    """The footer line: what researches now, how fast and how long it takes yet (2.1, Rory
    2026-10-31: ``research military-3 34%, 41 packs/min, ~22 min left``); loud when nothing
    researches, or when the labs used no pack in the last minute, with the cause when the read
    can tell (``no lab holds military-science-pack``)."""
    st = state or {}
    labs = int(st.get("labs") or 0)
    q = [x for x in _list(st.get("queue")) if x and x != st.get("current")]
    if st.get("current"):
        pct = f" {float(st.get('progress') or 0) * 100:.0f}%" if st.get("progress") is not None else ""
        # 3.0 (the retro: research sat idle in three long runs, the last time 8.5 h after its
        # queue ran out): say so while the last tech still runs, not once the labs stand
        queue = f"; queue: {', '.join(q)}" if q else \
            ("; QUEUE EMPTY after this: `fk research TECH ...` queues more" if "queue" in st else "")
        warn = " (0 labs: it will not move)" if labs == 0 else ""
        tail = f"{queue}; {labs} lab{'s' if labs != 1 else ''}{warn}"
        rate = research_rate(st)
        if rate is None:
            return f"research: {st['current']}{pct}{tail}"
        per_min, left = rate
        if per_min <= 0 and int(st.get("tick") or 0) >= 3600:
            ing = [i for i in _list(st.get("ingredients")) if isinstance(i, dict)]
            cause = _stall_cause(st, ing)
            return (f"RESEARCH STALLED: {st['current']}{pct}, 0 packs/min in the last minute"
                    + (f": {cause}" if cause else "") + tail)
        when = f", {_minutes(left)}" if left is not None else ""
        return f"research {st['current']}{pct}, {per_min:.0f} packs/min{when}{tail}"
    return (f"RESEARCH: NONE. Nothing is researching and the queue is empty; {labs} "
            f"lab{'s' if labs != 1 else ''}. Locked recipes stay locked: `fk research TECH` starts "
            "one, `fk tech ITEM` says which technology opens an item and what it costs.")


def ghost_state(sup, entity, ghosts=1):
    """(state, why) for ``ghosts`` waiting ghosts of ``entity``: ('SHORT', ...), ('needs-research', ...),
    or (None, ...) when the network holds enough for robots to build them."""
    row = sup.entities.get(entity)
    if row is None:
        return None, "not read"
    stock = int(row.get("stock") or 0)
    if stock >= ghosts:
        return None, f"the network holds {stock} {row.get('item')}"
    if row.get("known") and not row.get("enabled"):
        t = row.get("tech")
        how = f"research {_tech_brief(t)}" if t else "no technology unlocks it here"
        return "needs-research", f"{row.get('item')} is not researched: {how}; the network holds {stock}"
    have = f"the network holds {stock}" + (f" of {ghosts}" if ghosts > 1 else "")
    make = "make it (an assembler) or bring it" if row.get("known") else "it has no recipe: bring it"
    return "SHORT", f"{have} {row.get('item')}: {make}"


def mark_ghosts(verdicts, sup):
    """ghost -> ghost SHORT / ghost needs-research (with why) for a ghost robots cannot build now: a
    ghost that stays is a blocked build, not a slow one. Changes ``verdicts`` in place."""
    per = {}
    for v in verdicts:
        if v.verdict == "ghost":
            per[v.name] = per.get(v.name, 0) + 1
    states = {name: ghost_state(sup, name, n) for name, n in per.items()}
    for v in verdicts:
        if v.verdict == "ghost":
            state, why = states[v.name]
            if state:
                v.verdict = f"ghost {state}"
                v.detail = why
    return verdicts


def bom(need, sup, recipes_used=()):
    """(lines, checks): ``need`` {entity: count still to build} against the network's stock, and
    every machine recipe in ``recipes_used`` against what is researched. checks: [(code, where,
    msg)]: short-stock per short item, needs-research per item or recipe not researched."""
    from .elaborate import Check
    lines, checks, short, locked_items, ok = [], [], [], [], 0
    for ent in sorted(need, key=lambda e: (-need[e], e)):
        n = need[ent]
        row = sup.entities.get(ent, {})
        item, stock = row.get("item", ent), int(row.get("stock") or 0)
        state, why = ghost_state(sup, ent, n)
        mark = "" if state is None else f"  {state}"
        lines.append(f"  {item:<28} need {n:>5}  network {stock:>6}{mark}")
        if state is None:
            ok += 1
            continue
        if stock < n:
            short.append(f"{n - stock} {item}")
            checks.append(Check("short-stock", item, f"need {n}, the network holds {stock}"))
        if state == "needs-research":
            locked_items.append(item)
            checks.append(Check("needs-research", item, why))
    for rec in sorted(set(recipes_used)):
        row = sup.recipes.get(rec)
        if row and row.get("known") and not row.get("enabled"):
            t = row.get("tech")
            checks.append(Check("needs-research", f"recipe {rec}", "a machine is set to it, and it is not "
                                f"researched: research {_tech_brief(t)}" if t else
                                "a machine is set to it, and nothing unlocks it here"))
            locked_items.append(f"recipe {rec}")
    kinds = len(need)
    if short or locked_items:
        head = (f"STOCK: robots can build {ok} of {kinds} kinds now; SHORT {', '.join(short[:8])}"
                + (" ..." if len(short) > 8 else "")
                + (f"; needs-research {', '.join(locked_items[:8])}" if locked_items else "")
                + " (fk tech ITEM says what opens a locked one)")
    else:
        head = f"stock: the network holds enough for all {kinds} kinds still to build"
    return [head] + lines, checks


def tech_lines(data):
    """The report of ``tech``: per name, the technology (cost or trigger, prerequisites, what it
    unlocks), or the technologies that unlock a recipe or item."""
    out = []
    for ask in _list((data or {}).get("asks")):
        kind = ask.get("kind")
        if kind == "unknown":
            out.append(f"{ask.get('asked')}: no technology, recipe or item of that name")
            continue
        if kind == "recipe":
            rec = ask.get("recipe")
            alias = f" (the item that places it: {rec})" if rec != ask.get("asked") else ""
            state = "enabled: it can be made" if ask.get("enabled") else "needs-research: research it first"
            techs = _list(ask.get("techs"))
            out.append(f"{ask.get('asked')}{alias}: recipe {state}"
                       + ("" if techs or ask.get("enabled") else "; no technology unlocks it"))
        for t in _list(ask.get("techs")):
            done = "researched" if t.get("researched") else "NOT researched"
            first = _missing_prereqs(t)
            out.append(f"  tech {t.get('name')}: {done}; cost {cost_text(t)}"
                       + (f"; research first: {', '.join(first)}" if first else
                          ("; its prerequisites are done" if not t.get("researched") else "")))
            unl = [u for u in _list(t.get("unlocks")) if u]
            if unl:
                out.append(f"    unlocks: {', '.join(unl)}")
    return out or ["tech: nothing asked"]


def available_lines(data):
    techs = sorted(_list((data or {}).get("techs")),
                   key=lambda t: (0 if t.get("trigger") else 1, float(t.get("count") or 0),
                                  t.get("name") or ""))
    out = [f"research available now: {len(techs)} (prerequisites done; a trigger first, then the "
           "cheapest; a trigger technology completes when you craft or build what it names)"]
    for t in techs[:40]:
        out.append(f"  {t.get('name'):<36} {cost_text(t)}")
    if len(techs) > 40:
        out.append(f"  ... {len(techs) - 40} more")
    return out


def recipe_lines(data, enabled=False):
    rows = [r for r in _list((data or {}).get("rows")) if not r.get("missing")]
    missing = [r["name"] for r in _list((data or {}).get("rows")) if r.get("missing")]
    on = [r for r in rows if r.get("enabled")]
    off = [r for r in rows if not r.get("enabled")]
    out = [f"recipes: {len(rows)} read, {len(on)} enabled, {len(off)} locked"]
    if missing:
        out.append(f"  no such recipe: {', '.join(sorted(missing))}")
    shown = sorted(on if enabled else off, key=lambda r: r["name"])
    for r in shown[:RECIPES_SHOWN]:
        by = r.get("by")
        how = (f"by {by} ({'researched' if r.get('by_researched') else 'not researched'})" if by
               else "no technology")
        out.append(f"  {r['name']:<36} {'enabled' if r.get('enabled') else 'needs-research':<8} {how}")
    if len(shown) > RECIPES_SHOWN:
        out.append(f"  ... {len(shown) - RECIPES_SHOWN} more: name the recipes you need "
                   "(fk recipes NAME...), or ask fk tech ITEM")
    return out


#: at most this many recipe rows in one report (a world has hundreds of locked recipes)
RECIPES_SHOWN = 40
