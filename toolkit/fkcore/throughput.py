"""fkcore.throughput: the V2 flow solver over a lane graph (CORE-V2.md "Flow", Rory's approach).

Per item, sources put items on (``supply``), sinks take them off (``demand``), and every node of
the lane graph (``fkcore.lanes``) passes at most its capacity. The rules are the game's, as
measured (docs/LAB-LANES-20261020.md):

- **belt order**: a lane serves the inserters picking from it before what flows on, so along a
  lane the first machines are served first;
- **splitters** split evenly, and an output that can't take its half leaves the rest to the other;
  one with an output priority fills its preferred output first, and one with an input priority
  takes from its preferred input first (``fkcore.lanes``: Rory's bus staircases);
- **admission**: a node offered more than it can take serves its inputs by rank: a source at the
  node first, then what flows on from behind, then side-loads in the order they enter the lane,
  then drops; evenly within a rank;
- **back-pressure**: what a node does not admit backs up; a splitter above it sends that part the
  other way, a source behind it is blocked.

The solver (Rory): a forward pass in topological order distributes the flow by those rules, each
output capped by what it accepted in the round before (nothing, at first: the even split along the
topology); a backward pass then works out what every node accepts (its own demand plus what its
outputs accept, at most its capacity) and, per input, what it would admit from that input given
what the others offer. The two repeat until the flows stop changing. ``solve`` runs it twice:
without capacities (the **required** flows) and with them (the **flows**).

Several items on one node share its capacity: where they meet, by the rank of their inputs (so a
side-load's upstream lane goes first, as measured), and down a lane in the shares they met with.
A tighter spot further down cuts them in those shares, where the game would hold back the later
one first: a limit of this first version, until a joint solve of all items.

Machines (``Machine``: a furnace takes ore and coal and makes plates) couple the items. As in V1's
rate model, each side of a machine is judged against the other side's potential: it asks for its
inputs as fast as its outputs let it run and offers its products as fast as its inputs let it run,
from everything at full speed until both hold; it runs at the lower of the two. An endpoint served
in full tries a margin more the next round, and margins and steps halve where an endpoint swings,
so one that swings between two machines settles where they meet; ``Result.settled`` says whether
the coupling did within ``outer`` rounds. Pure, stdlib only.

A belt ring (a net's belts that loop back on themselves) is entered where it is fed, and the
required flows don't go round it: with no capacity, what goes round came back as more every round
(2.0.73, live 10-30: a coal ring made each coal solve take all 400 rounds, and ``fk build`` sat 10+
minutes in report_rates). The coupling also stops after ``BUDGET`` seconds, unsettled, which the
report says (``settled``).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .lanes import INF

__all__ = ["Machine", "Result", "solve", "report", "BIG"]

EPS = 1e-9
#: an unbounded source (supply INF) puts this much on, so the carriers decide; a required flow of
#: half of it or more is reported as unbounded
BIG = 1e6
#: the machine coupling: a grant's step grows by this while it keeps its way (halves when it
#: turns); a served endpoint's margin grows by this while it stays served (halves when that changes)
STEP_GROW = 1.2
MARGIN_GROW = 2.0
#: seconds the machine coupling may take before it stops, unsettled
BUDGET = 60.0


@dataclass
class Machine:
    needs: dict                    # item -> items/s at full speed
    makes: dict = field(default_factory=dict)


@dataclass
class Result:
    flow: dict                     # node id -> {item: items/s admitted into it}
    edge_flow: dict                # (src, dst) -> {item: items/s}
    required: dict                 # node id -> {item: items/s} with no capacities
    delivered: dict                # node id -> {item: items/s it consumed}
    supplied: dict                 # node id -> {item: items/s that left it}
    demand: dict                   # node id -> {item: items/s it asked}
    supply: dict                   # node id -> {item: items/s it offered}
    util: dict                     # machine node id -> utilization 0..1
    cap: dict                      # node id -> capacity
    rounds: int = 0                # the most rounds one item's flow took (the inner solve)
    residual: float = 0.0
    required_edge: dict = None     # (src, dst) -> {item: items/s} with no capacities
    edge_cap: dict = None          # (src, dst) -> capacity, the edges that have one
    outer: int = 0                 # rounds of the machine coupling
    settled: bool = True           # the coupling settled within its rounds
    moved: float = 0.0             # how far a grant moved in the last of them
    grant: dict = None             # (machine, "in" | "out", item) -> its grant, 0..1

    def total(self, nid, table=None):
        return sum((table or self.flow).get(nid, {}).values())

    def _capped(self, nid):
        return [k for k in (self.edge_cap or {}) if nid in k]

    def load(self, nid):
        """Flow over capacity: the node's, or the fullest of its capped edges (a splitter half)."""
        c = self.cap.get(nid, INF)
        v = 0.0 if c in (0, INF) else self.total(nid) / c
        for k in self._capped(nid):
            v = max(v, sum(self.edge_flow.get(k, {}).values()) / self.edge_cap[k])
        return v

    def required_ratio(self, nid):
        c = self.cap.get(nid, INF)
        v = 0.0 if c in (0, INF) else self.total(nid, self.required) / c
        for k in self._capped(nid):
            v = max(v, sum((self.required_edge or {}).get(k, {}).values()) / self.edge_cap[k])
        return v

    def binds(self, nid):
        """At capacity while more was required through it."""
        return self.load(nid) >= 1 - 1e-6 and self.required_ratio(nid) > 1 + 1e-6

    def limits(self, nid):
        """[(side, item)] of a machine's endpoints that hold it at its utilization: an input
        it is starved of ("in"), an output that backs up ("out")."""
        u = self.util.get(nid)
        if u is None or u >= 1 - 1e-6:
            return []
        return sorted((s, i) for (m, s, i), g in (self.grant or {}).items()
                      if m == nid and g <= u + 1e-6)

    def starved(self):
        """{node: {item: (got, asked)}} of every sink that got less than it asked."""
        out = {}
        for nid, items in self.demand.items():
            for item, want in items.items():
                if want == INF or want <= EPS:
                    continue
                got = self.delivered.get(nid, {}).get(item, 0.0)
                if got < want - 1e-6:
                    out.setdefault(nid, {})[item] = (got, want)
        return out


# -- helpers --------------------------------------------------------------------------------------
def _fill(amount, caps):
    """Water-fill ``amount`` over ``caps`` with equal levels: -> allocations (sum <= amount)."""
    n = len(caps)
    out = [0.0] * n
    if amount <= EPS or n == 0:
        return out
    order = sorted(range(n), key=lambda i: caps[i])
    left, rest = amount, n
    for i in order:
        share = left / rest
        take = min(caps[i], share)
        out[i] = take
        left -= take
        rest -= 1
    return out


def _admit(take, groups):
    """``groups``: [(rank, [(key, offer)])]; serve by rank, evenly within one. -> {key: admitted}."""
    out, left = {}, take
    for _rank, rows in sorted(groups, key=lambda g: g[0]):
        offers = [o for _k, o in rows]
        if sum(offers) <= left + EPS:
            for k, o in rows:
                out[k] = o
            left = max(0.0, left - sum(offers))
            continue
        for (k, _o), a in zip(rows, _fill(left, offers)):
            out[k] = a
        left = 0.0
    return out


def _share(avail, others):
    """The most one input can get when it shares ``avail`` evenly with inputs offering ``others``."""
    if avail <= EPS:
        return 0.0
    others = sorted(others)
    rest, left = len(others) + 1, avail
    for o in others:
        level = left / rest
        if o >= level:
            return level
        left -= o
        rest -= 1
    return left


def _topo(lg):
    """The nodes in topological order. A ring is entered where flow comes into it (2.0.73): its
    first node is one fed from outside it, so only the edge that closes it runs backward."""
    indeg = {v: 0 for v in lg.nodes}
    for e in lg.edges():
        indeg[e.dst] += 1
    todo = sorted((v for v, d in indeg.items() if d == 0), key=str)
    order, seen, ringed = [], set(), None
    while True:
        while todo:
            v = todo.pop(0)
            if v in seen:
                continue
            seen.add(v)
            order.append(v)
            for e in lg.out.get(v, ()):
                indeg[e.dst] -= 1
                if indeg[e.dst] == 0:
                    todo.append(e.dst)
        if len(seen) == len(lg.nodes):
            break
        if ringed is None:
            ringed = _on_rings(lg)
        rest = [v for v in ringed if v not in seen]
        fed = [v for v in rest if any(e.src in seen for e in lg.inn.get(v, ()))]
        todo = [min(fed or rest, key=str)]                  # a ring: in where it is fed
    return order


def _on_rings(lg):
    """The nodes that lie on a ring (a strongly connected part of more than one node, or one that
    feeds itself): Tarjan's, without recursion."""
    index, low, stack, on, out = {}, {}, [], set(), set()
    n = 0
    for root in lg.nodes:
        if root in index:
            continue
        work = [(root, iter(lg.out.get(root, ())))]
        index[root] = low[root] = n
        n += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            for e in it:
                w = e.dst
                if w not in index:
                    index[w] = low[w] = n
                    n += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(lg.out.get(w, ()))))
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            else:
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    part = []
                    while True:
                        w = stack.pop()
                        on.discard(w)
                        part.append(w)
                        if w == v:
                            break
                    if len(part) > 1 or any(e.dst == v for e in lg.out.get(v, ())):
                        out.update(part)
    return out


# -- one item -------------------------------------------------------------------------------------
def _usable(lg, item, takes, gives):
    """The edges ``item`` can move along: every belt edge; an inserter's only toward a target
    that takes the item, and out of a machine only what it gives."""
    def accepts(v):
        n = lg.nodes[v]
        if n.kind == "machine":
            return item in takes.get(v, ())
        if n.kind == "inserter":
            return any(accepts(e.dst) for e in lg.out.get(v, ()))
        return True

    out = set()
    for e in lg.edges():
        src, dst = lg.nodes[e.src], lg.nodes[e.dst]
        if src.kind == "machine" and item not in gives.get(e.src, ()):
            continue
        if not accepts(e.dst) and dst.kind in ("machine", "inserter"):
            continue
        out.add((e.src, e.dst))
    return out


def _prepare(lg, order, item, takes, gives, sources, sinks):
    """An item's part of the graph, once per solve: its nodes in topological order (only what
    lies between its sources and the sinks that take it), and per node its inputs and its outputs
    (the inserters picking from it first, then a splitter's preferred output, then the rest)."""
    usable = _usable(lg, item, takes, gives)
    keep = _reach(usable, sources) & _reach(usable, sinks, forward=False) | set(sources) | \
        set(sinks)
    ins, outs = {}, {}
    for e in lg.edges():
        if (e.src, e.dst) in usable and e.src in keep and e.dst in keep:
            outs.setdefault(e.src, []).append(e)
            ins.setdefault(e.dst, []).append(e)
    split = {}
    for v, rows in outs.items():
        taps = [e for e in rows if e.via == "pickup" and lg.nodes[v].kind != "machine"]
        first = [e for e in rows if e.first and e not in taps]
        split[v] = (taps, first, [e for e in rows if e not in taps and e not in first])
    mine = [v for v in order if v in keep]
    pos = {v: i for i, v in enumerate(mine)}
    back = {(e.src, e.dst) for rows in outs.values() for e in rows if pos[e.dst] <= pos[e.src]}
    return mine, ins, outs, split, back


def _solve_item(prep, sup, dem, cap, ecap, rounds, tol, prio=True, start=None, state=None):
    """-> (admitted into each node, flow on each edge, consumed per node, left per node,
    rounds, residual). ``start``: the (offers, acceptances) another solve of this item ended
    on, to begin from (2.1.27: the machine coupling solves every item again each outer round,
    from little changed); it runs two rounds at least, so a start that already fits the old
    supply and demand cannot stop it at once. ``state``: a dict that gets the ones this ends on.
    Without ``prio`` a splitter's preferred output is one of its two again:
    the required flows (no capacities) spread evenly, since a priority only decides where items
    go when the preferred side is full (Rory's bus staircases would heap a whole load on its far
    belt, and that belt would read as binding while the others stand empty). Nor do the required
    flows go round a ring (``back``: the edges that close one): with no capacity, what goes round
    would come back as more every round and never settle (2.0.73, live 10-30)."""
    order, ins, outs, split, back = prep
    if not prio:
        split = {v: (g[0], list(g[1]) + list(g[2])) for v, g in split.items()}
        if back:
            ins = {v: [e for e in rows if (e.src, e.dst) not in back] for v, rows in ins.items()}
            outs = {v: [e for e in rows if (e.src, e.dst) not in back]
                    for v, rows in outs.items()}
            split = {v: tuple([e for e in g if (e.src, e.dst) not in back] for g in groups)
                     for v, groups in split.items()}
    warm = start is not None
    if warm:
        offer, acc, accept = dict(start[0]), dict(start[1]), dict(start[2])
    else:
        acc, accept, offer = {}, {}, {}
    residual, done = INF, 0
    got = eat = left = {}
    for done in range(1, rounds + 1):
        new, got, eat, left = {}, {}, {}, {}
        for v in order:
            mine_in = ins.get(v, ())
            s = sup.get(v, 0.0)
            rows = {}
            for e in mine_in:
                o = new.get((e.src, v), offer.get((e.src, v), 0.0))
                rows.setdefault(e.rank, []).append(((e.src, v), o))
            total = s + sum(o for r in rows.values() for _k, o in r)
            limit = min(cap.get(v, INF), accept.get(v, INF))
            take = min(total, limit)
            adm = _admit(take, [(-1, [("supply", s)])] + list(rows.items()))
            g = sum(adm.values())
            got[v] = g
            e_ = min(g, dem.get(v, 0.0))
            eat[v] = e_
            rest = g - e_
            placed = 0.0
            for group in split.get(v, ((), ())):
                caps_ = [min(acc.get((e.src, e.dst), INF), ecap.get((e.src, e.dst), INF))
                         for e in group]
                for e, a in zip(group, _fill(rest - placed, caps_)):
                    new[(e.src, e.dst)] = a
                    placed += a
            left[v] = min(adm.get("supply", 0.0), placed)
        # backward: what each node accepts, and what it admits from each input
        for v in reversed(order):
            out_acc = sum(acc.get((e.src, e.dst), INF) for e in outs.get(v, ()))
            accept[v] = min(cap.get(v, INF), dem.get(v, 0.0) + out_acc)
            rows = {}
            for e in ins.get(v, ()):
                rows.setdefault(e.rank, []).append(e)
            avail = max(0.0, accept[v] - sup.get(v, 0.0))
            for rank in sorted(rows):
                group = rows[rank]
                offers = [new.get((e.src, v), 0.0) for e in group]
                for i, e in enumerate(group):
                    acc[(e.src, v)] = min(_share(avail, offers[:i] + offers[i + 1:]),
                                          ecap.get((e.src, v), INF))
                avail = max(0.0, avail - sum(offers))
        residual = max((abs(new.get(k, 0.0) - offer.get(k, 0.0)) for k in set(new) | set(offer)),
                       default=0.0)
        offer = new
        if residual < tol and (done > 1 or not warm):
            break
    if state is not None:
        state["end"] = (offer, acc, accept)
    return got, offer, eat, left, done, residual


# -- the solve ------------------------------------------------------------------------------------
def _reach(edges, starts, forward=True):
    adj = {}
    for a, b in edges:
        if forward:
            adj.setdefault(a, []).append(b)
        else:
            adj.setdefault(b, []).append(a)
    seen, todo = set(starts), list(starts)
    while todo:
        for w in adj.get(todo.pop(), ()):
            if w not in seen:
                seen.add(w)
                todo.append(w)
    return seen


def _solve_all(lg, preps, supply, demand, caps_on, shares, rounds, tol, memo=None):
    """Every item's flows. ``memo``: {(caps_on, item): (inputs, outcome)} from the rounds
    before; an item whose sources, sinks and capacities did not change is not solved again."""
    flow, edge, ate, left = {}, {}, {}, {}
    worst, most = 0.0, 0
    for item, prep in preps.items():
        sup = {v: min(t[item], BIG) for v, t in supply.items() if item in t and t[item] > EPS}
        dem = {v: t[item] for v, t in demand.items() if item in t and t[item] > EPS}
        if not sup:
            continue
        cap, ecap = {}, {}
        if caps_on:
            cap = {v: lg.nodes[v].cap * shares.get(v, {}).get(item, 1.0) for v in prep[0]
                   if lg.nodes[v].cap != INF}
            for rows in prep[2].values():
                for e in rows:
                    if e.cap != INF:
                        at = e.src if lg.nodes[e.src].kind == "splitter" else e.dst
                        ecap[(e.src, e.dst)] = e.cap * shares.get(at, {}).get(item, 1.0)
        key, inputs = (caps_on, item), (sup, dem, cap, ecap)
        if memo is not None and key in memo and memo[key][0] == inputs:
            got, off, eat, lft, n, res = memo[key][1]
        else:
            state = {}
            start = memo[key][2] if memo is not None and key in memo else None
            got, off, eat, lft, n, res = _solve_item(prep, sup, dem, cap, ecap, rounds, tol,
                                                     prio=caps_on, start=start, state=state)
            if memo is not None:
                memo[key] = (inputs, (got, off, eat, lft, n, res), state["end"])
        for v, x in got.items():
            if x > EPS:
                flow.setdefault(v, {})[item] = x
        for k, x in off.items():
            if x > EPS:
                edge.setdefault(k, {})[item] = x
        for v, x in eat.items():
            if x > EPS:
                ate.setdefault(v, {})[item] = x
        for v, x in lft.items():
            if x > EPS:
                left.setdefault(v, {})[item] = x
        worst, most = max(worst, res), max(most, n)
    return flow, edge, ate, left, most, worst


def solve(lg, supply=None, demand=None, machines=None, rounds=400, tol=1e-9, outer=80,
          outer_tol=1e-4, budget=None):
    """Flows over ``lg`` (``fkcore.lanes.LaneGraph``). ``supply`` / ``demand``: {node id: {item:
    items/s}} (a demand of INF takes everything); ``machines``: {node id: Machine}, whose needs
    and products join the demand and supply at their utilization. -> Result."""
    supply = {v: dict(t) for v, t in (supply or {}).items()}
    demand = {v: dict(t) for v, t in (demand or {}).items()}
    machines = dict(machines or {})
    order = _topo(lg)
    takes = {v: set(t) for v, t in demand.items()}
    gives = {v: set(t) for v, t in supply.items()}
    for m, mach in machines.items():
        takes.setdefault(m, set()).update(mach.needs)
        gives.setdefault(m, set()).update(mach.makes)
    sources, sinks = {}, {}
    for v, items in gives.items():
        for item in items:
            sources.setdefault(item, []).append(v)
    for v, items in takes.items():
        for item in items:
            sinks.setdefault(item, []).append(v)
    preps = {item: _prepare(lg, order, item, takes, gives, sources[item], sinks.get(item, []))
             for item in sorted(sources)}
    # One grant per endpoint of a machine, as in V1's rate model: it asks for an input as fast as
    # everything else (its other inputs, its outputs) lets it run, and offers a product as fast
    # as everything else lets it. An endpoint short of what it asked is granted what it got; one
    # that got all it asked (or rid all it offered) is granted that and a margin more, so the
    # machine tries a little faster the next round. From everything at full speed (margin 1).
    # The margin halves each time the endpoint turns from served to short or back, and grows
    # while it stays served; each grant moves toward its target by its own step, halved each
    # time it turns and grown while it keeps its way. So a grant that swings between two
    # machines (one asks less, the other gets more) settles where they meet.
    grant = {(m, side, item): 1.0 for m, mach in machines.items()
             for side, rows in (("in", mach.needs), ("out", mach.makes)) for item in rows}
    step = {g: 1.0 for g in grant}
    last = {g: 0.0 for g in grant}
    margin = {g: 1.0 for g in grant}
    served = {g: None for g in grant}

    def potential(m, skip):
        mach = machines[m]
        v = 1.0
        for side, rows in (("in", mach.needs), ("out", mach.makes)):
            for item in rows:
                if (m, side, item) != skip:
                    v = min(v, grant[(m, side, item)])
        return v

    done, settled, moved, most, memo = 0, not machines, 0.0, 0, {}
    until = time.monotonic() + (BUDGET if budget is None else budget)
    for done in range(1, outer + 1):
        sup = {v: dict(t) for v, t in supply.items()}
        dem = {v: dict(t) for v, t in demand.items()}
        asked, offered = {}, {}
        for m, mach in machines.items():
            for item, r in mach.needs.items():
                asked[(m, item)] = r * potential(m, (m, "in", item))
                dem.setdefault(m, {})[item] = dem.get(m, {}).get(item, 0.0) + asked[(m, item)]
            for item, r in mach.makes.items():
                offered[(m, item)] = r * potential(m, (m, "out", item))
                sup.setdefault(m, {})[item] = sup.get(m, {}).get(item, 0.0) + offered[(m, item)]
        req, req_edge, _a, _l, n0, _r = _solve_all(lg, preps, sup, dem, False, {}, rounds, tol,
                                                   memo)
        shares = _shares(lg, order, req, req_edge, sup, dem)
        flow, edge, ate, left, n, res = _solve_all(lg, preps, sup, dem, True, shares, rounds, tol,
                                                   memo)
        most = max(most, n0, n)
        if not machines:
            break
        moved = 0.0
        for m, mach in machines.items():
            for side, rows, table, want in (("in", mach.needs, ate, asked),
                                            ("out", mach.makes, left, offered)):
                for item, r in rows.items():
                    got = table.get(m, {}).get(item, 0.0)
                    g = (m, side, item)
                    if r <= EPS:
                        new = 1.0
                    elif got >= want[(m, item)] - 1e-9:
                        if served[g] is False:
                            margin[g] /= 2
                        elif served[g]:
                            margin[g] = min(1.0, margin[g] * MARGIN_GROW)
                        served[g] = True
                        new = min(1.0, want[(m, item)] / r + margin[g])
                    else:
                        if served[g]:
                            margin[g] /= 2
                        served[g] = False
                        new = got / r
                    d = new - grant[g]
                    if abs(d) <= EPS:
                        continue
                    if d * last[g] < 0:
                        step[g] /= 2
                    elif d * last[g] > 0:
                        step[g] = min(1.0, step[g] * STEP_GROW)
                    last[g] = d
                    grant[g] = min(1.0, max(0.0, grant[g] + d * step[g]))
                    moved = max(moved, abs(d * step[g]))
        if moved < outer_tol:
            settled = True
            break
        if time.monotonic() > until:
            break
    util = {m: max(0.0, min([1.0] + [grant[(m, s, i)] for s, rows in
                                     (("in", mach.needs), ("out", mach.makes)) for i in rows]))
            for m, mach in machines.items()}
    caps = {v: node.cap for v, node in lg.nodes.items()}
    ecaps = {(e.src, e.dst): e.cap for e in lg.edges() if e.cap != INF}
    return Result(flow, edge, req, ate, left, dem, sup, util, caps, most, res, req_edge, ecaps,
                  done, settled, moved, dict(grant))


def _shares(lg, order, req, req_edge, sup, dem):
    """{node: {item: share of its capacity}} where several items cross a node. Where they meet (a
    node with several inputs, or a source of its own), each gets what the node would admit of the
    required flows by rank (a source first, what flows on, then side-loads in the order they enter,
    then drops; evenly within a rank) within what the node can pass on: its capacity, or less when
    what lies after it carries less. Down a lane, a node fed by one input keeps the shares of the
    node before it. A limit of this first version: the joint solve of several items is next."""
    can = {}
    for v in reversed(order):
        d = sum(dem.get(v, {}).values())
        can[v] = min(lg.nodes[v].cap, d + sum(can.get(e.dst, INF) for e in lg.out.get(v, ())))
    out = {}
    for v in order:
        items = req.get(v, {})
        if len(items) < 2 or lg.nodes[v].cap == INF:    # a machine: its items don't share
            continue
        ins = [e for e in lg.inn.get(v, ()) if req_edge.get((e.src, v))]
        up = out.get(ins[0].src) if len(ins) == 1 and not sup.get(v) else None
        if up:
            got = {i: up.get(i, 0.0) for i in items}
        else:
            groups = {-1: [(("supply", i), x) for i, x in sup.get(v, {}).items() if x > EPS]}
            for e in ins:
                for i, x in req_edge.get((e.src, v), {}).items():
                    groups.setdefault(e.rank, []).append(((e.src, i), x))
            offered = sum(x for rows in groups.values() for _k, x in rows)
            adm = _admit(min(can[v], offered), list(groups.items()))
            got = {}
            for (_src, item), x in adm.items():
                got[item] = got.get(item, 0.0) + x
        total = sum(got.values())
        if total > EPS:
            out[v] = {i: got.get(i, 0.0) / total for i in items}
    return out


# -- report ---------------------------------------------------------------------------------------
def _name(nid):
    key, lane = nid
    return f"{key}{'.' + lane if lane else ''}"


def report(res, lg, top=8):
    """Report lines: the starved sinks, the blocked sources and the binding connectors."""
    lines = []
    starved = res.starved()
    for nid in sorted(starved, key=str):
        for item, (got, want) in sorted(starved[nid].items()):
            lines.append(f"STARVED {_name(nid)}: {item} {got:.3f}/s of {want:.3f}/s")
    for nid in sorted(res.supply, key=str):
        for item, want in sorted(res.supply[nid].items()):
            if want == INF or want <= EPS:
                continue
            gone = res.supplied.get(nid, {}).get(item, 0.0)
            if gone < want - 1e-6:
                lines.append(f"BLOCKED {_name(nid)}: {item} {gone:.3f}/s of {want:.3f}/s left")
    binding = sorted((v for v in lg.nodes if res.binds(v)),
                     key=lambda v: (-res.required_ratio(v), str(v)))
    for v in binding[:top]:
        lines.append(f"BINDS   {_name(v)} ({lg.nodes[v].name}): {res.total(v):.3f}/s at capacity "
                     f"{res.cap[v]:.3f}/s, {res.total(v, res.required):.3f}/s required "
                     f"(ratio {res.required_ratio(v):.2f})")
    if len(binding) > top:
        lines.append(f"BINDS   ... {len(binding) - top} more")
    return lines
