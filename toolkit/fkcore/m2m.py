"""fkcore.m2m: the many-to-many router, version 1 (the baseline of DESIGN-M2M.md; ``route --m2m``).

Rory, 2026-10-31: no trunk rule and no pinch point. The constraints are lane reach (every producer
reaches every consumer), capacity (a max-flow, one lane a run) and the ground; the objective is
belt cost. A net with two or more drivers and two or more consumers whose flow is above one lane
(or whose ``width`` above 2 asks for that many lanes) is laid here, when the route step is given
``--m2m``; every other net routes as before.

1. **The hub, without side-loads** (``fkcore.route._hub_search``): every driver merges into a
   trunk at splitters and inserter drops, the trunk splits to every consumer. Reach holds from
   the start. A driver's inserter stands on the belt's right, looking downstream, so it drops on
   the left lane (``LeftTap``); if no hub routes that way, the hub's own taps are kept.
2. **Measure** (``fkcore.maxflow``): the max-flow over the net's runs and splitters, one lane a
   run, against the demand; and the two sides of the cut where it binds.
3. **Augment** while it falls short: one A* (steiner's own step, ``_astar``) from every start at
   once to every target at once: a start is a straight belt of the net upstream of the cut (it
   becomes a splitter; the new run leaves from its spare half) or a driver with items to spare
   (a new inserter drops onto the run's first belt, on the run's right); a target is a straight
   belt downstream of the cut (it becomes a merge splitter; the run feeds its spare half) or a
   consumer that can take more (a new inserter picks from the run). A run is kept only when the
   max-flow rises. Adding runs never removes a path, so reach still holds.
4. **Improve**: each added run that no later run leans on is ripped up and searched again (or
   dropped, when the rest meets the demand without it) while that lowers the cost.

No grouping rule: which drivers share a run comes out of the search. A net whose flow fits a lane
is not laid here. Out of version 1: side-loads, both lanes, lane balancing, and the even split of
a splitter (a real network can carry less than its max-flow).

**Both lanes** (2.1.21, the default; ``route --m2m left`` is version 1): a driver's inserter may
stand on either side of its belt, and the side is its lane (an inserter across a belt fills the far
lane). The hub takes the taps its search finds; an added run may start from a driver on either
side. The max-flow is the lane graph's (``maxflow.solve(lanes=True)``): no side-loads are laid and
a splitter keeps the lanes, so the left and the right lanes are two networks on the same belts. A
run keeps its lanes from end to end, so it is searched lane by lane: its start upstream of the cut
on that lane (a split belt's lane, or the lane its new inserter drops on), its end downstream of
the cut on it (a merge belt's lane; a consumer picks from both); the cheaper of the two is laid.
A run that does not raise the flow is banned and the search tried again, so the router picks the
lanes that help.

**Lanes filled, and the game's split** (2.1.24):

- *A side-load fills a lane.* A splitter keeps the lanes, and a column of drivers on one side of
  its belt fills one lane, so before this every run carried one. A run may now also end by
  side-loading a plain belt (one fed from behind) on a lane that nothing feeds yet, so one belt
  carries both. No splitter goes on a side-loaded belt (``_straights``), and the run leans on the
  belt and the one behind it (``_improve`` rips neither from under it). A later run may feed that
  lane upstream: a merge, which the max-flow counts like any other.
- *A run comes first out of its split.* The game's splitter splits evenly until an output backs
  up, while the max-flow sends a run what it needs. So the splitter a run leaves by gets the
  output priority on the run's side, and passes on only what the run can't take. Without it, the
  big cases delivered 73-85% of their demand under the game's rules; with it, 93-97%.
- *The game's rules check the result.* Once the max-flow is met, the net is solved as the game
  runs it (``fkcore.throughput``: even splits, back-pressure, a lane serving its first inserters
  first; ``tools/hourglass_lab.py`` measured the same in a real game). While its consumers get
  less than the demand, runs are added from the drivers held back, and the carriers backed up
  behind a node that binds, to the consumers short and the lanes with room down to them. A run
  is kept only when what they get rises.

Still out: an exact balance (a real network can still deliver a few percent less than its
max-flow; the router reports both). Pure, stdlib only, deterministic.
"""
from __future__ import annotations

import json
import math
import time

from . import fkp as fkpmod
from . import lanes as lanesmod
from . import maxflow as mfmod
from . import throughput as tp
from .steiner import COSTS, DX, DY, FixedSink, TapSink, Tree, _astar

__all__ = ["LeftTap", "route", "spec_of", "report"]

_RUNS = 24         # runs added at most, or _RUNS_A_LANE a lane of the demand where that is more
_RUNS_A_LANE = 5   # (2.1.23: a 64/s net took 33 runs; each adds a driver's worth or a lane)
_BANS = 12         # runs passed over (they raised no flow) at most
_TRIES = 6         # searches per run when a found run overlaps its own start or end
_ROUNDS = 3        # improve rounds
_BELT_NAMES = ("transport-belt", "underground-belt", "splitter")
_ITEM = "item"     # the one item of the net, for the game's rules (its lanes don't tell items apart)


class LeftTap(TapSink):
    """A driver machine's tap on a tree laid reversed (the hub's merge tree): the inserter stands
    on the belt's right, looking downstream, so it drops on the left lane. Reversed, a state
    heading ``d`` is a belt running ``d + 2``, whose right is ``d + 3``."""

    def pick(self, path, tree, taken=()):
        on = {(s[0], s[1]) for s in path}
        tunnels = {path[i] for i in range(len(path) - 1) if path[i + 1][3]} \
            if tree.grid.drop_taps else ()
        got = []
        for s in reversed(path[-6:]):
            if s in tunnels:
                continue
            h = (s[2] + 3) % 4
            used = {g[0] for g in got}
            for name in self.inserters:
                r = 2 if name == "long-handed-inserter" else 1
                q = (s[0] + DX[h] * r, s[1] + DY[h] * r)
                if tree.free(q) and q not in on and q not in used and q not in taken and \
                        self.inside((q[0] + DX[h] * r, q[1] + DY[h] * r)):
                    got.append((q, name, ((h + 2) % 4) * 4, self.name))
                    break
            if len(got) >= self.k:
                break
        return got


class _EndTap(TapSink):
    """A consumer at the end of a new run: an inserter picks from it, and the run's last belt
    points into no belt (it would side-load its items there)."""

    def __init__(self, name, rect, inserters, belts):
        super().__init__(name, rect, 1, inserters)
        self.belts = belts

    def goal(self, state, tree, path=None, taken=()):
        x, y, d, _ex = state
        if (x + DX[d], y + DY[d]) in self.belts:
            return False
        return super().goal(state, tree, path, taken)


# -- the spec: what a net carries, per end -------------------------------------------------------
def spec_of(flat, cat, fn, job, lane):
    """{"supply", "take", "demand", "lanes"} of an m2m job: per driver end what it puts on (a
    machine's key, or ("start", tile) for a belt end), per consumer end what it takes (a machine's
    key, or ("goal", tile): one lane), the demand, and the lanes ``width`` asks for (None)."""
    from . import route as rt
    from .lanes import inserter_rate
    supply, take = {}, {}
    for entry, path in job.drv:
        key = entry[0] if isinstance(entry[0], str) else ("start", tuple(entry[0]))
        supply[key] = supply.get(key, 0.0) + rt._driver_rate(flat, fn, entry, path)
    for key, _rect in job.taps:
        m = flat.machines.get(key)
        r = inserter_rate("fast-inserter")
        if m is not None:
            mine = [m.needs[i] for i in fn.items if i in m.needs]
            if mine:
                r = sum(mine)
            elif m.drain is not None:
                r = m.drain
            elif m.extra.get("port"):
                r = lane
        take[key] = take.get(key, 0.0) + r
    for tile, _h in job.goals:
        take[("goal", tuple(tile))] = lane
    demand = min(sum(supply.values()), sum(take.values()), rt._flow(flat, fn))
    return {"supply": supply, "take": take, "demand": demand,
            "lanes": fn.width if fn.width > 2 else None, "both": True}


# -- the net as laid: plan entities by id ---------------------------------------------------------
class _Run:
    """A laid run: its own entities by id, the belts it turned into splitters ({id: (old,
    new)}), its cost and its two ends."""

    def __init__(self, plans, swaps, cost, start, end):
        self.plans, self.swaps, self.cost, self.start, self.end = plans, swaps, cost, start, end
        self.leans = set()            # ids of others' belts it needs as they are (a side-load's)

    @property
    def ids(self):
        return list(self.plans)


class _Net:
    """The net's plan entities (steiner's dicts) by id, with the carriers they make."""

    def __init__(self, cat, net, plan):
        self.cat, self.net = cat, net
        self.items, self.next = {}, 0
        for p in plan:
            self.add(p)

    def add(self, p):
        i = self.next
        self.next += 1
        self.items[i] = p
        return i

    def key(self, i):
        return f"~{self.net}/m{i}"

    def entity(self, i):
        p = self.items[i]
        attrs = {"net": self.net}
        if p.get("belt_to_ground_type"):
            attrs["ug"] = "in" if p["belt_to_ground_type"] == "input" else "out"
        if p.get("config"):
            attrs["config"] = p["config"]
        x, y = p["x"], p["y"]
        return fkpmod.Entity(self.key(i), p["name"], _clean(x), _clean(y), int(p["direction"]),
                             attrs)

    def entities(self):
        return [self.entity(i) for i in sorted(self.items)]

    def plan(self):
        return [self.items[i] for i in sorted(self.items)]

    def at(self):
        """{tile: id} of every tile the net takes."""
        out = {}
        for i in sorted(self.items):
            for t in fkpmod.tiles_of(self.cat, self.entity(i)):
                out[t] = i
        return out

    def cost(self):
        c = 0.0
        for p in self.items.values():
            n = p["name"]
            c += {"transport-belt": COSTS["belt"], "underground-belt": COSTS["ug"] / 2,
                  "splitter": COSTS["split"]}.get(n, COSTS["long_tap"] if "long" in n
                                                  else COSTS["tap"])
        return round(c, 3)


def _clean(v):
    return int(v) if float(v).is_integer() else v


def _tile(p):
    return (math.floor(p["x"]), math.floor(p["y"]))


def _head(p):
    return int(p["direction"]) // 4 % 4


def _straights(net):
    """[(id, tile, heading)] of the net's belts a splitter may replace: a plain belt fed from
    behind by a carrier heading the same way, running on into one that takes from behind, and
    side-loaded by none (a splitter there would cut the side-load off)."""
    at = net.at()
    out = []
    for i in sorted(net.items):
        p = net.items[i]
        if p["name"] != "transport-belt":
            continue
        n, d = _tile(p), _head(p)
        if any(_into(net, at, n, (d + side) % 4) for side in (1, 3)):
            continue
        b = at.get((n[0] - DX[d], n[1] - DY[d]))
        a = at.get((n[0] + DX[d], n[1] + DY[d]))
        if a is None or b is None:
            continue
        pb, pa = net.items[b], net.items[a]
        if pb["name"] not in _BELT_NAMES or _head(pb) != d or \
                pb.get("belt_to_ground_type") == "input":
            continue
        if pa["name"] not in _BELT_NAMES or _head(pa) != d or \
                pa.get("belt_to_ground_type") == "output":
            continue
        out.append((i, n, d))
    return out


def _into(net, at, n, sh):
    """Whether the net's belt on the tile beside ``n`` (heading ``sh`` from it) points into it."""
    j = at.get((n[0] + DX[sh], n[1] + DY[sh]))
    if j is None:
        return False
    q = net.items[j]
    return q["name"] in ("transport-belt", "underground-belt") and \
        q.get("belt_to_ground_type") != "input" and _head(q) == (sh + 2) % 4


def _fed(lg):
    """The lane graph's lane nodes some inserter drops reach (the lanes that carry anything)."""
    todo = [e.dst for e in lg.edges() if e.via == "drop"]
    seen = set(todo)
    while todo:
        for e in lg.out.get(todo.pop(), ()):
            if e.via != "pickup" and e.dst not in seen:
                seen.add(e.dst)
                todo.append(e.dst)
    return seen


def _splitter(n, m, d, first=None):
    """A splitter over tiles ``n`` and ``m`` heading ``d``; ``first``: its output priority (the
    half looking along it, "left" or "right")."""
    p = {"name": "splitter", "x": (n[0] + m[0]) / 2 + 0.5, "y": (n[1] + m[1]) / 2 + 0.5,
         "direction": d * 4, "role": "spine"}
    if first:
        p["config"] = json.dumps({"output_priority": first}, sort_keys=True)
    return p


def _belt(t, d, ug=None):
    p = {"name": "underground-belt" if ug else "transport-belt", "x": t[0] + 0.5,
         "y": t[1] + 0.5, "direction": d * 4, "role": "spine"}
    if ug:
        p["belt_to_ground_type"] = ug
    return p


def _path_plan(path, last_head):
    """Plan entities of a forward path (steiner states), its last belt heading ``last_head``."""
    out = []
    for j, s in enumerate(path):
        x, y, d, ex = s
        nxt = path[j + 1] if j + 1 < len(path) else None
        if nxt is not None and nxt[3]:
            out.append(_belt((x, y), d, "input"))
            continue
        if ex:
            out.append(_belt((x, y), d, "output"))
            continue
        out.append(_belt((x, y), nxt[2] if nxt is not None else last_head))
    return out


# -- the router -----------------------------------------------------------------------------------
class _Ctx:
    def __init__(self, cat, job, blocked, spans, clear, box, limit, ents, spec):
        self.cat, self.job, self.blocked, self.spans = cat, job, blocked, spans
        self.clear, self.box, self.limit, self.spec = clear, box, limit, spec
        keys = {k for k, _r in list(job.sources) + list(job.taps)}
        self.machines = [e for e in ents if e.key in keys]
        self.rects = {k: r for k, r in list(job.sources) + list(job.taps)}
        self.drivers = [k for k, _r in job.sources]
        self.consumers = [k for k, _r in job.taps]

    def flow(self, net, unbounded=False):
        at = net.at()
        supply, take = {}, {}
        for k, r in self.spec["supply"].items():
            key = net.key(at[k[1]]) if isinstance(k, tuple) and k[1] in at else k
            supply[key] = supply.get(key, 0.0) + r
        for k, r in self.spec["take"].items():
            key = net.key(at[k[1]]) if isinstance(k, tuple) and k[1] in at else k
            take[key] = take.get(key, 0.0) + r
        return mfmod.solve(net.entities() + self.machines, self.cat, supply, take,
                           demand=self.spec["demand"], unbounded=unbounded,
                           lanes=self.spec.get("both", False))

    def played(self, net):
        """The net under the game's rules (``fkcore.throughput``: a splitter splits evenly, what
        an output can't take backs up): (items/s its consumers get, the ``_Played`` sides)."""
        ents = net.entities() + self.machines
        lg = lanesmod.build(ents, self.cat)
        at = net.at()

        def nodes(k):
            if isinstance(k, tuple):
                if k[1] not in at:
                    return []
                return [(net.key(at[k[1]]), "L"), (net.key(at[k[1]]), "R")]
            return [(k, "")]

        supply, demand = {}, {}
        for table, out in ((self.spec["supply"], supply), (self.spec["take"], demand)):
            for k, r in table.items():
                ns = [v for v in nodes(k) if v in lg.nodes]
                for v in ns:
                    out[v] = {_ITEM: out.get(v, {}).get(_ITEM, 0.0) + r / len(ns)}
        res = tp.solve(lg, supply=supply, demand=demand)
        got = sum(res.delivered.get(v, {}).get(_ITEM, 0.0) for v in demand)
        return min(got, self.spec["demand"]), _Played(lg, res, supply, demand)

    def need(self, net):
        """(the flow to raise, unbounded?) or None when the net meets its demand and its lanes."""
        f = self.flow(net)
        if not f.met:
            return f, False
        if self.spec["lanes"]:
            u = self.flow(net, unbounded=True)
            if u.value < self.spec["lanes"] * u.lane - 1e-6:
                return u, True
        return None

    def grid(self, net):
        from . import route as rt
        at = net.at()
        mine = net.entities()
        fronts = set(rt._belt_fronts(self.cat, mine))
        spans = rt._ug_spans(self.cat, mine)
        g = rt._Bounded(set(self.blocked) | set(at) | fronts, self.box,
                        {a: set(self.spans[a]) | spans[a] for a in (0, 1)})
        return g, at

    def search(self, net, f, banned=()):
        """One new run from the source side of ``f``'s cut to its sink side, or None; on both
        lanes, the cheaper of a run on the left lane and one on the right."""
        if not self.spec.get("both"):
            return self._search(net, f, banned, None)
        best, why = None, None
        for ln in ("L", "R"):
            got, w = self._search(net, f, banned, ln)
            if got is not None and (best is None or got[2] < best[2] - 1e-9):
                best = got
            why = why or w
        return (best, None) if best is not None else (None, why)

    def _search(self, net, f, banned, ln):
        """``search`` on lane ``ln`` (None: one lane a run, version 1)."""
        from . import route as rt

        def upstream(key):
            return ((key, ln), 0) in f.lane_S if ln else (key, 0) in f.S

        def downstream(key):
            return ((key, ln), 1) in f.lane_T if ln else (key, 1) in f.T

        grid, at = self.grid(net)
        anchor = next(iter(sorted(at)))
        tree = Tree(grid, (anchor[0], anchor[1], 0, 0))
        tree.sink_terminal = {}
        belts = set(self.clear) | set(at)
        k = COSTS
        starts, sinks, ends = [], [], []
        straights = _straights(net)
        for i, n, d in straights:
            if not upstream(net.key(i)):
                continue
            for side in (1, 3):
                sh = (d + side) % 4
                m = (n[0] + DX[sh], n[1] + DY[sh])
                o2 = (m[0] + DX[d], m[1] + DY[d])
                how = ("split", i, side) + ((ln,) if ln else ())
                if how not in banned and tree.free(m) and tree.free(o2):
                    starts.append(((o2[0], o2[1], d, 0), k["split"] - k["belt"], how, (m,)))
        for key in self.drivers:
            if (key, 1) not in f.S:
                continue
            for q in rt._beside(grid, self.rects[key]):
                r = _away(self.rects[key], q)
                b = (q[0] + DX[r], q[1] + DY[r])
                # the inserter on the belt's right: the left lane; on its left: the right lane
                for h in ((r + 3) % 4,) if ln == "R" else ((r + 1) % 4,):
                    c = (b[0] + DX[h], b[1] + DY[h])
                    how = ("src", key, q, b, h, r)
                    if how not in banned and tree.free(q) and tree.free(b) and tree.free(c):
                        starts.append(((c[0], c[1], h, 0), k["tap"] + k["belt"], how, (q, b)))
        hands = rt._HANDS[0]
        for i, n, d in straights:
            if not downstream(net.key(i)):
                continue
            for side in (1, 3):
                sh = (d + side) % 4
                m = (n[0] + DX[sh], n[1] + DY[sh])
                a = (m[0] - DX[d], m[1] - DY[d])
                spare = (m[0] + DX[d], m[1] + DY[d])
                if tree.free(m) and tree.free(a) and spare not in belts:
                    sinks.append(FixedSink(f"merge{i}.{side}", a, d))
                    ends.append(("merge", i, side, m))
        if ln:
            # a side-load onto a lane nothing feeds yet: the run fills a belt's empty lane (a
            # splitter keeps the lanes, so only this puts both on one belt); a later run may feed
            # that lane upstream, a merge the max-flow counts like any other
            fed = _fed(lanesmod.build(net.entities() + self.machines, self.cat))
            for i, n, d in straights:
                for side in (1, 3):
                    near = "R" if side == 1 else "L"
                    if (net.key(i), near) in fed or \
                            ((net.key(i), near), 0) not in f.lane_T:
                        continue
                    sh = (d + side) % 4
                    m = (n[0] + DX[sh], n[1] + DY[sh])
                    if tree.free(m):
                        sinks.append(FixedSink(f"side{i}.{side}", m, (sh + 2) % 4))
                        ends.append(("side", i, side, m))
        for key in self.consumers:
            if (key, 0) in f.T:
                sinks.append(_EndTap(key, self.rects[key], hands, belts))
                ends.append(("tap", key))
        if not starts or not sinks:
            return None, "no start or no target"
        targets = [j for j in range(len(sinks))]
        for _try in range(_TRIES):
            got = _astar(tree, starts, sinks, targets, self.limit)
            if got is None:
                return None, "no run reaches the sink side"
            path, how, j, gcost = got
            run = self._run(net, tree, path, how, sinks[j], ends[j], gcost)
            if run is not None:
                return run, None
            starts = [s for s in starts if s[2] != how]
            if not starts:
                break
        return None, "every run found overlaps its own ends"

    def _run(self, net, tree, path, how, sink, end, gcost):
        """The plan of a found run, checked: (adds, swaps, cost, start, end) or None when its
        start or end overlaps it."""
        adds, swaps, taken = [], [], set()
        tiles = [(s[0], s[1]) for s in path]
        if how[0] == "split":
            _k, i, side = how[:3]
            p = net.items[i]
            n, d = _tile(p), _head(p)
            sh = (d + side) % 4
            m = (n[0] + DX[sh], n[1] + DY[sh])
            # the run first: a splitter splits evenly, and the run is there to take what the
            # belt on can't (the max-flow's way); it passes on only what the run can't take
            swaps.append((i, _splitter(n, m, d, "right" if side == 1 else "left")))
            taken |= {m}
        else:
            _k, key, q, b, h, r = how
            adds.append({"name": rt_hand(), "x": q[0] + 0.5, "y": q[1] + 0.5,
                         "direction": ((r + 2) % 4) * 4, "role": "ins"})
            adds.append(_belt(b, h))
            taken |= {q, b}
        cost = gcost
        if end[0] == "merge":
            _k, i, side, m = end
            if any(sw[0] == i for sw in swaps):
                return None
            p = net.items[i]
            n, d = _tile(p), _head(p)
            swaps.append((i, _splitter(n, m, d)))
            adds += _path_plan(path, d)
            taken |= {m, (m[0] + DX[d], m[1] + DY[d])}     # its spare output points there
            cost += COSTS["split"] - COSTS["belt"]
        elif end[0] == "side":
            _k, i, side, m = end
            if any(sw[0] == i for sw in swaps):
                return None
            adds += _path_plan(path, (_head(net.items[i]) + side + 2) % 4)
        else:
            got = sink.pick(path, tree, taken)
            if not got:
                return None
            adds += _path_plan(path, path[-1][2])
            for q, name, direction, _s in got:
                adds.append({"name": name, "x": q[0] + 0.5, "y": q[1] + 0.5,
                             "direction": direction, "role": "ins"})
                taken |= {q}
                cost += COSTS["tap"]
        if len(set(tiles)) != len(tiles) or set(tiles) & taken:
            return None
        return adds, swaps, round(cost, 3), how, end

    def lay(self, net, found):
        adds, swaps, cost, start, end = found
        plans = {net.add(p): p for p in adds}
        sw = {}
        for i, p in swaps:
            sw[i] = (net.items[i], p)
            net.items[i] = p
        run = _Run(plans, sw, cost, start, end)
        if end[0] == "side":
            # it leans on the belt it side-loads and the one behind it (without that, a turn)
            p = net.items[end[1]]
            n, d = _tile(p), _head(p)
            behind = net.at().get((n[0] - DX[d], n[1] - DY[d]))
            run.leans = {end[1]} | ({behind} if behind is not None else set())
        return run

    def rip(self, net, run):
        for i in run.plans:
            net.items.pop(i, None)
        for i, (old, _new) in run.swaps.items():
            net.items[i] = old

    def relay(self, net, run):
        """Put a ripped run back as it was (the same ids)."""
        for i, p in run.plans.items():
            net.items[i] = p
        for i, (_old, new) in run.swaps.items():
            net.items[i] = new


class _Played:
    """The two sides of a net short under the game's rules, shaped as a max-flow's (``Flow``) for
    ``_Ctx.search``: the source side is the drivers held back (what they offer backs up) and the
    lanes they put on; the sink side the consumers short of what they ask and the lanes with room
    that reach one."""

    def __init__(self, lg, res, supply, demand):
        blocked = [v for v, t in supply.items()
                   if res.supplied.get(v, {}).get(_ITEM, 0.0) < t[_ITEM] - 1e-6]
        starved = [v for v, t in demand.items()
                   if res.delivered.get(v, {}).get(_ITEM, 0.0) < t[_ITEM] - 1e-6]
        self.S = {(v[0], 1) for v in blocked}
        self.T = {(v[0], 0) for v in starved}
        # the sink side: what reaches a consumer short through nodes with room all the way
        down = _reach(lg, starved, back=True, room=lambda v: res.load(v) < 1 - 1e-6)
        # the source side: what the drivers held back put on, up to where it binds (at capacity
        # while more is asked of it)
        binds = [v for v in lg.nodes if res.binds(v)]
        up = (_reach(lg, blocked) & _reach(lg, binds, back=True)) - down
        lane = {v for v in lg.nodes if lg.nodes[v].kind == "lane"}
        self.lane_S = {(v, s) for v in up & lane for s in (0, 1)}
        self.lane_T = {(v, s) for v in down & lane for s in (0, 1)}


def _reach(lg, start, back=False, room=None):
    """The nodes reached from ``start`` along the lane graph (against it with ``back``), not
    through a machine, and only through nodes ``room`` passes."""
    seen, todo = set(start), list(start)
    while todo:
        v = todo.pop()
        for e in (lg.inn if back else lg.out).get(v, ()):
            w = e.src if back else e.dst
            if w not in seen and lg.nodes[w].kind != "machine" and (room is None or room(w)):
                seen.add(w)
                todo.append(w)
    return seen


def rt_hand():
    from . import route as rt
    return rt._HANDS[0][0]


def _away(rect, q):
    """The heading from a machine's rectangle out to the tile ``q`` beside it."""
    x0, y0, x1, y1 = rect
    if q[1] < y0:
        return 0
    if q[0] >= x1:
        return 1
    if q[1] >= y1:
        return 2
    return 3


def route(cat, job, blocked, spans, clear, box, limit, ents):
    """An m2m job's plan: the hub without side-loads, then augmenting runs, then improved.
    -> (plan entities, report text) or (None, reason)."""
    from . import route as rt
    from .lanes import inserter_rate
    t0 = time.time()
    slow = inserter_rate("long-handed-inserter")
    rate = job.m2m["supply"]

    def tap(key, rect, k, hands):
        # a driver that makes more than a long-handed inserter moves gets a fast one
        if rate.get(key, 0.0) > slow + 1e-9:
            hands = tuple(h for h in hands if h != "long-handed-inserter") or hands
        return LeftTap(key, rect, k, hands)

    both = job.m2m.get("both", False)

    def either(key, rect, k, hands):
        if rate.get(key, 0.0) > slow + 1e-9:
            hands = tuple(h for h in hands if h != "long-handed-inserter") or hands
        return TapSink(key, rect, k, hands)

    plan, how = rt._hub_search(cat, job, blocked, spans, clear, box, limit,
                               tap=either if both else tap)
    left = True
    if plan is None and not both:
        plan, how = rt._hub_search(cat, job, blocked, spans, clear, box, limit)
        left = False
    if plan is None:
        return None, how
    hub = how.rsplit(", hub ", 1)[-1] if ", hub " in how else "?"
    ctx = _Ctx(cat, job, blocked, spans, clear, box, limit, ents, job.m2m)
    net = _Net(cat, job.net, plan)
    runs, why, before, banned, bans = [], None, ctx.flow(net), set(), 0
    cap = max(_RUNS, _RUNS_A_LANE * math.ceil(job.m2m["demand"] / max(before.lane, 1e-9)))
    while len(runs) < cap:
        want = ctx.need(net)
        if want is None:
            break
        f, unb = want
        found, why = ctx.search(net, f, banned)
        if found is None:
            break
        run = ctx.lay(net, found)
        g = ctx.flow(net, unbounded=unb)
        if g.value <= f.value + 1e-6:
            ctx.rip(net, run)
            why = "a run that raises the flow was not found"
            banned.add(found[3])              # its start: search again without it
            bans += 1
            if bans > _BANS:
                break
            continue
        banned.clear()
        runs.append(run)
    _improve(ctx, net, runs)
    # under the game's rules: a splitter splits evenly until an output backs up, so the max-flow
    # can be met while the drivers back up behind a run that takes more than its share; add runs
    # from the drivers held back to the consumers short while that raises what they get
    played, sides = ctx.played(net)
    first, banned, bans = played, set(), 0
    while len(runs) < 2 * cap and played < job.m2m["demand"] - 1e-6 and ctx.flow(net).met:
        found, w = ctx.search(net, sides, banned)
        if found is None:
            why = why or w
            break
        run = ctx.lay(net, found)
        now, now_sides = ctx.played(net)
        if now <= played + 1e-6:
            ctx.rip(net, run)
            banned.add(found[3])
            bans += 1
            if bans > _BANS:
                break
            continue
        banned.clear()
        runs.append(run)
        played, sides = now, now_sides
    after = ctx.flow(net)
    c = {"transport-belt": 0, "underground-belt": 0, "splitter": 0, "ins": 0}
    for p in net.items.values():
        c[p["name"] if p["name"] in c else "ins"] += 1
    text = (f"{c['transport-belt']} belts, {c['underground-belt']} underground ends, "
            f"{c['splitter']} splitters, {c['ins']} inserters, hub {hub}; m2m: "
            f"{len(runs)} run{'s' if len(runs) != 1 else ''} added, max-flow "
            f"{_num(before.value)} -> {_num(after.value)} of {_num(after.demand)}/s, by the game's "
            f"rules {_num(first)} -> {_num(played)}"
            + ("; both lanes" if both else "" if left
               else "; drivers on either lane (no hub with left-lane taps)")
            + (f"; short: {why}" if not after.met and why else "")
            + f" ({time.time() - t0:.1f} s)")
    return net.plan(), text


def report(cat, job, laid, ents):
    """The ``MAXFLOW`` line of an m2m net as laid (``laid``: its carriers; ``ents``: the layout)
    -> (line, why it falls short or None)."""
    keys = {k for k, _r in list(job.sources) + list(job.taps)}
    machines = [e for e in ents if e.key in keys]
    at = {}
    for e in laid:
        if e.name in _BELT_NAMES:
            for t in fkpmod.tiles_of(cat, e):
                at[t] = e.key
    spec = job.m2m

    def keyed(table):
        out = {}
        for k, r in table.items():
            key = at.get(k[1], k) if isinstance(k, tuple) else k
            out[key] = out.get(key, 0.0) + r
        return out

    supply, take = keyed(spec["supply"]), keyed(spec["take"])
    both = spec.get("both", False)
    f = mfmod.solve(list(laid) + machines, cat, supply, take, demand=spec["demand"], lanes=both)
    u = mfmod.solve(list(laid) + machines, cat, supply, take, unbounded=True, lanes=both)
    lanes = u.value / u.lane if u.lane and u.value != float("inf") else 0.0
    text = (f"MAXFLOW   {job.net}: {_num(f.value)} of {_num(f.demand)}/s, the narrowest cut "
            f"{lanes:g} lane{'s' if lanes != 1 else ''}")
    short = None
    if not f.met:
        short = (f"its laid carriers move {_num(f.value)} of the {_num(f.demand)}/s its drivers "
                 f"and consumers ask, short {_num(f.short)}/s; the cut binds at "
                 + ", ".join(f.cut[:6]) + (" ..." if len(f.cut) > 6 else ""))
        text += f"; short {_num(f.short)}/s at " + ", ".join(f.cut[:4]) + \
            (" ..." if len(f.cut) > 4 else "")
    if spec["lanes"] and lanes < spec["lanes"] - 1e-6:
        why = f"width {spec['lanes']} asks for {spec['lanes']} lanes, the narrowest cut has " \
              f"{lanes:g}"
        short = f"{short}; {why}" if short else why
        text += f"; {why}"
    return text, short


def _num(v):
    return f"{v:.1f}" if v != float("inf") else "inf"


def _improve(ctx, net, runs):
    """Rip up each added run no other run leans on (none split or merged on its belts) and
    search it again, or drop it when the rest meets the demand without it, while that lowers
    the cost; the flow never drops below what it was."""
    for _round in range(_ROUNDS):
        better = False
        for idx in range(len(runs) - 1, -1, -1):
            run = runs[idx]
            mine = set(run.plans)
            if any(o is not run and mine & (set(o.swaps) | o.leans) for o in runs
                   if o is not None):
                continue
            was = ctx.flow(net).value, (ctx.flow(net, unbounded=True).value
                                        if ctx.spec["lanes"] else 0.0)
            ctx.rip(net, run)
            want = ctx.need(net)
            if want is None:
                runs[idx] = None             # the rest meets the demand without it
                better = True
                continue
            found, _why = ctx.search(net, want[0])
            if found is not None and found[2] < run.cost - 1e-9:
                new = ctx.lay(net, found)
                now = ctx.flow(net).value, (ctx.flow(net, unbounded=True).value
                                            if ctx.spec["lanes"] else 0.0)
                if now[0] >= was[0] - 1e-6 and now[1] >= was[1] - 1e-6:
                    runs[idx] = new
                    better = True
                    continue
                ctx.rip(net, new)
            ctx.relay(net, run)
        runs[:] = [r for r in runs if r is not None]
        if not better:
            break
