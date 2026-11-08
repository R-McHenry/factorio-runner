"""fkcore.walk: the net walker, world mode (PLAN-3.0 §3f, 2026-11-01).

What a machine in the game is waiting on, upstream and downstream along its nets, to diagnose a
starved or blocked line. ``fk walk X,Y`` (a machine there), ``fk walk n7`` (a net), or ``fk walk
X0,Y0,X1,Y1`` (every net in the area).

World mode: the graph comes from the entities alone, no design.

1. **Read** (:func:`lua_read`, one game call): every entity in the area with its live state:
   status (``defines.entity_status``), recipe, belt contents per transport line, an inserter's
   pickup and drop targets as the game has them, a drill's drop target, chest contents, fluid
   amounts; and per prototype seen, its type and size, so a name the catalog lacks (Space Age,
   a mod) still makes a node (:func:`catalog_for`).
2. **Graph:** :func:`fkcore.connect.graph` over the read, the toolkit's own carrier graph (belt,
   side-load, underground, pickup, drop, mine edges; fluid joins).
3. **Nets** (:func:`infer`): each connected run of belts (belt, side-load, underground edges,
   splitters inside) is a net, and each connected run of pipes is a fluid net. A machine whose
   inserter drops onto a net (or a drill that mines onto it) drives it; a machine whose inserter
   picks from it consumes it. An inserter between two machines is a hand link, between two nets a
   net feeding a net. Items come off the belts and the drivers' recipes. Nets get made-up names
   ``n1``, ``n2`` ... (``f1`` for fluids) in key order.
4. **Look** (:func:`marks`): a machine that is short of an input, has its output full, or has no
   power; a net that is empty, backed up (both lanes nearly full), or has one lane empty while the
   other carries; a belt that runs into or out of the read area.
5. **Tree** (:func:`tree`): the root, then its nets up (``--up N`` levels) and down (``--down
   N``), and where the flow stops: the furthest starved machine upstream whose own inputs are not
   starved in turn, with the reason.

``--save FILE`` keeps the read and ``--from FILE`` walks a kept read without the game (the unit
tests do). The read also says how far the graph's inserter edges agree with the game's own
pickup and drop targets (:func:`agreement`). Pure apart from :func:`read`; stdlib only.
"""
from __future__ import annotations

import json
import math
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field

from . import arms as armsmod
from . import connect
from .catalog import Catalog
from .fkp import _natural_key as natural_key

__all__ = ["lua_read", "read", "catalog_for", "entities", "World", "Net", "infer", "marks",
           "tree", "summary", "agreement", "find_root", "STARVED", "BLOCKED", "NO_POWER"]

BELT_KINDS = frozenset({"transport-belt", "underground-belt", "splitter", "loader",
                        "loader-1x1", "linked-belt"})
PIPE_KINDS = frozenset({"pipe", "pipe-to-ground", "pump"})
MACHINE_KINDS = connect.HOLDER_TYPES | {"rocket-silo", "chemical-plant", "oil-refinery",
                                        "agricultural-tower", "asteroid-collector"}
_JOIN = frozenset({"belt", "curve", "sideload", "ug_sideload", "ug"})
#: game statuses (defines.entity_status names) a mark reads
STARVED = frozenset({"no_ingredients", "item_ingredient_shortage", "fluid_ingredient_shortage",
                     "no_input_fluid", "no_fuel", "waiting_for_source_items", "no_minable_resources",
                     "missing_required_fluid", "no_ammo"})
BLOCKED = frozenset({"full_output", "waiting_for_space_in_destination",
                     "full_burnt_result_output", "item_production_overload"})
NO_POWER = frozenset({"no_power", "low_power", "low_input_fluid"})
WORKING = frozenset({"working", "normal", "low_temperature", "preparing_rocket_for_launch",
                     "waiting_to_launch_rocket", "launching_rocket"})
LANE_CAP = 4          # items a lane of one belt tile holds (2.0)
FULL = 0.85           # a lane this full is backed up
#: rows a silo or a landing pad spawns: never built, never inserter targets
UNBUILT = ("rocket-silo-rocket", "rocket-silo-rocket-shadow", "cargo-pod")
_SKIP = ("resource", "fish", "character", "corpse", "character-corpse", "item-entity",
         "item-request-proxy", "construction-robot", "logistic-robot", "combat-robot",
         "highlight-box", "speech-bubble", "explosion", "smoke-with-trigger", "particle-source",
         "projectile", "beam", "stream", "sticker", "fire", "deconstructible-tile-proxy",
         "tile-ghost", "entity-ghost", "tree", "simple-entity", "cliff", "unit", "unit-spawner",
         "turret", "spider-unit", "segmented-unit", "segment", "asteroid", "rail-remnants",
         "lightning", "fluid-turret", "plant") + UNBUILT


def _q(text):
    return json.dumps(str(text))


def lua_read(surface, area, tag):
    """Lua that reads ``area`` (x0, y0, x1, y1) on ``surface`` into ``fk5/<tag>.json``:
    ``{tick, area, rows, protos, recipes}``, and replies with the row count. Reads only."""
    x0, y0, x1, y1 = area
    skip = ", ".join(_q(t) for t in _SKIP)
    return f"""
local s = game.get_surface({_q(surface)})
if not s then rcon.print('no surface') return end
local st = {{}}
for k, v in pairs(defines.entity_status) do st[v] = k end
local rows, protos, recipes = {{}}, {{}}, {{}}
local function contents(inv)
  local c = {{}}
  if inv then for _, x in pairs(inv.get_contents()) do c[x.name] = (c[x.name] or 0) + x.count end end
  return next(c) and c or nil
end
local function lines(e)
  local r, n = {{}}, {{}}
  for i = 1, e.get_max_transport_line_index() do
    local c = {{}}
    local l = e.get_transport_line(i)
    for _, x in pairs(l.get_contents()) do c[x.name] = (c[x.name] or 0) + x.count end
    r[i] = c
    n[i] = #l.get_detailed_contents()
  end
  return r, n
end
local function recipe(r)
  if r and not recipes[r.name] then
    local ing, prod = {{}}, {{}}
    for _, x in pairs(r.ingredients) do ing[#ing + 1] = x.name end
    for _, x in pairs(r.products) do prod[#prod + 1] = x.name end
    recipes[r.name] = {{ing = ing, prod = prod}}
  end
end
for _, e in pairs(s.find_entities_filtered{{area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}}, type = {{{skip}}}, invert = true}}) do
  local t = e.type
  local row = {{n = e.name, t = t, x = e.position.x, y = e.position.y, d = e.direction, u = e.unit_number}}
  local bb = e.bounding_box
  row.box = {{bb.left_top.x, bb.left_top.y, bb.right_bottom.x, bb.right_bottom.y}}
  local ok, v = pcall(function() return e.status end)
  if ok and v then row.st = st[v] end
  if e.quality and e.quality.name ~= 'normal' then row.q = e.quality.name end
  if t == 'assembling-machine' or t == 'furnace' or t == 'rocket-silo' then
    local ok2, r = pcall(e.get_recipe)
    if ok2 and r then row.rc = r.name recipe(r)
    elseif t == 'furnace' and e.previous_recipe then row.rc = e.previous_recipe.name.name or e.previous_recipe.name end
    row.cp = e.crafting_progress
    row.inp = contents(e.get_inventory(defines.inventory.crafter_input))
    row.out = contents(e.get_inventory(defines.inventory.crafter_output))
  elseif t == 'transport-belt' or t == 'underground-belt' or t == 'splitter' or t == 'loader' or t == 'loader-1x1' or t == 'linked-belt' then
    row.ln, row.ls = lines(e)
    if t == 'underground-belt' then row.ug = e.belt_to_ground_type end
    if t == 'splitter' then
      row.sp = {{e.splitter_input_priority, e.splitter_output_priority, e.splitter_filter and e.splitter_filter.name and e.splitter_filter.name.name or (e.splitter_filter and e.splitter_filter.name)}}
    end
  elseif t == 'inserter' then
    row.pk = {{e.pickup_position.x, e.pickup_position.y}}
    row.dp = {{e.drop_position.x, e.drop_position.y}}
    if e.pickup_target then row.pt = e.pickup_target.unit_number end
    if e.drop_target then row.dt = e.drop_target.unit_number end
    if e.held_stack and e.held_stack.valid_for_read then row.hs = e.held_stack.name end
    if e.use_filters then row.fl = true end
  elseif t == 'mining-drill' then
    row.dp = {{e.drop_position.x, e.drop_position.y}}
    if e.drop_target then row.dt = e.drop_target.unit_number end
    if e.mining_target then row.res = e.mining_target.name end
  elseif t == 'container' or t == 'logistic-container' or t == 'infinity-container' then
    row.it = contents(e.get_inventory(defines.inventory.chest))
  elseif t == 'lab' then
    row.inp = contents(e.get_inventory(defines.inventory.lab_input))
  end
  if #e.fluidbox > 0 then
    local f = {{}}
    for i = 1, #e.fluidbox do local b = e.fluidbox[i] if b then f[b.name] = (f[b.name] or 0) + b.amount end end
    if next(f) then row.fl_ = f end
  end
  if not protos[e.name] then
    local p = e.prototype
    protos[e.name] = {{type = t, w = p.tile_width, h = p.tile_height, ud = t == 'underground-belt' and p.max_underground_distance or nil}}
  end
  rows[#rows + 1] = row
end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({{tick = game.tick, surface = {_q(surface)}, area = {{{x0}, {y0}, {x1}, {y1}}}, rows = rows, protos = protos, recipes = recipes}}), false)
rcon.print(#rows)
"""


def read(r, surface, area, tag="walk"):
    """-> the read of ``area`` (one game call, file through script-output)."""
    from . import link
    tag = f"{tag}-{os.getpid()}"
    return link.read_output(r, lua_read(surface, area, tag), tag, "walk read", keep=False)


# -- the graph ------------------------------------------------------------------------------------
def catalog_for(dump, cat):
    """``cat``, plus a row for each prototype of the read that it lacks (type and size from the
    game), so Space Age and modded entities still make nodes. -> (catalog, [added names])."""
    added = sorted(n for n in dump.get("protos") or {} if not cat.has_entity(n))
    if not added:
        return cat, []
    rows = [cat.entity(n) for n in cat._entities]
    for n in added:
        p = dump["protos"][n]
        row = {"name": n, "type": p.get("type"), "tile_width": p.get("w") or 1,
               "tile_height": p.get("h") or 1}
        if p.get("ud"):
            row["max_distance"] = p["ud"]
        rows.append(row)
    return Catalog(cat.version, {r["name"]: r for r in rows}, cat._recipes, cat._fluid), added


@dataclass
class Ent:
    key: str
    name: str
    x: float
    y: float
    dir: int
    attrs: dict
    tiles: tuple
    row: dict


def _tiles(box):
    x0, y0, x1, y1 = box
    return tuple((i, j) for i in range(math.floor(x0 + 0.01), math.ceil(x1 - 0.01))
                 for j in range(math.floor(y0 + 0.01), math.ceil(y1 - 0.01)))


_ABBR = {"assembling-machine": "asm", "furnace": "fur", "mining-drill": "drill", "lab": "lab",
         "container": "chest", "logistic-container": "chest", "inserter": "ins",
         "transport-belt": "belt", "underground-belt": "ug", "splitter": "split", "pipe": "pipe",
         "pipe-to-ground": "ptg", "chemical-plant": "chem", "oil-refinery": "refy",
         "boiler": "boiler", "rocket-silo": "silo"}


def entities(dump):
    """[Ent] of a read, keyed ``asm@12.5,30.5`` style (type, then position), in read order."""
    out = []
    for row in dump.get("rows") or ():
        if row.get("t") in ("entity-ghost",) + UNBUILT:      # reads kept before the skip
            continue
        attrs = {}
        if row.get("ug"):
            attrs["ug"] = {"input": "in", "output": "out"}.get(row["ug"], row["ug"])
        if row.get("rc"):
            attrs["recipe"] = row["rc"]
        if row.get("res"):
            attrs["resource"] = row["res"]
        if row.get("sp") and row["t"] == "splitter":
            pr = row["sp"]
            conf = {k: v for k, v in (("input_priority", pr[0] if len(pr) > 0 else None),
                                      ("output_priority", pr[1] if len(pr) > 1 else None))
                    if v in ("left", "right")}
            if conf:
                attrs["config"] = json.dumps(conf)
        if row.get("t") == "inserter" and row.get("pk") and row.get("dp"):
            # 3.0: the game's own vectors (Bob's adjustable inserters), whole tiles from its own
            off = lambda p: (round(float(p[0]) - float(row["x"])), round(float(p[1]) - float(row["y"])))
            attrs.update(armsmod.canon(row["n"], int(row.get("d") or 0), off(row["pk"]),
                                       off(row["dp"])))
        key = f"{_ABBR.get(row['t'], row['t'])}@{_num(row['x'])},{_num(row['y'])}"
        tiles = _tiles(row["box"]) if row.get("box") else ()
        out.append(Ent(key, row["n"], float(row["x"]), float(row["y"]), int(row.get("d") or 0),
                       attrs, tiles, row))
    return out


def _num(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:g}"


# -- nets -----------------------------------------------------------------------------------------
@dataclass
class Net:
    name: str
    fluid: bool
    carriers: list                        # node keys
    drivers: list = field(default_factory=list)       # machine keys (and nets: "net:n3")
    consumers: list = field(default_factory=list)
    items: dict = field(default_factory=dict)         # item -> count on it now
    lanes: tuple = (0, 0)                 # items on the left and right lanes (belts only)
    tiles: int = 0                        # belt tiles (for fill)
    enters: list = field(default_factory=list)        # belts fed from outside the read area
    leaves: list = field(default_factory=list)        # belts that run out of it
    made: set = field(default_factory=set)            # what its drivers' recipes make
    wanted: set = field(default_factory=set)          # what its consumers' recipes take

    def fill(self):
        if not self.tiles:
            return None
        cap = LANE_CAP * self.tiles
        return self.lanes[0] / cap, self.lanes[1] / cap

    def label(self):
        items = ", ".join(sorted(self.items, key=lambda i: -self.items[i])[:3]) or \
            ", ".join(sorted(self.made)[:3]) or "nothing on it"
        return f"{self.name} {items}"


@dataclass
class World:
    dump: dict
    graph: object
    ents: dict                            # key -> Ent
    nets: dict                            # name -> Net
    net_of: dict                          # carrier key -> net name
    ins: dict                             # machine key -> [(net name | "hand:KEY", via inserter)]
    outs: dict                            # machine key -> [(net name | "hand:KEY", via)]
    feeds: dict                           # net -> [net] it feeds through an inserter or splitter
    added: list                           # prototypes taken from the game, not the catalog
    recipes: dict

    def machine(self, key):
        return self.ents.get(key)

    def status(self, key):
        return (self.ents[key].row.get("st") or "") if key in self.ents else ""

    def label(self, key):
        e = self.ents[key]
        rc = e.row.get("rc") or e.row.get("res")
        q = f" ({e.row['q']})" if e.row.get("q") else ""
        return f"{e.name}{q} {_num(e.x)},{_num(e.y)}" + (f" [{rc}]" if rc else "")


def _uf(keys):
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    return parent, find


def _lanes(row):
    """(left, right) of a belt-ish entity's transport lines (odd lines left, even right): the
    item stacks on them where the read has them (``ls``: Space Age stacks up to 4 items on a
    belt), else the items."""
    ls = row.get("ls")
    if ls:
        if isinstance(ls, dict):
            ls = [ls[k] for k in sorted(ls, key=int)]
        return (sum(v for i, v in enumerate(ls) if i % 2 == 0),
                sum(v for i, v in enumerate(ls) if i % 2 == 1))
    ln = row.get("ln") or []
    if isinstance(ln, dict):
        ln = [ln[k] for k in sorted(ln, key=int)]
    left = sum(sum(c.values()) for i, c in enumerate(ln) if c and i % 2 == 0)
    right = sum(sum(c.values()) for i, c in enumerate(ln) if c and i % 2 == 1)
    return left, right


def _items(row):
    out = defaultdict(int)
    ln = row.get("ln") or []
    if isinstance(ln, dict):
        ln = list(ln.values())
    for c in ln:
        for k, v in (c or {}).items():
            out[k] += v
    for k, v in (row.get("fl_") or {}).items():
        out[k] += round(v)
    return out


def infer(dump, cat):
    """The world of a read: graph, nets, and each machine's ins and outs."""
    wcat, added = catalog_for(dump, cat)
    ents = entities(dump)
    by_key = {e.key: e for e in ents}
    g = connect.graph(ents, wcat)
    kind = {k: n.kind for k, n in g.nodes.items()}
    belts = [k for k in g.nodes if kind[k] in BELT_KINDS]
    pipes = [k for k in g.nodes if kind[k] in PIPE_KINDS]
    parent, find = _uf(belts + pipes)
    for a, b, via in g.edges():
        if via in _JOIN and a in parent and b in parent:
            parent[find(a)] = find(b)
    for (a, _ab), (b, _bb), _via in g.joins:
        if a in parent and b in parent and kind[a] in PIPE_KINDS and kind[b] in PIPE_KINDS:
            parent[find(a)] = find(b)
    groups = defaultdict(list)
    for k in belts + pipes:
        groups[find(k)].append(k)
    nets, net_of = {}, {}
    order = sorted(groups.values(), key=lambda ks: natural_key(min(ks, key=natural_key)))
    nb = nf = 0
    for ks in order:
        fluid = kind[ks[0]] in PIPE_KINDS
        if fluid:
            nf += 1
            name = f"f{nf}"
        else:
            nb += 1
            name = f"n{nb}"
        net = Net(name, fluid, sorted(ks, key=natural_key))
        for k in ks:
            net_of[k] = name
            row = by_key[k].row
            for i, v in _items(row).items():
                net.items[i] = net.items.get(i, 0) + v
            if not fluid:
                left, right = _lanes(row)
                net.lanes = (net.lanes[0] + left, net.lanes[1] + right)
                # capacity: a belt tile, an underground end and the tiles under it (counted on
                # its input), a splitter's two tiles
                net.tiles += {"transport-belt": 1, "splitter": 2}.get(kind[k], 1)
                if kind[k] == "underground-belt" and k in g.pairs and g.nodes[k].ug == "in":
                    a, b = g.nodes[k].tiles[0], g.nodes[g.pairs[k]].tiles[0]
                    net.tiles += max(0, abs(a[0] - b[0]) + abs(a[1] - b[1]) - 1)
        nets[name] = net
    _ends(g, nets, net_of, dump.get("area"))
    ins, outs, feeds = defaultdict(list), defaultdict(list), defaultdict(list)

    def link(src, dst, via):
        sm, dm = src in net_of, dst in net_of
        if sm and dm:
            a, b = net_of[src], net_of[dst]
            if a != b and b not in feeds[a]:
                feeds[a].append(b)
                nets[a].consumers.append("net:" + b)
                nets[b].drivers.append("net:" + a)
        elif sm:
            n = net_of[src]
            if dst not in nets[n].consumers:
                nets[n].consumers.append(dst)
            ins[dst].append((n, via))
        elif dm:
            n = net_of[dst]
            if src not in nets[n].drivers:
                nets[n].drivers.append(src)
            outs[src].append((n, via))
        elif src != dst:
            outs[src].append(("hand:" + dst, via))
            ins[dst].append(("hand:" + src, via))

    for k, n in g.nodes.items():
        if n.kind == "inserter":
            src = [a for a, v in g.pred.get(k, ()) if v == "pickup"]
            dst = [b for b, v in g.succ.get(k, ()) if v == "drop"]
            for s in src:
                for d in dst:
                    link(s, d, k)
        elif n.kind == "mining-drill":
            for d, v in g.succ.get(k, ()):
                if v == "mine":
                    link(k, d, k)
    for (a, _ab), (b, _bb), _via in g.joins:
        if (a in net_of) == (b in net_of):
            continue
        pipe, mach = (a, b) if a in net_of else (b, a)
        moves_in = any(m[0][0] == mach for m in g.fluid.get((pipe, _ab if pipe == a else _bb), ()))
        moves_out = any(m[0][0] == pipe for m in g.fluid.get((mach, _bb if pipe == a else _ab), ()))
        if moves_out:
            link(mach, pipe, "fluid")
        if moves_in or not moves_out:
            link(pipe, mach, "fluid")
    recipes = dump.get("recipes") or {}
    for net in nets.values():
        for d in net.drivers:
            net.made |= set(_products(by_key.get(d), recipes, cat))
        for c in net.consumers:
            net.wanted |= set(_ingredients(by_key.get(c), recipes, cat))
    return World(dump, g, by_key, nets, net_of, dict(ins), dict(outs), dict(feeds), added,
                 recipes)


def _ends(g, nets, net_of, area):
    """Mark the belts of each net that take items from, or give them to, a tile outside the
    read area (a cut, not a real end)."""
    if not area:
        return
    x0, y0, x1, y1 = area

    def outside(t):
        return not (x0 <= t[0] < x1 and y0 <= t[1] < y1)
    for k, name in net_of.items():
        n = g.nodes[k]
        step = connect._STEP.get(n.dir)
        if step is None or n.kind != "transport-belt":
            continue
        tx, ty = n.tiles[0]
        if outside((tx - step[0], ty - step[1])) and not g.pred.get(k):
            nets[name].enters.append(k)
        if outside((tx + step[0], ty + step[1])) and not g.succ.get(k):
            nets[name].leaves.append(k)


def _recipe(e, recipes, cat):
    if e is None:
        return None, None
    rc = e.row.get("rc")
    if not rc:
        return None, None
    if rc in recipes:
        return recipes[rc].get("ing") or [], recipes[rc].get("prod") or []
    try:
        r = cat.recipe(rc)
    except KeyError:
        return [], [rc]
    return [i["name"] for i in r.get("ingredients", [])], [p["name"] for p in r.get("products", [])]


def _products(e, recipes, cat):
    if e is None:
        return []
    if e.row.get("res"):
        return [e.row["res"]]
    return _recipe(e, recipes, cat)[1] or []


def _ingredients(e, recipes, cat):
    return _recipe(e, recipes, cat)[0] or []


# -- marks ----------------------------------------------------------------------------------------
def net_mark(net):
    """What is wrong with a net now, or ''."""
    if net.fluid:
        return "empty" if not net.items else ""
    total = sum(net.items.values())
    fill = net.fill()
    if total == 0:
        return "EMPTY"
    if fill and min(fill) >= FULL:
        return "BACKED UP"
    if fill and fill[0] >= FULL and net.lanes[1] == 0:
        return "left lane backed up, right lane empty"
    if fill and fill[1] >= FULL and net.lanes[0] == 0:
        return "right lane backed up, left lane empty"
    if net.tiles >= 4 and (net.lanes[0] == 0) != (net.lanes[1] == 0):
        return ("left" if net.lanes[0] == 0 else "right") + " lane empty"
    return ""


def machine_mark(w, key):
    st = w.status(key)
    if st in STARVED:
        return "STARVED " + st.replace("_", " ")
    if st in BLOCKED:
        return "BLOCKED " + st.replace("_", " ")
    if st in NO_POWER:
        return "POWER " + st.replace("_", " ")
    if st in ("frozen", "no_recipe"):            # Aquilo's cold; a crusher left unset
        return st.replace("_", " ").upper()
    if st and st not in WORKING:
        return st.replace("_", " ")
    return ""


def marks(w):
    """{key or net name: mark} for everything with a mark."""
    out = {}
    for k in w.ents:
        if w.graph.nodes.get(k) and w.graph.nodes[k].kind in MACHINE_KINDS:
            m = machine_mark(w, k)
            if m:
                out[k] = m
    for name, net in w.nets.items():
        m = net_mark(net)
        if m:
            out[name] = m
    return out


# -- the tree -------------------------------------------------------------------------------------
def find_root(w, what):
    """A machine key, a net name, or None, for ``what``: ``X,Y``, an entity key or a net name."""
    if what in w.nets or what in w.ents:
        return what
    try:
        x, y = (float(v) for v in what.split(","))
    except ValueError:
        return None
    t = (math.floor(x), math.floor(y))
    k = w.graph.at.get(t)
    if k is None:
        return None
    if k in w.net_of:
        return w.net_of[k]
    if w.graph.nodes[k].kind == "inserter":
        hands = [b for b, v in w.graph.succ.get(k, ()) if v == "drop"]
        return (w.net_of.get(hands[0]) or hands[0]) if hands else k
    return k


def _net_line(w, name):
    net = w.nets[name]
    bits = [net.label()]
    if net.fluid:
        bits.append(f"pipes {len(net.carriers)}")
        if net.items:
            bits.append(", ".join(f"{k} {v:.0f}" for k, v in sorted(net.items.items())))
    else:
        f = net.fill()
        bits.append(f"belts {net.tiles}")
        if f:
            bits.append(f"L {net.lanes[0]}/{LANE_CAP * net.tiles} R {net.lanes[1]}/"
                        f"{LANE_CAP * net.tiles}")
    nd = sum(1 for d in net.drivers if not d.startswith("net:"))
    nc = sum(1 for c in net.consumers if not c.startswith("net:"))
    bits.append(f"{nd} in, {nc} out")
    if net.enters:
        bits.append(f"enters from outside at {_where(w, net.enters[0])}")
    if net.leaves:
        bits.append(f"runs out of the area at {_where(w, net.leaves[0])}")
    m = net_mark(net)
    return " | ".join(bits) + (f"  ** {m}" if m else "")


def _where(w, key):
    n = w.graph.nodes[key]
    return f"{_num(n.x)},{_num(n.y)}"


def _machine_line(w, key):
    m = machine_mark(w, key)
    st = w.status(key) or "?"
    e = w.ents[key]
    extra = ""
    if e.row.get("inp"):
        extra = " has " + ", ".join(f"{k} {v}" for k, v in sorted(e.row["inp"].items()))
    return f"{w.label(key)} {st}" + extra + (f"  ** {m}" if m else "")


def tree(w, root, up=2, down=1, width=6):
    """Lines: the root, its nets up ``up`` levels and down ``down`` levels, then the stops."""
    lines = []
    seen = set()
    if root in w.nets:
        lines.append("net " + _net_line(w, root))
        _up_net(w, root, up, "  ", lines, seen, width)
        _down_net(w, root, down, "  ", lines, seen, width)
    else:
        lines.append(_machine_line(w, root))
        _up_machine(w, root, up, "  ", lines, seen, width)
        _down_machine(w, root, down, "  ", lines, seen, width)
    stops = stop_points(w, root, up)
    if stops:
        lines.append("flow stops:")
        lines += [f"  {s}" for s in stops]
    else:
        lines.append("flow stops: nowhere upstream within the walk")
    return lines


def _more(items, width, pad, lines, w=None):
    """The first ``width`` of ``items``; machines alike (name, recipe, status) fold into the
    first of them, which is then shown as ``6 x ...``."""
    if w is not None:
        groups, order = {}, []
        for k in items:
            sig = k if k.startswith("net:") or k not in w.ents else \
                (w.ents[k].name, w.ents[k].row.get("rc"), w.status(k))
            if sig not in groups:
                groups[sig] = []
                order.append(sig)
            groups[sig].append(k)
        w.folded = getattr(w, "folded", {})
        for sig in order:
            w.folded[groups[sig][0]] = groups[sig]
        items = [groups[sig][0] for sig in order]
    if len(items) > width:
        lines.append(f"{pad}... and {len(items) - width} more")
    return items[:width]


def _many(w, key):
    ks = getattr(w, "folded", {}).get(key) or [key]
    if len(ks) == 1:
        return _machine_line(w, key)
    xs = [w.ents[k] for k in ks]
    return (f"{len(ks)} x " + _machine_line(w, key)
            + f"  (at {_num(xs[0].x)},{_num(xs[0].y)} .. {_num(xs[-1].x)},{_num(xs[-1].y)})")


def _up_machine(w, key, depth, pad, lines, seen, width):
    if depth <= 0 or key in seen:
        return
    seen.add(key)
    ins = w.ins.get(key, [])
    if not ins and w.ents[key].row.get("t") == "logistic-container":
        lines.append(f"{pad}<- robots (the logistic network: the walker can't follow it)")
        return
    if not ins:
        lines.append(f"{pad}<- nothing feeds it in the area")
    done = set()
    for n, _via in ins:
        if n in done:
            continue
        done.add(n)
        if n.startswith("hand:"):
            src = n[5:]
            lines.append(f"{pad}<- hand from {_machine_line(w, src)}")
            _up_machine(w, src, depth - 1, pad + "   ", lines, seen, width)
        else:
            lines.append(f"{pad}<- {_net_line(w, n)}")
            _up_net(w, n, depth, pad + "   ", lines, seen, width)


def _up_net(w, name, depth, pad, lines, seen, width):
    if name in seen:
        return
    seen.add(name)
    net = w.nets[name]
    drivers = _more(net.drivers, width, pad, lines, w)
    if not net.drivers and not net.enters:
        lines.append(f"{pad}<- no driver in the area")
    for d in drivers:
        if d.startswith("net:"):
            lines.append(f"{pad}<- fed by net {_net_line(w, d[4:])}")
            if depth > 1:
                _up_net(w, d[4:], depth - 1, pad + "   ", lines, seen, width)
        else:
            lines.append(f"{pad}<- {_many(w, d)}")
            _up_machine(w, d, depth - 1, pad + "   ", lines, seen, width)


def _down_machine(w, key, depth, pad, lines, seen, width):
    if depth <= 0:
        return
    outs = w.outs.get(key, [])
    if not outs:
        lines.append(f"{pad}-> its output goes nowhere in the area")
    done = set()
    for n, _via in outs:
        if n in done:
            continue
        done.add(n)
        if n.startswith("hand:"):
            lines.append(f"{pad}-> hand to {_machine_line(w, n[5:])}")
            _down_machine(w, n[5:], depth - 1, pad + "   ", lines, seen | {key}, width)
        else:
            lines.append(f"{pad}-> {_net_line(w, n)}")
            _down_net(w, n, depth, pad + "   ", lines, seen | {key}, width)


def _down_net(w, name, depth, pad, lines, seen, width):
    net = w.nets[name]
    if not net.consumers and not net.leaves:
        lines.append(f"{pad}-> no consumer in the area")
    for c in _more(net.consumers, width, pad, lines, w):
        if c.startswith("net:"):
            lines.append(f"{pad}-> feeds net {_net_line(w, c[4:])}")
            if depth > 1:
                _down_net(w, c[4:], depth - 1, pad + "   ", lines, seen, width)
        else:
            lines.append(f"{pad}-> {_many(w, c)}")
            if c not in seen:
                _down_machine(w, c, depth - 1, pad + "   ", lines, seen | {c}, width)


def _sick(w, k):
    if k in w.nets:
        return net_mark(w.nets[k]) == "EMPTY"
    return k in w.ents and machine_mark(w, k).startswith("STARVED")


def _srcs(w, k):
    if k in w.nets:
        return [x[4:] if x.startswith("net:") else x for x in w.nets[k].drivers]
    return list(dict.fromkeys(n[5:] if n.startswith("hand:") else n
                              for n, _v in w.ins.get(k, [])))


def _short_net(w, name):
    net = w.nets[name]
    bits = [f"{name} {sum(net.items.values())} items"]
    m = net_mark(net)
    if m:
        bits.append(m.lower())
    if net.enters and not [d for d in net.drivers if not d.startswith("net:")]:
        bits.append("fed only from outside the area")
    return ", ".join(bits)


def stop_points(w, root, depth):
    """Where the flow stops, as lines. Upstream of ``root`` (``depth`` machine levels): the head
    of each starved chain, a starved machine or an empty net whose own suppliers are not starved
    in turn, with what it is short of and the state of its inputs; alike heads fold into one
    line. For a root whose output is full: downstream, the first net that is backed up and why."""
    if root in w.ents and machine_mark(w, root).startswith("BLOCKED"):
        return _down_stops(w, root, depth)
    heads, order = {}, []
    seen = set()
    todo = deque([(root, 0)])
    while todo:
        k, d = todo.popleft()
        if k in seen:
            continue
        seen.add(k)
        srcs = [s for s in _srcs(w, k) if s in w.nets or s in w.ents]
        sick = [s for s in srcs if _sick(w, s)]
        if _sick(w, k) and not sick:
            if k in w.nets:
                net = w.nets[k]
                why = ("no driver in the area" if not srcs and not net.enters else
                       "fed only from outside the area" if not srcs else
                       "its drivers are " + ", ".join(sorted({w.status(u) or "a net"
                                                                for u in srcs})))
                sig = ("net", k)
                text = f"net {net.label()} is empty: {why}"
            else:
                e = w.ents[k]
                need = _missing(w, k)
                feeds = "; ".join(_short_net(w, s) if s in w.nets else
                                  f"{w.label(s)} {w.status(s)}" for s in srcs) or \
                    "nothing feeds it in the area"
                sig = (e.name, e.row.get("rc"), w.status(k), need, feeds)
                text = (f"{e.name} [{e.row.get('rc') or e.row.get('res') or ''}] "
                        f"{machine_mark(w, k)}" + (f", short of {need}" if need else "")
                        + f"; inputs: {feeds}")
            if sig not in heads:
                heads[sig] = [text, []]
                order.append(sig)
            heads[sig][1].append(k)
        if d < depth:
            nxt = sick if _sick(w, k) else srcs
            for s in nxt:
                todo.append((s, d + (0 if s in w.nets else 1)))
    out = []
    for sig in order:
        text, keys = heads[sig]
        out.append((f"{len(keys)} x " if len(keys) > 1 else "") + text
                   + ("" if keys[0] in w.nets else f" (at {_where_e(w, keys[0])}"
                      + (f" .. {_where_e(w, keys[-1])}" if len(keys) > 1 else "") + ")"))
    return out


def _where_e(w, k):
    e = w.ents[k]
    return f"{_num(e.x)},{_num(e.y)}"


def _down_stops(w, root, depth):
    """Why ``root``'s output is full: each output net that is backed up, with its consumers'
    states, followed while those consumers are blocked in turn."""
    out, seen = [], set()
    todo = deque([(root, 0)])
    while todo:
        k, d = todo.popleft()
        if k in seen:
            continue
        seen.add(k)
        nexts = [n[5:] if n.startswith("hand:") else n for n, _v in w.outs.get(k, [])]
        if not nexts and k != root:
            out.append(f"{w.label(k)} {machine_mark(w, k)}: its output goes nowhere in the area")
        if not w.outs.get(k) and k == root:
            out.append(f"{w.label(k)}: its output goes nowhere in the area")
        for n in dict.fromkeys(nexts):
            if n in w.nets:
                net = w.nets[n]
                cons = [c for c in net.consumers if not c.startswith("net:")]
                states = sorted({w.status(c) or "?" for c in cons})
                mark = net_mark(net) or "moving"
                if "BACKED" in mark or "backed" in mark:
                    if not cons and not net.leaves:
                        out.append(f"net {net.label()} {mark.lower()}: nothing takes from it")
                    elif not cons:
                        out.append(f"net {net.label()} {mark.lower()}: its consumers are outside "
                                   "the area")
                    else:
                        blocked = [c for c in cons if machine_mark(w, c).startswith("BLOCKED")]
                        out.append(f"net {net.label()} {mark.lower()}: {len(cons)} consumers, "
                                   + ", ".join(states))
                        if d < depth:
                            for c in blocked:
                                todo.append((c, d + 1))
            elif n in w.ents:
                if machine_mark(w, n).startswith("BLOCKED") and d < depth:
                    todo.append((n, d + 1))
                elif n != root:
                    out.append(f"hand to {w.label(n)} {w.status(n) or ''}".rstrip())
    return out


def _missing(w, key):
    e = w.ents[key]
    ing = _ingredients(e, w.recipes, None) if e.row.get("rc") in w.recipes else []
    have = e.row.get("inp") or {}
    short = [i for i in ing if not have.get(i)]
    return ", ".join(short)


def summary(w):
    """Lines: what the read holds and every net with its mark (area mode)."""
    mk = marks(w)
    nmach = sum(1 for k, n in w.graph.nodes.items() if n.kind in MACHINE_KINDS)
    st = defaultdict(int)
    for k, n in w.graph.nodes.items():
        if n.kind in MACHINE_KINDS:
            m = machine_mark(w, k)
            st[m.split(" ")[0] if m.startswith(("STARVED", "BLOCKED", "POWER")) else
               (w.status(k) or "?") if m else "ok"] += 1
    lines = [f"walk: {len(w.ents)} entities, {nmach} machines "
             f"({', '.join(f'{k} {v}' for k, v in sorted(st.items()))}), "
             f"{sum(not n.fluid for n in w.nets.values())} belt nets, "
             f"{sum(n.fluid for n in w.nets.values())} fluid nets"]
    if w.added:
        lines.append(f"not in the catalog, taken from the game: {', '.join(w.added)}")
    if w.graph.problems:
        lines.append(f"graph problems: {len(w.graph.problems)} (first: {w.graph.problems[0]})")
    ag = agreement(w)
    if ag:
        lines.append(ag)
    for name in sorted(w.nets, key=natural_key):
        net = w.nets[name]
        if len(net.carriers) < 2 and not net.drivers and not net.consumers:
            continue
        lines.append(_net_line(w, name))
    return lines


_BELT_VIA = frozenset({"belt", "curve", "sideload", "ug_sideload"})


def crossings(w, frame):
    """Where a module frame drawn on the base (X0,Y0,X1,Y1, tiles, X1/Y1 exclusive) is crossed:
    -> lines. A belt over the edge is a port (``port`` line written out); an underground under
    it, an inserter or drill reaching over it, a pipe through it and an entity standing across it
    are what a sealed module of that frame can't have as drawn: move the frame or the piece."""
    x0, y0, x1, y1 = frame
    g = w.graph

    def tin(t):
        return x0 <= t[0] < x1 and y0 <= t[1] < y1

    def kin(k):
        return tin(g.nodes[k].tiles[0])

    def side(t):
        return "w" if t[0] < x0 else "e" if t[0] >= x1 else "n" if t[1] < y0 else "s"

    ports, under, hands, pipes, across = [], [], [], [], []
    for a, lst in g.succ.items():
        for b, via in lst:
            ia, ib = kin(a), kin(b)
            if ia == ib:
                continue
            inner, outer = (b, a) if ib else (a, b)
            way = "in" if ib else "out"
            if via in _BELT_VIA:
                t, o = g.nodes[inner].tiles[0], g.nodes[outer].tiles[0]
                s = side(o)
                pos = t[1] - y0 if s in "we" else t[0] - x0
                name = w.net_of.get(inner) or w.net_of.get(outer)
                net = w.nets.get(name)
                its = sorted((net.items if net else {}) or (net.made | net.wanted if net else ()))
                ports.append((name, way, f"@{s}{pos}", inner, its))
            elif via == "ug":
                under.append(f"  under  {w.net_of.get(a, '?')}: {a} -> {b} passes under the edge "
                             f"({way})")
            else:
                hands.append(f"  hand   {via} {a} -> {b} reaches over the edge ({way})")
    for (ka, _ba), (kb, _bb), via in g.joins:
        if kin(ka) != kin(kb):
            pipes.append(f"  pipe   {ka} -- {kb} ({via})")
    for k, n in g.nodes.items():
        ins = {tin(t) for t in n.tiles}
        if len(ins) == 2:
            across.append(f"  across {k} stands on the edge")
    lines = [f"frame {x0},{y0},{x1},{y1} ({x1 - x0}x{y1 - y0}): {len(ports)} belt ports, "
             f"{len(under)} undergrounds under the edge, {len(hands)} hands over it, "
             f"{len(pipes)} pipes through it, {len(across)} entities across it"
             + (": sealable as drawn" if not (under or hands or pipes or across) else "")]
    for i, (name, way, at, k, its) in enumerate(sorted(ports, key=lambda p: (p[2], p[1])), 1):
        two = (its + its)[:2] if its else ["?", "?"]
        lines.append(f"  port p{i} : {way} {two[0]} | {two[1]} {at}    # net {name}, {k}"
                     + (f" (+{len(its) - 2} more items)" if len(its) > 2 else ""))
    return lines + under + hands + pipes + across


def agreement(w):
    """How far the graph's inserter and drill edges agree with the game's own targets."""
    unit = {e.row.get("u"): k for k, e in w.ents.items() if e.row.get("u") is not None}
    same = diff = 0
    first = []
    for k, e in w.ents.items():
        row = e.row
        if row.get("t") not in ("inserter", "mining-drill"):
            continue
        for end, via, adj in (("pt", "pickup", w.graph.pred), ("dt", "drop", w.graph.succ)):
            if row["t"] == "mining-drill" and end == "pt":
                continue
            game = unit.get(row.get(end))
            v = "mine" if row["t"] == "mining-drill" else via
            ours = [b for b, x in adj.get(k, ()) if x == v]
            if game is None and not ours:
                continue
            if game in ours:
                same += 1
            else:
                diff += 1
                if len(first) < 3:
                    first.append(f"{k} {via}: game {game}, graph {ours[0] if ours else None}")
    if not same + diff:
        return ""
    return (f"hands: {same} of {same + diff} pickup/drop ends agree with the game"
            + (f"; differ: {'; '.join(first)}" if diff else ""))
