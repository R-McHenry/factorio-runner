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
        if self.size is not None:
            w, h = self.size
            if any(not (0 <= x < w and 0 <= y < h) for x, y in self.tiles(spot)):
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
        if self.size is not None:
            w, h = self.size
            if i < 0 or j < 0 or i + pw > w or j + ph > h:
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
    nodes, parent, queue, seen = [], [], deque(), set()
    for a in sources:
        nodes.append(a)
        parent.append(None)
        queue.append(len(nodes) - 1)
    offsets = {}
    while queue:
        index = queue.popleft()
        node = nodes[index]
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
            if any(_wired(child, b) for b in targets):
                path, k = [], len(nodes) - 1
                while parent[k] is not None:
                    path.append(nodes[k])
                    k = parent[k]
                return path[::-1]
            queue.append(len(nodes) - 1)
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
                standing=None, only=None, deny=(), attrs=None):
    """``(poles, report)``: the poles to add to ``lay`` and a :class:`CoverReport`.

    ``keep_out`` is an iterable of ``(x, y)`` tiles no pole may use (a route's tiles, a reserved
    lane). ``pinned``: the committed poles to keep where they still fit (they come back among
    ``poles``, under their own keys). ``standing``: the keys of the pinned poles the last diff
    found built; a pinned pole of another kind than ``pole`` (and its size) stays only when it
    stands, so switching ``pole`` keeps the built grid and lays the new kind where the old one
    never stood (2.0.1, the first lead run: medium poles were SHORT before steel, and switching to
    small dropped every medium pole, the built ones too). ``lay`` is not changed.

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

    # The committed poles that still fit: kept as they stand (wire nodes, prunable last); one of
    # another kind only when it stands built (``standing``) and takes the same footprint.
    kept, names = [], {}
    for e in sorted(pinned, key=lambda e: natural_key(e.key)):
        try:
            tiles = fkp.tiles_of(cat, e)
        except (KeyError, ValueError):
            continue
        spot = (min(x for x, _y in tiles), min(y for _x, y in tiles))
        if e.name != pole and (standing is None or e.key not in standing
                               or cat.kind(e.name) != "electric-pole"
                               or cat.size(e.name) != cat.size(pole)):
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
        for _d, _first, other in ranked:
            path = _search_links(ground, [nodes[i] for i in main], [nodes[i] for i in other],
                                 s, wire)
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
    # New poles first (newest first), then the pinned ones, and a standing pole only when it
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
                standing=None, only=None, deny=(), attrs=None, poles=None):
    """A new Layout: ``lay`` without ``owner``'s previous poles, with the pinned ones that still
    fit, the new ones and the record ``cover <owner> power pole=<pole>``. ``lay`` is not changed.
    ``poles``: :func:`cover_power`'s, when the caller has them already."""
    if poles is None:
        poles, _rep = cover_power(lay, cat, pole, owner, keep_out, pinned, standing, only, deny,
                                  attrs)
    out = copy.deepcopy(lay)
    out.entities = {k: e for k, e in out.entities.items() if not _is_own_pole(e, owner, cat)}
    old = out.covers.get(owner)
    params = dict(old.params) if old is not None else {}
    params["pole"] = pole
    out.covers[owner] = fkp.Cover(owner, "power", params)
    for e in poles:
        out.add(e)
    return out
