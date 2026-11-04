"""fkcore.routed: a factory's netlist on its routed layout, through the V2 flow solver (CORE-V2.md).

``solve(flat, lay, cat)`` builds the lane graph of the layout (``fkcore.lanes``) and puts every
netlist machine whose entity the layout holds on it (``fkcore.throughput``):

- a crafter or a drill is a ``Machine``: its needs and products at full speed, times its duty, or
  times its nominal utilization where V1's rate model has it bound by a fluid (pipes are not
  modeled: 2.0's pipes are tanks, Rory), the fluids themselves left out;
- a requester chest puts its items on at its rate, each on its own (no recipe ties them); one
  without a rate puts on as much as the carriers take (the solver's ``BIG``: "unbounded");
- a sink (a provider or storage chest, a lab, a turret) takes what reaches it of the items of the
  nets that feed it, up to its rate split over them, without end when it has none.

``report(routed)`` -> (lines, checks). The lines: per instance its nominal utilization (V1) and
its routed one (mean, and the lowest machine), and what limits it; per net and item what its
consumers asked and got, and its critical connector (the largest required ratio). The checks:
``too-thin`` per net and kind of connector that binds (it runs at capacity while more is required
through it): the required flow, what the connector carries, the ratio and the consumers it starves
— and, 2.0.41, per **port** its declared width (one lane 7.5/s, an ``X | X`` port both, 15/s).
Checks warn, never gate. Pure, stdlib only.

A net is one lane (2.0): a belt written ``net=A|B`` gives its left lane to A and its right to B
(``fkcore.fkp``), so each net's critical connector and ``too-thin`` line count only its own lane,
7.5/s on yellow; a V1 ``net=A`` gives A both lanes, and an inserter ``A+B`` counts for both nets
(they share its one hand). ``WRONG-LANE`` (a warning) is the lane-level ``SPILL``: what the design asks of
a lane carries an item none of its nets carries (a pair's lanes swapped), a lane holds nets that go
to different sinks, or a net's items reach the closed side of a side-loaded underground, where they
stop (only the lane on its open side gets through: Rory; ``connect``'s 1.x ``UG_SIDELOAD`` line is
gone for this one).

A bus (``Bus<...>``) is its belts and splitters here, as laid: no pool, the lanes carry what they
carry (a yellow belt 15/s across its two lanes, 7.5 a lane), and its staircases' splitters keep
their priorities. A bus port is no machine: a net's row counts what crosses its belt, and the
consumers a net starves are those beyond the bus too (a load's net, the bus, its taps' nets).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from . import fkp as fkpmod
from . import lanes as lanesmod
from . import rates as ratesmod
from . import throughput as tp
from .elaborate import Check

__all__ = ["Routed", "solve", "required", "report", "critical", "word", "TIER_WORD"]

INF = float("inf")
#: what a belt tier is called in a report line
TIER_WORD = {"transport-belt": "yellow", "underground-belt": "yellow", "splitter": "yellow",
             "fast-transport-belt": "red", "fast-underground-belt": "red", "fast-splitter": "red",
             "express-transport-belt": "blue", "express-underground-belt": "blue",
             "express-splitter": "blue", "turbo-transport-belt": "turbo",
             "turbo-underground-belt": "turbo", "turbo-splitter": "turbo"}
_SHOW = 4


@dataclass
class Routed:
    flat: object
    lay: object
    lg: object                    # fkcore.lanes.LaneGraph
    res: object                   # fkcore.throughput.Result: the routed flows
    model: object                 # V1's nominal rates.Model
    machines: dict                # node id -> throughput.Machine
    base: dict                    # machine key -> the share of full speed it was given (duty, fluid)
    net_of: dict                  # node id -> (net, ...) it carries (the layout's net=, by lane)
    need: dict = None             # node id -> {item: items/s} the design asks (``required``)
    need_edge: dict = None        # (src, dst) -> {item: items/s} the design asks of an edge

    def nodes_of(self, net):
        """The connectors that carry ``net``: its lanes (one of a pair's belts, both of a V1
        one), its inserters."""
        return [nid for nid, nets in self.net_of.items() if net in nets]

    def util(self, key):
        """A machine's routed utilization (absolute, duty included)."""
        nid = (key, "")
        if nid in self.res.util:
            return self.res.util[nid] * self.base.get(key, 1.0)
        if key in self.base and self.res.supply.get(nid):     # a requester at its rate
            return min(self.res.supplied.get(nid, {}).get(i, 0.0) / x
                       for i, x in self.res.supply[nid].items() if x > 0)                 if any(x > 0 for x in self.res.supply[nid].values()) else 0.0
        return None

    def asked(self, nid):
        """What the design asks of a node, items/s (every item)."""
        return sum((self.need or {}).get(nid, {}).values())

    def ratio(self, nid):
        """Required ratio: what the design asks of a node over its capacity, or of the fullest
        of its capped edges (one half of a splitter's lane)."""
        cap = self.res.cap.get(nid, INF)
        v = 0.0 if cap in (0, INF) else self.asked(nid) / cap
        for k in self.res._capped(nid):
            v = max(v, sum((self.need_edge or {}).get(k, {}).values()) / self.res.edge_cap[k])
        return v

    def at(self, nid):
        """(what the design asks, what it carries) where a node binds: a splitter's fullest half
        (one lane per side), anything else itself."""
        capped = self.res._capped(nid)
        if self.lg.nodes[nid].kind == "splitter" and capped:
            k = max(capped, key=lambda k: sum((self.need_edge or {}).get(k, {}).values())
                    / self.res.edge_cap[k])
            return sum((self.need_edge or {}).get(k, {}).values()), self.res.edge_cap[k]
        return self.asked(nid), self.res.cap.get(nid, INF)

    def binds(self, nid):
        """At capacity as routed, while the design asks more of it than it carries."""
        return self.res.load(nid) >= 1 - 1e-6 and self.ratio(nid) > 1 + 1e-6


def _fluid_bound(model, key, cat):
    b = model.binding.get(key)
    return b is not None and b[0] in ("in", "out") and b[1] is not None and cat.is_fluid(b[1])


def _ends(flat, lay, cat, model, lg):
    """(machines, supply, demand, base) of the netlist's machines the layout holds."""
    machines, supply, demand, base = {}, {}, {}, {}
    fed = {}
    for (key, item), nets in flat._in_hold.items():
        fed.setdefault(key, set()).add(item)
    for key, m in flat.machines.items():
        nid = (key, "")
        if key not in lay.entities or nid not in lg.nodes:
            continue
        solid_needs = {i: r for i, r in m.needs.items() if not cat.is_fluid(i)}
        solid_makes = {i: r for i, r in m.makes.items() if not cat.is_fluid(i)}
        if m.sink:
            items = sorted(i for i in fed.get(key, ()) if not cat.is_fluid(i))
            if items:
                each = INF if m.drain is None else (
                    m.drain if m.extra.get("each") else m.drain / len(items))
                demand[nid] = {i: each for i in items}
            if m.source and solid_makes:            # 2.1: a buffer drains as it fills
                supply[nid] = {i: INF for i in solid_makes}
            continue
        if m.source:
            if solid_makes:
                supply[nid] = {i: INF for i in solid_makes}
            continue
        if m.extra.get("role") == "requester":
            if solid_makes:
                supply[nid] = dict(solid_makes)
                base[key] = 1.0
            continue
        share = m.duty
        if _fluid_bound(model, key, cat):
            share = min(share, model.util.get(key, share))
        base[key] = share
        machines[nid] = tp.Machine({i: r * share for i, r in solid_needs.items()},
                                   {i: r * share for i, r in solid_makes.items()})
    return machines, supply, demand, base


def solve(flat, lay, cat, model=None, hand=1, bulk_hand=1) -> Routed:
    """The netlist ``flat`` on its layout ``lay`` through the flow solver. ``hand``/``bulk_hand``:
    the force's inserter hand sizes (1 = no research)."""
    model = model or ratesmod.solve(flat)
    if flat.ports:
        from .expand import port_edges
        flat._port_edges = port_edges(flat, lay, cat)       # 2.0.48: ports are edges
    lg = lanesmod.build(lay, cat, hand=hand, bulk_hand=bulk_hand)
    machines, supply, demand, base = _ends(flat, lay, cat, model, lg)
    res = tp.solve(lg, supply=supply, demand=demand, machines=machines)
    _lg, need = required(flat, lay, cat, model, lg=lg)
    return Routed(flat, lay, lg, res, model, machines, base, lane_nets(lay, lg), need.required,
                  need.required_edge)


def lane_nets(lay, lg):
    """{node id: (net, ...)} of every connector the layout gives a net: a belt's lane the nets
    its ``net=`` puts there (``A|B``: A the left lane), both lanes for a V1 ``net=A``; an
    inserter every net it names."""
    out = {}
    for nid in lg.nodes:
        e = lay.entities.get(nid[0])
        net = e.attrs.get("net") if e is not None else None
        if not net:
            continue
        lanes = fkpmod.net_lanes(net)
        nets = lanes[nid[1]] if lanes is not None and nid[1] in lanes else fkpmod.net_names(net)
        if nets:
            out[nid] = tuple(nets)
    return out


def overflow(flat, net, item):
    """The overflow chests of a net's item: chests without a rate (provider, storage, buffer) on a
    net where something else consumes the item too. They take what the others leave, while they
    have room: the design asks nothing of the carriers into them, and nothing runs short when
    they get less. A chest that is the item's only kind of consumer is where the net goes."""
    cons = [k for k, i in flat.nets[net].consumers if i == item]
    chests = {k for k in cons if flat.machines[k].sink and flat.machines[k].drain is None
              and flat.machines[k].extra.get("role") in _OVERFLOW}
    return chests if len(chests) < len(cons) else set()


_OVERFLOW = ("provider", "storage", "buffer")


def sizing_model(flat, model=None):
    """The rates model a hand is sized by (``fkcore.hands``): every net's supply unlimited
    (``rates.solve``'s ``unbounded``), so each machine runs at what its consumers take, whatever
    its inputs get in the model. A shortage upstream never shrinks a hand; only what the consumers
    take downstream does. 3.0 (the live run, ~7820k): the model saw no plastic out of a hand-piped
    Literal, so all of B5 stood at 0 and its cable hand was left plain at 1.7/s against 3/s.
    Each machine's utilization and each net's delivered and to-sinks is the higher of that solve's
    and the build's, so a hand is never sized below the build's model. Where that solve has no end
    to a net's flow (an unbounded net), what its drivers that make at a rate put on it stands in,
    or the build's figure if that is higher (a source without a rate). Solved once per model
    (``model``, the build's own, keys the cache)."""
    model = model or ratesmod.solve(flat)
    cache = getattr(flat, "_sizing", None)
    if cache is not None and cache[0] is model:
        return cache[1]
    free = ratesmod.solve(flat, unbounded=tuple(flat.nets))
    util = dict(model.util)
    for key, u in free.util.items():
        util[key] = max(util.get(key, 0.0), u)
    def made(ni):
        """What the net's drivers that make at a rate put on it, at the utilizations above."""
        net, item = ni
        if net not in flat.nets:
            return 0.0
        rates_ = [flat.machines[k].makes.get(item, 0.0) * util.get(k, 1.0)
                  for k, i in flat.nets[net].drivers if i == item and k in flat.machines]
        return sum(r for r in rates_ if r != INF)

    def higher(a, b, ni):
        return max(made(ni), b) if a == INF else max(a, b)
    nets = dict(model.nets)
    for ni, st in free.nets.items():
        own = model.nets.get(ni)
        if own is not None:
            nets[ni] = dataclasses.replace(own, delivered=higher(st.delivered, own.delivered, ni),
                                           to_sinks=higher(st.to_sinks, own.to_sinks, ni))
    got = dataclasses.replace(model, util=util, nets=nets)
    flat._sizing = (model, got)
    return got


def required(flat, lay, cat, model=None, hand=1, bulk_hand=1, lg=None):
    """What the design asks of every connector: the flows with every machine at V1's nominal rate
    and no capacities (the solver's ``required``), so a lane that binds today still shows what it
    would carry. An unbounded requester chest puts on what its nets deliver nominally; an overflow
    chest (``overflow``) asks nothing, and any other sink without a rate (a lab, a turret) its
    share of what its nets leave for sinks (the first one on a belt would otherwise ask it all).
    -> (lane graph, throughput.Result)."""
    model = model or ratesmod.solve(flat)
    lg = lg or lanesmod.build(lay, cat, hand=hand, bulk_hand=bulk_hand)
    machines, supply, demand, _base = _ends(flat, lay, cat, model, lg)
    for (net, item), st in model.nets.items():
        spill = overflow(flat, net, item)
        sinks = [k for k, i in flat.nets[net].consumers
                 if i == item and demand.get((k, ""), {}).get(item) == INF]
        for k in sinks:
            if k in spill:
                demand[(k, "")][item] = 0.0
        sinks = [k for k in sinks if k not in spill]
        if not sinks or st.to_sinks == INF:
            continue
        finite = sum(demand.get((k, ""), {}).get(item, 0.0) for k, i in flat.nets[net].consumers
                     if i == item and flat.machines[k].sink and k not in sinks)
        each = max(0.0, st.to_sinks - finite) / len(sinks)
        for k in sinks:
            demand[(k, "")][item] = each
    for nid, items in supply.items():
        if nid[0] in _base and nid not in machines:      # a requester: at its nominal rate
            u = model.util.get(nid[0], 1.0)
            for i in items:
                items[i] *= u
    for nid in machines:
        m, u = flat.machines[nid[0]], model.util.get(nid[0], 1.0)
        for i, r in m.needs.items():
            if not cat.is_fluid(i):
                demand.setdefault(nid, {})[i] = r * u
        for i, r in m.makes.items():
            if not cat.is_fluid(i) and r != INF:
                supply.setdefault(nid, {})[i] = r * u
    for nid, items in supply.items():
        for i, v in list(items.items()):
            if v != INF:
                continue
            total = 0.0
            for n in flat._out_hold.get((nid[0], i), ()):
                st = model.nets.get((n, i))
                if st is not None and st.delivered != INF:
                    drivers = sum(1 for _k, it in flat.nets[n].drivers if it == i)
                    total += st.delivered / max(1, drivers)
            items[i] = total
    return lg, tp.solve(lg, supply=supply, demand=demand)


# -- report ---------------------------------------------------------------------------------------
def word(lg, nid):
    """What a connector is called in a report line: ``yellow lanes``, ``fast-inserters``."""
    n = lg.nodes[nid]
    if n.kind == "inserter":
        return f"{n.name}s"
    tier = TIER_WORD.get(n.name, n.name)
    return f"{tier} splitters (a lane a side)" if n.kind == "splitter" else f"{tier} lanes"


def _keys(nids):
    names = []
    for key, lane in nids:
        name = f"{key}{'.' + lane if lane else ''}"
        if name not in names:
            names.append(name)
    shown = ", ".join(names[:_SHOW])
    return shown + (f" and {len(names) - _SHOW} more" if len(names) > _SHOW else "")


def _consumers(flat, net):
    return [(key, item) for key, item in flat.nets[net].consumers]


def report(r: Routed, factory=None):
    """-> (report lines, too-thin checks)."""
    flat, res, lg = r.flat, r.res, r.lg
    where = f"{factory} " if factory else ""
    head = (f"routed {factory or flat.name} (V2: the lanes and inserters as laid): "
            f"{len(lg.nodes)} connectors and machines; ")
    if not r.machines:
        head += "no machines"
    elif res.settled:
        head += f"the machines settled in {res.outer} round{'s' if res.outer != 1 else ''}"
    else:
        head += (f"the machines did NOT settle in {res.outer} rounds (a utilization still moves "
                 f"by {res.moved:.4f} a round): the routed rates are approximate")
    lines = [head]
    lines.append(f"  {'instance':<18} {'nominal':>7} {'routed':>7} {'lowest':>7}  limited by")
    starved = res.starved()
    for nid in r.machines:              # a machine: what holds it at its utilization
        starved.pop(nid, None)
        for side, item in res.limits(nid):
            if side == "in":
                starved.setdefault(nid, {})[item] = None
    for path, inst in flat.insts.items():
        if inst.module is not None or not inst.machines:
            continue
        us = [(k, r.util(k)) for k in inst.machines if r.util(k) is not None]
        if not us:
            continue
        nominal = min(r.model.util[k] for k in inst.machines)
        mean = sum(u for _k, u in us) / len(us)
        low = min(u for _k, u in us)
        why = []
        hungry = sorted({item for k, _u in us for item in starved.get((k, ""), {})})
        if hungry:
            n = sum(1 for k, _u in us if (k, "") in starved)
            why.append(f"{n} of {len(us)} starved of {', '.join(hungry)}")
        blocked = [k for k, _u in us if any(s == "out" for s, _i in res.limits((k, ""))) or (
            (k, "") not in r.machines and any(
                res.supplied.get((k, ""), {}).get(i, 0.0) < x - 1e-6
                for i, x in res.supply.get((k, ""), {}).items()))]
        if blocked:
            why.append(f"{len(blocked)} of {len(us)} blocked (output backs up)")
        lines.append(f"  {path:<18} {nominal:>7.3f} {mean:>7.3f} {low:>7.3f}  "
                     + ("; ".join(why) if why else ("as nominal" if mean >= nominal - 1e-6
                                                    else "")))
    lines += ["", f"  {'net':<16} {'item':<26} {'asked/s':>8} {'got/s':>8}  critical connector"]
    checks = []
    for net in flat.nets:
        mine = r.nodes_of(net)
        if not mine:
            continue
        cons = _consumers(flat, net)
        for item in flat.nets[net].items:
            asked = sum(_took(r, k, i, True) for k, i in cons if i == item)
            got = sum(_took(r, k, i, False) for k, i in cons if i == item)
            ask = "unbounded" if asked == INF else f"{asked:.3f}"
            lines.append(f"  {net:<16} {item:<26} {ask:>8} {got:>8.3f}  "
                         + _critical(r, mine))
        checks += _capacity(r, net, mine, where)
    checks += lane_checks(r, where)
    checks += port_capacity(r, where)
    return lines, checks


def _took(r, key, item, asked):
    """What a consumer asked of an item (``asked``), or got, as routed. A bus port is no machine:
    what crosses its belt, both lanes (what the design asks of it, or the flow)."""
    if r.flat.machines[key].extra.get("port"):
        table = (r.need or {}) if asked else r.res.flow
        return sum(table.get((key, lane), {}).get(item, 0.0) for lane in ("L", "R"))
    table = r.res.demand if asked else r.res.delivered
    return table.get((key, ""), {}).get(item, 0.0)


def through(flat, net, item):
    """The net and every net its ``item`` goes on to through bus ports: a load's bus, the bus's
    taps' nets, and on (what one net brings to a bus, the bus's taps hand on)."""
    out, todo = [], [net]
    while todo:
        n = todo.pop(0)
        if n in out:
            continue
        out.append(n)
        for key, it in flat.nets[n].consumers:
            m = flat.machines[key]
            if it != item or not m.extra.get("port"):
                continue
            if m.extra["port"] == "load":
                todo.append(m.inst)
            else:
                todo += [x for x in flat._out_hold.get((key, item), ()) if x != m.inst]
    return out


def critical(r, nids):
    """The critical connector among ``nids`` (a net's), the one asked the most of what it
    carries: -> (node id, required, carries, ratio), None when none of them has a capacity."""
    finite = [nid for nid in nids if r.res.cap.get(nid, INF) not in (0, INF)]
    if not finite:
        return None
    nid = max(finite, key=lambda v: (r.ratio(v), str(v)))
    req, cap = r.at(nid)
    return nid, req, cap, r.ratio(nid)


def _critical(r, mine):
    found = critical(r, mine)
    if found is None:
        return "-"
    nid, req, cap, ratio = found
    if req >= tp.BIG / 2:
        return f"{nid[0]} ({word(r.lg, nid)}): unbounded"
    return (f"{nid[0]}{'.' + nid[1] if nid[1] else ''} ({word(r.lg, nid)}): "
            f"{req:.3f}/s required of {cap:.3f}/s, ratio {ratio:.2f}")


def _short(r, net):
    """Who runs short of the net's items -> [text] per item: what the net delivers to its
    consumers against the nominal model, then each consumer short of the item: a machine the item
    holds below its nominal utilization, a sink with a rate that gets less than it asks, the
    labs and turrets (sinks without a rate) when together they get less than their nominal share.
    Left out: overflow chests (they take what is left), and a machine held by something else
    (another input, its output backing up), which asks less."""
    res, flat, model = r.res, r.flat, r.model
    need = r.need or {}
    out = []

    def got_(k, item):
        return res.delivered.get((k, ""), {}).get(item, 0.0)

    def need_(k, item):
        return need.get((k, ""), {}).get(item, 0.0)

    for item in flat.nets[net].items:
        spill, cons = set(), []
        for n in through(flat, net, item):          # past a bus: who the taps feed
            spill |= overflow(flat, n, item)
            cons += [k for k, i in flat.nets[n].consumers
                     if i == item and not flat.machines[k].extra.get("port")]
        cons = [k for k in cons if k not in spill]
        victims, open_ = [], []
        for k in cons:
            nid = (k, "")
            if nid in r.machines:
                u, nominal = r.util(k), model.util.get(k, 1.0)
                if u is not None and u < nominal - 1e-3 and ("in", item) in res.limits(nid):
                    victims.append(f"{k} runs at {u:.2f}, {nominal:.2f} nominal")
            elif res.demand.get(nid, {}).get(item) == INF:
                open_.append(k)
            elif got_(k, item) < need_(k, item) * (1 - 1e-3) - 1e-6:
                victims.append(f"{k} gets {got_(k, item):.2f}/s, {need_(k, item):.2f}/s nominal")
        if sum(got_(k, item) for k in open_) < sum(need_(k, item) for k in open_) * (1 - 1e-3) \
                - 1e-6:
            victims += [f"{k} gets {got_(k, item):.2f}/s, {need_(k, item):.2f}/s nominal"
                        for k in open_ if got_(k, item) < need_(k, item) * (1 - 1e-3) - 1e-6]
        if not victims:
            continue
        got = sum(got_(k, item) for k in cons)
        want = sum(need_(k, item) for k in cons)
        out.append(f"it delivers {got:.2f}/s of {item} to its consumers, {want:.2f}/s nominal")
        out += victims
    return out


def _capacity(r, net, mine, where):
    """``too-thin`` lines of one net: one per kind of connector that binds on it, when the net
    leaves something short of its nominal rate."""
    res, lg = r.res, r.lg
    short = _short(r, net)
    if not short:
        return []
    binding = [nid for nid in mine if r.binds(nid) and r.asked(nid) < tp.BIG / 2]
    groups = {}
    for nid in binding:
        groups.setdefault(word(lg, nid), []).append(nid)
    out = []
    for what, nids in sorted(groups.items()):
        nids.sort(key=lambda v: (-r.ratio(v), str(v)))
        top = nids[0]
        req, cap = r.at(top)
        msg = (f"{req:.2f}/s required where its {what} carry "
               f"{cap:.2f}/s (ratio {r.ratio(top):.2f}): {_keys(nids)}; "
               + "; ".join(short[:_SHOW + 1])
               + (f" and {len(short) - _SHOW - 1} more" if len(short) > _SHOW + 1 else ""))
        out.append(Check("too-thin", f"{where}net {net}", msg))
    return out


_SIDE = {"L": "left", "R": "right"}


def lane_checks(r, where=""):
    """``WRONG-LANE`` lines (a warning, the lane-level SPILL): per item and lane what the design asks of a
    lane that none of its nets carries; a lane given to nets that go to different sinks; the items
    that reach the closed side of a side-loaded underground."""
    flat, lg, lay, need = r.flat, r.lg, r.lay, r.need or {}
    items_of = {n: set(net.items) for n, net in flat.nets.items()}
    sinks_of = {n: {k for k, _i in net.consumers} for n, net in flat.nets.items()}
    wrong, mixed = {}, {}
    for nid, node in lg.nodes.items():
        if node.kind not in ("lane", "splitter"):
            continue
        e = lay.entities.get(nid[0])
        lanes = fkpmod.net_lanes(e.attrs.get("net")) if e is not None else None
        if lanes is None:
            continue                                     # V1: the net has both lanes
        nets = tuple(lanes[nid[1]])
        allowed = set().union(*(items_of.get(n, set()) for n in nets)) if nets else set()
        for item, x in need.get(nid, {}).items():
            if x > 1e-6 and item not in allowed:
                whose = [n for n in fkpmod.net_names(e.attrs["net"]) if item in items_of.get(n, ())]
                wrong.setdefault((whose[0] if whose else None, item, nid[1], nets), []).append(nid)
        known = [n for n in nets if n in sinks_of]
        if len(known) > 1 and len({frozenset(sinks_of[n]) for n in known}) > 1:
            mixed.setdefault((nets, nid[1]), []).append(nid)
    out = []
    for (net, item, lane, nets), nids in sorted(wrong.items(), key=str):
        there = f"which the layout gives net {' and '.join(nets)}" if nets else \
            "which the layout gives no net"
        who = net or (nets[0] if nets else "-")
        out.append(Check("WRONG-LANE", f"{where}net {who}",
                         f"its {item} reaches the {_SIDE[lane]} lane of {_keys(nids)}, {there}: "
                         "a lane carries its own nets' items only"))
    for (nets, lane), nids in sorted(mixed.items(), key=str):
        out.append(Check("WRONG-LANE", f"{where}net {nets[0]}",
                         f"nets {' and '.join(nets)} share the {_SIDE[lane]} lane of "
                         f"{_keys(nids)} but go to different sinks: one lane reaches the same "
                         "sinks for every net on it"))
    for lane_nid, ug in getattr(lg, "closed", ()):
        items = sorted(_reaching(r, lane_nid))
        if not items:
            continue
        e = lay.entities.get(lane_nid[0])
        net = (e.attrs.get("net") if e is not None else None) or "no net"
        other = "R" if lane_nid[1] == "L" else "L"
        out.append(Check("WRONG-LANE", f"{where}net {net}",
                         f"{', '.join(items)} on the {_SIDE[lane_nid[1]]} lane of {lane_nid[0]} "
                         f"reach underground {ug} from its closed side and stop there: only the "
                         f"{_SIDE[other]} lane of a belt side-loaded onto an underground gets "
                         "through"))
    return out


def port_capacity(r, where=""):
    """``too-thin`` lines for a sealed module's **ports** (2.0.41, PLAN-GLOBAL phase A): what the
    design asks through a port against the width the declaration gives it. A one-lane port
    (``@e4L``) is one lane of its belt end, 7.5/s on yellow; an ``X | X`` port is the whole belt
    end, both lanes, 15/s, and its carrier says so (``net=A|A``). A crossing net that asks more
    than its port's width is short **at the port**, whatever the route behind it carries: the fix
    is a second lane (``item | item``) or a second port.

    2.0.48: a port is an edge and lays no belt, so its lanes are those of the belt on the tile
    just inside its edge (the module's own route, where every item through the port passes).

    2.0.54: a port is as wide as that belt carries its item: both lanes there make it a
    both-lane port, written ``item | item`` or not (the width comes from the layout). A port
    pinned to a lane (``@e4L``) stays that one lane: the pin is the promise."""
    flat, lg, need = r.flat, r.lg, r.need or {}
    out = []
    edges = getattr(flat, "_port_edges", None) or {}
    at = {}
    for e in r.lay.entities.values():
        at.setdefault((int(float(e.x) // 1), int(float(e.y) // 1)), e.key)
    for key in sorted(flat.ports):
        port = flat.ports[key]
        pe = edges.get(key)
        ent = at.get(tuple(pe.inside)) if pe is not None else None
        if ent is None:
            continue
        lanes = [nid for nid in ((ent, "L"), (ent, "R")) if nid in lg.nodes]
        if not lanes:
            continue
        unit = lg.nodes[lanes[0]].cap
        what = TIER_WORD.get(lg.nodes[lanes[0]].name, lg.nodes[lanes[0]].name)
        for item in dict.fromkeys(port.all_items):
            on = [nid for nid in lanes if (need.get(nid) or {}).get(item, 0.0) > 1e-6]
            width = port.width_of(item) if port.lane else max(port.width_of(item), len(on))
            cap = unit * width
            req = sum((need.get(nid) or {}).get(item, 0.0) for nid in lanes)
            if req <= cap + 1e-6:
                continue
            if width == 1:
                msg = (f"{req:.2f}/s asked through it, and a one-lane port carries {cap:.2f}/s "
                       f"(one {what} lane of its belt end): declare it both lanes (item | item) "
                       f"or give it a second port")
            else:
                msg = (f"{req:.2f}/s asked through it, and a both-lane port carries {cap:.2f}/s "
                       f"(both {what} lanes of its belt end): give it a wider belt or a second "
                       f"port")
            out.append(Check("too-thin", f"{where}port {key}", msg))
    return out


def _reaching(r, nid):
    """The items the machines and sources upstream of node ``nid`` put on (a machine is an end:
    what goes into it is not what comes out)."""
    lg, res = r.lg, r.res
    seen, todo, items = {nid}, [nid], set()
    while todo:
        v = todo.pop()
        for e in lg.inn.get(v, ()):
            u = e.src
            if u in seen:
                continue
            seen.add(u)
            if lg.nodes[u].kind == "machine":
                items |= set(res.supply.get(u, {})) | set(getattr(r.machines.get(u), "makes", {}))
                continue
            todo.append(u)
    return items
