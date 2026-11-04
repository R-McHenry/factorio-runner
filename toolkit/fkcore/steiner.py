"""fkcore.steiner: one belt net as a TREE over mixed steps.

Copied from C:\\fk-lead\\repo\\fkc\\steiner.py (repo d6018d3; the file last changed at 144e783).
fkcore.route wraps it: it builds the Grid per net from a layout and turns trees into keyed .fkp
carriers. Changes since the copy:

- A split is cut only where its feed runs straight into it, the root included. The copy let the
  root turn into its first belt and still split there, but ``split_tiles`` lays a splitter by its
  feed's own heading: the splitter landed beside the turn, fed from its side (no edge, the net cut).
- For a tree laid reversed (a collector), set on its Grid by route.py: ``Grid.keep_clear``, tiles a
  split may not leave its spare output on (reversed, every splitter is a merge facing back, whose
  second output points at the tile beside the split's feed; a foreign belt there would take half
  the merged items, a SPILL); ``Grid.drop_taps``: a tap's inserter drops onto its belt, and an
  underground exit takes no drop (fkcore.connect, v4 B-120), so a tapped belt never starts a
  tunnel (which, reversed, is an exit).
- A start state may name the tiles its move takes (a splitter's two, 2.0.77): the goal test picks
  no tap on them. The 10-30 run's Sci0 copper split one tile short of red2, the search planned
  red2's inserter on the splitter's second tile, the attach found it taken and laid no tap, and
  the tree still counted red2 reached ("partly-connected" on every build). ``problems`` now names
  a tap sink with fewer taps than it takes (UNTAPPED).
- A forward branch to a tap sink never ends on an underground exit whose tile ahead is taken
  (2.0.78): a belt's last tile is turned off another net's belt (route._settle_leaves), but an
  exit can't turn, and the 10-30 run's cb5o side-loaded onto the coal belt from one (SPILL).
- A reversed tree's own belts keep off its merges' spare outputs too (2.1, ``Tree.spare``): the
  hourglass 16to16 hub merged two drivers' belts in a splitter and ran a later driver's branch
  through the tile its spare output points at, as a turn; the spare output fed that belt from
  behind, so the game made it straight and the branch side-loaded onto it.
- ``ribbon`` (2.1, PLAN-2.1 "Bus routing: ribbons"): several belts routed as one path, side by
  side and in order. One A* over the ribbon's cross-sections, its footprint as wide as the
  ribbon: straight steps, bus turns (an n x n square, the inner belt turning first) and every
  belt under a crossing together. ``fkcore.ribbons`` turns the result into belts.

The original docstring follows.


``fk.route`` finds ONE belt path between two ports; a net with one source and several consumers is
really a tree, and today every branch point (``BeltSplit``) is hand-declared in source. Here the
search creates the branch points itself: a step from a belt tile may be

    step    a belt onto the next tile (straight, or a 90 degree turn: +turn_cost)
    jump    an underground pair over 2..max_ug tiles (one blocked tile under it, as fk.route)
    split   a splitter across the next tile and one beside it; both outputs continue as branches
    tap     (sinks of kind "tap") an inserter beside the belt picking into a machine: the belt
            itself goes on, so one belt threads past several machines without splitters

States are ``fk.route``'s: (x, y, heading, just_exited_underground), heading 0..3 = N E S W.

Two searches over the same steps (Rory's two directions):

* ``grow`` (forward, many destinations): the tree starts as the source; each round runs one A*
  from EVERY point of the tree at once (dead-end leaves extend for free, a straight belt tile can
  become a splitter at a price) towards ALL unconnected sinks (heuristic: distance to the nearest),
  attaches the cheapest, and repeats. Then rip-up and re-route: each sink's branch is removed and
  re-attached while that lowers the total.
* ``dw`` (reverse, Dreyfus-Wagner): cost[S][state] = cheapest subtree from ``state`` reaching the
  sink set S. Singletons grow backwards from each sink (reverse Dijkstra); larger sets are formed
  where two subtrees meet at a splitter (forwards that is the branch point), then relax backwards
  again. Exact on the state graph except that subtrees do not see each other's tiles; overlaps are
  negotiated PathFinder-style (contested tiles get a growing history cost, then redo).

Pure, stdlib only.
"""
from __future__ import annotations

import heapq
import math
import time

__all__ = ["Grid", "Tree", "FixedSink", "TapSink", "grow", "dw", "COSTS", "ribbon"]

DX = (0, 1, 0, -1)
DY = (-1, 0, 1, 0)
INF = float("inf")
COSTS = {"belt": 1.0, "turn": 0.6, "ug": 6.0, "split": 3.0, "tap": 2.0, "long_tap": 2.5, "max_ug": 5}


def _fdir(h):
    return h * 4


def _hof(direction):
    return {0: 0, 4: 1, 8: 2, 12: 3}[int(direction)]


def _axis(h):
    return h % 2


# -- the ground ---------------------------------------------------------------------------------
class Grid:
    """Blocked tiles, plus the tiles a foreign belt points into (a belt there would get its items
    side-loaded) and the hidden spans of foreign undergrounds per axis (no same-axis tunnel may
    overlap one: fk.route has the same interleave rule in fkc.build._fix_ug_interleave)."""

    def __init__(self, blocked, fronts=(), spans=None):
        self.occ = set(blocked)
        self.fronts = set(fronts)
        self.spans = spans or {0: set(), 1: set()}
        self.keep_clear = set()      # no split's spare (reversed) output may point at these
        self.drop_taps = False       # laid reversed: taps drop onto their belt

    def blocked(self, t):
        return t in self.occ or t in self.fronts

    @classmethod
    def from_entities(cls, cat, entities, extra=()):
        occ, fronts, spans = set(extra), set(), {0: set(), 1: set()}
        ugs = []
        for e in entities:
            name = e.get("name")
            try:
                ts = cat.tiles(name, e["x"], e["y"], e.get("direction"))
            except (KeyError, ValueError):
                continue
            occ |= ts
            d = e.get("direction")
            if d is None or ("belt" not in name and "splitter" not in name):
                continue
            h = _hof(d)
            if name.startswith("underground") or name.endswith("underground-belt"):
                ugs.append((e, h))
                if e.get("belt_to_ground_type") == "input":
                    continue
            for (x, y) in ts:
                fronts.add((x + DX[h], y + DY[h]))
        # pair underground ends: an input and the next output ahead on its line
        outs = {(math.floor(e["x"]), math.floor(e["y"]), h) for e, h in ugs
                if e.get("belt_to_ground_type") == "output"}
        for e, h in ugs:
            if e.get("belt_to_ground_type") != "input":
                continue
            x, y = math.floor(e["x"]), math.floor(e["y"])
            for k in range(1, 12):
                t = (x + DX[h] * k, y + DY[h] * k, h)
                if t in outs:
                    spans[_axis(h)] |= {(x + DX[h] * j, y + DY[h] * j) for j in range(k + 1)}
                    break
        return cls(occ, fronts - occ, spans)


# -- sinks --------------------------------------------------------------------------------------
class FixedSink:
    """A belt must arrive on ``tile`` and leave it flowing ``edir`` (a port's link tile)."""
    terminal = True

    def __init__(self, name, tile, edir):
        self.name, self.tile, self.edir = name, tuple(tile), edir

    def goal(self, state, tree, path=None):
        x, y, d, ex = state
        return (x, y) == self.tile and d != (self.edir + 2) % 4 and (not ex or d == self.edir)

    def h(self, x, y):
        return abs(x - self.tile[0]) + abs(y - self.tile[1])


class TapSink:
    """A machine (tile rectangle) fed by ``k`` inserters picking off the net's belt."""
    terminal = False

    def __init__(self, name, rect, k=1, inserters=("inserter", "long-handed-inserter")):
        self.name, self.rect, self.k, self.inserters = name, rect, k, inserters

    def inside(self, t):
        x0, y0, x1, y1 = self.rect
        return x0 <= t[0] < x1 and y0 <= t[1] < y1

    def taps_at(self, tile, free):
        """(inserter tile, name, direction) taps from a belt on ``tile`` into this machine."""
        out = []
        for h in range(4):
            for name in self.inserters:
                r = 2 if name == "long-handed-inserter" else 1
                q = (tile[0] + DX[h] * r, tile[1] + DY[h] * r)
                if free(q) and self.inside((q[0] + DX[h] * r, q[1] + DY[h] * r)):
                    out.append((q, name, _fdir((h + 2) % 4)))
        return out

    def pick(self, path, tree, taken=()):
        """Taps off the last tiles of ``path`` (one per belt tile, newest first), up to k; none on
        ``taken`` (the tiles the path's start move lays, a splitter's)."""
        on = {(s[0], s[1]) for s in path}
        tunnels = {path[i] for i in range(len(path) - 1) if path[i + 1][3]} \
            if tree.grid.drop_taps else ()
        got = []
        for s in reversed(path[-6:]):
            if s in tunnels:
                continue     # reversed this is an underground exit, which takes no drop
            used = {g[0] for g in got}
            for q, name, direction in self.taps_at((s[0], s[1]), lambda q: tree.free(q) and q not in on
                                                   and q not in used and q not in taken):
                got.append((q, name, direction, self.name))
                break
            if len(got) >= self.k:
                break
        return got

    def goal(self, state, tree, path=None, taken=()):
        x, y, d, ex = state
        if ex and not tree.grid.drop_taps and tree.grid.blocked((x + DX[d], y + DY[d])):
            return False     # an exit can't turn: it would feed what stands ahead (2.0.78)
        return len(self.pick(path or [state], tree, taken)) >= self.k

    def h(self, x, y):
        x0, y0, x1, y1 = self.rect
        dx = max(x0 - x, 0, x - (x1 - 1))
        dy = max(y0 - y, 0, y - (y1 - 1))
        return max(0, dx + dy - 3)    # a long-handed tap reaches 3 tiles from the footprint


# -- the tree -----------------------------------------------------------------------------------
class Tree:
    """Arrival states with their outgoing move: ("step", child), ("jump", child),
    ("split", side, child_straight, child_side); a state with no move is a leaf (a sink or a
    dead end). ``taps`` are inserters picking off the tree into machines."""

    def __init__(self, grid, root, costs=None):
        self.grid, self.root, self.costs = grid, root, dict(COSTS, **(costs or {}))
        self.succ, self.sink_at, self.taps, self.reserved = {}, {}, [], set()
        self._tiles = None

    def copy(self):
        t = Tree(self.grid, self.root, self.costs)
        t.succ, t.sink_at = dict(self.succ), dict(self.sink_at)
        t.taps, t.reserved = list(self.taps), set(self.reserved)
        return t

    def states(self):
        out, stack = [], [self.root]
        while stack:
            s = stack.pop()
            out.append(s)
            mv = self.succ.get(s)
            if mv is None:
                continue
            stack.extend(mv[2:] if mv[0] == "split" else [mv[1]])
        return out

    def tiles(self):
        """tile -> list of owners (a state or a splitter half); >1 owner is a conflict."""
        if self._tiles is None:
            own, spare = {}, set()
            for s in self.states():
                own.setdefault((s[0], s[1]), []).append(s)
                mv = self.succ.get(s)
                if mv and mv[0] == "split":
                    n, m = self.split_tiles(s, mv[1])
                    own.setdefault(n, []).append(("splitter", s))
                    own.setdefault(m, []).append(("splitter", s))
                    if self.grid.drop_taps:
                        spare.add(self.spare_tile(s, mv[1]))
            for q, _n, _d, _s in self.taps:
                own.setdefault(q, []).append(("tap", q))
            self._tiles, self._spare = own, spare
        return self._tiles

    def spare(self):
        """Laid reversed, the tiles the merges' spare outputs point at: a belt of the tree there
        would be fed from behind by the merge, so a branch turning there side-loads instead."""
        self.tiles()
        return self._spare

    def touch(self):
        self._tiles = None

    def free(self, t):
        return (not self.grid.blocked(t) and t not in self.tiles() and t not in self.reserved
                and t not in self.spare())

    @staticmethod
    def split_tiles(s, side):
        x, y, d, _ex = s
        n = (x + DX[d], y + DY[d])
        sh = (d + side) % 4
        return n, (n[0] + DX[sh], n[1] + DY[sh])

    @staticmethod
    def spare_tile(s, side):
        """The tile beside a split's feed ``s``: laid reversed, its merge's spare output points
        there."""
        sh = (s[2] + side) % 4
        return (s[0] + DX[sh], s[1] + DY[sh])

    def spans(self):
        out = {0: set(), 1: set()}
        for s in self.states():
            mv = self.succ.get(s)
            if mv and mv[0] == "jump":
                c = mv[1]
                k = abs(c[0] - s[0]) + abs(c[1] - s[1])
                out[_axis(c[2])] |= {(s[0] + DX[c[2]] * j, s[1] + DY[c[2]] * j) for j in range(k + 1)}
        return out

    # -- views ------------------------------------------------------------------------------------
    def entities(self, sinks, net="net", reverse=False):
        """Plan entities of the tree. ``reverse``: the same tree with the items flowing the other
        way, from the sinks to the root -- a collector (a block's output net): every belt points back
        at the tile it was reached from, the underground halves swap, a splitter merges its two
        branches, and every tap inserter turns round to pick from the machine and drop on the belt."""
        if reverse:
            return self._entities_reversed(net)
        ents = []
        for s in self.states():
            x, y, d, ex = s
            mv = self.succ.get(s)
            if mv is None:
                i = self.sink_at.get(s)
                out = sinks[i].edir if i is not None and isinstance(sinks[i], FixedSink) else d
            elif mv[0] == "jump":
                ents.append(_ent("underground-belt", x, y, d, net, "input"))
                continue
            elif mv[0] == "split":
                out = d
                n, m = self.split_tiles(s, mv[1])
                ents.append({"name": "splitter", "x": (n[0] + m[0]) / 2 + 0.5, "y": (n[1] + m[1]) / 2 + 0.5,
                             "direction": _fdir(d), "role": "spine", "net": net})
            else:
                out = mv[1][2]
            if ex:
                ents.append(_ent("underground-belt", x, y, d, net, "output"))
            else:
                ents.append(_ent("transport-belt", x, y, out, net))
        for q, name, direction, sink in self.taps:
            ents.append({"name": name, "x": q[0] + 0.5, "y": q[1] + 0.5, "direction": direction,
                         "role": "ins", "link": f"{net}~{sink}"})
        return ents

    def _entities_reversed(self, net):
        ents = []
        for s in self.states():
            x, y, d, ex = s
            mv = self.succ.get(s)
            back = (d + 2) % 4               # towards the tile this state was reached from
            if mv is not None and mv[0] == "jump":
                ents.append(_ent("underground-belt", x, y, back, net, "output"))
                continue
            if mv is not None and mv[0] == "split":
                n, m = self.split_tiles(s, mv[1])
                ents.append({"name": "splitter", "x": (n[0] + m[0]) / 2 + 0.5, "y": (n[1] + m[1]) / 2 + 0.5,
                             "direction": _fdir(back), "role": "spine", "net": net})
            if ex:
                ents.append(_ent("underground-belt", x, y, back, net, "input"))
            else:
                ents.append(_ent("transport-belt", x, y, back, net))
        for q, name, direction, sink in self.taps:
            ents.append({"name": name, "x": q[0] + 0.5, "y": q[1] + 0.5, "direction": (direction + 8) % 16,
                         "role": "ins", "link": f"{sink}->{net}"})
        return ents

    def counts(self):
        c = {"belts": 0, "turns": 0, "undergrounds": 0, "splitters": 0, "taps": len(self.taps)}
        for s in self.states():
            mv = self.succ.get(s)
            if s[3]:
                c["undergrounds"] += 1
            if mv is None:
                c["belts"] += 0 if s[3] else 1
                continue
            if mv[0] == "jump":
                c["undergrounds"] += 1
                continue
            if not s[3]:
                c["belts"] += 1
            if mv[0] == "split":
                c["splitters"] += 1
            elif mv[1][2] != s[2] and s != self.root:
                c["turns"] += 1
        return c

    def cost(self):
        c, k = self.counts(), self.costs
        return round(c["belts"] * k["belt"] + c["turns"] * k["turn"] + c["undergrounds"] / 2 * k["ug"]
                     + c["splitters"] * k["split"]
                     + sum(k["long_tap"] if n == "long-handed-inserter" else k["tap"] for _q, n, _d, _s in self.taps), 3)

    def problems(self, sinks):
        """What would break in the game: shared tiles, blocked tiles, unreached sinks, tunnels
        crossing a same-axis tunnel."""
        out = []
        for t, owners in sorted(self.tiles().items()):
            if len(owners) > 1:
                out.append(f"TILE_SHARED {t}: {len(owners)} pieces")
            if self.grid.blocked(t):
                out.append(f"BLOCKED {t}")
        for t in sorted(self.spare() & set(self.tiles())):
            out.append(f"SPARE_FEEDS {t}: a merge's spare output points at the tree's own belt")
        reached = set(self.sink_at.values())
        for i, s in enumerate(sinks):
            if i not in reached:
                out.append(f"UNREACHED {s.name}")
            elif isinstance(s, TapSink):
                got = sum(t[3] == s.name for t in self.taps)
                if got < s.k:
                    out.append(f"UNTAPPED {s.name}: {got} of {s.k} inserters")
        mine = self.spans()
        for ax in (0, 1):
            if mine[ax] & self.grid.spans[ax]:
                out.append(f"UG_INTERLEAVE axis {ax}: {sorted(mine[ax] & self.grid.spans[ax])[:3]}")
        return out


def _ent(name, x, y, h, net, ug=None):
    e = {"name": name, "x": x + 0.5, "y": y + 0.5, "direction": _fdir(h), "role": "spine", "net": net}
    if ug:
        e["belt_to_ground_type"] = ug
    return e


def audit(cat, others, ents, root, sinks, max_ug=5):
    """Check a belt tree from its ENTITIES alone (not the Tree structure): follow the items from
    the source through belts, tunnel pairs (nearest same-axis end, as the game pairs them) and
    splitters; report overlaps, side-loads into or from foreign belts, mispaired tunnels, loops and
    unreached sinks. ``others``: everything else that stands."""
    probs = []
    mine = {}
    for i, e in enumerate(ents):
        for t in cat.tiles(e["name"], e["x"], e["y"], e.get("direction")):
            if t in mine:
                probs.append(f"OVERLAP {t}")
            mine[t] = i
    foreign = {}
    for e in others:
        try:
            for t in cat.tiles(e["name"], e["x"], e["y"], e.get("direction")):
                foreign[t] = e
                if t in mine:
                    probs.append(f"ON_FOREIGN {t} {e['name']}")
        except (KeyError, ValueError):
            pass
    sink_tiles = {s.tile for s in sinks}
    ug_ends = {}
    for e in list(ents) + list(others):
        if e.get("name") == "underground-belt":
            ug_ends[(math.floor(e["x"]), math.floor(e["y"]))] = e

    def fronts(i):
        e = ents[i]
        h = _hof(e["direction"])
        if e["name"] == "underground-belt" and e.get("belt_to_ground_type") == "input":
            x, y = math.floor(e["x"]), math.floor(e["y"])
            for k in range(1, max_ug + 1):
                t = (x + DX[h] * k, y + DY[h] * k)
                u = ug_ends.get(t)
                if u is not None and _axis(_hof(u["direction"])) == _axis(h):
                    if u in ents and u.get("belt_to_ground_type") == "output" and _hof(u["direction"]) == h:
                        return [("ug", t)]
                    probs.append(f"UG_PAIR {(x, y)} meets {u.get('belt_to_ground_type')} at {t}")
                    return []
            probs.append(f"UG_PAIR {(x, y)} has no exit")
            return []
        ts = cat.tiles(e["name"], e["x"], e["y"], e.get("direction"))
        return [("belt", (t[0] + DX[h], t[1] + DY[h])) for t in ts]

    seen, reached, stack = set(), set(), [mine.get((root[0], root[1]))]
    if stack[0] is None:
        return probs + ["NO_SOURCE"]
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        for kind, t in fronts(i):
            if t in mine:
                j = mine[t]
                if kind == "belt" and ents[j]["name"] == "underground-belt" and \
                        ents[j].get("belt_to_ground_type") == "output":
                    probs.append(f"FEEDS_UG_EXIT {t}")
                stack.append(j)
            elif kind == "belt":
                me = ents[i]
                here = (math.floor(me["x"]), math.floor(me["y"]))
                if here in sink_tiles or me["name"] == "splitter" and any(
                        s.tile in cat.tiles(me["name"], me["x"], me["y"], me.get("direction")) for s in sinks):
                    reached.add(here)
                elif t in foreign and ("belt" in foreign[t]["name"] or "splitter" in foreign[t]["name"]):
                    probs.append(f"SIDELOADS_FOREIGN {here} -> {t}")
    for s in sinks:
        if s.tile not in reached:
            probs.append(f"UNREACHED {s.name}")
    if len(seen) != len(ents):
        probs.append(f"ORPHANS {len(ents) - len(seen)}")
    for e in others:   # a foreign belt pointing into the tree side-loads its items onto our net
        n = e.get("name", "")
        if not ("belt" in n or "splitter" in n) or e.get("belt_to_ground_type") == "input":
            continue
        h = _hof(e["direction"])
        try:
            ts = cat.tiles(n, e["x"], e["y"], e.get("direction"))
        except (KeyError, ValueError):
            continue
        for t in ts:
            f = (t[0] + DX[h], t[1] + DY[h])
            if f in mine and f != (root[0], root[1]):
                probs.append(f"FOREIGN_FEEDS {t} -> {f}")
    return probs


# -- forward: grow the tree towards all sinks ---------------------------------------------------
def _starts(tree):
    """(state, g0, how[, taken]) the next branch may leave the tree from: a dead-end leaf for
    free, or a splitter cut into a straight belt (``taken``: its two tiles)."""
    k = tree.costs
    out = []
    for s in tree.states():
        mv = tree.succ.get(s)
        if mv is None and s not in tree.sink_at:
            out.append((s, 0.0, ("leaf", s)))
        elif mv is None and s in tree.sink_at and not tree.sink_terminal.get(s, True):
            out.append((s, 0.0, ("leaf", s)))
        if mv is None or mv[0] != "step":
            continue
        c = mv[1]
        d = c[2]
        cm = tree.succ.get(c)
        if c[3] or cm is None or cm[0] != "step" or cm[1][2] != d or c in tree.sink_at:
            continue
        if s[2] != d:
            continue  # s turns into c: c is a curve, a splitter needs a straight feed
        for side in (1, 3):
            n, m = Tree.split_tiles(s, side)
            o2 = (m[0] + DX[d], m[1] + DY[d], d, 0)
            sh = (d + side) % 4
            if (s[0] + DX[sh], s[1] + DY[sh]) in tree.grid.keep_clear:
                continue     # laid reversed, this merge's spare output would feed what is there
            if tree.grid.drop_taps and Tree.spare_tile(s, side) in tree.tiles():
                continue     # ... the tree's own belt there, too
            if tree.free(m) and tree.free((o2[0], o2[1])):
                out.append((o2, k["split"] - k["belt"], ("split", s, side, c), (n, m)))
    return out


def _astar(tree, starts, sinks, targets, limit=60000):
    """One A* from every start state to the nearest target sink; (path, how, sink, cost) or None.
    A start is (state, g0, how) or (state, g0, how, taken): a tap is never planned on ``taken``."""
    k = tree.costs
    grid, tiles, res, spare = tree.grid, tree.tiles(), tree.reserved, tree.spare()
    sink_tiles = {s.tile for i, s in enumerate(sinks) if isinstance(s, FixedSink)}
    spans = tree.spans()

    def blocked(t):
        return grid.blocked(t) or t in tiles or t in res or t in spare

    def crosses_own_spare(path):
        how = how_of[path[0]]
        if not grid.drop_taps or how[0] != "split":
            return False
        return Tree.spare_tile(how[1], how[2]) in {(p[0], p[1]) for p in path}

    def hh(x, y):
        return min(sinks[i].h(x, y) for i in targets)

    tapped = set()           # laid reversed, a tapped belt takes a drop: it starts no tunnel
    if grid.drop_taps:
        for q, name, direction, _s in tree.taps:
            r, g0 = (2 if name == "long-handed-inserter" else 1), int(direction) // 4
            tapped.add((q[0] + DX[g0] * r, q[1] + DY[g0] * r))

    heap, g, parent, how_of, taken_of = [], {}, {}, {}, {}
    for st, g0, how, *taken in starts:
        if g0 < g.get(st, INF):
            g[st] = g0
            parent[st] = None
            how_of[st] = how
            taken_of[st] = frozenset(taken[0]) if taken else frozenset()
            heapq.heappush(heap, (g0 + hh(st[0], st[1]), -g0, st))
    done = set()
    jumps = range(2, int(k["max_ug"]) + 1)
    while heap:
        _f, ng, st = heapq.heappop(heap)
        gs = -ng
        if st in done or gs > g.get(st, INF):
            continue
        done.add(st)
        if len(done) > limit:
            return None
        path = None
        for i in targets:
            if sinks[i].terminal:
                hit = sinks[i].goal(st, tree)
            elif sinks[i].h(st[0], st[1]) > 0:
                hit = False          # too far for any tap: skip the path trace
            else:
                path = path or _trace(parent, st)
                hit = sinks[i].goal(st, tree, path, taken_of[path[0]])
            if hit:
                path = path or _trace(parent, st)
                if len({(p[0], p[1]) for p in path}) == len(path) and \
                        not crosses_own_spare(path):
                    return path, how_of[path[0]], i, gs
        x, y, d, ex = st
        if (x, y) in sink_tiles and parent[st] is not None:
            continue   # nothing continues through a port's link tile
        for nd in range(4):
            if ex and nd != d:
                continue
            if nd == (d + 2) % 4:
                continue
            nx, ny = x + DX[nd], y + DY[nd]
            step = k["belt"] + (k["turn"] if nd != d else 0.0)
            if not blocked((nx, ny)):
                _push(heap, g, parent, (nx, ny, nd, 0), st, gs + step, hh)
            if ex or nd != d or (x, y) in tapped:
                continue
            seen_block = False
            for j in jumps:
                mx, my = x + DX[d] * (j - 1), y + DY[d] * (j - 1)
                if (mx, my) in sink_tiles:
                    break
                if blocked((mx, my)):
                    seen_block = True
                tx, ty = x + DX[d] * j, y + DY[d] * j
                span = {(x + DX[d] * q, y + DY[d] * q) for q in range(j + 1)}
                if span & grid.spans[_axis(d)] or span & spans[_axis(d)]:
                    break
                if seen_block and not blocked((tx, ty)):
                    _push(heap, g, parent, (tx, ty, d, 1), st, gs + k["ug"], hh)
    return None


def _push(heap, g, parent, st, prev, cost, hh):
    if cost < g.get(st, INF):
        g[st] = cost
        parent[st] = prev
        heapq.heappush(heap, (cost + hh(st[0], st[1]), -cost, st))


def _trace(parent, st):
    out = [st]
    while parent[out[-1]] is not None:
        out.append(parent[out[-1]])
    out.reverse()
    return out


def _attach(tree, path, how, sink_i, sinks):
    tree = tree.copy()
    if how[0] == "split":
        _k, s, side, c = how
        nxt = tree.succ.pop(c)
        tree.succ[s] = ("split", side, nxt[1], path[0])
    for a, b in zip(path, path[1:]):
        tree.succ[a] = ("jump", b) if b[3] else ("step", b)
    end = path[-1]
    sink = sinks[sink_i]
    if isinstance(sink, TapSink):
        got = sink.pick(path, tree)    # the same choice the goal test made (before the path lands)
        tree.touch()
        tree.taps += got
        tree.reserved |= {g[0] for g in got}
    tree.sink_at[end] = sink_i
    tree.sink_terminal = getattr(tree, "sink_terminal", {})
    tree.sink_terminal[end] = sink.terminal
    tree.touch()
    return tree


def _detach(tree, sink_i, sinks):
    """The tree without sink i's own branch (back to its split point or its dead-end start)."""
    tree = tree.copy()
    tree.sink_terminal = dict(getattr(tree, "sink_terminal", {}))
    end = next(s for s, i in tree.sink_at.items() if i == sink_i)
    if any(tree.succ.get(end) for _ in [0]):
        return None   # a tap sink the tree runs on through: keep it
    parent = {}
    for s, mv in tree.succ.items():
        for c in (mv[2:] if mv[0] == "split" else [mv[1]]):
            parent[c] = s
    chain, s = [end], end
    while s in parent:
        p = parent[s]
        mv = tree.succ[p]
        if mv[0] == "split" or p in tree.sink_at or p == tree.root:
            break
        chain.append(p)
        s = p
    p = parent.get(s)
    for c in chain:
        tree.succ.pop(c, None)
        tree.sink_at.pop(c, None)
    if p is not None and tree.succ[p][0] == "split":
        _k, side, c1, c2 = tree.succ[p]
        keep = c2 if s == c1 else c1
        n, m = Tree.split_tiles(p, side)
        d = p[2]
        if keep == c1:
            mid = (n[0], n[1], d, 0)
            tree.succ[p] = ("step", mid)
            tree.succ[mid] = ("step", c1)
        else:
            sh = (d + side) % 4
            a, b = (n[0], n[1], d, 0), (m[0], m[1], sh, 0)
            tree.succ[p] = ("step", a)
            tree.succ[a] = ("step", b)
            tree.succ[b] = ("step", c2)
    elif p is not None:
        tree.succ.pop(p, None)
    tree.taps = [t for t in tree.taps if t[3] != sinks[sink_i].name]
    tree.reserved = {t[0] for t in tree.taps}
    tree.touch()
    return tree


def grow(grid, root, sinks, costs=None, improve=8, order=None, limit=60000):
    """Forward tree search; {"tree", "cost", "counts", "problems", "seconds", "log"}."""
    t0 = time.time()
    tree = Tree(grid, root, costs)
    tree.sink_terminal = {}
    left = list(order) if order is not None else list(range(len(sinks)))
    log = []
    while left:
        targets = left if order is None else left[:1]
        got = _astar(tree, _starts(tree), sinks, targets, limit)
        if got is None:
            log.append(f"cannot reach {[sinks[i].name for i in targets]}")
            break
        path, how, i, gcost = got
        tree = _attach(tree, path, how, i, sinks)
        left.remove(i)
        log.append(f"+{sinks[i].name} via {how[0]} ({gcost:.1f})")
    for _round in range(improve):
        better = False
        for i in range(len(sinks)):
            cut = _detach(tree, i, sinks) if i in tree.sink_at.values() else None
            if cut is None:
                continue
            got = _astar(cut, _starts(cut), sinks, [i], limit)
            if got is None:
                continue
            new = _attach(cut, got[0], got[1], i, sinks)
            if not new.problems(sinks) and new.cost() < tree.cost() - 1e-9:
                log.append(f"reroute {sinks[i].name}: {tree.cost():.1f} -> {new.cost():.1f}")
                tree, better = new, True
        if not better:
            break
    return {"tree": tree, "cost": tree.cost(), "counts": tree.counts(), "problems": tree.problems(sinks),
            "seconds": round(time.time() - t0, 2), "log": log}


# -- reverse: Dreyfus-Wagner over the state graph -----------------------------------------------
def dw(grid, root, sinks, costs=None, margin=6, iters=12, history_inc=3.0):
    """Exact Steiner DP per negotiation round (fixed-tile sinks only)."""
    t0 = time.time()
    k = dict(COSTS, **(costs or {}))
    pts = [(root[0], root[1])] + [s.tile for s in sinks]
    x0, y0 = min(p[0] for p in pts) - margin, min(p[1] for p in pts) - margin
    x1, y1 = max(p[0] for p in pts) + margin, max(p[1] for p in pts) + margin
    W, H = x1 - x0 + 1, y1 - y0 + 1
    nS = len(sinks)
    full = (1 << nS) - 1
    sink_tile = {s.tile: i for i, s in enumerate(sinks)}
    free = [[not grid.blocked((x0 + i, y0 + j)) for i in range(W)] for j in range(H)]

    def sid(x, y, d, ex):
        return (((y - y0) * W + (x - x0)) * 4 + d) * 2 + ex

    def ok(x, y):
        return x0 <= x <= x1 and y0 <= y <= y1 and free[y - y0][x - x0]

    N = W * H * 8
    states = [(x, y, d, ex) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)
              for d in range(4) for ex in (0, 1)]
    # reverse moves: for state index s, list of (pred index, cost, tiles-entered-cost-key)
    preds = [[] for _ in range(N)]
    for (x, y, d, ex) in states:
        if not ok(x, y):
            continue
        s = sid(x, y, d, ex)
        if ex == 0:
            px, py = x - DX[d], y - DY[d]
            if ok(px, py) and (px, py) not in sink_tile and ((px, py) != (root[0], root[1])):
                for pd in range(4):
                    if pd == (d + 2) % 4:
                        continue
                    for pex in (0, 1):
                        if pex and pd != d:
                            continue
                        preds[s].append((sid(px, py, pd, pex), k["belt"] + (k["turn"] if pd != d else 0)))
            elif (px, py) == (root[0], root[1]) and d != (root[2] + 2) % 4:
                preds[s].append((sid(*root), k["belt"] + (k["turn"] if root[2] != d else 0)))
        else:
            seen_block = False
            for j in range(1, int(k["max_ug"]) + 1):
                mx, my = x - DX[d] * j, y - DY[d] * j
                if j >= 2 and seen_block and ok(mx, my) and (mx, my) not in sink_tile:
                    span = {(x - DX[d] * q, y - DY[d] * q) for q in range(j + 1)}
                    if not span & grid.spans[_axis(d)]:
                        preds[s].append((sid(mx, my, d, 0), k["ug"]))
                if (mx, my) in sink_tile:
                    break
                if grid.blocked((mx, my)):
                    seen_block = True
    # splitter moves: (state, side, n, m, o1, o2)
    splits = []
    for (x, y, d, ex) in states:
        if not ok(x, y) or (x, y) in sink_tile:
            continue
        for side in (1, 3):
            n, m = Tree.split_tiles((x, y, d, ex), side)
            o1 = (n[0] + DX[d], n[1] + DY[d])
            o2 = (m[0] + DX[d], m[1] + DY[d])
            if all(ok(*t) for t in (n, m, o1, o2)) and not {n, m} & set(sink_tile):
                splits.append((sid(x, y, d, ex), side, n, m, sid(o1[0], o1[1], d, 0), sid(o2[0], o2[1], d, 0)))
    hist = {}
    best = None
    log = []
    for it in range(iters):
        def pen(t):
            return hist.get(t, 0.0)
        D = [None] * (full + 1)
        how = [None] * (full + 1)
        for S in sorted(range(1, full + 1), key=lambda m: bin(m).count("1")):
            cost = [INF] * N
            arg = [None] * N
            if S & (S - 1) == 0:
                i = S.bit_length() - 1
                sk = sinks[i]
                for d in range(4):
                    for ex in (0, 1):
                        st = (sk.tile[0], sk.tile[1], d, ex)
                        if ok(*sk.tile) and sk.goal(st, None):
                            cost[sid(*st)] = pen(sk.tile)
                            arg[sid(*st)] = ("sink", i)
            else:
                for s, side, n, m, o1, o2 in splits:
                    base = k["split"] + pen(n) + pen(m)
                    sub = (S - 1) & S
                    while sub:      # ordered: o1 runs straight on, o2 leaves to the side
                        a = D[sub][o1]
                        if a < INF:
                            c = base + a + D[S ^ sub][o2]
                            if c < cost[s]:
                                cost[s] = c
                                arg[s] = ("split", side, sub, o1, o2)
                        sub = (sub - 1) & S
            # reverse Dijkstra: relax towards predecessors
            heap = [(c, s) for s, c in enumerate(cost) if c < INF]
            heapq.heapify(heap)
            while heap:
                c, s = heapq.heappop(heap)
                if c > cost[s]:
                    continue
                x, y = states[s][0], states[s][1]
                for p, step in preds[s]:
                    px, py = states[p][0], states[p][1]
                    nc = c + step + pen((px, py))
                    if nc < cost[p]:
                        cost[p] = nc
                        arg[p] = ("move", s)
                        heapq.heappush(heap, (nc, p))
            D[S], how[S] = cost, arg
        rs = sid(*root)
        if D[full][rs] == INF:
            log.append("no tree")
            break
        tree = Tree(grid, root, costs)
        tree.sink_terminal = {}

        def build(S, s):
            a = how[S][s]
            st = states[s]
            if a[0] == "sink":
                tree.sink_at[st] = a[1]
                return
            if a[0] == "move":
                nxt = states[a[1]]
                tree.succ[st] = ("jump", nxt) if nxt[3] else ("step", nxt)
                build(S, a[1])
                return
            _k, side, sub, o1, o2 = a
            tree.succ[st] = ("split", side, states[o1], states[o2])
            build(sub, o1)
            build(S ^ sub, o2)
        build(full, rs)
        tree.touch()
        probs = tree.problems(sinks)
        cost_now = tree.cost()
        log.append(f"round {it + 1}: cost {cost_now:.1f}, {len(probs)} problems")
        if best is None or (len(probs), cost_now) < (len(best[1]), best[0].cost()):
            best = (tree, probs)
        if not probs:
            break
        for t, owners in tree.tiles().items():
            if len(owners) > 1:
                hist[t] = hist.get(t, 0.0) + history_inc
    tree, probs = best
    return {"tree": tree, "cost": tree.cost(), "counts": tree.counts(), "problems": probs,
            "seconds": round(time.time() - t0, 2), "log": log, "region": [W, H]}


# -- ribbons: several belts as one path (2.1) ------------------------------------------------------
#: A ribbon of n belts is searched by its cross-section: n tiles side by side across its heading,
#: belt k on the k-th tile to the right of belt 0 (looking along the heading), all pointing ahead.
#: A state is (x, y, h, exits, live): belt 0's tile, the heading, the belts whose tile there is an
#: underground exit, and the belts still in the ribbon (a bit each). Moves:
#:
#:   step   every live belt one tile ahead;
#:   turn   a bus turn: the n x n square ahead, each belt running on to its turning row and across;
#:          turning right, belt n-1 (the inner one) turns first, on the square's first row, and
#:          belt 0 last; turning left, belt 0 first. The move ends on the cross-section beyond the
#:          square, so every belt there runs straight again and the order is the same: belt k is
#:          still k tiles right of belt 0;
#:   tunnel the ribbon j tiles on, each belt under what blocks its own line and the rest on the
#:          surface (2.1 ribbon tunnels): belt k's tunnel starts on the free tile before the first
#:          blocked tile of its line and ends on the free tile after the last one, at most max_ug
#:          apart (the reach of the fastest underground the ribbon may lay), clear of every other
#:          tunnel on that line (``grid.spans``); its entrance is a fresh tile or the cross-section
#:          it starts from, unless that is its exit. The move starts where some belt's line is
#:          blocked one tile ahead and ends on the last exit, so a crossing that blocks every belt
#:          is every belt under together, side by side and the same length. Straight only: no
#:          belt tunnels through a turn's square.
#:
#: A belt leaves the ribbon at its fixed end (its tile and heading there; it costs nothing and the
#: rest keep their offsets) or, every live belt at once, onto the tiles ahead of them (``walks``:
#: loose belts from there on, at ``loose`` times their walking distance). The ribbon starts on any
#: of ``sources`` (a cross-section and what reaching it costs).


_YELLOW_REACH = 5


def _turn_rel(n):
    """{(side, k): [(dx, dy) per tile]} for heading north, from belt k's own tile: its tiles
    through a turn, the tile on the cross-section beyond the square last."""
    out = {}
    for k in range(n):
        # north: ahead is (0, -1), right is (1, 0); (i, c) = i ahead, c to the right of belt 0
        out[1, k] = [(0, -i) for i in range(1, n - k + 1)] + [(c - k, -(n - k)) for c in
                                                            range(k + 1, n + 1)]
        out[3, k] = [(0, -i) for i in range(1, k + 2)] + [(c - k, -(k + 1))
                                                          for c in range(k - 1, -2, -1)]
    return out


def _rot(dx, dy, h):
    """(dx, dy) laid out for heading north, turned to heading ``h``."""
    for _ in range(h):
        dx, dy = -dy, dx
    return dx, dy


def _cross(x, y, h, k):
    r = (h + 1) % 4
    return (x + DX[r] * k, y + DY[r] * k)


def ribbon(grid, n, sources, fixed=None, walks=None, costs=None, loose=2.0, limit=400000,
           own=None):
    """One ribbon of ``n`` belts over ``grid`` (module comment above). ``sources``:
    [((x, y, h), g0)]; ``fixed``: per belt, (tile, heading) where it may end, or None; ``walks``:
    per belt, {tile: steps} from its loose ends, or None (no loose end); ``own``: {tile: (belt,
    heading)}, tiles only that belt may take, and only on a cross-section with that heading (its
    own end: another belt there, or itself turned, would feed or block it). -> {"lines": per belt
    [[tile, kind]] in search order (kind "belt", "in" or "out"), "heads": per belt the heading of
    its last tile, "ends": per belt ("fixed",) or ("loose", tile ahead), "start", "cost",
    "counts", "seconds", "expanded"}, or {"fail": reason}."""
    t0 = time.time()
    k_ = dict(COSTS, **(costs or {}))
    fixed = list(fixed or [None] * n)
    walks = list(walks or [None] * n)
    full = (1 << n) - 1
    max_ug = int(k_["max_ug"])
    c_belt, c_turn, c_ug = k_["belt"], k_["turn"], k_["ug"]
    c_far = 1.5             # a tile of tunnel beyond yellow's reach: a dearer underground, so a
    #                         long tunnel is laid only where a yellow one doesn't reach
    rel = _turn_rel(n)
    turns = {(h, side, b): [_rot(dx, dy, h) for dx, dy in rel[side, b]]
             for h in range(4) for side in (1, 3) for b in range(n)}
    own = dict(own or {})
    gblocked = grid.blocked

    def blocked(t):
        return gblocked(t) or t in own

    def taken(t, b, h):
        """Belt b may not stand on t, heading h, as a cross-section's tile."""
        if t in own:
            return own[t] != (b, h)
        return gblocked(t)

    def settle(x, y, h, mask):
        for b in range(n):
            if mask >> b & 1 and fixed[b] is not None:
                t, fh = fixed[b]
                if fh == h and _cross(x, y, h, b) == tuple(t):
                    mask &= ~(1 << b)
        return mask

    def hh(x, y, h, mask):
        tot = 0
        for b in range(n):
            if not mask >> b & 1:
                continue
            t = _cross(x, y, h, b)
            best = INF
            if fixed[b] is not None:
                ft = fixed[b][0]
                best = abs(t[0] - ft[0]) + abs(t[1] - ft[1])
            if walks[b] is not None:
                w = walks[b].get(t)
                if w is not None and w < best:
                    best = w
            if best == INF:
                return INF
            tot += best
        return tot

    heap, g, parent, count = [], {}, {}, 0
    for (x, y, h), g0 in sources:
        if any(taken(_cross(x, y, h, b), b, h) for b in range(n)):
            continue
        st = (x, y, h, 0, settle(x, y, h, full))
        if g0 < g.get(st, INF):
            f = g0 + hh(x, y, h, st[4])
            if f == INF and st[4]:
                continue
            g[st] = g0
            parent[st] = (None, ("start",))
            count += 1
            heapq.heappush(heap, (f, -g0, count, st))
    if not heap:
        return {"fail": "no free cross-section to start from"}

    def push(st, prev, move, cost):
        nonlocal count
        if cost >= g.get(st, INF):
            return
        f = cost + (hh(st[0], st[1], st[2], st[4]) if st[0] != "fan" and st[4] else 0.0)
        if f == INF:
            return
        g[st] = cost
        parent[st] = (prev, move)
        count += 1
        heapq.heappush(heap, (f, -cost, count, st))

    done, end = set(), None
    while heap:
        _f, ng, _c, st = heapq.heappop(heap)
        gs = -ng
        if st in done or gs > g.get(st, INF):
            continue
        done.add(st)
        if st[0] == "fan" or st[4] == 0:
            end = st
            break
        if len(done) > limit:
            return {"fail": f"the search gave up after {limit} cross-sections"}
        x, y, h, ex, mask = st
        live = [b for b in range(n) if mask >> b & 1]
        fx, fy = DX[h], DY[h]
        # every live belt leaves here, onto the tile ahead of it (loose from there)
        if all(walks[b] is not None for b in live):
            extra = 0.0
            for b in live:
                a = _cross(x + fx, y + fy, h, b)
                w = walks[b].get(a)
                if w is None or blocked(a):
                    break
                extra += loose * (w + 1)
            else:
                push(("fan", st), st, ("fan",), gs + extra)
        # the outermost belt on either side leaves alone (the rest keep their offsets)
        if len(live) > 1:
            for b in {live[0], live[-1]}:
                if walks[b] is None:
                    continue
                a = _cross(x + fx, y + fy, h, b)
                w = walks[b].get(a)
                if w is not None and not blocked(a):
                    push((x, y, h, ex, mask & ~(1 << b)), st, ("drop", b),
                         gs + loose * (w + 1))
        # step
        nx, ny = x + fx, y + fy
        if not any(taken(_cross(nx, ny, h, b), b, h) for b in live):
            push((nx, ny, h, 0, settle(nx, ny, h, mask)), st, ("step",), gs + len(live) * c_belt)
        # bus turns
        r = (h + 1) % 4
        for side in (1, 3):
            laid, ok = 0, True
            nh = (h + 1) % 4 if side == 1 else (h + 3) % 4
            for b in live:
                bx, by = _cross(x, y, h, b)
                path = turns[h, side, b]
                for i, (dx, dy) in enumerate(path):
                    t = (bx + dx, by + dy)
                    if taken(t, b, nh) if i == len(path) - 1 else blocked(t):
                        ok = False
                        break
                    laid += 1
                if not ok:
                    break
            if not ok:
                continue
            if side == 1:
                sx, sy, sh = x + n * fx + n * DX[r], y + n * fy + n * DY[r], (h + 1) % 4
            else:
                sx, sy, sh = x + fx - DX[r], y + fy - DY[r], (h + 3) % 4
            push((sx, sy, sh, 0, settle(sx, sy, sh, mask)), st, ("turn", side),
                 gs + laid * c_belt + len(live) * c_turn)
        # tunnels: each belt under what blocks its own line, the rest on the surface
        if not any(blocked(_cross(nx, ny, h, b)) for b in live):
            continue                    # nothing blocks a belt one tile ahead: a step comes first
        theirs = grid.spans[_axis(h)]
        first, last = {}, {}
        for j in range(1, 2 * max_ug + 2):
            row = [_cross(x + fx * j, y + fy * j, h, b) for b in live]
            if j >= 2 and first and not any(taken(t, b, h) for t, b in zip(row, live)):
                # each belt out just past what blocks it, or every belt that went under out on
                # row j together (its tunnel longer, the tiles under it saved)
                ways = []
                if any(last.get(b) == j - 1 for b in live):
                    ways.append(False)
                if any(last[b] != j - 1 for b in first):
                    ways.append(True)
                for stretch in ways:
                    cost, tun, exits = 0.0, [], 0
                    for b in live:
                        if b not in first:
                            cost += j * c_belt
                            continue
                        a, e = first[b] - 1, j if stretch else last[b] + 1
                        if e - a > max_ug or (a == 0 and ex >> b & 1) or any(
                                _cross(x + fx * i, y + fy * i, h, b) in theirs
                                for i in range(a, e + 1)):
                            break
                        cost += (max(a - 1, 0) + j - e) * c_belt + c_ug + \
                            c_far * max(0, e - a - _YELLOW_REACH)
                        tun.append((b, a, e))
                        if e == j:
                            exits |= 1 << b
                    else:
                        sx, sy = x + fx * j, y + fy * j
                        push((sx, sy, h, exits, settle(sx, sy, h, mask)), st,
                             ("tunnel", j, tuple(tun)), gs + cost)
            for t, b in zip(row, live):
                if blocked(t):
                    first.setdefault(b, j)
                    last[b] = j
            if any(last.get(b) == j and j + 1 - (first[b] - 1) > max_ug for b in live):
                break                   # a belt's tunnel would end beyond its reach
    if end is None:
        return {"fail": "no path for the ribbon"}
    # the path, start first
    chain = []
    s = end
    while s is not None:
        prev, move = parent[s]
        chain.append((s, move))
        s = prev
    chain.reverse()
    fan = chain[-1][0][0] == "fan"
    if fan:
        chain = chain[:-1]
    (x0, y0, h0, _e, _m), _mv = chain[0]
    lines = [[[_cross(x0, y0, h0, b), "belt"]] for b in range(n)]
    heads = [h0] * n
    ends = [("fixed",) if not chain[0][0][4] >> b & 1 else None for b in range(n)]
    counts = {"belts": 0, "undergrounds": 0, "turns": 0, "crossings": 0, "under": 0}
    tunnels = []
    for (prev, _pm), (st, move) in zip(chain, chain[1:]):
        x, y, h, _ex, mask = prev
        live = [b for b in range(n) if mask >> b & 1]
        if move[0] == "drop":
            b = move[1]
            ends[b] = ("loose", _cross(x + DX[h], y + DY[h], h, b))
            heads[b] = h
            continue
        if move[0] == "step":
            for b in live:
                lines[b].append([_cross(st[0], st[1], h, b), "belt"])
        elif move[0] == "turn":
            counts["turns"] += 1
            for b in live:
                bx, by = _cross(x, y, h, b)
                for dx, dy in turns[h, move[1], b]:
                    lines[b].append([(bx + dx, by + dy), "belt"])
        else:
            counts["crossings"] += 1
            _tag, j, tun = move
            under = {b: (a, e) for b, a, e in tun}
            counts["under"] += len(under)
            for b in live:
                a, e = under.get(b, (j + 1, j + 1))
                if a == 0:
                    lines[b][-1][1] = "in"
                for i in range(1, j + 1):
                    t = _cross(x + DX[h] * i, y + DY[h] * i, h, b)
                    if i == a:
                        lines[b].append([t, "in"])
                    elif i == e:
                        lines[b].append([t, "out"])
                    elif not a < i < e:
                        lines[b].append([t, "belt"])
                if b in under:
                    tunnels.append((b, _cross(x + DX[h] * a, y + DY[h] * a, h, b),
                                    _cross(x + DX[h] * e, y + DY[h] * e, h, b), e - a))
        for b in live:
            heads[b] = st[2]
            if not st[4] >> b & 1:
                ends[b] = ("fixed",)
    x, y, h, _ex, mask = chain[-1][0]
    for b in range(n):
        if mask >> b & 1:
            ends[b] = ("loose", _cross(x + DX[h], y + DY[h], h, b))
    for b in range(n):
        for _t, kind in lines[b]:
            if kind == "belt":
                counts["belts"] += 1
            else:
                counts["undergrounds"] += 1
    cost = g[end]
    return {"lines": lines, "heads": heads, "ends": ends, "start": (x0, y0, h0),
            "cost": round(cost, 3), "counts": counts, "tunnels": tunnels,
            "seconds": round(time.time() - t0, 2), "expanded": len(done)}
