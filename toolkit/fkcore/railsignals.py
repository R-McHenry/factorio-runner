"""Signals for a one-way rail network, from its rails (2.1.32).

Rory (11-07): "intersections need to be all chain signals and rail signals only at the exit on the
long segment". The lab's networks were signalled by hand-made captures cut into pieces, so a
station column's signals stood where the capture had them, wherever the next column now was.

`plan` takes the pieces (straight, half-diagonal and curved rails, as the game stores them) and the
train stops, and works out the network:

- joints: where two piece ends meet (the same point, facing opposite ways), from `RAIL_ENDS`,
  each piece's two ends measured on the lab;
- travel: one-way, spread from the stops (a stop faces the way its trains run) across every joint;
  a piece run both ways is a conflict, reported;
- junctions: joints where tracks fork or merge; segments: the track between two junctions.

and signals it: a chain signal at every junction's entrance (so inside an intersection there are
only chain signals), and a rail signal at the first joint of every segment at least ``long``
tiles long (the exit onto it), where a whole train fits. A signal on a tile a rail takes moves to
the joint's alternative spot, or is left out (reported).
"""
import math

from . import railtiles

#: 2.1.32: measured on the lab (2.1.24): each rail piece facing d (as the game stores it), its two
#: ends relative to its position: ((x, y, outward direction), out signal, in signal, alternative
#: out, alternative in), a signal as (x, y, direction) or None
RAIL_ENDS = {
    'straight-rail': {
        0: (((0.0, -1.0, 0), (1.5, -0.5, 8), (-1.5, -0.5, 0), None, None), ((0.0, 1.0, 8), (-1.5, 0.5, 0), (1.5, 0.5, 8), None, None)),
        2: (((-1.0, 1.0, 10), (-1.5, -0.5, 2), (0.5, 1.5, 10), None, None), ((1.0, -1.0, 2), (1.5, 0.5, 10), (-0.5, -1.5, 2), None, None)),
        4: (((1.0, 0.0, 4), (0.5, 1.5, 12), (0.5, -1.5, 4), None, None), ((-1.0, 0.0, 12), (-0.5, -1.5, 4), (-0.5, 1.5, 12), None, None)),
        6: (((-1.0, -1.0, 14), (0.5, -1.5, 6), (-1.5, 0.5, 14), None, None), ((1.0, 1.0, 6), (-0.5, 1.5, 14), (1.5, -0.5, 6), None, None)),
    },
    'half-diagonal-rail': {
        0: (((1.0, 2.0, 7), (-0.5, 1.5, 15), (1.5, 0.5, 7), None, (0.5, -0.5, 7)), ((-1.0, -2.0, 15), (0.5, -1.5, 7), (-1.5, -0.5, 15), None, (-0.5, 0.5, 15))),
        2: (((-1.0, 2.0, 9), (-1.5, 0.5, 1), (0.5, 1.5, 9), (-0.5, -0.5, 1), None), ((1.0, -2.0, 1), (1.5, -0.5, 9), (-0.5, -1.5, 1), (0.5, 0.5, 9), None)),
        4: (((-2.0, 1.0, 11), (-1.5, -0.5, 3), (-0.5, 1.5, 11), None, (0.5, 0.5, 11)), ((2.0, -1.0, 3), (1.5, 0.5, 11), (0.5, -1.5, 3), None, (-0.5, -0.5, 3))),
        6: (((-2.0, -1.0, 13), (-0.5, -1.5, 5), (-1.5, 0.5, 13), (0.5, -0.5, 5), None), ((2.0, 1.0, 5), (0.5, 1.5, 13), (1.5, -0.5, 5), (-0.5, 0.5, 13), None)),
    },
    'curved-rail-a': {
        0: (((0.0, 2.0, 8), (-1.5, 1.5, 0), (1.5, 1.5, 8), None, None), ((-1.0, -3.0, 15), (0.5, -2.5, 7), (-1.5, -1.5, 15), None, (-0.5, -0.5, 15))),
        2: (((0.0, 2.0, 8), (-1.5, 1.5, 0), (1.5, 1.5, 8), None, None), ((1.0, -3.0, 1), (1.5, -1.5, 9), (-0.5, -2.5, 1), (0.5, -0.5, 9), None)),
        4: (((-2.0, 0.0, 12), (-1.5, -1.5, 4), (-1.5, 1.5, 12), None, None), ((3.0, -1.0, 3), (2.5, 0.5, 11), (1.5, -1.5, 3), None, (0.5, -0.5, 3))),
        6: (((-2.0, 0.0, 12), (-1.5, -1.5, 4), (-1.5, 1.5, 12), None, None), ((3.0, 1.0, 5), (1.5, 1.5, 13), (2.5, -0.5, 5), (0.5, 0.5, 13), None)),
        8: (((0.0, -2.0, 0), (1.5, -1.5, 8), (-1.5, -1.5, 0), None, None), ((1.0, 3.0, 7), (-0.5, 2.5, 15), (1.5, 1.5, 7), None, (0.5, 0.5, 7))),
        10: (((0.0, -2.0, 0), (1.5, -1.5, 8), (-1.5, -1.5, 0), None, None), ((-1.0, 3.0, 9), (-1.5, 1.5, 1), (0.5, 2.5, 9), (-0.5, 0.5, 1), None)),
        12: (((2.0, 0.0, 4), (1.5, 1.5, 12), (1.5, -1.5, 4), None, None), ((-3.0, 1.0, 11), (-2.5, -0.5, 3), (-1.5, 1.5, 11), None, (-0.5, 0.5, 11))),
        14: (((2.0, 0.0, 4), (1.5, 1.5, 12), (1.5, -1.5, 4), None, None), ((-3.0, -1.0, 13), (-1.5, -1.5, 5), (-2.5, 0.5, 13), (-0.5, -0.5, 5), None)),
    },
    'curved-rail-b': {
        0: (((1.0, 2.0, 7), (-0.5, 1.5, 15), (1.5, 0.5, 7), None, (0.5, -0.5, 7)), ((-2.0, -2.0, 14), (-0.5, -2.5, 6), (-2.5, -0.5, 14), None, None)),
        2: (((-1.0, 2.0, 9), (-1.5, 0.5, 1), (0.5, 1.5, 9), (-0.5, -0.5, 1), None), ((2.0, -2.0, 2), (2.5, -0.5, 10), (0.5, -2.5, 2), None, None)),
        4: (((-2.0, 1.0, 11), (-1.5, -0.5, 3), (-0.5, 1.5, 11), None, (0.5, 0.5, 11)), ((2.0, -2.0, 2), (2.5, -0.5, 10), (0.5, -2.5, 2), None, None)),
        6: (((-2.0, -1.0, 13), (-0.5, -1.5, 5), (-1.5, 0.5, 13), (0.5, -0.5, 5), None), ((2.0, 2.0, 6), (0.5, 2.5, 14), (2.5, 0.5, 6), None, None)),
        8: (((-1.0, -2.0, 15), (0.5, -1.5, 7), (-1.5, -0.5, 15), None, (-0.5, 0.5, 15)), ((2.0, 2.0, 6), (0.5, 2.5, 14), (2.5, 0.5, 6), None, None)),
        10: (((1.0, -2.0, 1), (1.5, -0.5, 9), (-0.5, -1.5, 1), (0.5, 0.5, 9), None), ((-2.0, 2.0, 10), (-2.5, 0.5, 2), (-0.5, 2.5, 10), None, None)),
        12: (((2.0, -1.0, 3), (1.5, 0.5, 11), (0.5, -1.5, 3), None, (-0.5, -0.5, 3)), ((-2.0, 2.0, 10), (-2.5, 0.5, 2), (-0.5, 2.5, 10), None, None)),
        14: (((2.0, 1.0, 5), (0.5, 1.5, 13), (1.5, -0.5, 5), (-0.5, 0.5, 13), None), ((-2.0, -2.0, 14), (-0.5, -2.5, 6), (-2.5, -0.5, 14), None, None)),
    },
}

RAILS = frozenset(RAIL_ENDS)
#: N, E, S, W as 16-way directions -> unit vectors (y down)
_VEC = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}


def _key(name, d):
    d = int(d or 0) % 16
    d -= d % 2
    return d if d in RAIL_ENDS[name] else d % 8


def piece_ends(name, x, y, d):
    """[(end point (x, y, outward dir), out signal, in signal, alt out, alt in)] in world terms."""
    out = []
    for end in RAIL_ENDS[name][_key(name, d)]:
        out.append(tuple(None if loc is None else (x + loc[0], y + loc[1], loc[2]) for loc in end))
    return out


class Network:
    """A one-way rail network: ``pieces`` [(name, x, y, d)], ``stops`` [(x, y, d)]."""

    def __init__(self, pieces, stops=()):
        self.pieces = [p for p in pieces if p[0] in RAILS]
        self.ends = [piece_ends(*p) for p in self.pieces]
        self.joints = {}                    # (x, y, axis) -> {side: [(piece, end)]}
        for i, ends in enumerate(self.ends):
            for k, (pt, *_s) in enumerate(ends):
                j = (pt[0], pt[1], pt[2] % 8)
                self.joints.setdefault(j, {0: [], 1: []})[pt[2] // 8].append((i, k))
        self.exit = [None] * len(self.pieces)   # the end trains leave a piece by
        self.conflicts = []
        self._orient([s for s in (self._stop_seed(*st) for st in stops) if s is not None])

    def joint_of(self, i, k):
        pt = self.ends[i][k][0]
        return (pt[0], pt[1], pt[2] % 8), pt[2] // 8

    def _stop_seed(self, x, y, d):
        """A stop's rail and the end its trains leave by (a stop stands 2 right of its rail)."""
        d = int(d or 0) % 16
        if d not in _VEC:
            return None
        vx, vy = _VEC[d]
        rx, ry = x + vy * 2, y - vx * 2          # right of travel (vx, vy) is (-vy, vx)
        for i, (name, px, py, _pd) in enumerate(self.pieces):
            if name == "straight-rail" and abs(px - rx) <= 1 and abs(py - ry) <= 1:
                for k, (pt, *_s) in enumerate(self.ends[i]):
                    if pt[2] == d:
                        return i, k
        return None

    def _orient(self, seeds):
        todo = list(seeds)
        while todo:
            i, k = todo.pop()
            if self.exit[i] is not None:
                if self.exit[i] != k:
                    self.conflicts.append(self.pieces[i])
                continue
            self.exit[i] = k
            # at the exit: the other side's pieces enter (they leave by their far end), this
            # side's merge into it (they leave here too); at the entry the other way about
            for kk, leaving in ((k, True), (1 - k, False)):
                j, side = self.joint_of(i, kk)
                for pj, pk in self.joints[j][1 - side]:
                    todo.append((pj, 1 - pk) if leaving else (pj, pk))
                for pj, pk in self.joints[j][side]:
                    if pj != i:
                        todo.append((pj, pk) if leaving else (pj, 1 - pk))

    def flow(self, j):
        """-> (pieces leaving into joint j, pieces entering from it) as [(piece, end)]."""
        ins, outs = [], []
        for side in (0, 1):
            for i, k in self.joints[j][side]:
                if self.exit[i] is None:
                    continue
                (ins if self.exit[i] == k else outs).append((i, k))
        return ins, outs

    def is_junction(self, j):
        ins, outs = self.flow(j)
        return len(ins) > 1 or len(outs) > 1

    def length(self, i):
        (a, *_r), (b, *_s) = self.ends[i]
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def segments(self):
        """[(start joint, [pass-through joints], end joint, length, [pieces])]: track from a
        junction to the next (a junction-free loop is left out); the pieces in travel order."""
        out = []
        for j in self.joints:
            if not self.is_junction(j) and self.flow(j)[0]:
                continue                       # (a track's start begins one too)
            for i, k in self.flow(j)[1]:
                mids, length, run = [], 0.0, []
                while True:
                    length += self.length(i)
                    run.append(i)
                    nj, _side = self.joint_of(i, self.exit[i])
                    nxt = self.flow(nj)[1]
                    if self.is_junction(nj) or len(nxt) != 1:
                        out.append((j, mids, nj, length, run))
                        break
                    mids.append(nj)
                    i, k = nxt[0]
                    if nj == j or len(mids) > 100000:
                        break
        return out

    def tiles(self, i):
        if not hasattr(self, "_tiles"):
            self._tiles = {}
        if i not in self._tiles:
            name, x, y, d = self.pieces[i]
            self._tiles[i] = railtiles.tiles(name, x, y, d) or set()
        return self._tiles[i]

    def branch(self, i, k, up, reach=12):
        """The pieces along a branch from joint end (i, k), away from its junction: upstream
        (``up``, against travel) or downstream, up to ``reach`` pieces or the next junction."""
        out = []
        while len(out) < reach:
            out.append(i)
            jj, _side = self.joint_of(i, (1 - self.exit[i]) if up else self.exit[i])
            if self.is_junction(jj):
                break
            ins, outs = self.flow(jj)
            nxt = ins if up else outs
            if len(nxt) != 1:
                break
            i, k = nxt[0]
        return out

    def contested(self, j):
        """The pieces near junction j that share a tile with another of its branches: inside
        the intersection, where a train waiting would foul another track (2.1.32: a signal
        there gets the game's "too close to a merge" warning)."""
        ins, outs = self.flow(j)
        branches = [self.branch(i, k, True) for i, k in ins]
        branches += [self.branch(i, k, False) for i, k in outs]
        held = [set().union(*(self.tiles(p) for p in b)) if b else set() for b in branches]
        out = set()
        for n, b in enumerate(branches):
            others = set().union(*(h for m, h in enumerate(held) if m != n)) if len(held) > 1                 else set()
            for p in b:
                if self.tiles(p) & others:
                    out.add(p)
        return out

    def signal_spots(self, j):
        """The spots a signal for travel through joint j can take: [main, alternative]."""
        ins, _outs = self.flow(j)
        for i, k in ins:
            _pt, sig, _in, alt, _ain = self.ends[i][k]
            return [s for s in (sig, alt) if s is not None]
        return []


def plan(pieces, stops=(), long=42.0, taken=None, gap=8.0, pitch=25.0):
    """-> (signals [{name, x, y, d}], notes). Every segment between two junctions gets two
    signals, outside both intersections: an exit signal just past the last piece of the
    intersection it leaves (a rail signal when a train fits between it and the next, else a chain
    signal) and a chain signal just before the first piece of the intersection it enters. An
    intersection's pieces are those its branches overlap (``Network.contested``); a segment
    wholly inside (two junctions too close) gets none, so the two are one intersection.
    ``taken``: tiles signals may not stand on besides the rails' (the other entities'); no signal
    stands on a rail's tiles (fkcore/railtiles.py: fk gives each tile one owner, and a measured
    spot beside a diagonal is on its own piece's tiles). A blocked signal moves along its
    segment, away from the junction. Past its exit signal a segment is cut into blocks of at
    least ``pitch`` tiles by rail signals (a 1-2 train and a little), so as many trains as fit
    can stand on it."""
    net = Network(pieces, stops)
    notes = []
    taken = set(taken or ())
    railed = {}                                # tile -> the pieces on it
    for i in range(len(net.pieces)):
        for t in net.tiles(i):
            railed.setdefault(t, set()).add(i)
    unoriented = sum(1 for e in net.exit if e is None)
    if unoriented:
        notes.append(f"{unoriented} of {len(net.pieces)} rail pieces no stop's trains reach: "
                     "not signalled")
    if net.conflicts:
        notes.append(f"{len(net.conflicts)} pieces run both ways, first {net.conflicts[0]}")
    segs = net.segments()
    inside = {}
    signals, used = [], set()
    left = merged = short = middles = 0

    def put(j, name, share=False):
        for x, y, d in net.signal_spots(j):
            t = (math.floor(x), math.floor(y))
            if share and t in used and any(s["x"] == x and s["y"] == y and s["d"] == d for s in signals):
                return True                    # the same spot (two branches into one merge)
            if t not in taken and t not in used and t not in railed:
                used.add(t)
                signals.append({"name": name, "x": x, "y": y, "d": d})
                return True
        return False

    def busy(j):
        if j not in inside:
            inside[j] = net.contested(j) if net.is_junction(j) else set()
        return inside[j]

    for start, mids, end, _length, run in segs:
        joints = [start] + mids + [end]        # run[t] runs from joints[t] to joints[t + 1]
        a = 0
        for t, p in enumerate(run):
            if p in busy(start):
                a = t + 1
        b = len(run)
        for t, p in enumerate(run):
            if p in busy(end):
                b = t
                break
        if not net.is_junction(end):
            b = len(run)
        if a >= b:
            merged += 1                        # wholly inside: the two are one intersection
            continue
        ein = None
        if net.is_junction(end):
            for t in range(b, a, -1):          # the entrance, then back along the segment
                if put(joints[t], "rail-chain-signal", share=True):
                    ein = t
                    break
            else:
                left += 1
        if not net.is_junction(start):
            continue                           # a track's start: no intersection to leave
        stop = ein if ein is not None else b
        xt = None
        for t in range(a, stop):
            room = sum(net.length(p) for p in run[t:stop])
            if room < gap:                     # moved too near the entrance: none
                short += 1
                break
            if t == 0:
                continue                       # a fork's own joint is its entrance, and a
                #                                merge's: rails overlap just before it
            if put(joints[t], "rail-signal" if room >= long else "rail-chain-signal"):
                xt = t
                break
        else:
            left += 1
        # 2.1.32 (Rory, 11-07: "a chain signal in the middle of a segment so that two trains can
        # occupy it"), then (11-07: "any straight segment ultimately should be broken up into
        # chunks with rail signals that let more trains occupy the rail"; his lab test: two rail
        # signals 26 tiles apart): rail signals every ``pitch`` tiles or more past the exit, the
        # last block before the entrance a ``pitch`` too. Rail, not chain: a straight one-way
        # run can't lock, and a chain signal reads on to the next one
        if xt is not None and ein is not None:
            along = [0.0]
            for p in run[xt:stop]:
                along.append(along[-1] + net.length(p))
            last = 0.0
            for i in range(1, len(along) - 1):
                if along[i] - last < pitch or along[-1] - along[i] < pitch:
                    continue
                if put(joints[xt + i], "rail-signal"):
                    middles += 1
                    last = along[i]
    chains = sum(1 for s in signals if s["name"] == "rail-chain-signal")
    notes.append(f"{len(segs)} segments, {sum(1 for s in segs if s[3] >= long)} of them "
                 f"{long:g}+ tiles; {chains} chain signals, {len(signals) - chains} rail signals"
                 + (f"; {middles} rail signals cutting segments into {pitch:g}-tile blocks"
                    if middles else "")
                 + (f"; {merged} segments inside an intersection" if merged else "")
                 + (f"; {short} too short for an exit signal" if short else "")
                 + (f"; {left} left out (no free spot)" if left else ""))
    return signals, notes
