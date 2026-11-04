"""fkcore.capital: how much of what the base owns is working, in raw units (2.0.69).

Rory, 2026-10-30: measure "what percentage of stuff is reinvested already, versus just sitting
idle". Everything the force owns is priced in raw resources (``cost.item_cost``: an iron plate is
1, an assembling-machine-2 54) and lands in one of three buckets:

- **working**: a production entity (assembler, furnace, drill, lab, silo, beacon) and the modules
  in it, times the share of the time it ran; a robot in the air; a researched technology whose
  unlocks are in use (below), and what has gone into the research under way;
- **idle**: every item, wherever it is (chests, belts, machine inputs and outputs, inserter hands,
  robot cargo, the ground, the pack): stock is not reinvested, a mall's included; a production
  entity that stood; a robot in a roboport; a researched technology nothing uses;
- **left out**: the infrastructure (belts, inserters, poles, pipes, chests, roboports, power,
  walls, turrets), counted on neither side and only reported; and whatever the player parks.

The headline is working / (working + idle). Nothing is left out by default: **parking** is a
deliberate choice, an area named with a reason (``park``), and what is parked is always reported.

**A machine's share of the time** comes from its finished crafts (``products_finished``) since
the last read: crafts x the recipe's time / the crafting speed / the seconds between, at most 1.
With no last read (a new machine, the first read), or a craft longer than the gap, it is its status
now: working is 1, low power 0.5, anything else 0. Drills, labs and beacons go by status.

**A technology** is worth the packs it took (units x ingredients, each pack its raw cost), times
its weight (1; 0.25 for one whose effects are all military; ``weight`` sets any). It is working
when something it unlocks is in use: a working machine runs one of its recipes, or a recipe's
product is placed (infrastructure), working (a machine, a module in one) or flying (a robot). A
bonus technology is working while what it boosts works (mining productivity: a drill; robot
techs: a robot in the air; lab techs: a lab; inserter and belt stack bonuses: an inserter placed;
military: a turret placed). Infinite technologies are left out for now.

**Growth**: each read is kept (``history.jsonl``), and the line says how fast the working capital
doubles over the last half hour or so of game time.

A free read, like ``stock``: the whole surface (or an area, for a lab slot), never an alert.
The game read is ``lua_read`` (``helpers.write_file``, nothing written to the world); ``score``
is pure. State lives in ``<game>/out/capital/``: ``last.json`` (the last read's crafts per
machine), ``history.jsonl`` and ``park.json`` (the parked areas and the weights).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass, field

from . import cost, link

__all__ = ["lua_read", "read", "score", "Capital", "lines", "line", "run", "Store", "MAKERS"]

#: production entity types: their capital works when they do
MAKERS = ("assembling-machine", "furnace", "rocket-silo", "mining-drill", "lab", "beacon")
#: of them, the ones that craft recipes (their share of the time comes from finished crafts)
CRAFTERS = ("assembling-machine", "furnace", "rocket-silo")
#: entity types that are neither capital nor stock: never counted (their contents are)
SKIP = ("entity-ghost", "tile-ghost", "item-request-proxy", "character", "character-corpse",
        "item-entity", "resource", "corpse", "construction-robot", "logistic-robot",
        "deconstructible-tile-proxy", "highlight-box", "flying-text", "smoke-with-trigger",
        "projectile", "explosion", "fire", "sticker", "stream", "particle-source")

#: technology effects and what has to work for them to count as working
EFFECT_USE = {
    "mining-drill-productivity-bonus": "drill",
    "worker-robot-speed": "robot", "worker-robot-storage": "robot",
    "worker-robot-battery": "robot", "maximum-following-robots-count": "robot",
    "laboratory-speed": "lab", "laboratory-productivity": "lab",
    "inserter-stack-size-bonus": "inserter", "bulk-inserter-capacity-bonus": "inserter",
    "belt-stack-size-bonus": "inserter",
    "ammo-damage": "turret", "gun-speed": "turret", "turret-attack": "turret",
    "artillery-range": "turret",
}
MILITARY = {"ammo-damage", "gun-speed", "turret-attack", "artillery-range"}
MILITARY_WEIGHT = 0.25
TURRETS = ("ammo-turret", "electric-turret", "fluid-turret", "artillery-turret")
#: the half hour or so the growth is measured over (ticks), and the least it needs
GROWTH_SPAN, GROWTH_MIN = 30 * 3600, 5 * 3600


def lua_read(force, surface, area=None, parked=(), tag="capital"):
    """One read of everything ``force`` owns on ``surface`` (inside ``area`` when given), by zone:
    0 counted, k the k-th parked box. Production entities one row each (their crafts, recipe,
    status, modules); every other placed entity as an item count; every item in every inventory,
    on every belt lane, in every inserter's hand and on the ground; robots in the air and in
    roboports; the technologies researched and the research under way."""
    box = "nil" if not area else "{{%s,%s},{%s,%s}}" % tuple(area)
    zones = "{" + ", ".join("{%s,%s,%s,%s}" % tuple(b) for b in parked) + "}"
    makers = "{" + ", ".join(link._q(t) for t in MAKERS) + "}"
    crafters = "{" + ", ".join(f"[{link._q(t)}] = true" for t in CRAFTERS) + "}"
    skip = "{" + ", ".join(f"[{link._q(t)}] = true" for t in SKIP) + "}"
    return f"""
local f = game.forces[{link._q(force)}]
local s = game.get_surface({link._q(surface)})
if not f or not s then rcon.print('no force or surface') return end
local box, zones = {box}, {zones}
local makers, crafters, skip = {{}}, {crafters}, {skip}
local belts = {{['transport-belt'] = true, ['underground-belt'] = true, splitter = true,
  loader = true, ['loader-1x1'] = true, ['linked-belt'] = true, ['lane-splitter'] = true}}
for _, t in pairs({makers}) do makers[t] = true end
local names = {{}}
for k, v in pairs(defines.entity_status) do names[v] = k end
local function zone(p)
  for i, b in ipairs(zones) do
    if p.x >= b[1] and p.x <= b[3] and p.y >= b[2] and p.y <= b[4] then return i end
  end
  return 0
end
local items, placed, port, flying, rows = {{}}, {{}}, {{}}, {{}}, {{}}
local function bag(t, z) local k = tostring(z) t[k] = t[k] or {{}} return t[k] end
local function add(into, name, n) into[name] = (into[name] or 0) + n end
local function contents(into, inv)
  if inv and inv.valid then
    for _, it in pairs(inv.get_contents()) do add(into, it.name, it.count) end
  end
end
local function item_of(e)
  local it = e.prototype.items_to_place_this
  return it and it[1] and (type(it[1]) == 'table' and it[1].name or it[1]) or nil
end
local q = {{force = f}}
if box then q.area = box end
for _, e in pairs(s.find_entities_filtered(q)) do
  local t = e.type
  if not skip[t] then
    local z = zone(e.position)
    local its = bag(items, z)
    local mi = makers[t] and e.get_module_inventory and e.get_module_inventory() or nil
    for i = 1, e.get_max_inventory_index() do
      local inv = e.get_inventory(i)
      if inv and inv.valid and not (mi and inv.index == mi.index) then
        if t == 'roboport' and i == defines.inventory.roboport_robot then
          contents(bag(port, z), inv)
        else
          contents(its, inv)
        end
      end
    end
    if belts[t] then
      for l = 1, e.get_max_transport_line_index() do
        for _, it in pairs(e.get_transport_line(l).get_contents()) do add(its, it.name, it.count) end
      end
    end
    if t == 'inserter' and e.held_stack and e.held_stack.valid_for_read then
      add(its, e.held_stack.name, e.held_stack.count)
    end
    local name = item_of(e)
    if name then
      if makers[t] then
        local row = {{u = e.unit_number, n = name, t = t, z = z, st = names[e.status] or tostring(e.status)}}
        if crafters[t] then
          row.pf = e.products_finished
          local ok, r = pcall(e.get_recipe)
          if ok and r then
            row.rc, row.e = r.name, r.energy
          elseif t == 'furnace' and e.previous_recipe then
            -- a furnace that stands reports no recipe: the one it last ran
            local n = e.previous_recipe.name
            if type(n) ~= 'string' then n = n.name end
            local p = prototypes.recipe[n]
            row.rc, row.e = n, p and p.energy
          end
          row.sp = e.crafting_speed
        end
        if mi then
          local m = {{}}
          contents(m, mi)
          if next(m) then row.m = m end
        end
        rows[#rows + 1] = row
      else
        local p = bag(placed, z)
        add(p, name, 1)
        if string.find(t, 'turret', 1, true) then add(p, '__turret', 1) end
      end
    end
  end
end
for _, e in pairs(s.find_entities_filtered{{force = f, type = {{'logistic-robot', 'construction-robot'}}, area = box}}) do
  local z = zone(e.position)
  add(bag(flying, z), e.name, 1)
  local inv = e.get_inventory(defines.inventory.robot_cargo)
  contents(bag(items, z), inv)
end
for _, e in pairs(s.find_entities_filtered{{type = 'item-entity', area = box}}) do
  if e.stack and e.stack.valid_for_read then add(bag(items, zone(e.position)), e.stack.name, e.stack.count) end
end
for _, p in pairs(box and {{}} or game.players) do
  if p.character and p.force == f and p.surface == s then
    local its = bag(items, zone(p.character.position))
    for i = 1, p.character.get_max_inventory_index() do contents(its, p.character.get_inventory(i)) end
  end
end
local techs = {{}}
for name, t in pairs(f.technologies) do
  if t.researched and t.enabled then
    local pt = t.prototype
    if pt.research_unit_count_formula == nil or pt.max_level == 1 then
      local row = {{packs = {{}}, fx = {{}}, un = {{}}}}
      for _, i in pairs(t.research_unit_ingredients or {{}}) do
        row.packs[i.name] = (row.packs[i.name] or 0) + i.amount * (t.research_unit_count or 0)
      end
      for _, e in pairs(t.prototype.effects or {{}}) do
        if e.type == 'unlock-recipe' then row.un[#row.un + 1] = e.recipe
        else row.fx[#row.fx + 1] = e.type end
      end
      techs[name] = row
    end
  end
end
local now
local cr = f.current_research
if cr then
  now = {{name = cr.name, pct = f.research_progress, packs = {{}}}}
  for _, i in pairs(cr.research_unit_ingredients or {{}}) do
    now.packs[i.name] = (now.packs[i.name] or 0) + i.amount * (cr.research_unit_count or 0)
  end
end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({{tick = game.tick, force = f.name,
  surface = s.name, rows = rows, items = items, placed = placed, port = port, flying = flying,
  techs = techs, research = now}}), false)
rcon.print(#rows)
"""


def read(r, force, surface, area=None, parked=()):
    """The raw read (``lua_read``) through the game link."""
    tag = f"capital-{os.getpid()}"
    return link.read_output(r, lua_read(force, surface, area, parked, tag), tag, "capital",
                            keep=False)


def _dict(x):
    """Lua's empty table comes back as a JSON list."""
    return x if isinstance(x, dict) else {}


@dataclass
class Capital:
    tick: int
    working: float = 0.0
    idle: float = 0.0
    parked: float = 0.0
    infra: float = 0.0
    # per part: {"working": w, "total": t}
    parts: dict = field(default_factory=dict)
    makers: tuple = (0, 0)          # (working, placed): any share of the time counts as working
    duty: float = 0.0               # the makers' shares of the time, summed
    stood: dict = field(default_factory=dict)       # status -> raw units of makers that stood
    piles: list = field(default_factory=list)       # [(raw, item, count)] the stock, largest first
    unused: list = field(default_factory=list)      # [(raw, tech)] researched, nothing uses it
    techs: tuple = (0, 0)           # (working, researched)
    research: tuple | None = None   # (name, raw units gone into it)
    parks: list = field(default_factory=list)       # [(raw, why)] per parked area
    crafts: dict = field(default_factory=dict)      # unit -> [products finished, recipe]: next read
    robot_share: dict = field(default_factory=dict)  # robot -> share in the air: next read

    @property
    def total(self):
        return self.working + self.idle

    @property
    def share(self):
        return self.working / self.total if self.total > 0 else 0.0

    def part(self, name, working, total):
        p = self.parts.setdefault(name, {"working": 0.0, "total": 0.0})
        p["working"] += working
        p["total"] += total
        self.working += working
        self.idle += total - working


def _worth(cat, memo):
    def worth(item):
        if item in memo:
            return memo[item]
        try:
            return cost.item_cost(cat, item, memo) if cat.is_item(item) else 1.0
        except (KeyError, TypeError):
            memo[item] = 1.0
            return 1.0
    return worth


def _duty(row, last, dt):
    """A maker's share of the time since the last read (module docstring)."""
    st = row.get("st")
    now = 1.0 if st == "working" else 0.5 if st == "low_power" else 0.0
    prev = last.get(str(row.get("u")))
    if row.get("t") not in CRAFTERS or not prev or dt <= 0 or prev[1] != row.get("rc"):
        return now
    crafts = (row.get("pf") or 0) - (prev[0] or 0)
    energy, speed = float(row.get("e") or 0), float(row.get("sp") or 0)
    if crafts <= 0 or energy <= 0 or speed <= 0:
        # a craft longer than the gap may simply not have finished yet
        return now if energy and speed and energy / speed > dt else 0.0
    return min(1.0, crafts * energy / speed / dt)


def score(raw, cat, last=None, weights=None, whys=(), memo=None) -> Capital:
    """The read (``lua_read``) -> ``Capital``. ``last``: the last read's ``crafts``, ``robots``
    and ``tick`` (``Capital.crafts``, ``.robot_share``), for the machines' shares of the time.
    ``weights``: {tech: weight}. ``whys``: each parked area's reason, in zone order."""
    worth = _worth(cat, {} if memo is None else memo)
    last = last or {}
    weights = weights or {}
    tick = int(raw.get("tick") or 0)
    lt = int(last.get("tick") or 0)
    dt = (tick - lt) / 60.0 if last and tick > lt else 0.0
    crafts_last = _dict(last.get("crafts")) if dt else {}
    cap = Capital(tick)
    parks = {}

    def park(z, raw_units):
        parks[z] = parks.get(z, 0.0) + raw_units
        cap.parked += raw_units

    # makers
    active_recipes, active_items = set(), set()
    working, placed, kinds = 0, 0, {"drill": False, "lab": False}
    for row in _dict(raw).get("rows") or []:
        mods = _dict(row.get("m"))
        r = worth(row["n"]) + sum(worth(m) * n for m, n in mods.items())
        u = str(row.get("u"))
        if row.get("t") in CRAFTERS:
            cap.crafts[u] = [row.get("pf") or 0, row.get("rc")]
        z = int(row.get("z") or 0)
        if z:
            park(z, r)
            continue
        d = _duty(row, crafts_last, dt)
        cap.part("machines", r * d, r)
        placed += 1
        cap.duty += d
        if d > 0:
            working += 1
            active_items.add(row["n"])
            active_items.update(mods)
            if row.get("rc"):
                active_recipes.add(row["rc"])
            if row.get("t") == "mining-drill":
                kinds["drill"] = True
            if row.get("t") == "lab":
                kinds["lab"] = True
        if d < 1:
            st = row.get("st") or "?"
            st = "part of the time" if st == "working" else st
            cap.stood[st] = cap.stood.get(st, 0.0) + r * (1 - d)
    cap.makers = (working, placed)

    # robots: in the air works, in a roboport waits; the share in the air averages two reads
    port, flying = _dict(raw.get("port")), _dict(raw.get("flying"))
    shares_last = _dict(last.get("robots"))
    names = set()
    for bags in (port, flying):
        for z, b in bags.items():
            names.update(_dict(b))
    for name in sorted(names):
        up = _dict(flying.get("0")).get(name, 0)
        home = _dict(port.get("0")).get(name, 0)
        for z, b in list(flying.items()) + list(port.items()):
            if z != "0":
                park(int(z), worth(name) * _dict(b).get(name, 0))
        if up + home == 0:
            continue
        share = up / (up + home)
        cap.robot_share[name] = share
        if name in shares_last:
            share = (share + float(shares_last[name])) / 2
        r = worth(name) * (up + home)
        cap.part("robots", r * share, r)
        if up:
            active_items.add(name)

    # stock: every item is idle
    piles = {}
    for z, b in _dict(raw.get("items")).items():
        for item, n in _dict(b).items():
            if z != "0":
                park(int(z), worth(item) * n)
                continue
            piles[item] = piles.get(item, 0) + n
    for item, n in piles.items():
        r = worth(item) * n
        cap.part("stock", 0.0, r)
        cap.piles.append((r, item, n))
    cap.piles.sort(key=lambda p: (-p[0], p[1]))

    # infrastructure: left out, reported
    infra = _dict(_dict(raw.get("placed")).get("0"))
    placed_items = {i for i in infra if not i.startswith("__")}
    cap.infra = sum(worth(i) * n for i, n in infra.items() if not i.startswith("__"))
    for z, b in _dict(raw.get("placed")).items():
        if z != "0":
            park(int(z), sum(worth(i) * n for i, n in _dict(b).items() if not i.startswith("__")))
    uses = {"drill": kinds["drill"], "lab": kinds["lab"],
            "robot": any(_dict(flying.get("0")).values()),
            "inserter": any(i.endswith("inserter") for i in placed_items),
            "turret": bool(infra.get("__turret"))}

    # research
    used = active_items | placed_items
    techs, on = _dict(raw.get("techs")), 0
    for name, t in sorted(techs.items()):
        packs = _dict(t.get("packs"))
        fx = [e for e in t.get("fx") or [] if isinstance(e, str)]
        w = weights.get(name)
        if w is None:
            w = MILITARY_WEIGHT if fx and not t.get("un") and all(e in MILITARY for e in fx) else 1.0
        r = w * sum(worth(p) * n for p, n in packs.items())
        if r <= 0:
            continue
        live = False
        for rc in t.get("un") or []:
            if rc in active_recipes:
                live = True
                break
            try:
                prods = [p.get("name") for p in cat.recipe(rc).get("products", ())]
            except (KeyError, TypeError, AttributeError):
                prods = [rc]
            if any(p in used for p in prods):
                live = True
                break
        if not live and not t.get("un"):
            live = any(uses.get(EFFECT_USE.get(e), True) for e in fx) if fx else True
        cap.part("research", r if live else 0.0, r)
        if live:
            on += 1
        else:
            cap.unused.append((r, name))
    cap.techs = (on, len([1 for t in techs.values()
                          if sum(_dict(t.get("packs")).values()) > 0]))
    cap.unused.sort(key=lambda u: (-u[0], u[1]))
    now = raw.get("research")
    if isinstance(now, dict):
        w = weights.get(now.get("name"), 1.0)
        r = w * float(now.get("pct") or 0) * sum(worth(p) * n
                                                  for p, n in _dict(now.get("packs")).items())
        cap.research = (now.get("name"), r)
        if r > 0:
            cap.part("research", r, r)
    cap.parks = [(parks.get(i + 1, 0.0), why) for i, why in enumerate(whys)]
    return cap


def growth(history, tick, working):
    """How fast the working capital doubles: against the read nearest half an hour back (at least
    five minutes back) -> (minutes to double, or None when it shrank, minutes measured over), or
    None with too little history."""
    back = [h for h in history if tick - GROWTH_MIN >= h[0] >= tick - 3 * GROWTH_SPAN]
    if not back:
        return None
    then = min(back, key=lambda h: abs(tick - GROWTH_SPAN - h[0]))
    span = (tick - then[0]) / 3600.0
    if then[1] <= 0 or working <= 0:
        return None
    ratio = working / then[1]
    if ratio <= 1:
        return (None, span, ratio)
    return (span * math.log(2) / math.log(ratio), span, ratio)


def _k(v):
    v = float(v)
    if v >= 10000:
        return f"{v / 1000:.0f}k"
    if v >= 1000:
        return f"{v / 1000:.1f}k"
    return f"{v:.0f}"


def _growth_text(g):
    if not g:
        return "growth: not enough reads yet"
    mins, span, ratio = g
    if mins is None:
        return f"growth: the working capital shrank to {ratio:.0%} over {span:.0f} min"
    return f"the working capital doubles every {mins:.0f} min (over {span:.0f} min)"


def line(cap, g=None):
    """One sentence for the world line."""
    def pct(name):
        p = cap.parts.get(name)
        return f"{p['working'] / p['total']:.0%} of {_k(p['total'])}" if p and p["total"] else "none"
    out = (f"working capital {cap.share:.0%} of {_k(cap.total)} raw units (machines "
           f"{pct('machines')}, robots {pct('robots')}, research {pct('research')}, stock "
           f"{_k(cap.parts.get('stock', {}).get('total', 0))} idle; makers {cap.makers[0]} of "
           f"{cap.makers[1]} ran")
    if cap.parked:
        out += f"; {_k(cap.parked)} parked"
    out += "); " + _growth_text(g)
    return out


def lines(cap, g=None, surface="", top=5):
    """The report: the headline, each part, what stands idle most, what is left out."""
    out = [f"capital {surface}: tick {cap.tick}: {cap.share:.1%} working, {_k(cap.working)} of "
           f"{_k(cap.total)} raw units (a free read: never an alert)"]

    def part(name):
        p = cap.parts.get(name, {"working": 0.0, "total": 0.0})
        return f"{_k(p['working']):>6} working of {_k(p['total']):>6}"
    stood = ", ".join(f"{_k(v)} {k.replace('_', ' ')}" for k, v in
                      sorted(cap.stood.items(), key=lambda kv: -kv[1])[:top])
    out.append(f"  machines {part('machines')}  ({cap.makers[0]} of {cap.makers[1]} makers ran, "
               f"{cap.duty:.1f} at full time)" + (f"; idle: {stood}" if stood else ""))
    rb = cap.parts.get("robots")
    if rb:
        out.append(f"  robots   {part('robots')}  (in the air works, in a roboport waits)")
    out.append(f"  research {part('research')}  ({cap.techs[0]} of {cap.techs[1]} techs in use"
               + (f", {cap.research[0]} under way" if cap.research else "") + ")"
               + ("; unused: " + ", ".join(f"{t} {_k(r)}" for r, t in cap.unused[:top])
                  if cap.unused else ""))
    out.append(f"  stock    {part('stock')}  (every item is idle until it is built or spent)"
               + ("; most: " + ", ".join(f"{n} {i} ({_k(r)})" for r, i, n in cap.piles[:top])
                  if cap.piles else ""))
    out.append(f"  left out: {_k(cap.infra)} in belts, inserters, poles, pipes, chests, roboports, "
               "power and turrets")
    for i, (r, why) in enumerate(cap.parks, 1):
        out.append(f"  parked {i}: {_k(r)} ({why})")
    out.append("  " + _growth_text(g))
    return out


class Store:
    """``<game>/out/capital/``: the last read and the history (per area, and per game: the
    planner, ``fk sim game capital``, keeps its own beside the live game's), and the parked
    areas."""

    def __init__(self, game, area=None):
        self.dir = os.path.join(game, "out", "capital")
        where = [str(v) for v in area or ()]
        if (link.HOST, link.PORT) != ("127.0.0.1", 27015):
            where.append(f"{link.HOST}:{link.PORT}")
        self.key = "" if not where else "-" + hashlib.sha1(
            ",".join(where).encode()).hexdigest()[:8]

    def _path(self, name):
        return os.path.join(self.dir, name)

    def _load(self, name, default):
        try:
            with open(self._path(name), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    def _save(self, name, data):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._path(name) + f".{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, self._path(name))

    @property
    def config(self):
        c = self._load("park.json", {})
        return {"parked": list(c.get("parked") or []), "weights": dict(c.get("weights") or {})}

    def save_config(self, c):
        self._save("park.json", c)

    def last(self):
        return self._load(f"last{self.key}.json", {})

    def history(self):
        out = []
        try:
            with open(self._path(f"history{self.key}.jsonl"), encoding="utf-8") as fh:
                for ln in fh:
                    try:
                        h = json.loads(ln)
                        out.append((int(h["tick"]), float(h["working"]), float(h["total"])))
                    except (ValueError, KeyError, TypeError):
                        pass
        except OSError:
            pass
        return out

    def keep(self, cap):
        self._save(f"last{self.key}.json", {"tick": cap.tick, "crafts": cap.crafts,
                                            "robots": cap.robot_share})
        hist = [h for h in self.history() if h[0] < cap.tick]     # a reloaded save starts over
        hist.append((cap.tick, cap.working, cap.total))
        hist = hist[-2000:]
        os.makedirs(self.dir, exist_ok=True)
        tmp = self._path(f"history{self.key}.jsonl") + f".{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            for t, w, tot in hist:
                fh.write(json.dumps({"tick": t, "working": round(w, 1), "total": round(tot, 1)})
                         + "\n")
        os.replace(tmp, self._path(f"history{self.key}.jsonl"))


def run(r, game, cat, force, surface, area=None, brief=False, top=5):
    """Read, score against the last read, keep this one -> the report's lines (one for brief)."""
    store = Store(game, area)
    cfg = store.config
    parked = [p["area"] for p in cfg["parked"]]
    whys = [p.get("why") or "parked" for p in cfg["parked"]]
    raw = read(r, force, surface, area, parked)
    last = store.last()
    if int(last.get("tick") or 0) >= int(raw.get("tick") or 0):
        last = {}                       # a reloaded save: the crafts count from here
    cap = score(raw, cat, last, cfg["weights"], whys)
    g = growth(store.history(), cap.tick, cap.working)
    store.keep(cap)
    return [line(cap, g)] if brief else lines(cap, g, surface, top)
