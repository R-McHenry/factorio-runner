"""fkcore.maxflow: the max-flow of one net's laid carriers, one lane a run (DESIGN-M2M.md, 2.1 m2m).

``solve(ents, cat, supply, take)`` takes a net's carriers and its machines (fkp entities: belts,
undergrounds, splitters, inserters, the driver and consumer machines), what each driver puts on
(``supply``: {key: items/s}) and what each consumer takes (``take``), and answers with the most
the carriers move from all drivers to all consumers at once (``Flow.value``), against ``demand``
(what the drivers make, or what the consumers take where that is less).

The graph is the lane graph's (``fkcore.lanes``: underground pairing, inserter reach, a splitter's
halves), with the two lanes of a carrier made one: version 1 of the many-to-many router puts a
net on the left lane only, so a run carries one lane (yellow 7.5/s), a splitter two (one per
half, each half one lane), an inserter what it swings. A machine has no capacity of its own. Each
carrier is a node with its capacity (split in two, ``in`` and ``out``); an edge is one way items
move. ``unbounded``: the drivers, consumers and inserters without limit, so the value is what the
carriers alone pass (``Flow.lanes``: that over one lane, the runs at the narrowest cut).

The cut where it binds: the nodes reached from the source in the residual graph (``Flow.S``) and
those that reach the sink in it (``Flow.T``), each as (key, 0) for its ``in`` side and (key, 1)
for its ``out`` side; ``Flow.cut`` names the carriers (and splitter halves) at capacity between
the two sides. A splitter divides evenly, so a real network can carry less than this (the lab
measures the gap).

``lanes`` (2.1.21, the router on both lanes): the lane graph as it is, a node per lane. An
inserter drops onto one lane and picks from both, a straight, a turn, a tunnel and a splitter keep
the lanes, a side-load puts both onto the near lane; a lane carries one lane's worth, a
splitter's lane both halves'. A belt end that supplies or takes (a key with lanes and no node of
its own) does so on either lane. ``Flow.S`` and ``Flow.T`` still name carriers, (key, 0 | 1),
whichever of their lanes is reached; ``Flow.cut`` names lanes (``key:L``), ``through`` sums both.
Pure, stdlib only, deterministic.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from . import lanes as lanesmod

__all__ = ["Flow", "solve", "of_net", "rates_of"]

INF = float("inf")
_BIG = 1e9
_EPS = 1e-9
_SRC, _SNK = ("<source>", 1), ("<sink>", 0)


@dataclass
class Flow:
    value: float                   # items/s from every driver to every consumer at most
    demand: float                  # what the drivers make, or the consumers take where less
    lane: float                    # one lane, items/s (yellow 7.5)
    cut: list = field(default_factory=list)       # carrier keys at capacity on the cut
    S: set = field(default_factory=set)           # (key, 0 | 1) reached from the source
    T: set = field(default_factory=set)           # (key, 0 | 1) that reach the sink
    through: dict = field(default_factory=dict)   # key -> items/s through it
    lane_S: set = field(default_factory=set)      # with ``lanes``: (node id, 0 | 1) reached
    lane_T: set = field(default_factory=set)      # with ``lanes``: (node id, 0 | 1) that reach

    @property
    def short(self):
        return max(0.0, self.demand - self.value)

    @property
    def met(self):
        return self.value >= self.demand - 1e-6

    @property
    def lanes(self):
        """The value in lanes (a whole number of runs at the narrowest cut when unbounded)."""
        return self.value / self.lane if self.lane else 0.0


def _graph(ents, cat, unbounded, hand, lanes=False):
    """{node: capacity}, {(a, b): capacity} of the one-lane carrier graph (a node a key), or with
    ``lanes`` of the lane graph (a node a lane graph node id)."""
    lg = lanesmod.build(ents, cat, hand=hand)
    cap, edges = {}, {}
    for nid, n in lg.nodes.items():
        node = nid if lanes else n.key
        if n.kind in ("lane", "splitter"):
            cap[node] = n.cap
        elif n.kind == "inserter":
            cap[node] = _BIG if unbounded else min(n.cap, _BIG)
        else:
            cap[node] = _BIG
    for e in lg.edges():
        a, b = (e.src, e.dst) if lanes else (e.src[0], e.dst[0])
        if a == b:
            continue
        c = min(e.cap, _BIG)
        edges[(a, b)] = min(edges.get((a, b), _BIG), c)
    return lg, cap, edges


def solve(ents, cat, supply, take, demand=None, unbounded=False, hand=1, lane=None,
          lanes=False) -> Flow:
    """The max-flow (module docstring). ``supply`` / ``take``: {node key: items/s}; a key the
    entities don't hold counts for nothing. ``lanes``: on the lane graph as it is."""
    lg, cap, edges = _graph(ents, cat, unbounded, hand, lanes)
    if lane is None:
        lane = next((lanesmod.lane_rate(cat, n.name) for n in lg.nodes.values()
                     if n.kind == "lane"), 7.5)
    res = {}                                       # residual capacity
    adj = {}

    def link(u, v, c):
        if (u, v) not in res:
            adj.setdefault(u, []).append(v)
            adj.setdefault(v, []).append(u)
            res[(u, v)] = 0.0
            res.setdefault((v, u), 0.0)
        res[(u, v)] += c

    for k in sorted(cap):
        link((k, 0), (k, 1), cap[k])
    for (a, b) in sorted(edges):
        link((a, 1), (b, 0), edges[(a, b)])
    def nodes_of(k):
        """The graph nodes an end key stands for: itself, or with ``lanes`` its machine node or
        a belt end's two lanes."""
        if not lanes:
            return [k] if k in cap else []
        if (k, "") in cap:
            return [(k, "")]
        return [(k, ln) for ln in ("L", "R") if (k, ln) in cap]

    for k in sorted(supply, key=str):
        ns = nodes_of(k)
        r = _BIG if unbounded else min(supply[k], _BIG)
        if len(ns) == 1:
            link(_SRC, (ns[0], 0), r)
        elif ns:
            link(_SRC, (("<in>", k), 1), r)
            for n in ns:
                link((("<in>", k), 1), (n, 0), _BIG)
    for k in sorted(take, key=str):
        ns = nodes_of(k)
        r = _BIG if unbounded else min(take[k], _BIG)
        if len(ns) == 1:
            link((ns[0], 1), _SNK, r)
        elif ns:
            for n in ns:
                link((n, 1), (("<out>", k), 0), _BIG)
            link((("<out>", k), 0), _SNK, r)
    if demand is None:
        demand = min(sum(supply.values()), sum(take.values()))
    if unbounded:
        demand = INF
    flow = 0.0
    orig = dict(res)
    while True:                                    # Edmonds-Karp: shortest augmenting paths
        par = {_SRC: None}
        todo = deque([_SRC])
        while todo and _SNK not in par:
            u = todo.popleft()
            for v in adj.get(u, ()):
                if v not in par and res[(u, v)] > _EPS:
                    par[v] = u
                    todo.append(v)
        if _SNK not in par:
            break
        push, v = INF, _SNK
        while par[v] is not None:
            push = min(push, res[(par[v], v)])
            v = par[v]
        v = _SNK
        while par[v] is not None:
            res[(par[v], v)] -= push
            res[(v, par[v])] += push
            v = par[v]
        flow += push
        if flow >= _BIG / 2:
            break
    S = set(par) - {_SRC}
    back = {_SNK}
    todo = deque([_SNK])
    while todo:
        v = todo.popleft()
        for u in adj.get(v, ()):
            if u not in back and res.get((u, v), 0.0) > _EPS:
                back.add(u)
                todo.append(u)
    T = back - {_SNK}

    def key_of(n):
        return n[0] if lanes else n

    def name_of(n):
        return f"{n[0]}:{n[1]}" if lanes and n[1] else key_of(n)

    cut = []
    for n in sorted(cap, key=str):
        k = key_of(n)
        if (n, 0) in S and (n, 1) not in S and k in lg.graph.nodes \
                and lg.graph.nodes[k].kind in ("transport-belt", "underground-belt", "splitter",
                                               "inserter"):
            cut.append(name_of(n))
    for (a, b) in sorted(edges, key=str):
        if (a, 1) in S and (b, 0) not in S and edges[(a, b)] < _BIG / 2 and \
                name_of(b) not in cut:
            cut.append(f"{name_of(a)}>{name_of(b)}")
    through = {}
    for n in cap:
        k = key_of(n)
        through[k] = through.get(k, 0.0) + orig[((n, 0), (n, 1))] - res[((n, 0), (n, 1))]
    lane_S, lane_T = set(), set()
    if lanes:                          # the carriers, whichever of their lanes is reached
        lane_S = {(n, side) for n, side in S if n in cap}
        lane_T = {(n, side) for n, side in T if n in cap}
        S = {(key_of(n), side) for n, side in lane_S}
        T = {(key_of(n), side) for n, side in lane_T}
    value = min(flow, _BIG)
    return Flow(value if value < _BIG / 2 else INF, demand, lane, cut, S, T, through, lane_S,
                lane_T)


def rates_of(flat, net):
    """({driver key: items/s}, {consumer key: items/s}) of a net's machines: what each driver
    makes of the net's items, and what each consumer takes (its need, its rate, a whole lane at
    a port, else one fast inserter's worth: ``fkcore.route._flow``'s rule)."""
    fn = flat.nets[net]
    items = set(fn.items)
    supply, take = {}, {}
    for k, i in fn.drivers:
        m = flat.machines.get(k)
        if m is not None:
            supply[k] = supply.get(k, 0.0) + sum(r for it, r in m.makes.items()
                                                 if it == i or (i is None and it in items))
    for k, i in fn.consumers:
        m = flat.machines.get(k)
        if m is None:
            continue
        if i in m.needs:
            r = m.needs[i]
        elif m.drain is not None:
            r = m.drain
        elif m.extra.get("port"):
            r = INF
        else:
            r = lanesmod.inserter_rate("fast-inserter")
        take[k] = take.get(k, 0.0) + r
    return supply, take


def of_net(flat, lay, cat, net, unbounded=False, lanes=False) -> Flow:
    """The max-flow of ``net`` as laid in ``lay``: its carriers (``~net``) and its machines."""
    supply, take = rates_of(flat, net)
    ents = [e for e in lay.entities.values() if e.owner == f"~{net}"]
    ents += [lay.entities[k] for k in sorted(set(supply) | set(take)) if k in lay.entities]
    return solve(ents, cat, supply, take, unbounded=unbounded, lanes=lanes)
