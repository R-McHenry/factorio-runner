"""fkcore.terrain: the bare world of a surface: ore, water, cliffs (CORE-V1.md §6.2 role 1, §9).

The terrain is what layout designs against (§8: layout never reads the live world). It is read
once from the game (:func:`read`, ``python -m fkcore read-terrain``), written into the game
codebase as ``<name>.terrain.json``, and loaded by the flow's ``read_terrain`` step::

    {"fk_terrain": 1, "surface": "nauvis", "area": [x0, y0, x1, y1], "tick": 146760,
     "resources": {name: [[x, y, amount], ...]},       one row per resource tile
     "patches":   {name: [{"n", "tiles", "amount", "box"}, ...]},
     "water":     [[x, y], ...],
     "other":     [[x, y], ...],
     "cliffs":    [{"name", "x", "y", "orientation", "box", "box_orientation"}, ...],
     "removable": {"trees": n, "rocks": n, "names": {name: n}},
     "removable_at": {name: [[x, y], ...]},
     "enemies":   {name: n},
     "tiles":     {tile name: n},                       the blocking tiles, by name
     "ungenerated": [[cx, cy], ...],                    chunks of the area not generated
     "blocked":   [[x, y], ...]}                        water + other + cliff tiles

**What blocks** is the game's own rule, read from the collision masks: a tile blocks when its
prototype's mask shares a layer with what layouts build (the union of the masks of
:data:`REFERENCE`). It is ``water`` when its mask has the ``water_tile`` layer (water, deepwater,
their green, shallow and mud variants), else ``other`` (out-of-map and the like). A cliff blocks
every tile its collision box reaches more than :data:`CLIFF_INSET` into (no entity a layout
builds comes nearer its tiles' edges: a belt's box is 0.4 from its centre), so the corner a
diagonal cliff only grazes stays free; a diagonal cliff's box is turned by its
``box_orientation`` (an eighth of a turn) about its centre. **Trees and rocks are not obstacles**:
a superforced paste marks them for deconstruction (build on bare ground and steamroll what
stands there), so they are counted as removable and listed apart. Enemy structures are counted.

**Patches** are the 8-connected components of one resource's tiles, numbered by size (``#1`` is
the largest), each with its tile count, total amount and bounding box: the names a surface file's
``place ... patch iron-ore#2`` uses.

**The read** goes in blocks of :data:`BLOCK` x :data:`BLOCK` tiles, one RCON command each (the
link's 5 s timeout), every block written by ``helpers.write_file`` into script-output and read back
from disk, like ``link.snapshot``. It only reads: a chunk that is not generated is listed as
unknown ground, never generated.

**Frames.** A factory is laid out in its own frame and placed by its plot (``fkcore.compose``):
:func:`to_frame` / :func:`from_frame` map tiles between the surface and a factory frame (the
plot offset and rotation; the inverse of ``compose.turn``), and :func:`frame_ground` is the
terrain as one plot sees it. A rotated plot's frame depends on the factory's size, so the caller
passes the size of the moment. Pure, stdlib only, except :func:`read` (the game link).
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from functools import cached_property

from . import pack as _pack

__all__ = ["BLOCK", "REFERENCE", "Cliff", "Patch", "Terrain", "FrameGround", "lua_block", "read",
           "merge", "cliff_tiles", "patches", "from_dict", "load", "save", "to_frame",
           "from_frame", "frame_size", "frame_blocked", "frame_ground", "summary",
           "fmt_amount"]

#: Tiles per side of one read block (one RCON command each).
BLOCK = 128
#: A cliff blocks a tile when its box reaches more than this far inside the tile's edges.
CLIFF_INSET = 0.1
#: Entities whose collision masks decide which tiles block: what layouts build.
REFERENCE = ("assembling-machine-1", "transport-belt", "underground-belt", "splitter",
             "fast-inserter", "medium-electric-pole", "electric-mining-drill", "stone-furnace")
_EPS = 1e-6
_N8 = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


@dataclass
class Cliff:
    name: str
    x: float
    y: float
    orientation: str                 # the game's cliff_orientation ("west-to-east", ...)
    box: tuple                       # collision box (left, top, right, bottom), unturned
    box_orientation: float = 0.0     # the box's turn about its centre, a fraction of a full turn

    def tiles(self):
        return cliff_tiles(self.box, self.box_orientation)


@dataclass
class Patch:
    resource: str
    n: int
    tiles: frozenset
    amount: int
    box: tuple                       # (x0, y0, x1, y1), half-open

    @property
    def name(self):
        return f"{self.resource}#{self.n}"

    @property
    def size(self):
        return self.box[2] - self.box[0], self.box[3] - self.box[1]


@dataclass
class Terrain:
    surface: str
    area: tuple
    tick: int | None = None
    resources: dict = field(default_factory=dict)     # name -> {(x, y): amount}
    water: frozenset = frozenset()
    other: frozenset = frozenset()
    cliffs: list = field(default_factory=list)        # [Cliff]
    removable: dict = field(default_factory=dict)     # name -> {"type": tree|rock, "at": [(x, y)]}
    enemies: dict = field(default_factory=dict)       # name -> count
    tiles: dict = field(default_factory=dict)         # blocking tile name -> count
    ungenerated: list = field(default_factory=list)   # [(cx, cy)]

    @cached_property
    def cliff_tiles(self):
        x0, y0, x1, y1 = self.area
        out = set()
        for c in self.cliffs:
            out |= {t for t in c.tiles() if x0 <= t[0] < x1 and y0 <= t[1] < y1}
        return frozenset(out)

    @cached_property
    def blocked(self):
        # a pack with landfill (Sea Block): water is ground that costs landfill (fkcore.pack)
        water = frozenset() if _pack.landfill() else self.water
        return frozenset(water | self.other | self.cliff_tiles)

    @cached_property
    def patches(self):
        return patches(self.resources)

    def removable_count(self, kind):
        return sum(len(r["at"]) for r in self.removable.values() if r["type"] == kind)

    def to_dict(self):
        pats = {name: [{"n": p.n, "tiles": len(p.tiles), "amount": p.amount, "box": list(p.box)}
                       for p in ps] for name, ps in sorted(self.patches.items())}
        return {
            "fk_terrain": 1, "surface": self.surface, "area": list(self.area), "tick": self.tick,
            "resources": {name: [[x, y, a] for (x, y), a in sorted(tiles.items(),
                                                                   key=lambda r: (r[0][1], r[0][0]))]
                          for name, tiles in sorted(self.resources.items())},
            "patches": pats,
            "water": _rows(self.water), "other": _rows(self.other),
            "cliffs": [{"name": c.name, "x": c.x, "y": c.y, "orientation": c.orientation,
                        "box": list(c.box), "box_orientation": c.box_orientation}
                       for c in sorted(self.cliffs, key=lambda c: (c.y, c.x, c.name))],
            "removable": {"trees": self.removable_count("tree"),
                          "rocks": self.removable_count("rock"),
                          "names": {n: len(r["at"]) for n, r in sorted(self.removable.items())}},
            "removable_at": {n: {"type": r["type"], "at": [list(p) for p in sorted(r["at"])]}
                             for n, r in sorted(self.removable.items())},
            "enemies": dict(sorted(self.enemies.items())),
            "tiles": dict(sorted(self.tiles.items())),
            "ungenerated": [list(c) for c in sorted(self.ungenerated)],
            "blocked": _rows(self.blocked),
        }


def _rows(tiles):
    return [[x, y] for x, y in sorted(tiles, key=lambda t: (t[1], t[0]))]


# -- the read (game link) -------------------------------------------------------------------------
def _q(s):
    return json.dumps(str(s))


def lua_block(surface, area, tag):
    """Lua reading one block of ``area`` into script-output ``fk5/<tag>.json``; prints ``ok``."""
    x0, y0, x1, y1 = (int(v) for v in area)
    refs = ", ".join(_q(n) for n in REFERENCE)
    return f"""
local s = game.get_surface({_q(surface)})
if not s then rcon.print('no surface') return end
local x0, y0, x1, y1 = {x0}, {y0}, {x1}, {y1}
local ref = {{}}
for _, n in pairs({{{refs}}}) do
  local p = prototypes.entity[n]
  if p then for l in pairs(p.collision_mask.layers) do ref[l] = true end end
end
local block, water = {{}}, {{}}
for name, tp in pairs(prototypes.tile) do
  local layers = tp.collision_mask.layers
  for l in pairs(layers) do if ref[l] then block[#block + 1] = name break end end
  if layers.water_tile then water[name] = true end
end
local out = {{tick = game.tick, area = {{x0, y0, x1, y1}}, water = {{}}, other = {{}}, tiles = {{}},
  resources = {{}}, removable = {{}}, cliffs = {{}}, enemies = {{}}, ungenerated = {{}}}}
for cx = math.floor(x0 / 32), math.floor((x1 - 1) / 32) do
  for cy = math.floor(y0 / 32), math.floor((y1 - 1) / 32) do
    if not s.is_chunk_generated({{cx, cy}}) then out.ungenerated[#out.ungenerated + 1] = {{cx, cy}} end
  end
end
local A = {{{{x0, y0}}, {{x1, y1}}}}
local function inside(p) return p.x >= x0 and p.x < x1 and p.y >= y0 and p.y < y1 end
if #block > 0 then
  for _, t in pairs(s.find_tiles_filtered{{area = A, name = block}}) do
    local p = t.position
    local rows = water[t.name] and out.water or out.other
    rows[#rows + 1] = {{p.x, p.y}}
    out.tiles[t.name] = (out.tiles[t.name] or 0) + 1
  end
end
for _, e in pairs(s.find_entities_filtered{{area = A, type = 'resource'}}) do
  local p = e.position
  if inside(p) then
    local r = out.resources[e.name] or {{}}
    out.resources[e.name] = r
    r[#r + 1] = {{math.floor(p.x), math.floor(p.y), e.amount}}
  end
end
for _, e in pairs(s.find_entities_filtered{{area = A, type = {{'tree', 'simple-entity'}}}}) do
  local p = e.position
  if inside(p) then
    local r = out.removable[e.name] or {{type = e.type == 'tree' and 'tree' or 'rock', at = {{}}}}
    out.removable[e.name] = r
    r.at[#r.at + 1] = {{p.x, p.y}}
  end
end
for _, e in pairs(s.find_entities_filtered{{area = A, force = 'enemy', type = {{'unit-spawner', 'turret'}}}}) do
  if inside(e.position) then out.enemies[e.name] = (out.enemies[e.name] or 0) + 1 end
end
for _, e in pairs(s.find_entities_filtered{{area = A, type = 'cliff'}}) do
  local bb = e.bounding_box
  out.cliffs[#out.cliffs + 1] = {{name = e.name, x = e.position.x, y = e.position.y,
    orientation = e.cliff_orientation, box = {{bb.left_top.x, bb.left_top.y, bb.right_bottom.x,
    bb.right_bottom.y}}, box_orientation = bb.orientation or 0}}
end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json(out), false)
rcon.print('ok ' .. #out.water .. ' ' .. #out.cliffs)
"""


def read(rcon, surface, area, tag="terrain", block=BLOCK, out_dir=None) -> dict:
    """Read ``area`` of ``surface`` block by block; -> the terrain file's dict (see the module
    docstring). Only reads; one RCON command per block."""
    from . import link
    out_dir = out_dir or link.OUT_DIR
    x0, y0, x1, y1 = (int(v) for v in area)
    parts = []
    for by in range(y0, y1, block):
        for bx in range(x0, x1, block):
            sub = (bx, by, min(bx + block, x1), min(by + block, y1))
            name = f"{tag}-{os.getpid()}-{len(parts)}"
            reply = rcon.lua(lua_block(surface, sub, name)).strip()
            if not reply.startswith("ok"):
                raise link.LinkError(f"terrain read of {sub} failed: {reply[:200]}")
            path = os.path.join(out_dir, "fk5", f"{name}.json")
            with open(path, encoding="utf-8") as fh:
                parts.append(json.load(fh))
            os.remove(path)
    return merge(surface, (x0, y0, x1, y1), parts).to_dict()


def _table(v):
    """A Lua table from table_to_json: an empty one may come back as [] or {}."""
    return v if isinstance(v, dict) else {}


def merge(surface, area, parts) -> "Terrain":
    """The blocks of a read (each one block's dict) -> one Terrain. Cliffs seen by two blocks
    are kept once."""
    resources, removable, enemies, tiles = {}, {}, {}, {}
    water, other, cliffs, ungen, ticks = set(), set(), {}, set(), []
    for p in parts:
        ticks.append(p.get("tick"))
        water |= {(int(x), int(y)) for x, y in p.get("water") or ()}
        other |= {(int(x), int(y)) for x, y in p.get("other") or ()}
        for name, rows in _table(p.get("resources")).items():
            mine = resources.setdefault(name, {})
            for x, y, a in rows:
                mine[(int(x), int(y))] = int(a)
        for name, r in _table(p.get("removable")).items():
            mine = removable.setdefault(name, {"type": r["type"], "at": []})
            mine["at"] += [(float(x), float(y)) for x, y in r.get("at") or ()]
        for name, n in _table(p.get("enemies")).items():
            enemies[name] = enemies.get(name, 0) + int(n)
        for name, n in _table(p.get("tiles")).items():
            tiles[name] = tiles.get(name, 0) + int(n)
        for c in p.get("cliffs") or ():
            key = (c["name"], float(c["x"]), float(c["y"]))
            cliffs[key] = Cliff(c["name"], float(c["x"]), float(c["y"]), c["orientation"],
                                tuple(float(v) for v in c["box"]),
                                float(c.get("box_orientation") or 0.0))
        ungen |= {(int(cx), int(cy)) for cx, cy in p.get("ungenerated") or ()}
    tick = max((t for t in ticks if t is not None), default=None)
    return Terrain(surface, tuple(int(v) for v in area), tick, resources, frozenset(water),
                   frozenset(other), [cliffs[k] for k in sorted(cliffs)], removable, enemies,
                   tiles, sorted(ungen))


def combine(old: "Terrain", new: "Terrain") -> "Terrain":
    """A read of ``new.area`` merged into what ``old`` holds (2.1.3: ``read-terrain --area`` had
    replaced the whole file with the one area, and a run lost its base's terrain). Inside the new
    area the new read wins, tile by tile; outside it the old file stays. The area is the box round
    both. Counts (enemies, blocking tiles by name) add up when the two areas don't meet, and take
    the larger of the two where they do."""
    nx0, ny0, nx1, ny1 = new.area

    def outside(x, y):
        return not (nx0 <= x < nx1 and ny0 <= y < ny1)

    resources = {}
    for name, tiles in old.resources.items():
        kept = {t: a for t, a in tiles.items() if outside(*t)}
        if kept:
            resources[name] = kept
    for name, tiles in new.resources.items():
        resources.setdefault(name, {}).update(tiles)
    water = frozenset({t for t in old.water if outside(*t)} | set(new.water))
    other = frozenset({t for t in old.other if outside(*t)} | set(new.other))
    cliffs = {(c.name, c.x, c.y): c for c in old.cliffs if outside(math.floor(c.x),
                                                                    math.floor(c.y))}
    cliffs.update({(c.name, c.x, c.y): c for c in new.cliffs})
    removable = {}
    for src, keep in ((old.removable, True), (new.removable, False)):
        for name, r in src.items():
            at = [p for p in r["at"] if not keep or outside(math.floor(p[0]), math.floor(p[1]))]
            if at:
                removable.setdefault(name, {"type": r["type"], "at": []})["at"] += at
    ox0, oy0, ox1, oy1 = old.area
    meet = ox0 < nx1 and nx0 < ox1 and oy0 < ny1 and ny0 < oy1

    def counts(a, b):
        out = dict(a)
        for k, v in b.items():
            out[k] = max(out.get(k, 0), v) if meet else out.get(k, 0) + v
        return out

    ungen = {c for c in old.ungenerated if outside(c[0] * 32, c[1] * 32)} | set(new.ungenerated)
    area = (min(ox0, nx0), min(oy0, ny0), max(ox1, nx1), max(oy1, ny1))
    return Terrain(new.surface, area, new.tick if new.tick is not None else old.tick, resources,
                   water, other, [cliffs[k] for k in sorted(cliffs)], removable,
                   counts(old.enemies, new.enemies), counts(old.tiles, new.tiles),
                   sorted(tuple(c) for c in ungen))


# -- geometry -------------------------------------------------------------------------------------
def cliff_tiles(box, orientation=0.0, inset=CLIFF_INSET):
    """The tiles a (possibly turned) collision box overlaps, each tile taken ``inset`` inside
    its edges (0: any overlap with positive area).

    ``box`` is (left, top, right, bottom) before the turn; ``orientation`` turns it clockwise
    about its centre by that fraction of a full turn (the game's BoundingBox orientation)."""
    left, top, right, bottom = (float(v) for v in box)
    cx, cy = (left + right) / 2, (top + bottom) / 2
    hx, hy = (right - left) / 2, (bottom - top) / 2
    a = float(orientation) * 2 * math.pi
    ux, uy = math.cos(a), math.sin(a)                 # the box's own x axis, turned clockwise
    vx, vy = -uy, ux                                  # its y axis
    ex = hx * abs(ux) + hy * abs(vx)                  # half extents of the turned box on x and y
    ey = hx * abs(uy) + hy * abs(vy)
    out = set()
    for j in range(math.floor(cy - ey), math.ceil(cy + ey)):
        for i in range(math.floor(cx - ex), math.ceil(cx + ex)):
            if _overlaps(i, j, 0.5 - inset, (cx, cy, hx, hy), (ux, uy, vx, vy), (ex, ey)):
                out.add((i, j))
    return out


def _overlaps(i, j, s, box, axes, ext):
    """Separating axes: the square of half-size ``s`` centred in tile (i, j) and the turned box
    overlap with positive area."""
    cx, cy, hx, hy = box
    ux, uy, vx, vy = axes
    sx, sy = i + 0.5, j + 0.5
    if abs(sx - cx) >= ext[0] + s - _EPS or abs(sy - cy) >= ext[1] + s - _EPS:
        return False
    for ax, ay, h in ((ux, uy, hx), (vx, vy, hy)):
        d = abs((sx - cx) * ax + (sy - cy) * ay)
        if d >= h + s * (abs(ax) + abs(ay)) - _EPS:
            return False
    return True


def patches(resources) -> dict:
    """{resource: [Patch]}: 8-connected components of each resource's tiles, largest first
    (ties: more ore, then north-west first), numbered from 1."""
    out = {}
    for name, tiles in sorted(resources.items()):
        left, comps = set(tiles), []
        while left:
            start = min(left, key=lambda t: (t[1], t[0]))
            left.discard(start)
            comp, todo = {start}, [start]
            while todo:
                x, y = todo.pop()
                for dx, dy in _N8:
                    t = (x + dx, y + dy)
                    if t in left:
                        left.discard(t)
                        comp.add(t)
                        todo.append(t)
            comps.append(comp)
        comps.sort(key=lambda c: (-len(c), -sum(tiles[t] for t in c),
                                  min((t[1], t[0]) for t in c)))
        out[name] = [Patch(name, k + 1, frozenset(c), sum(tiles[t] for t in c),
                           (min(t[0] for t in c), min(t[1] for t in c),
                            max(t[0] for t in c) + 1, max(t[1] for t in c) + 1))
                     for k, c in enumerate(comps)]
    return out


# -- the file -------------------------------------------------------------------------------------
def from_dict(d) -> Terrain:
    resources = {name: {(int(x), int(y)): int(a) for x, y, a in rows}
                 for name, rows in _table(d.get("resources")).items()}
    removable = {}
    for name, r in _table(d.get("removable_at")).items():
        removable[name] = {"type": r["type"], "at": [(float(x), float(y)) for x, y in r["at"]]}
    cliffs = [Cliff(c["name"], float(c["x"]), float(c["y"]), c["orientation"],
                    tuple(float(v) for v in c["box"]), float(c.get("box_orientation") or 0.0))
              for c in d.get("cliffs") or ()]
    return Terrain(d["surface"], tuple(int(v) for v in d["area"]), d.get("tick"), resources,
                   frozenset((int(x), int(y)) for x, y in d.get("water") or ()),
                   frozenset((int(x), int(y)) for x, y in d.get("other") or ()), cliffs,
                   removable, dict(_table(d.get("enemies"))), dict(_table(d.get("tiles"))),
                   [tuple(c) for c in d.get("ungenerated") or ()])


def load(path) -> Terrain:
    with open(path, encoding="utf-8") as fh:
        return from_dict(json.load(fh))


def dumps(d) -> str:
    """The file text: one top-level key per line, each value compact (diffable, not huge)."""
    lines = [f" {json.dumps(k)}: {json.dumps(v, separators=(',', ':'))}" for k, v in d.items()]
    return "{\n" + ",\n".join(lines) + "\n}\n"


def save(t, path):
    """Write a Terrain (or the dict :func:`read` returns) as ``<name>.terrain.json``."""
    d = t.to_dict() if isinstance(t, Terrain) else from_dict(t).to_dict()
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(dumps(d))


# -- frames ---------------------------------------------------------------------------------------
def frame_size(plot):
    """The plot's extent in its factory's frame (a quarter-turned plot swaps W and H)."""
    return (plot.w, plot.h) if plot.rot % 2 == 0 else (plot.h, plot.w)


def from_frame(tile, plot, size):
    """A factory-frame tile -> the surface tile it lands on (``compose``: the factory of ``size``
    turned ``plot.rot`` quarter turns clockwise, its turned frame's top-left on the plot origin)."""
    tx, ty = tile
    w, h = size
    r = plot.rot % 4
    if r == 0:
        u, v = tx, ty
    elif r == 1:
        u, v = h - 1 - ty, tx
    elif r == 2:
        u, v = w - 1 - tx, h - 1 - ty
    else:
        u, v = ty, w - 1 - tx
    return u + plot.x, v + plot.y


def to_frame(tile, plot, size):
    """A surface tile -> the factory-frame tile on it (the inverse of :func:`from_frame`)."""
    u, v = tile[0] - plot.x, tile[1] - plot.y
    w, h = size
    r = plot.rot % 4
    if r == 0:
        return u, v
    if r == 1:
        return v, h - 1 - u
    if r == 2:
        return w - 1 - u, h - 1 - v
    return w - 1 - v, u


@dataclass
class FrameGround:
    """A terrain as one plot sees it, in the factory frame."""
    blocked: frozenset                  # tiles no entity may use
    ore: dict                           # resource -> {tile: amount}
    box: tuple                          # the plot, (0, 0, W, H) in the frame
    patch: str | None = None            # the plot's patch (``iron-ore#2``): ore is that patch only
    water: int = 0                      # blocked tiles inside the plot, by kind (for reports)
    cliff: int = 0
    other: int = 0


def frame_blocked(t: Terrain, plot, size=None, margin=8) -> frozenset:
    """The terrain's blocked tiles inside ``plot`` plus ``margin`` tiles around it (where a
    route may stray), in the factory frame of ``size`` (default: the plot's own extent)."""
    size = size or frame_size(plot)
    x0, y0 = plot.x - margin, plot.y - margin
    x1, y1 = plot.x + plot.w + margin, plot.y + plot.h + margin
    return frozenset(to_frame(b, plot, size) for b in t.blocked
                     if x0 <= b[0] < x1 and y0 <= b[1] < y1)


def frame_ground(t: Terrain, plot, size=None, margin=8, patch=None) -> FrameGround:
    """The terrain as ``plot`` sees it, in the factory frame of ``size`` (default: the plot's own
    extent): blocked tiles (:func:`frame_blocked`) and the ore inside the plot. ``patch``
    (``iron-ore#2``) keeps only that patch's ore of its resource."""
    size = size or frame_size(plot)

    def inside(tile):
        return plot.x <= tile[0] < plot.x + plot.w and plot.y <= tile[1] < plot.y + plot.h

    blocked = frame_blocked(t, plot, size, margin)
    keep = None
    if patch and "#" in patch:
        res, n = patch.split("#", 1)
        found = [p for p in t.patches.get(res, ()) if str(p.n) == n]
        keep = (res, found[0].tiles if found else frozenset())
    ore = {}
    for res, tiles in t.resources.items():
        mine = {to_frame(k, plot, size): a for k, a in tiles.items() if inside(k)
                and (keep is None or keep[0] != res or k in keep[1])}
        if mine:
            ore[res] = mine
    return FrameGround(blocked, ore, (0, 0) + tuple(frame_size(plot)), patch,
                       sum(1 for b in t.water if inside(b)),
                       sum(1 for b in t.cliff_tiles if inside(b)),
                       sum(1 for b in t.other if inside(b)))


# -- report ---------------------------------------------------------------------------------------
def fmt_amount(a):
    a = float(a)
    if a >= 1e6:
        return f"{a / 1e6:.2f}M"
    if a >= 1e3:
        return f"{a / 1e3:.1f}k"
    return f"{int(a)}"


def _box_text(box):
    return f"{box[0]},{box[1]}..{box[2] - 1},{box[3] - 1}"


def summary(t: Terrain, name="map", show=3) -> list:
    """Report lines: the area, blocked ground, resources and patches, removable, enemies."""
    x0, y0, x1, y1 = t.area
    lines = [f"terrain {name}: {t.surface} area {x0},{y0},{x1},{y1} ({x1 - x0}x{y1 - y0}) at tick "
             f"{t.tick}; {len(t.ungenerated)} chunk{'s' if len(t.ungenerated) != 1 else ''} not "
             "generated (unknown ground)",
             f"  blocked {len(t.blocked)} tiles: water {len(t.water)}, cliffs {len(t.cliffs)} "
             f"({len(t.cliff_tiles)} tiles), other {len(t.other)}"
             + (" (" + ", ".join(f"{k} {v}" for k, v in sorted(t.tiles.items())) + ")"
                if t.tiles else "")]
    for res, ps in sorted(t.patches.items()):
        total = sum(p.amount for p in ps)
        tiles = sum(len(p.tiles) for p in ps)
        head = (f"  {res}: {tiles} tiles, {fmt_amount(total)}, {len(ps)} "
                f"patch{'es' if len(ps) != 1 else ''}")
        parts = [f"#{p.n} {len(p.tiles)} tiles {fmt_amount(p.amount)} at {_box_text(p.box)}"
                 for p in ps[:show]]
        lines.append(head + ("; " + "; ".join(parts) if parts else "")
                     + (f"; ... {len(ps) - show} more" if len(ps) > show else ""))
    lines.append(f"  removable, not obstacles (a paste marks them for deconstruction): "
                 f"{t.removable_count('tree')} trees, {t.removable_count('rock')} rocks")
    if t.enemies:
        lines.append("  enemies: " + ", ".join(f"{n} {k}" for k, n in sorted(t.enemies.items())))
    return lines
