"""fkcore.vision: `fk look` zoomed out, with layers, as text or a PNG (2.1, "Vision").

One render model drives both outputs. A look is a :class:`Frame` (the area and ``zoom``, the
tiles a cell is wide), a base grid (one glyph a cell) and the layers asked for, each a
:class:`Layer`: marks on free cells (an ``area`` function read at a cell's centre), marks on
points (a ghost, a stalled machine, a pipe), shapes for the image (squares, circles, boxes) and
legend lines. Text draws one panel per layer; ``--image`` draws them all over one picture.

Zoom:
- 1, no layers: the look renders as before, byte for byte (``compose.render_scene``).
- above 1, a cell shows the first of (``decide``): a machine (the commonest kind), a roboport, a
  turret, belt when at least half of what touches the cell is belt (its main direction), a
  tank, a chest, an accumulator, a solar panel, a beacon, a radar, any belt, a pump, an
  inserter, a combinator, a pipe, a pole, a power switch, a wall; ``?`` anything else, ``.`` free
  ground, a blank an uncharted chunk (play mode).
- from 8 up (``PLOT_ZOOM``), each module plot is drawn as its outline with its name and machine
  count, the way the map view goes from entities to a chart.
- a map look (``map_mode``: zoom 8 and up, or an area over ``MAP_TILES``) reads counts per cell
  in the game (``fkcore.link.vision_read``), never every entity, so the whole base is fast. It
  says no verdicts: those come from a look at zoom 1 to 4.

Layers (``LAYERS``; the design knows ``DESIGN_LAYERS``): robots, power, defense, pollution,
pipes, recipes, tags. The facts come from the game's read (``facts_from_read``) or from the
design (``facts_from_design``), the same :class:`Facts` either way.

The recipe tags are a data file, ``fkcore/data/recipe-tags.json`` (``RECIPE_TAGS``), so the
prelude can own it later; a recipe it lacks gets a tag made from its name (``recipe_tag``).
Pure, stdlib only.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

from . import compose
from . import scene as scenemod

__all__ = ["ZOOMS", "LAYERS", "DESIGN_LAYERS", "PLOT_ZOOM", "MAP_TILES", "View", "Frame",
           "Facts", "Layer", "parse_layers", "map_mode", "cell_class", "decide", "scene_cells",
           "base_grid", "facts_from_design", "facts_from_read", "layers_for", "panel_lines",
           "render_image", "recipe_tag", "design_tags", "plots_of", "read_config",
           "compose_view", "RECIPE_TAGS"]

ZOOMS = (1, 2, 4, 8, 16, 32)
LAYERS = ("robots", "power", "defense", "pollution", "pipes", "recipes", "tags")
#: What the design knows: coverage, networks, turret ranges, recipes, tags.
DESIGN_LAYERS = ("robots", "power", "defense", "recipes", "tags")
#: From this zoom on, module plots are outlines with names and machine counts.
PLOT_ZOOM = 8
#: A look over more tiles than this (at a zoom above 1) is a map look: counts per cell.
MAP_TILES = 256 * 256
CHUNK = 32

# -- cells ----------------------------------------------------------------------------------------
_TYPE_CLASS = {
    "assembling-machine": "A", "rocket-silo": "A", "furnace": "F", "mining-drill": "D",
    "lab": "L", "reactor": "N", "boiler": "B", "generator": "E", "burner-generator": "E",
    "offshore-pump": "W", "roboport": "R", "ammo-turret": "T", "electric-turret": "T",
    "fluid-turret": "T", "artillery-turret": "T", "container": "C", "logistic-container": "C",
    "infinity-container": "C", "linked-container": "C", "storage-tank": "K", "accumulator": "Z",
    "solar-panel": "Y", "beacon": "b", "radar": "r", "transport-belt": "belt",
    "underground-belt": "belt", "splitter": "belt", "loader": "belt", "loader-1x1": "belt",
    "linked-belt": "belt", "lane-splitter": "belt", "pump": "P", "inserter": "i",
    "arithmetic-combinator": "c", "decider-combinator": "c", "constant-combinator": "c",
    "selector-combinator": "c", "pipe": "=", "pipe-to-ground": "%", "infinity-pipe": "=",
    "electric-pole": "+", "power-switch": "$", "wall": "#", "gate": "#",
    "land-mine": None,                       # the defense layer's; too small for a cell
}
_NAME_CLASS = {"chemical-plant": "H", "oil-refinery": "O", "pumpjack": "J"}
_ARROWS = {0: "^", 4: ">", 8: "v", 12: "<"}
_BELTS = "^>v<"
#: Machines, the first in a cell's order; among them the commonest wins, ties in this order.
MACHINES = "AHOFDJLNBEW"
#: Then, in this order, with the "mostly belt" rule between ``_FIRST`` and ``_THEN``.
_FIRST = "RT"
_THEN = "KCZYbr"
_LAST = "Pic=%+$#?"
#: The glyphs a cell legend names.
GLYPHS = {
    "A": "assembler", "H": "chemical plant", "O": "refinery", "F": "furnace", "D": "drill",
    "J": "pumpjack", "L": "lab", "N": "reactor", "B": "boiler", "E": "generator",
    "W": "offshore pump", "R": "roboport", "T": "turret", "K": "tank", "C": "chest",
    "Z": "accumulator", "Y": "solar panel", "b": "beacon", "r": "radar", "^": "belt north",
    ">": "belt east", "v": "belt south", "<": "belt west", "P": "pump", "i": "inserter",
    "c": "combinator", "=": "pipe", "%": "pipe to ground", "+": "pole", "$": "power switch",
    "#": "wall", "?": "something else", "u": "underground in", "U": "underground out",
    "S": "splitter",
}
#: Types a look never draws (render_scene's rule): the ground's, characters', items', corpses'.
_UNDRAWN = ("character", "item-entity", "corpse")


def cell_class(typ, name, direction):
    """The class a thing counts as in a cell: a glyph, a belt's arrow, or None (not drawn)."""
    c = _NAME_CLASS.get(name)
    if c is None:
        c = _TYPE_CLASS.get(typ, "?")
    if c == "belt":
        return _ARROWS.get(int(direction or 0) // 4 * 4, ">")
    return c


def _machine(typ, name):
    c = cell_class(typ, name, 0)
    return c is not None and c in MACHINES


def decide(counts):
    """A cell's glyph from its counts {class: n} (``cell_class``); ``.`` when empty."""
    if not counts:
        return "."
    machines = [(counts[m], -MACHINES.index(m), m) for m in MACHINES if counts.get(m)]
    if machines:
        return max(machines)[2]
    for g in _FIRST:
        if counts.get(g):
            return g
    belts = [(counts.get(b, 0), -_BELTS.index(b), b) for b in _BELTS]
    nbelt = sum(n for n, _k, _b in belts)
    if nbelt and 2 * nbelt >= sum(counts.values()):
        return max(belts)[2]
    for g in _THEN:
        if counts.get(g):
            return g
    if nbelt:
        return max(belts)[2]
    for g in _LAST:
        if counts.get(g):
            return g
    return "?"


@dataclass(frozen=True)
class View:
    """What a look draws: ``zoom`` (tiles a cell), ``layers`` and ``image`` (a PNG path, or
    None for text)."""
    zoom: int = 1
    layers: tuple = ()
    image: str | None = None

    @property
    def plain(self):
        """The look of 2.0.78, byte for byte."""
        return self.zoom == 1 and not self.layers and not self.image


def parse_layers(text):
    """``robots,power`` or ``all`` -> a tuple in ``LAYERS`` order; ValueError on a bad name."""
    names = [n.strip() for n in (text or "").split(",") if n.strip()]
    if names == ["all"]:
        return LAYERS
    bad = [n for n in names if n not in LAYERS]
    if bad:
        raise ValueError(f"a layer is one of {', '.join(LAYERS)} or all, got {', '.join(bad)}")
    return tuple(n for n in LAYERS if n in names)


def map_mode(area, zoom):
    """True when the look reads counts per cell: zoom 8 and up, or a big area above zoom 1."""
    x0, y0, x1, y1 = area
    return zoom >= PLOT_ZOOM or (zoom > 1 and (x1 - x0) * (y1 - y0) > MAP_TILES)


class Frame:
    """The area cut into cells ``zoom`` tiles wide (the last row and column may be narrower)."""

    def __init__(self, area, zoom):
        self.x0, self.y0, self.x1, self.y1 = (int(v) for v in area)
        self.z = int(zoom)
        self.cols = -(-(self.x1 - self.x0) // self.z)
        self.rows = -(-(self.y1 - self.y0) // self.z)

    def inside(self, i, j):
        return self.x0 <= i < self.x1 and self.y0 <= j < self.y1

    def cell(self, i, j):
        """The cell of tile (i, j)."""
        return ((int(i) - self.x0) // self.z, (int(j) - self.y0) // self.z)

    def cell_at(self, x, y):
        return self.cell(math.floor(x), math.floor(y))

    def centre(self, ci, cj):
        """The point at a cell's centre (a tile's centre at zoom 1)."""
        w = min(self.z, self.x1 - self.x0 - ci * self.z)
        h = min(self.z, self.y1 - self.y0 - cj * self.z)
        return self.x0 + ci * self.z + w / 2.0, self.y0 + cj * self.z + h / 2.0

    def cells(self):
        return ((ci, cj) for cj in range(self.rows) for ci in range(self.cols))

    def chunks(self):
        """The chunks the area touches, row by row."""
        return [(cx, cy) for cy in range(math.floor(self.y0 / CHUNK), math.floor((self.y1 - 1) / CHUNK) + 1)
                for cx in range(math.floor(self.x0 / CHUNK), math.floor((self.x1 - 1) / CHUNK) + 1)]

    def dark_cells(self, dark_chunks):
        """The cells whose centre lies in an uncharted chunk."""
        if not dark_chunks:
            return set()
        out = set()
        for ci, cj in self.cells():
            px, py = self.centre(ci, cj)
            if (math.floor(px / CHUNK), math.floor(py / CHUNK)) in dark_chunks:
                out.add((ci, cj))
        return out


def _drawn(t):
    return t["force"] not in ("enemy", "neutral") and t["type"] not in _UNDRAWN


def scene_cells(sc, frame):
    """{(ci, cj): {class: n}} of a scene: each thing counts once in every cell its tiles touch
    (the game's read counts the same way, by bounding box)."""
    cells = {}
    for t in sc["things"]:
        if not _drawn(t):
            continue
        cls = cell_class(t["type"], t["name"], t["direction"])
        if cls is None:
            continue
        for c in {frame.cell(i, j) for i, j in t["tiles"] if frame.inside(i, j)}:
            box = cells.setdefault(c, {})
            box[cls] = box.get(cls, 0) + 1
    return cells


def base_grid(frame, cat, sc=None, cells=None, dark=()):
    """{(ci, cj): glyph}: at zoom 1 from the scene exactly as ``compose.render_scene`` draws it,
    above 1 from the cells (``scene_cells`` of ``sc``, or the game's counts ``cells``). Cells in
    ``dark`` (uncharted) are blank."""
    grid = {}
    if frame.z == 1 and sc is not None:
        for (x, y), g in compose.scene_grid(sc, cat).items():
            if frame.inside(x, y):
                grid[(x - frame.x0, y - frame.y0)] = g
    else:
        if cells is None:
            cells = scene_cells(sc, frame) if sc is not None else {}
        for c, counts in cells.items():
            grid[c] = decide(counts)
    for c in dark:
        grid[c] = " "
    return grid


def grid_rows(frame, grid):
    return ["".join(grid.get((ci, cj), ".") for ci in range(frame.cols))
            for cj in range(frame.rows)]


# -- facts ----------------------------------------------------------------------------------------
#: Turret ranges where the game doesn't say (the design): base 2.0 prototype values; the Space
#: Age ones are not checked on the game.
TURRET_RANGE = {"gun-turret": 18, "laser-turret": 24, "flamethrower-turret": 30,
                "artillery-turret": 224, "rocket-turret": 36, "tesla-turret": 30,
                "railgun-turret": 40}
_TURRET_KIND = {"ammo-turret": "g", "electric-turret": "l", "fluid-turret": "f",
                "artillery-turret": "a"}
_TURRET_WORD = {"g": "gun", "l": "laser", "f": "flamethrower", "a": "artillery"}
_RECIPE_MACHINES = ("assembling-machine", "furnace", "rocket-silo")


@dataclass
class Facts:
    """What the layers draw from, the same shape for the game and the design."""
    source: str                                      # "live" | "design"
    knows: tuple = LAYERS
    roboports: list = field(default_factory=list)    # {x, y, name, lr, cr, net, ldock, cdock, charging, waiting}
    networks: dict = field(default_factory=dict)     # net -> {lall, lfree, call, cfree}
    ghosts: list = field(default_factory=list)       # (x, y, name)
    nghosts: int = 0
    poles: list = field(default_factory=list)        # {x, y, name, supply, net}
    machines: list = field(default_factory=list)     # {x, y, name, type, dir, status, recipe, net}
    switches: list = field(default_factory=list)     # {x, y, on}
    turrets: list = field(default_factory=list)      # {x, y, name, type, range, ammo: {item: n} | None}
    mines: list = field(default_factory=list)        # (x, y)
    walls: list = field(default_factory=list)        # (x, y, type)
    nests: list = field(default_factory=list)        # {x, y, name, type}
    pipes: list = field(default_factory=list)        # {x, y, name, type, dir, seg, fluid, amount, cap}
    opens: list = field(default_factory=list)        # (x, y, name, n)
    pollution: dict = field(default_factory=dict)    # (cx, cy) -> (value, nests in the chunk)
    dark: set = field(default_factory=set)           # uncharted chunks (play mode)
    plot_machines: dict = field(default_factory=dict)  # plot -> machines standing in it
    cells: dict | None = None                        # the game's counts per cell (map look)
    tick: int | None = None


def _components(items, linked):
    """Connected components (lists of indices, by smallest index) under ``linked(a, b)``."""
    parent = list(range(len(items)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            if linked(items[i], items[j]):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    out = {}
    for i in range(len(items)):
        out.setdefault(find(i), []).append(i)
    return [out[r] for r in sorted(out)]


def facts_from_design(sc, cat, plots=()):
    """The design's facts (``scene.design_scene``): roboports with the catalog's radii and their
    logistic networks (areas that touch join), poles with their wire components as networks,
    machines with recipes, turrets with ``TURRET_RANGE``, mines and walls. No status, robots,
    ghosts, pollution or pipes: the design doesn't know them."""
    f = Facts("design", knows=DESIGN_LAYERS)
    for t in sc["things"]:
        typ, name, x, y = t["type"], t["name"], t["x"], t["y"]
        row = cat.entity(name) if cat.has_entity(name) else {}
        if typ == "roboport":
            f.roboports.append({"x": x, "y": y, "name": name,
                                "lr": float(row.get("logistic_radius") or 25),
                                "cr": float(row.get("construction_radius") or 55), "net": None})
        elif typ == "electric-pole":
            f.poles.append({"x": x, "y": y, "name": name,
                            "supply": float(row.get("supply_area_distance") or 2.5),
                            "reach": float(row.get("max_wire_distance") or 7.5), "net": None})
        elif typ in _TURRET_KIND:
            f.turrets.append({"x": x, "y": y, "name": name, "type": typ,
                              "range": TURRET_RANGE.get(name), "ammo": None})
        elif typ == "land-mine":
            f.mines.append((x, y))
        elif typ in ("wall", "gate"):
            f.walls.append((x, y, typ))
        elif typ == "power-switch":
            f.switches.append({"x": x, "y": y, "on": None})
        if _machine(typ, name):
            f.machines.append({"x": x, "y": y, "name": name, "type": typ, "dir": t["direction"],
                               "status": None, "recipe": t["recipe"], "net": None})
    for k, group in enumerate(_components(
            f.roboports, lambda a, b: abs(a["x"] - b["x"]) <= a["lr"] + b["lr"]
            and abs(a["y"] - b["y"]) <= a["lr"] + b["lr"]), start=1):
        for i in group:
            f.roboports[i]["net"] = k
    for k, group in enumerate(_components(
            f.poles, lambda a, b: (a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2
            <= (min(a["reach"], b["reach"]) + 1e-6) ** 2), start=1):
        for i in group:
            f.poles[i]["net"] = k
    for m in f.machines:
        m["net"] = next((p["net"] for p in f.poles if abs(m["x"] - p["x"]) <= p["supply"] + 1
                         and abs(m["y"] - p["y"]) <= p["supply"] + 1), None)
    for p in plots:
        f.plot_machines[p[0]] = sum(1 for t in sc["things"]
                                    if _machine(t["type"], t["name"])
                                    and p[1] <= t["x"] < p[3] and p[2] <= t["y"] < p[4])
    return f


def _net(v):
    return None if v is None or int(v) < 0 else int(v)


def facts_from_read(raw, sc=None, plots=()):
    """The game's facts from ``link.vision_read``'s merged reply; ``sc`` (the look's snapshot
    scene, when there is one) gives the machine counts per plot."""
    f = Facts("live", tick=raw.get("tick"))
    f.dark = {tuple(c) for c in raw.get("dark") or ()}
    for r in raw.get("roboports") or ():
        f.roboports.append({"x": r[0], "y": r[1], "name": r[2], "lr": r[3], "cr": r[4],
                            "net": _net(r[5]), "ldock": r[6], "cdock": r[7], "charging": r[8],
                            "waiting": r[9]})
    for k, v in (raw.get("networks") or {}).items():
        f.networks[int(k)] = {"lall": v[0], "lfree": v[1], "call": v[2], "cfree": v[3]}
    f.ghosts = [tuple(g) for g in raw.get("ghosts") or ()]
    f.nghosts = int(raw.get("nghosts") or len(f.ghosts))
    f.poles = [{"x": p[0], "y": p[1], "name": p[2], "supply": p[3], "net": _net(p[4])}
               for p in raw.get("poles") or ()]
    f.machines = [{"x": m[0], "y": m[1], "name": m[2], "type": m[3], "dir": m[4],
                   "status": m[5] or None, "recipe": m[6] or None, "net": _net(m[7])}
                  for m in raw.get("machines") or ()]
    f.switches = [{"x": s[0], "y": s[1], "on": bool(s[2])} for s in raw.get("switches") or ()]
    f.turrets = [{"x": t[0], "y": t[1], "name": t[2], "type": t[3],
                  "range": t[4] or TURRET_RANGE.get(t[2]),
                  "ammo": (dict(t[5]) if isinstance(t[5], dict) else {})
                  if t[3] == "ammo-turret" else None} for t in raw.get("turrets") or ()]
    f.mines = [tuple(m) for m in raw.get("mines") or ()]
    f.walls = [tuple(w) for w in raw.get("walls") or ()]
    f.nests = [{"x": n[0], "y": n[1], "name": n[2], "type": n[3]} for n in raw.get("nests") or ()]
    f.pipes = [{"x": p[0], "y": p[1], "name": p[2], "type": p[3], "dir": p[4], "seg": p[5],
                "fluid": p[6] or None, "amount": p[7] or 0, "cap": p[8] or 0}
               for p in raw.get("pipes") or ()]
    f.opens = [tuple(o) for o in raw.get("opens") or ()]
    f.pollution = {(p[0], p[1]): (p[2], p[3]) for p in raw.get("pollution") or ()}
    if raw.get("cells") is not None:
        f.cells = {tuple(int(v) for v in k.split(",")): dict(v)
                   for k, v in raw["cells"].items()}
    f.plot_machines = dict(raw.get("plot_machines") or {})
    if sc is not None and plots:
        for p in plots:
            f.plot_machines[p[0]] = sum(1 for t in sc["things"] if not t["ghost"] and _drawn(t)
                                        and _machine(t["type"], t["name"])
                                        and p[1] <= t["x"] < p[3] and p[2] <= t["y"] < p[4])
    return f


# -- recipe tags ----------------------------------------------------------------------------------
_TAGS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "recipe-tags.json")


def _load_tags():
    try:
        with open(_TAGS_PATH, encoding="utf-8") as fh:
            return {k: v for k, v in json.load(fh).items() if not k.startswith("_")}
    except (OSError, ValueError):
        return {}


#: recipe -> short tag (``fkcore/data/recipe-tags.json``; the prelude may own it later).
RECIPE_TAGS = _load_tags()


def recipe_tag(recipe):
    """The short tag of a recipe: the table's, else made from its name (``iron-plate`` ->
    ``iplt``: the first word's initial and the last word's consonants, four letters at most)."""
    if not recipe:
        return "-"
    if recipe in RECIPE_TAGS:
        return RECIPE_TAGS[recipe]
    words = recipe.split("-")
    if len(words) == 1:
        return recipe[:4]
    last = words[-1]
    tail = last[0] + "".join(ch for ch in last[1:] if ch not in "aeiou")
    return (words[0][0] + tail)[:4]


# -- layers ---------------------------------------------------------------------------------------
#: Keys for networks, segments, recipes and instances: never a base glyph (b c i r u v, and
#: the capitals the cells use), so a key on free ground reads as a key.
_KEYS = "123456789adefghjkmnopqstwxyz"
_SAFE = "adefghjkmnopqstwxyzGIMQVX"
_NET_RGB = [(80, 200, 120), (90, 160, 240), (240, 200, 70), (230, 110, 200), (120, 230, 230),
            (250, 140, 60), (170, 130, 250), (200, 230, 90), (240, 90, 90)]


#: Networks named one a line at most; the rest are summed.
_MAX_NETS = 12


def _key(k):
    return _KEYS[k] if k < len(_KEYS) else "*"


def _net_rgb(k):
    return _NET_RGB[k % len(_NET_RGB)]


@dataclass
class Layer:
    """One layer of one render: ``area(px, py)`` -> (glyph, rgb) or None on free cells,
    ``points`` [(tile x, tile y, glyph, rank, rgb)] over anything (the highest rank in a cell
    wins), ``shapes`` for the image, and its legend lines."""
    name: str
    legend: list = field(default_factory=list)
    area: object = None
    points: list = field(default_factory=list)
    shapes: list = field(default_factory=list)       # ("square"|"circle", cx, cy, r, rgb) | ("box", x0, y0, x1, y1, rgb, label, width)
    chunk_tint: dict = field(default_factory=dict)    # (cx, cy) -> (rgb, alpha) (pollution)
    image_points: bool = True                         # the image draws the points (recipes: labels)


class _Index:
    """Items that reach ``reach(item)`` tiles around their (x, y), bucketed by 32-tile squares,
    so a point asks only the items near it (a whole-base look has thousands of poles)."""

    def __init__(self, items, reach):
        self.buckets = {}
        for it in items:
            r = reach(it)
            for bx in range(math.floor((it["x"] - r) / 32), math.floor((it["x"] + r) / 32) + 1):
                for by in range(math.floor((it["y"] - r) / 32),
                                math.floor((it["y"] + r) / 32) + 1):
                    self.buckets.setdefault((bx, by), []).append(it)

    def near(self, px, py):
        """The items whose reach may hold the point, in their list order."""
        return self.buckets.get((math.floor(px / 32), math.floor(py / 32)), ())


def _pt(x, y):
    return math.floor(float(x)), math.floor(float(y))


def _robots(f, frame):
    ports = sorted(f.roboports, key=lambda r: (r["x"], r["y"]))
    nets = sorted({r["net"] for r in ports if r["net"] is not None})
    key = {n: k for k, n in enumerate(nets)}
    lay = Layer("robots")

    idx = _Index(ports, lambda r: r["cr"])

    def area(px, py):
        best = None
        for r in idx.near(px, py):
            dx, dy = abs(px - r["x"]), abs(py - r["y"])
            if r["net"] is not None and dx <= r["lr"] and dy <= r["lr"]:
                return _key(key[r["net"]]), _net_rgb(key[r["net"]])
            if dx <= r["cr"] and dy <= r["cr"]:
                best = (",", None)              # the image draws its orange square only
        return best

    lay.area = area
    for r in ports:
        lay.shapes.append(("square", r["x"], r["y"], r["cr"], (240, 150, 40)))
        if r["net"] is not None:
            lay.shapes.append(("square", r["x"], r["y"], r["lr"], _net_rgb(key[r["net"]])))
    outside = [g for g in f.ghosts
               if not any(abs(g[0] - r["x"]) <= r["cr"] and abs(g[1] - r["y"]) <= r["cr"]
                          for r in ports)]
    for g in outside:
        i, j = _pt(g[0], g[1])
        lay.points.append((i, j, "!", 9, (255, 60, 220)))
    if not ports:
        lay.legend.append("robots: no roboport here")
    if len(nets) > _MAX_NETS:
        lay.legend.append(f"robots: {len(nets)} logistic networks; the first {_MAX_NETS} "
                          "follow")
    for n in nets[:_MAX_NETS]:
        mine = [r for r in ports if r["net"] == n]
        line = f"robots {_key(key[n])}: logistic network {n}, {len(mine)} roboport(s)"
        if f.source == "live":
            st = f.networks.get(n) or {}
            ld = sum(r.get("ldock") or 0 for r in mine)
            cd = sum(r.get("cdock") or 0 for r in mine)
            ch = sum(r.get("charging") or 0 for r in mine)
            wt = sum(r.get("waiting") or 0 for r in mine)
            la, lf = st.get("lall", 0), st.get("lfree", 0)
            ca, cf = st.get("call", 0), st.get("cfree", 0)
            line += (f"; logistic robots {la} ({la - lf} busy, {lf} idle, {max(0, la - ld)} in "
                     f"flight), construction {ca} ({ca - cf} busy, {cf} idle, {max(0, ca - cd)} "
                     f"in flight); docked {ld} logistic, {cd} construction; charging {ch}, "
                     f"waiting to charge {wt}")
        lay.legend.append(line)
    if any(r["net"] is None for r in ports):
        lay.legend.append(f"robots: {sum(1 for r in ports if r['net'] is None)} roboport(s) "
                          "in no logistic network (no power yet?)")
    if ports:
        lay.legend.append("robots: a digit or letter is that network's logistic area (green "
                          "squares), , construction area only (orange squares)")
    if f.source == "live":
        if outside:
            where = ", ".join(f"{g[2]} {scenemod.num(g[0])},{scenemod.num(g[1])}"
                              for g in outside[:6])
            more = f" and {len(outside) - 6} more" if len(outside) > 6 else ""
            lay.legend.append(f"robots: ! {len(outside)} ghost(s) outside every construction "
                              f"area, robots never build them: {where}{more}")
        else:
            lay.legend.append(f"robots: every ghost here ({f.nghosts}) is inside a construction "
                              "area")
    return lay


def _power(f, frame, cat):
    poles = sorted(f.poles, key=lambda p: (p["x"], p["y"]))
    nets = sorted({p["net"] for p in poles if p["net"] is not None})
    key = {n: k for k, n in enumerate(nets)}
    lay = Layer("power")

    idx = _Index(poles, lambda p: p["supply"])

    def area(px, py):
        for p in idx.near(px, py):
            if p["net"] is not None and abs(px - p["x"]) <= p["supply"] \
                    and abs(py - p["y"]) <= p["supply"]:
                return _key(key[p["net"]]), _net_rgb(key[p["net"]])
        return None

    lay.area = area
    for p in poles:
        if p["net"] is not None:
            lay.shapes.append(("fill", p["x"], p["y"], p["supply"], _net_rgb(key[p["net"]])))
    bad = {"no_power": ("X", (255, 50, 50)), "low_power": ("~", (255, 210, 40))}
    stalled = {}
    for m in f.machines:
        if m["status"] in bad:
            g, rgb = bad[m["status"]]
            i, j = _pt(m["x"], m["y"])
            lay.points.append((i, j, g, 8, rgb))
            stalled.setdefault(m["status"], []).append(m)
    for s in f.switches:
        i, j = _pt(s["x"], s["y"])
        lay.points.append((i, j, "$" if s["on"] is not False else "/", 7,
                           (240, 240, 240) if s["on"] is not False else (255, 120, 60)))
    if not poles:
        lay.legend.append("power: no pole here")
    for n in nets[:_MAX_NETS]:
        ps = [p for p in poles if p["net"] == n]
        ms = [m for m in f.machines if m["net"] == n]
        what = "network" if f.source == "live" else "wire component"
        lay.legend.append(f"power {_key(key[n])}: electric {what} {n}, {len(ps)} pole(s), "
                          f"{len(ms)} machine(s) in its supply area")
    if len(nets) > _MAX_NETS:
        rest = [p for p in poles if p["net"] in nets[_MAX_NETS:]]
        lay.legend.append(f"power: and {len(nets) - _MAX_NETS} more networks ({len(rest)} "
                          "poles)")
    if poles:
        lay.legend.append("power: a digit or letter is that network's pole supply area")
    for st, ms in sorted(stalled.items()):
        g = bad[st][0]
        where = ", ".join(f"{m['name']} {scenemod.num(m['x'])},{scenemod.num(m['y'])}"
                          for m in ms[:6])
        lay.legend.append(f"power: {g} {st} {len(ms)}: {where}")
    if f.source == "live" and f.machines and not stalled:
        lay.legend.append("power: no machine here is without power or short of it")
    for s in f.switches:
        state = "unknown" if s["on"] is None else ("on (closed)" if s["on"] else "off (open)")
        lay.legend.append(f"power: switch {scenemod.num(s['x'])},{scenemod.num(s['y'])} {state} "
                          f"($ on, / off)")
    return lay


def _defense(f, frame):
    lay = Layer("defense")
    turrets = [t for t in f.turrets if t["range"]]
    idx = _Index(turrets, lambda t: t["range"])

    def area(px, py):
        kinds = {_TURRET_KIND.get(t["type"], "a") for t in idx.near(px, py)
                 if (px - t["x"]) ** 2 + (py - t["y"]) ** 2 <= t["range"] ** 2}
        if not kinds:
            return None
        return ("*" if len(kinds) > 1 else kinds.pop()), None    # the image draws circles

    lay.area = area
    for t in turrets:
        rgb = {"g": (240, 200, 60), "l": (255, 90, 90), "f": (255, 150, 40)}.get(
            _TURRET_KIND.get(t["type"]), (180, 180, 255))
        lay.shapes.append(("circle", t["x"], t["y"], t["range"], rgb))
    for t in f.turrets:
        i, j = _pt(t["x"], t["y"])
        empty = t["ammo"] is not None and not sum(t["ammo"].values())
        lay.points.append((i, j, "0" if empty else "T", 6,
                           (255, 60, 60) if empty else (230, 230, 230)))
    for x, y in f.mines:
        i, j = _pt(x, y)
        lay.points.append((i, j, "x", 4, (255, 170, 0)))
    open_walls = []
    for x, y, typ in f.walls:
        i, j = _pt(x, y)
        covered = any((x - t["x"]) ** 2 + (y - t["y"]) ** 2 <= t["range"] ** 2
                      for t in idx.near(x, y))
        if not covered:
            open_walls.append((x, y))
        lay.points.append((i, j, "!" if not covered else ("G" if typ == "gate" else "#"),
                           5 if not covered else 3,
                           (255, 50, 50) if not covered else (170, 170, 170)))
    for n in f.nests:
        i, j = _pt(n["x"], n["y"])
        lay.points.append((i, j, "@" if n["type"] == "unit-spawner" else "w", 9, (255, 40, 40)))
    by = {}
    for t in f.turrets:
        by.setdefault(t["name"], []).append(t)
    for name, ts in sorted(by.items()):
        k = _TURRET_KIND.get(ts[0]["type"], "a")
        rng = ts[0]["range"]
        line = f"defense {k}: {len(ts)} {name} ({_TURRET_WORD.get(k, 'turret')}), range " \
               f"{scenemod.num(rng) if rng else '?'}"
        ammo = {}
        empty = 0
        for t in ts:
            if t["ammo"] is not None:
                if not sum(t["ammo"].values()):
                    empty += 1
                for a, n in t["ammo"].items():
                    ammo[a] = ammo.get(a, 0) + n
        if ammo:
            line += "; ammo " + ", ".join(f"{a} {n}" for a, n in sorted(ammo.items()))
        if empty:
            line += f"; {empty} EMPTY (0)"
        lay.legend.append(line)
    if not f.turrets:
        lay.legend.append("defense: no turret here")
    else:
        lay.legend.append("defense: g gun, l laser, f flamethrower, a artillery range, * two "
                          "kinds; T a turret")
    nw = sum(1 for w in f.walls if w[2] == "wall")
    ng = len(f.walls) - nw
    if f.walls:
        lay.legend.append(f"defense: # {nw} wall(s), G {ng} gate(s); ! {len(open_walls)} of "
                          "them outside every turret's range (a gap in the ring)")
    if f.mines:
        lay.legend.append(f"defense: x {len(f.mines)} land mine(s)")
    if f.source == "live":
        sp = sum(1 for n in f.nests if n["type"] == "unit-spawner")
        lay.legend.append(f"defense: @ {sp} nest(s), w {len(f.nests) - sp} worm(s) here")
    return lay


#: Pollution bands per chunk: glyph, upper bound.
POLLUTION_BANDS = (("1", 20.0), ("2", 100.0), ("3", 500.0), ("4", float("inf")))


def _band(v):
    if not v or v <= 0:
        return None
    return next(g for g, top in POLLUTION_BANDS if v <= top)


def _pollution(f, frame):
    lay = Layer("pollution")
    tint = {"1": 0.08, "2": 0.16, "3": 0.26, "4": 0.38}

    def area(px, py):
        v = f.pollution.get((math.floor(px / CHUNK), math.floor(py / CHUNK)))
        g = _band(v[0]) if v else None
        return (g, None) if g else None

    lay.area = area
    for c, (v, _n) in f.pollution.items():
        g = _band(v)
        if g:
            lay.chunk_tint[c] = ((150, 100, 50), tint[g])
    reached = sorted((c, v, n) for c, (v, n) in f.pollution.items() if n and v > 0)
    hot = {c for c, _v, _n in reached}
    for n in f.nests:
        i, j = _pt(n["x"], n["y"])
        hit = (math.floor(n["x"] / CHUNK), math.floor(n["y"] / CHUNK)) in hot
        lay.points.append((i, j, "!" if hit else "@", 9, (255, 40, 40) if hit else (200, 80, 80)))
    vals = [v for v, _n in f.pollution.values()]
    if not vals:
        lay.legend.append("pollution: no chunk read")
        return lay
    top = max(f.pollution.items(), key=lambda kv: kv[1][0])
    lay.legend.append(f"pollution: {len(vals)} chunk(s), {sum(1 for v in vals if v > 0)} "
                      f"polluted, total {sum(vals):.0f}, most {top[1][0]:.1f} in chunk "
                      f"{top[0][0]},{top[0][1]}; 1 up to 20, 2 up to 100, 3 up to 500, 4 more")
    if reached:
        lay.legend.append("pollution: ! reaches a nest in " + ", ".join(
            f"chunk {c[0]},{c[1]} ({v:.1f}, {n} nest(s))" for c, v, n in reached[:8]))
    else:
        nests = sum(n for _v, n in f.pollution.values())
        lay.legend.append(f"pollution: reaches no nest ({nests} nest(s) in these chunks)")
    return lay


_FLUID_RGB = {"water": (60, 130, 230), "steam": (220, 220, 230), "crude-oil": (90, 70, 60),
              "heavy-oil": (200, 110, 40), "light-oil": (230, 190, 60),
              "petroleum-gas": (170, 90, 170), "sulfuric-acid": (210, 230, 60),
              "lubricant": (90, 200, 90)}


def _pipes(f, frame, cat):
    lay = Layer("pipes")
    segs = {}
    for p in f.pipes:
        s = segs.setdefault(p["seg"], {"fluid": None, "amount": 0.0, "cap": 0.0, "pipes": 0,
                                       "tanks": 0, "pumps": 0})
        s["fluid"] = s["fluid"] or p["fluid"]
        s["amount"] += p["amount"]
        s["cap"] += p["cap"]
        s["pipes" if p["type"] in ("pipe", "pipe-to-ground") else
          "tanks" if p["type"] == "storage-tank" else "pumps"] += 1
    order = sorted(segs, key=lambda s: (s is None, s if s is not None else 0))
    key = {s: k for k, s in enumerate(order)}
    letters = _SAFE
    for p in f.pipes:
        k = key[p["seg"]]
        g = {"storage-tank": "K", "pump": "P"}.get(p["type"],
                                                   letters[k] if k < len(letters) else "*")
        s = segs[p["seg"]]
        rgb = _FLUID_RGB.get(s["fluid"], (150, 150, 170)) if s["fluid"] else (90, 90, 90)
        tiles = scenemod.footprint(p["name"], p["x"], p["y"], p["dir"], cat) \
            if cat is not None else [list(_pt(p["x"], p["y"]))]
        for i, j in tiles:
            lay.points.append((i, j, g, 4 if p["type"] == "pump" else 2, rgb))
    for x, y, name, n in f.opens:
        i, j = _pt(x, y)
        lay.points.append((i, j, "!", 9, (255, 50, 50)))
    for s in order:
        d = segs[s]
        k = key[s]
        fill = f"{100 * d['amount'] / d['cap']:.0f}% full" if d["cap"] else "fill ?"
        lay.legend.append(f"pipes {letters[k] if k < len(letters) else '*'}: "
                          f"{d['fluid'] or 'empty'}, {fill}, {d['amount']:.0f}/{d['cap']:.0f} "
                          f"({d['pipes']} pipe(s), {d['tanks']} tank(s) K, {d['pumps']} "
                          f"pump(s) P)")
    if not f.pipes:
        lay.legend.append("pipes: no pipe here")
    if f.opens:
        where = ", ".join(f"{name} {scenemod.num(x)},{scenemod.num(y)} ({n})"
                          for x, y, name, n in f.opens[:6])
        lay.legend.append(f"pipes: ! {len(f.opens)} machine(s) with a fluid connection that "
                          f"nothing meets: {where}")
    elif f.source == "live":
        lay.legend.append("pipes: every fluid connection of a machine here is met")
    return lay


#: A status, as a recipe mark at zoom 1.
STATUS_MARK = {"working": "*", "normal": "*", "no_power": "P", "low_power": "p",
               "no_ingredients": "n", "item_ingredient_shortage": "n",
               "fluid_ingredient_shortage": "n", "no_input_fluid": "n", "full_output": "f",
               "waiting_for_space_in_destination": "f", "output_full": "f", "no_recipe": "0",
               "disabled_by_control_behavior": "d", "disabled_by_script": "d",
               "marked_for_deconstruction": "x", "no_fuel": "u", "low_input_fluid": "n",
               "waiting_for_source_items": "n", "no_minable_resources": "m"}


def recipe_keys(*facts):
    """{tag: index} over the recipes of every source, so design and live share their keys."""
    tags = sorted({recipe_tag(m["recipe"]) for f in facts if f is not None for m in f.machines
                   if m["type"] in _RECIPE_MACHINES})
    return {t: k for k, t in enumerate(tags)}


def _recipes(f, frame, cat, key=None):
    lay = Layer("recipes")
    ms = [m for m in f.machines if m["type"] in _RECIPE_MACHINES]
    tags = sorted({recipe_tag(m["recipe"]) for m in ms})
    key = key or recipe_keys(f)
    lay.image_points = False
    for m in ms:
        tag = recipe_tag(m["recipe"])
        working = m["status"] in (None, "working", "normal")
        rgb = (120, 230, 120) if m["status"] in ("working", "normal") else \
            (200, 200, 200) if m["status"] is None else \
            (255, 70, 70) if m["status"] in ("no_power", "low_power") else (255, 200, 60)
        if frame.z == 1:
            tiles = sorted(scenemod.footprint(m["name"], m["x"], m["y"], m["dir"], cat),
                           key=lambda t: (t[1], t[0]))
            top = min(t[1] for t in tiles)
            row = [t for t in tiles if t[1] == top]
            for (i, j), ch in zip(row, tag):
                lay.points.append((i, j, ch, 5, rgb))
            if m["status"] is not None:
                for i, j in tiles:
                    if j != top:
                        lay.points.append((i, j, STATUS_MARK.get(m["status"], "?"), 4, rgb))
        else:
            i, j = _pt(m["x"], m["y"])
            lay.points.append((i, j, _key(key[tag]) if working else "!", 5 if working else 6,
                               rgb))
        tiles = scenemod.footprint(m["name"], m["x"], m["y"], m["dir"], cat)
        lay.shapes.append(("label", min(t[0] for t in tiles), min(t[1] for t in tiles),
                           max(t[0] for t in tiles) + 1, max(t[1] for t in tiles) + 1, tag, rgb))
        m["_tag"] = tag
    for t in tags:
        mine = [m for m in ms if m["_tag"] == t]
        recipes = sorted({m["recipe"] or "no recipe" for m in mine})
        k = key[t]
        head = f"recipes {t}" if frame.z == 1 else f"recipes {_key(k)} {t}"
        line = f"{head} ({', '.join(recipes)}): {len(mine)} machine(s)"
        if f.source == "live":
            st = {}
            for m in mine:
                st[m["status"] or "?"] = st.get(m["status"] or "?", 0) + 1
            line += " (" + ", ".join(f"{n} {s}" for s, n in sorted(st.items())) + ")"
        lay.legend.append(line)
    if not ms:
        lay.legend.append("recipes: no crafting machine here")
    elif f.source == "live":
        if frame.z == 1:
            seen = sorted({(STATUS_MARK.get(m["status"], "?"), m["status"]) for m in ms
                           if m["status"]})
            lay.legend.append("recipes: the tag on a machine's top row, its status below: "
                              + ", ".join(f"{g} {s}" for g, s in seen))
        else:
            lay.legend.append("recipes: a cell shows its recipe's key, ! when a machine there "
                              "doesn't work")
    elif frame.z == 1:
        lay.legend.append("recipes: the tag on a machine's top row")
    lay.legend.append("recipes: tags from fkcore/data/recipe-tags.json")
    return lay


def plots_of(s):
    """A composed surface's plots: [(name, x0, y0, x1, y1, module)], top to bottom."""
    return [(p.factory, *p.box(), (p.params or {}).get("module", p.factory))
            for p in sorted(s.plots.values(), key=lambda p: (p.y, p.x, p.factory))]


def design_tags(game, sf, s):
    """(plots, insts) of a composed surface: plots [(name, x0, y0, x1, y1, module)], insts
    [(name, kind, x0, y0, x1, y1)], an instance's box being its entities' tiles."""
    from . import fkp
    plots = plots_of(s)
    insts = []
    for name in sorted(s.plots):
        place = next((pl for pl in getattr(sf, "places", {}).values() if pl.factory == name),
                     None)
        module = place.module if place is not None else name
        path = os.path.join(game, f"{module}.fkp") if game else None
        if not path or not os.path.exists(path):
            continue
        try:
            lay = fkp.load(path)
        except (OSError, ValueError):
            continue
        for ipath, inst in sorted(lay.insts.items()):
            if "." in ipath:
                continue                                  # a top-level instance's parts
            prefix = f"{s.name}:{name}/{ipath}/"
            tiles = [t for k, e in s.entities.items() if k.startswith(prefix) for t in e.tiles]
            sub = [k for k in lay.insts if k.startswith(ipath + ".")]
            for k, e in s.entities.items():
                if any(k.startswith(f"{s.name}:{name}/{q}/") for q in sub):
                    tiles.extend(e.tiles)
            if tiles:
                insts.append((f"{name}/{ipath}", inst.kind, min(t[0] for t in tiles),
                              min(t[1] for t in tiles), max(t[0] for t in tiles) + 1,
                              max(t[1] for t in tiles) + 1))
    return plots, insts


def _tags(tags, frame):
    plots, insts = tags
    lay = Layer("tags")
    letters = _SAFE
    inside = {}
    border = {}
    for name, x0, y0, x1, y1, _module in plots:
        c0, r0 = frame.cell(x0, y0)
        c1, r1 = frame.cell(x1 - 1, y1 - 1)
        for ci in range(c0, c1 + 1):
            border[(ci, r0)] = border[(ci, r1)] = "-"
        for cj in range(r0, r1 + 1):
            border[(c0, cj)] = border[(c1, cj)] = "|"
        for c in ((c0, r0), (c1, r0), (c0, r1), (c1, r1)):
            border[c] = "*"
    for k, (name, kind, x0, y0, x1, y1) in enumerate(insts):
        g = letters[k] if k < len(letters) else "*"
        lay.points.append((x0, y0, g, 8, (110, 220, 230)))
        lay.shapes.append(("box", x0, y0, x1, y1, (110, 220, 230), name.split("/", 1)[1], 1))
        for cj in range(max(0, (y0 - frame.y0) // frame.z),
                        min(frame.rows, (y1 - 1 - frame.y0) // frame.z + 1)):
            for ci in range(max(0, (x0 - frame.x0) // frame.z),
                            min(frame.cols, (x1 - 1 - frame.x0) // frame.z + 1)):
                inside.setdefault((ci, cj), g)
        lay.legend.append(f"tags {g}: instance {name} ({kind}) at {x0},{y0} {x1 - x0}x{y1 - y0}")
    for name, x0, y0, x1, y1, module in plots:
        lay.shapes.append(("box", x0, y0, x1, y1, (255, 255, 255), name, 2))
        lay.legend.append(f"tags: plot {name} ({module}) at {x0},{y0} {x1 - x0}x{y1 - y0}")
    if not plots and not insts:
        lay.legend.append("tags: no plot or instance in the design")

    def area(px, py):
        c = frame.cell_at(px, py)
        g = border.get(c) or inside.get(c)
        return (g, None) if g else None

    lay.area = area
    lay.image_points = False
    if insts:
        lay.legend.append("tags: a letter marks an instance's top-left corner and its free "
                          "cells; - | * a plot's edge")
    return lay


def layers_for(f, frame, names, cat=None, tags=None, rkeys=None):
    """The ``names`` layers of one source's facts; those the source doesn't know are left out.
    ``rkeys``: the recipe keys both sources share (``recipe_keys``)."""
    out = []
    for n in names:
        if n not in f.knows:
            continue
        if n == "robots":
            out.append(_robots(f, frame))
        elif n == "power":
            out.append(_power(f, frame, cat))
        elif n == "defense":
            out.append(_defense(f, frame))
        elif n == "pollution":
            out.append(_pollution(f, frame))
        elif n == "pipes":
            out.append(_pipes(f, frame, cat))
        elif n == "recipes":
            out.append(_recipes(f, frame, cat, rkeys))
        elif n == "tags":
            out.append(_tags(tags or ([], []), frame))
    return out


# -- text -----------------------------------------------------------------------------------------
def with_plots(frame, grid, plots, counts):
    """The grid with each plot drawn as its outline, name and machine count (zoom 8 and up):
    a plot ``-``/``|`` with ``*`` corners when it is at least 3 cells by 2, the label on its top
    row. -> (grid, legend lines)."""
    g = dict(grid)
    legend = []
    for name, x0, y0, x1, y1, _module in plots:
        n = counts.get(name)
        label = f"{name} {n}" if n is not None else name
        legend.append(f"plot {name}: {n if n is not None else '?'} machine(s), at {x0},{y0} "
                      f"{x1 - x0}x{y1 - y0}")
        c0, r0 = (x0 - frame.x0) // frame.z, (y0 - frame.y0) // frame.z
        c1, r1 = (x1 - 1 - frame.x0) // frame.z, (y1 - 1 - frame.y0) // frame.z
        if c1 - c0 + 1 < 3 or r1 - r0 + 1 < 2:
            continue
        for ci in range(c0, c1 + 1):
            for cj in (r0, r1):
                g[(ci, cj)] = "-"
        for cj in range(r0, r1 + 1):
            for ci in (c0, c1):
                g[(ci, cj)] = "|"
        for c in ((c0, r0), (c1, r0), (c0, r1), (c1, r1)):
            g[c] = "*"
        for k, ch in enumerate(label[:max(0, c1 - c0 - 1)]):
            g[(c0 + 1 + k, r0)] = ch
    return {k: v for k, v in g.items() if 0 <= k[0] < frame.cols and 0 <= k[1] < frame.rows}, \
        legend


def overlay(frame, grid, lay, dark=()):
    """The grid with one layer drawn: a point over anything, an area mark on free cells."""
    g = dict(grid)
    best = {}
    for i, j, ch, rank, _rgb in lay.points:
        if not frame.inside(i, j):
            continue
        c = frame.cell(i, j)
        if c in dark:
            continue
        if c not in best or rank > best[c][0]:
            best[c] = (rank, ch)
    if lay.area is not None:
        for c in frame.cells():
            if c in best or c in dark or g.get(c, ".") != ".":
                continue
            m = lay.area(*frame.centre(*c))
            if m:
                g[c] = m[0]
    for c, (_r, ch) in best.items():
        g[c] = ch
    return g


def cell_legend(*grids):
    """``cells: A assembler, ...`` for the glyphs the grids show."""
    seen = set()
    for g in grids:
        seen.update(g.values())
    names = [f"{k} {v}" for k, v in GLYPHS.items() if k in seen] + [". free"]
    if " " in seen:
        names.append("(blank) uncharted")
    return "cells: " + ", ".join(names)


def panel_lines(frame, title, design_rows, live_rows, max_width=160):
    """Two renders (cell rows) side by side, each row labelled with its top tile's y; one
    above the other when two would not fit; one render when ``design_rows`` is None. -> (lines,
    cells that differ)."""
    z = frame.z
    head = f"x {frame.x0}..{frame.x1 - 1}, y {frame.y0}..{frame.y1 - 1}"
    if z > 1:
        head = f"zoom {z} (a cell is {z}x{z} tiles), " + head
    if design_rows is None:
        out = [f"{title}: live, {head}"]
        out += [f"{frame.y0 + k * z:>5} {r}" for k, r in enumerate(live_rows)]
        return out, 0
    if live_rows is None:
        out = [f"{title}: design, {head}"]
        out += [f"{frame.y0 + k * z:>5} {r}" for k, r in enumerate(design_rows)]
        return out, 0
    differ = sum(1 for a, b in zip(design_rows, live_rows) for p, q in zip(a, b)
                 if p != q and q != " ")
    out = [f"{title}: design | live, {head}: {differ} cells differ"]
    if 2 * frame.cols + 9 <= max_width:
        out += [f"{frame.y0 + k * z:>5} {a} | {b}"
                for k, (a, b) in enumerate(zip(design_rows, live_rows))]
    else:
        out += ["design:"] + [f"{frame.y0 + k * z:>5} {r}" for k, r in enumerate(design_rows)]
        out += ["live:"] + [f"{frame.y0 + k * z:>5} {r}" for k, r in enumerate(live_rows)]
    return out, differ


# -- image ----------------------------------------------------------------------------------------
_RGB = {
    "A": (80, 105, 160), "H": (60, 140, 140), "O": (110, 85, 140), "F": (170, 105, 55),
    "D": (125, 95, 60), "J": (105, 80, 70), "L": (145, 145, 200), "N": (60, 170, 60),
    "B": (150, 80, 60), "E": (125, 125, 125), "W": (60, 110, 175), "R": (200, 150, 40),
    "T": (190, 60, 60), "K": (90, 125, 145), "C": (160, 130, 80), "Z": (100, 100, 150),
    "Y": (40, 60, 115), "b": (130, 60, 130), "r": (120, 140, 120), "^": (205, 175, 45),
    ">": (205, 175, 45), "v": (205, 175, 45), "<": (205, 175, 45), "u": (175, 145, 40),
    "U": (175, 145, 40), "S": (215, 160, 35), "P": (80, 140, 200), "i": (110, 160, 90),
    "c": (90, 150, 150), "=": (100, 130, 175), "%": (80, 110, 155), "+": (150, 110, 70),
    "$": (200, 200, 200), "#": (140, 140, 140), "?": (190, 0, 190), "-": (255, 255, 255),
    "|": (255, 255, 255), "*": (255, 255, 255),
}
_FREE = (30, 34, 38)
_DARK = (8, 8, 10)
_INK = (15, 15, 15)


def _size(frame, panels):
    cp = min(48, max(2, 1800 // max(1, frame.cols * panels)), max(2, 1400 // max(1, frame.rows)))
    return cp


def _panel(cv, frame, grid, layers, ox, oy, cp, plots=(), counts=None, dark=()):
    from .png import FONT_H, FONT_W
    z = frame.z

    def px(x):
        return ox + (x - frame.x0) / z * cp

    def py(y):
        return oy + (y - frame.y0) / z * cp

    W, H = frame.cols * cp, frame.rows * cp
    cv.rect(ox, oy, W, H, _FREE)
    for (ci, cj), gl in grid.items():
        if gl == ".":
            continue
        cv.rect(ox + ci * cp, oy + cj * cp, cp, cp, _DARK if gl == " " else _RGB.get(gl, (190, 0, 190)))
    for lay in layers:
        for (cx, cy), (rgb, a) in lay.chunk_tint.items():
            x0, y0 = px(max(cx * CHUNK, frame.x0)), py(max(cy * CHUNK, frame.y0))
            x1, y1 = px(min((cx + 1) * CHUNK, frame.x1)), py(min((cy + 1) * CHUNK, frame.y1))
            cv.rect(x0, y0, x1 - x0, y1 - y0, rgb, a)
        if lay.area is not None and not lay.chunk_tint and lay.name != "tags":
            for c in frame.cells():
                if c in dark:
                    continue
                m = lay.area(*frame.centre(*c))
                if m and m[1] is not None:
                    free = grid.get(c, ".") == "."
                    cv.rect(ox + c[0] * cp, oy + c[1] * cp, cp, cp, m[1], 0.2 if free else 0.08)
    if cp >= FONT_H:
        for (ci, cj), gl in grid.items():
            if gl not in ". ":
                cv.text(ox + ci * cp + (cp - FONT_W) // 2 + 1,
                        oy + cj * cp + (cp - FONT_H) // 2 + 1, gl, _INK)
    clip = (ox, oy, ox + W, oy + H)
    for lay in layers:
        for sh in lay.shapes:
            if sh[0] == "square":
                _, x, y, r, rgb = sh
                _clipped_outline(cv, px(x - r), py(y - r), px(x + r), py(y + r), rgb, clip)
            elif sh[0] == "circle":
                _, x, y, r, rgb = sh
                _clipped_circle(cv, px(x), py(y), r / z * cp, rgb, clip)
            elif sh[0] == "label":
                _, x0, y0, x1, y1, label, rgb = sh
                w = int((px(x1) - px(x0)) // FONT_W)
                if w >= 1 and frame.inside(x0, y0) and px(x1) - px(x0) >= FONT_W:
                    cv.outline(px(x0), py(y0), px(x1) - px(x0), py(y1) - py(y0), rgb)
                    cv.text(px(x0) + 1, py(y0) + 2, label[:w], rgb, bg=(0, 0, 0))
                elif frame.inside(x0, y0):
                    cv.rect(px(x0), py(y0), max(2, px(x1) - px(x0)), max(2, py(y1) - py(y0)),
                            rgb, 0.6)
            elif sh[0] == "box":
                _, x0, y0, x1, y1, rgb, label, w = sh
                if not (x0 < frame.x1 and frame.x0 < x1 and y0 < frame.y1 and frame.y0 < y1):
                    continue
                _clipped_outline(cv, px(x0), py(y0), px(x1), py(y1), rgb, clip, w)
                if label and px(x0) + 2 >= ox and py(y0) + 2 >= oy:
                    cv.text(px(x0) + 3, py(y0) + 3, label[:max(1, int((px(x1) - px(x0)) // FONT_W))],
                            rgb, bg=(0, 0, 0))
    for lay in layers:
        if not lay.image_points:
            continue
        best = {}
        for i, j, ch, rank, rgb in lay.points:
            if not frame.inside(i, j):
                continue
            c = frame.cell(i, j)
            if c in dark:
                continue
            if c not in best or rank > best[c][0]:
                best[c] = (rank, ch, rgb)
        for (ci, cj), (_r, ch, rgb) in best.items():
            x, y = ox + ci * cp, oy + cj * cp
            if cp >= FONT_H:
                cv.rect(x, y, cp, cp, (0, 0, 0), 0.55)
                cv.text(x + (cp - FONT_W) // 2 + 1, y + (cp - FONT_H) // 2 + 1, ch, rgb)
            else:
                cv.rect(x, y, cp, cp, rgb)
    for name, x0, y0, x1, y1, _m in plots:
        if not (x0 < frame.x1 and frame.x0 < x1 and y0 < frame.y1 and frame.y0 < y1):
            continue
        _clipped_outline(cv, px(x0), py(y0), px(x1), py(y1), (255, 255, 255), clip, 2)
        n = (counts or {}).get(name)
        label = f"{name} {n}" if n is not None else name
        if px(x0) + 3 < ox + W and py(y0) + 3 < oy + H:
            cv.text(max(ox, px(x0)) + 4, max(oy, py(y0)) + 4, label, (255, 255, 255),
                    bg=(0, 0, 0))


def _clipped_outline(cv, x0, y0, x1, y1, rgb, clip, w=1):
    cx0, cy0, cx1, cy1 = clip
    a, b = max(x0, cx0), max(y0, cy0)
    c, d = min(x1, cx1), min(y1, cy1)
    if a >= c or b >= d:
        return
    if y0 >= cy0:
        cv.rect(a, y0, c - a, w, rgb)
    if y1 <= cy1:
        cv.rect(a, y1 - w, c - a, w, rgb)
    if x0 >= cx0:
        cv.rect(x0, b, w, d - b, rgb)
    if x1 <= cx1:
        cv.rect(x1 - w, b, w, d - b, rgb)


def _clipped_circle(cv, cx, cy, r, rgb, clip):
    x0, y0, x1, y1 = clip
    steps = max(24, int(r * 6.3))
    for k in range(steps):
        a = 2 * math.pi * k / steps
        x, y = cx + r * math.cos(a), cy + r * math.sin(a)
        if x0 <= x < x1 and y0 <= y < y1:
            cv.rect(int(x), int(y), 1, 1, rgb)


def render_image(path, frame, title, panels, legend):
    """Write the PNG: ``panels`` [(name, grid, layers, plots, counts, dark)] side by side with
    their titles, the legend lines under them. -> path."""
    from .png import FONT_H, FONT_W, Canvas
    cp = _size(frame, len(panels))
    W, H = frame.cols * cp, frame.rows * cp
    gap, top = 12, FONT_H * 2 + 10
    width = max(len(panels) * (W + gap) + gap, 900)
    chars = max(40, (width - 2 * gap) // FONT_W)
    wrapped = []
    for line in [title] + list(legend):
        while len(line) > chars:
            cut = line.rfind(" ", 0, chars)
            cut = cut if cut > chars // 2 else chars
            wrapped.append(line[:cut])
            line = "    " + line[cut:].lstrip()
        wrapped.append(line)
    height = top + H + gap + (len(wrapped) + 1) * (FONT_H + 3)
    cv = Canvas(width, height, (16, 18, 22))
    for k, (name, grid, layers, plots, counts, dark) in enumerate(panels):
        ox = gap + k * (W + gap)
        cv.text(ox, 6, name, (230, 230, 230))
        _panel(cv, frame, grid, layers, ox, top, cp, plots, counts, dark)
    y = top + H + gap
    for k, line in enumerate(wrapped):
        cv.text(gap, y + k * (FONT_H + 3), line, (235, 235, 235) if k == 0 else (190, 195, 200))
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return cv.save(path)


# -- one look -------------------------------------------------------------------------------------
def read_config(surface, force, frame, layers, play, plots, cells, skip):
    """The ``link.vision_read`` config of one look: what to read in which chunks, and the cell
    classes (this module's, so the game counts exactly as ``scene_cells`` does)."""
    want = {n: n in layers for n in LAYERS if n != "tags"}
    want["cells"] = bool(cells)
    return {"surface": surface, "force": force,
            "area": [frame.x0, frame.y0, frame.x1, frame.y1],
            "chunks": [list(c) for c in frame.chunks()], "zoom": frame.z, "play": bool(play),
            "want": want,
            "classes": {"types": {k: (v if v is not None else "") for k, v in _TYPE_CLASS.items()},
                        "names": dict(_NAME_CLASS),
                        "dirs": {str(k): v for k, v in _ARROWS.items()}},
            "machines": list(MACHINES), "plots": [list(p[:5]) for p in plots],
            "skip": list(skip), "scan": any(want.values()) or bool(plots)}


def compose_view(view, frame, cat, design_sc=None, live_sc=None, live=None, plots=(),
                 tags=None, title="look", plain_base=None):
    """One look's renders: -> (lines, image path or None). ``design_sc`` / ``live_sc``: the two
    scenes (either may be None); ``live``: the game's facts (``facts_from_read``), which carry the
    counts per cell on a map look; ``plots`` / ``tags``: ``design_tags``. ``plain_base``: the
    zoom-1 base lines the caller already has (``look.side_by_side``), kept as they are. Text: the
    base render, then a panel a layer, each with its legend lines. Image: the legend lines and the
    PNG's path."""
    def meets(b):
        return b[0] < frame.x1 and frame.x0 < b[2] and b[1] < frame.y1 and frame.y0 < b[3]

    plots = [p for p in plots if meets(p[1:5])]
    if tags is not None:
        tags = (plots, [i for i in tags[1] if meets(i[2:6])])
    dark = frame.dark_cells(live.dark) if live is not None and frame.z > 1 else set()
    dfacts = facts_from_design(design_sc, cat, plots) if design_sc is not None else None
    rkeys = recipe_keys(dfacts, live)
    dgrid = base_grid(frame, cat, sc=design_sc) if design_sc is not None else None
    lgrid = None
    if live is not None or live_sc is not None:
        lgrid = base_grid(frame, cat, sc=live_sc,
                          cells=live.cells if live is not None and live.cells is not None
                          else None, dark=dark)
    zoomed_plots = plots if frame.z >= PLOT_ZOOM else ()
    tdg, tlg, plot_legend = dgrid, lgrid, []
    if zoomed_plots:
        if dgrid is not None:
            tdg, pl = with_plots(frame, dgrid, plots, dfacts.plot_machines)
            plot_legend += [f"design {x}" for x in pl] if lgrid is not None else pl
        if lgrid is not None:
            tlg, pl = with_plots(frame, lgrid, plots, live.plot_machines if live else {})
            plot_legend += pl

    def rows(g):
        return grid_rows(frame, g) if g is not None else None

    text = list(plain_base) if plain_base is not None else \
        panel_lines(frame, "render", rows(tdg), rows(tlg))[0]
    legend = [cell_legend(*[g for g in (tdg, tlg) if g is not None])] + plot_legend
    text += legend
    panels_d, panels_l = [], []
    for name in view.layers:
        dl = layers_for(dfacts, frame, [name], cat, tags, rkeys) if dfacts is not None else []
        if name == "tags":
            ll = layers_for(Facts("live"), frame, [name], cat, tags) if lgrid is not None else []
        else:
            ll = layers_for(live, frame, [name], cat, tags, rkeys) if live is not None else []
        dl, ll = (dl[0] if dl else None), (ll[0] if ll else None)
        if dl is None and ll is None:
            line = f"layer {name}: the design doesn't know it, and this look reads no game"
            text.append(line)
            legend.append(line)
            continue
        lines = []
        if ll is not None:
            lines += ll.legend
            if dl is not None and name != "tags":
                lines += [f"design {x}" for x in dl.legend if not x.startswith("recipes: tags")]
        else:
            lines += dl.legend
            if lgrid is not None:
                lines.append(f"layer {name}: the design's only")
        drows = grid_rows(frame, overlay(frame, tdg, dl)) if dl is not None else None
        lrows = grid_rows(frame, overlay(frame, tlg, ll, dark)) if ll is not None else None
        text += panel_lines(frame, f"layer {name}", drows, lrows)[0] + lines
        legend += lines
        if dl is not None:
            panels_d.append(dl)
        if ll is not None:
            panels_l.append(ll)
    if not view.image:
        return text, None
    panels = []
    if dgrid is not None:
        panels.append(("design", dgrid, panels_d, zoomed_plots, dfacts.plot_machines, set()))
    if lgrid is not None:
        panels.append(("live", lgrid, panels_l, zoomed_plots,
                       live.plot_machines if live else {}, dark))
    head = (f"{title}: x {frame.x0}..{frame.x1 - 1}, y {frame.y0}..{frame.y1 - 1}, zoom "
            f"{frame.z}" + (f", layers {','.join(view.layers)}" if view.layers else ""))
    path = render_image(view.image, frame, head, panels, legend)
    full = os.path.abspath(path)
    rel = os.path.relpath(full)
    shown = rel if not rel.startswith("..") and not os.path.isabs(rel) else full
    return legend + [f"image: {shown}"], path
