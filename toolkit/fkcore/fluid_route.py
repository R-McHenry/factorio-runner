"""fkcore.fluid_route: the router chunk's fluid plug-in: pipes and pipe-to-ground for fluid nets.

CORE-V1.md §13: a pipe is a net, a fluid one. ``fkcore.route.route_layout`` calls
``route(flat, lay, cat, nets, blocked, box, limit) -> (added, how, failed)`` after the belt nets,
with the belts already in ``lay``:

- ``added`` {net: [Entity]}: pipes keyed ``~<net>/p<k>``, pipe-to-ground ``~<net>/g<k>``, each
  ``net=<net>``. A pipe has no direction; a pipe-to-ground's is its open (above-ground) side, the
  game's own direction: the codec writes it as the blueprint's ``direction`` (0 N, 4 E, 8 S,
  12 W), and the underground side faces the other way. The two ends of a pair face away from
  each other.
- ``how`` {net: "1 -> 2, 14 pipes, 2 pipe-to-ground pairs"}, ``failed`` {net: reason}.

``reserved`` and ``why_not`` serve the router's jobs: the tiles a fluid net's connections need
(the belt router keeps off them) and why a net can't be tried (a machine not placed, no box for
its fluid).

An endpoint is a (machine, fluid) pair of the flat netlist (fkcore.elaborate), on its placed
entity: the machine's live fluid box that holds the fluid (``Catalog.fluid_boxes_at``, the game's
own geometry, read from it), reached when a pipe stands on one of the box's connection targets.
Boxes that already join directly (a boiler straight onto a steam engine, two boilers side by side)
are one group. The rules, from the game:

- a pipe joins every neighbour that offers it a connection, so no carrier of a fluid net stands
  next to another fluid net's pipe, pipe-to-ground or connection tile, nor on a machine
  connection that is not its own net's (a box no net uses included);
- a pipe-to-ground pair sits on one axis, at most the prototype's max_distance (10) end to end,
  open sides facing away from each other; the game pairs the first pipe-to-ground on that axis,
  so no other same-axis end stands between a pair's ends and no end stands inside another pair's
  span. Another net's pipe between the ends is fine: only the undergrounds cross over;
- a fluid net is a graph: it branches freely, no splitters. An offshore pump's water tiles carry
  nothing.

The search is v4's fkc/pipes.py (C:\\fk-lead\\repo, d6018d3), copied and re-pointed at the V1
layout: a net is a tree grown from a driver's group, one group at a time, nearest first; each
join is an A* from the group's free connection tiles to the tree over (tile, heading,
just-exited) states, with pipe steps and pipe-to-ground jumps (a straight run of 5 or more tiles
is cheaper as a pair). Each routed net is judged by ``fkcore.connect`` (connected, no SPILL)
before it is kept; other group orders are tried when it fails, then other net orders. Pure,
stdlib only, deterministic.
"""
from __future__ import annotations

import heapq
import math
from collections import deque

from . import connect as connectmod
from . import fkp as fkpmod
from .catalog import PipeConn

__all__ = ["route", "reserved", "why_not", "PIPE", "PTG"]

PIPE, PTG = "pipe", "pipe-to-ground"
_CARRIERS = ("pipe", "pipe-to-ground", "pump")
_STEP = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
_DIR_OF = {v: k for k, v in _STEP.items()}
_DIRS = (0, 4, 8, 12)
_INF = float("inf")
_CONTESTED = "\0contested"          # a connection tile two nets (or a net and nobody) need
PIPE_COST, TURN_COST = 1.0, 0.25
JUMP_COST, JUMP_PER_TILE = 4.0, 0.1   # a pair costs less than pipes from 5 tiles end to end
_PER_TILE = 0.5                       # the cheapest cost of a tile (a 10-tile jump): A* stays exact


class _Skip(Exception):
    pass


def _axis(d):
    """0 for a north-south line, 1 for east-west."""
    return 0 if int(d) % 8 == 0 else 1


def _between(a, b):
    """Tiles strictly between two tiles on one row or column."""
    if a[0] == b[0]:
        lo, hi = sorted((a[1], b[1]))
        return [(a[0], y) for y in range(lo + 1, hi)]
    lo, hi = sorted((a[0], b[0]))
    return [(x, a[1]) for x in range(lo + 1, hi)]


def _nbrs(t):
    return [(t[0] + dx, t[1] + dy) for dx, dy in _STEP.values()]


# -- endpoints ------------------------------------------------------------------------------------
def _boxes(cat, e):
    return cat.fluid_boxes_at(e.name, e.x, e.y, e.dir, e.attrs.get("recipe"),
                              resource=e.attrs.get("resource"))


def port_edges(flat, lay, cat):
    """{port key: expand.PortEdge} of the fluid ports of ``lay`` (2.1), once per layout."""
    if not any(cat.is_fluid(i) for p in getattr(flat, "ports", {}).values() for i in p.items):
        return {}
    memo = getattr(flat, "_fluid_edges", None)
    if memo is not None and memo[0] is lay and memo[1] == len(lay.insts):
        return memo[2]
    from .expand import port_edges as _port_edges
    try:
        edges = {k: pe for k, pe in _port_edges(flat, lay, cat).items() if pe.fluid}
    except Exception:                      # the router reports a port it can't place
        edges = {}
    flat._fluid_edges = (lay, len(lay.insts), edges)
    return edges


def _port_end(flat, pe, key, item, side):
    """A fluid port's end of one net (2.1): a box with one connection across the frame's edge,
    from the far side's tile onto this side's, where this net's pipe stands (the tile just
    inside the edge for the module's own net, just outside for the parent's)."""
    port = flat.ports[key]
    which = connectmod.PORT_INSIDE if (side == "out") == (port.dir == "in") \
        else connectmod.PORT_OUTSIDE
    here, there = (pe.inside, pe.outside) if which == connectmod.PORT_INSIDE \
        else (pe.outside, pe.inside)
    d = _DIR_OF[(here[0] - there[0], here[1] - there[1])]
    b = connectmod.port_box(pe, which)
    conn = PipeConn(tuple(there), d, tuple(here), b.production, "normal")
    return b._replace(conns=(conn,))


def _abut(flat, lay, cat, net):
    """A net between two fluid ports that meet on one edge (2.1: a Stack's cells, one's out port
    on the next one's in port): connected as they stand, nothing to lay."""
    fn = flat.nets[net]
    keys = [k for k, _i in list(fn.drivers) + list(fn.consumers)]
    edges = port_edges(flat, lay, cat)
    if len(keys) < 2 or not all(k in edges for k in keys):
        return False
    return len({(tuple(edges[k].src), tuple(edges[k].dst)) for k in keys}) == 1


def _ends(flat, lay, cat, net):
    """[(key, item, side, FluidBox)] of a fluid net's endpoints in ``lay``; raises _Skip. A fluid
    port's end (2.1) is the box ``_port_end`` gives it."""
    fn = flat.nets[net]
    out = []
    edges = port_edges(flat, lay, cat)
    for side, rows in (("out", fn.drivers), ("in", fn.consumers)):
        for key, item in rows:
            if key in getattr(flat, "ports", {}):
                pe = edges.get(key)
                if pe is None:
                    raise _Skip(f"{key} is not placed")
                out.append((key, item, side, _port_end(flat, pe, key, item, side)))
                continue
            e = lay.entities.get(key)
            if e is None:
                raise _Skip(f"{key} is not placed")
            b = connectmod.endpoint_box(_boxes(cat, e), item, side)
            if b is None:
                raise _Skip(f"{key} ({e.name}) has no fluid box for {item}")
            # 3.0: a box that takes in at one end and pushes out at the other (a pump) meets the
            # net that feeds it at its input and the net it drives at its output
            want = ("output", "input-output") if side == "out" else ("input", "input-output")
            if {c.flow for c in b.conns} >= {"input", "output"}:
                b = b._replace(conns=tuple(c for c in b.conns if c.flow in want))
            out.append((key, item, side, b))
    if not any(s == "out" for _k, _i, s, _b in out):
        raise _Skip("no driver is placed")
    if not any(s == "in" for _k, _i, s, _b in out):
        raise _Skip("no consumer is placed")
    return out


def why_not(flat, lay, cat, net):
    """Why fluid net ``net`` can't be routed on ``lay`` (None: it can be tried)."""
    try:
        _ends(flat, lay, cat, net)
    except _Skip as exc:
        return str(exc)
    return None


def reserved(flat, lay, cat, net):
    """The tiles fluid net ``net`` needs kept free: its machines' connection tiles (a pipe of the
    net stands there) and an offshore pump's water."""
    try:
        ends = _ends(flat, lay, cat, net)
    except _Skip:
        return []
    if _abut(flat, lay, cat, net):
        return []
    out = set()
    for key, _item, _side, b in ends:
        out |= {c.target for c in b.conns if c.type == "normal"}
        e = lay.entities.get(key)
        if e is not None:
            out |= cat.water_tiles(e.name, e.x, e.y, e.dir)
    return sorted(out)


# -- the pipe world -------------------------------------------------------------------------------
class _World:
    """What a fluid carrier may use: blocked tiles, tiles each net's carriers own, the machine
    connection tiles each net needs, and every pipe-to-ground end and pair span per axis."""

    def __init__(self, box, occ):
        self.box = box
        self.occ = set(occ)
        self.owner = {}                      # tile -> the net of the fluid carrier on it
        self.reserve = {}                    # connection tile -> net, None (nobody), _CONTESTED
        self.ends = {0: set(), 1: set()}     # axis -> tiles of pipe-to-ground ends
        self.spans = {0: set(), 1: set()}    # axis -> tiles strictly inside a pair
        self.cross = {}                      # (tile, tile) -> the nets of a fluid port's edge

    def inside(self, t):
        x0, y0, x1, y1 = self.box
        return x0 <= t[0] < x1 and y0 <= t[1] < y1

    def ok(self, t, net):
        """A carrier of ``net`` may stand on ``t``."""
        if not self.inside(t) or t in self.occ or t in self.owner:
            return False
        if self.reserve.get(t, net) != net:
            return False
        for n in _nbrs(t):
            # 2.1: across a fluid port's edge, the nets the port joins meet on purpose
            mine = self.cross.get((t, n), ())
            mine = mine if net in mine else (net,)
            if self.owner.get(n, net) not in mine:
                return False
            r = self.reserve.get(n)
            if r is not None and r != _CONTESTED and r not in mine:
                return False
        return True

    def jump_ok(self, a, b, d):
        """A pair from ``a`` to ``b`` leaves every other pair's pairing alone."""
        ax = _axis(d)
        if a in self.spans[ax] or b in self.spans[ax]:
            return False
        return not any(t in self.ends[ax] for t in _between(a, b))

    def take(self, path, net):
        for tile, kind, d in path:
            self.owner[tile] = net
            if kind != PIPE:
                self.ends[_axis(d)].add(tile)
        for (a, ka, _da), (b, kb, db) in zip(path, path[1:]):
            if ka == "ug_in" and kb == "ug_out":
                self.spans[_axis(db)] |= set(_between(a, b))

    def snapshot(self):
        return (dict(self.owner), {k: set(v) for k, v in self.ends.items()},
                {k: set(v) for k, v in self.spans.items()})

    def restore(self, snap):
        self.owner, self.ends, self.spans = snap[0], snap[1], snap[2]


def _world(flat, lay, cat, nets_all, blocked, box, keep):
    """The world before any fluid net is routed: every entity, frame and kept tile is blocked;
    the layout's fluid carriers own their tiles; every machine connection tile is reserved for
    the net whose endpoint its box is (None for a box no net uses)."""
    occ = set(blocked) | set(keep)
    for e in lay.entities.values():
        occ |= fkpmod.tiles_of(cat, e)
    w = _World(box, occ)
    owners, ways = {}, {}            # ways: (key, box, side) -> net, a two-way box's ends (a pump)
    edges = port_edges(flat, lay, cat)
    # a machine on the tile just inside a port's edge whose connection points across it (3.0: the
    # pump of an InlinePump) joins the port directly: the tile outside stays the outer net's
    across = {(tuple(pe.inside), tuple(pe.outside)) for pe in edges.values()}
    ports = getattr(flat, "ports", {})
    for net in nets_all:
        abut = _abut(flat, lay, cat, net)
        for side, rows in (("out", flat.nets[net].drivers), ("in", flat.nets[net].consumers)):
            for key, item in rows:
                if key in ports:
                    # 2.1: a fluid port's end of this net: its tile is this net's, and the two
                    # tiles of the edge may hold the two nets' pipes side by side
                    pe = edges.get(key)
                    if pe is None:
                        continue
                    for pair in ((tuple(pe.inside), tuple(pe.outside)),
                                 (tuple(pe.outside), tuple(pe.inside))):
                        w.cross[pair] = tuple(sorted(set(w.cross.get(pair, ())) | {net}))
                    if abut:
                        continue
                    b = _port_end(flat, pe, key, item, side)
                    owners.setdefault((key, b.box), net)
                    t = b.conns[0].target
                    if t in w.reserve and w.reserve[t] != net:
                        w.reserve[t] = _CONTESTED
                    else:
                        w.reserve[t] = net
                    continue
                e = lay.entities.get(key)
                b = connectmod.endpoint_box(_boxes(cat, e), item, side) if e is not None else None
                if b is not None:
                    owners.setdefault((key, b.box), net)
                    ways[(key, b.box, side)] = net
    for key in sorted(lay.entities, key=fkpmod.natural_key):
        e = lay.entities[key]
        if not cat.has_fluid_boxes(e.name):
            continue
        tiles = fkpmod.tiles_of(cat, e)
        # a carrier the netlist names as a machine (3.0, a Pump) is an end of its nets, not a pipe
        if cat.kind(e.name) in _CARRIERS and key not in flat.machines:
            for t in tiles:
                w.owner[t] = e.net
            if e.name == PTG or cat.kind(e.name) == "pipe-to-ground":
                w.ends[_axis(e.dir or 0)] |= tiles
            continue
        w.occ |= cat.water_tiles(e.name, e.x, e.y, e.dir)
        for b in _boxes(cat, e):
            for c in b.conns:
                if c.type != "normal":
                    continue
                if (tuple(c.tile), tuple(c.target)) in across:
                    continue    # it meets a port across the frame's edge: the port's tile (3.0)
                side = {"input": "in", "output": "out"}.get(c.flow)
                o = ways.get((key, b.box, side), owners.get((key, b.box)))
                if c.target in w.reserve and w.reserve[c.target] != o:
                    w.reserve[c.target] = _CONTESTED
                else:
                    w.reserve[c.target] = o
    g = connectmod.graph(list(lay.entities.values()), cat, edges or None)
    for a, b in sorted(g.fluid_pairs.items()):
        if a < b:
            ea, eb = lay.entities[a], lay.entities[b]
            ta = (math.floor(float(ea.x)), math.floor(float(ea.y)))
            tb = (math.floor(float(eb.x)), math.floor(float(eb.y)))
            w.spans[_axis(ea.dir or 0)] |= set(_between(ta, tb))
    return w, owners, g


# -- the search -----------------------------------------------------------------------------------
class _L1:
    """Manhattan distance to the nearest goal for every tile of ``box`` (0 off it), as
    ``.get(tile, default)``. 2.1.29: a BFS of the goals' own bounding box, not of the whole box (a
    BFS of the 20261030-2352 world's 1636x1329 frame for every join: minutes a fluid net); a tile
    off the goals' box is its distance to that box plus the distance at the nearest tile of it,
    which is exact for Manhattan distance, coordinate by coordinate."""

    def __init__(self, box, goals):
        self.box = box
        self.dist = {t: 0 for t in goals}
        if not goals:
            self.near = None
            return
        x0, y0, x1, y1 = box
        gx0 = max(x0 - 1, min(t[0] for t in goals))
        gy0 = max(y0 - 1, min(t[1] for t in goals))
        gx1 = min(x1, max(t[0] for t in goals))
        gy1 = min(y1, max(t[1] for t in goals))
        self.near = (gx0, gy0, gx1, gy1)
        dist = self.dist
        todo = deque(sorted(goals))
        while todo:
            t = todo.popleft()
            for n in _nbrs(t):
                if n not in dist and gx0 <= n[0] <= gx1 and gy0 <= n[1] <= gy1:
                    dist[n] = dist[t] + 1
                    todo.append(n)

    def get(self, t, default=0):
        got = self.dist.get(t)
        if got is not None:
            return got
        x0, y0, x1, y1 = self.box
        if self.near is None or not (x0 - 1 <= t[0] <= x1 and y0 - 1 <= t[1] <= y1):
            return default
        gx0, gy0, gx1, gy1 = self.near
        cx = gx0 if t[0] < gx0 else gx1 if t[0] > gx1 else t[0]
        cy = gy0 if t[1] < gy0 else gy1 if t[1] > gy1 else t[1]
        got = self.dist.get((cx, cy))
        if got is None:
            return default        # a goal off the box: the old map left such tiles out too
        return abs(t[0] - cx) + abs(t[1] - cy) + got


def _l1_map(box, goals):
    return _L1(box, goals)


def _path(parent, st):
    chain = [st]
    while chain[-1] in parent:
        chain.append(parent[chain[-1]][0])
    chain.reverse()
    out = [[chain[0][0], PIPE, chain[0][1]]]
    for cur in chain[1:]:
        if parent[cur][1] == "jump":
            out[-1][1], out[-1][2] = "ug_in", cur[1]
            out.append([cur[0], "ug_out", cur[1]])
        else:
            out.append([cur[0], PIPE, cur[1]])
    return [tuple(x) for x in out]


def _search(w, net, starts, pipes, ports, reach, limit, bans, hmap=None):
    """A* from ``starts`` [(tile, heading)] (a group's free connection tiles, heading away from
    the machine) to the tree: a step onto one of its ``pipes`` or onto one of its free ``ports``.
    -> [(tile, kind, dir)] from the start (the tree pipe stepped onto is not in it); kind pipe,
    ug_in or ug_out, dir the step or jump that reached the tile. None: no route."""
    goals = set(pipes) | set(ports)
    if hmap is None:
        hmap = _l1_map(w.box, goals)
    first = {t for t, _h in starts}
    heap, best, parent = [], {}, {}
    for t, h in sorted(starts):
        if t in ports:
            return [(t, PIPE, h)]                 # one pipe joins both groups
        if w.ok(t, net):
            best[(t, h, False)] = 0.0
            heapq.heappush(heap, (_PER_TILE * hmap.get(t, 0), 0.0, (t, h, False)))
    seen = 0
    while heap:
        _f, g, st = heapq.heappop(heap)
        if g > best.get(st, _INF):
            continue
        seen += 1
        if seen > limit:
            return None
        t, hd, ex = st
        for d in _DIRS:
            if (ex and d != hd) or d == (hd + 8) % 16:
                continue                          # an exit goes straight on; no doubling back
            dx, dy = _STEP[d]
            n = (t[0] + dx, t[1] + dy)
            if (n, d) in bans:
                continue
            if n in pipes:
                return _path(parent, st)
            if n in ports and w.ok(n, net):
                return _path(parent, st) + [(n, PIPE, d)]
            if w.ok(n, net):
                ns = (n, d, False)
                ng = g + PIPE_COST + (TURN_COST if d != hd else 0.0)
                if ng < best.get(ns, _INF):
                    best[ns], parent[ns] = ng, (st, "step")
                    heapq.heappush(heap, (ng + _PER_TILE * hmap.get(n, 0), ng, ns))
            if ex or d != hd or t in first or t in w.reserve:
                continue                          # a jump starts straight on, off a machine tile
            for k in range(2, reach + 1):
                n = (t[0] + dx * k, t[1] + dy * k)
                if not w.inside(n):
                    break
                if n in goals or n in w.reserve or (n, d) in bans or not w.ok(n, net):
                    continue
                if not w.jump_ok(t, n, d):
                    continue
                ns = (n, d, True)
                ng = g + JUMP_COST + JUMP_PER_TILE * k
                if ng < best.get(ns, _INF):
                    best[ns], parent[ns] = ng, (st, "jump")
                    heapq.heappush(heap, (ng + _PER_TILE * hmap.get(n, 0), ng, ns))
    return None


def _path_problem(path):
    """(tile, dir) to ban when ``path`` breaks a rule on its own: a tile used twice (a
    heading-aware search can pass a tile and come back), or one of its pairs stealing another's."""
    seen = set()
    for tile, _kind, d in path:
        if tile in seen:
            return (tile, d)
        seen.add(tile)
    pairs = [(a, b, db) for (a, ka, _da), (b, kb, db) in zip(path, path[1:])
             if ka == "ug_in" and kb == "ug_out"]
    ends = [(tile, d) for tile, kind, d in path if kind != PIPE]
    for a, b, d in pairs:
        span = set(_between(a, b))
        for tile, td in ends:
            if _axis(td) == _axis(d) and tile in span:
                return (tile, td)
    return None


def _join(w, net, starts, pipes, ports, reach, limit):
    bans = set()
    # 2.1.29: the distance map once per join, not once per try (a BFS of the whole box: the
    # 20261030-2352 world's frame is 1636x1329)
    hmap = _l1_map(w.box, set(pipes) | set(ports))
    for _attempt in range(16):
        path = _search(w, net, starts, pipes, ports, reach, limit, bans, hmap)
        if path is None:
            return None
        bad = _path_problem(path)
        if bad is None:
            return path
        bans.add(bad)
    return None


# -- one net --------------------------------------------------------------------------------------
def _groups(net, ends, g, w):
    """The net's endpoint boxes in groups joined directly, each with its free connection tiles:
    [{"keys", "boxes", "driver", "ports": [(tile, heading)]}], plus the notes on direct joins to
    boxes of other nets."""
    nids = {(key, b.box): (key, side, b) for key, _item, side, b in ends}
    parent = {n: n for n in nids}

    def find(n):
        while parent[n] != n:
            parent[n] = parent[parent[n]]
            n = parent[n]
        return n

    notes, direct = [], 0
    for a, b, _via in g.joins:
        if a in nids and b in nids:
            if find(a) != find(b):
                direct += 1
            parent[find(a)] = find(b)
        elif (a in nids) != (b in nids) and not g.nodes[(b if a in nids else a)[0]].carrier:
            mine, other = (a, b) if a in nids else (b, a)
            notes.append(f"{mine[0]} joins {other[0]} directly (a SPILL by placement)")
    out = {}
    for n in sorted(nids, key=lambda x: (fkpmod.natural_key(x[0]), x[1])):
        key, side, b = nids[n]
        grp = out.setdefault(find(n), {"keys": [], "boxes": [], "driver": False, "ports": []})
        if key not in grp["keys"]:
            grp["keys"].append(key)
        grp["boxes"].append(n)
        grp["driver"] |= side == "out"
        for c in b.conns:
            if c.type == "normal" and c.target not in w.occ and w.reserve.get(c.target) == net:
                if (c.target, c.dir) not in grp["ports"]:
                    grp["ports"].append((c.target, c.dir))
    return list(out.values()), notes, direct


def _dist(ports, tiles):
    if not ports or not tiles:
        return _INF
    return min(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, _h in ports for b in tiles)


def _grow(w, net, groups, root, mode, reach, limit):
    """Join every group to the tree grown from ``root``. -> ([(tile, kind, dir)], None) or
    (None, reason)."""
    placed = []
    pipes, ports = set(), {t for t, _h in groups[root]["ports"]}
    joined = {root}
    while len(joined) < len(groups):
        left = [i for i in range(len(groups)) if i not in joined]
        tree = pipes | ports
        if mode == "keys":
            i = left[0]
        else:
            sign = -1 if mode == "far" else 1
            i = min(left, key=lambda j: (sign * _dist(groups[j]["ports"], tree), j))
        grp = groups[i]
        if not grp["ports"]:
            return None, f"{', '.join(grp['keys'])}: every connection tile of its box is taken"
        path = _join(w, net, grp["ports"], pipes, ports, reach, limit)
        if path is None:
            return None, (f"no pipe route joins {', '.join(grp['keys'])} to "
                          f"{', '.join(k for j in sorted(joined) for k in groups[j]['keys'])}")
        w.take(path, net)
        placed += path
        pipes |= {t for t, kind, _d in path if kind == PIPE}
        joined.add(i)
        ports |= {t for t, _h in grp["ports"]}
        ports -= pipes
        for j in range(len(groups)):            # a pipe on another group's tile joins it too
            if j not in joined and any(t in pipes for t, _h in groups[j]["ports"]):
                joined.add(j)
                ports |= {t for t, _h in groups[j]["ports"]} - pipes
    return placed, None


def _entities(net, placed):
    out, count = [], {"p": 0, "g": 0}
    for tile, kind, d in placed:
        x, y = tile[0] + 0.5, tile[1] + 0.5
        if kind == PIPE:
            out.append(fkpmod.Entity(f"~{net}/p{count['p']}", PIPE, x, y, None, {"net": net}))
            count["p"] += 1
        else:
            facing = (d + 8) % 16 if kind == "ug_in" else d       # the open side
            out.append(fkpmod.Entity(f"~{net}/g{count['g']}", PTG, x, y, facing, {"net": net}))
            count["g"] += 1
    return out


def _judge(cat, base, extra, flat, net, new, edges=None):
    """``fkcore.connect`` on the layout with the new carriers: connected and no SPILL of them.
    ``edges``: the fluid ports' edges (2.1), so a port is the net's end."""
    fn = flat.nets[net]
    rep = connectmod.connect(list(base) + list(extra) + list(new), cat,
                             {net: (list(fn.drivers), list(fn.consumers))}, edges or None)
    mine = {e.key for e in new}
    leaks = [lk for lk in rep.leaks if lk.src in mine or lk.dst in mine]
    r = rep.nets[net]
    if r.connected and not leaks:
        return None
    if leaks:
        return leaks[0].text
    return f"{r.reached} of {r.pairs} driver->consumer pairs connected"


def _route_net(w, owners, g, flat, cat, net, ends, base, extra, reach, limit, edges=None):
    """-> (entities, how) or (None, reason)."""
    groups, notes, direct = _groups(net, ends, g, w)
    ports = getattr(flat, "ports", {})
    if len(groups) == 1 and ends and all(k in ports for k, _i, _s, _b in ends):
        why = _judge(cat, base, extra, flat, net, [], edges)
        if why is None:
            n_d = len({k for k, _i, s, _b in ends if s == "out"})
            n_c = len({k for k, _i, s, _b in ends if s == "in"})
            return [], f"{n_d} -> {n_c}, nothing to lay (its ports meet on one edge)"
    drivers = sorted({k for k, _i, s, _b in ends if s == "out"}, key=fkpmod.natural_key)
    consumers = sorted({k for k, _i, s, _b in ends if s == "in"}, key=fkpmod.natural_key)
    roots = [i for i, grp in enumerate(groups) if grp["driver"]]
    roots += [i for i in range(len(groups)) if i not in roots]
    tries = [(roots[0], m) for m in ("near", "far", "keys")] + [(r, "near") for r in roots[1:]]
    first_reason = None
    for root, mode in tries:
        snap = w.snapshot()
        placed, why = _grow(w, net, groups, root, mode, reach, limit)
        if placed is not None:
            new = _entities(net, placed)
            why = _judge(cat, base, extra, flat, net, new, edges)
            if why is None:
                pairs = sum(1 for _t, kind, _d in placed if kind == "ug_in")
                pipes = sum(1 for _t, kind, _d in placed if kind == PIPE)
                how = (f"{len(drivers)} -> {len(consumers)}, {pipes} pipes, {pairs} "
                       f"pipe-to-ground pair{'s' if pairs != 1 else ''}")
                if direct:
                    how += f", {direct} direct join{'s' if direct != 1 else ''}"
                if notes:
                    how += "; " + "; ".join(notes)
                return new, how
            why = f"routed, but the judge says: {why}"
        w.restore(snap)
        first_reason = first_reason or why
    return None, first_reason


# -- the plug-in ----------------------------------------------------------------------------------
def route(flat, lay, cat, nets, blocked=(), box=None, limit=60000, budget=None):
    """Route the fluid nets ``nets`` of ``flat`` on ``lay`` (belts already laid). ``blocked``:
    terrain tiles in the factory frame; ``box`` (x0, y0, x1, y1): the search bounds (default:
    the factory frame). ``budget``: seconds per net that failed the first pass for passes in
    another order (3.0, the build script's ``--group-budget``; a pass that would not fit in what
    is left is not started); None: as many as help. -> (added {net: [Entity]}, how {net: text},
    failed {net: reason})."""
    import time
    from . import route as routemod                 # the belt jobs: their attach tiles stay free
    from .expand import frame_of
    w0, h0 = lay.size or (0, 0)
    box = box or (0, 0, w0, h0)
    keep = set()
    for name, job in routemod.jobs(flat, lay, cat).items():
        if not job.fluid:
            keep |= set(job.ends())
    # 2.0.62 (the opus run's Oil module: "every connection tile of its box is taken"): every
    # frame was blocked for every net, a sealed module's own included, so no pipe could stand
    # inside a module. Now, as for belts (route.net_frames), a net inside a sealed module routes in
    # that module's frame and keeps off every other frame. 2.0.75: two sealed frames that overlap
    # share the tiles they both cover, as for belts
    frames = {}
    for path, rec in lay.insts.items():
        if rec.kind != "Literal" and path in flat.insts:
            frames[path] = frame_of(flat, lay, path, cat)
    sealed = set(getattr(flat, "sealed", ()) or ())
    tiles_of = {p: {(x, y) for x in range(x0, x1) for y in range(y0, y1)}
                for p, (x0, y0, x1, y1) in frames.items()}

    def net_frames(net):
        parts = net.split("/")[:-1]
        inside = ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]
        inside = [p for p in inside if p in sealed and p in frames]
        blocked = set()
        inner = inside[-1] if inside else None
        for p, tiles in tiles_of.items():
            if p in inside:
                continue
            if inner is not None and p in sealed and not p.startswith(inner + "/") \
                    and not inner.startswith(p + "/"):
                tiles = tiles - tiles_of[inner]
            blocked |= tiles
        return blocked, (frames[inner] if inside else box)
    fluid_all = sorted(routemod.fluid_nets(flat, cat))
    edges = port_edges(flat, lay, cat)
    reach = int(cat.entity(PTG).get("max_distance") or 10)
    base = list(lay.entities.values())
    todo = list(nets)
    ends = {}
    failed0 = {}
    for net in todo:
        try:
            ends[net] = _ends(flat, lay, cat, net)
        except _Skip as exc:
            failed0[net] = str(exc)
    order = [n for n in todo if n in ends]
    per_net = {}
    best = None
    # 2.1.29: retries stop once one routes no more nets than the best before it; each laid every
    # fluid net again, len(order) + 1 times (12+ min on the 20261030-2352 world)
    last = 0
    took = spent = 0.0
    left = None                    # 3.0: the retry budget, set by the first pass's failures
    for attempt in range(len(order) + 1):
        if attempt > 1 and best[0][0] <= last:
            break
        if attempt and left is not None and spent + took > left:
            # 3.0: one pass, as the belts: another would go past the build script's budget
            for net in best[3]:
                if net in ends:
                    best[3][net] += (f"; not tried again in another order: a fluid pass takes "
                                     f"{took:.0f} s, past the route's --group-budget")
            break
        last = best[0][0] if best else 0
        t0 = time.monotonic()
        w, owners, g = _world(flat, lay, cat, fluid_all, blocked, box, keep)
        added, how, failed = {}, {}, dict(failed0)
        extra = []
        occ0 = set(w.occ)
        for k, net in enumerate(order):
            routemod._progress(attempt, k, order, net, which=f"fluid pass {attempt + 1}")
            if net not in per_net:
                per_net[net] = net_frames(net)
            w.occ = occ0 | per_net[net][0]
            w.box = per_net[net][1]
            got, why = _route_net(w, owners, g, flat, cat, net, ends[net], base, extra, reach,
                                  limit, edges)
            w.occ, w.box = occ0, box
            if got is None:
                failed[net] = why
                continue
            added[net], how[net] = got, why
            extra += got
        score = (len(added), -attempt)
        if best is None or score > best[0]:
            best = (score, added, how, failed)
        took = time.monotonic() - t0
        if attempt:
            spent += took
        elif budget is not None:
            left = float(budget) * sum(1 for n in failed if n in ends)
        if len(added) == len(order):
            break
        order = [n for n in order if n in failed] + [n for n in order if n not in failed]
    _score, added, how, failed = best
    return added, how, failed
