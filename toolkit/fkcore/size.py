"""fkcore.size: the build's ``size`` step, the counts of the ``n=auto`` rows (1.6).

Rory, 2026-10-21: "the original factory source code should have been parametric for number of
bldgs, maybe we just need a solver that's better at setting those parameters to meet constraints
during a build." A row written ``n=auto`` (``n=auto LO..HI``: within those bounds; plain ``auto``
is 1..100) has no count in the source. The build gives it the fewest machines that meet, on the
nominal rate model's numbers (``fkcore.rates``):

- every ``target ITEM RATE`` of the surface file: what the whole surface makes of the item,
  counted as ``report_rates``' target-not-met line counts it;
- every ``assert INST.rate >= R`` of the factory (``>`` and ``==`` too; ``<=`` and ``<`` are
  checked, and a count is never grown for one);
- what its consumers ask (a **feed**): a net an auto row drives carries what the machines on it
  would take at their potential, plus what its sinks with a ``rate=`` take. A sink without a
  rate (labs, a provider chest) takes what is left and asks nothing, so a row into one grows only
  for a target or an assert;
- for an auto sink row (``LabRow<n=auto, rate=...>``) a **drain**: the labs take what reaches them.

How (``size``):

1. **Seed** (``seed``): fkcore.plan's demand propagation on the design's own nets and recipes:
   consumers before producers, a row runs as many machines as its most-asked product takes (duty
   included), and what they need goes on the nets that feed it, shared among their drivers: fixed
   rows and requester chests at their rate first, auto rows the rest, evenly.
2. **Grow**: the real model. While a constraint fails, follow the binding chain from what it
   constrains (as ``rates.chain`` does: what limits it, then what limits that) to the rows that can
   make more, and grow them by what the shortfall takes, spread evenly over the auto rows of one
   net. A chain that ends at a fixed row, a requester chest at its rate, an output nothing takes or
   a row at the top of its bounds stops there, and the report says so.
3. **Trim**: each auto row, consumers first, gives back machines while every constraint that holds
   still holds and none that can't gets worse. The constraint that breaks at one machine fewer is
   the one its count serves (``report`` names it per row).

A growth that doesn't help (a row at full speed whose inputs then run short) is kept only while the
chain names a further row to grow; else it is left out, and the constraint's line says what stops
it. Counts sized to the rate balance a net exactly, and on an exact balance the rate model can put
the binding on either side (the flows are the same either way): inside the size step an auto row
makes ``EDGE`` more than its recipe says, so a tie is always the producer's slack (a rate it
meets, it meets within twice that). The flats it hands on are the plain ones.

Rows grow; where they stand is the player's. ``fit`` says when a sized row runs past its plot or
into another instance (``does-not-fit``), and ``belts`` when a net of a sized row asks more of its belts than
they carry once routed (``size``, from report_rates after route). Checks warn, never gate. Pure,
stdlib only.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from . import expand, rates
from . import surface as surfacemod
from . import terrain as terrainmod
from .elaborate import Check, ElabError, elaborate, load
from .lang import AUTO_TOP, FkSyntaxError

__all__ = ["Want", "Row", "Sizing", "size", "seed", "report", "fit", "belts", "size_file",
           "maker", "has_auto", "EDGE"]

INF = float("inf")
#: an auto row's extra output inside the size step: a tie is the producer's slack
EDGE = 1e-7
_TOL = 1e-9                    # a want holds within this (as report_rates' TARGET and ASSERT)
_NEAR = 1 - 2 * EDGE           # ... and a rate within EDGE's echo
_MOVE = 1e-6                   # a want gets better or worse by more than this (EDGE's echo is less)
#: growth steps at most; each adds a machine or more, so the bounds end it long before
_STEPS = 400
#: names a report line lists before "and N more"
_SHOW = 3
#: what a count serves, the nearest first: the net it feeds or takes from, then what that is for
_DIRECT = {"feed": 0, "drain": 0, "assert": 1, "target": 2}


@dataclass
class Want:
    """One constraint the counts must meet."""
    kind: str                      # target | assert | feed | drain
    factory: str | None            # None: a target, what the whole surface makes
    item: str | None = None
    rate: float = 0.0              # target, assert
    op: str = ">="                 # assert
    net: str | None = None         # feed, drain
    path: str | None = None        # drain: the sink row
    index: int = 0                 # assert: its place in the factory's asserts
    text: str = ""
    ok: bool = True
    slack: float = 0.0             # >= 0 when met: what it has over what it asks (items/s)
    value: float = 0.0             # what it reads at the counts (see _one)
    why: str = ""                  # when not met: what stops the counts from meeting it
    root: str = ""                 # the blocker alone (the why without the nets it runs through)
    sized: bool = False            # an n=auto row is on the way (else TARGET/ASSERT says it all)


@dataclass
class Row:
    """An ``n=auto`` row and the count it got."""
    factory: str
    path: str
    kind: str
    n: int
    lo: int
    hi: int
    seed: int = 0
    was: int | None = None         # the count the committed layout has
    serves: list = field(default_factory=list)    # [Want] that break at one fewer, nearest first
    top: bool = False              # held at its top while a constraint still asks more
    sink: bool = False             # a sink row (labs)
    rated: bool = False            # a sink row with a rate= (else it asks nothing, takes all)
    util: float = 0.0              # its machines' utilization at the counts
    binding: tuple | None = None   # what holds them there (rates' binding)
    nets: list = field(default_factory=list)      # [Want]: the feeds of the nets it drives


@dataclass
class Sizing:
    flats: dict                    # factory -> the sized Flat (plain)
    models: dict                   # factory -> its rates.Model at the counts ({} without rows)
    rows: list                     # [Row], in source order
    wants: list                    # [Want], as they stand at the counts
    solves: int = 0                # rate model solves it took
    grown: int = 0                 # growth steps after the seed
    #: 2.0.60: [(factory, Choice key, pick, menu)] the choices made, and the options tried:
    #: [(score, {key: pick}, cost, build, draw)] best first
    picks: list = field(default_factory=list)
    tried: list = field(default_factory=list)
    payback: float = 0.0           # seconds of the design's draw counted into its cost

    def counts(self, factory):
        return {r.path: r.n for r in self.rows if r.factory == factory}


def has_auto(flat):
    return any(inst.auto for inst in flat.insts.values())


def maker(path, cat, name=None, layout=None):
    """A factory's elaboration at given counts, its file read once: ``make(sizes) -> Flat``."""
    f, modules = load(path)
    name = name or os.path.splitext(os.path.basename(path))[0]
    return lambda sizes: elaborate(f, modules, cat, name, layout, sizes)


# -- the model at given counts --------------------------------------------------------------------
class _Run:
    """Every factory's flat and model at given counts, each solved once; an auto row makes
    ``EDGE`` more (see the module doc)."""

    def __init__(self, makers, base):
        self.makers, self.base, self.cache, self.solves = makers, base, {}, 0

    def at(self, counts):
        out = {}
        for f, make in self.makers.items():
            mine = tuple(sorted((p, n) for (g, p), n in counts.items() if g == f))
            if (f, mine) not in self.cache:
                flat = self.base[f]
                if mine:
                    flat = make(dict(mine))
                    for m in flat.machines.values():
                        if flat.insts[m.inst].auto:
                            m.makes = {i: r * (1 + EDGE) for i, r in m.makes.items()}
                self.cache[(f, mine)] = (flat, rates.solve(flat))
                self.solves += 1
            out[f] = self.cache[(f, mine)]
        return out

    def plain(self, counts):
        """The flats and models at ``counts`` as the steps after this one see them."""
        out = {}
        for f, make in self.makers.items():
            mine = {p: n for (g, p), n in counts.items() if g == f}
            flat = make(mine) if mine else self.base[f]
            out[f] = (flat, rates.solve(flat))
            self.solves += 1
        return out


def _leaves(flat):
    return [p for p, inst in flat.insts.items() if inst.module is None and inst.machines]


def _own(flat, path):
    """The instance whose count a machine of instance ``path`` hangs on: the nearest enclosing
    Stack with ``n=auto`` (2.0.59: its cells' machines), else ``path`` itself."""
    parts = path.split("/")
    for i in range(1, len(parts)):
        node = flat.insts.get("/".join(parts[:i]))
        if node is not None and node.kind == "Stack" and node.auto:
            return node.path
    return path


def _first(flat, path):
    return flat.machines[flat.insts[path].machines[0]]


def _insts(flat, keys):
    out = []
    for k in keys:
        p = flat.machines[k].inst
        if p not in out:
            out.append(p)
    return out


def _makers(flat, net, item):
    return _insts(flat, [k for k, i in flat.nets[net].drivers if i == item])


def _takers(flat, net, item):
    return _insts(flat, [k for k, i in flat.nets[net].consumers if i == item])


def _outs(flat, key, item):
    return max(1, len(flat._out_hold.get((key, item), ())))


def _names(paths):
    shown = ", ".join(paths[:_SHOW])
    return shown + (f" and {len(paths) - _SHOW} more" if len(paths) > _SHOW else "")


def _rate(r):
    """A rate as a source line writes it: 1/s, 10/min."""
    return f"{r:g}/s" if r >= 1 and float(r).is_integer() else f"{r * 60:g}/min"


def _whole(x):
    """Whole machines for ``x``: rounded up, never one more for float dust (plan's rule)."""
    return math.ceil(x - _TOL) if x > _TOL else 0


# -- the constraints ------------------------------------------------------------------------------
def _wants(flats, autos, targets):
    """Targets, asserts, drains, feeds: the order they are met in."""
    out = [Want("target", None, item, want, text=f"target {item} {_rate(want)}")
           for item, want in (targets or {}).items()]
    for f, flat in flats.items():
        for i, (a, _scope) in enumerate(flat.asserts):
            out.append(Want("assert", f, rate=a.rate, op=a.op, index=i,
                            text=f"assert {a.ref}.rate {a.op} {a.rate * 60:g}/min"))
    for f, flat in flats.items():
        for path in _leaves(flat):
            m = _first(flat, path)
            if (f, path) not in autos or not m.sink or m.drain is None:
                continue
            for (k, item), nets in flat._in_hold.items():
                if k == flat.insts[path].machines[0]:
                    out += [Want("drain", f, item, net=n, path=path,
                                 text=f"{path} takes the {item} of net {n}") for n in nets]
    for f, flat in flats.items():
        for name, net in flat.nets.items():
            for item in net.items:
                if not any((f, _own(flat, d)) in autos for d in _makers(flat, name, item)):
                    continue
                if any(_own(flat, d) != d for d in _makers(flat, name, item)) and \
                        all(_own(flat, d) != d for d in _takers(flat, name, item)):
                    continue            # a net inside an auto Stack, from one cell to the next
                asks = _insts(flat, [k for k, i in net.consumers if i == item
                                     and _asks(flat, f, k, item, autos)])
                if asks:
                    out.append(Want("feed", f, item, net=name,
                                    text=f"net {name}: {item} for {_names(asks)}"))
    return out


def _asks(flat, f, key, item, autos):
    """Whether consumer machine ``key`` asks something of an item: a machine that needs it, or a
    sink with a rate that is no auto row (an auto sink is sized to what comes, not the reverse)."""
    m = flat.machines[key]
    if m.needs.get(item, 0.0) > 0:
        return True
    return m.sink and m.drain is not None and (f, _own(flat, m.inst)) not in autos


def _rated(flat, model, f, autos, net, item):
    """What the sinks with a rate on (net, item) take at most (an auto sink row's excepted)."""
    total = 0.0
    for k, i in flat.nets[net].consumers:
        m = flat.machines[k]
        if i == item and m.sink and m.drain is not None and m.needs.get(item, 0.0) <= 0 \
                and (f, m.inst) not in autos:
            total += model.sink_share.get((k, net, item), 0.0)
    return total


def _measure(wants, got, autos):
    """[(ok, slack, value)] of every want on the factories' flats and models ``got``."""
    return [_one(w, got, autos) for w in wants]


def _one(w, got, autos):
    """(ok, slack, value) of one want. value: a target what the surface makes; an assert the
    asserted rate; a feed what the net's drivers can make; a drain what its takers can take."""
    if w.kind == "target":
        v = sum(model.rate(k, w.item) for flat, model in got.values() for k in flat.machines)
        return v >= w.rate * _NEAR - _TOL, v - w.rate, v
    flat, model = got[w.factory]
    if w.kind == "assert":
        return _assert(flat, model, w)
    st = model.nets[(w.net, w.item)]
    if w.kind == "feed":
        ask = st.would_take + _rated(flat, model, w.factory, autos, w.net, w.item)
        # 2.0.59: a port (a Stack's last cell's) makes nothing of its own, it passes on what its
        # cells make: the net's measure is what it delivers
        made = st.delivered if any(k in flat.ports for k, _i in flat.nets[w.net].drivers) \
            else st.can_make
        slack = made - ask
        return slack >= -_TOL * max(1.0, ask), slack, made
    if st.can_make == INF:                                # a drain fed by an unbounded source
        return True, INF, st.can_take
    slack = st.can_take - st.can_make / (1 + EDGE)
    return slack >= -_TOL * max(1.0, st.can_make), slack, st.can_take


def _assert(flat, model, w):
    """An assert's (ok, slack, value), as rates' asserts read it, at the plain rates."""
    a, scope = flat.asserts[w.index]
    _node, keys = flat.resolve_ref(a.ref, scope)
    net = "/".join(p for p in (scope, ".".join(a.ref.parts)) if p)
    if keys:
        item = rates.primary(flat, keys)
        value = sum(model.rate(k, item) for k in keys)
    elif net in flat.nets:
        value = sum(model.nets[(net, i)].delivered for i in flat.nets[net].items)
    else:
        return False, -INF, 0.0
    near = a.rate * 2 * EDGE + _TOL
    ok = {">=": value >= a.rate - near, "<=": value <= a.rate + near, ">": value > a.rate,
          "<": value < a.rate, "==": abs(value - a.rate) <= near}[a.op]
    slack = {">=": value - a.rate, ">": value - a.rate, "<=": a.rate - value,
             "<": a.rate - value}.get(a.op, -abs(value - a.rate))
    return ok, slack, value


def _better(new, old):
    """Whether slack ``new`` is better than ``old`` by more than EDGE's echo."""
    return new > old + _MOVE * max(1.0, abs(old)) if old != -INF else new != -INF


def _worse(new, old):
    return new < old - _MOVE * max(1.0, abs(old)) if old not in (INF, -INF) else new < old


# -- growing --------------------------------------------------------------------------------------
@dataclass
class _Lead:
    rows: list                     # [(factory, path)] that can make more, by one machine each
    net: str | None = None         # the net-item whose shortfall sizes them; None: the want's own
    item: str | None = None
    side: str = "self"             # self | in (drivers of a short net) | out (sinks taking more)
    root: str = ""                 # when no row can grow: what stops it
    via: list = field(default_factory=list)       # the nets it runs short through, to the root
    sized: bool = False            # an n=auto row was on the way
    tops: set = field(default_factory=set)        # auto rows found at their top
    more: list = field(default_factory=list)      # rows whose growth would only run short

    def why(self):
        text = self.root + (f" (through {'; '.join(self.via)})" if self.via else "")
        return (f"more {_names(self.more)} would run short: " if self.more else "") + text


def _free(model, key):
    """A machine that only its duty holds: more of them make more (2.0.63: a DNP one, at duty 0,
    makes nothing however many)."""
    b = model.binding.get(key)
    return b is None or (b[0] == "duty" and model.flat.machines[key].duty > 0)


def _fixed(flat, path, key):
    m = flat.machines[key]
    inst = flat.insts[path]
    if m.extra.get("role") == "requester":
        rate = next(iter(m.makes.values()), 0.0)
        return f"{path} is a requester chest at {rate:.3f}/s (its rate=)"
    if inst.kind == "Literal":
        return f"{path} is a Literal, as built"
    held = f"at its duty {m.duty:g}" if m.duty < 1.0 else "at full speed"
    return f"{path} runs {held} as written (n={len(inst.machines)})"


def _top(c, counts, autos):
    return f"{c[1]} is at n={counts[c]}, the top of its auto {autos[c][0]}..{autos[c][1]}"


def _lead(f, flat, model, path, key, counts, autos, seen):
    """Where more of what ``path`` makes comes from: the rows to grow, or what stops it."""
    me = (f, path)
    if _free(model, key):
        if me not in autos:
            return _Lead([], root=_fixed(flat, path, key))
        if counts[me] < autos[me][1]:
            return _Lead([me], sized=True)
        return _Lead([], root=_top(me, counts, autos), sized=True, tops={me})
    kind, item, net = model.binding[key]
    if net is None:
        return _Lead([], root=f"{path}: its {item} {'input' if kind == 'in' else 'output'} has "
                              "no net", sized=me in autos)
    st = model.nets[(net, item)]
    if kind == "out":
        sinks = [(f, s) for s in _takers(flat, net, item) if (f, s) in autos and _first(flat, s).sink]
        grow = [s for s in sinks if counts[s] < autos[s][1]]
        if grow:
            return _Lead(grow, net, item, "out", sized=True)
        tops = {s for s in sinks}
        root = ("; ".join(_top(s, counts, autos) for s in sinks) if sinks else
                f"{path}: its {item} is not taken on net {net}, whose consumers and sinks take "
                f"{st.demand + st.to_sinks:.3f}/s")
        return _Lead([], root=root, sized=bool(sinks) or me in autos, tops=tops)
    drivers = _makers(flat, net, item)
    keys = {d: next(k for k, i in flat.nets[net].drivers if i == item and flat.machines[k].inst == d)
            for d in drivers}
    free = [(f, d) for d in drivers if _free(model, keys[d])]
    grow = [c for c in free if c in autos and counts[c] < autos[c][1]]
    if grow:
        return _Lead(grow, net, item, "in", sized=True)
    tops = {c for c in free if c in autos}
    deeper = []
    for d in drivers:
        if d in seen or (f, d) in free:
            continue
        lead = _lead(f, flat, model, d, keys[d], counts, autos, seen | {d})
        if lead.rows:
            return lead
        tops |= lead.tops
        deeper.append(lead)
    here = f"net {net}: {st.can_make:.3f}/s of {item} for {st.would_take:.3f}/s asked"
    sized = bool(tops) or me in autos or any((f, d) in autos for d in drivers) \
        or any(lead.sized for lead in deeper)
    if len(deeper) == 1 and not free:
        return _Lead([], root=deeper[0].root, via=[here] + deeper[0].via, sized=sized, tops=tops)
    roots = [_top(c, counts, autos) if c in tops else _fixed(flat, c[1], keys[c[1]]) for c in free]
    roots += [lead.root for lead in deeper if lead.root]
    default = (f"net {net}'s drivers make no more" if drivers
               else f"nothing drives {item} on net {net}")
    return _Lead([], root="; ".join(roots) or default, via=[here], sized=sized, tops=tops)


def _subjects(w, got):
    """[(factory, path, machine key)] whose output the want asks more of, and its item."""
    if w.kind == "target":
        out = []
        for f, (flat, _model) in got.items():
            for path in _leaves(flat):
                for k in flat.insts[path].machines:
                    if flat.machines[k].makes.get(w.item, 0.0) > 0 and not flat.machines[k].source:
                        out.append((f, path, k))
                        break
        return out, w.item
    flat, _model = got[w.factory]
    if w.kind == "assert":
        a, scope = flat.asserts[w.index]
        _node, keys = flat.resolve_ref(a.ref, scope)
        net = "/".join(p for p in (scope, ".".join(a.ref.parts)) if p)
        if not keys and net in flat.nets:
            keys = [k for k, _i in flat.nets[net].drivers]
        item = rates.primary(flat, keys)
        keys = [k for k in keys if flat.machines[k].makes.get(item, 0.0) > 0]
        return [(w.factory, p, next(k for k in keys if flat.machines[k].inst == p))
                for p in _insts(flat, keys)], item
    keys = [k for k, i in flat.nets[w.net].drivers if i == w.item]
    return [(w.factory, p, next(k for k in keys if flat.machines[k].inst == p))
            for p in _insts(flat, keys)], w.item


def _per(flat, model, c, lead, item, w):
    """What one more machine of row ``c`` adds to the shortfall the lead measures, items/s."""
    m = _first(flat, c[1])
    key = flat.insts[c[1]].machines[0]
    if lead.side == "out":
        return model.sink_share.get((key, lead.net, lead.item), 0.0)
    if lead.side == "in":
        return m.makes.get(lead.item, 0.0) / _outs(flat, key, lead.item) * m.duty
    if w.kind == "feed":
        return m.makes.get(item, 0.0) / _outs(flat, key, item) * m.duty
    return m.makes.get(item, 0.0) * m.duty


def _short(flat, model, f, autos, lead, slack):
    """The shortfall (items/s) the lead's rows make up: the want's own, or its net's."""
    if lead.side == "self":
        return -slack if slack != -INF else INF
    st = model.nets[(lead.net, lead.item)]
    if lead.side == "out":
        return st.can_make - st.can_take
    return st.would_take + _rated(flat, model, f, autos, lead.net, lead.item) - st.can_make


def _spread(cands, short, per, counts, autos):
    """Machines for ``short`` over ``cands``, one at a time to the smallest row below its top (at
    least one: a failing want always moves)."""
    extra, left = {}, short
    while True:
        open_ = [c for c in cands if per.get(c, 0.0) > 0
                 and counts[c] + extra.get(c, 0) < autos[c][1]]
        if not open_:
            break
        c = min(open_, key=lambda c: (counts[c] + extra.get(c, 0), cands.index(c)))
        extra[c] = extra.get(c, 0) + 1
        left -= per[c]
        if left <= _TOL * max(1.0, short):
            break
    return extra


def _grow(w, state, got, counts, autos):
    """Machines to add for a failing want (``state``: its (ok, slack, value)) -> (extra
    {(factory, path): k}, lead); the lead's root says what stops it when extra is empty."""
    _ok, slack, value = state
    if w.kind == "assert" and (w.op in ("<=", "<") or (w.op == "==" and value > w.rate)):
        flat, _model = got[w.factory]
        return {}, _Lead([], root="the fewest machines the other constraints take already make "
                                  "more", sized=any((w.factory, p) in autos for p in _leaves(flat)))
    if w.kind == "drain":
        flat, model = got[w.factory]
        c = (w.factory, w.path)
        lead = _Lead([c], w.net, w.item, "out", sized=True)
        if counts[c] >= autos[c][1]:
            return {}, _Lead([], root=_top(c, counts, autos), sized=True, tops={c})
        per = {c: _per(flat, model, c, lead, w.item, w)}
        return _spread([c], _short(flat, model, w.factory, autos, lead, slack), per, counts,
                       autos), lead
    subjects, item = _subjects(w, got)
    if not subjects:
        return {}, _Lead([], root="nothing on the surface makes it" if w.kind == "target"
                         else "it names nothing that makes anything")
    leads = []
    for f, path, key in subjects:
        flat, model = got[f]
        leads.append((f, _lead(f, flat, model, path, key, counts, autos, frozenset({path}))))
    own = [(f, lead) for f, lead in leads if lead.rows and lead.side == "self"]
    pick = own or [(f, lead) for f, lead in leads if lead.rows][:1]
    if not pick:
        stuck = [lead for _f, lead in leads]
        roots = list(dict.fromkeys(lead.root for lead in stuck if lead.root))
        via = next((lead.via for lead in stuck if lead.via), [])
        return {}, _Lead([], root="; ".join(roots), via=via if len(roots) == 1 else [],
                         sized=any(lead.sized for lead in stuck),
                         tops=set().union(*(lead.tops for lead in stuck)))
    cands, per = [], {}
    for f, lead in pick:
        flat, model = got[f]
        for c in lead.rows:
            if c not in per:
                cands.append(c)
                per[c] = _per(flat, model, c, lead, item, w)
    f, lead = pick[0]
    flat, model = got[f]
    extra = _spread(cands, _short(flat, model, f, autos, lead, slack), per, counts, autos)
    if not extra:
        return {}, _Lead([], root=f"{_names([c[1] for c in cands])} make none of {item}",
                         sized=True)
    return extra, lead


def _plus(counts, extra):
    out = dict(counts)
    for c, k in extra.items():
        out[c] += k
    return out


def _helps(run, w, state, trial, autos, depth=4):
    """Whether growing to ``trial`` gets the want closer, now or once the chain's next row grows
    too (a row at full speed whose inputs then run short)."""
    got = run.at(trial)
    new = _one(w, got, autos)
    if _better(new[1], state[1]):
        return True
    if depth <= 0:
        return False
    extra = _grow(w, new, got, trial, autos)[0]
    return bool(extra) and _helps(run, w, state, _plus(trial, extra), autos, depth - 1)


def _stops(run, w, state, counts, autos, depth=4, more=()):
    """The lead that stops a want the counts don't meet (``state``: its (ok, slack, value)
    there): where the chain ends, past any growth that wouldn't help (``more``: the rows such a
    growth would add to, as the lead's ``via`` then says)."""
    got = run.at(counts)
    extra, lead = _grow(w, _one(w, got, autos), got, counts, autos)
    if not extra:
        lead.more = list(more)
        return lead
    trial = _plus(counts, extra)
    if _better(_one(w, run.at(trial), autos)[1], state[1]) or depth <= 0:
        return _Lead([], root=f"more machines would get closer; the size step stopped at its "
                              f"step limit ({_STEPS})", sized=True)
    grown = list(more) + [c[1] for c in extra if c[1] not in more]
    return _stops(run, w, state, trial, autos, depth - 1, tuple(grown))


# -- the seed -------------------------------------------------------------------------------------
def _consumers_first(flat):
    """The leaf instances, every consumer before the rows that feed it (a reverse DFS post-order;
    a loop of nets is cut where the walk closes it)."""
    feeds = {p: [] for p in _leaves(flat)}
    for net in flat.nets.values():
        drivers = _insts(flat, [k for k, _i in net.drivers])
        for c in _insts(flat, [k for k, _i in net.consumers]):
            feeds[c] += [d for d in drivers if d != c and d not in feeds[c]]
    post, seen = [], set()

    def visit(p):
        seen.add(p)
        for d in feeds[p]:
            if d not in seen:
                visit(d)
        post.append(p)

    for p in feeds:
        if p not in seen:
            visit(p)
    return post[::-1]


def _outlet(flat, f, path, autos):
    """Whether a row's output has somewhere that takes it all: a sink without a rate, or an auto
    sink row (sized to take what comes). Then all its machines run, asked or not."""
    key = flat.insts[path].machines[0]
    for item in flat.machines[key].makes:
        for net in flat._out_hold.get((key, item), ()):
            for k, i in flat.nets[net].consumers:
                m = flat.machines[k]
                if i == item and m.sink and (m.drain is None or (f, m.inst) in autos):
                    return True
    return False


def _cap(flat, path, net, item):
    """Instance ``path``'s full-speed rate of ``item`` onto ``net`` (INF for an unbounded source)."""
    m = _first(flat, path)
    if m.source:
        return INF
    key = flat.insts[path].machines[0]
    return m.makes.get(item, 0.0) / _outs(flat, key, item) * m.duty * len(flat.insts[path].machines)


def _share(flat, f, net, item, path, asked, autos):
    """Of what (net, item)'s consumers ask, the part driver ``path`` makes: fixed rows and requester
    chests first at their full rate, the auto rows the rest, evenly."""
    total = asked.get((net, item), 0.0)
    fixed, auto, source = 0.0, [], False
    for d in _makers(flat, net, item):
        if (f, d) in autos:
            auto.append(d)
        elif _first(flat, d).source:
            source = True
        else:
            fixed += _cap(flat, d, net, item)
    if path in auto:
        return 0.0 if source else max(0.0, total - fixed) / len(auto)
    if _first(flat, path).source:
        return max(0.0, total - fixed)
    return _cap(flat, path, net, item) * (min(1.0, total / fixed) if fixed > 0 else 0.0)


def seed(flats, autos, targets=None):
    """{(factory, path): n}: fkcore.plan's demand propagation (consumers before producers, a row's
    count from its most-asked product, what it needs on the nets that feed it) on the design's own
    nets, recipes and machines, duty and requester chests included. Literals are left out; the
    real model that follows makes up for that and everything else a seed can't know."""
    asked = {}                                        # (factory, path) -> {item: /s asked of it}
    for item, want in (targets or {}).items():
        fixed, auto = 0.0, []
        for f, flat in flats.items():
            for path in _leaves(flat):
                m = _first(flat, path)
                if m.makes.get(item, 0.0) <= 0 or flat.insts[path].kind == "Literal":
                    continue
                if (f, path) in autos:
                    auto.append((f, path))
                else:
                    fixed += INF if m.source else m.makes[item] * m.duty * len(
                        flat.insts[path].machines)
        for c in auto:
            mine = asked.setdefault(c, {})
            mine[item] = mine.get(item, 0.0) + max(0.0, want - fixed) / len(auto)
    counts = {}
    for f, flat in flats.items():
        for a, scope in flat.asserts:
            node, keys = flat.resolve_ref(a.ref, scope)
            if a.op in (">=", ">", "==") and node is not None and (f, node.path) in autos:
                item = rates.primary(flat, keys)
                mine = asked.setdefault((f, node.path), {})
                mine[item] = max(mine.get(item, 0.0), a.rate)
        net_ask, runs = {}, {}
        order = [p for p in _consumers_first(flat) if flat.insts[p].kind != "Literal"]
        for path in order:
            c, inst = (f, path), flat.insts[path]
            key = inst.machines[0]
            m = flat.machines[key]
            if m.sink:
                if m.drain is not None and c not in autos:   # a sink with a rate asks its rate
                    for (k, item), nets in flat._in_hold.items():
                        if k == key:
                            for n in nets:
                                each = m.drain if m.extra.get("each") else m.drain / len(nets)
                                net_ask[(n, item)] = net_ask.get((n, item), 0.0) \
                                    + each * len(inst.machines)
                continue
            want = dict(asked.get(c, {}))
            for item in m.makes:
                for n in flat._out_hold.get((key, item), ()):
                    want[item] = want.get(item, 0.0) + _share(flat, f, n, item, path, net_ask,
                                                              autos)
            need = max((v / m.makes[i] for i, v in want.items() if m.makes.get(i, 0.0) > 0
                        and m.makes[i] != INF), default=0.0)        # machines x duty it takes
            full = len(inst.machines) * m.duty
            if c in autos:
                lo, hi = autos[c]
                counts[c] = min(max(_whole(need / m.duty) if m.duty > 0 else lo, lo), hi)
                full = counts[c] * m.duty
            run = full if _outlet(flat, f, path, autos) else min(full, need)
            runs[path] = run
            for item, each in m.needs.items():
                nets = flat._in_hold.get((key, item), ())
                for n in nets:
                    net_ask[(n, item)] = net_ask.get((n, item), 0.0) + run * each / len(nets)
        for path in order:                    # an auto sink row with a rate: what comes to it
            c, inst = (f, path), flat.insts[path]
            m = _first(flat, path)
            if c not in autos or not m.sink or not m.drain:
                continue
            most = 0.0
            for (k, item), nets in flat._in_hold.items():
                if k != inst.machines[0]:
                    continue
                for n in nets:
                    made = sum(runs.get(d, 0.0) * _first(flat, d).makes.get(item, 0.0)
                               / _outs(flat, flat.insts[d].machines[0], item)
                               for d in _makers(flat, n, item))
                    each = m.drain if m.extra.get("each") else m.drain / len(nets)
                    most = max(most, made / each)
            counts[c] = min(max(_whole(most), autos[c][0]), autos[c][1])
    for c, (lo, _hi) in autos.items():
        counts.setdefault(c, lo)
    return counts


# -- size -----------------------------------------------------------------------------------------
#: the most combinations of choices tried one by one; past it, one choice at a time
COMBOS = 64


def draw(flat, model, cat, memo=None):
    """Raw resources a factory takes in per second at its rates: what its sources (requester
    chests, drills, pumps) put on its nets, at each item's raw cost (``fkcore.cost``)."""
    from .cost import item_cost
    memo = {} if memo is None else memo
    total = 0.0
    for key, m in flat.machines.items():
        if m.extra.get("role") == "requester" or m.extra.get("resource") or m.source:
            for item in m.makes:
                r = model.rate(key, item) if hasattr(model, "rate") else 0.0
                if r and r != INF:
                    total += r * item_cost(cat, item, memo)
    return total


def size(makers, targets=None, previous=None, start=None, cat=None, payback=0.0) -> Sizing:
    """The counts of every ``n=auto`` row and Stack, and the picks of every ``modules=auto`` and
    ``machine=auto``, of the factories ``makers``, sized together.

    2.0.60: the picks are searched around the counts. For each combination of picks the counts
    are sized on the rate model (``_size_counts``); of the combinations that meet every want the
    cheapest wins: what its machines and modules cost to build (``fkcore.cost``), plus
    ``payback`` seconds of the raw resources it draws (productivity modules draw less for the
    same output). Up to ``COMBOS`` combinations are each tried; past that, one choice at a time,
    the others held, until none changes."""
    base = {f: make({}) for f, make in makers.items()}
    choices = [(f, key, ch.menu) for f, flat in base.items()
               for key, ch in sorted(getattr(flat, "choices", {}).items())]
    if not choices:
        return _size_counts(makers, targets, previous, start)
    from .catalog import Catalog
    from .cost import design_cost
    cat = cat or Catalog.load()
    memo, seen = {}, {}

    def at(picks):
        key = tuple(sorted(picks.items()))
        if key not in seen:
            mine = {f: {k: v for (g, k), v in picks.items() if g == f} for f in makers}
            wrapped = {f: (lambda sizes, make=make, p=mine[f]: make({**p, **sizes}))
                       for f, make in makers.items()}
            sz = _size_counts(wrapped, targets, previous, start)
            models = dict(sz.models)
            for f, flat in sz.flats.items():
                models.setdefault(f, rates.solve(flat))
            build = sum(design_cost(flat, cat, memo) for flat in sz.flats.values())
            use = sum(draw(flat, models[f], cat, memo) for f, flat in sz.flats.items())
            # what it draws for the rate asked of it: a design that runs past its asserts or
            # targets (into a chest that takes all) is counted at the rate asked
            asked = [w.rate / w.value for w in sz.wants if w.kind in ("target", "assert")
                     and w.op in (">=", ">") and w.value not in (0, INF) and w.ok]
            use *= min([1.0] + asked)
            bad = [w for w in sz.wants if not w.ok]
            short = sum(-w.slack if w.slack != -INF else 1e9 for w in bad)
            cost = build + use * payback
            built = {(f, k): ch.pick for f, flat in sz.flats.items()
                     for k, ch in getattr(flat, "choices", {}).items()}
            seen[key] = ((len(bad), short, cost), sz, cost, build, use, built)
        return seen[key]

    menus = {(f, k): menu for f, k, menu in choices}
    combos = 1
    for menu in menus.values():
        combos *= len(menu)
    if combos <= COMBOS:
        import itertools
        keys = list(menus)
        for pick in itertools.product(*(menus[k] for k in keys)):
            at(dict(zip(keys, pick)))
    else:
        picks = {k: menu[0] for k, menu in menus.items()}
        for _round in range(4):
            changed = False
            for k, menu in menus.items():
                best = min(menu, key=lambda v: at({**picks, k: v})[0])
                if best != picks[k]:
                    picks[k], changed = best, True
            if not changed:
                break
    tried, built = [], set()
    for v in sorted(seen.values(), key=lambda v: v[0]):
        mark = tuple(sorted(v[5].items()))
        if mark not in built:                  # two picks that build the same design: once
            built.add(mark)
            tried.append((v[0], v[5], v[2], v[3], v[4]))
    best = tried[0]
    sz = at(best[1])[1]
    sz.picks = [(f, k, best[1][(f, k)], menus[(f, k)]) for f, k in menus]
    sz.tried = tried
    sz.payback = payback
    return sz


def _size_counts(makers, targets=None, previous=None, start=None) -> Sizing:
    """The counts of every ``n=auto`` row and Stack of the factories ``makers`` ({factory:
    make(sizes) -> Flat}, see ``maker``), sized together: ``targets`` ({item: /s}) are the
    surface's. ``previous`` ({factory: committed Layout}) says what each had; ``start``
    ({(factory, path): n}) replaces the seed (a test, or a what-if).

    2.0.59 (Rory, 2026-10-29: "it should have the rate model, and just optimise the params to get
    the desired rate"): a Stack's ``n=auto`` is a parameter of an outer search. At each count of
    the Stacks the rows are sized as before (``_size_rows``) and the rate model measures every
    want; while one fails, the Stack whose one more cell cuts the shortfall most grows; then each
    Stack gives back cells while every want that holds still holds. A Stack's cells can be any
    machines, so its count is never guessed from a recipe: only the model's answer counts."""
    base = {f: make({}) for f, make in makers.items()}
    stacks = {(f, p): inst.auto for f, flat in base.items() for p, inst in flat.insts.items()
              if inst.auto and inst.kind == "Stack"}
    if not stacks:
        return _size_rows(makers, targets, previous, start)
    every = {(f, p): inst.auto for f, flat in base.items() for p, inst in flat.insts.items()
             if inst.auto}
    wants = _wants(base, every, targets)
    rows_start = None if start is None else {c: n for c, n in start.items() if c not in stacks}
    cache, solves = {}, [0]

    def at(sc):
        key = tuple(sorted(sc.items()))
        if key not in cache:
            fixed = {f: {p: n for (g, p), n in sc.items() if g == f} for f in makers}
            wrapped = {f: (lambda sizes, make=make, fx=fixed[f]: make({**fx, **sizes}))
                       for f, make in makers.items()}
            sz = _size_rows(wrapped, targets, previous, rows_start)
            models = dict(sz.models)
            for f, flat in sz.flats.items():
                if f not in models:
                    models[f] = rates.solve(flat)
            solves[0] += sz.solves + (len(sz.flats) - len(sz.models))
            got = {f: (sz.flats[f], models[f]) for f in sz.flats}
            cache[key] = (sz, got, _measure(wants, got, every))
        return cache[key]

    def score(states):
        bad = [s for ok, s, _v in states if not ok]
        return (len(bad), sum(-s if s != -INF else 1e9 for s in bad))

    def first(c):
        if start is not None and c in start:
            return start[c]
        was = _was(previous, c, "Stack")
        return was if was is not None else stacks[c][0]

    sc = {c: min(max(int(first(c)), lo), hi) for c, (lo, hi) in stacks.items()}
    seeded = dict(sc)
    grown = 0
    while grown < _STEPS:
        _sz, _got, states = at(sc)
        now = score(states)
        if now[0] == 0:
            break
        best = None
        for c, (_lo, hi) in stacks.items():
            if sc[c] >= hi:
                continue
            trial = {**sc, c: sc[c] + 1}
            s = score(at(trial)[2])
            if s < now and (best is None or s < best[0]):
                best = (s, trial)
        if best is None:
            break
        sc, grown = best[1], grown + 1
    serves = {c: [] for c in stacks}
    for _round in range(3):
        changed = False
        for c, (lo, _hi) in stacks.items():
            while sc[c] > lo:
                state = at(sc)[2]
                trial = {**sc, c: sc[c] - 1}
                new = at(trial)[2]
                broke = [w for w, (ok0, s0, _v0), (ok1, s1, _v1) in zip(wants, state, new)
                         if (ok0 and not ok1) or (not ok0 and _worse(s1, s0))]
                if broke:
                    serves[c] = sorted(broke, key=lambda w: _DIRECT[w.kind])
                    break
                sc, changed = trial, True
                serves[c] = []
        if not changed:
            break
    sz, got, states = at(sc)
    for w, (ok, slack, value) in zip(wants, states):
        w.ok, w.slack, w.value = ok, slack, value
        if not ok:
            tops = [c[1] for c in stacks if sc[c] >= stacks[c][1]]
            w.sized = True
            w.root = (f"{_names(tops)} at the top of its auto bounds" if tops else
                      "one more cell of any auto Stack doesn't get closer")
            w.why = w.root
    out = []
    for c, (lo, hi) in stacks.items():
        flat, model = got[c[0]]
        node = flat.insts[c[1]]
        key = node.machines[0] if node.machines else None
        r = Row(c[0], c[1], "Stack", sc[c], lo, hi, seeded[c], _was(previous, c, "Stack"),
                serves=serves[c], top=sc[c] >= hi and not all(w.ok for w in wants))
        if key is not None:
            r.util = model.util.get(key, 0.0)
            r.binding = model.binding.get(key)
        out.append(r)
    return Sizing(sz.flats, {f: m for f, (_fl, m) in got.items()}, sz.rows + out, wants,
                  solves[0], grown + sz.grown)


def _size_rows(makers, targets=None, previous=None, start=None) -> Sizing:
    """The rows' counts (1.6): seed, grow along the binding chains, trim (the module doc). A
    Stack's count is fixed here, by the sizes ``makers`` are given (``size``'s outer search)."""
    base = {f: make({}) for f, make in makers.items()}
    autos = {(f, p): inst.auto for f, flat in base.items() for p, inst in flat.insts.items()
             if inst.auto and inst.kind != "Stack"}
    if not autos:
        return Sizing(base, {}, [], [])
    run = _Run(makers, base)
    wants = _wants(base, autos, targets)
    first = dict(start) if start is not None else seed(base, autos, targets)
    counts = {c: min(max(int(first.get(c, lo)), lo), hi) for c, (lo, hi) in autos.items()}
    rows = {}
    for c, (lo, hi) in autos.items():
        flat = base[c[0]]
        m = _first(flat, c[1])
        rows[c] = Row(c[0], c[1], flat.insts[c[1]].kind, counts[c], lo, hi, counts[c],
                      _was(previous, c, flat.insts[c[1]].kind), sink=m.sink,
                      rated=m.drain is not None)
    grown = 0
    while grown < _STEPS:
        got = run.at(counts)
        step = None
        for w, state in zip(wants, _measure(wants, got, autos)):
            if state[0]:
                continue
            extra = _grow(w, state, got, counts, autos)[0]
            if extra and _helps(run, w, state, _plus(counts, extra), autos):
                step = extra
                break
        if step is None:
            break
        counts, grown = _plus(counts, step), grown + 1
    counts = _trim(run, wants, counts, autos, rows,
                   [(f, p) for f, flat in base.items() for p in _consumers_first(flat)
                    if (f, p) in autos])
    got = run.at(counts)
    tops = set()
    for w, state in zip(wants, _measure(wants, got, autos)):
        w.ok, w.slack, w.value = state
        if not w.ok:
            lead = _stops(run, w, state, counts, autos)
            w.root, w.sized, w.why = lead.root, lead.sized, lead.why()
            tops |= lead.tops
    plain = run.plain(counts)
    for c, r in rows.items():
        r.n = counts[c]
        r.top = c in tops and r.n >= r.hi
        flat, model = got[c[0]]                    # a tie is the producer's slack here
        r.util = model.util[flat.insts[c[1]].machines[0]]
        r.binding = model.binding.get(flat.insts[c[1]].machines[0])
        mine = {n for (k, _i), ns in flat._out_hold.items() if k == flat.insts[c[1]].machines[0]
                for n in ns}
        r.nets = [w for w in wants if w.kind == "feed" and w.factory == c[0] and w.net in mine]
    return Sizing({f: plain[f][0] for f in base}, {f: plain[f][1] for f in base},
                  list(rows.values()), wants, run.solves, grown)


def _was(previous, c, kind):
    lay = (previous or {}).get(c[0])
    rec = lay.insts.get(c[1]) if lay is not None else None
    if rec is None or rec.kind != kind:
        return None
    try:
        return int(rec.params.get("n", ""))
    except ValueError:
        return None


def _trim(run, wants, counts, autos, rows, order):
    """Each auto row, consumers first, one machine fewer while no want that holds breaks and none
    that doesn't gets worse; the wants that break at one fewer are what the count serves."""
    state = _measure(wants, run.at(counts), autos)
    for _round in range(3):
        changed = False
        for c in order:
            while counts[c] > autos[c][0]:
                trial = dict(counts)
                trial[c] -= 1
                new = _measure(wants, run.at(trial), autos)
                broke = [w for w, (ok0, s0, _v0), (ok1, s1, _v1) in zip(wants, state, new)
                         if (ok0 and not ok1) or (not ok0 and _worse(s1, s0))]
                if broke:
                    rows[c].serves = sorted(broke, key=lambda w: _DIRECT[w.kind])
                    break
                counts, state, changed = trial, new, True
                rows[c].serves = []
        if not changed:
            break
    return counts


# -- report ---------------------------------------------------------------------------------------
def _auto(r):
    text = "auto" if (r.lo, r.hi) == (1, AUTO_TOP) else f"auto {r.lo}..{r.hi}"
    return text + (", at its top" if r.top else "")


def _serves(r):
    if r.serves:
        direct = [w for w in r.serves if w.kind in ("feed", "drain")]
        ends = [w for w in r.serves if w.kind in ("target", "assert")]
        text = (direct or ends)[0].text
        if direct and ends:
            text += f" (for {ends[0].text}" + (f" and {len(ends) - 1} more" if len(ends) > 1
                                                else "") + ")"
        return text
    if r.top:
        return "its top, and a constraint still asks more (below)"
    if r.sink:
        if not r.rated:
            return ("its low bound: a lab takes every pack that reaches it (no rate=), so nothing "
                    "asks for more labs")
        return "its low bound: what comes to them fits"
    if r.util >= 1 - 1e-6:
        return "its low bound, all of it used" + (f": {r.nets[0].text}" if r.nets else "")
    kind, item, net = r.binding or ("out", None, None)
    if kind in ("in", "out") and net is None and item is not None:
        return (f"its low bound: its {item} {'input' if kind == 'in' else 'output'} has no net, "
                "so it makes nothing")
    if kind == "in" and net is not None:
        return f"its low bound, used {r.util:.0%}: short of {item} on net {net}"
    if kind == "duty":
        return f"its low bound, at its duty ({r.util:.0%})"
    if r.util > 1e-6:
        return f"its low bound, used {r.util:.0%}: what is asked of it takes less"
    return "its low bound: nothing asks for it"


def _value(w):
    if w.kind == "target":
        return "unbounded" if w.value == INF else f"{w.value:.3f}/s"
    if w.kind == "assert":
        return f"{w.value * 60:.3f}/min"
    if w.kind == "feed":
        return f"its drivers can make {w.value:.3f}/s, its consumers ask {w.value - w.slack:.3f}/s"
    return f"its takers can take {w.value:.3f}/s of {w.value - w.slack:.3f}/s"


def report(sz, cat=None):
    """-> (report lines, checks). The first line, ``sized: red n=10 (auto), ...``; per row its
    count, its seed, its bounds and what it serves; the constraints at the counts; the nets of
    sized rows that carry more than a yellow lane. A constraint the counts can't meet with an
    n=auto row on the way is a ``size`` check (else the target-not-met or rate-not-met line says it all), one
    per blocker."""
    if not sz.rows and not sz.picks:
        return ["no n=auto rows: every count is as written"], []
    if not sz.rows:
        lines = _chose(sz)
        lines += ["", "  constraints at these picks"]
        for w in sz.wants:
            lines.append(f"  {'met    ' if w.ok else 'NOT met'} {w.text}: {_value(w)}"
                         + ("" if w.ok else f"; {w.why}"))
        return lines, []
    multi = len({r.factory for r in sz.rows}) > 1

    def name(r):
        return f"{r.factory}/{r.path}" if multi else r.path

    failing = [w for w in sz.wants if not w.ok]
    lines = ["sized: " + ", ".join(f"{name(r)} n={r.n} ({_auto(r)})" for r in sz.rows),
             f"  {len(sz.rows)} n=auto row{'s' if len(sz.rows) != 1 else ''}, "
             f"{sum(r.n for r in sz.rows)} machines; "
             + (f"{len(failing)} constraint{'s' if len(failing) != 1 else ''} not met (below)"
                if failing else "every target and assert holds, and every net of an n=auto row "
                                "carries what its consumers ask")
             + f"; {sz.solves} model solves, {sz.grown} growth step"
             + ("s" if sz.grown != 1 else "") + " after the seed", ""]
    width = max(20, *(len(name(r)) + 1 for r in sz.rows))
    lines.append(f"  {'row':<{width}} {'kind':<11} {'n':>4} {'seed':>4} {'bounds':>8}  serves")
    for r in sz.rows:
        lines.append(f"  {name(r):<{width}} {r.kind:<11} {r.n:>4} {r.seed:>4} "
                     f"{f'{r.lo}..{r.hi}':>8}  {_serves(r)}")
        if r.was is not None and r.was != r.n:
            lines.append(f"  {'':<{width}} the committed layout has n={r.was}: the row "
                         f"{'grows' if r.n > r.was else 'shrinks'} by {abs(r.n - r.was)}")
    lines += ["", "  constraints at these counts"]
    for w in sz.wants:
        lines.append(f"  {'met    ' if w.ok else 'NOT met'} {w.text}: {_value(w)}"
                     + ("" if w.ok else f"; {w.why}"))
    groups = {}
    for w in failing:
        if w.sized:
            groups.setdefault(w.root, []).append(w)
    checks = []
    for _root, ws in groups.items():
        w = ws[0]
        where = w.text if w.factory is None else f"{w.factory} {w.text}"
        also = [x.text for x in ws[1:]]
        checks.append(Check("size", where, f"{_value(w)}: {w.why}"
                            + (f"; the same stops {_names(also)}" if also else "")))
    lines += _lanes(sz, cat, name)
    if sz.picks:
        lines += [""] + _chose(sz)
    return lines, checks


def _picks_text(picks):
    return ", ".join(f"{k.split('.', 1)[1].rsplit('.', 1)[0]} {k.rsplit('.', 1)[1]}="
                     f"{v if v is not None else 'none'}" for (_f, k), v in sorted(picks.items()))


def _chose(sz):
    """The picks of ``modules=auto`` / ``machine=auto`` (2.0.60), and the next options tried."""
    best = sz.tried[0]
    cost = (f"cost {best[2]:.0f}: build {best[3]:.0f}"
            + (f" + {best[4]:.2f}/s drawn over {sz.payback / 3600:g} h" if sz.payback else ""))
    lines = [f"chose: {_picks_text(best[1])} ({cost}; {len(sz.tried)} options tried)"]
    for score, picks, c, build, _use in sz.tried[1:4]:
        why = (f"{score[0]} constraint{'s' if score[0] != 1 else ''} not met" if score[0]
               else f"cost {c:.0f}, build {build:.0f}")
        lines.append(f"  then: {_picks_text(picks)} ({why})")
    return lines


def _lanes(sz, cat, name):
    """Nets of sized rows that carry more at the counts than a yellow lane does (on paper)."""
    lane = (cat.belt_throughput("transport-belt") / 2) if cat is not None else 7.5
    out = []
    for f, flat in sz.flats.items():
        mine = {r.path for r in sz.rows if r.factory == f}
        model = sz.models.get(f)
        if not mine or model is None:
            continue
        for net_name, net in flat.nets.items():
            ends = _insts(flat, [k for k, _i in net.drivers + net.consumers])
            sized = [p for p in ends if p in mine]
            if not sized or (cat is not None and all(cat.is_fluid(i) for i in net.items)):
                continue
            flow = sum(model.nets[(net_name, i)].delivered for i in net.items)
            if lane < flow < INF:
                out.append(f"  net {net_name}: {flow:.3f}/s at these counts (sized: "
                           f"{_names(sized)}), more than a yellow lane carries ({lane:g}/s)")
    if out:
        out = ["", "  belts on paper (a lane is half a belt; report_rates after route says what "
                   "the belts carry as laid)"] + out
    return out


def fit(sz, places, cat):
    """``does-not-fit`` lines: a sized row whose frame, at its ``@(x, y)`` hint, runs past its factory's
    plot or into another instance's frame. Nothing is moved: rows grow, and the player arranges
    the layout (place stops on two entities on a tile, floorplan on a factory past its plot)."""
    checks = []
    for f, flat in sz.flats.items():
        mine = [r for r in sz.rows if r.factory == f]
        if not mine:
            continue
        cache, frames = {}, {}
        for path in flat.insts:
            box = expand.planned_frame(flat, path, cat, cache)
            if box is not None:
                frames[path] = box
        p = places.get(f) if places else None
        room = terrainmod.frame_size(p) if p is not None else None
        order = {r.path: i for i, r in enumerate(mine)}      # a pair once, under the first row
        for r in mine:
            box = frames.get(r.path)
            if box is None:
                continue
            x0, y0, x1, y1 = box
            what = (f"n={r.n} ({_auto(r)}) makes it {_n(x1 - x0)}x{_n(y1 - y0)} at "
                    f"@({_n(x0)}, {_n(y0)})")
            if room is not None and (x0 < 0 or y0 < 0 or x1 > room[0] or y1 > room[1]):
                past = [f"x to {_n(x1)}" if x1 > room[0] else "",
                        f"y to {_n(y1)}" if y1 > room[1] else "",
                        "a negative corner" if x0 < 0 or y0 < 0 else ""]
                checks.append(Check("does-not-fit", f"{f} {r.path}",
                                    f"{what}: it runs past plot {f} ({room[0]}x{room[1]} in the "
                                    f"factory's frame), {', '.join(t for t in past if t)}"))
            for other, ob in frames.items():
                if other == r.path or order.get(other, len(order)) < order[r.path]:
                    continue
                w = min(x1, ob[2]) - max(x0, ob[0])
                h = min(y1, ob[3]) - max(y0, ob[1])
                if w > 0 and h > 0:
                    checks.append(Check("does-not-fit", f"{f} {r.path}",
                                        f"{what}: its frame overlaps {other}'s "
                                        f"({_n(ob[2] - ob[0])}x{_n(ob[3] - ob[1])} at "
                                        f"@({_n(ob[0])}, {_n(ob[1])})) on {_n(w)}x{_n(h)} tiles"))
    return checks


def _n(v):
    return int(v) if float(v).is_integer() else v


def belts(sz, factory, r):
    """``size`` lines after route (report_rates' second run): a net an n=auto row is on whose
    critical connector the design asks more of than it carries, at the sized counts. ``r`` is the
    factory's ``routed.Routed``."""
    from . import routed as routedmod
    from . import throughput as tp
    rows = {x.path: x for x in sz.rows if x.factory == factory}
    if not rows:
        return []
    flat, out = r.flat, []
    for name, net in flat.nets.items():
        sized = [p for p in _insts(flat, [k for k, _i in net.drivers + net.consumers]) if p in rows]
        if not sized:
            continue
        found = routedmod.critical(r, r.nodes_of(name))
        if found is None:
            continue
        nid, req, cap, ratio = found
        if ratio <= 1 + 1e-6 or req >= tp.BIG / 2:
            continue
        out.append(Check("size", f"{factory} net {name}",
                         f"at the sized counts ({', '.join(f'{p} n={rows[p].n}' for p in sized)}) "
                         f"it asks {req:.2f}/s where its {routedmod.word(r.lg, nid)} carry "
                         f"{cap:.2f}/s (ratio {ratio:.2f}): a yellow lane carries 7.5/s, red 15, "
                         "blue 22.5 (tier --allow), or two nets can share the rate"))
    return out


# -- a factory file on its own --------------------------------------------------------------------
def size_file(path, cat, layout=None):
    """``path``'s factory, sized: together with the surface file beside it when that places this
    module (its targets, and its other factories), else alone (its asserts and what its rows ask).
    -> (the sized Flat, Sizing, what it was sized with). For ``fk rates`` and the other commands
    that read one file."""
    from . import fkp
    folder = os.path.dirname(os.path.abspath(path))
    module = os.path.splitext(os.path.basename(path))[0]
    try:
        sf = surfacemod.load_game(folder)
    except (surfacemod.SurfaceError, OSError):
        sf = None
    placed = [p for p in sf.places.values() if p.lifecycle != "retired"] if sf else []
    me = next((p.factory for p in placed if p.module == module), None)
    makers, previous = {}, {}
    if me is None:
        makers[module] = maker(path, cat, module, layout)
        me, note, targets = module, "with this file's asserts and what its rows ask", None
    else:
        targets = sf.targets
        note = f"with the targets of {os.path.basename(sf.path)} and its placed factories"
        for p in placed:
            src = os.path.join(folder, f"{p.module}.fk")
            if not os.path.exists(src):
                continue
            lay_path = os.path.join(folder, f"{p.module}.fkp")
            try:
                lay = layout if p.factory == me else (fkp.load(lay_path) if os.path.exists(lay_path)
                                                      else None)
                makers[p.factory] = maker(src, cat, p.module, lay)
                makers[p.factory]({})
            except (FkSyntaxError, ElabError, OSError, fkp.FkpError):
                makers.pop(p.factory, None)
                if p.factory == me:
                    raise
                continue
            if lay is not None:
                previous[p.factory] = lay
    sz = size(makers, targets, previous)
    return sz.flats[me], sz, note
