"""fkcore.standin: god-mode stand-ins for what a design gets from nowhere (3.1; the lab's).

    python -m fkcore standin GAME NAME supply [--surface S]        power, the requesters' items,
                                                                   burners' first coal, module requests
    python -m fkcore standin GAME NAME feed [--surface S]          a source or sink at each open port
    python -m fkcore standin GAME NAME ore [--surface S] --ore RESOURCE --area X0,Y0,X1,Y1 [--amount N]
    python -m fkcore standin GAME NAME ore [--surface S] --copy-ore X0,Y0,X1,Y1 [--to X,Y]
    python -m fkcore standin GAME NAME ore [--surface S] --clear-ore

These write the world directly: an electric energy interface beside a pole for power, items into
requester chests, coal into empty burners, a paste's module requests put straight in, infinity
chests and loaders outside a module's open belt ports and infinity pipes outside its fluid ports,
ore under the ground. **None of it is implied by a surface, free or not** (Rory, 11-08: lab
features are general tools or small named god-mode conveniences): the agent asks for each one by
name, for a module built alone (``fkcore.alone``: NAME's slot on ``--surface S``), and only
where things are free (``free`` in the surface file; elsewhere they are refused). ``fk lab``
(``fkcore.lab``) asks for ``supply`` and ``feed`` after every build, as the lab always did.

- ``supply``: a requester chest is filled with what it asks for, its slots shared among the items
  (a request is about a minute's worth; run again, it tops them up); the paste's item requests
  (2.0.53: a machine's ``modules=``) go straight into their slots, since no robot flies here. The
  energy interface gives ``POWER`` J a tick (6 GW, 2.1.31: 120 MW left a beaconed PU chain on low
  power).
- ``feed`` (2.2, Rory 2026-10-31: "lab: should have infinity fluid source, and infinity chest
  source using loaders, loaders should also not be placed on half cells"): a port of a top-level
  sealed instance that no net binds from outside gets a source at its edge: an infinity chest and
  the 2x1 loader (on two whole tiles, outside every frame) for a belt port, a loader per lane for
  a pair, an infinity pipe for a fluid port; an ``out`` port empties into a chest or pipe that
  deletes what it gets. A port wired to the design's own chests is left alone.
- ``ore`` (2.0.56, PLAN-CELLS step 3): ore on the slot in the design's own coordinates (the plot's
  top-left is 0,0): a resource over an area, a map area of the world copied (shape and amounts)
  with its top-left at ``--to``, or every resource of the slot taken up. A rebuild keeps the ore
  (the slot is emptied of everything but resources), and the apply seeds drills only where none is.

Every game call is one RCON command with the link's 5 s timeout, and nothing is registered.
"""
from __future__ import annotations

import json

from . import alone, link
from . import surface as surfacemod

__all__ = ["StandinError", "POWER", "require_free", "supply", "lua_supply", "feeds", "lua_feed",
           "feed", "ore", "lua_ore_read", "lua_ore_clear", "lua_ore_put", "lua_unbounded"]

#: J a tick from the energy interface: 6 GW (2.1.31)
POWER = 100_000_000
#: one game call reads or writes the ore of at most this many tiles each way (5 s a call)
ORE_BATCH = 32
#: the world the ore is copied from
WORLD = "nauvis"


class StandinError(ValueError):
    pass


def require_free(sf: surfacemod.SurfaceFile, what):
    """A stand-in stands only where things are free."""
    if not sf.free:
        raise StandinError(f"{what}: {sf.name} is not free; a stand-in is a god-mode convenience "
                           "for a surface whose file says `free` (the lab)")


# -- supply: power, stock, fuel, module requests --------------------------------------------------
def lua_supply(surface, force, box):
    """Power, the requesters' items and burners' first coal for what stands in ``box`` of
    ``surface`` on ``force``. -> JSON {power, stocked, fuel, modules}."""
    x0, y0, x1, y1 = box
    return f"""
local s, f = game.get_surface('{surface}'), '{force}'
local area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}}
local out = {{power = nil, stocked = {{}}, fuel = 0, modules = 0}}
if s.count_entities_filtered{{area = area, name = 'electric-energy-interface'}} == 0 then
  for _, p in pairs(s.find_entities_filtered{{area = area, type = 'electric-pole'}}) do
    for dx = -3, 3 do for dy = -3, 3 do
      if not out.power then
        local pos = {{p.position.x + dx, p.position.y + dy}}
        if s.can_place_entity{{name = 'electric-energy-interface', position = pos, force = f}} then
          local ei = s.create_entity{{name = 'electric-energy-interface', position = pos, force = f}}
          ei.power_production = {POWER}
          ei.electric_buffer_size = {POWER}
          out.power = ei.position.x .. ',' .. ei.position.y
        end
      end
    end end
  end
else
  out.power = 'already'
end
for _, c in pairs(s.find_entities_filtered{{area = area, name = 'requester-chest'}}) do
  local ok, rp = pcall(function() return c.get_requester_point() end)
  local want = {{}}
  if ok and rp then
    for _, sec in pairs(rp.sections) do
      for _, fl in pairs(sec.filters) do
        local n = fl.value and fl.value.name
        if n and (fl.min or 0) > 0 and prototypes.item[n] then want[#want + 1] = n end
      end
    end
  end
  local inv = c.get_inventory(defines.inventory.chest)
  local share = #want > 0 and math.max(1, math.floor(#inv / #want)) or 0
  for _, n in pairs(want) do
    local full = prototypes.item[n].stack_size * share
    local k = full - inv.get_item_count(n)
    if k > 0 then
      k = c.insert{{name = n, count = k}}
      out.stocked[n] = (out.stocked[n] or 0) + k
    end
  end
end
for _, e in pairs(s.find_entities_filtered{{area = area}}) do
  local ok, inv = pcall(function() return e.get_fuel_inventory() end)
  if ok and inv and inv.is_empty() then
    inv.insert{{name = 'coal', count = 5}}
    out.fuel = out.fuel + 1
  end
end
for _, p in pairs(s.find_entities_filtered{{area = area, name = 'item-request-proxy'}}) do
  local t = p.proxy_target
  if t and t.valid then
    for _, plan in pairs(p.insert_plan or {{}}) do
      for _, at in pairs((plan.items and plan.items.in_inventory) or {{}}) do
        local inv = t.get_inventory(at.inventory)
        local slot = inv and inv[at.stack + 1]
        if slot and slot.set_stack{{name = plan.id.name, count = at.count or 1,
                                   quality = plan.id.quality or 'normal'}} then
          out.modules = out.modules + (at.count or 1)
        end
      end
    end
    p.destroy()
  end
end
rcon.print(helpers.table_to_json(out))
"""


def supply(r, surface, force, box):
    """Stand power, stock, fuel and module requests in ``box``. -> (report line, the JSON)."""
    got = json.loads(r.lua(lua_supply(surface, force, box)).strip() or "{}")
    stocked = got.get("stocked") or {}
    if isinstance(stocked, list):
        stocked = {}
    power = got.get("power")
    said = ("an energy interface at " + power if power not in (None, "already")
            else power or "no pole to put an energy interface beside")
    line = (f"  stand-in supply: power {said}; requesters stocked: "
            f"{', '.join(f'{n} {k}' for n, k in sorted(stocked.items())) or 'none'}"
            f"; burners given a first load: {got.get('fuel', 0)}"
            + (f"; modules put in: {got['modules']}" if got.get("modules") else ""))
    return line, got


# -- feeds: sources and sinks at the open ports (2.2) ---------------------------------------------
_VEC = {(0, -1): "north", (1, 0): "east", (0, 1): "south", (-1, 0): "west"}
_LEFTV = {(0, -1): (-1, 0), (1, 0): (0, -1), (0, 1): (1, 0), (-1, 0): (0, 1)}


def _frames(flat, lay):
    """[(path, x0, y0, x1, y1)] of the top-level sealed instances' frames, factory frame."""
    out = []
    for path, rec in lay.insts.items():
        inst = flat.insts.get(path)
        if "/" in path or inst is None or inst.module is None or inst.size is None:
            continue
        w, h = inst.size if rec.rot % 2 == 0 else inst.size[::-1]
        out.append((path, rec.x, rec.y, rec.x + w, rec.y + h))
    return out


def _bound_outside(flat, key):
    """A net outside port ``key``'s module names it."""
    scope = flat.ports[key].scope
    return any(n.scope != scope and any(k == key for k, _it in list(n.drivers) + list(n.consumers))
               for n in flat.nets.values())


def feeds(flat, lay, cat, ox=0, oy=0):
    """What stands at each open port of a top-level sealed instance: (entities, skipped). An
    entity is a dict ``{k: chest|loader|belt|pipe, at: [x, y] (a tile centre, world), port,
    ...}``; ``skipped`` is ``[(port, why)]``, a port whose source has no room outside the frames."""
    from . import expand
    frames = _frames(flat, lay)
    tops = {p for p, *_r in frames}
    edges = expand.port_edges(flat, lay, cat)
    ents, skipped, taken = [], [], set()

    def inside(t):
        return any(x0 <= t[0] < x1 and y0 <= t[1] < y1 for _p, x0, y0, x1, y1 in frames)

    for key in sorted(edges):
        port = flat.ports[key]
        if port.scope not in tops or _bound_outside(flat, key):
            continue
        pe = edges[key]
        d = (pe.dst[0] - pe.src[0], pe.dst[1] - pe.src[1])
        o = pe.outside

        def at(t, k, **kw):
            return dict(k=k, at=[t[0] + ox + 0.5, t[1] + oy + 0.5], port=key, tiles=[t], **kw)

        def loader(belt, v, **kw):
            # 2.1.20 (Rory: "loaders placed on half cells ... keeps the 2x1 aligned"): the game's
            # 2x1 loader on two whole tiles, its belt half on ``belt`` and its container half the
            # next tile along ``v``; its centre is the edge between them. Read from the game
            # (2.0.77): an output loader facing d has its belt half in front (the d side), an
            # input one facing d its container half in front
            box = (belt[0] + v[0], belt[1] + v[1])
            return dict(k="loader", at=[(belt[0] + box[0]) / 2 + ox + 0.5,
                                        (belt[1] + box[1]) / 2 + oy + 0.5],
                        port=key, tiles=[belt, box], **kw)

        if pe.fluid:
            want = [at(o, "pipe", fluid=pe.fluid, out=port.dir == "out")]
        elif port.dir == "out":
            want = [at((o[0] + 2 * d[0], o[1] + 2 * d[1]), "chest", items=[]),
                    loader(o, d, dir=_VEC[d], type="input")]
        elif port.right is None or list(port.right) == list(port.items):
            want = [at((o[0] - 2 * d[0], o[1] - 2 * d[1]), "chest",
                       items=sorted(set(port.all_items))),
                    loader(o, (-d[0], -d[1]), dir=_VEC[d], type="output")]
        else:                     # a pair: a belt at the edge, a loader side-loading each lane
            want = [at(o, "belt", dir=_VEC[d])]
            for sign, items in ((1, port.items), (-1, port.right)):
                lx, ly = _LEFTV[d][0] * sign, _LEFTV[d][1] * sign
                side, far = (o[0] + lx, o[1] + ly), (o[0] + 3 * lx, o[1] + 3 * ly)
                want += [at(far, "chest", items=sorted(set(items))),
                         loader(side, (lx, ly), dir=_VEC[(-lx, -ly)], type="output")]
        tiles = [t for e in want for t in e.pop("tiles")]
        if any(inside(t) or t in taken for t in tiles):
            skipped.append((key, "its source would stand inside a frame or on another port's"))
            continue
        taken |= set(tiles)
        ents += want
    return ents, skipped


def lua_feed(surface, force, ents):
    """Lua that stands the ports' sources and sinks (``feeds``) on ``surface`` for ``force``.
    -> JSON {placed, blocked: [port]}."""
    rows = []
    for e in sorted(ents, key=lambda e: e["k"] == "loader"):     # chests before their loaders
        parts = [f"k='{e['k']}'", f"x={e['at'][0]}", f"y={e['at'][1]}", f"port='{e['port']}'"]
        if "dir" in e:
            parts.append(f"dir=defines.direction.{e['dir']}")
        if "type" in e:
            parts.append(f"type='{e['type']}'")
        if "fluid" in e:
            parts.append(f"fluid='{e['fluid']}', out={'true' if e['out'] else 'false'}")
        if "items" in e:
            parts.append("items={" + ", ".join(f"'{n}'" for n in e["items"]) + "}")
        rows.append("{" + ", ".join(parts) + "}")
    return f"""
local s, f = game.get_surface('{surface}'), '{force}'
local function top(names) for _, n in ipairs(names) do if prototypes.entity[n] then return n end end end
local NAME = {{chest = 'infinity-chest', pipe = 'infinity-pipe',
              loader = top{{'turbo-loader', 'express-loader', 'fast-loader', 'loader'}},
              belt = top{{'turbo-transport-belt', 'express-transport-belt', 'fast-transport-belt',
                          'transport-belt'}}}}
local out = {{placed = 0, blocked = {{}}}}
for _, x in pairs({{{', '.join(rows)}}}) do
  local spec = {{name = NAME[x.k], position = {{x.x, x.y}}, force = f, direction = x.dir}}
  if x.k == 'loader' then spec.type = x.type end
  local e = s.can_place_entity{{name = spec.name, position = spec.position, direction = spec.direction,
                                force = f}} and s.create_entity(spec)
  if not e then
    out.blocked[#out.blocked + 1] = x.port
  else
    out.placed = out.placed + 1
    if x.k == 'pipe' then
      e.set_infinity_pipe_filter{{name = x.fluid, percentage = x.out and 0 or 1,
                                 mode = x.out and 'at-most' or 'at-least'}}
    elseif x.k == 'chest' then
      if #x.items == 0 then e.remove_unfiltered_items = true end
      for i, n in ipairs(x.items) do
        e.set_infinity_container_filter(i, {{name = n, count = prototypes.item[n].stack_size,
                                             mode = 'exactly', index = i}})
      end
    end
  end
end
rcon.print(helpers.table_to_json(out))
"""


def feed(r, ctx, name, surface, force, x, y):
    """Stand the sources and sinks of NAME's open ports (plot at x, y) on ``surface``. -> report
    lines ([] when the design has no open port)."""
    flat, lay = ctx.flats.get(name), ctx.layouts.get(name)
    if flat is None or lay is None or not flat.ports:
        return []
    ents, skipped = feeds(flat, lay, ctx.cat, x, y)
    if not ents and not skipped:
        return []
    got = json.loads(r.lua(lua_feed(surface, force, ents)).strip() or "{}") if ents else {}
    blocked = sorted(set(got.get("blocked") or []) if isinstance(got.get("blocked"), list) else [])
    ports = sorted({e["port"] for e in ents} - set(blocked))
    out = [f"  stand-in feed: {len(ports)} open ports ({', '.join(p.split('/')[-1] for p in ports)}):"
           " infinity chest + loader at a belt port, infinity pipe at a fluid port; out ports "
           "empty into a chest or pipe that deletes"] if ports else []
    for key, why in skipped:
        out.append(f"  {key}: not fed, {why}")
    for key in blocked:
        out.append(f"  {key}: not fed, a tile outside it is taken (the game said can't place)")
    return out


# -- ore (2.0.56) ---------------------------------------------------------------------------------
def _rect(text, what):
    """``X0,Y0,X1,Y1`` -> four ints, X0 < X1 and Y0 < Y1."""
    try:
        x0, y0, x1, y1 = (int(float(v)) for v in str(text).split(","))
    except ValueError:
        raise StandinError(f"{what}: X0,Y0,X1,Y1 (four numbers), got {text!r}") from None
    if x1 <= x0 or y1 <= y0:
        raise StandinError(f"{what}: {text} is empty (X0 < X1 and Y0 < Y1)")
    return x0, y0, x1, y1


def _batches(x0, y0, x1, y1):
    for by in range(y0, y1, ORE_BATCH):
        for bx in range(x0, x1, ORE_BATCH):
            yield bx, by, min(bx + ORE_BATCH, x1), min(by + ORE_BATCH, y1)


def lua_ore_read(surface, area):
    """The resources whose tile is in ``area`` (x0, y0, x1, y1) of ``surface``:
    JSON [[name, x, y, amount], ...], x and y the tile. A read: it writes nothing."""
    x0, y0, x1, y1 = area
    return f"""
local s = game.get_surface('{surface}')
local out = {{}}
for _, e in pairs(s.find_entities_filtered{{area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}}, type = 'resource'}}) do
  local x, y = math.floor(e.position.x), math.floor(e.position.y)
  if x >= {x0} and x < {x1} and y >= {y0} and y < {y1} then
    out[#out + 1] = {{e.name, x, y, e.amount}}
  end
end
rcon.print(helpers.table_to_json(out))
"""


def lua_ore_clear(surface, area):
    """Take up the resources of ``surface`` whose tile is in ``area``. -> how many."""
    x0, y0, x1, y1 = area
    return f"""
local s = game.get_surface('{surface}')
local n = 0
for _, e in pairs(s.find_entities_filtered{{area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}}, type = 'resource'}}) do
  local x, y = math.floor(e.position.x), math.floor(e.position.y)
  if x >= {x0} and x < {x1} and y >= {y0} and y < {y1} then e.destroy() n = n + 1 end
end
rcon.print(n)
"""


def lua_ore_put(surface, rows):
    """Put [[name, x, y, amount], ...] on ``surface``, each on tile x, y. -> how many stood."""
    data = json.dumps(rows, separators=(",", ":"))
    return f"""
local s = game.get_surface('{surface}')
local n = 0
for _, t in pairs(helpers.json_to_table('{data}')) do
  if s.create_entity{{name = t[1], position = {{t[2] + 0.5, t[3] + 0.5}}, amount = t[4]}} then
    n = n + 1
  end
end
rcon.print(n)
"""


def ore(r, game, name, surface=alone.LAB, resource=None, area=None, amount=100000, copy=None,
        to=(0, 0), clear_ore=False):
    """Ore on NAME's slot of ``surface``, in the design's own coordinates (its plot's top-left is
    0,0): ``resource`` over ``area``; or ``copy``, the resources of a map area of the world, shape
    and amounts, with its top-left at ``to``; or ``clear_ore``, every resource of the slot taken
    up. -> the report lines."""
    held = alone.slots(game, surface)
    if name not in held:
        raise StandinError(f"{name}: no slot on {surface} yet: build it once alone, then lay its ore")
    sx, sy, sw, sh = held[name]
    sbox = alone.box(sx, sy, sw, sh)
    head = f"stand-in ore, {name} on {surface}"
    if clear_ore:
        gone = sum(int(r.lua(lua_ore_clear(surface, b)).strip() or 0) for b in _batches(*sbox))
        return [f"{head}: {gone} ore tiles taken up from its slot {sx},{sy}"]
    force = alone.surface_of(game, name, surface).force_name if name in held else "player"
    r.lua(link.lua_generate_area(surface, force, sbox))
    if copy is not None:
        x0, y0, x1, y1 = _rect(copy, "--copy-ore")
        tx, ty = sx + int(to[0]), sy + int(to[1])
        dest = (tx, ty, tx + x1 - x0, ty + y1 - y0)
        if dest[0] < sbox[0] or dest[1] < sbox[1] or dest[2] > sbox[2] or dest[3] > sbox[3]:
            raise StandinError(f"--copy-ore {copy} at {to[0]},{to[1]} runs past the slot "
                               f"({sw}x{sh} tiles): copy a smaller area, or build NAME bigger")
        gone = sum(int(r.lua(lua_ore_clear(surface, b)).strip() or 0) for b in _batches(*dest))
        put, kinds, total = 0, {}, 0
        for b in _batches(x0, y0, x1, y1):
            rows = json.loads(r.lua(lua_ore_read(WORLD, b)).strip() or "[]")
            if not isinstance(rows, list):
                rows = []
            moved = [[n, x - x0 + tx, y - y0 + ty, a] for n, x, y, a in rows]
            for n, _x, _y, a in moved:
                kinds[n] = kinds.get(n, 0) + 1
                total += a
            if moved:
                put += int(r.lua(lua_ore_put(surface, moved)).strip() or 0)
        what = ", ".join(f"{n} {k} tiles" for n, k in sorted(kinds.items())) or "no ore"
        return [f"{head}: copied {copy} of {WORLD} to {to[0]},{to[1]} of its slot: {what}, "
                f"{total} in all; {put} stood ({gone} ore tiles taken up first)"]
    if resource is None or area is None:
        raise StandinError("--ore RESOURCE --area X0,Y0,X1,Y1, --copy-ore X0,Y0,X1,Y1 or --clear-ore")
    x0, y0, x1, y1 = _rect(area, "--area")
    dest = (sx + x0, sy + y0, sx + x1, sy + y1)
    gone = sum(int(r.lua(lua_ore_clear(surface, b)).strip() or 0) for b in _batches(*dest))
    put = 0
    for b in _batches(*dest):
        rows = [[resource, x, y, int(amount)] for y in range(b[1], b[3]) for x in range(b[0], b[2])]
        put += int(r.lua(lua_ore_put(surface, rows)).strip() or 0)
    return [f"{head}: {resource} on {put} tiles of {area} ({int(amount)} each; {gone} ore "
            "tiles there taken up first)"]


# -- the surface's edge (2.0.12) ------------------------------------------------------------------
def lua_unbounded(surface):
    """Take ``surface``'s edge off (2.0.12, Rory: "the lab should be allowed to have infinite
    size"; the scenario does it too, on a newer script): width and height 0 in its
    map_gen_settings, which the game reads back as 2000000 (2.0.77). -> "unbounded" or "was WxH"."""
    return f"""
local s = game.get_surface('{surface}')
if not s then rcon.print('no surface {surface}') return end
local m = s.map_gen_settings
if (m.width == 0 or m.width >= 2000000) and (m.height == 0 or m.height >= 2000000) then
  rcon.print('unbounded') return
end
local was = m.width .. 'x' .. m.height
m.width, m.height = 0, 0
s.map_gen_settings = m
rcon.print('was ' .. was)
"""
