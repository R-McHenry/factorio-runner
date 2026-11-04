"""fkcore.placer: the V1 placer's ``@patch`` hint (CORE-V1.md §9 Placer): a MinerRow on real ore.

:func:`place_on_patch` picks a row's origin and rotation inside its factory's plot (the plot in
the factory frame, ``FrameGround.box``) so that

- **every drill stands on ore** of the row's resource: every tile under the drill's body holds it;
- **no row entity is on blocked ground** (water, cliffs) or on a tile something placed already
  holds (``taken``: entity tiles, instance frames and their route ends); the row's frame covers
  nothing taken, and the tiles its routes start or end on are free and inside the plot;
- among those spots, the drills' **mining areas** (their union: 5x5 per electric drill) hold the
  most ore tiles, then the most ore, then the drill bodies the most ore; ties go north, then
  west, then to the lower rotation. Every rotation is tried, so a row is laid along a patch's
  long axis when that is where it fits.

The candidates are the origins that put the first drill's first tile on ore, so the search is
over the ore, not over the plot. Deterministic, pure, stdlib only. It never changes ``n``: when
no spot holds every drill, :func:`largest_fit` says how many drills of the same row would fit,
and where, for the report.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .catalog import MINING_RADIUS
from .compose import turn

__all__ = ["Spot", "shape", "mining_area", "place_on_patch", "largest_fit"]

_H = {0: 0, 4: 1, 8: 2, 12: 3}
_DX = (0, 1, 0, -1)
_DY = (-1, 0, 1, 0)


@dataclass
class Spot:
    x: int
    y: int
    rot: int
    drills: int              # drills with ore under every tile of their body
    under: int               # ore tiles under the drill bodies
    under_amount: int
    reach: int               # ore tiles in the union of the drills' mining areas
    reach_amount: int
    size: tuple              # the turned frame, (w, h)


@dataclass
class _Shape:
    w: int
    h: int
    ents: frozenset          # every entity tile, relative to the origin
    bodies: list             # [frozenset] one per drill
    areas: list              # [frozenset] one per drill: its mining area
    ends: frozenset          # the tiles the row's routes start or end on (outside its entities)


def mining_area(name, x, y):
    """The tiles a drill of ``name`` centred at (x, y) mines (``catalog.MINING_RADIUS``)."""
    r = MINING_RADIUS.get(name, 2.49)
    return frozenset((i, j) for i in range(math.floor(x - r) - 1, math.ceil(x + r) + 1)
                     for j in range(math.floor(y - r) - 1, math.ceil(y + r) + 1)
                     if abs(i + 0.5 - x) <= r and abs(j + 0.5 - y) <= r)


def shape(frag, cat, rot) -> _Shape:
    """The fragment turned ``rot`` quarter turns (as ``expand`` turns it), relative to its origin."""
    w, h = frag.size if rot % 2 == 0 else frag.size[::-1]
    ents, bodies, areas = set(), [], []
    for fe in frag.entities:
        x, y, d = turn(float(fe.x), float(fe.y), fe.dir, rot, frag.size)
        tiles = cat.tiles(fe.name, x, y, d if d is not None else 0)
        ents |= tiles
        if cat.kind(fe.name) == "mining-drill":
            bodies.append(frozenset(tiles))
            areas.append(mining_area(fe.name, x, y))
    ends = set()
    for role in frag.roles.values():
        for ax, ay, ad in role.attach:
            if ad is None:
                continue
            x, y, d = turn(float(ax), float(ay), ad, rot, frag.size)
            k = _H[int(d)]
            tx, ty = math.floor(x), math.floor(y)
            sign = 1 if role.side == "out" else -1
            ends.add((tx + sign * _DX[k], ty + sign * _DY[k]))
    return _Shape(w, h, frozenset(ents), bodies, areas, frozenset(ends - ents))


def place_on_patch(frag, cat, ore, blocked, taken, box):
    """The best :class:`Spot` for ``frag`` (a MinerRow fragment), or None.

    ``ore``: {tile: amount} of the row's resource in the factory frame; ``blocked``: terrain
    tiles; ``taken``: tiles held by what is placed already; ``box``: the plot, (x0, y0, x1, y1)."""
    if not ore:
        return None
    bx0, by0, bx1, by1 = box
    best, best_key = None, None
    for rot in range(4):
        sh = shape(frag, cat, rot)
        if not sh.bodies:
            continue
        first = min(sh.bodies[0], key=lambda t: (t[1], t[0]))
        seen = set()
        for ox, oy in sorted(ore, key=lambda t: (t[1], t[0])):
            ox, oy = ox - first[0], oy - first[1]
            if (ox, oy) in seen:
                continue
            seen.add((ox, oy))
            spot = _judge(sh, ox, oy, rot, ore, blocked, taken, (bx0, by0, bx1, by1))
            if spot is None:
                continue
            key = (spot.reach, spot.reach_amount, spot.under_amount, -oy, -ox, -rot)
            if best_key is None or key > best_key:
                best, best_key = spot, key
    return best


def _judge(sh, ox, oy, rot, ore, blocked, taken, box):
    bx0, by0, bx1, by1 = box
    if not (bx0 <= ox and by0 <= oy and ox + sh.w <= bx1 and oy + sh.h <= by1):
        return None
    under = amount = 0
    for body in sh.bodies:
        for i, j in body:
            a = ore.get((ox + i, oy + j))
            if a is None:
                return None
            under += 1
            amount += a
    for i, j in sh.ents:
        t = (ox + i, oy + j)
        if t in blocked or t in taken:
            return None
    for i, j in sh.ends:
        t = (ox + i, oy + j)
        if t in blocked or t in taken or not (bx0 <= t[0] < bx1 and by0 <= t[1] < by1):
            return None
    for j in range(oy, oy + sh.h):
        for i in range(ox, ox + sh.w):
            if (i, j) in taken:
                return None
    reach = set()
    for area in sh.areas:
        reach |= {(ox + i, oy + j) for i, j in area}
    held = [ore[t] for t in reach if t in ore]
    return Spot(ox, oy, rot, len(sh.bodies), under, amount, len(held), sum(held), (sh.w, sh.h))


def largest_fit(make, n, cat, ore, blocked, taken, box):
    """(k, Spot) for the largest k < n whose row (``make(k)`` -> its fragment) has a spot, or
    (0, None): the report's 'n=8 does not fit; 6 would'."""
    for k in range(n - 1, 0, -1):
        spot = place_on_patch(make(k), cat, ore, blocked, taken, box)
        if spot is not None:
            return k, spot
    return 0, None
