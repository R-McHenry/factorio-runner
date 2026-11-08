"""fkcore.cover: power coverage, the flow's ``cover power`` step (CORE-V1.md §4.2, §7, §9).

:func:`cover_power` takes a factory :class:`fkcore.fkp.Layout` and the catalog and returns the poles
to add (:class:`fkcore.fkp.Entity` rows keyed ``<owner>/p<k>``, no net) and a :class:`CoverReport`.
Two rules: every electric entity (``Catalog.is_electric``, plus the grid's own sources: generators,
solar panels, accumulators) overlaps some pole's supply area, and every pole, new or already in the
layout, is on ONE wire network. :func:`apply_cover` returns a new Layout with the poles and the
record ``cover <owner> power pole=<pole>``.

Geometry (v4): a pole powers an entity when the entity's tile rectangle ``[x0, x1) x [y0, y1)``
overlaps the pole's supply square ``[px - s, px + s]`` with positive area (``s`` =
``cat.pole_supply``). Every pole prototype has its square on whole tile edges, so this is exact for
the game's collision boxes too. Two poles are wired when their centres are at most the smaller
``cat.pole_reach`` apart. Positions here are exact, so there is no cushion (v4 kept 0.25 for live
positions).

A pole goes on a **spot**: its footprint's top-left tile, every tile of it inside the layout's
``size`` frame, on no entity's tile and not in ``keep_out``. Nothing is read from the world: route
tiles, plot edges and anything else to keep clear come in as ``keep_out``. The steps:

1. **Cover**: greedy set cover over free spots, v4's rule: the spot powering the most still
   unpowered entities, ties to the spot nearest the existing poles' centroid, then north, then west.
2. **Bridge**: while the poles form more than one wire component, the fewest new poles joining the
   main component (the existing poles' one, else the first pick's) to the nearest other component
   that can be joined: a BFS over free spots, each step within wire reach, inside the box around
   both components plus ``BRIDGE_PAD`` tiles. A component nothing can join stays apart, reported.
3. **Prune**: drop each new pole, newest first, whose entities other poles power too and whose
   removal splits no component. Greedy cover leaves a few of those. The wired neighbours of a
   pole that went are looked at again (a bridge to it can be idle now).

New poles are numbered north to south, then west to east. Poles already under ``owner`` and named
``p<k>`` are the previous run's: they are ignored (neither obstacle nor wire node) and
:func:`apply_cover` replaces them; every other pole in the layout is an existing wire node that is
never moved. 1.7 (Rory, 2026-10-21: rebuilds pin what stands and patch, they don't regenerate):
``pinned`` poles, the committed .fkp's own ``p<k>``, stay where they still fit (no new entity or
keep-out tile under them) and power what they reach, so a rebuild adds poles only where coverage
is missing and never re-lays the grid that stands (both overnight players lost power to a re-laid
grid). New poles take numbers after the pinned ones; the prune step drops a pinned pole only when
it powers nothing at all and joins nothing (a new pole never replaces a standing one). What
cannot be done is a report line, never raised: ``NO_POWER`` for an entity no free spot can power
(with what takes the spots), ``power-split`` for a pole component left apart.

Taken from C:\\fk-lead\\repo (d6018d3), copied, never imported: from fk\\power.py ``cover`` (the
greedy cover, its tie-break, existing poles as wire nodes, the bridge loop), ``unpowered`` and
``_overlap`` (the supply rule), ``components`` / ``_groups`` (union-find over wire reach, minus
the game's network ids: a layout has none), ``_search_links`` (the fewest-pole BFS and its search
box) and ``bridge``; from fk\\reroute.py ``plan``: consumers in a fixed order, kept poles as
anchors, reserved tiles as a set the planner may not use (``_Reserved``, here ``keep_out``). New for
V1: the layout's frame, poles of any footprint, the prune step, owner-keyed poles and the report.
Pure, stdlib only, deterministic: the same layout gives the same poles whatever its entity order.
"""
from __future__ import annotations

import copy
import math
import re
from collections import deque
from dataclasses import dataclass, field

from . import fkp
from .elaborate import Check
from .fkp import Entity, natural_key

__all__ = ["CoverReport", "cover_power", "apply_cover", "GRID_SOURCE_TYPES", "BRIDGE_PAD"]

_EPS = 1e-9
#: Entity types that put power on the grid: they need a pole's supply area like a consumer does.
GRID_SOURCE_TYPES = frozenset({"generator", "solar-panel", "accumulator",
                               "electric-energy-interface"})
#: How far (tiles) a bridge may detour outside the box of the two components it joins (v4).
BRIDGE_PAD = 24
_SHOW = 4


@dataclass
class CoverReport:
    owner: str
    pole: str
    poles: list                    # keys of the poles added, in key order
    total: int                     # electric entities in the layout
    covered: int                   # of them inside some pole's supply area, new or existing
    components: int                # wire-connected groups of every pole, new and existing
    uncovered: list                # [(key, reason)]
    splits: list                   # [[pole keys]]: each component apart from the main one
    bridges: int                   # new poles placed only to join components
    pruned: int                    # greedy picks dropped again as redundant
    existing: int                  # poles already in the layout (wire nodes)
    checks: list = field(default_factory=list)      # [Check]: NO_POWER, POWER_SPLIT
    pinned: int = 0                # of ``poles``, the committed ones kept where they stand (1.7)

    @property
    def ok(self):
        return self.covered == self.total and self.components <= 1

    def lines(self):
        return [str(c) for c in self.checks]

    def summary(self):
        s = "" if self.components == 1 else "s"
        kept = f"{self.pinned} kept where they stand, " if self.pinned else ""
        return (f"cover {self.owner} power pole={self.pole}: {len(self.poles)} poles "
                f"({kept}{self.bridges} bridging, {self.existing} existing); "
                f"{self.covered}/{self.total} electric entities covered; "
                f"{self.components} wire component{s}")


@dataclass
class _Node:
    """A pole as a wire node: an existing one (``key``) or a new one (``spot``)."""
    x: float
    y: float
    supply: float
    reach: float
    key: str | None = None
    spot: tuple | None = None
    bridge: bool = False
    dom: object = None             # 2.1: its power domain (codec.pole_domain); wired only within


# -- geometry -------------------------------------------------------------------------------------
def _rect(tiles):
    """Half-open tile rectangle ``(x0, x1, y0, y1)`` around a set of tiles."""
    xs = [t[0] for t in tiles]
    ys = [t[1] for t in tiles]
    return (min(xs), max(xs) + 1, min(ys), max(ys) + 1)


def _overlap(rect, px, py, s):
    """True when the tile rectangle overlaps the supply square with positive area (v4)."""
    x0, x1, y0, y1 = rect
    return x0 < px + s and x1 > px - s and y0 < py + s and y1 > py - s


def _wired(a, b, reach=None):
    """True when two poles are within wire reach of each other (the smaller of both)."""
    limit = min(a.reach, b.reach) if reach is None else reach
    dx, dy = a.x - b.x, a.y - b.y
    return dx * dx + dy * dy <= (limit + _EPS) ** 2


def networks(ents, cat):
    """The wire networks of an entity list (a layout's or a composed surface's entities):
    ``[{"poles": [keys], "sources": [keys]}]``, a source being a grid source (generator, solar,
    accumulator ...) inside some pole's supply area. Poles wire to every pole in reach, as the game
    auto-wires them; wires are not recorded anywhere yet. 2.1: only poles of one power domain
    (``codec.pole_domain``) are wired together."""
    ents = sorted(ents, key=lambda e: natural_key(e.key))
    poles = [e for e in ents if cat.kind(e.name) == "electric-pole"]
    from .codec import pole_domain
    nodes = [_Node(float(e.x), float(e.y), cat.pole_supply(e.name), cat.pole_reach(e.name), e.key,
                   dom=pole_domain({"src": e.key, "attrs": e.attrs})) for e in poles]
    sources = [(e.key, _rect(fkp.tiles_of(cat, e))) for e in ents
               if cat.kind(e.name) in GRID_SOURCE_TYPES]
    out = []
    for group in _groups(nodes):
        found = sorted({k for k, rect in sources for i in group
                        if _overlap(rect, nodes[i].x, nodes[i].y, nodes[i].supply)},
                       key=natural_key)
        out.append({"poles": [nodes[i].key for i in group], "sources": found})
    return out


def _groups(nodes):
    """Wire components as lists of node indices, each in index order, by smallest index (v4)."""
    parent = list(range(len(nodes)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    # 2.1.26: only poles in neighbouring cells of a grid as wide as the longest reach can wire,
    # so each pole is checked against those, not every other pole. The live 20261030-2352 world's
    # cover_power called this once per pole it tried to prune: 30 s and more. The same pairs
    # join, and a root is always its component's smallest index, so the groups are the same
    cell = max([n.reach for n in nodes] + [1.0]) + _EPS
    grid = {}
    for i, n in enumerate(nodes):
        grid.setdefault((math.floor(n.x / cell), math.floor(n.y / cell)), []).append(i)
    for (cx, cy), here in grid.items():
        near = [j for dx in (-1, 0, 1) for dy in (-1, 0, 1) for j in grid.get((cx + dx, cy + dy), ())]
        for i in here:
            for j in near:
                if j > i and nodes[i].dom == nodes[j].dom and _wired(nodes[i], nodes[j]):
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[max(ri, rj)] = min(ri, rj)
    grouped = {}
    for i in range(len(nodes)):
        grouped.setdefault(find(i), []).append(i)
    return [grouped[r] for r in sorted(grouped)]


class _Ground:
    """Where a new pole may stand: the frame, the entities' tiles, keep_out, poles placed so far.
    2.1 (power domains): ``deny``, the tile rectangles of another domain's machines, which a new
    pole's supply square (``supply``) may not reach."""

    def __init__(self, taken, keep_out, size, pole_size, deny=(), supply=0.0):
        self.taken = taken                       # tile -> entity key
        self.keep_out = keep_out
        self.size = size                         # (W, H) or None
        # 2.1.35: the box new poles stand in, (x0, y0, x1, y1): the frame, or a sealed unit's
        self.bounds = None if size is None else (0, 0, size[0], size[1])
        self.pw, self.ph = pole_size
        self.placed = set()
        self.deny = list(deny)
        self.supply = supply

    def tiles(self, spot):
        i, j = spot
        return [(i + dx, j + dy) for dy in range(self.ph) for dx in range(self.pw)]

    def centre(self, spot):
        return spot[0] + self.pw / 2, spot[1] + self.ph / 2

    def why(self, spot):
        """None when a pole fits on ``spot``, else what stops it: frame, entity, keep_out, pole."""
        if self.bounds is not None:
            x0, y0, x1, y1 = self.bounds
            if any(not (x0 <= x < x1 and y0 <= y < y1) for x, y in self.tiles(spot)):
                return "frame"
        for t in self.tiles(spot):
            if t in self.taken:
                return "entity"
        for t in self.tiles(spot):
            if t in self.keep_out:
                return "keep_out"
        for t in self.tiles(spot):
            if t in self.placed:
                return "pole"
        if self.deny:
            cx, cy = self.centre(spot)
            if any(_overlap(r, cx, cy, self.supply) for r in self.deny):
                return "domain"
        return None

    def free(self, spot):
        """``why(spot) is None``, in one pass (2.1.26: the bridge search asks it of every spot
        within wire reach of every pole it walks from, and ``why`` built the tiles four times)."""
        i, j = spot
        pw, ph = self.pw, self.ph
        if self.bounds is not None:
            x0, y0, x1, y1 = self.bounds
            if i < x0 or j < y0 or i + pw > x1 or j + ph > y1:
                return False
        taken, keep_out, placed = self.taken, self.keep_out, self.placed
        for y in range(j, j + ph):
            for x in range(i, i + pw):
                t = (x, y)
                if t in taken or t in keep_out or t in placed:
                    return False
        if self.deny:
            cx, cy = i + pw / 2, j + ph / 2
            if any(_overlap(r, cx, cy, self.supply) for r in self.deny):
                return False
        return True

    def place(self, spot):
        self.placed.update(self.tiles(spot))

    def lift(self, spot):
        self.placed.difference_update(self.tiles(spot))

    def spots_powering(self, rect, s):
        """Every spot (free or not) from which a pole's supply square overlaps ``rect``."""
        x0, x1, y0, y1 = rect
        out = []
        for j in range(math.floor(y0 - s - self.ph), math.ceil(y1 + s) + 1):
            for i in range(math.floor(x0 - s - self.pw), math.ceil(x1 + s) + 1):
                cx, cy = self.centre((i, j))
                if _overlap(rect, cx, cy, s):
                    out.append((i, j))
        return out


# -- bridging (v4 _search_links) ------------------------------------------------------------------
def bridge(ents, cat, keep_out=(), pole="medium-electric-pole", owner="bridge"):
    """Shared infrastructure (CORE-V1.md §6.2): new poles joining every wire network that reaches
    no grid source to one that does, on free tiles outside ``keep_out`` (a surface passes every
    plot's and claim's tiles: a bridge stands only in sanctioned space). -> (poles, lines).

    The fewest-pole chain per network is :func:`_search_links` (v4's BFS); a network joined becomes
    a source for the next. Keys continue ``<owner>/p<k>`` after the owner's existing poles."""
    ents = sorted(ents, key=lambda e: natural_key(e.key))
    nets = networks(ents, cat)
    if not any(n["sources"] for n in nets):
        return [], ["no wire network reaches a grid source: nothing to bridge to"]
    by_key = {e.key: e for e in ents}
    taken = {}
    for e in ents:
        for t in fkp.tiles_of(cat, e):
            taken[t] = e.key
    ground = _Ground(taken, set(keep_out), None, cat.size(pole))
    supply, reach = cat.pole_supply(pole), cat.pole_reach(pole)

    def nodes(keys):
        return [_Node(float(by_key[k].x), float(by_key[k].y), cat.pole_supply(by_key[k].name),
                      cat.pole_reach(by_key[k].name), k) for k in keys]

    used = [int(m.group(1)) for k in by_key
            if (m := re.fullmatch(rf".*\b{re.escape(owner)}/p(\d+)", k))]
    k = max(used) + 1 if used else 0
    sources = [n for net in nets if net["sources"] for n in nodes(net["poles"])]
    poles, lines = [], []
    for net in nets:
        if net["sources"]:
            continue
        targets = nodes(net["poles"])
        path = _search_links(ground, sources, targets, supply, reach)
        where = f"the network of {net['poles'][0]} ({len(net['poles'])} poles)"
        if path is None:
            lines.append(f"cannot bridge {where}: no chain of free sanctioned tiles reaches it")
            continue
        for node in path:
            ground.place(node.spot)
            poles.append(fkp.Entity(f"{owner}/p{k}", pole, _clean(node.x), _clean(node.y)))
            node.key = f"{owner}/p{k}"
            k += 1
        sources += path + targets
        lines.append(f"bridged {where} with {len(path)} pole{'s' if len(path) != 1 else ''}")
    return poles, lines


def _clean(v):
    return int(v) if float(v).is_integer() else v


def _search_links(ground, sources, targets, supply, reach):
    """Fewest new spots joining some node of ``sources`` to some node of ``targets``.

    BFS over free spots: a step links two pole centres within wire reach. ``[]`` when a source
    already wires to a target; ``None`` when no chain exists inside the search box.
    """
    for a in sources:
        for b in targets:
            if _wired(a, b):
                return []
    every = list(sources) + list(targets)
    bx0 = min(p.x for p in every) - BRIDGE_PAD
    bx1 = max(p.x for p in every) + BRIDGE_PAD
    by0 = min(p.y for p in every) - BRIDGE_PAD
    by1 = max(p.y for p in every) + BRIDGE_PAD
    # 2.1.35: across a long gap, best first (A*): the hops so far plus the hops the straight gap
    # to the nearest target still needs at least (a plain BFS walked every spot around the
    # megabase grid's 20-pole gaps). A short gap keeps the BFS order (h = 0) and its chains
    import heapq
    tpts = [(b.x, b.y) for b in targets]
    gap = math.sqrt(min((a.x - tx) ** 2 + (a.y - ty) ** 2 for a in sources for tx, ty in tpts))
    far = gap > 4 * reach

    def h(x, y):
        if not far:
            return 0.0
        d = math.sqrt(min((x - tx) ** 2 + (y - ty) ** 2 for tx, ty in tpts))
        return max(0.0, d / reach - 1.0)

    nodes, parent, queue, seen, hops = [], [], [], set(), []
    for a in sources:
        nodes.append(a)
        parent.append(None)
        hops.append(0)
        heapq.heappush(queue, (h(a.x, a.y), h(a.x, a.y), len(nodes) - 1))
    offsets = {}
    while queue:
        _f, _h, index = heapq.heappop(queue)
        node = nodes[index]
        if parent[index] is not None and any(_wired(node, b) for b in targets):
            path, k = [], index
            while parent[k] is not None:
                path.append(nodes[k])
                k = parent[k]
            return path[::-1]
        if not (bx0 <= node.x <= bx1 and by0 <= node.y <= by1):
            continue
        step = min(node.reach, reach)
        # Spots whose centre lies within ``step`` of the node, as offsets from a base spot; the
        # list depends only on the step and the node centre's fraction, so it is cached.
        ox, oy = node.x - ground.pw / 2, node.y - ground.ph / 2
        bi, bj = math.floor(ox), math.floor(oy)
        fx, fy = ox - bi, oy - bj
        offs = offsets.get((step, fx, fy))
        if offs is None:
            d = int(math.ceil(step)) + 1
            lim2 = step * step + _EPS
            offs = [(di, dj) for dj in range(-d, d + 1) for di in range(-d, d + 1)
                    if (di - fx) ** 2 + (dj - fy) ** 2 <= lim2]
            offsets[(step, fx, fy)] = offs
        for di, dj in offs:
            spot = (bi + di, bj + dj)
            if spot in seen or not ground.free(spot):
                continue
            seen.add(spot)
            cx, cy = ground.centre(spot)
            child = _Node(cx, cy, supply, reach, spot=spot, bridge=True)
            nodes.append(child)
            parent.append(index)
            hops.append(hops[index] + 1)
            # a child that wires to a target needs no more hops: it comes off the queue first
            # among its equals, the fewest spots still (the goal is tested as a node is taken)
            # ties go to the child nearer the target (none on a short gap: h is 0 there)
            hc = 0.0 if any(_wired(child, b) for b in targets) else max(1.0, h(cx, cy))
            heapq.heappush(queue, (hops[-1] + hc, hc, len(nodes) - 1))
    return None


# -- the step -------------------------------------------------------------------------------------
_OWN_POLE = re.compile(r"p\d+")


def _is_own_pole(e, owner, cat):
    """A pole a previous run of this cover placed: ``<owner>/p<k>``."""
    return (e.owner == owner and _OWN_POLE.fullmatch(e.key[len(owner) + 1:]) is not None
            and _kind(cat, e) == "electric-pole")


def _kind(cat, e):
    try:
        return cat.kind(e.name)
    except KeyError:
        return None


def _needs_power(cat, e):
    try:
        return cat.is_electric(e.name) or cat.kind(e.name) in GRID_SOURCE_TYPES
    except KeyError:
        return False


def _check_args(lay, cat, pole, owner):
    if not owner or owner.startswith("~") or " " in owner:
        raise ValueError(f"cover owner {owner!r}: a cover name is one word, not a net")
    if owner in lay.insts or any(p.startswith(owner + "/") for p in lay.insts):
        raise ValueError(f"cover owner {owner!r} is an inst path of {lay.name}")
    rec = lay.covers.get(owner)
    if rec is not None and rec.kind != "power":
        raise ValueError(f"cover {owner} is a {rec.kind} cover, not power")
    if not cat.has_entity(pole) or cat.kind(pole) != "electric-pole":
        raise ValueError(f"{pole} is not an electric pole in the catalog")


def cover_power(lay, cat, pole="medium-electric-pole", owner="power", keep_out=(), pinned=(),
                only=None, deny=(), attrs=None, frame=None):
    """``(poles, report)``: the poles to add to ``lay`` and a :class:`CoverReport`.

    ``frame`` (2.1.35): the box (x0, y0, x1, y1) new poles stand in, in place of the layout's
    frame (a sealed unit's, :func:`cover_power_units`).

    ``keep_out`` is an iterable of ``(x, y)`` tiles no pole may use (a route's tiles, a reserved
    lane). ``pinned``: the committed poles to keep where they still fit (they come back among
    ``poles``, under their own keys). A pinned pole of another kind than ``pole`` is not kept: it
    is laid again in the new kind (3.1, Rory 11-08: nothing the diff found built reaches the
    cover; 2.0.1 had kept a built pole of the old kind from ``out/standing.json``). ``lay`` is
    not changed.

    2.1 (power domains, ``fkcore.domains``): ``only``, the keys of the entities and poles of one
    domain (None: all of them); every other entity's tiles stay taken, and its poles are no wire
    nodes. ``deny``: the tile rectangles of other domains' machines, which no new pole's supply
    area may reach (an entity that only such spots could power is a ``domain-overlap`` line).
    ``attrs``: the new poles' attrs (``domain=NAME``).
    """
    _check_args(lay, cat, pole, owner)
    s, wire = cat.pole_supply(pole), cat.pole_reach(pole)
    rows = sorted(lay.entities.values(), key=lambda e: natural_key(e.key))
    rows = [e for e in rows if not _is_own_pole(e, owner, cat)]

    # Tiles taken, existing poles as wire nodes, and the entities that need power.
    taken, existing, electric = {}, [], []
    for e in rows:
        try:
            tiles = fkp.tiles_of(cat, e)
        except (KeyError, ValueError):
            continue                                 # unknown or misplaced: invariants say so
        for t in sorted(tiles):
            taken.setdefault(t, e.key)
        kind = _kind(cat, e)
        if only is not None and e.key not in only:
            continue                                 # another domain's: its tiles stay taken
        if kind == "electric-pole" and e.attrs.get("dnp"):
            # 2.0.63: a DNP pole is never built, so it powers nothing; its tiles stay taken. What
            # a DNP piece would power still gets its poles, ready for when it is built
            continue
        if kind == "electric-pole":
            existing.append(_Node(float(e.x), float(e.y), cat.pole_supply(e.name),
                                  cat.pole_reach(e.name), key=e.key))
        elif _needs_power(cat, e):
            electric.append((e.key, _rect(tiles)))
    ground = _Ground(taken, {(math.floor(x), math.floor(y)) for x, y in keep_out}, lay.size,
                     cat.size(pole), deny, s)
    if frame is not None:
        ground.bounds = tuple(frame)

    # The committed poles of this kind that still fit: kept as they stand (wire nodes, prunable
    # last).
    kept, names = [], {}
    for e in sorted(pinned, key=lambda e: natural_key(e.key)):
        try:
            tiles = fkp.tiles_of(cat, e)
        except (KeyError, ValueError):
            continue
        spot = (min(x for x, _y in tiles), min(y for _x, y in tiles))
        if e.name != pole:
            continue
        if not ground.free(spot):
            continue
        ground.place(spot)
        kept.append(_Node(float(e.x), float(e.y), cat.pole_supply(e.name), cat.pole_reach(e.name),
                          key=e.key, spot=spot))
        names[e.key] = e.name
    keep_keys = {n.key for n in kept}
    fixed = len(existing)                        # the layout's own wire nodes, never pruned
    existing = existing + kept

    def powered(rect, nodes):
        return any(_overlap(rect, n.x, n.y, n.supply) for n in nodes)

    # 1. Cover: greedy over the free spots that power entities the existing poles do not.
    pending = [i for i, (_k, rect) in enumerate(electric) if not powered(rect, existing)]
    candidates = {}
    for i in pending:
        for spot in ground.spots_powering(electric[i][1], s):
            if ground.free(spot):
                candidates.setdefault(spot, set()).add(i)
    ref = None
    if existing:
        ref = (sum(n.x for n in existing) / len(existing),
               sum(n.y for n in existing) / len(existing))
    chosen, left = [], set(pending)
    while left:
        best, best_key = None, None
        for spot, covers in candidates.items():
            count = len(covers & left)
            if count == 0 or not ground.free(spot):
                continue
            dist = 0.0
            if ref is not None:
                cx, cy = ground.centre(spot)
                dist = (cx - ref[0]) ** 2 + (cy - ref[1]) ** 2
            key = (count, -dist, -spot[1], -spot[0])
            if best_key is None or key > best_key:
                best, best_key = spot, key
        if best is None:
            break
        ground.place(best)
        cx, cy = ground.centre(best)
        chosen.append(_Node(cx, cy, s, wire, spot=best))
        left -= candidates[best]

    # 2. Bridge: join every component to the main one while some chain of free spots can.
    nodes = existing + chosen
    groups = _groups(nodes)
    while len(groups) > 1:
        main = groups[0]
        ranked = []
        for other in groups[1:]:
            d = min((nodes[i].x - nodes[j].x) ** 2 + (nodes[i].y - nodes[j].y) ** 2
                    for i in main for j in other)
            ranked.append((d, other[0], other))
        ranked.sort(key=lambda r: (r[0], r[1]))
        added = None
        for d, _first, other in ranked:
            # 2.1.35: first from the poles near the gap only (the megabase grid's band pass
            # walked out from every pole of a main grid hundreds strong, for each of 27 joins),
            # then, when that finds no chain, from them all
            src, dst = [nodes[i] for i in main], [nodes[i] for i in other]
            lim = (math.sqrt(d) + 2 * wire) ** 2
            near_src = [a for a in src
                        if min((a.x - b.x) ** 2 + (a.y - b.y) ** 2 for b in dst) <= lim]
            near_dst = [b for b in dst
                        if min((a.x - b.x) ** 2 + (a.y - b.y) ** 2 for a in near_src) <= lim]
            path = None
            if len(near_src) < len(src) or len(near_dst) < len(dst):
                path = _search_links(ground, near_src, near_dst, s, wire)
            if not path:
                path = _search_links(ground, src, dst, s, wire)
            if path:
                added = path
                break
        if added is None:
            break
        for n in added:
            ground.place(n.spot)
            nodes.append(n)
        groups = _groups(nodes)

    # 3. Prune: a new pole whose entities others power too, and whose loss splits nothing, goes.
    # New poles first (newest first), then the pinned ones, and a pinned pole only when it
    # powers nothing at all (a new one never replaces it). A bridge kept to join a pole that went
    # is looked at again.
    rects = [rect for _k, rect in electric]
    order = [n for n in reversed(nodes) if n.spot is not None and n.key not in keep_keys] + \
            [n for n in reversed(nodes) if n.key in keep_keys]
    pruned = 0
    while order:
        n = order.pop(0)
        if n.key in keep_keys and any(_overlap(r, n.x, n.y, n.supply) for r in rects):
            continue
        rest = [m for m in nodes if m is not n]
        if any(not powered(r, rest) for r in rects if _overlap(r, n.x, n.y, n.supply)):
            continue
        regrouped = _groups(rest)
        if len(regrouped) > len(groups):
            continue
        nodes, groups = rest, regrouped
        ground.lift(n.spot)
        pruned += 1
        # A neighbour kept only to join this one may be idle now: it is looked at again.
        order += [m for m in rest if m.spot is not None and _wired(m, n)
                  and all(m is not o for o in order)]

    # Keys north to south, then west to east, after the pinned ones'; the report.
    new = sorted((n for n in nodes if n.spot is not None and n.key not in keep_keys),
                 key=lambda n: (n.spot[1], n.spot[0]))
    top = max([int(k.rsplit("/p", 1)[1]) for k in keep_keys] + [-1]) + 1
    poles = [Entity(f"{owner}/p{top + k}", pole, n.x, n.y, None, dict(attrs or {}))
             for k, n in enumerate(new)]
    key_of = {id(n): e.key for n, e in zip(new, poles)}
    for n in nodes:
        if n.key is not None:
            key_of[id(n)] = n.key
    still = [n for n in nodes if n.key in keep_keys]
    poles += [Entity(n.key, names.get(n.key, pole), n.x, n.y, None, dict(attrs or {}))
              for n in still]
    uncovered, checks = [], []
    for key, rect in electric:
        if not powered(rect, nodes):
            reason = _why_not(ground, rect, s, pole, lay.size)
            uncovered.append((key, reason))
            checks.append(Check("domain-overlap" if "another power domain" in reason
                                else "NO_POWER", key, reason))
    splits = []
    for g in groups[1:]:
        keys = [key_of[id(nodes[i])] for i in g]
        splits.append(keys)
        shown = ", ".join(keys[:_SHOW]) + (f" ... {len(keys) - _SHOW} more" if len(keys) > _SHOW
                                           else "")
        checks.append(Check("power-split", f"cover {owner}",
                            f"{shown} ({len(keys)} pole{'s' if len(keys) != 1 else ''}) cannot be "
                            f"wired to the main grid: no chain of free spots at most "
                            f"{fkp.fmt(wire)} tiles apart joins them"))
    rep = CoverReport(owner, pole, [e.key for e in poles], len(electric),
                      len(electric) - len(uncovered), len(groups), uncovered, splits,
                      sum(1 for n in new if n.bridge), pruned, fixed, checks, len(still))
    return poles, rep


def cover_power_units(lay, cat, units, pole="medium-electric-pole", owner="power", keep_out=(),
                      attrs=None):
    """2.1.35 (Rory 2026-11-07: power in the hierarchy, as the routing is): ``(poles, report)``
    as :func:`cover_power`, laid unit by unit. ``units``: [(path, (x0, y0, x1, y1), twin)] of the
    sealed modules, innermost first; ``twin`` is what makes two units alike (their kind and turn),
    None for none.

    Each unit gets the poles its own electric entities need and the bridges between its
    children's poles, standing in its frame; a unit alike to one laid already (the same twin, the
    same entities and keep-out in its frame) takes that one's poles, moved. A last pass over the
    whole layout powers what no unit could and joins the units' grids (the band and row
    backbones). The megabase grid's flat cover tried every spot of 6263 machines for each of its
    600 poles: ten minutes."""
    _check_args(lay, cat, pole, owner)
    from types import SimpleNamespace
    S = 32
    own = [e for e in lay.entities.values() if not _is_own_pole(e, owner, cat)]
    tiles_of, buckets = {}, {}
    for e in own:
        try:
            tiles_of[e.key] = fkp.tiles_of(cat, e)
        except (KeyError, ValueError):
            continue
        xs = [t[0] for t in tiles_of[e.key]] or [math.floor(e.x)]
        ys = [t[1] for t in tiles_of[e.key]] or [math.floor(e.y)]
        for bx in range(min(xs) // S, max(xs) // S + 1):
            for by in range(min(ys) // S, max(ys) // S + 1):
                buckets.setdefault((bx, by), []).append(e)
    ko_b = {}
    for x, y in keep_out:
        t = (math.floor(x), math.floor(y))
        ko_b.setdefault((t[0] // S, t[1] // S), set()).add(t)
    paths = [u[0] for u in units]
    pset = set(paths)

    def unit_of(key):
        parts = key.split("/")[:-1]
        best = ""
        for i in range(1, len(parts) + 1):
            if "/".join(parts[:i]) in pset:
                best = "/".join(parts[:i])
        return best

    def in_box(e, box):
        x0, y0, x1, y1 = box
        return x0 <= e.x < x1 and y0 <= e.y < y1

    def near(box, extra=()):
        x0, y0, x1, y1 = box
        seen, out = set(), []
        for bx in range(math.floor(x0) // S, math.floor(x1 - 1) // S + 1):
            for by in range(math.floor(y0) // S, math.floor(y1 - 1) // S + 1):
                for e in buckets.get((bx, by), ()):
                    if e.key not in seen:
                        seen.add(e.key)
                        ts = tiles_of[e.key]
                        if any(x0 <= t[0] < x1 and y0 <= t[1] < y1 for t in ts) or in_box(e, box):
                            out.append(e)
        out += [p for p in extra if in_box(p, box)]
        return out

    def ko_in(box):
        x0, y0, x1, y1 = box
        out = set()
        for bx in range(math.floor(x0) // S, math.floor(x1 - 1) // S + 1):
            for by in range(math.floor(y0) // S, math.floor(y1 - 1) // S + 1):
                out |= {t for t in ko_b.get((bx, by), ()) if x0 <= t[0] < x1 and y0 <= t[1] < y1}
        return out

    mine = {}
    for e in own:
        mine.setdefault(unit_of(e.key), set()).add(e.key)
    placed, twins, laid, reused = [], {}, 0, 0
    for i, (path, box, twin) in enumerate(units):
        own_keys = mine.get(path, set())
        rows = near(box, placed)
        kids = [p for p in placed if in_box(p, box)]
        kid_keys = {p.key for p in kids}
        if not any(_needs_power(cat, e) for e in rows if e.key in own_keys) and len(kids) < 2:
            continue
        x0, y0 = box[0], box[1]
        ko = ko_in(box)
        key = None
        if twin is not None:
            key = (twin, box[2] - x0, box[3] - y0,
                   tuple(sorted((e.name, e.x - x0, e.y - y0, -1 if e.dir is None else e.dir,
                                 e.key in own_keys or e.key in kid_keys) for e in rows)),
                   tuple(sorted((t[0] - x0, t[1] - y0) for t in ko)))
        if key is not None and key in twins:
            src_x, src_y, src_poles = twins[key]
            got = [Entity(f"{owner}~{i}/p{k}", p.name, p.x - src_x + x0, p.y - src_y + y0,
                          None, dict(p.attrs)) for k, p in enumerate(src_poles)]
            reused += 1
        else:
            sub = SimpleNamespace(name=lay.name, size=None, insts={}, covers={},
                                  entities={e.key: e for e in rows})
            only = own_keys | {p.key for p in kids}
            poles, _rep = cover_power(sub, cat, pole=pole, owner=f"pw{i}", keep_out=ko,
                                      only=only, attrs=attrs, frame=box)
            got = [Entity(f"{owner}~{i}/p{k}", p.name, p.x, p.y, None, dict(p.attrs))
                   for k, p in enumerate(poles)]
            laid += 1
            if key is not None:
                twins[key] = (x0, y0, got)
        placed += got
        for p in got:
            tiles_of[p.key] = fkp.tiles_of(cat, p)
    # the last pass: the whole layout, the units' poles standing as wire nodes
    whole = SimpleNamespace(name=lay.name, size=lay.size, insts=lay.insts, covers=lay.covers,
                            entities={**{e.key: e for e in own}, **{p.key: p for p in placed}})
    last, rep = cover_power(whole, cat, pole=pole, owner=owner, keep_out=keep_out, attrs=attrs)
    allp = sorted(placed + last, key=lambda p: (p.y, p.x))
    poles = [Entity(f"{owner}/p{k}", p.name, p.x, p.y, None, dict(p.attrs))
             for k, p in enumerate(allp)]
    rep.poles = [p.key for p in poles]
    rep.existing = max(0, rep.existing - len(placed))
    rep.units = (laid, reused)
    return poles, rep


def _why_not(ground, rect, s, pole, size):
    """Why no pole powers an entity: what takes each spot that could."""
    spots = ground.spots_powering(rect, s)
    count, owners = {}, []
    for spot in spots:
        why = ground.why(spot)
        if why is None:                  # free, yet no pole: the greedy cover stopped short
            return f"a free spot at {spot[0]},{spot[1]} powers it, but no {pole} was placed"
        count[why] = count.get(why, 0) + 1
        if why == "entity":
            for t in ground.tiles(spot):
                if t in ground.taken:
                    owner = ground.taken[t].rsplit("/", 1)[0]
                    if owner not in owners:
                        owners.append(owner)
    parts = []
    if count.get("entity"):
        owners.sort(key=natural_key)
        more = f" ... {len(owners) - _SHOW} more" if len(owners) > _SHOW else ""
        parts.append(f"{count['entity']} on entities ({', '.join(owners[:_SHOW])}{more})")
    if count.get("keep_out"):
        parts.append(f"{count['keep_out']} keep-out")
    if count.get("pole"):
        parts.append(f"{count['pole']} under other poles")
    if count.get("frame"):
        parts.append(f"{count['frame']} outside the {size[0]}x{size[1]} frame")
    if count.get("domain"):
        parts.append(f"{count['domain']} would power another power domain's machines too")
    return f"all {len(spots)} spots a {pole} could power it from are taken: " + "; ".join(parts)


def apply_cover(lay, cat, pole="medium-electric-pole", owner="power", keep_out=(), pinned=(),
                only=None, deny=(), attrs=None, poles=None):
    """A new Layout: ``lay`` without ``owner``'s previous poles, with the pinned ones that still
    fit, the new ones and the record ``cover <owner> power pole=<pole>``. ``lay`` is not changed.
    ``poles``: :func:`cover_power`'s, when the caller has them already."""
    if poles is None:
        poles, _rep = cover_power(lay, cat, pole, owner, keep_out, pinned, only, deny, attrs)
    out = copy.deepcopy(lay)
    out.entities = {k: e for k, e in out.entities.items() if not _is_own_pole(e, owner, cat)}
    old = out.covers.get(owner)
    params = dict(old.params) if old is not None else {}
    params["pole"] = pole
    out.covers[owner] = fkp.Cover(owner, "power", params)
    for e in poles:
        out.add(e)
    return out
