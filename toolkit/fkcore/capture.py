"""fkcore.capture: something built, as a Literal file that can go anywhere (2.0.62).

Rory, 2026-10-29: "it should be easy for a player to make a design in the lab, or simply separate
build, and turn it into a bp or literal that can then go anywhere. So in any case where being in
a module is different, a build could happen without modules and go into one after."

Three sources, one kind of file:

- ``lab NAME``: a lab slot as it stands in the game (the lab's own power, the energy interface,
  stays out);
- ``world X0,Y0,X1,Y1``: an area of the live game, or of the planner (``fk sim game capture``);
- ``design PATH``: an instance of a laid-out design (``FACTORY/INST`` in the committed .fkp of
  game\\, or of a lab workspace with ``--fkp``), its machines and its own carriers.

The game sources blueprint the area and write ``{"bp": "0e...", ...}``; the design source writes
an entity list ``{"entities": [{name, x, y, direction, recipe, ...}]}``. Both are what
``Literal<file="...">`` reads (``fkcore.literal``) and what ``fk sim place`` takes, so the same
file goes into a module, onto another plot, or into the planner.

2.1 (PLAN-2.1 H): the file names each machine it holds, ``"names": {"asm1": 0, ...}`` (``asm1``,
``asm2``, ...; ``furnace1``, ...; ``drill1``, ``lab1``), each an index into its entities, so a
net outside the Literal can end on one (``mall.asm3``)."""
from __future__ import annotations

import json
import os

from . import fkp as fkpmod

#: what the lab adds for itself, never part of a design
LAB_OWN = ("electric-energy-interface", "infinity-chest", "infinity-pipe", "loader",
           "loader-1x1", "fast-loader", "express-loader", "turbo-loader")


class CaptureError(ValueError):
    pass


def _q(text):
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


def lua_capture(surface, force, area, tag, skip=LAB_OWN):
    """Lua that blueprints ``area`` (x0, y0, x1, y1) on ``surface`` without the entities named in
    ``skip``, and writes ``fk5/<tag>.json``: ``{bp, n, names}``; it replies with the count. Only
    an entity whose centre is in the area is taken (2.1.3: a blueprint takes every entity whose box
    touches the area, and ``capture lab u0`` took a drill of the next slot)."""
    x0, y0, x1, y1 = area
    drop = ", ".join(f"[{_q(n)}] = true" for n in skip)
    return f"""
local s, f = game.get_surface({_q(surface)}), game.forces[{_q(force)}]
if not s or not f then rcon.print('no surface or force') return end
local inv = game.create_inventory(1)
inv.insert{{name = 'blueprint'}}
local st = inv[1]
local src = st.create_blueprint{{surface = s, force = f, area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}},
  include_trains = false, include_fuel = false, include_station_names = false}} or {{}}
local function inside(e)
  if not (e and e.valid) then return true end
  local p = e.position
  return p.x >= {x0} and p.x < {x1} and p.y >= {y0} and p.y < {y1}
end
local drop, names, kept, at = {{{drop}}}, {{}}, {{}}, {{}}
for _, b in pairs(st.is_blueprint_setup() and st.get_blueprint_entities() or {{}}) do
  if not drop[b.name] and inside(src[b.entity_number]) then
    kept[#kept + 1] = b
    local w = src[b.entity_number]
    at[#kept] = w and w.valid and {{w.position.x, w.position.y, b.name}} or false
    names[b.name] = (names[b.name] or 0) + 1
  end
end
local bp = ''
if #kept > 0 then
  st.set_blueprint_entities(kept)
  bp = st.export_stack()
end
inv.destroy()
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({{bp = bp, n = #kept, names = names, at = at}}), false)
rcon.print(#kept)
"""


def world_list(bp, at):
    """2.1.31: the captured entities in world positions, ``[{name, wx, wy, bx, by}]`` in the
    blueprint's order, ``at`` the game's world position of each (Lua's ``at``). With it in the
    file, a Literal's ``box`` is in world tiles and its corner is where the build stood, so a
    Literal placed at that corner (or several Literals cut from one capture) sits as it did.
    None when the two do not line up (then the file keeps the blueprint alone)."""
    from . import codec
    if not bp or not isinstance(at, list):
        return None
    try:
        rows, _desc, _wires = codec.parse_blueprint(bp)
    except codec.CodecError:
        return None
    if len(rows) != len(at) or not all(isinstance(p, list) and len(p) == 3 and p[2] == r["name"]
                                       for r, p in zip(rows, at)):
        return None
    return [{"name": r["name"], "wx": _num(p[0]), "wy": _num(p[1]), "bx": _num(r["x"]),
             "by": _num(r["y"])} for r, p in zip(rows, at)]


def anchored(got, anchor, cat):
    """3.1 (``fk capture --from``): a game read ``got`` ({bp, n, names, at}) as a Literal file
    anchored at ``anchor`` (a whole tile, the placement's origin or the area's corner): the
    entities in the blueprint's order with their positions relative to the anchor (``wx``,
    ``wy``) and their blueprint positions (``bx``, ``by``: how each is set), the ``bp``, the
    machine ``names``, the ``ports`` and the ``anchor``. ``Literal<file="..."> @(ax, ay)`` then
    puts every entity back exactly where it stands, so a module written against the file mates
    with the standing objects (ok) and adds its own as ghosts."""
    listed = world_list(got.get("bp"), got.get("at"))
    if listed is None:
        raise CaptureError("the capture's entities and its blueprint do not line up (an older game "
                           "link?); fk capture world AREA writes the blueprint alone")
    ax, ay = anchor
    data = {"bp": got["bp"], "anchor": [int(ax), int(ay)],
            "entities": [{"name": e["name"], "wx": _num(e["wx"] - ax), "wy": _num(e["wy"] - ay),
                          "bx": e["bx"], "by": e["by"]} for e in listed]}
    named = names_of_bp(got["bp"], cat)
    if named:
        data["names"] = named
    edge = ports_of_bp(got["bp"], cat)
    if edge:
        data["ports"] = edge
    return data


def from_game(r, surface, force, area, tag="capture"):
    """-> {bp, n, names}: ``area`` blueprinted in the game."""
    from . import link
    tag = f"{tag}-{os.getpid()}"
    got = link.read_output(r, lua_capture(surface, force, area, tag), tag, "capture", keep=False)
    if not got.get("n"):
        raise CaptureError(f"nothing stands in {','.join(str(v) for v in area)} on {surface}")
    return got


def from_design(lay, cat, path):
    """-> [entity dicts]: instance ``path`` of layout ``lay``, its own entities (machines, a
    template's belts) and every carrier standing inside its frame (its own nets' routes), at
    positions relative to the instance's origin (3.0.1: its ``@(x, y)``, so the Literal placed
    where the instance was lays the same tiles, and its file keeps its origin when what it
    holds grows; a turned instance's, whose layout record is its turned corner, relative to its
    entities' top-left tile). Cover poles and roboports stay out: the build covers the Literal
    where it lands."""
    rec = lay.insts.get(path)
    if rec is None:
        near = sorted(p for p in lay.insts if path in p)[:6]
        raise CaptureError(f"no instance {path} in the layout"
                           + (f" (did you mean {', '.join(near)}?)" if near else ""))
    mine = [e for e in lay.entities.values() if e.owner == path or e.owner.startswith(path + "/")
            or e.owner.startswith(f"~{path}/")]           # its own entities and its own nets' routes
    if not mine:
        raise CaptureError(f"{path} has no entities in the layout")
    tiles = set()
    for e in mine:
        tiles |= fkpmod.tiles_of(cat, e)
    if rec.rot % 4 == 0 and rec.kind != "Literal":
        x0, y0 = float(rec.x), float(rec.y)
    else:
        x0, y0 = min(t[0] for t in tiles), min(t[1] for t in tiles)
    out = []
    for e in sorted(mine, key=lambda e: fkpmod.natural_key(e.key)):
        row = {"name": e.name, "x": _num(float(e.x) - x0), "y": _num(float(e.y) - y0)}
        if e.dir is not None:
            row["direction"] = int(e.dir)
        for k in ("recipe", "ug", "config"):
            if k in e.attrs:
                row[k] = e.attrs[k]
        out.append(row)
    return out


def _num(v):
    return int(v) if float(v).is_integer() else v


def write(path, data):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")


def summary(names):
    """'12 assembling-machine-2, 40 transport-belt, ...' (most first)."""
    return ", ".join(f"{n} {k}" for k, n in sorted(names.items(), key=lambda kv: (-kv[1], kv[0])))


def names(entity_names, cat):
    """{name: index} for the machines among ``entity_names`` (file order), 2.1."""
    from .literal import machine_names
    return machine_names(entity_names, cat)


def names_of_bp(bp, cat):
    """{name: index} for the machines of blueprint string ``bp``, in its entities' order (the
    order ``fkcore.literal`` reads them in)."""
    from . import codec
    try:
        rows, _desc, _wires = codec.parse_blueprint(bp)
    except codec.CodecError:
        return {}
    return names([r["name"] for r in rows], cat)


_OPEN = {"n": (0, -1), "e": (1, 0), "s": (0, 1), "w": (-1, 0)}


def ports(rows, cat):
    """3.0, Literal ports: ``{name: {"carrier": index, "side": s}}`` for the carriers on the edge of
    the captured frame (the box of every entity's tiles) that cross it: a belt heading out over the
    edge or in from it, a pipe that ends on the edge (one fluid neighbour inside: a pipe beside a
    machine is no end), a pipe-to-ground whose open side faces it. Named by side and place along
    it, ``n1`` the westmost on the north edge, ``e1`` the northmost on the east: a name says where
    the port is on the frame, whatever the file's order or the recipes inside. ``rows``:
    (name, x, y, direction) in file order."""
    from .literal import SIDES
    tiles, wet = [], set()
    for name, x, y, d in rows:
        if cat.has_entity(name):
            tiles.append(cat.tiles(name, x, y, d or 0))
            if cat.has_fluid_boxes(name):
                wet |= set(tiles[-1])
    if not tiles:
        return {}
    every = [t for ts in tiles for t in ts]
    x0, y0 = min(t[0] for t in every), min(t[1] for t in every)
    x1, y1 = max(t[0] for t in every), max(t[1] for t in every)
    found = []
    for k, (name, x, y, d) in enumerate(rows):
        if not cat.has_entity(name):
            continue
        kind = cat.kind(name)
        belt = kind in ("transport-belt", "underground-belt")
        pipe = kind in ("pipe", "pipe-to-ground")
        if not (belt or pipe):
            continue
        tx, ty = int(x // 1), int(y // 1)
        h = (int(d or 0) // 4) % 4
        for side, edge in (("n", ty == y0), ("e", tx == x1), ("s", ty == y1), ("w", tx == x0)):
            if not edge:
                continue
            s = SIDES[side]
            if belt and h not in (s, (s + 2) % 4):
                continue          # a belt along the edge crosses nothing
            inner = [(dx, dy) for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0))
                     if (tx + dx, ty + dy) in wet]
            if kind == "pipe" and len(inner) > 1:
                continue          # a pipe that runs on inside: no end
            if kind == "pipe" and inner and _OPEN[side] != (-inner[0][0], -inner[0][1]):
                continue          # a corner pipe opens on the other edge
            if kind == "pipe-to-ground" and h != s:
                continue          # its open side faces inside
            found.append((side, tx if side in "ns" else ty, k))
            break
    out, count = {}, {}
    for side, _along, k in sorted(found):
        count[side] = count.get(side, 0) + 1
        out[f"{side}{count[side]}"] = {"carrier": k, "side": side}
    return out


def ports_of_bp(bp, cat):
    """``ports`` for blueprint string ``bp``'s entities, in the order ``fkcore.literal`` reads them."""
    from . import codec
    try:
        got, _desc, _wires = codec.parse_blueprint(bp)
    except codec.CodecError:
        return {}
    return ports([(r["name"], float(r["x"]), float(r["y"]), r["direction"]) for r in got], cat)


def name_span(named):
    """'asm1..asm12, furnace1..furnace4' for the report line."""
    import re
    stems = {}
    for name in named:
        m = re.fullmatch(r"([a-z]+)(\d+)", name)
        if m:
            stems.setdefault(m.group(1), []).append(int(m.group(2)))
    return ", ".join(f"{s}{min(v)}" + (f"..{s}{max(v)}" if len(v) > 1 else "")
                     for s, v in stems.items())
