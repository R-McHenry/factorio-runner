"""fkcore.compose: a surface's composed layout, its tile index and whose a tile is.

The composed layout (CORE-V1.md §4.1) is every placed factory's .fkp turned into its plot plus the
surface's own .fkp. It is computed, never stored. A plot ``at X,Y rot R size WxH`` turns the
factory's frame (``size w x h``) R quarter turns clockwise about its origin, then moves it so the
turned frame's top-left tile lands on (X, Y): rotating never moves the corner you named. Keys
become ``<surface>:<factory>/<owner>/<local>``; the surface's own entities are
``<surface>:<owner>/<local>``.

Circuit wires (2.0): each factory's ``w`` records come along with their ends' composed keys and
the net named ``<factory>/<net>``, as carriers' nets are; the surface's own keep theirs.

Composition reports the invariants it finds (two entities on a tile across plots, two plots
overlapping, an entity outside its plot, a plot with no factory, shared infrastructure inside a
plot, plus each factory's own) rather than raising: the caller decides; a writer refuses.
:meth:`Surface.whose` answers "whose tile is this?" for vision, diff and adopt. Pure, stdlib only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import fkp as fkpmod

__all__ = ["Placed", "Surface", "compose", "turn", "render"]


@dataclass
class Placed:
    key: str
    name: str
    x: float
    y: float
    dir: int | None
    attrs: dict
    plot: str | None              # the factory whose plot holds it; None for shared infra
    tiles: tuple

    @property
    def net(self):
        return self.attrs.get("net")


@dataclass
class Surface:
    name: str
    entities: dict = field(default_factory=dict)       # key -> Placed
    tiles: dict = field(default_factory=dict)          # (x, y) -> key
    plots: dict = field(default_factory=dict)
    claims: dict = field(default_factory=dict)
    marks: dict = field(default_factory=dict)
    problems: list = field(default_factory=list)
    wires: list = field(default_factory=list)          # [fkp.Wire], composed keys (2.0)

    def whose(self, x, y):
        """(verdict, detail) for tile (x, y) (a position works too: it is floored): entity, claim,
        mark, plot or sanctioned."""
        t = (math.floor(x), math.floor(y))
        key = self.tiles.get(t)
        if key is not None:
            e = self.entities[key]
            return "entity", {"key": key, "name": e.name, "net": e.net, "plot": e.plot}
        for c in self.claims.values():
            if c.x0 <= t[0] < c.x1 and c.y0 <= t[1] < c.y1:
                return "claim", {"key": f"{self.name}:claim/{c.name}", "reason": c.reason}
        for m in self.marks.values():
            if t in {(math.floor(a), math.floor(b)) for a, b in m.tiles}:
                return "mark", {"key": f"{self.name}:mark/{m.name}"}
        for p in self.plots.values():
            x0, y0, x1, y1 = p.box()
            if x0 <= t[0] < x1 and y0 <= t[1] < y1:
                return "plot", {"plot": p.factory}
        return "sanctioned", {}

    def rows(self):
        """The composed entities as codec rows (sorted by key)."""
        return [{"src": e.key, "name": e.name, "x": e.x, "y": e.y, "direction": e.dir,
                 "attrs": dict(e.attrs)}
                for e in sorted(self.entities.values(), key=lambda e: fkpmod.natural_key(e.key))]


def turn(x, y, d, rot, size):
    """A point and direction in a factory frame of ``size`` turned ``rot`` quarter turns
    clockwise, with the turned frame's top-left at (0, 0)."""
    w, h = size
    for _ in range(rot % 4):
        x, y = h - y, x            # clockwise about the origin, then shift the frame back to x >= 0
        w, h = h, w
        if d is not None:
            d = (d + 4) % 16
    return x, y, d


def compose(surface: fkpmod.Layout, factories: dict, cat) -> Surface:
    """``surface`` (a surface .fkp) + ``factories`` {name: factory .fkp} -> Surface."""
    if surface.level != "surface":
        raise ValueError(f"{surface.name} is a {surface.level} layout, not a surface")
    s = Surface(surface.name, plots=dict(surface.plots), claims=dict(surface.claims),
                marks=dict(surface.marks))
    names = sorted(surface.plots, key=fkpmod.natural_key)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if _overlap(surface.plots[a].box(), surface.plots[b].box()):
                s.problems.append(f"plots {a} and {b} overlap")

    def place(key, e, x, y, d, plot, box):
        try:
            tiles = tuple(sorted(cat.tiles(e.name, float(x), float(y), d if d is not None else 0)))
        except (ValueError, KeyError) as exc:
            s.problems.append(f"{key}: {exc}")
            return
        for t in tiles:
            if t in s.tiles:
                s.problems.append(f"{key}: tile {t[0]},{t[1]} is already {s.tiles[t]}")
            else:
                s.tiles[t] = key
            if box is not None and not (box[0] <= t[0] < box[2] and box[1] <= t[1] < box[3]):
                s.problems.append(f"{key}: tile {t[0]},{t[1]} is outside plot {plot}")
        s.entities[key] = Placed(key, e.name, x, y, d, dict(e.attrs), plot, tiles)

    for name in names:
        p = surface.plots[name]
        fac = factories.get(name)
        if fac is None:
            s.problems.append(f"plot {name}: no factory layout called {name}")
            continue
        if fac.size is None:
            s.problems.append(f"plot {name}: the factory layout has no size line")
            continue
        tw, th = fac.size if p.rot % 2 == 0 else (fac.size[1], fac.size[0])
        if tw > p.w or th > p.h:
            s.problems.append(f"plot {name}: the factory ({tw}x{th} turned) does not fit "
                              f"{p.w}x{p.h}")
        s.problems.extend(f"{name}: {msg}" for msg in fkpmod.invariants(fac, cat))
        for key in sorted(fac.entities, key=fkpmod.natural_key):
            e = fac.entities[key]
            x, y, d = turn(float(e.x), float(e.y), e.dir, p.rot, fac.size)
            place(f"{surface.name}:{name}/{key}", e, _clean(x + p.x), _clean(y + p.y), d, name,
                  p.box())
        s.wires += [fkpmod.Wire(f"{name}/{w.net}", w.colour, f"{surface.name}:{name}/{w.a}",
                                f"{surface.name}:{name}/{w.b}") for w in fac.wires]
    s.problems.extend(f"{surface.name}: {msg}" for msg in fkpmod.invariants(surface, cat))
    for key in sorted(surface.entities, key=fkpmod.natural_key):
        e = surface.entities[key]
        full = f"{surface.name}:{key}"
        place(full, e, e.x, e.y, e.dir, None, None)
        for t in s.entities[full].tiles if full in s.entities else ():
            for p in surface.plots.values():
                x0, y0, x1, y1 = p.box()
                if x0 <= t[0] < x1 and y0 <= t[1] < y1:
                    s.problems.append(f"{full}: shared infrastructure inside plot {p.factory}")
                    break
    s.wires += [fkpmod.Wire(w.net, w.colour, f"{surface.name}:{w.a}", f"{surface.name}:{w.b}")
                for w in surface.wires]
    s.wires = fkpmod.sort_wires(s.wires)
    return s


def _clean(v):
    return int(v) if float(v).is_integer() else v


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


# -- render ---------------------------------------------------------------------------------------
_ARROW = {0: "^", 4: ">", 8: "v", 12: "<"}
_GLYPH = {"assembling-machine": "A", "furnace": "F", "mining-drill": "D", "lab": "L",
          "container": "C", "logistic-container": "C", "electric-pole": "+", "roboport": "R",
          "ammo-turret": "T", "splitter": "S", "pipe": "=", "pipe-to-ground": "%", "boiler": "B",
          "generator": "E", "offshore-pump": "W", "inserter": "i", "reactor": "N"}


def _glyph(cat, name, d, attrs):
    kind = cat.kind(name)
    if kind == "transport-belt":
        return _ARROW.get(d or 0, "?")
    if kind == "underground-belt":
        return "u" if attrs.get("ug") == "in" else "U"
    if kind == "assembling-machine" and name == "chemical-plant":
        return "H"
    if kind == "assembling-machine" and name == "oil-refinery":
        return "O"
    if name == "pumpjack":
        return "J"
    return _GLYPH.get(kind, "?")


def scene_grid(sc, cat):
    """{(x, y): glyph} of a scene, one glyph a tile, as :func:`render_scene` draws it (the last
    thing on a tile wins). fkcore.vision draws its zoom-1 layers over it."""
    grid = {}
    for t in sc["things"]:
        if t["force"] in ("enemy", "neutral") or t["type"] in ("character", "item-entity", "corpse"):
            continue
        if not cat.has_entity(t["name"]):
            glyph = "?"
        else:
            glyph = _glyph(cat, t["name"], t["direction"],
                           {"ug": {"input": "in", "output": "out"}.get(t.get("ug"))})
        for x, y in t["tiles"]:
            grid[(x, y)] = glyph
    return grid


def render_scene(sc, cat, box):
    """ASCII of a scene (``fkcore.scene``: the design or the live game) inside ``box``, with the
    same glyphs as :func:`render`; a ghost is drawn as its entity. Free tiles are ``.``. The
    design and a live capture of a finished apply render the same text (look parity)."""
    grid = scene_grid(sc, cat)
    rows = []
    for y in range(box[1], box[3]):
        rows.append("".join(grid.get((x, y), ".") for x in range(box[0], box[2])).rstrip("."))
    return "\n".join(rows)


def render(surface: Surface, cat, box=None) -> str:
    """ASCII of ``box`` (x0, y0, x1, y1; default: everything): entities by glyph, ``.`` a free
    tile in a plot, ``:`` a claim, ``!`` a mark, a space sanctioned ground."""
    if box is None:
        xs = [t[0] for t in surface.tiles] + [p.x for p in surface.plots.values()] + \
             [p.x + p.w - 1 for p in surface.plots.values()]
        ys = [t[1] for t in surface.tiles] + [p.y for p in surface.plots.values()] + \
             [p.y + p.h - 1 for p in surface.plots.values()]
        box = (min(xs), min(ys), max(xs) + 1, max(ys) + 1)
    rows = []
    for y in range(box[1], box[3]):
        line = []
        for x in range(box[0], box[2]):
            verdict, detail = surface.whose(x, y)
            if verdict == "entity":
                e = surface.entities[detail["key"]]
                line.append(_glyph(cat, e.name, e.dir, e.attrs))
            else:
                line.append({"claim": ":", "mark": "!", "plot": "."}.get(verdict, " "))
        rows.append("".join(line).rstrip())
    return "\n".join(rows)
