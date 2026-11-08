"""fkcore.route: the router chunk (CORE-V1.md §9): a factory's nets -> keyed route carriers.

Input: the flat netlist, the factory layout (its fragments placed, each role with an attach
point) and the catalog. For every net that has no carriers of its own yet, the router lays the
belts between the attach points: from each driver role's product belt (the route starts on the
tile its belt points into) to each consumer role's input belt (the route ends on the tile behind
its first belt, pointing into it). Output: carriers keyed ``~<net>/b<k>`` (belts), ``u<k>``
(undergrounds) and ``s<k>`` (splitters), ``net=<net>``, plus a report line per net. A net it
can't route is reported with the reason, never raised; nothing else is moved.

The search is ``fkcore.steiner`` (v4's tree router, copied): one net is a tree of belt steps,
underground jumps and splitters. Every driver must reach every consumer (CORE: a route is any
connected graph joining every driver to every consumer); a net is laid in one of three shapes:

- **forward** (one driver belt): the tree grows from it to all consumers, splitting where it
  branches.
- **reverse** (one consumer belt): the tree grows from the consumer to all drivers and is laid
  reversed, so the splitters become merges (``Tree.entities(reverse)``).
- **hub** (everything else: several drivers and several consumers, a machine driver into a
  machine, a machine driver into several consumers, several drivers into one machine): every
  driver merges into one trunk, which then splits to every consumer. The trunk is a hub of two
  tiles, H and F = H + f: a reversed tree from H to the drivers (laid as merges, H's belt pointing
  into F) and a forward tree from F to the consumers, grown on the ground the first one left.
  Merge-then-split is the shape that joins every driver to every consumer with the fewest
  splitters: each driver joins once and each consumer leaves once (k - 1 merges, m - 1
  splitters), where pairwise trees would need k * m paths and a crossbar of splitters; the trunk
  carries everything, which V1 does not rate (throughput is V2). The hub is searched: free tiles
  ranked by the belt distance (around obstacles, tunnels included) from every driver to H and
  from F to every consumer, 3 tiles apart, tried in order; the cheapest pair of trees of the
  first three that route wins. A hub within 2 tiles of an end, on a side with several ends,
  ranks last: a tree whose first branch ends at once has no straight belt left to cut its next
  splitter into. One machine driver into one machine: a single inserter, where one reaches.

A consumer that is a single machine (a chest, an assembler cell: its role has no attach tile) is
reached by direct insertion: a fast (or long-handed) inserter beside the belt picks into it
(``steiner.TapSink``), keyed ``~<net>/i<k>``. A single machine as a driver works the same way on a
reversed tree, so its inserter turns round to pick from the machine. A driver that puts its output
down itself (3.0: a drill, a recycler; ``Catalog.drop_tile``, the game's drop vector) is reached
by a belt over its drop tile, any heading and no inserter (``steiner.DropSink``,
``NetJob.drops``); the tile is its net's end, so the through-belt of a cell may pass it. A
recycler no belt reaches that way is emptied by an inserter. One whose drop tile lies in a
consumer of its net (a drill into a furnace or a chest) feeds it straight: nothing to lay
(``NetJob.direct``).

Two nets that share a belt (``lanes A | B``, 2.0) are one job, ``A|B``: each net from its drivers
to its side of a join, and one route of both lanes from the join to every consumer
(``fkcore.pairs``, which lays it through ``_route_one`` part by part; ``NetJob.parts``).

A **fluid net** (every item a fluid) is not a belt job: it goes to the fluid plug-in,
``fkcore.fluid_route.route(flat, lay, cat, nets, blocked, box, limit, budget) -> (added, how,
failed)``
(``{net: [Entity]}``, ``{net: report text}``, ``{net: reason}``), called after the belt nets
with the belts already laid. Its job carries the plug-in's ``reserved`` tiles (its machines'
connection tiles, an offshore pump's water), which are among its ends, so no belt takes them, and
the plug-in's ``why_not`` as its skip reason. Without the plug-in a fluid net is reported, not
routed.

Per net the ground is: every entity tile, every placed instance's whole frame (the empty tiles
inside a row are kept for poles), every other net's start and end tiles, every tile another
net's belt points into (any belt tier; an underground of unknown type counts as an exit), every
tile another net's inserter picks from or drops onto and every tile a drill drops onto,
``blocked`` (terrain: water, cliffs) and everything outside ``bounds``. A Bus's frame is not
ground of its own: its belts and splitters are entities, and the empty tiles of its two outer
rows (where its ports meet it) let a route cross it by an underground, as one crosses a main
bus (four belts and those two rows: a yellow tunnel spans the belts).

**The underground guard.** The game pairs an underground entrance with the nearest end of the same
name facing the same way within its reach (``fkcore.connect`` has the same rule). So no new
tunnel may hide another same-axis underground end, and no new end may sit in another tunnel:
``_ug_spans`` gives the search, per axis, every underground end of the layout (any tier; paired,
alone, or of unknown type as a Literal imported from an entity list has them) and the hidden
tiles of every tunnel, paired as the game pairs them; ``steiner`` refuses a jump whose span
touches one, and its own tree's spans likewise. A net that only the guard stops is refused with
the tiles it protects. Laid reversed, a merge splitter's spare output may not point at a belt
(``Grid.keep_clear``) and a tapped belt never starts a tunnel (``Grid.drop_taps``: reversed it is
an exit, which takes no drop); a tapped belt's last tile never points into another net's belt.

Nets are routed one at a time, each seeing the ones before it; a net that fails moves to the
front and the pass is retried, and the pass that routes the most nets is kept. ``fkcore.connect``
is the judge: a routed layout is done when every net is connected with no SPILL. The router runs it
on its own result: a net whose route leaks or does not connect is re-routed around the tiles at
fault (twice at most), else refused with the judge's line.

**A rebuild patches what stands** (2.0, Rory 2026-10-21: "Pin what stands and make patch changes
rather than regenerating the base"). ``route --only stale`` hands the router, as ``patch``, the
committed route of every net none of whose ends moved. The judge reads it against the netlist as
it is now, not by the net's name: a route that joins every driver to every consumer, leaks
nowhere and has no carrier that serves no end stands as it is. Any other keeps what still serves,
loses a dropped end's branch, and gets one branch per new end from the router's own search: a new
consumer off where every standing driver reaches it (a dead-end belt extended, a splitter in
place of a straight belt, an inserter off a belt), a new driver into where it reaches every
consumer (side-loaded onto a belt, into the back of a belt nothing feeds, an inserter onto one).
A lane pair is patched by its two nets, each new driver on its own lane (2.0.2). A patch that
can't be laid or that the judge refuses is routed again as a whole, with the reason.

**Promotion** (2.0.78): a net that finds no route is tried once more with other nets' straight
route belts as ground it may cross (``_promotable``); each tile its route takes turns that net's
belts behind, on and ahead of it into an underground pair (``_promote``), pinned or not, when no
inserter, drill or side-load touches them. ``promoted`` lines name each one.
Pure, stdlib only, deterministic.
"""
from __future__ import annotations

import hashlib
import heapq
import math
import os
import re
import time
from fnmatch import fnmatchcase
from dataclasses import dataclass, field

from . import arms as armsmod
from . import compiled as compiledmod
from . import connect as connectmod
from . import fkp as fkpmod
from . import steiner as steinermod
from .expand import attach_points, frame_of, port_edges, record_misfit
from .steiner import COSTS, DropSink, FixedSink, Grid, TapSink, Tree, grow
from .steiner import _astar as _search      # one A* from many start states: grow()'s own step

__all__ = ["NetJob", "Routed", "jobs", "route_layout", "fluid_nets"]

_H = {0: 0, 4: 1, 8: 2, 12: 3}
_DX = (0, 1, 0, -1)
_DY = (-1, 0, 1, 0)
_BELT_KINDS = frozenset({"transport-belt", "underground-belt", "splitter"})
#: direct insertion: fast inserters (Rory's rule) unless only a long-handed one can reach; the
#: route step's ``--hand`` picks another short hand (1.1, from the 0534 player's own patch: plain
#: inserters while fast ones were short and locked)
_HANDS = (("fast-inserter",), ("fast-inserter", "long-handed-inserter"))
_REACH = {"fast-inserter": 1, "inserter": 1, "burner-inserter": 1, "long-handed-inserter": 2}
_HUBS = 8          # hub candidates tried at most
_HUB_WINS = 3      # stop after this many hubs that route
_NEAR = 12         # tiles around a net's own ends it is searched in first (2.0.45)
_REPAIRS = 2       # judge-and-reroute rounds before a leaking net is refused
#: 3.0 (Rory, 2026-11-01: "routing multiple nets when we can, budgeting like 3 seconds by default
#: before starting to fall back to 1 at a time"): a net that fails is laid again together with the
#: nets laid this run in its way (at most GROUP_MAX, their carriers within GROUP_MARGIN tiles of the
#: way it finds with them lifted), in new orders for up to GROUP_BUDGET s; then it is left to
#: itself. The build script sets both (``route --group-budget S --group-max N``)
GROUP_BUDGET = 3.0
GROUP_MAX = 8
GROUP_MARGIN = 1
#: 3.0: a net that failed is not searched again while its ends and the ground it was searched on
#: stand as they did (``route --again NET`` tries it); the 20261030-2352 world's six nets that
#: never route cost 5-10 s each in every build. Bump it when the router can find more.
MEMO_VERSION = 1
#: 2.1 ``route --m2m``: many-to-many nets above one lane go to ``fkcore.m2m`` (``route_layout``);
#: True on both lanes (2.1.21), "left" on the left lane only (version 1)
_M2M = False
#: 2.1.19 (the live run, ~4525k: a real paste routed 15+ min with nothing said): a callable the
#: route loop tells how far it is, at most every PROGRESS_EVERY seconds; None says nothing
PROGRESS = None
PROGRESS_EVERY = 30.0
_progress_at = [0.0, 0.0]                  # [the last line, the route's start] (monotonic)
#: 2.1.26: [the net routing now, when it began]. The line used to name only the net starting, so
#: the 20261030-2352 run's 300 s on coal read as "net 2 of 43 (larea/b)", the net after it
_progress_net = [None, 0.0]


def _progress(attempt, k, order, net, promo=False, which=None):
    if PROGRESS is None:
        return
    import time
    now = time.monotonic()
    # 2.1.28: the promoting pass is attempt retries + 1, which read as "pass 280". 2.1.29: the
    # fluid nets say so (``which``); they were silent, minutes on end
    if which is None:
        which = "the promoting pass" if promo else f"pass {attempt + 1}"
    last, since = _progress_net
    _progress_net[0], _progress_net[1] = net, now
    if last is not None and now - since >= PROGRESS_EVERY:
        _progress_at[0] = now
        PROGRESS(f"route: {now - _progress_at[1]:.0f}s in, {which}: {last} took "
                 f"{now - since:.0f} s; next net {k + 1} of {len(order)} ({net})")
        return
    if now - _progress_at[0] < PROGRESS_EVERY:
        return
    _progress_at[0] = now
    PROGRESS(f"route: {now - _progress_at[1]:.0f}s in, {which}, net {k + 1} of "
             f"{len(order)} ({net})")


@dataclass
class NetJob:
    net: str
    starts: list = field(default_factory=list)     # [(tile, heading)] the tile a driver belt feeds
    goals: list = field(default_factory=list)      # [(tile, heading)] the tile behind a consumer
    taps: list = field(default_factory=list)       # [(machine key, tile rect)] direct insertion
    sources: list = field(default_factory=list)    # [(machine key, tile rect)] a machine driver
    owners: list = field(default_factory=list)     # "<inst>.<role>" per start/goal, for the report
    skip: str | None = None
    fluid: bool = False                            # a fluid net: the fluid plug-in routes it
    reserved: list = field(default_factory=list)   # tiles a fluid net's connections need
    anchors: dict = field(default_factory=dict)    # "<inst>.<role>" -> what the route is laid to
    parts: tuple = ()                              # two nets on one belt: (A, B, both) jobs
    straight: bool = False                         # a stack cell's net: a straight run first
    wide: bool = False                             # 2.0.54: one net on both lanes (its parts)
    pin: str | None = None                         # 2.0.54: the lane it ends on (L or R)
    drv: list = field(default_factory=list)        # [(start or source, instance path)]
    m2m: dict | None = None                        # 2.1 route --m2m: fkcore.m2m.spec_of
    drops: dict = field(default_factory=dict)      # 3.0: {source key: tile} a drill drops onto
    direct: list = field(default_factory=list)     # 3.0: [(driver, consumer)] dropped straight in
    # PLAN-LANES phase 2: {tile: (need, feeds)}, a tile other carriers feed (a pair's join):
    # need {lane: labels} what its carrier must hold, feeds [(heading 0-3, {lane: labels})] what
    # comes in; lanephys says which carriers the search may put there (was 2.1.31's no_bend)
    lanes: dict = field(default_factory=dict)

    def ends(self):
        return [t for t, _h in self.starts] + [t for t, _h in self.goals] + list(self.reserved) \
            + sorted(set(self.drops.values()))

    def signature(self):
        """Everything a route of this net is laid to: per role, its start or goal tiles with their
        headings and the tiles of the machines it inserts into or picks from; and the reserved
        tiles. A committed fluid route is kept (``route --only stale``) only while this is the
        same: a chest moved one tile changes no belt end, only its machine's tiles (1.2, the 0534
        run). A belt route is kept or patched while no end moved (``moved_ends``)."""
        return (tuple(sorted((o, tuple(sorted(a))) for o, a in self.anchors.items())),
                tuple(sorted(self.reserved)))

    def moved(self, other):
        """The roles whose anchors differ between this job and ``other`` (the same net, another
        layout), sorted."""
        roles = set(self.anchors) | set(other.anchors)
        return sorted(r for r in roles if sorted(self.anchors.get(r, ())) !=
                      sorted(other.anchors.get(r, ())))

    def moved_ends(self, old):
        """The roles with an end that no longer stands where it stood in ``old`` (the same net on
        the committed layout), sorted: a belt end on another tile or heading, a machine of the
        role on other tiles. A role or a machine only one of the two has is an end added or
        dropped, not moved: the committed route is patched for it (2.0, Rory 2026-10-21: "Pin
        what stands and make patch changes rather than regenerating the base")."""
        out = []
        for role in sorted(set(self.anchors) & set(old.anchors)):
            now, was = self.anchors[role], old.anchors[role]
            rects = {a[1]: a[2] for a in now if len(a) == 3}
            if any(a not in now if len(a) == 2 else rects.get(a[1], a[2]) != a[2] for a in was):
                out.append(role)
        return out

    def drivers(self):
        return len(self.starts) + len(self.sources)

    def consumers(self):
        return len(self.goals) + len(self.taps)

    @property
    def mode(self):
        """forward (one driver belt) | reverse (one consumer belt) | hub (the rest)."""
        if len(self.starts) == 1 and not self.sources:
            return "forward"
        if len(self.goals) == 1 and not self.taps:
            return "reverse"
        return "hub"


@dataclass
class Routed:
    layout: object                 # the factory Layout with the route carriers added
    lines: list                    # one report line per net
    routed: list                   # net names routed this run
    failed: dict                   # net -> reason
    kept: list                     # nets whose carriers stand as they were (pinned)
    passes: int = 1
    patched: dict = field(default_factory=dict)    # net -> what its patch laid and kept
    again: dict = field(default_factory=dict)      # net -> why its committed route was not kept
    maxflow: dict = field(default_factory=dict)    # 2.1 --m2m: net -> why its max-flow is short
    memo: dict = field(default_factory=dict)       # 3.0: net -> {key, why} of a net that failed
    #: 3.1, the compile store: sealed unit -> {module, key, nets (its own, in order), from
    #: ("routed", "twin <unit>", "store", or None: not keyed), stored (1 when this run wrote it),
    #: refused (names the force can't build in the stored entry, which was not taken)}
    compiled: dict = field(default_factory=dict)


class _Bounded(Grid):
    """A Grid that is also blocked outside a box, so no search wanders off the factory.
    ``belts`` (the tiles belts stand on) makes it the ground of a tree laid reversed: no merge's
    spare output may point at one, and taps drop onto their belt."""

    def __init__(self, blocked, box, spans=None, belts=None):
        super().__init__(blocked, (), spans)
        self.box = box
        if belts is not None:
            self.keep_clear = set(belts)
            self.drop_taps = True

    def blocked(self, t):
        x0, y0, x1, y1 = self.box
        return not (x0 <= t[0] < x1 and y0 <= t[1] < y1) or t in self.occ

    blocked.native_bounded = True     # steiner's native kernel knows this rule (box, then occ)


def fluid_nets(flat, cat) -> set:
    """The nets whose items are all fluids (pipes, not belts)."""
    return {name for name, net in flat.nets.items()
            if net.items and all(cat.is_fluid(i) for i in net.items)}


def _fluid_plugin():
    try:
        from . import fluid_route
    except ImportError:
        return None
    return fluid_route


def jobs(flat, lay, cat) -> dict:
    """{net: NetJob} from every placed instance's attach points (a fluid net: flagged only)."""
    out = {}
    fluids = fluid_nets(flat, cat)
    plugin = _fluid_plugin() if fluids else None
    for name in sorted(fluids):
        if plugin is None:
            out[name] = NetJob(name, fluid=True, skip="a fluid net: pipes are routed by the fluid "
                                                       "plug-in, not built yet")
        else:
            out[name] = NetJob(name, fluid=True, skip=plugin.why_not(flat, lay, cat, name),
                               reserved=plugin.reserved(flat, lay, cat, name))
    for path, rec in sorted(lay.insts.items()):
        if rec.kind == "Literal" or path not in flat.insts:
            continue
        for role, side, net, (x, y, d) in attach_points(flat, lay, path, cat):
            if net is None or net in fluids:
                continue
            job = out.setdefault(net, NetJob(net))
            mine = job.anchors.setdefault(f"{path}.{role}", [])
            if d is None:
                for key in flat.insts[path].machines:
                    if key not in lay.entities:          # an older layout, before the machine
                        continue
                    ts = fkpmod.tiles_of(cat, lay.entities[key])
                    rect = (min(t[0] for t in ts), min(t[1] for t in ts),
                            max(t[0] for t in ts) + 1, max(t[1] for t in ts) + 1)
                    (job.sources if side == "out" else job.taps).append((key, rect))
                    if side == "out":
                        job.drv.append(((key, rect), path))
                    mine.append((side, key, rect))
                job.owners.append(f"{path}.{role}")
                continue
            h = _H[int(d)]
            tile = (math.floor(x), math.floor(y))
            if side == "out":
                job.starts.append(((tile[0] + _DX[h], tile[1] + _DY[h]), h))
                job.drv.append((job.starts[-1], path))
                mine.append((side, job.starts[-1]))
            else:
                job.goals.append(((tile[0] - _DX[h], tile[1] - _DY[h]), h))
                mine.append((side, job.goals[-1]))
            job.owners.append(f"{path}.{role}")
    _literal_ends(flat, lay, cat, out, fluids)
    _drill_drops(lay, cat, out)
    if getattr(flat, "pairs", None):
        from . import pairs
        pairs.merge(flat, out)
    if _M2M:
        _mark_m2m(flat, cat, out)
    _widen(flat, cat, out)
    for net, job in out.items():
        if job.skip is not None or job.fluid or _abuts(job):
            continue
        if not job.drivers():
            job.skip = "no driver role is placed"
        elif not job.consumers():
            job.skip = "no consumer role is placed"
    return out


def _drop_tile(cat, e):
    """The tile an entity puts its output down on itself (``Catalog.drop_tile``: a drill, a
    recycler), or None (none, a pumpjack, or an undirected one)."""
    if not cat.has_entity(e.name) or _heading(e) is None:
        return None
    return cat.drop_tile(e.name, e.x, e.y, _heading(e) * 4)


class _Drop(tuple):
    """A drop tile; ``hand``: an inserter may take the output instead (a recycler)."""
    hand = False


def _drill_drops(lay, cat, out):
    """3.0: every machine driver with a drop tile (a drill, a recycler) puts its output down
    there, no inserter. One whose drop tile lies in a consumer of its net feeds that consumer
    straight (a drill into a furnace or a chest): nothing to lay between them. A recycler whose
    drop tile something else stands on is emptied by an inserter."""
    taken = None
    for job in out.values():
        for key, _rect in job.sources:
            e = lay.entities.get(key)
            t = None if e is None else _drop_tile(cat, e)
            if t is None:
                continue
            into = [k for k, (x0, y0, x1, y1) in job.taps if x0 <= t[0] < x1 and y0 <= t[1] < y1]
            hand = cat.kind(e.name) != "mining-drill"   # an inserter may empty a recycler
            if into:
                job.direct.append((key, into[0]))
                continue
            if hand:
                if taken is None:
                    taken = {u for x in lay.entities.values() for u in fkpmod.tiles_of(cat, x)}
                if t in taken:
                    continue
            job.drops[key] = _Drop(t)
            job.drops[key].hand = hand
        if not job.direct:
            continue
        gone = {d for d, _c in job.direct}
        job.sources = [s for s in job.sources if s[0] not in gone]
        job.drv = [d for d in job.drv if d[0][0] not in gone]
        if not job.sources and not job.starts:
            fed = {c for _d, c in job.direct}
            job.taps = [t for t in job.taps if t[0] not in fed]


def _literal_ends(flat, lay, cat, out, fluids):
    """2.1 (PLAN-2.1 H): a Literal's named machine on a net is an end the router reaches from
    outside the Literal's entities, as it reaches a single machine: an inserter beside it, on a
    free tile. Only named machines: the rest of a Literal is laid by hand, its own carriers."""
    for path, names in sorted(getattr(flat, "literal_names", {}).items()):
        if path not in lay.insts:
            continue
        for name, key in sorted(names.items()):
            if key not in lay.entities:
                continue
            m = flat.machines.get(key)
            if m is not None and m.extra.get("belt_end"):
                _belt_end(flat, lay, out, fluids, path, name, key)
                continue
            if m is not None and m.extra.get("pipe_end"):
                continue                         # a fluid port: the fluid plug-in's end
            ts = fkpmod.tiles_of(cat, lay.entities[key])
            rect = (min(t[0] for t in ts), min(t[1] for t in ts),
                    max(t[0] for t in ts) + 1, max(t[1] for t in ts) + 1)
            for side, hold in (("out", flat._out_hold), ("in", flat._in_hold)):
                for net in sorted({n for (k, _i), ns in hold.items() if k == key for n in ns}):
                    if net in fluids or net not in flat.nets:
                        continue
                    job = out.setdefault(net, NetJob(net))
                    owner = f"{path}.{name}"
                    mine = job.anchors.setdefault(owner, [])
                    (job.sources if side == "out" else job.taps).append((key, rect))
                    if side == "out":
                        job.drv.append(((key, rect), path))
                    mine.append((side, key, rect))
                    if owner not in job.owners:
                        job.owners.append(owner)


def _belt_end(flat, lay, out, fluids, path, name, key):
    """2.1.19: a Literal's named belt on a net, as a port's edge is: a net into it ends on the tile
    behind it flowing its way, a net out of it starts on the tile in front."""
    e = lay.entities[key]
    h = _heading(e)
    tile = (math.floor(float(e.x)), math.floor(float(e.y)))
    edge = port_side(flat, lay, key)
    ug = flat.machines[key].extra.get("ug") if key in flat.machines else None
    for side, hold in (("out", flat._out_hold), ("in", flat._in_hold)):
        if ug is not None and edge is None and (ug == "in") == (side == "out"):
            continue      # 3.0: an underground's entry only takes, its exit only gives
        for net in sorted({n for (k, _i), ns in hold.items() if k == key for n in ns}):
            if net in fluids or net not in flat.nets:
                continue
            job = out.setdefault(net, NetJob(net))
            owner = f"{path}.{name}"
            mine = job.anchors.setdefault(owner, [])
            if edge is not None:
                # 3.0, a Literal port: the net meets the carrier across its frame edge
                t = (tile[0] + _DX[edge], tile[1] + _DY[edge])
                if side == "out":
                    job.starts.append((t, edge))
                    job.drv.append((job.starts[-1], path))
                    mine.append((side, job.starts[-1]))
                else:
                    job.goals.append((t, (edge + 2) % 4))
                    mine.append((side, job.goals[-1]))
            elif side == "out":
                job.starts.append(((tile[0] + _DX[h], tile[1] + _DY[h]), h))
                job.drv.append((job.starts[-1], path))
                mine.append((side, job.starts[-1]))
            else:
                job.goals.append(((tile[0] - _DX[h], tile[1] - _DY[h]), h))
                mine.append((side, job.goals[-1]))
            if owner not in job.owners:
                job.owners.append(owner)


def port_side(flat, lay, key):
    """3.0: the heading (N E S W = 0..3) out of a Literal port's frame edge, turned with the
    Literal, or None when ``key`` is no Literal port."""
    m = flat.machines.get(key)
    side = m.extra.get("lit_port") if m is not None else None
    if side is None:
        return None
    from .literal import SIDES
    rec = lay.insts.get(key.rsplit("/", 1)[0])
    return (SIDES[side] + (int(rec.rot) if rec is not None else 0)) % 4


# -- the ground -----------------------------------------------------------------------------------
def _heading(e):
    """N E S W = 0..3 of a directed entity (no direction counts as north, as in connect)."""
    return _H.get(int(e.dir or 0) % 16)


def _is_belt(cat, e):
    return cat.has_entity(e.name) and cat.kind(e.name) in _BELT_KINDS


def _belt_fronts(cat, ents):
    """{tile: net} for every tile a belt, an underground exit or a splitter points into (any
    tier; an underground of unknown type counts as an exit)."""
    out = {}
    for e in ents:
        if not _is_belt(cat, e) or e.attrs.get("ug") == "in":
            continue
        h = _heading(e)
        if h is None:
            continue
        for tx, ty in fkpmod.tiles_of(cat, e):
            out.setdefault((tx + _DX[h], ty + _DY[h]), e.net)
    return out


def _hand_tiles(cat, ents):
    """{tile: net} for every tile an inserter picks from or drops onto, and every tile a drill
    drops onto (net None): another net's belt there would be picked from or fed."""
    out = {}
    for e in ents:
        if not cat.has_entity(e.name):
            continue
        kind = cat.kind(e.name)
        if kind == "inserter":
            (tile,) = fkpmod.tiles_of(cat, e)
            got = armsmod.tiles(e.name, e.dir, e.attrs, tile)
            for t in got or ():
                out.setdefault(t, e.net)
        else:
            t = _drop_tile(cat, e)
            if t is not None:          # its own net may still lay on it: it is that net's end
                out.setdefault(t, None)
    return out


def _ug_spans(cat, ents):
    """Per axis, the tiles no new same-axis tunnel may cover (the game would pair it wrong):
    every underground end (any tier, paired or alone; ``ug`` known or not) and the hidden tiles of
    every tunnel. A tunnel is paired as the game pairs it: an entrance looks ahead, up to its
    tier's reach, for the first end of the same name facing the same way; an exit there makes the
    tunnel. An end of unknown type counts as an entrance, and as an exit when one looks for it."""
    spans = {0: set(), 1: set()}
    ugs = {}
    for e in ents:
        if not cat.has_entity(e.name) or cat.kind(e.name) != "underground-belt":
            continue
        h = _heading(e)
        if h is None:
            continue
        t = (math.floor(e.x), math.floor(e.y))
        ugs[t] = (e, h)
        spans[h % 2].add(t)
    for (x, y), (e, h) in ugs.items():
        if e.attrs.get("ug") == "out":
            continue
        reach = int(cat.entity(e.name).get("max_distance") or 5)
        for k in range(1, reach + 1):
            o = ugs.get((x + _DX[h] * k, y + _DY[h] * k))
            if o is None or o[0].name != e.name or o[1] != h:
                continue                                  # passed through
            if o[0].attrs.get("ug") != "in":
                spans[h % 2] |= {(x + _DX[h] * j, y + _DY[h] * j) for j in range(k + 1)}
            break
    return spans


def _ent_ground(cat, e):
    """(tiles, front tiles, hand tiles, hand owner is the net) of one entity: what it stands on,
    what its belt points into (``_belt_fronts``), what its hand picks from or drops onto
    (``_hand_tiles``; a drill's drop has no net). Remembered on the catalog per entity value."""
    memo = cat.__dict__.setdefault("_route_ground_memo", {})
    key = (e.name, e.x, e.y, e.dir, e.attrs.get("ug"))
    got = memo.get(key)
    if got is None:
        got = memo[key] = (tuple(fkpmod.tiles_of(cat, e)), tuple(_belt_fronts(cat, [e])),
                           tuple(_hand_tiles(cat, [e])), cat.has_entity(e.name)
                           and cat.kind(e.name) == "inserter")
    return got


def _ground(cat, ents, frames, js, net, terrain=(), box=None):
    """``box`` (2.1.33): only the other nets' ends inside it count (a search never leaves it),
    from an index of every net's ends built once for ``js``."""
    blocked = set(frames) | set(terrain)
    fronts, hands = {}, {}
    for e in ents:         # as _belt_fronts and _hand_tiles: the first entity on a tile owns it
        ts, fs, hs, ins = _ent_ground(cat, e)
        blocked.update(ts)
        for t in fs:
            fronts.setdefault(t, e.net)
        for t in hs:
            hands.setdefault(t, e.net if ins else None)
    for tile, owner in fronts.items():
        if owner != net:
            blocked.add(tile)
    for tile, owner in hands.items():
        if owner != net:
            blocked.add(tile)
    if box is None:
        for other, job in js.items():
            if other != net:
                blocked |= set(job.ends())
    else:
        index = _ends_index(js)
        x0, y0, x1, y1 = box
        if (x1 - x0) * (y1 - y0) < len(index):
            got = ((x, y) for x in range(x0, x1) for y in range(y0, y1) if (x, y) in index)
        else:
            got = (t for t in index if x0 <= t[0] < x1 and y0 <= t[1] < y1)
        for t in got:
            if index[t] - {net}:
                blocked.add(t)
    return blocked - set(js[net].ends())


_ENDS = [None, None]


def _ends_index(js):
    """{tile: the nets that end there} for ``js``, built once per jobs dict (2.1.33)."""
    if _ENDS[0] is not js:
        index = {}
        for name, job in js.items():
            for t in job.ends():
                index.setdefault(tuple(t), set()).add(name)
        _ENDS[0], _ENDS[1] = js, index
    return _ENDS[1]


#: 2.1.33 (lab FRICTION 35): how far past a net's search box an entity can still matter (its
#: tiles, a belt's front, a long hand's reach, an underground's far end: turbo reaches 11)
_CLIP = 16


def _clipped(ents, frames, box):
    """``ents`` and ``frames`` near ``box`` only: a search is blocked outside its box anyway.
    Every net built its ground from every entity and frame tile of the whole layout, so a
    megabase loop of 1300 nets routed 28+ minutes where each bay alone took 20-40 s."""
    x0, y0, x1, y1 = box
    a0, b0, a1, b1 = x0 - _CLIP, y0 - _CLIP, x1 + _CLIP, y1 + _CLIP
    near = [e for e in ents if a0 <= e.x < a1 and b0 <= e.y < b1]
    if len(near) == len(ents):
        near = ents
    if (a1 - a0) * (b1 - b0) < len(frames):
        fr = {(x, y) for x in range(a0, a1) for y in range(b0, b1) if (x, y) in frames}
    else:
        fr = {t for t in frames if a0 <= t[0] < a1 and b0 <= t[1] < b1}
    return near, fr


class _GroundParts:
    """``_ground`` for many nets of one layout: the parts every net shares, worked out once
    (2.1.32: ``_direct_claims`` worked them out again for every net, most of a megabase grid's
    route setup). ``of(net)`` == ``_ground(cat, ents, frames, js, net, terrain)``."""

    def __init__(self, cat, ents, frames, js, terrain=()):
        self.base = set(frames) | set(terrain)
        for e in ents:
            self.base |= fkpmod.tiles_of(cat, e)
        self.parts = []                         # (every tile, {owner: its tiles})
        for tiles in (_belt_fronts(cat, ents), _hand_tiles(cat, ents)):
            mine = {}
            for tile, owner in tiles.items():
                mine.setdefault(owner, set()).add(tile)
            self.parts.append((set(tiles), mine))
        self.ends = set()                       # every net's ends: its own are taken off after
        for job in js.values():
            self.ends |= set(job.ends())
        self.js = js

    def grid(self, net, box):
        """A ground for one net's direct inserters: ``blocked(t)`` as ``_Bounded(of(net), box)``
        has it, tile by tile, with no set of the whole frame built for the net."""
        parts, own = self, set(self.js[net].ends())
        x0, y0, x1, y1 = box

        class _View:
            def blocked(self, t):
                if not (x0 <= t[0] < x1 and y0 <= t[1] < y1):
                    return True
                if t in own:
                    return False
                if t in parts.base or t in parts.ends:
                    return True
                return any(t in every and t not in mine.get(net, ()) for every, mine in parts.parts)
        return _View()

    def of(self, net):
        blocked = self.base | self.ends
        for every, mine in self.parts:
            blocked |= every - mine.get(net, set())
        return blocked - set(self.js[net].ends())


# -- promotion: another net's belt becomes an underground pair (2.0.78) ----------------------------
#: Rory, 2026-10-31: "a mutation option for a belt that is blocking a path to be modified into an
#: underground. Even if its pinned ... as long as that does not affect inserter connectivity".
#: A net that finds no route is tried once more with every promotable tile of another net's
#: route as ground it may cross; each tile its route takes turns that net's three belts there
#: (behind, on, ahead) into an underground entrance and exit, the same items on the same lanes.
_PROMOTE_NEAR = 6        # no underground end of the run's axis this close along its line


def _promotable(cat, ents, js, net, frames, terrain):
    """{tile: (axis, (behind, on, ahead) entities, underground name)} for every tile of another
    net's route belt that may become the hidden middle of an underground pair: a straight run of
    five plain belts of one name, one owner and one heading (two behind, two ahead), the three in
    the middle touched by no inserter or drill and fed by nothing but the belt behind them, no
    underground end of the same axis within ``_PROMOTE_NEAR`` tiles along the line, and the tile
    in no frame, terrain or net end."""
    belts, into = {}, {}
    for e in ents:
        if not _is_belt(cat, e):
            continue
        h = _heading(e)
        if cat.kind(e.name) == "transport-belt":
            (t,) = fkpmod.tiles_of(cat, e)
            belts[t] = (e, h)
        if h is None or e.attrs.get("ug") == "in":
            continue
        for tx, ty in fkpmod.tiles_of(cat, e):
            f = (tx + _DX[h], ty + _DY[h])
            into[f] = into.get(f, 0) + 1
    hands = set(_hand_tiles(cat, ents))
    ugs = {}
    for e in ents:
        if cat.has_entity(e.name) and cat.kind(e.name) == "underground-belt" \
                and _heading(e) is not None:
            ugs.setdefault(_heading(e) % 2, set()).add((math.floor(e.x), math.floor(e.y)))
    ends = set()
    for other, job in js.items():
        ends |= set(job.ends())
    out = {}
    for t, (e, h) in belts.items():
        if h is None or not e.owner.startswith("~") or e.owner[1:] == net \
                or t in frames or t in terrain or t in ends:
            continue
        ug = e.name.replace("transport-belt", "underground-belt")
        if not cat.has_entity(ug):
            continue
        line = [(t[0] + _DX[h] * k, t[1] + _DY[h] * k) for k in (-2, -1, 0, 1, 2)]
        run = [belts.get(q) for q in line]
        if any(r is None or r[1] != h or r[0].name != e.name or r[0].owner != e.owner
               for r in run):
            continue
        if any(q in hands or into.get(q, 0) != 1 for q in line[1:4]):
            continue
        near = {(t[0] + _DX[h] * k, t[1] + _DY[h] * k)
                for k in range(-_PROMOTE_NEAR, _PROMOTE_NEAR + 1)}
        if near & ugs.get(h % 2, set()):
            continue
        out[t] = (h % 2, tuple(r[0] for r in run[1:4]), ug)
    return out


def _promote(cat, ents, got, promote):
    """The swaps ``got`` (a route laid with ``promote``'s tiles as ground) takes: [(removed
    keys, new entities, text)], one per tile its carriers stand on; None when two of them meet on
    one run (their pairs would overlap)."""
    mine = set()
    for e in got:
        mine |= fkpmod.tiles_of(cat, e)
    used = sorted(t for t in promote if t in mine)
    taken, swaps = set(), []
    keys = {e.key for e in ents}
    for t in used:
        _axis, (a, m, b), ug = promote[t]
        tiles = {(math.floor(x.x), math.floor(x.y)) for x in (a, m, b)}
        if tiles & taken:
            return None
        taken |= tiles
        pre = re.fullmatch(r"([lr]?)([a-z])(\d+)", a.key.rsplit("/", 1)[1])
        prefix = pre[1] if pre else ""
        new = [fkpmod.Entity(f"{a.owner}/{prefix}u", ug, a.x, a.y, a.dir, dict(a.attrs, ug="in")),
               fkpmod.Entity(f"{a.owner}/{prefix}u", ug, b.x, b.y, b.dir, dict(b.attrs, ug="out"))]
        new = _fresh_keys(new, a.owner[1:], {k for k in keys if k.startswith(a.owner + "/")},
                          prefix)
        keys |= {e.key for e in new}
        swaps.append(((a.key, m.key, b.key), new,
                      f"{a.owner[1:]} crossed at {t[0]},{t[1]} ({a.key}..{b.key}: an underground "
                      f"pair now)"))
    return swaps


def _route_promoted(cat, job, ents, frames, js, box, limit, terrain, avoid, allow):
    """``_route_one`` again with other nets' promotable belt tiles as ground. -> (carriers,
    report text, swaps) or None."""
    if job.parts or job.fluid:
        return None
    promote = _promotable(cat, ents, js, job.net, frames, terrain)
    promote = {t: p for t, p in promote.items() if t not in avoid}
    if not promote:
        return None
    spans = _ug_spans(cat, ents)
    spans = {0: set(spans[0]), 1: set(spans[1])}
    for t, (axis, run, _ug) in promote.items():
        spans[axis] |= {(math.floor(x.x), math.floor(x.y)) for x in run}
    free = set(promote)
    shown = [fkpmod.Entity(e.key, e.name, e.x, e.y, e.dir, e.attrs) for e in ents
             if not (fkpmod.tiles_of(cat, e) & free and _is_belt(cat, e))]
    got, how = _route_one(cat, job, shown, frames, js, box, limit, terrain, avoid, spans, allow,
                          promoted=free)
    if got is None:
        return None
    swaps = _promote(cat, ents, got, promote)
    if swaps is None:
        return None
    return got, "; ".join([how] + [s[2] for s in swaps]), swaps


def _swap_added(added, swaps):
    """The carriers of nets routed this pass with the promoted belts swapped for their pairs."""
    gone = {k for s in swaps for k in s[0]}
    for other in added:
        if any(e.key in gone for e in added[other]):
            added[other] = [e for e in added[other] if e.key not in gone] + \
                [e for s in swaps for e in s[1] if e.owner == f"~{other}"]


def _belt_tiles(cat, ents):
    """Every tile a belt, an underground or a splitter stands on (a merge must not feed one)."""
    out = set()
    for e in ents:
        if _is_belt(cat, e):
            out |= fkpmod.tiles_of(cat, e)
    return out


# -- trees ----------------------------------------------------------------------------------------
def _carriers(job, tree_ents, net):
    """The tree's plan entities as keyed .fkp entities, in the tree's own order."""
    count = {"b": 0, "u": 0, "s": 0, "i": 0}
    out = []
    for p in tree_ents:
        letter = {"transport-belt": "b", "underground-belt": "u", "splitter": "s"}.get(p["name"], "i")
        key = f"~{net}/{letter}{count[letter]}"
        count[letter] += 1
        attrs = {"net": net}
        if p.get("belt_to_ground_type"):
            attrs["ug"] = "in" if p["belt_to_ground_type"] == "input" else "out"
        if p.get("config"):                        # 2.1.24: an m2m split's output priority
            attrs["config"] = p["config"]
        for k in ("pick", "drop"):                 # 3.0: an inserter's own vectors
            if p.get(k):
                attrs[k] = p[k]
        x, y = p["x"], p["y"]
        out.append(fkpmod.Entity(key, p["name"], _clean(x), _clean(y), int(p["direction"]), attrs))
    return out


def _clean(v):
    return int(v) if float(v).is_integer() else v


#: 2.0.76: driver machines that hold items besides their nets' (a requester of several items): the
#: inserter that loads from one needs a filter, which a long-handed one can't take, so they get
#: fast inserters first (``_route_layout`` sets it)
_FILTERED = frozenset()

#: 3.0 (Rory, 11-01: "a net needs two or 3 inserters on the same machine, a common case in seablock
#: earlygame"): {(net, machine): k} for a machine one hand can't keep up with, the best hand the
#: force can build at what the design asks of it (``_tap_counts``); ``_grow`` asks the tree for k
#: inserters there, and one where k don't fit
_TAPS = {}
TAPS_MAX = 3
#: 3.0 (Rory, 11-02: "ideally our throughput fix is more general"): what flows through each
#: machine's side of a net, {(net, machine, "in"|"out"): items/s}, and what one hand of the best
#: the force can build carries: every inserter link the router lays (a tap off a belt, a drop onto
#: one, a machine straight into a machine) is sized by them (``_link_k``)
_NEED = {}
_HAND = 0.0
#: 3.0 (Bob's adjustable inserters, ``fkcore.arms``): the vectors the force may set an inserter to,
#: or None (the base game). ``_route_layout`` sets it from the build's stock read; nothing read:
#: the pack's base set (range 1, 4 ways). Taps and direct inserters then turn corners, and reach
#: further with a short hand, before a long-handed one is laid.
_ARMS = None
INF_RATE = float("inf")


def _link_k(net, src=None, dst=None, need=None):
    """How many inserters one link of ``net`` needs: what flows through it (the lesser of what
    machine ``src`` puts on the net and what ``dst`` takes from it; ``need`` when given) over what
    one hand of the best the force can build carries, 1 to TAPS_MAX. 1 when nothing is known."""
    r = need if need is not None else min(_NEED.get((net, src, "out"), INF_RATE),
                                          _NEED.get((net, dst, "in"), INF_RATE))
    if not _HAND or not 0 < r < INF_RATE:
        return 1
    return max(1, min(TAPS_MAX, math.ceil(r / _HAND - 1e-9)))


def _sizing(flat, cat):
    """``_tap_counts`` and the flows behind it: (taps, need, hand) for ``_TAPS``, ``_NEED`` and
    ``_HAND``. Nothing read: ({}, {}, 0.0)."""
    need, hand = {}, [0.0]
    taps = _tap_counts(flat, cat, need, hand)
    return taps, need, hand[0]


def _tap_counts(flat, cat, need=None, hand=None):
    """{(net, machine): k} for every machine on a belt net whose take (a consumer) or put (a
    driver) is more than one inserter of the best hand the force can build carries: k of them, at
    most TAPS_MAX. Nothing read (a lab, a paper game): {} (one hand each; ``hands`` raises it).
    ``need`` (a dict) gets every machine's flow, ``hand`` (a one-item list) the one hand's rate."""
    from . import avail as availmod
    from . import lanes as lanesmod
    from . import routed as routedmod
    if availmod.snapshot().sup is None:
        return {}
    rungs = [h for h in availmod.HANDS if availmod.buildable(h)]
    best = rungs[-1] if rungs else availmod.HANDS[0]
    take = lanesmod.inserter_rate(best, cat=cat)
    put = lanesmod.inserter_rate(best, onto_belt=True, cat=cat)
    if not take or not put:
        return {}
    if hand is not None:
        hand[0] = take
    model = routedmod.sizing_model(flat)
    out = {}
    for name, net in flat.nets.items():
        want = {}
        for key, item in net.consumers:
            m = flat.machines.get(key)
            if m is None or cat.is_fluid(item):
                continue
            r = m.needs.get(item, 0.0) * model.util.get(key, 1.0)
            if not r and m.sink:         # a chest at a net's end: its share of what reaches sinks
                st = model.nets.get((name, item))
                n = sum(1 for k, i in net.consumers if i == item and flat.machines[k].sink)
                r = st.to_sinks / max(1, n) if st is not None and st.to_sinks < INF_RATE else 0.0
            if 0 < r < INF_RATE:
                want[(key, "in")] = want.get((key, "in"), 0.0) + r
        for key, item in net.drivers:
            m = flat.machines.get(key)
            if m is None or cat.is_fluid(item):
                continue
            r = m.makes.get(item, 0.0)
            if r >= INF_RATE:
                st = model.nets.get((name, item))
                n = sum(1 for _k, i in net.drivers if i == item)
                r = st.delivered / max(1, n) if st is not None and st.delivered < INF_RATE else 0.0
            else:
                r *= model.util.get(key, 1.0)
            if r > 0:
                want[(key, "out")] = want.get((key, "out"), 0.0) + r
        for (key, side), r in want.items():
            if need is not None:
                need[(name, key, side)] = r
            k = min(TAPS_MAX, math.ceil(r / (take if side == "in" else put) - 1e-9))
            if k > 1:
                out[(name, key)] = k
    return out


def _grow(grid, root, fixed, machines, limit, sources=False, tap=TapSink, drops=None, net=None,
          cap=TAPS_MAX):
    """grow() towards ``fixed`` sinks and ``machines`` [(key, rect)] by direct insertion: fast
    inserters, or long-handed ones where only those reach. ``sources``: the machines are drivers,
    and one in ``_FILTERED`` is tried with fast inserters alone before a long-handed one may load
    it (2.0.76). ``tap``: the machines' sink kind (2.1 m2m: ``m2m.LeftTap``). ``drops``: {key:
    tile} of the driver machines that drop on a tile (drills: ``DropSink``). -> (result, sinks)."""
    drops = drops or {}
    passes = list(_HANDS)
    if sources and any(key in _FILTERED for key, _r in machines):
        passes = [_HANDS[0], None] + passes[1:]
    # two drills facing one belt tile (a pair) drop on the same tile: one sink for both
    first = {}
    for key, _r in machines:
        if key in drops:
            first.setdefault(drops[key], key)
    machines = [(k, r) for k, r in machines if k not in drops or first[drops[k]] == k]
    for hands in passes:
        sinks = fixed + [DropSink(key, drops[key]) if key in drops else
                         tap(key, rect, min(_TAPS.get((net, key), 1), cap),
                             _HANDS[0] if hands is None and key in _FILTERED
                             else hands or _HANDS[-1], arms=_ARMS) for key, rect in machines]
        res = grow(grid, root, sinks, limit=limit)
        if not res["problems"] or not machines:
            break
    if res["problems"] and cap > 1 and any(_TAPS.get((net, k), 1) > 1 for k, _r in machines):
        # 3.0: k inserters on a machine don't fit here: one fewer, down to one each as before
        # (too-thin says what that costs)
        return _grow(grid, root, fixed, machines, limit, sources, tap, drops, net,
                     min(cap, max(_TAPS.get((net, k), 1) for k, _r in machines)) - 1)
    if res["problems"] and any(getattr(t, "hand", False) for t in drops.values()):
        # 3.0: a recycler whose drop tile no belt reaches is emptied by an inserter instead
        return _grow(grid, root, fixed, machines, limit, sources, tap,
                     {k: t for k, t in drops.items() if not getattr(t, "hand", False)}, net, cap)
    return res, sinks


def _tree_job(cat, job, blocked, spans, clear, box, limit):
    """forward or reverse: one tree. -> (plan entities, report text) or (None, reason)."""
    reverse = job.mode == "reverse"
    grid = _Bounded(blocked, box, spans, clear if reverse else None)
    if job.lanes and not reverse:
        from . import lanephys
        grid.lane_ok = {t: lanephys.tile_mask(need, [(h * 4, held) for h, held in feeds])
                        for t, (need, feeds) in job.lanes.items()}
    if reverse:
        (gtile, gh), = job.goals
        root = (gtile[0], gtile[1], (gh + 2) % 4, 0)
        fixed = [FixedSink(f"driver{i}", t, (h + 2) % 4) for i, (t, h) in enumerate(job.starts)]
        machines = job.sources
    else:
        (stile, sh), = job.starts
        root = (stile[0], stile[1], sh, 0)
        fixed = [FixedSink(f"consumer{i}", t, h) for i, (t, h) in enumerate(job.goals)]
        machines = job.taps
    if grid.blocked((root[0], root[1])):
        return None, f"its {'end' if reverse else 'start'} tile {root[0]},{root[1]} is taken"
    res, sinks = _grow(grid, root, fixed, machines, limit, sources=reverse,
                       drops=job.drops if reverse else None, net=job.net)
    if res["problems"]:
        return None, "; ".join(res["problems"][:3])
    c = res["counts"]
    how = (f"{c['belts']} belts, {c['undergrounds']} underground ends, {c['splitters']} "
           f"{'merges' if reverse else 'splitters'}" + (f", {c['taps']} inserters" if c["taps"]
                                                         else ""))
    return res["tree"].entities(sinks, net=job.net, reverse=reverse), how


# -- the hub --------------------------------------------------------------------------------------
def _native_box_grid(grid):
    """The grid's fkroute.NativeGrid when the native kernel may walk it (a _Bounded), else None."""
    if not steinermod.native_on():
        return None
    try:
        ng = steinermod._native_grid(grid)
    except (TypeError, OverflowError):
        return None
    return ng if ng is not None and getattr(grid, "box", None) is not None and \
        type(grid).blocked is _Bounded.blocked else None


def _int_tiles(ts):
    out = []
    for t in ts:
        if type(t[0]) is not int or type(t[1]) is not int:
            return None
        out.append((t[0], t[1]))
    return out


def _walk(grid, seeds, reach=5):
    """Belt distance over free ground from ``seeds``: a step to a free tile, or a tunnel under
    whatever blocks it to the first free tile within ``reach`` (as the search may jump).
    Runs in fkroute when it can (the same dict, in the same order)."""
    seeds = list(seeds)
    ng = _native_box_grid(grid)
    ints = _int_tiles(seeds) if ng is not None else None
    if ints is not None:
        got = dict(steinermod._native.walk(ng, ints, reach))
        if os.environ.get("FK_NATIVE") == "check":
            py = _walk_py(grid, seeds, reach)
            if list(py.items()) != list(got.items()):
                raise AssertionError("fkroute.walk differs from _walk_py")
        return got
    return _walk_py(grid, seeds, reach)


def _walk_py(grid, seeds, reach=5):
    dist = {s: 0 for s in seeds}
    todo = [(0, s) for s in sorted(dist)]
    while todo:
        d, t = heapq.heappop(todo)
        if d > dist[t]:
            continue
        for h in range(4):
            for j in range(1, reach + 1):
                u = (t[0] + _DX[h] * j, t[1] + _DY[h] * j)
                if not grid.blocked(u):
                    if d + j < dist.get(u, d + j + 1):
                        dist[u] = d + j
                        heapq.heappush(todo, (d + j, u))
                    break
                if not grid.box[0] <= u[0] < grid.box[2] or not grid.box[1] <= u[1] < grid.box[3]:
                    break
    return dist


def _beside(grid, rect):
    """The free tiles beside a machine's rectangle (where its inserter could stand)."""
    x0, y0, x1, y1 = rect
    ring = [(x, y0 - 1) for x in range(x0, x1)] + [(x, y1) for x in range(x0, x1)] + \
           [(x0 - 1, y) for y in range(y0, y1)] + [(x1, y) for y in range(y0, y1)]
    return [t for t in ring if not grid.blocked(t)]


def _hubs(grid, job, most=_HUBS):
    """[(H, f)] hub candidates, best first: H and F = H + f free, reached from every end; ranked
    by the summed walking distance from every driver to H and from F to every consumer; a hub
    within 2 tiles of an end on a side with several ends ranks last; picked 3 tiles apart."""
    ends = set(job.ends())
    near_d = [[t] for t, _h in job.starts] + [[job.drops[k]] if k in job.drops else
                                              _beside(grid, r) for k, r in job.sources]
    near_c = [[t] for t, _h in job.goals] + [_beside(grid, r) for _k, r in job.taps]
    ng = _native_box_grid(grid)
    if ng is not None:
        nd = [_int_tiles(s) for s in near_d]
        nc = [_int_tiles(s) for s in near_c]
        ne = _int_tiles(ends)
        if ne is not None and all(s is not None for s in nd + nc):
            got = [(tuple(t), f) for t, f in steinermod._native.hubs(ng, nd, nc, ne, most)]
            if os.environ.get("FK_NATIVE") == "check":
                py = _hubs_py(grid, ends, near_d, near_c, most)
                if py != got:
                    raise AssertionError(f"fkroute.hubs differs from _hubs_py: {got} != {py}")
            return got
    return _hubs_py(grid, ends, near_d, near_c, most)


def _hubs_py(grid, ends, near_d, near_c, most):
    dd = [_walk_py(grid, s) for s in near_d]
    dc = [_walk_py(grid, s) for s in near_c]
    if not dd or not dc:
        return []
    common = set.intersection(*(set(d) for d in dd + dc)) - ends
    rows = []
    for t in common:
        a = [d[t] for d in dd]
        for f in range(4):
            u = (t[0] + _DX[f], t[1] + _DY[f])
            if u not in common:
                continue
            b = [d[u] for d in dc]
            short = (len(a) > 1 and min(a) < 3) or (len(b) > 1 and min(b) < 3)
            rows.append((short, sum(a) + sum(b), t[1], t[0], f))
    rows.sort()
    out = []
    for _short, _score, y, x, f in rows:
        if any(abs(x - o[0]) + abs(y - o[1]) < 3 for o, _f in out):
            continue
        out.append(((x, y), f))
        if len(out) >= most:
            break
    return out


def _hub_trees(cat, job, blocked, spans, clear, box, limit, hub, f, tap=TapSink):
    """The reversed tree from H to every driver, then the forward tree from F = H + f to every
    consumer on what is left. ``tap``: the driver machines' sink kind. -> (cost, plan, counts,
    counts) or a reason."""
    net = job.net
    fx, fy = hub[0] + _DX[f], hub[1] + _DY[f]
    rgrid = _Bounded(set(blocked) | {(fx, fy)}, box, spans, clear)
    fixed = [FixedSink(f"driver{i}", t, (h + 2) % 4) for i, (t, h) in enumerate(job.starts)]
    rres, rsinks = _grow(rgrid, (hub[0], hub[1], (f + 2) % 4, 0), fixed, job.sources, limit,
                         sources=True, tap=tap, drops=job.drops, net=job.net)
    if rres["problems"]:
        return "drivers: " + "; ".join(rres["problems"][:2])
    rtree = rres["tree"]
    rplan = rtree.entities(rsinks, net=net, reverse=True)
    laid = _carriers(job, rplan, net)
    fb = set(blocked) | set(rtree.tiles())
    for e in laid:
        fb |= fkpmod.tiles_of(cat, e)
    fb |= set(_belt_fronts(cat, laid)) - {(fx, fy)}
    rs = rtree.spans()
    fgrid = _Bounded(fb, box, {a: set(spans[a]) | rs[a] for a in (0, 1)})
    fixed = [FixedSink(f"consumer{i}", t, h) for i, (t, h) in enumerate(job.goals)]
    fres, fsinks = _grow(fgrid, (fx, fy, f, 0), fixed, job.taps, limit, net=job.net)
    if fres["problems"]:
        return "consumers: " + "; ".join(fres["problems"][:2])
    ftree = fres["tree"]
    plan = rplan + ftree.entities(fsinks, net=net)
    return rtree.cost() + ftree.cost(), plan, rres["counts"], fres["counts"]


def _arm_plan(name, q, arm, role="ins"):
    """A plan inserter on tile ``q`` with its own (pick, drop) vectors (3.0, ``fkcore.arms``): it
    faces its pickup, and keeps the vectors that are not that direction's own."""
    direction = armsmod.heading(arm[0])
    return dict({"name": name, "x": q[0] + 0.5, "y": q[1] + 0.5, "direction": direction,
                 "role": role}, **armsmod.canon(name, direction, arm[0], arm[1]))


def _passes():
    """The hand passes of a direct inserter: ``_HANDS``, with the force's vectors (``_ARMS``)
    after the first: (names, None) for a straight swing, (names, pairs) for vectors."""
    out = [(_HANDS[0], None)]
    if _ARMS is not None:
        short = [n for n in _HANDS[0] if _REACH.get(n) == 1]
        if short:
            out.append(((short[0],), armsmod.pairs(_ARMS, straight=False)))
    return out + [(hands, None) for hands in _HANDS[1:]]


def _direct_pair(src, dst, grid, taken=()):
    """An inserter from machine rect ``src`` into machine rect ``dst`` on a free tile not in
    ``taken`` (one this plan already holds), if one fits: a short hand first, then (3.0) one with
    the force's vectors (round a corner, further out), else a long one."""
    for hands, arms in _passes():
        if arms is not None:
            for pick, drop in arms:
                for x in range(src[0], src[2]):
                    for y in range(src[1], src[3]):
                        q = (x - pick[0], y - pick[1])
                        d = (q[0] + drop[0], q[1] + drop[1])
                        if q in taken or grid.blocked(q) or not (dst[0] <= d[0] < dst[2]
                                                                 and dst[1] <= d[1] < dst[3]):
                            continue
                        return _arm_plan(hands[0], q, (pick, drop))
            continue
        for name in hands:
            r = _REACH[name]
            for x in range(src[0], src[2]):
                for y in range(src[1], src[3]):
                    for g in range(4):           # the inserter faces its pickup side, g
                        q = (x - _DX[g] * r, y - _DY[g] * r)
                        drop = (q[0] - _DX[g] * r, q[1] - _DY[g] * r)
                        if q in taken or grid.blocked(q) or not (dst[0] <= drop[0] < dst[2]
                                                                 and dst[1] <= drop[1] < dst[3]):
                            continue
                        return {"name": name, "x": q[0] + 0.5, "y": q[1] + 0.5,
                                "direction": g * 4, "role": "ins"}
    return None


def _direct_link(src, dst, grid, taken=(), k=1):
    """Up to ``k`` inserters from machine rect ``src`` into ``dst`` (``_direct_pair``, each on a
    tile of its own): [] when none fits, fewer than k where only those fit."""
    got, held = [], set(taken)
    for _ in range(max(1, k)):
        one = _direct_pair(src, dst, grid, held)
        if one is None:
            break
        got.append(one)
        held.add((math.floor(one["x"]), math.floor(one["y"])))
    return got


def _tiles_of(plans):
    return {(math.floor(p["x"]), math.floor(p["y"])) for p in plans}


def _direct(cat, job, grid):
    """One machine driver into one machine: a single inserter between them, if one fits."""
    (sk, src), = job.sources
    (tk, dst), = job.taps
    return _direct_link(src, dst, grid, (), _link_k(job.net, sk, tk)) or None


def _direct_claims(cat, js, todo, ents, frames, box, terrain=()):
    """{net: tiles} for the direct inserters of every net with one machine driver, picked before
    any route is laid, so no other net's tree is laid over them: trees are routed first (by their
    belt ends), and a mall's machine-to-machine nets have none. Greedy, net by net, one inserter
    per tile."""
    taken, out = set(), {}
    shared = None
    for net in sorted(todo):
        pairs = _direct_pairs_of(js[net])
        if not pairs:
            continue
        if shared is None:
            shared = _GroundParts(cat, ents, frames, js, terrain)
        grid = shared.grid(net, box)
        mine, ends = set(), set(js[net].ends())
        for (sk, src), (tk, dst) in pairs:
            mine |= _tiles_of(_direct_link(src, dst, grid, taken | mine | ends,
                                           _link_k(net, sk, tk)))
        if mine:
            out[net] = mine
            taken |= mine
    return out


def _direct_pairs_of(job):
    """[((driver, rect), (consumer, rect))] a direct inserter may join: one machine driver and each
    consumer machine, or (1.4) each of several machine drivers and one consumer machine (two
    cable assemblers beside one circuit assembler). [] for any other net."""
    if job.starts or job.goals:
        return []
    if len(job.sources) == 1 and job.taps:
        return [(job.sources[0], tap) for tap in job.taps]
    if len(job.taps) == 1 and len(job.sources) > 1:
        return [(src, job.taps[0]) for src in job.sources]
    return []


def _hub_job(cat, job, blocked, spans, clear, box, limit):
    """hub: merge every driver into a trunk, split it to every consumer. -> (plan, how).

    One machine driver (1.3, Rory 2026-10-20: a mall placed with care): every consumer machine
    standing next to it takes from it by an inserter of its own (a yellow belt assembler hands its
    belts to the underground, splitter and red belt assemblers beside it), and only the rest is
    laid as a belt tree from the driver."""
    grid = _Bounded(blocked, box, spans)
    # 1.4.1 (the router review): a direct inserter never stands on the net's own belt ends, and
    # when the belt tree for the consumers left can't get out (the direct inserters and other
    # nets' claims took every side of the driver), the whole net is laid as a tree, as before 1.4
    ends = set(job.ends())
    if len(job.sources) == 1 and not job.starts and job.taps:
        (sk, src), = job.sources
        taken, direct, rest = set(), [], []
        for key, rect in job.taps:
            got = _direct_link(src, rect, grid, taken | ends, _link_k(job.net, sk, key))
            if got:
                direct += got
                taken |= _tiles_of(got)
            else:
                rest.append((key, rect))
        if direct:
            n = f"{len(direct)} inserter{'s' if len(direct) > 1 else ''} (direct insertion)"
            if not rest and not job.goals:
                return direct, n
            sub = NetJob(job.net, starts=list(job.starts), goals=list(job.goals), taps=rest,
                         sources=list(job.sources), drops=dict(job.drops), owners=list(job.owners))
            if sub.mode == "hub":
                plan, how = _hub_search(cat, sub, set(blocked) | taken, spans, clear, box, limit)
            else:
                plan, how = _tree_job(cat, sub, set(blocked) | taken, spans, clear, box, limit)
            if plan is not None:
                return direct + plan, f"{n}, then {how}"
            return _hub_search(cat, job, blocked, spans, clear, box, limit)
    if len(job.taps) == 1 and len(job.sources) > 1 and not job.starts and not job.goals:
        # several machine drivers beside one consumer machine: each hands to it by an inserter
        # of its own (1.4); only the drivers left are laid as a belt tree into it
        (tk, dst), = job.taps
        taken, direct, rest = set(), [], []
        for key, rect in job.sources:
            got = _direct_link(rect, dst, grid, taken | ends, _link_k(job.net, key, tk))
            if got:
                direct += got
                taken |= _tiles_of(got)
            else:
                rest.append((key, rect))
        if direct:
            n = f"{len(direct)} inserter{'s' if len(direct) > 1 else ''} (direct insertion)"
            if not rest:
                return direct, n
            sub = NetJob(job.net, taps=list(job.taps), sources=rest, drops=dict(job.drops), owners=list(job.owners))
            plan, how = _hub_search(cat, sub, set(blocked) | taken, spans, clear, box, limit)
            if plan is not None:
                return direct + plan, f"{n}, then {how}"
            return _hub_search(cat, job, blocked, spans, clear, box, limit)
    return _hub_search(cat, job, blocked, spans, clear, box, limit)


def _hub_search(cat, job, blocked, spans, clear, box, limit, tap=TapSink):
    """The hub search proper: a trunk from every driver, split to every consumer. ``tap``: the
    driver machines' sink kind (2.1 m2m: on the left lane)."""
    grid = _Bounded(blocked, box, spans)
    cands = _hubs(grid, job)
    if not cands:
        return None, "no free tile is reached from every driver and consumer (no hub)"
    best, why, wins = None, None, 0
    for hub, f in cands:
        got = _hub_trees(cat, job, blocked, spans, clear, box, limit, hub, f, tap)
        if isinstance(got, str):
            why = why or f"hub {hub[0]},{hub[1]}: {got}"
            continue
        if best is None or got[0] < best[0][0] - 1e-9:
            best = (got, hub, f)
        wins += 1
        if wins >= _HUB_WINS:
            break
    if best is None:
        return None, f"none of {len(cands)} hubs routes; first: {why}"
    (_cost, plan, cr, cf), hub, f = best
    taps = cr["taps"] + cf["taps"]
    how = (f"{cr['belts'] + cf['belts']} belts, {cr['undergrounds'] + cf['undergrounds']} "
           f"underground ends, {cr['splitters']} merges, {cf['splitters']} splitters"
           + (f", {taps} inserters" if taps else "")
           + f", hub {hub[0]},{hub[1]} {'NESW'[f]}")
    return plan, how


# -- one net --------------------------------------------------------------------------------------
def _settle_leaves(cat, laid, ents, also=frozenset(), allow=frozenset()):
    """A tapped belt's last tile points on into whatever stands ahead: turn it (never back at its
    feed) where that is another net's belt, or one of the keys ``also`` (a patch's tapped branch:
    the standing route it branched from). ``allow``: keys it may point into on purpose (a joint: a bus
    port or a sealed module's port carrier, 2.0.36). -> the carriers, or None when no turn is clear."""
    occ = {}
    for e in ents:
        if _is_belt(cat, e):
            for t in fkpmod.tiles_of(cat, e):
                occ[t] = e
    mine = {}
    for e in laid:
        for t in fkpmod.tiles_of(cat, e):
            mine[t] = e
    out = []
    for e in laid:
        h = _heading(e)
        if e.name != "transport-belt" or h is None:
            out.append(e)
            continue
        (tx, ty), = fkpmod.tiles_of(cat, e)

        def bad(d):
            ahead = (tx + _DX[d], ty + _DY[d])
            o = occ.get(ahead)
            if ((tx, ty), ahead) in allow:        # across a sealed module's port (2.0.48)
                return False
            return o is not None and o.key not in allow and (o.net != e.net or o.key in also)

        if not bad(h):
            out.append(e)
            continue
        behind = (tx - _DX[h], ty - _DY[h])
        turns = [d for d in ((h + 1) % 4, (h + 3) % 4) if not bad(d)
                 and (tx + _DX[d], ty + _DY[d]) not in mine]
        if behind not in mine or not turns:
            return None
        out.append(fkpmod.Entity(e.key, e.name, e.x, e.y, turns[0] * 4, dict(e.attrs)))
    return out


# -- one net on both lanes (2.0.54) ----------------------------------------------------------------
def _lane_cap(cat):
    from .lanes import lane_rate
    return lane_rate(cat, "transport-belt")


def _flow(flat, fn):
    """What a net carries at full speed: what its drivers make of its items, or what its consumers
    take where that is less. A sink without a rate (a chest) takes what one fast inserter hands
    it: a mall's products into one chest are one inserter's worth, whatever the makers make."""
    from .lanes import inserter_rate
    made = sum(flat.machines[k].makes.get(i, 0.0) for k, i in fn.drivers)
    taken, sinks = 0.0, set()
    for k, i in fn.consumers:
        m = flat.machines[k]
        if i in m.needs:
            taken += m.needs[i]
        elif m.drain is not None:
            taken += m.drain
        elif m.extra.get("port"):
            taken = float("inf")              # a bus's load or mouth: a whole belt end
        else:
            sinks.add(k)
    taken += len(sinks) * inserter_rate("fast-inserter")
    return min(made, taken)


def _driver_rate(flat, fn, entry, path):
    """What one driver end of a net puts on it: a machine's own product, or every machine of the
    instance whose belt starts there."""
    items = set(fn.items)
    if isinstance(entry[0], str):                         # a machine driver: (key, rect)
        m = flat.machines.get(entry[0])
        return sum(r for i, r in (m.makes.items() if m else ()) if i in items)
    keys = flat.insts[path].machines if path in flat.insts else ()
    return sum(r for k in keys if k in flat.machines
               for i, r in flat.machines[k].makes.items() if i in items)


def _halves(flat, fn, job, cap):
    """The net's drivers in two groups, one a lane: split along the axis they spread on, where the
    two groups' rates come closest. None when one driver alone fills a lane (a two-sided MinerRow:
    a side-load would squeeze its two lanes onto one)."""
    rows = []
    for entry, path in job.drv:
        if isinstance(entry[0], str):
            r = entry[1]
            at = ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
        else:
            at = entry[0]
        rows.append((at, _driver_rate(flat, fn, entry, path), entry))
    if len(rows) < 2 or any(rate > cap + 1e-9 for _a, rate, _e in rows):
        return None
    xs, ys = [a[0] for a, _r, _e in rows], [a[1] for a, _r, _e in rows]
    axis = 0 if max(xs) - min(xs) >= max(ys) - min(ys) else 1
    rows.sort(key=lambda row: (row[0][axis], row[0][1 - axis]))
    total = sum(rate for _a, rate, _e in rows)
    best, run = None, 0.0
    for i in range(1, len(rows)):
        run += rows[i - 1][1]
        gap = abs(total - 2 * run)
        if best is None or gap < best[0] - 1e-9:
            best = (gap, i)
    i = best[1]
    return [e for _a, _r, e in rows[:i]], [e for _a, _r, e in rows[i:]]


def _mark_m2m(flat, cat, js):
    """2.1 ``route --m2m``: a net with two or more drivers and two or more consumers whose flow
    is above one lane, or whose ``width`` above 2 asks for that many lanes, is laid by
    ``fkcore.m2m`` (its job's ``m2m``: what each end puts on and takes). Not a lane pair, a
    pinned lane or a fluid."""
    from . import m2m as m2mmod
    cap = _lane_cap(cat)
    for net, job in js.items():
        fn = flat.nets.get(net)
        if fn is None or job.parts or job.fluid or job.skip or net in getattr(flat, "lane_of", {}) \
                or fn.lane is not None or job.drivers() < 2 or job.consumers() < 2:
            continue
        if fn.width > 2 or _flow(flat, fn) > cap + 1e-9:
            job.m2m = m2mmod.spec_of(flat, cat, fn, job, cap)
            job.m2m["both"] = _M2M != "left"


def _widen(flat, cat, js):
    """A net on both lanes of its belt (``width 2``, or a flow above one lane) with two or more
    drivers: its job is a pair of itself (``fkcore.pairs``): the drivers in two groups, chosen from
    where they stand, each side-loaded onto its own lane at a join, then one route of both lanes
    to every consumer (Rory, 2026-10-28: the router picks the grouping; nothing prescribes it)."""
    cap = _lane_cap(cat)
    for net, job in list(js.items()):
        fn = flat.nets.get(net)
        if fn is None or job.parts or job.fluid or job.skip or not job.drivers() or \
                not job.consumers() or net in getattr(flat, "lane_of", {}) or fn.width > 2 \
                or job.m2m is not None:
            continue          # 2.1: a wide net (width above 2) is a ribbon (fkcore.ribbons)
        pin = fn.lane if fn.width < 2 else None
        if pin is None and (job.drivers() < 2 or
                            fn.width < 2 and _flow(flat, fn) <= cap + 1e-9):
            continue
        if pin is not None:
            # `lane L` (2.0.54): every driver onto that side of the join, an empty stub on the
            # other, so the join is a side-load and its items run on that lane to the end
            every = [entry for entry, _path in job.drv]
            halves = (every, []) if pin == "L" else ([], every)
        else:
            halves = _halves(flat, fn, job, cap)
        if halves is None:
            continue
        parts = []
        for side, group in zip("lr", halves):
            part = NetJob(f"{net}.{side}")
            for entry in group:
                (part.sources if isinstance(entry[0], str) else part.starts).append(entry)
                if isinstance(entry[0], str) and entry[0] in job.drops:
                    part.drops[entry[0]] = job.drops[entry[0]]
            parts.append(part)
        both = NetJob(net, goals=list(job.goals), taps=list(job.taps), owners=list(job.owners))
        wide = NetJob(net, starts=list(job.starts), goals=list(job.goals), taps=list(job.taps),
                      sources=list(job.sources), drops=dict(job.drops), owners=list(job.owners),
                      anchors=dict(job.anchors), parts=(parts[0], parts[1], both),
                      wide=pin is None, pin=pin, drv=list(job.drv))
        js[net] = wide


def _near_box(job, box, margin=None):
    """The box a net is searched in first (2.0.45): its own ends and machines plus ``_NEAR``
    tiles and a quarter of their extent, inside ``box``. None when that is ``box`` already.

    Rory, 2026-10-26: "we should be routing a lot of independent small connections so done one at
    a time ideally it would be just as fast as what we typically have." The hub search ranks every
    free tile of its box by belt distance from each end, so a short net in a map-sized frame paid
    for the whole frame: 33 nets took 0.9 s packed and 6.9 s spread over 265x178."""
    xs, ys = [], []
    for j in (job,) + tuple(job.parts or ()):
        for t in j.ends():
            xs.append(t[0])
            ys.append(t[1])
        for _k, r in list(j.sources) + list(j.taps):
            xs += [r[0], r[2] - 1]
            ys += [r[1], r[3] - 1]
    if not xs:
        return None
    m = (_NEAR if margin is None else margin) + max(max(xs) - min(xs), max(ys) - min(ys)) // 4
    near = (max(box[0], min(xs) - m), max(box[1], min(ys) - m),
            min(box[2], max(xs) + 1 + m), min(box[3], max(ys) + 1 + m))
    return None if near == tuple(box) else near


#: 2.1.26: what a search box may cost, in tile visits: its area times the walks the search floods
#: (``_seeds_of``). The 20261030-2352 world's coal (a pair net, dozens of drills and furnaces)
#: failed near its ends in ~1 s, then flooded the whole 1554x1246 frame once per end (28 ends,
#: 54M visits) for ~250 s, in every pass, and failed there too. ~0.2M visits/s: 3M is ~15 s.
WALK_BUDGET = 3_000_000


def _seeds_of(job):
    """How many walks a search of ``job`` floods: one per end, source and tap, its parts' too."""
    n = 0
    for j in (job,) + tuple(job.parts or ()):
        n += len(j.ends()) + len(j.sources) + len(j.taps)
    return max(n, 1)


def _area(b):
    return max(b[2] - b[0], 0) * max(b[3] - b[1], 0)


def _ladder(near, box):
    """The boxes after ``near``: each twice as wide and tall about the same middle, inside
    ``box``, the last ``box`` itself."""
    out, b = [], near
    while tuple(b) != tuple(box):
        w, h = b[2] - b[0], b[3] - b[1]
        b = (max(box[0], b[0] - (w + 1) // 2), max(box[1], b[1] - (h + 1) // 2),
             min(box[2], b[2] + (w + 1) // 2), min(box[3], b[3] + (h + 1) // 2))
        out.append(b)
    return out


def _search_box(job, box):
    """The widest box ``_route_one`` searches ``job`` in, inside ``box``."""
    near = _near_box(job, box)
    seeds = _seeds_of(job)
    if near is None or seeds * _area(box) <= WALK_BUDGET:
        return tuple(box)
    last = near
    for b in _ladder(near, box):
        if seeds * _area(b) > WALK_BUDGET:
            break
        last = b
    return tuple(last)


def _ground_key(cat, job, ents, box, terrain, avoid, extra, whole=False):
    """What a search of ``job`` in ``box`` sees, as one hash: its ends, and every entity, terrain
    tile and claimed tile in the widest box it searches (``_search_box``; ``whole``: all of
    ``box``, as the fluid router searches). The same key, the same answer (3.0: a net that failed
    is not searched again)."""
    x0, y0, x1, y1 = tuple(box) if whole else _search_box(job, box)

    def inside(t):
        return x0 <= t[0] < x1 and y0 <= t[1] < y1
    items = []
    for e in ents:
        if x0 - 6 <= e.x < x1 + 6 and y0 - 6 <= e.y < y1 + 6 \
                and any(inside(t) for t in fkpmod.tiles_of(cat, e)):
            items.append((e.name, e.x, e.y, e.dir, str(e.attrs.get("net", "")), e.owner))
    text = repr((MEMO_VERSION, (x0, y0, x1, y1), job.signature(), sorted(job.ends()),
                 sorted(map(repr, job.taps)), sorted(map(repr, job.sources)), extra,
                 sorted(t for t in terrain if inside(t)), sorted(t for t in avoid if inside(t)),
                 sorted(items)))
    return hashlib.sha1(text.encode()).hexdigest()


def _route_one(cat, job, ents, frames, js, box, limit, terrain=(), avoid=(), spans=None,
               allow=frozenset(), promoted=frozenset(), soft=None):
    """One net's route in ``box``: first in its own neighbourhood (``_near_box``), then, if that
    fails, in the whole box, so bounding the search never loses a route the box allowed. 2.1.26:
    when the whole box would cost more than ``WALK_BUDGET``, in boxes twice as big in turn, as far
    as the budget allows; then it fails, saying how far it looked. ``promoted``: tiles freed for it
    (``_route_promoted``). ``soft``: {tile: extra cost} of the tiles only other nets' routes hold
    (``_negotiate``): its tree or hub may lay belts there, paying."""
    near = _near_box(job, box)
    seeds = _seeds_of(job)
    if near is None:
        boxes = [box]
    elif seeds * _area(box) <= WALK_BUDGET:
        boxes = [near, box]                       # as before 2.1.26: the same routes
    else:
        boxes = [near] + _ladder(near, box)
    got = how = None
    for k, b in enumerate(boxes):
        if k and seeds * _area(b) > WALK_BUDGET:
            last = boxes[k - 1]
            how = (f"{how}; not searched past {last[2] - last[0]}x{last[3] - last[1]} tiles: the next "
                   f"box ({b[2] - b[0]}x{b[3] - b[1]}, {seeds} ends to walk from) costs more than "
                   f"the route's walk budget")
            break
        got, how = _route_in(cat, job, ents, frames, js, b, limit, terrain, avoid, spans, allow,
                             promoted, soft)
        if got is not None:
            break
    if got is not None or not (job.wide or job.pin):
        return got, how
    # 2.0.54: no join routes, so the net goes on as one lane, the way it went before
    plain = NetJob(job.net, starts=list(job.starts), goals=list(job.goals), taps=list(job.taps),
                   sources=list(job.sources), drops=dict(job.drops), owners=list(job.owners), anchors=dict(job.anchors))
    got, text = _route_one(cat, plain, ents, frames, js, box, limit, terrain, avoid, spans, allow,
                           promoted, soft)
    if got is None:
        return None, how
    what = "both lanes" if job.wide else f"lane {job.pin}"
    return got, f"{text}; one lane: no join for {what} routes ({how})"


def _seed_ends(cat, seed):
    """A seed's ways in and out: ([(tile, heading)] a piece ends on, [(tile, heading)] a piece
    starts on). A way in is the tile behind a seed carrier that no seed carrier fills (the
    piece's last belt stands there, facing in); a way out is the tile one points into."""
    tiles = set()
    for e in seed:
        tiles |= set(fkpmod.tiles_of(cat, e))
    ins, outs = [], []
    for e in seed:
        h = _heading(e)
        ug = e.attrs.get("ug")
        for tx, ty in fkpmod.tiles_of(cat, e):
            back, front = (tx - _DX[h], ty - _DY[h]), (tx + _DX[h], ty + _DY[h])
            if ug != "out" and back not in tiles:
                ins.append((back, h))
            if ug != "in" and front not in tiles:
                outs.append((front, h))
    return ins, outs


def _route_seeded(cat, job, ents, frames, js, box, limit, terrain, avoid, seed,
                  allow=frozenset()):
    """3.0 ``fk seed NET``: one net laid through carriers the player pinned (a splitter, a belt,
    a short run), the rest of it the router's (Rory, 2026-11-01: "a manual way to just pin the
    splitter in"). Each driver goes to the seed's nearest way in, each consumer comes from its
    nearest way out, a piece of the router's own search each, laid on the seed and the pieces
    before it. -> (carriers, report text) or (None, reason).

    3.0 (the walker's blueprints): a seed may hold inserters too. One that picks from a machine
    and drops on a seed belt feeds it from that driver, one that picks from a seed belt and drops
    into a machine feeds that consumer, so the router lays no hand of its own for them."""
    if job.parts or job.m2m is not None or job.fluid:
        return None, "a seed takes a net on one belt (not a lane pair, an m2m net or a pipe)"
    hands = [e for e in seed if cat.has_entity(e.name) and cat.kind(e.name) == "inserter"
             and _heading(e) is not None]
    for e in seed:
        if e not in hands and (not _is_belt(cat, e) or _heading(e) is None):
            return None, f"seed {e.key} ({e.name}) is no belt, underground, splitter or inserter"
    belts = [fkpmod.Entity(e.key, e.name, e.x, e.y, e.dir, dict(e.attrs, net=job.net))
             for e in seed if e not in hands]
    if not belts:
        return None, "a seed of inserters alone has no belt for them to stand on"
    hands = [fkpmod.Entity(e.key, e.name, e.x, e.y, e.dir, dict(e.attrs, net=job.net))
             for e in hands]
    seed = belts + hands
    on = {t for e in belts for t in fkpmod.tiles_of(cat, e)}
    reach = []                                          # (pickup tile, drop tile) per hand
    for e in hands:
        (tile,) = fkpmod.tiles_of(cat, e)
        reach.append(armsmod.tiles(e.name, e.dir, e.attrs, tile))

    def inside(t, rect):
        return rect[0] <= t[0] < rect[2] and rect[1] <= t[1] < rect[3]
    sources = [m for m in job.sources
               if not any(inside(p, m[1]) and d in on for p, d in reach)]
    taps = [m for m in job.taps if not any(p in on and inside(d, m[1]) for p, d in reach)]
    ins, outs = _seed_ends(cat, belts)
    if not ins or not outs:
        return None, f"the seed has no way {'in' if not ins else 'out'}: every end of it is " \
                     "another of its carriers"

    def mid(rect):
        return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)

    def share(ends, belts, machines):
        """Each end (a belt's tile, a machine's middle) to the seed's nearest way; then, while a
        way has none and another has two or more, the one of those nearest the empty way moves
        to it: a splitter's two outputs both get consumers. -> [(belts, machines)] per way."""
        items = [(t, ("b", b)) for b in belts for t in [b[0]]] + \
                [(mid(m[1]), ("m", m)) for m in machines]

        def d(t, k):
            return abs(ends[k][0][0] - t[0]) + abs(ends[k][0][1] - t[1])
        to = [min(range(len(ends)), key=lambda k, t=t: d(t, k)) for t, _x in items]
        while True:
            empty = [k for k in range(len(ends)) if k not in to]
            crowded = {k for k in to if to.count(k) > 1}
            if not empty or not crowded:
                break
            i = min((i for i, k in enumerate(to) if k in crowded),
                    key=lambda i: d(items[i][0], empty[0]))
            to[i] = empty[0]
        got = [([], []) for _ in ends]
        for (_t, (kind, x)), k in zip(items, to):
            got[k][0 if kind == "b" else 1].append(x)
        return got

    into = share(ins, job.starts, sources)              # per way in: (starts, sources)
    out_of = share(outs, job.goals, taps)               # per way out: (goals, taps)
    laid, words = list(seed), [f"your seed, {len(seed)} carriers"]
    for k, (starts, sources) in enumerate(into):
        if not starts and not sources:
            continue
        piece = NetJob(job.net, starts=starts, sources=sources, drops=dict(job.drops), goals=[ins[k]],
                       owners=list(job.owners), anchors=dict(job.anchors))
        got, why = _route_one(cat, piece, list(ents) + laid, frames, js, box, limit, terrain,
                              avoid, allow=allow)
        t = ins[k][0]
        if got is None:
            return None, f"no way from its drivers into the seed at {t[0]},{t[1]}: {why}"
        laid += got
        words.append(f"{len(got)} carriers into it at {t[0]},{t[1]}")
    for k, (goals, taps) in enumerate(out_of):
        if not goals and not taps:
            continue
        piece = NetJob(job.net, starts=[outs[k]], goals=goals, taps=taps,
                       owners=list(job.owners), anchors=dict(job.anchors))
        got, why = _route_one(cat, piece, list(ents) + laid, frames, js, box, limit, terrain,
                              avoid, allow=allow)
        t = outs[k][0]
        if got is None:
            return None, f"no way out of the seed at {t[0]},{t[1]} to its consumers: {why}"
        laid += got
        words.append(f"{len(got)} carriers out of it at {t[0]},{t[1]}")
    used = {e.key for e in seed}
    count, out = {}, list(seed)
    for e in laid[len(seed):]:                # the pieces' keys: b0, b1, ... past the seed's
        letter = e.key.rsplit("/", 1)[1][0]
        while True:
            key = f"~{job.net}/{letter}{count.get(letter, 0)}"
            count[letter] = count.get(letter, 0) + 1
            if key not in used:
                break
        used.add(key)
        out.append(fkpmod.Entity(key, e.name, e.x, e.y, e.dir, dict(e.attrs)))
    return out, "through " + "; ".join(words)


def try_route(flat, lay, cat, a, b, bounds=None, margin=4, blocked=(), limit=60000):
    """3.0 ``route --try A:B``, the question mode: how a belt would run from tile ``a`` to tile
    ``b`` on ``lay`` as it stands, every heading tried at both ends; commits nothing.
    -> (carriers, report text) of the shortest, or (None, reasons)."""
    js = jobs(flat, lay, cat)
    frames = set()
    for path, rec in lay.insts.items():
        if rec.kind in ("Literal", "Bus") or path not in flat.insts:
            continue
        x0, y0, x1, y1 = frame_of(flat, lay, path, cat)
        frames |= {(x, y) for x in range(x0, x1) for y in range(y0, y1)}
    w, h = lay.size or (0, 0)
    box = bounds or (0, 0, w + margin, h + margin)
    ents = list(lay.entities.values())
    terrain = frozenset(tuple(t) for t in blocked)
    best, whys = None, []
    for sh in range(4):
        for gh in range(4):
            job = NetJob("?try", starts=[(tuple(a), sh)], goals=[(tuple(b), gh)])
            got, how = _route_one(cat, job, ents, frames, dict(js, **{"?try": job}), box, limit,
                                  terrain)
            if got is None:
                if how not in whys:
                    whys.append(how)
            elif best is None or len(got) < len(best[0]):
                best = (got, how)
    if best is None:
        return None, "; ".join(whys[:3])
    return best


def _route_in(cat, job, ents, frames, js, box, limit, terrain=(), avoid=(), spans=None,
              allow=frozenset(), promoted=frozenset(), soft=None):
    net = job.net
    ents, frames = _clipped(ents, frames, box)
    if terrain:
        terrain = _clipped((), terrain, box)[1]
    if job.parts:
        joined = _prejoined(job)
        if joined is None:
            from . import pairs
            return pairs.route(cat, job, ents, frames, js, box, limit, terrain, avoid, spans,
                               allow)
        # a pair already paired where it comes in (a stack cell's paired port, 2.0.48): its one
        # belt runs straight on, both lanes, and every machine hands on or off it
        blocked = _ground(cat, ents, frames, js, net, terrain, box) | \
            (set(avoid) - set(job.ends()))
        plan = _straight(joined, _Bounded(blocked, box))
        if plan is None:
            return None, ("its two lanes come in paired, and no straight belt runs on from there "
                          "past every machine to its end")
        laid = _carriers(job, plan, net)
        for e in laid:
            if e.name not in _BELT_NAMES:
                e.attrs["net"] = fkpmod.hand_net(net)
        return laid, f"a straight belt of both lanes, {len(laid)} carriers (paired at its port)"
    blocked = (_ground(cat, ents, frames, js, net, terrain, box)
               | (set(avoid) - set(job.ends()))) - set(promoted)
    spans = _ug_spans(cat, ents) if spans is None else spans
    clear = _belt_tiles(cat, ents)
    if job.straight:
        # a stack cell's through belt: straight across the cell, port edge to port edge, when it
        # can (Rory, 2026-10-26: "every belt routing as 3 belts in a row, no bends")
        straight = _straight(job, _Bounded(blocked, box))
        if straight is not None:
            laid = _settle_leaves(cat, _carriers(job, straight, net), ents, allow=allow)
            if laid is not None:
                return laid, f"a straight belt, {len(straight)} carriers"
    # negotiated: the tree or hub may lay belts on what only other nets' routes hold, paying (a
    # tile its own ground blocks stays blocked)
    if soft:
        soft = {t: c for t, c in soft.items() if t not in blocked}
    soft_blocked = blocked
    if job.m2m is not None:
        from . import m2m as m2mmod
        plan, how = m2mmod.route(cat, job, blocked, spans, clear, box, limit, ents)
    elif job.mode == "hub":
        with steinermod.soft_ground(soft):
            plan, how = _hub_job(cat, job, soft_blocked, spans, clear, box, limit)
    else:
        with steinermod.soft_ground(soft):
            plan, how = _tree_job(cat, job, soft_blocked, spans, clear, box, limit)
    if plan is None:
        # 2.0.48: a belt that runs straight through, every machine handing on or off it by one
        # inserter (a stack cell's through belt: no room for a tree's turns or hub)
        straight = _straight(job, _Bounded(blocked, box))
        if straight is not None:
            plan, how = straight, f"a straight belt, {len(straight)} carriers"
    if plan is None:
        return None, how
    laid = _settle_leaves(cat, _carriers(job, plan, net), ents, allow=allow)
    if laid is None:
        return None, "a tapped belt's last tile points into another net's belt"
    return laid, how


_BELT_NAMES = ("transport-belt", "underground-belt", "splitter")
_UG_REACH = 5        # a yellow underground's exit at most 5 tiles from its entrance


def _prejoined(job):
    """A pair job whose two nets come in on one belt already (both start where a paired port's
    belt crosses into a stack cell): -> one NetJob of both, from that start, or None."""
    left, right, pair = job.parts
    shared = list(pair.starts) or [s for s in left.starts if s in right.starts]
    if not shared:
        return None
    return NetJob(job.net, starts=shared[:1],
                  goals=list(pair.goals) + list(left.goals) + list(right.goals),
                  taps=list(pair.taps) + list(left.taps) + list(right.taps),
                  sources=list(left.sources) + list(right.sources),
                  drops={**left.drops, **right.drops}, owners=list(job.owners))


def _straight(job, grid):
    """A net laid as one straight belt from its one start to its one goal, and one inserter for
    each machine on it (fast where it reaches, long-handed where only that reaches), taking from
    the belt into a consumer or dropping from a driver onto it (2.0.48, Rory 2026-10-26: "it should
    be possible to do it with every belt routing as 3 belts in a row, no bends or undergrounds").
    -> plan entities, or None when the two ends don't line up, a tile is taken or a machine finds
    no inserter tile."""
    if len(job.starts) != 1 or len(job.goals) != 1:
        return None
    (s, h), (g, gh) = job.starts[0], job.goals[0]
    if h != gh:
        return None
    tiles = [tuple(s)]
    while tiles[-1] != tuple(g):
        if len(tiles) > 64:
            return None
        tiles.append((tiles[-1][0] + _DX[h], tiles[-1][1] + _DY[h]))
    # a taken tile in the middle (a pinned pole, 2.0.49) is tunnelled under: an entrance on the
    # tile before it, an exit on the tile after, within an underground's reach
    ends = {tuple(s), tuple(g)}
    kind = {t: "belt" for t in tiles}
    i = 1
    while i < len(tiles) - 1:
        if not grid.blocked(tiles[i]) or tiles[i] in ends:
            i += 1
            continue
        j = i
        while j < len(tiles) - 1 and grid.blocked(tiles[j]) and tiles[j] not in ends:
            j += 1
        if j - (i - 1) > _UG_REACH or kind[tiles[i - 1]] != "belt":
            return None
        kind[tiles[i - 1]] = "in"
        kind[tiles[j]] = "out"
        for t in tiles[i:j]:
            kind[t] = None
        i = j + 1
    plan = []
    for x, y in tiles:
        k = kind[(x, y)]
        if k == "belt":
            plan.append({"name": "transport-belt", "x": x + 0.5, "y": y + 0.5, "direction": h * 4})
        elif k is not None:
            plan.append({"name": "underground-belt", "x": x + 0.5, "y": y + 0.5,
                         "direction": h * 4,
                         "belt_to_ground_type": "input" if k == "in" else "output"})
    laid = [t for t in tiles if kind[t] is not None]
    taken = set(tiles)
    for want, pairs in ((job.taps, False), (job.sources, True)):
        for key, rect in want:
            k, n = _TAPS.get((job.net, key), 1), 0
            for x, y in laid:
                belt = (x, y, x + 1, y + 1)
                got = _direct_pair(rect, belt, grid, taken) if pairs else \
                    _direct_pair(belt, rect, grid, taken)
                if got:
                    taken.add((math.floor(got["x"]), math.floor(got["y"])))
                    plan.append(got)
                    n += 1
                    if n >= k:
                        break
            if not n:
                return None
    return plan


def _guarded(cat, job, ents, frames, js, box, limit, terrain):
    """Why a net failed, when the underground guard is the cause: without it the search routes,
    and its tunnels cover these tiles of other undergrounds on the same axis. None otherwise."""
    got, _how = _route_one(cat, job, ents, frames, js, box, limit, terrain,
                           spans={0: set(), 1: set()})
    if got is None:
        return None
    mine, theirs = _ug_spans(cat, got), _ug_spans(cat, ents)
    hit = sorted((mine[0] & theirs[0]) | (mine[1] & theirs[1]))
    if not hit:
        return None
    return ("the only ways in tunnel past another underground on the same axis, which the game "
            "would pair wrong: " + " ".join(f"{x},{y}" for x, y in hit[:4]))


# -- the judge ------------------------------------------------------------------------------------
def _judge(flat, lay, cat, mine, js):
    """{net: (reason, tiles at fault)} for the nets in ``mine`` (routed this run, in routing
    order) whose route leaks, or leaves a pair of its placed ends apart, by ``fkcore.connect``. A
    SPILL between two routes is the later one's fault."""
    rep = connectmod.connect(lay, cat, connectmod.endpoints(flat), _ports(flat))
    g = rep.graph
    out = {}

    def route_net(key):
        owner = key.rsplit("/", 1)[0] if key in g.nodes else ""
        return owner[1:] if owner.startswith("~") and owner[1:] in mine else None

    for lk in rep.leaks:
        a, b = route_net(lk.src), route_net(lk.dst)
        blame = b if b is not None and (a is None or mine.index(b) > mine.index(a)) else a
        if blame is None:
            continue
        key = lk.dst if blame == b else lk.src
        _reason, tiles = out.setdefault(blame, (f"its route would SPILL: {lk.text}", set()))
        tiles |= set(g.nodes[key].tiles)
    for name in mine:
        rs = [rep.nets[n] for n in fkpmod.net_names(name) if n in rep.nets]   # a pair: both
        if not rs or name in out:
            continue
        placed = set()
        for owner in js[name].owners:
            placed |= set(flat.insts[owner.rsplit(".", 1)[0]].machines)
        apart = [(d, c) for r in rs for d, c in r.unreached if d in placed and c in placed]
        alone = sorted({k for r in rs for k in r.unpaired if k.startswith(f"~{name}/")},
                       key=fkpmod.natural_key)
        if not apart and not alone:
            continue
        reason = f"its route does not connect {len(apart)} driver->consumer pairs"
        if alone:
            reason += f"; the game pairs no exit with {', '.join(alone[:4])}"
        tiles = set()
        for key in alone:
            tiles |= set(g.nodes[key].tiles)
        out[name] = (reason, tiles)
    return out


# -- patching a committed route (2.0) -------------------------------------------------------------
_BELTY = frozenset({"transport-belt", "splitter"})    # what a drop or a pickup patch meets
_HELD = _BELTY | {"underground-belt"}     # a standing route with one of these is held, not re-laid


def _placed(flat, job):
    """The machines of the instances the net's job has an end in (``_judge``'s pairs), and every
    port: a port end is always the router's to reach. 2.0.62 (the opus run's mall1/fe): without
    the ports, a net driven by a port had no drivers here, so a consumer added to it read as
    joined and the route was kept as laid, the new machine never fed."""
    out = set(getattr(flat, "ports", None) or ())
    for owner in job.owners:
        out |= set(flat.insts[owner.rsplit(".", 1)[0]].machines)
    return out


def _ends_of(flat, table, job, g, routed=True, net=None):
    """(drivers, consumers): the net's endpoint keys that stand in graph ``g``; ``routed``: only
    those of the instances the router lays the net to (a Literal's are laid by hand); ``net``: one
    member of a lane pair's job (its ends are the member's, the pair's name has none)."""
    placed = _placed(flat, job) if routed else None
    drivers, consumers = table.get(net or job.net, ((), ()))
    pick = lambda ends: sorted({k for k, _i in ends if k in g.nodes
                                and (placed is None or k in placed)}, key=fkpmod.natural_key)
    return pick(drivers), pick(consumers)


def _flows(g, net, drivers, consumers):
    """({driver: what it reaches}, {consumer: what reaches it}) through ``net``'s carriers."""
    cons, drvs = set(consumers), set(drivers)
    return ({d: g.reach({d}, net, ends=cons) for d in drivers},
            {c: g.reach({c}, net, forward=False, ends=drvs) for c in consumers})


def _serving(g, net, drivers, consumers, keys):
    """The keys of ``keys`` (route carriers) on some path from a driver to a consumer."""
    fwd, bwd = _flows(g, net, drivers, consumers)
    up = set().union(*fwd.values())
    down = set().union(*bwd.values())
    return {k for k in keys if k in up and k in down}


def _trunk(g, net, ends, keys, names):
    """The keys of ``keys`` (route carriers) the drivers still feed when no consumer of the net
    is reached any more (each moved or went, 2.0.74: a chest moved a tile): its belts, and the
    hands that still drop on something. The patch branches the consumers off it where they
    stand now, instead of the net being laid again whole."""
    fwd, _bwd = _flows(g, net, *ends)
    reach = set().union(*fwd.values()) & set(keys)
    return {k for k in reach if names.get(k) in _HELD or g.succ.get(k)}


def _is_stub(e):
    """A lane pin's stub (2.0.54, ``fkcore.pairs``): one empty belt into the side of its join,
    keyed ``~<net>/z<k>``. It carries nothing and serves no end, and it is what makes the join a
    side-load: without it the belt into the join would be a turn, which keeps its lanes."""
    return e.key.rsplit("/", 1)[-1][:1] == "z"


def _serving_only(flat, cat, job, ents, laid):
    """``laid`` less what serves no end (a tail past its last pickup): the rule a rebuild keeps a
    committed route by (``_patch_net``), so the next build keeps this one as it stands."""
    g = connectmod.graph(list(ents) + list(laid), cat, _ports(flat))
    table = connectmod.endpoints(flat)
    keep = _serving(g, job.net, *_ends_of(flat, table, job, g, routed=False),
                    {e.key for e in laid})
    out = [e for e in laid if e.key in keep or _is_stub(e)]
    return out if out else laid


def tag_dnp(flat, lay, cat):
    """2.0.63, do not populate (Rory, 2026-10-29): mark ``dnp`` on what a build lays but never
    pastes. That is every entity of a DNP instance, and every route carrier that serves no live
    pair (a driver and a consumer, neither DNP), such as a branch to a DNP machine, its inserter,
    or a net whose ends are all DNP. The trunk a live pair shares with it stays. Each build tags
    afresh, so un-DNP'ing a piece builds it where it was laid. -> how many entities are DNP."""
    for e in lay.entities.values():
        e.attrs.pop("dnp", None)
    if not getattr(flat, "dnp", None):
        return 0
    by_net, n = {}, 0
    for e in lay.entities.values():
        if e.owner.startswith("~"):
            by_net.setdefault(e.owner[1:], []).append(e)
        elif e.owner and flat.is_dnp(e.owner):
            e.attrs["dnp"] = 1
            n += 1
    table = connectmod.endpoints(flat)
    g = None
    for net, ents in sorted(by_net.items()):
        names = fkpmod.net_names(net) or [net]
        if all(flat.is_dnp(m) for m in names):
            dead = ents
        else:
            ends = {m: table.get(m, ((), ())) for m in names}
            if not any(flat.is_dnp(k) for d, c in ends.values() for k, _i in list(d) + list(c)):
                continue                             # no DNP end: all of it is live
            if g is None:
                g = connectmod.graph(list(lay.entities.values()), cat, _ports(flat))
            keys, serve = {e.key for e in ents}, set()
            fluid = any(cat.kind(e.name) in ("pipe", "pipe-to-ground", "pump") for e in ents)
            for m, (drivers, consumers) in ends.items():
                live_d = [k for k, _i in drivers if k in g.nodes and not flat.is_dnp(k)]
                live_c = [k for k, _i in consumers if k in g.nodes and not flat.is_dnp(k)]
                if not live_d or not live_c:
                    continue
                serve |= keys if fluid else _serving(g, m, live_d, live_c, keys)
            dead = [e for e in ents if e.key not in serve and not _is_stub(e)]
        for e in dead:
            e.attrs["dnp"] = 1
        n += len(dead)
    return n


def _common(sets):
    sets = list(sets)
    return set.intersection(*sets) if sets else set()


def _fresh_keys(laid, owner, used, prefix=""):
    """``laid`` keyed ``~<owner>/<prefix><letter><k>`` after every key in ``used``: a committed
    route's keys, taken-up ones too, never go to another carrier (the diff would read a moved
    belt). ``prefix``: a lane pair's part (``l``: its left net's own, ``r``: its right's)."""
    top = {}
    for k in used:
        m = re.fullmatch(r"([lr]?)([a-z])(\d+)", k.rsplit("/", 1)[1])
        if m:
            top[m[1], m[2]] = max(top.get((m[1], m[2]), -1), int(m[3]))
    out = []
    for e in laid:
        letter = e.key.rsplit("/", 1)[1][0]
        top[prefix, letter] = top.get((prefix, letter), -1) + 1
        out.append(fkpmod.Entity(f"~{owner}/{prefix}{letter}{top[prefix, letter]}", e.name,
                                 e.x, e.y, e.dir, dict(e.attrs)))
    return out


def _new_branch(grid, starts, sink, net, reverse, limit):
    """One branch from the standing route: one A* from every start state at once to ``sink``
    (grow()'s own step). -> (plan entities, the start it left from, path) or None."""
    probe = Tree(grid, (grid.box[0] - 9, grid.box[1] - 9, 0, 0))     # empty: the search's ground
    probe.sink_terminal = {}
    got = _search(probe, starts, [sink], [0], limit)
    if got is None:
        return None
    path, how, _i, _cost = got
    t = Tree(grid, path[0])
    t.sink_terminal = {}
    for a, b in zip(path, path[1:]):
        t.succ[a] = ("jump", b) if b[3] else ("step", b)
    t.sink_at[path[-1]] = 0
    if isinstance(sink, TapSink):
        taken = next((s[3] for s in starts if s[0] == path[0] and len(s) > 3), ())
        t.taps = sink.pick(path, probe, taken)   # the choice the search's goal test made
    t.touch()
    return t.entities([sink], net=net, reverse=reverse), how, path


def _branch_words(laid):
    """'12 belts, 2 underground ends, 1 inserter' for a new branch's carriers."""
    belts = sum(e.name == "transport-belt" for e in laid)
    ugs = sum(e.name == "underground-belt" for e in laid)
    ins = sum(e.name not in ("transport-belt", "underground-belt", "splitter") for e in laid)
    words = [f"{belts} belt{'s' if belts != 1 else ''}"]
    if ugs:
        words.append(f"{ugs} underground ends")
    if ins:
        words.append(f"{ins} inserter{'s' if ins != 1 else ''}")
    return ", ".join(words)


class _Patch:
    """One net's patch in the making: its standing carriers, what it adds, and the ground. A lane
    pair (``A|B``, 2.0) is patched by its two member nets: each has its own drivers, consumers and
    reach, and a new carrier's ``net=`` says which lanes it carries."""

    def __init__(self, cat, flat, table, job, ents, kept, frames, js, box, limit, terrain, avoid,
                 g=None):
        self.cat, self.flat, self.table, self.job, self.net = cat, flat, table, job, job.net
        self.members = fkpmod.net_names(job.net) or [job.net]
        self.ents, self.kept, self.added, self.replaced = list(ents), list(kept), [], []
        self.frames, self.js, self.box, self.limit = frames, js, box, limit
        self.terrain, self.avoid = terrain, set(avoid)
        self.used = {e.key for e in kept}
        self.refresh(g)

    def refresh(self, g=None):
        """The graph, each member's ends and flows, and the ground with what stands now. ``g``:
        the graph of exactly what stands now, when the caller has it (2.1.30)."""
        cat, net = self.cat, self.net
        allents = self.ents + self.kept + self.added
        self.g = g if g is not None else connectmod.graph(allents, cat, _ports(self.flat))
        self.ends, self.fwd, self.bwd = {}, {}, {}
        for m in self.members:
            self.ends[m] = _ends_of(self.flat, self.table, self.job, self.g, net=m)
            self.fwd[m], self.bwd[m] = _flows(self.g, m, *self.ends[m])
        ends = set(self.job.ends())
        self.base = (_ground(cat, allents, self.frames, self.js, net, self.terrain)
                     | (self.avoid - ends))
        self.fronts = {}
        for e in allents:
            if not _is_belt(cat, e) or e.attrs.get("ug") == "in" or _heading(e) is None:
                continue
            h = _heading(e)
            for tx, ty in fkpmod.tiles_of(cat, e):
                self.fronts.setdefault((tx + _DX[h], ty + _DY[h]), []).append(e.key)
        # a new belt never stands where any belt points, the net's own included (it would be fed)
        self.blocked = self.base | (set(self.fronts) - ends)
        self.spans = _ug_spans(cat, allents)
        self.clear = _belt_tiles(cat, allents)

    def route_keys(self):
        return {e.key for e in self.kept + self.added}

    def standing(self, m):
        """(member ``m``'s drivers that reach one of its consumers, its consumers one reaches)."""
        drivers, consumers = self.ends[m]
        cons = set(consumers)
        sd = [d for d in drivers if self.fwd[m][d] & cons]
        sc = [c for c in consumers if any(c in self.fwd[m][d] for d in drivers)]
        if not sd:              # every consumer moved or went: the drivers of the trunk (_trunk)
            mine = self.route_keys()
            sd = [d for d in drivers if self.fwd[m][d] & mine]
        return sd, sc

    def grid(self, target, reverse=False):
        """The ground of one new branch, and the tiles it may not start on: the net's other
        free end tiles too, so one new end's branch never takes another's tile (the router lays
        every end of a net in one search; a patch lays them one at a time)."""
        others = set(self.job.ends()) - {target}
        g = _Bounded(self.blocked | others, self.box, self.spans,
                     self.clear if reverse else None)
        return g, others

    def lay(self, laid, nets, replaced=()):
        """Add ``laid`` (plan carriers) carrying ``nets``: belts ``A|B`` for both lanes of a pair,
        inserters ``A+B``; keyed under the job, a pair member's own with its part's prefix."""
        net = "|".join(nets)
        for e in laid:
            lanes = self.cat.has_entity(e.name) and self.cat.kind(e.name) in fkpmod.LANE_TYPES
            e.attrs["net"] = net if lanes else fkpmod.hand_net(net)
        prefix = ""
        if len(self.members) > 1 and len(nets) == 1:
            prefix = "l" if nets[0] == self.members[0] else "r"
        laid = _fresh_keys(laid, self.net, self.used, prefix)
        self.used |= {e.key for e in laid}
        self.kept = [e for e in self.kept if e.key not in replaced]
        self.added = [e for e in self.added if e.key not in replaced] + laid
        self.replaced += list(replaced)
        self.refresh()
        return laid

    # -- a consumer: a branch out of the standing route ------------------------------------------
    def out_starts(self, sources):
        """Start states of a new branch from route carriers ``sources`` (every standing driver
        reaches them): a dead-end belt extended, or a splitter cut into a straight belt. Both
        keep every item on its lane."""
        g, out = self.g, []
        for key in sorted(sources, key=fkpmod.natural_key):
            n = g.nodes[key]
            h = _H.get(n.dir)
            if h is None or n.kind not in ("transport-belt", "underground-belt"):
                continue
            (tx, ty), = n.tiles
            f = (tx + _DX[h], ty + _DY[h])
            if (n.kind == "transport-belt" or n.ug == "out") and f not in g.at \
                    and f not in self.base and self.fronts.get(f) == [key]:
                out.append(((f[0], f[1], h, 0), 0.0, ("leaf", key)))
            if n.kind != "transport-belt":
                continue
            feeds = [(k, via) for k, via in g.pred.get(key, ()) if g.nodes[k].kind in _BELT_KINDS]
            ahead = [(k, via) for k, via in g.succ.get(key, ()) if g.nodes[k].kind in _BELT_KINDS]
            if len(feeds) != 1 or feeds[0][1] != "belt" or len(ahead) != 1 \
                    or ahead[0][1] != "belt" or not set(g.nodes[ahead[0][0]].nets) & set(n.nets):
                continue
            for side in (1, 3):
                sh = (h + side) % 4
                m = (tx + _DX[sh], ty + _DY[sh])
                o2 = (m[0] + _DX[h], m[1] + _DY[h])
                if m in g.at or m in self.base or m in self.fronts or o2 in g.at \
                        or o2 in self.blocked:
                    continue
                out.append(((o2[0], o2[1], h, 0), COSTS["split"] - COSTS["belt"],
                            ("split", key, side), ((tx, ty), m)))
        return out

    def add_consumer(self, role, belt, machine, nets):
        """Join one new consumer end of ``nets`` (its members) to the standing route, off the
        carriers that every standing driver of each reaches and that carry none but them (a
        branch of both lanes for both nets of a pair). -> report words, or None."""
        g, mine = self.g, self.route_keys()
        common = mine
        for m in nets:
            common = common & _common(self.fwd[m][d] for d in self.standing(m)[0])
        common = {k for k in common if set(g.nodes[k].nets) <= set(nets)}
        if machine is not None:
            got = self._tap_from(common, machine, nets) or self._direct_from(machine, nets)
            if got:
                return f"{role}: {got}"
        grid, others = self.grid(belt[0] if belt is not None else None)
        starts = [s for s in self.out_starts(common) if (s[0][0], s[0][1]) not in others]
        if not starts:
            return None
        sinks = ([FixedSink(role, belt[0], belt[1])] if belt is not None else
                 [TapSink(machine[0], machine[1], 1, hands, arms=_ARMS) for hands in _HANDS])
        for sink in sinks:
            got = _new_branch(grid, starts, sink, self.net, False, self.limit)
            if got is not None:
                break
        else:
            return None
        plan, how, _path = got
        laid = _carriers(self.job, plan, self.net)
        replaced, words = (), ""
        if how[0] == "split":
            _k, key, side = how
            n = g.nodes[key]
            (tx, ty), = n.tiles
            h = _H[n.dir]
            sh = (h + side) % 4
            laid.append(fkpmod.Entity(f"~{self.net}/s0", "splitter",
                                      _clean(tx + (_DX[sh] + 1) / 2), _clean(ty + (_DY[sh] + 1) / 2),
                                      h * 4, {"net": self.net}))
            replaced, words = (key,), f"a splitter in place of {key}"
        else:
            words = f"{how[1]} extended"
        also = frozenset(mine) if belt is None else frozenset()
        settled = _settle_leaves(self.cat, laid, self.ents + self.kept + self.added, also,
                                 _crossings(self.flat))
        if settled is None:
            return None
        laid = self.lay(settled, nets, replaced)
        return f"{role}: {words}, {_branch_words([e for e in laid if e.name != 'splitter'])}"

    def _tap_from(self, keys, machine, nets):
        """An inserter from a standing belt the drivers reach straight into a new machine."""
        mkey, rect = machine
        grid, _others = self.grid(None)
        free = lambda q: not grid.blocked(q) and q not in self.g.at
        for hands in _HANDS:
            sink = TapSink(mkey, rect, 1, hands, arms=_ARMS)
            for key in sorted(keys, key=fkpmod.natural_key):
                n = self.g.nodes[key]
                if n.kind not in _BELT_KINDS or (n.kind == "underground-belt" and not n.ug):
                    continue
                for tile in n.tiles:
                    for q, name, direction, arm in sink.taps_at(tile, free):
                        p = _arm_plan(name, q, arm) if arm else {"direction": direction}
                        e = fkpmod.Entity(f"~{self.net}/i0", name, q[0] + 0.5, q[1] + 0.5,
                                          p["direction"], {k: p[k] for k in ("pick", "drop")
                                                           if k in p})
                        laid = self.lay([e], nets)
                        return f"an inserter from {key} ({laid[0].key})"
        return None

    def _direct_from(self, machine, nets):
        """A machine-to-machine net: an inserter from every standing driver machine of each of
        ``nets`` straight into the new one, where each fits."""
        rects = dict(self.job.sources)
        drivers = [(d, m) for m in nets for d in self.standing(m)[0]]
        if not drivers or any(d not in rects for d, _m in drivers):
            return None
        grid, _others = self.grid(None)
        plan, taken = [], set()
        for d, m in drivers:
            got = _direct_link(rects[d], machine[1], grid, taken, _link_k(m, d, machine[0]))
            if not got:
                return None
            plan += [(g, m) for g in got]
            taken |= _tiles_of(got)
        for got, m in plan:
            self.lay(_carriers(self.job, [got], self.net), [m])
        names = [d for d, _m in drivers]
        return f"{len(plan)} inserter{'s' if len(plan) != 1 else ''} from {', '.join(names)}"

    # -- a driver: a branch into the standing route ----------------------------------------------
    def in_roots(self, targets, m):
        """Root states of a new branch of member ``m``, laid reversed, into route belts
        ``targets`` (they reach every consumer of ``m``): beside a belt, side-loading onto it
        (never a curve, which a second input would straighten), or behind a belt no belt feeds
        yet. Onto a belt of a lane pair, only from the side of ``m``'s lane: a side-load lands on
        the near lane (CORE-V2 "Lane physics")."""
        g, out = self.g, []
        for key in sorted(targets, key=fkpmod.natural_key):
            n = g.nodes[key]
            e = _H.get(n.dir)
            if n.kind != "transport-belt" or e is None or m not in n.nets:
                continue
            (tx, ty), = n.tiles
            vias = [via for k, via in g.pred.get(key, ()) if g.nodes[k].kind in _BELT_KINDS]
            if "curve" in vias:
                continue
            if n.nets == (m,):
                ways = [(s, "side-loaded onto") for s in ((e + 1) % 4, (e + 3) % 4)]
                if not vias:
                    ways.append((e, "into the back of"))
            else:
                lanes = fkpmod.net_lanes(n.net) or {}
                if m in lanes.get("L", ()):
                    ways = [((e + 1) % 4, "side-loaded onto the left lane of")]
                elif m in lanes.get("R", ()):
                    ways = [((e + 3) % 4, "side-loaded onto the right lane of")]
                else:
                    ways = []
            for s, words in ways:
                u = (tx - _DX[s], ty - _DY[s])
                if u in g.at or u in self.blocked:
                    continue
                out.append(((u[0], u[1], (s + 2) % 4, 0), 0.0, (words, key)))
        return out

    def add_driver(self, role, belt, machine, m):
        """Join one new driver end of member ``m`` into the standing route, where it reaches
        every consumer of ``m``. -> report words, or None."""
        common = _common(self.bwd[m][c] for c in self.ends[m][1]) & self.route_keys()
        if machine is not None:
            got = self._drop_onto(common, machine, m) or self._direct_into(machine, m)
            if got:
                return f"{role}: {got}"
        grid, others = self.grid(belt[0] if belt is not None else None, reverse=True)
        roots = [s for s in self.in_roots(common, m) if (s[0][0], s[0][1]) not in others]
        if not roots:
            return None
        sinks = ([FixedSink(role, belt[0], (belt[1] + 2) % 4)] if belt is not None else
                 [TapSink(machine[0], machine[1], 1, hands, arms=_ARMS) for hands in _HANDS])
        for sink in sinks:
            got = _new_branch(grid, roots, sink, m, True, self.limit)
            if got is not None:
                break
        else:
            return None
        plan, how, _path = got
        settled = _settle_leaves(self.cat, _carriers(self.job, plan, m),
                                 self.ents + self.kept + self.added, allow=_crossings(self.flat))
        if settled is None:
            return None
        laid = self.lay(settled, [m])
        return f"{role}: {_branch_words(laid)}, {how[0]} {how[1]}"

    def _drop_onto(self, keys, machine, m):
        """An inserter from a new driver machine of ``m`` straight onto a standing belt of ``m``
        alone that reaches every consumer (an inserter drops on a belt's far lane, so never onto
        a pair's belt)."""
        mkey, rect = machine
        grid, _others = self.grid(None)
        for hands, arms in _passes():
            for key in sorted(keys, key=fkpmod.natural_key):
                n = self.g.nodes[key]
                if n.nets != (m,) or (n.kind not in _BELTY and not (
                        n.kind == "underground-belt" and n.ug == "in")):
                    continue
                for (tx, ty) in n.tiles:
                    for pick, drop in arms or ():       # 3.0: the force's vectors
                        q = (tx - drop[0], ty - drop[1])
                        p = (q[0] + pick[0], q[1] + pick[1])
                        if grid.blocked(q) or q in self.g.at or not (
                                rect[0] <= p[0] < rect[2] and rect[1] <= p[1] < rect[3]):
                            continue
                        plan = _arm_plan(hands[0], q, (pick, drop))
                        e = fkpmod.Entity(f"~{self.net}/i0", hands[0], q[0] + 0.5, q[1] + 0.5,
                                          plan["direction"],
                                          {k: plan[k] for k in ("pick", "drop") if k in plan})
                        laid = self.lay([e], [m])
                        return f"an inserter onto {key} ({laid[0].key})"
                    for h in range(4) if arms is None else ():
                        for name in hands:
                            r = _REACH[name]
                            q = (tx + _DX[h] * r, ty + _DY[h] * r)
                            pick = (q[0] + _DX[h] * r, q[1] + _DY[h] * r)
                            if grid.blocked(q) or q in self.g.at or not (
                                    rect[0] <= pick[0] < rect[2] and rect[1] <= pick[1] < rect[3]):
                                continue
                            e = fkpmod.Entity(f"~{self.net}/i0", name, q[0] + 0.5, q[1] + 0.5,
                                              h * 4, {})
                            laid = self.lay([e], [m])
                            return f"an inserter onto {key} ({laid[0].key})"
        return None

    def _direct_into(self, machine, m):
        """A machine-to-machine net: an inserter from the new driver of ``m`` into every
        consumer machine of ``m``, where each fits."""
        rects = dict(self.job.taps)
        consumers = self.ends[m][1]
        if not consumers or any(c not in rects for c in consumers):
            return None
        grid, _others = self.grid(None)
        plan, taken = [], set()
        for c in consumers:
            got = _direct_link(machine[1], rects[c], grid, taken, _link_k(m, machine[0], c))
            if not got:
                return None
            plan += got
            taken |= _tiles_of(got)
        laid = self.lay(_carriers(self.job, plan, m), [m])
        return f"{len(laid)} inserter{'s' if len(laid) != 1 else ''} into {', '.join(consumers)}"


def _role_keys(flat, role):
    """The endpoint keys a role (``inst.name``) stands for: its instance's machines, and the
    port itself when the role is one (``mil.so_in!`` is port ``mil/so_in``). 2.0.74 (the 10-30
    run's MIL step): a new module's port ends were none of its machines, so the patch skipped
    them, its judge found them apart and four main nets were laid again whole."""
    inst, name = role.rsplit(".", 1)
    keys = set(flat.insts[inst].machines)
    port = f"{inst}/{name.rstrip('!')}"
    if port in (getattr(flat, "ports", None) or ()):
        keys.add(port)
    return keys


def _role_ends(flat, job, machines, side):
    """[(role, belt end or None, (machine key, rect) or None)]: the ends on ``side`` of the
    net's roles in the instances of ``machines`` (or the ports), one per belt end or machine."""
    machines, out = set(machines), []
    for role, entries in sorted(job.anchors.items()):
        if not machines & _role_keys(flat, role):
            continue
        for a in entries:
            if a[0] != side:
                continue
            if len(a) == 2:
                out.append((role, a[1], None))
            elif a[1] in machines:
                out.append((role, None, (a[1], a[2])))
    return out


def _patch_net(flat, cat, table, job, old, ents, frames, js, box, limit, terrain=(), avoid=()):
    """Patch one net's committed route ``old`` (carriers) on the layout ``ents``, whose ends all
    still stand where they stood: keep what still serves, take up what serves no end any more (a
    dropped end's branch), and route only each new end into it. -> (carriers, report text, None),
    or (None, why, held): ``held`` is what stands of the route when only a new end could not be
    joined (it stays as it is, the net fails), None when what stands is no route to keep.

    2.0 (Rory, 2026-10-21): "Pin what stands and make patch changes rather than regenerating the
    base ... routing a new section into the old one, even as a bit of spaghetti, beats ripping
    everything up." The first lead run's players re-laid iron, coal and plates at once for one
    consumer added, and iron stood for 20 minutes. A new consumer branches off where every
    standing driver reaches: a dead-end belt extended, a splitter cut into a straight belt, or an
    inserter straight off a belt (or a driver machine) into it; a new driver joins where it
    reaches every consumer: side-loaded onto a belt, into the back of a belt nothing feeds, or an
    inserter onto it. Each branch is the router's own search (``steiner``), on the router's
    ground, and ``fkcore.connect`` judges the result: every pair joined, no SPILL.

    A lane pair (``A|B``, 2.0.2; the lead's merge of V2 lanes, 2026-10-22, routed a changed pair
    again whole) is patched by its two nets: a new consumer of both branches off the belt of both
    lanes (a splitter or the belt's end, which keep each item on its lane), a new driver of A
    joins A's own belt or the left lane of the pair's, and each net's report is judged.

    2.0.74 (the 10-30 run's MIL step): a new end the patch can't join no longer gets the net
    routed again whole. That paste re-laid four main nets with 549 belts short, robots tore the
    old mains up first and the base stopped; now what stands is kept and only the new end fails."""
    members = fkpmod.net_names(job.net) or [job.net]
    sub = {m: table.get(m, ((), ())) for m in members}
    mine = {e.key for e in old}
    g = connectmod.graph(list(ents) + list(old), cat, _ports(flat))
    serve, trunked = set(), False
    for m in members:
        ends = _ends_of(flat, table, job, g, routed=False, net=m)
        s = _serving(g, m, *ends, mine)
        if not s:
            s = _trunk(g, m, ends, mine, {e.key: e.name for e in old})
            trunked = trunked or bool(s)
        serve |= s
    rep = connectmod.connect(g, cat, sub)
    leak = next((lk for lk in rep.leaks if lk.src in serve or lk.dst in serve), None)
    if leak is not None:
        return None, f"what stands of its route would SPILL: {leak.text}", None
    kept = [e for e in old if e.key in serve]
    kept += [e for e in old if _is_stub(e) and e.key not in serve and kept]   # a lane pin's join
    # 2.1.30: a patch that keeps every carrier stands on the graph just built (the same entities)
    same = {e.key for e in kept} == mine
    p = _Patch(cat, flat, table, job, ents, kept, frames, js, box, limit, terrain, avoid,
               g if same else None)
    p.used |= mine
    new_c, new_d = set(), set()
    for m in members:
        sd, sc = p.standing(m)
        if not sd:
            of = f" of {m}" if len(members) > 1 else ""
            return (None, f"nothing of its committed route joins a driver{of} to a consumer now",
                    None)
        apart = [(d, c) for d in sd for c in sc if c not in p.fwd[m][d]]
        if apart:
            return None, f"its committed route leaves {apart[0][0]} -> {apart[0][1]} apart", None
        drivers, consumers = p.ends[m]
        new_c |= {c for c in consumers if c not in sc}
        new_d |= {d for d in drivers if d not in sd}

    def nets_of(role, machine, side):
        """The members whose ends (``side``: 0 drivers, 1 consumers) the role's machine is."""
        keys = {machine[0]} if machine else _role_keys(flat, role)
        return [m for m in members if keys & set(p.ends[m][side])]

    words = []
    for role, belt, machine in _role_ends(flat, job, new_c, "in"):
        got = p.add_consumer(role, belt, machine, nets_of(role, machine, 1))
        if got is None:
            return None, f"no branch from the standing route reaches {role}", kept
        words.append(got)
    for role, belt, machine in _role_ends(flat, job, new_d, "out"):
        for m in nets_of(role, machine, 0):
            got = p.add_driver(role, belt, machine, m)
            if got is None:
                return None, f"no branch from {role} reaches the standing route", kept
            words.append(got)
    rep = connectmod.connect(p.g, cat, sub)
    keys = p.route_keys()
    leak = next((lk for lk in rep.leaks if lk.src in keys or lk.dst in keys), None)
    if leak is not None:
        return None, f"the patch would SPILL: {leak.text}", kept
    for m in members:
        r = rep.nets.get(m)
        drivers, consumers = p.ends[m]
        apart = [(d, c) for d, c in (r.unreached if r else ()) if d in drivers and c in consumers]
        if apart:
            return None, f"the patch leaves {apart[0][0]} -> {apart[0][1]} apart", kept
        alone = [k for k in (r.unpaired if r else ()) if k in keys]
        if alone:
            return None, f"the game would pair no exit with {', '.join(alone[:4])}", kept
    tail = []
    if trunked:         # the trunk's tail past the new branches went to where the ends stood
        serving = set().union(*(_serving(p.g, m, *p.ends[m], p.route_keys()) for m in members))
        tail = [e for e in p.kept if e.key not in serving and not _is_stub(e)]
    if tail:
        p.kept = [e for e in p.kept if e not in tail]
        p.refresh()
    gone = len(old) - len(kept) + len(tail)
    if gone:
        words.append(f"{gone} carrier{'s' if gone != 1 else ''} taken up (no end of the net "
                     f"needs {'them' if gone != 1 else 'it'} now)")
    text = (f"{job.drivers()} -> {job.consumers()}, " + ("; ".join(words) or "nothing new")
            + f"; {len(p.kept)} of {len(old)} carriers kept")
    return p.kept + p.added, text, None


def _standing(flat, new, cat, js, patch, frames, box, limit, terrain, avoid, net_frames=None):
    """The committed routes of ``patch`` ({net: carriers}: nets none of whose ends moved) on
    ``new``, judged against the netlist as it is now (``fkcore.connect``). A route that joins
    every driver of its net to every consumer, leaks nowhere and has no carrier that serves no
    end stands as it is; any other is patched (``_patch_net``), or routed again when it can't
    be. A belt route that only fails to reach a new end is held: it stands as it is and the net
    fails (2.0.74). What stands is added to ``new``. ``net_frames``: net -> (its blocked frames,
    its box), as the router routes it (a net inside a sealed module patches inside that module's
    frame, 2.0.74: with every frame blocked no branch of one could be laid). -> (nets kept as
    they were, {net: patch report}, {net: why it is routed again}, {net: why it is held}).

    2.0: the netlist decides, not the net's name. The first lead run changed a net's sink and
    kept its name (``net packs: X -> lab`` to ``X -> px``): the route stood as it was, the build
    said ok, and the old inserter still fed the lab."""
    table = connectmod.endpoints(flat)
    for net in sorted(patch):
        for e in patch[net]:
            new.add(fkpmod.Entity(e.key, e.name, e.x, e.y, e.dir, dict(e.attrs)))
    g = connectmod.graph(new, cat, _ports(flat))
    rep = connectmod.connect(g, cat, table)
    faulty = {}
    for net in sorted(patch):
        keys = {e.key for e in patch[net]}
        leak = next((lk for lk in rep.leaks if lk.src in keys or lk.dst in keys), None)
        # a lane pair (``A|B``, merged with route patching 2026-10-22) is judged by its two nets:
        # the ends and the connect report are theirs, its own name has none
        apart, served = [], set()
        for m in fkpmod.net_names(net):
            drivers, consumers = _ends_of(flat, table, js[net], g, net=m)
            r = rep.nets.get(m)
            apart += [(d, c) for d, c in (r.unreached if r else ())
                      if d in drivers and c in consumers]
            if not js[net].fluid:
                served |= _serving(g, m, *_ends_of(flat, table, js[net], g, routed=False, net=m),
                                   keys)
        idle = [] if js[net].fluid else sorted(keys - served, key=fkpmod.natural_key)
        if leak is not None:
            faulty[net] = f"its committed route would SPILL: {leak.text}"
        elif apart:
            more = len(apart) - 1
            faulty[net] = (f"its committed route does not join {apart[0][0]} -> {apart[0][1]}"
                           + (f" and {more} more pair{'s' if more != 1 else ''}" if more else ""))
        elif idle:
            faulty[net] = (f"{len(idle)} of its committed carriers serve no end of it now "
                           f"({idle[0]} ...)")
        elif js[net].skip:
            faulty[net] = js[net].skip
    for net in faulty:
        for e in patch[net]:
            del new.entities[e.key]
    standing = sorted(n for n in patch if n not in faulty)
    patched, again, held = {}, {}, {}
    pending = {n: patch[n] for n in faulty}
    for net in sorted(faulty):
        old = pending.pop(net)
        if js[net].fluid or js[net].skip:
            again[net] = faulty[net]
            continue
        # the routes still waiting for their patch stand in the way, as their carriers may stay
        ground = list(new.entities.values()) + [e for ents in pending.values() for e in ents]
        fnet, bnet = net_frames(net) if net_frames else (frames, box)
        try:
            got, how, hold = _patch_net(flat, cat, table, js[net], old, ground, fnet, js, bnet,
                                        limit, terrain, avoid)
        except Exception as exc:         # noqa: BLE001 - the net is routed again, as before 2.0
            got, how, hold = None, f"{type(exc).__name__}: {exc}", None
        # a route of inserters alone (direct insertion) is laid again whole: nothing to tear up
        if got is None and hold is not None and any(e.name in _HELD for e in hold):
            for e in hold:
                new.add(e)
            held[net] = (f"its standing route is kept as it is ({len(hold)} of {len(old)} "
                         f"carriers), the new end is not joined: {how}; to lay the net again "
                         f"whole: fk build --reroute {net}")
            continue
        if got is None:
            again[net] = f"{faulty[net]}; patching it failed: {how}"
            continue
        for e in got:
            new.add(e)
        patched[net] = how
    return standing, patched, again, held


#: inserters that take a filter (2.0: the fast inserter took the filter inserter's place)
_FILTERS = {"fast-inserter": 5, "bulk-inserter": 5, "stack-inserter": 5}


def _filter_loads(flat, lay, cat):
    """2.0.76 (Rory, 2026-10-31: shared belts kept jamming): every route inserter that picks from
    a machine making items its net doesn't carry (a requester of four items, two of them for this
    belt) is filtered to its net's items, ``filter=a|b``. Unfiltered, it put whatever the chest
    held on the belt: the 10-29 run's blue belt filled with plastic no sink on it took, and the two
    lanes of a ``lanes`` pair from one chest each got both items. One that already has a filter (a
    gated net's ``filter=circuit``) is left as it is. -> report lines for the ones that can't take
    a filter (a long-handed inserter)."""
    g = connectmod.graph(lay, cat)
    out = []
    for key, e in lay.entities.items():
        if "inserter" not in e.name or not e.owner.startswith("~") or "filter" in e.attrs:
            continue
        net = flat.nets.get(e.attrs.get("net"))
        if net is None:
            continue
        for src, via in g.pred.get(key, ()):
            m = flat.machines.get(src)
            if via != "pickup" or m is None:
                continue
            mine = [i for i in net.items if i in m.makes]
            if not mine or set(m.makes) <= set(mine):
                continue
            if len(mine) > _FILTERS.get(e.name, 0):
                out.append(f"  unfiltered {net.name}: {key} is a {e.name}, which "
                           + (f"takes {_FILTERS[e.name]} filters, and the net has {len(mine)}"
                              if e.name in _FILTERS else "can't filter")
                           + f", so it loads everything {m.inst} holds onto the belt and can "
                           "clog it: give the chest a free side a fast inserter can load from")
                continue
            e.attrs["filter"] = "|".join(mine)
    return out


# 3.1: the router's version is ``compiled.ROUTER``, the one constant of the one compile store
# (route.COMPILE_VERSION, 2.1.31..3.0.3, is retired with the router's own store).


def _unit(net, sealed):
    """The compile unit of ``net``: the innermost sealed module it is inside, "" for none (a lane
    pair ``a|b`` by its first net)."""
    parts = fkpmod.net_names(net)[0].split("/")[:-1] if net else []
    best = ""
    for i in range(1, len(parts) + 1):
        pre = "/".join(parts[:i])
        if pre in sealed:
            best = pre
    return best


def _renamed(net, src, dst):
    """``net`` (a net of unit ``src``, or a lane pair of them) as the same net of unit ``dst``;
    ``dst`` "" gives the name relative to ``src``. None when a part is outside ``src``."""
    out = []
    for part in re.split(r"([|+])", net):
        if part in ("|", "+", ""):
            out.append(part)
        elif part.startswith(src + "/"):
            out.append((dst + "/" if dst else "") + part[len(src) + 1:])
        else:
            return None
    return "".join(out)


class _UnitTurn:
    """A sealed unit's frame-relative terms (2.1.32): plain, or, for a unit turned 180 degrees
    (``half``), the terms of its unturned frame, so a turned twin keys and stores alike."""

    def __init__(self, frame, rot):
        self.x0, self.y0, x1, y1 = frame[0], frame[1], frame[2], frame[3]
        self.w, self.h = x1 - self.x0, y1 - self.y0
        self.half = int(rot or 0) % 4 == 2

    def point(self, x, y):
        x, y = x - self.x0, y - self.y0
        return (self.w - x, self.h - y) if self.half else (x, y)

    def point_abs(self, x, y):
        if self.half:
            x, y = self.w - x, self.h - y
        return x + self.x0, y + self.y0

    def tile(self, t):
        x, y = t[0] - self.x0, t[1] - self.y0
        return (self.w - 1 - x, self.h - 1 - y) if self.half else (x, y)

    def rect(self, r):
        a0, b0, a1, b1 = r[0] - self.x0, r[1] - self.y0, r[2] - self.x0, r[3] - self.y0
        return (self.w - a1, self.h - b1, self.w - a0, self.h - b0) if self.half             else (a0, b0, a1, b1)

    def d16(self, d):
        return None if d is None else ((int(d) + 8) % 16 if self.half else d)

    def h4(self, h):
        return (h + 2) % 4 if self.half else h


def unit_key(flat, lay, cat, js, u, sealed, mine, ground, terrain, child_keys=(), seeds=None,
             settings=()):
    """The compile key of sealed unit ``u`` (3.1, the one key of the compile store,
    ``fkcore.compiled``): a sha over everything its own nets ``mine`` are laid to, relative to
    its frame -- ``compiled.ROUTER``, the frame's size, the instances placed in it (the module's
    source and parameters, as they elaborate), every entity standing in it (machines, Literals,
    its children's routes, a held route), the keys of the sealed units inside it
    (``child_keys``: [(relative path, key)]), the catalog's version and the footprint of every entity name in
    the frame, the terrain in it, the router's ``settings`` (hand, ribbon tiers, pipe), and each
    net's ends, items, lanes and seed (``seeds``: {net: carriers}). Two units with one key are
    laid the same. -> the key (20 hex), or None when a net's name is not the unit's own."""
    x0, y0, x1, y1, _tiles = sealed[u]
    rec = lay.insts.get(u)
    rel = {n: _renamed(n, u, "") for n in mine}
    if rec is None or any(r is None for r in rel.values()):
        return None

    def key_of(k):
        return k[len(u) + 1:] if k.startswith(u + "/") else "^" + k

    tf = _UnitTurn(sealed[u], rec.rot)
    tile, rect = tf.tile, tf.rect

    # 2.1.32: a unit turned 180 degrees is keyed in its unturned frame (a `>` bay's chains are
    # its unturned twin's, turned): its routes are stored unturned (``_store_save``)
    parts = [compiledmod.ROUTER, x1 - x0, y1 - y0, rec.kind, 0 if tf.half else rec.rot,
             sorted(child_keys, key=repr), repr(settings)]
    kids = []
    for p, r in lay.insts.items():
        if not p.startswith(u + "/"):
            continue
        if tf.half:
            inst = flat.insts.get(p)
            try:
                if r.kind == "Literal" and inst is not None and inst.literal is not None:
                    w, h = inst.literal[1]
                    if r.rot % 2:
                        w, h = h, w
                    box = (int(r.x), int(r.y), int(r.x) + w, int(r.y) + h)
                else:
                    box = frame_of(flat, lay, p, cat)
                fx0, fy0, _fx1, _fy1 = tf.rect(box)
            except Exception:                 # noqa: BLE001 (a child with no frame: no key)
                return None
            kids.append((p[len(u) + 1:], r.kind, sorted(r.params.items()), fx0, fy0,
                         (r.rot + 2) % 4))
        else:
            kids.append((p[len(u) + 1:], r.kind, sorted(r.params.items()), r.x - x0, r.y - y0,
                         r.rot))
    parts.append(sorted(kids))
    # 2.1.35: a rail or signal another module owns (a shared lane's ramp, the planned signals)
    # is left out of the key: it differs from twin to twin at the frame's edge, and ``_fits``
    # still rejects a moved route that would stand on it
    standing = sorted((e.name,) + tf.point(e.x, e.y) + (tf.d16(e.dir) if e.dir is not None
                                                        else -1,)
                      for e in _near(ground, x0, y0, x1, y1)
                      if x0 <= e.x < x1 and y0 <= e.y < y1
                      and not ("rail" in e.name and not e.key.startswith(u + "/")))
    parts.append(standing)
    # 3.1: the catalog facts the routes are laid against: its version and each name's footprint
    facts = [getattr(cat, "version", None)]
    for name in sorted({s[0] for s in standing}):
        try:
            facts.append((name, cat.size(name)))
        except Exception:                 # noqa: BLE001 (a name the catalog has no row for)
            facts.append((name, None))
    parts.append(facts)
    if terrain:
        parts.append(sorted(tile((x, y)) for x in range(x0, x1) for y in range(y0, y1)
                            if (x, y) in terrain))
    for n in sorted(mine, key=lambda m: rel[m]):
        j = js[n]
        items = sorted(i for m in fkpmod.net_names(n) if m in flat.nets for i in flat.nets[m].items)
        parts.append((rel[n], items,
                      sorted((tile(t), tf.h4(h)) for t, h in j.starts),
                      sorted((tile(t), tf.h4(h)) for t, h in j.goals),
                      sorted((key_of(k), rect(r)) for k, r in j.taps),
                      sorted((key_of(k), rect(r)) for k, r in j.sources),
                      sorted(tile(t) for t in j.reserved),
                      sorted((tile(t), sorted((ln, sorted(v)) for ln, v in need.items()),
                              sorted((tf.h4(h), sorted((ln, sorted(v)) for ln, v in held.items()))
                                     for h, held in feeds))
                             for t, (need, feeds) in j.lanes.items()),
                      j.skip, j.fluid, j.straight, j.wide, j.pin, j.m2m is not None,
                      len(j.parts),
                      # 3.1: a seed (fk seed) is an input of the net: it is in the key
                      sorted((key_of(e.key), e.name) + tf.point(e.x, e.y) + (tf.d16(e.dir)
                                                                              if e.dir is not None
                                                                              else -1,)
                             for e in (seeds or {}).get(n, ()))))
    return hashlib.sha1(repr(parts).encode("utf-8")).hexdigest()[:20]


def _moved_entity(e, net, new_net, dx, dy, src, dst):
    """Carrier ``e`` of ``net`` (unit ``src``'s) as the same carrier of ``new_net`` (unit
    ``dst``'s), moved by (dx, dy); None when its key or its ``net=`` is not the unit's own. The
    ``net=`` is renamed as written: an inserter of a lane pair says ``a+b``, its belts ``a|b``."""
    head = f"~{net}"
    if not e.key.startswith(head):
        return None
    attrs = dict(e.attrs)
    if "net" in attrs:
        attrs["net"] = _renamed(attrs["net"], src, dst)
        if attrs["net"] is None:
            return None
    return fkpmod.Entity(f"~{new_net}{e.key[len(head):]}", e.name, e.x + dx, e.y + dy, e.dir,
                         attrs)


def _moved_routes(added, nets, src, src_frame, dst, dst_frame):
    """The routes unit ``src`` laid this build (``added``, its ``nets``), moved to unit ``dst``'s
    frame and renamed to its nets: -> {net: carriers}, or None when one is missing."""
    dx, dy = dst_frame[0] - src_frame[0], dst_frame[1] - src_frame[1]
    out = {}
    for n in nets:
        new = _renamed(n, src, dst)
        if n not in added or new is None:
            return None
        ents = [_moved_entity(e, n, new, dx, dy, src, dst) for e in added[n]]
        if any(e is None for e in ents):
            return None
        out[new] = ents
    return out


class _Buckets:
    """Entities by place (2.1.33, lab FRICTION 35): ``near(x0, y0, x1, y1)`` yields every entity
    whose position may lie in the box, from 32-tile buckets. A megabase loop's ~700 sealed units
    each scanned the whole ground to key and fit their compiled routes."""
    S = 32

    def __init__(self, ents=()):
        self.b = {}
        self.extend(ents)

    def extend(self, ents):
        for e in ents:
            self.b.setdefault((math.floor(e.x) // self.S, math.floor(e.y) // self.S),
                              []).append(e)

    def near(self, x0, y0, x1, y1):
        s = self.S
        for bx in range(math.floor(x0) // s, math.floor(x1) // s + 1):
            for by in range(math.floor(y0) // s, math.floor(y1) // s + 1):
                yield from self.b.get((bx, by), ())

    def __iter__(self):
        for ents in self.b.values():
            yield from ents


def _near(ground, x0, y0, x1, y1):
    """The entities of ``ground`` (a list, or ``_Buckets``) that may lie in the box."""
    return ground.near(x0, y0, x1, y1) if isinstance(ground, _Buckets) else ground


def _fits(cat, got, ground, frame, blocked, terrain, claimed, mine):
    """True when the moved routes ``got`` ({net: carriers}) stand in ``frame`` on free tiles: no
    entity of ``ground`` there, no terrain, no blocked frame, no tile another net's direct
    insertion claims (``claimed``: tile -> nets; ``mine``: the unit's own nets)."""
    x0, y0, x1, y1, _tiles = frame
    occ = set()
    for e in _near(ground, x0 - 3, y0 - 3, x1 + 3, y1 + 3):
        if x0 - 3 <= e.x < x1 + 3 and y0 - 3 <= e.y < y1 + 3:
            occ |= fkpmod.tiles_of(cat, e)
    for ents in got.values():
        for e in ents:
            for t in fkpmod.tiles_of(cat, e):
                if not (x0 <= t[0] < x1 and y0 <= t[1] < y1) or t in occ or t in terrain \
                        or t in blocked or claimed.get(t, mine) - mine:
                    return False
    return True


def _prefixed(rel, u):
    """A net name relative to unit ``u`` (``x|y``, ``x+y``) as ``u``'s own (``u/x|u/y``)."""
    return re.sub(r"(^|[|+])", lambda m: m.group(1) + u + "/", rel)


def _unit_rel(routes, u, frame, rot=0):
    """Sealed unit ``u``'s routes ({net: carriers}) in its own terms: its nets' names relative to
    it, the carriers relative to its frame (unturned, ``_UnitTurn``): the compiled store's form.
    -> {rel net: [[tag, name, x, y, dir, attrs, own net]]}, or None when one is not its own."""
    tf = _UnitTurn(frame, rot)
    nets = {}
    for n, ents in routes.items():
        rel = _renamed(n, u, "")
        head = f"~{n}"
        if rel is None or any(not e.key.startswith(head) for e in ents):
            return None
        own = [_renamed(e.attrs["net"], u, "") if "net" in e.attrs else "" for e in ents]
        if any(o is None for o in own):
            return None
        nets[rel] = [[e.key[len(head):], e.name, *tf.point(e.x, e.y), tf.d16(e.dir),
                      {k: v for k, v in e.attrs.items() if k != "net"}, o]
                     for e, o in zip(ents, own)]
    return nets


def _unit_abs(nets, u, frame, rot=0):
    """``_unit_rel``'s inverse: the routes of unit ``u`` in its frame, turned as it is."""
    tf = _UnitTurn(frame, rot)
    out = {}
    for rel, ents in nets.items():
        net = _prefixed(rel, u)
        got = []
        for tag, name, x, y, d, attrs, own in ents:
            attrs = dict(attrs)
            if own:
                attrs["net"] = _prefixed(own, u)
            ax, ay = tf.point_abs(x, y)
            got.append(fkpmod.Entity(f"~{net}{tag}", name, ax, ay, tf.d16(d), attrs))
        out[net] = got
    return out


def _store_save(store, ckey, module, u, frame, routes, rot=0, inputs=None):
    """Write sealed unit ``u``'s routes ({net: carriers}) to the compile store
    (``compiled.Store``), relative to its frame (unturned), under ``module`` and its key. -> 1
    when written, 0 when the key was there already or the store can't be written."""
    nets = _unit_rel(routes, u, frame, rot)
    if nets is None:
        return 0
    return store.save(module, ckey, u, nets, inputs)


def _store_load(store, ckey, module, u, frame, rot=0):
    """Sealed unit ``u``'s routes from the compile store under ``module`` and ``ckey``, moved to
    its frame: -> {net: carriers}, or None when the store has none it may take."""
    nets = store.load(module, ckey)
    if nets is None:
        return None
    return _unit_abs(nets, u, frame, rot)


def _module_of(flat, lay, u):
    """The module name sealed unit ``u`` is an instance of (the store's folder)."""
    inst = flat.insts.get(u)
    if inst is not None and (inst.module or inst.kind):
        return compiledmod.module_name(inst.module or inst.kind)
    return compiledmod.module_name(lay.insts[u].kind)


def route_layout(flat, lay, cat, bounds=None, margin=4, retries=None, limit=60000,
                 blocked=(), hand=None, patch=None, ug=None, m2m=False, group_budget=None,
                 group_max=None, first=(), skip=(), memo=None, again=(), on_net=None,
                 on_reach=None, seeds=None, retry="changed", pipe=None, store=None) -> Routed:
    """``_route_layout`` with direct insertion by ``hand`` (a short inserter) instead of the
    fast one; long-handed ones still go where only they reach. ``ug``: the fastest belt tier a
    ribbon's tunnels may have (yellow, red, blue, turbo; default yellow): a ribbon reaches as far
    as it, and lays each tunnel with the slowest tier that reaches it (2.1 ribbon tunnels).
    ``m2m`` (2.1, ``route --m2m``): a net with two or more drivers and two or more consumers
    whose flow is above one lane (or whose ``width`` above 2 asks for that many lanes) is laid by
    ``fkcore.m2m`` and gets a ``MAXFLOW`` line; without it nothing changes. ``m2m="left"``: on
    the left lane only (version 1); otherwise on both lanes (2.1.21).

    3.0, the build script's routing policy: ``group_budget`` / ``group_max`` (GROUP_BUDGET,
    GROUP_MAX; a budget of 0 lays every net once and alone), ``first``: net names or patterns
    (``fnmatch``, ``mall/*``) laid first, in that order; ``skip``: nets or patterns the router
    leaves to the player (reported ``manual``, never a failure). ``memo``: the last build's
    ``Routed.memo``; a net in it whose ends and ground are as they were then is not searched again
    (it fails with its last reason), unless ``again`` (names or patterns) names it.

    3.0, the design database on disk (``fkcore.design``): ``on_net(net, status, carriers, why,
    seconds)`` is told each net the moment it lands (routed or unrouted), so a build that stops
    leaves what it finished; ``on_reach(net, which, k, n)`` is asked as the router reaches a net,
    and may answer carriers to take as they stand (a net the player held while the build ran).
    ``seeds``: {net: carriers the player pinned} (``fk seed``), each such net laid through its
    seed (``_route_seeded``). ``retry`` (``route --retry-failed``): a net that failed last build is
    searched again when its ground ``changed`` (the default), ``always``, or ``never`` (until
    ``again`` names it or the player unroutes it). ``pipe``: (pipe, pipe-to-ground) the fluid
    nets are laid with (``fluid_route.material``; default the iron ones). ``store`` (a folder,
    or None): the compiled sealed modules (PLAN-3.0 §4), read and written (``_route_layout``)."""
    global _HANDS, _M2M
    policy = dict(group_budget=group_budget, group_max=group_max, first=tuple(first or ()),
                  skip=tuple(skip or ()), memo=memo or {}, fresh=tuple(again or ()),
                  on_net=on_net, on_reach=on_reach, seeds=dict(seeds or {}), retry=retry)
    from . import ribbons as ribmod
    from . import fluid_route as fluidmod
    saved_ug, saved_m2m, saved_pipe = ribmod._UG, _M2M, list(fluidmod._LAY)
    fluidmod._LAY[:] = list(pipe or (fluidmod.PIPE, fluidmod.PTG))
    ribmod._UG = ribmod.ug_tiers(cat, ug or "yellow")
    _M2M = "left" if m2m == "left" else bool(m2m)
    try:
        if not hand or hand == "fast-inserter":
            return _route_layout(flat, lay, cat, bounds, margin, retries, limit, blocked, patch,
                                 store=store, **policy)
        if _REACH.get(hand) != 1:
            raise ValueError(f"hand is a short inserter (inserter, fast-inserter, "
                             f"burner-inserter), got {hand!r}")
        saved, _HANDS = _HANDS, ((hand,), (hand, "long-handed-inserter"))
        try:
            return _route_layout(flat, lay, cat, bounds, margin, retries, limit, blocked, patch,
                                 store=store, **policy)
        finally:
            _HANDS = saved
    finally:
        ribmod._UG = saved_ug
        _M2M = saved_m2m
        fluidmod._LAY[:] = saved_pipe


def _named(net, patterns):
    """True when ``net`` matches one of ``patterns`` (names or ``fnmatch`` patterns)."""
    return any(net == p or fnmatchcase(net, p) for p in patterns)


# -- negotiation (PathFinder): nets share ground at a price until none overlaps ---------------------
#: rounds, the price of a tile another net holds (x2 each round), the history a contested tile adds
NEG_ROUNDS, NEG_PRES, NEG_GROW, NEG_HIST = 8, 1.0, 2.0, 1.0


def negotiate_on():
    """FK_NEGOTIATE=1: route_layout negotiates before its passes (opt-in while it is measured)."""
    return os.environ.get("FK_NEGOTIATE", "0") == "1"


def _holds(cat, laid):
    """(the tiles a net's carriers stand on, the tiles they forbid another net: those, the tiles
    their belts point into, the tiles their hands reach; their tunnels' spans per axis) --
    ``_ground``'s and ``_ug_spans``' rules for one net."""
    on, forbid = set(), set()
    for e in laid:
        ts, fs, hs, _ins = _ent_ground(cat, e)
        on.update(ts)
        forbid.update(ts)
        forbid.update(fs)
        forbid.update(hs)
    return on, forbid, _ug_spans(cat, laid)


def _negotiate(flat, cat, js, order, ribs, base_ents, net_frames, claims, rib_of, joints, limit,
               terrain):
    """Every net routed as if alone but for a price: on the hard ground (the layout, ribbons,
    terrain, claims), a belt may go where another net's route is, paying (1 + history) *
    (1 + pres * nets there) - 1 a tile (``soft``; its tree and hub only, never a splitter or a
    tap). A clash: a net's carrier on a tile another net's route forbids, or two nets' tunnels
    overlapping on one axis. Round by round the clashing nets are routed again, ``pres`` doubling
    and every contested tile's history growing, until none clashes (PathFinder); a net that
    cannot move (a straight belt, a pair) keeps its tiles and the others go round it. Nets still
    clashing after ``NEG_ROUNDS`` are laid again on hard ground, in order, each seeing the rest.
    Ribbons are laid first, on hard ground, as a pass lays them. -> the pass loop's ``best``."""
    from . import ribbons as ribmod
    added, failed, how, rlines = {}, {}, {}, {}
    hard = list(base_ents)
    plain = []

    def avoid_of(net):
        out = set()
        for other, tiles in claims.items():
            if other != net and rib_of.get(other) != net:
                out |= tiles
        return out

    for key in order:
        if key not in ribs:
            plain.append(key)
            continue
        rib = ribs[key]
        fnet, bnet = net_frames(rib.nets[0])
        got, why, summary = ribmod.route(cat, rib, hard, fnet, js, bnet, limit, terrain,
                                         avoid_of(key), joints)
        if got is None:
            for m in rib.nets:
                if js[m].skip is None:
                    failed[m] = why
            continue
        for m, laid in got.items():
            added[m] = laid
            how[m] = why[m]
            hard += laid
        rlines[key] = summary

    routes, holds, hist = {}, {}, {}
    total = {}                       # tile -> how many nets' routes forbid it

    def hold(net, h):
        old = holds.pop(net, None)
        if old is not None:
            for t in old[1]:
                total[t] -= 1
                if not total[t]:
                    del total[t]
        if h is not None:
            holds[net] = h
            for t in h[1]:
                total[t] = total.get(t, 0) + 1

    todo, rounds, clash = list(plain), 0, set()
    for rnd in range(NEG_ROUNDS):
        rounds = rnd + 1
        pres = NEG_PRES * NEG_GROW ** rnd
        for k, net in enumerate(todo):
            _progress(rnd, k, todo, net, which="negotiate")
            fnet, bnet = net_frames(net)
            avoid = avoid_of(net)
            mine = holds[net][1] if net in holds else ()
            soft = {}                    # _route_in keeps what its own ground blocks out of it
            for t, n in total.items():
                n -= t in mine
                if n > 0:
                    soft[t] = (1.0 + hist.get(t, 0.0)) * (1.0 + pres * n) - 1.0
            got, why = _route_one(cat, js[net], hard, fnet, js, bnet, limit, terrain, avoid,
                                  allow=joints, soft=soft)
            if got is not None and (js[net].wide or js[net].pin):
                got = _serving_only(flat, cat, js[net], hard, got)
            if got is None:
                routes.pop(net, None)
                hold(net, None)
                failed[net] = why
                continue
            failed.pop(net, None)
            routes[net], how[net] = got, why
            hold(net, _holds(cat, got))
        # a clash: a net's carrier on a tile another net's route forbids (its own ends excepted:
        # _ground frees those for it)
        owners, tunnels = {}, ({}, {})
        for net, (_on, forbid, spans) in holds.items():
            for t in forbid:
                owners.setdefault(t, []).append(net)
            for ax in (0, 1):
                for t in spans[ax]:
                    tunnels[ax].setdefault(t, []).append(net)
        clash, bad = set(), set()
        for a, (on, _forbid, _spans) in holds.items():
            ends = set(js[a].ends())
            for t in on:
                if t in ends:
                    continue
                others = [b for b in owners.get(t, ()) if b != a]
                if others:
                    clash.add(a)
                    clash.update(others)
                    bad.add(t)
        for ax in (0, 1):
            for t, nets in tunnels[ax].items():
                if len(set(nets)) > 1:
                    clash.update(nets)
                    bad.add(t)
        for t in bad:
            hist[t] = hist.get(t, 0.0) + NEG_HIST
        if not clash:
            break
        todo = [n for n in plain if n in clash or n in failed]

    # still overlapping: rip those up, lay them again on hard ground in order
    for net in clash:
        routes.pop(net, None)
        hold(net, None)
    for k, net in enumerate([n for n in plain if n in clash]):
        _progress(rounds, k, plain, net, which="negotiate: settle")
        fnet, bnet = net_frames(net)
        ents = hard + [e for laid in routes.values() for e in laid]
        got, why = _route_one(cat, js[net], ents, fnet, js, bnet, limit, terrain, avoid_of(net),
                              allow=joints)
        if got is not None and (js[net].wide or js[net].pin):
            got = _serving_only(flat, cat, js[net], ents, got)
        if got is None:
            failed[net] = why
            how.pop(net, None)
            continue
        failed.pop(net, None)
        routes[net], how[net] = got, why
    for net in plain:
        if net in routes:
            added[net] = routes[net]
    _NEG_LAST.clear()
    _NEG_LAST.update(rounds=rounds, settled=len(clash), routed=len(added), failed=len(failed),
                     why={n: str(w)[:160] for n, w in failed.items()})
    return ((len(added), 0, 0), added, failed, how, rounds, list(order), [], rlines)


#: the last negotiation's numbers: rounds, nets settled on hard ground, routed, failed
_NEG_LAST = {}


def _route_layout(flat, lay, cat, bounds=None, margin=4, retries=None, limit=60000,
                  blocked=(), patch=None, group_budget=None, group_max=None, first=(),
                  skip=(), memo=None, fresh=(), on_net=None, on_reach=None,
                  seeds=None, retry="changed", store=None) -> Routed:
    """Route every net of ``flat`` that has no carriers in ``lay`` yet; -> Routed.

    The nets route per compile unit (PLAN-3.0 §4): each sealed module's own nets on their own,
    innermost first, then the rest. FK_NEGOTIATE=1 (3.0.1): a unit's nets are negotiated
    first, and each negotiated route is laid when its net comes up, if it still fits. A sealed
    module routed whole is compiled: a twin of it later in the build, or in a later build
    through ``store`` (a folder), takes its routes moved to the twin's frame.

    ``bounds`` (x0, y0, x1, y1) limits the search; default: the factory frame plus ``margin``
    tiles east and south (the frame starts at 0, 0). ``blocked``: ground no carrier may use,
    in the factory frame (terrain: water, cliffs). ``patch``: {net: its committed carriers} for
    nets none of whose ends moved (``route --only stale``): each stands as it is, is patched,
    or is routed again (``_standing``), before the nets with nothing laid are routed.

    3.0: each net is laid once, in order, and keeps what it laid; a net that fails gets its group
    try (GROUP_BUDGET), then one promoting search of its own. ``retries`` is not used any more
    (kept for callers)."""
    global _FILTERED, _TAPS, _ARMS, _NEED, _HAND
    _ARMS = armsmod.of()
    _FILTERED = frozenset(k for net in flat.nets.values() for k, _i in net.drivers
                          if k in flat.machines
                          and not set(flat.machines[k].makes) <= set(net.items))
    _TAPS, _NEED, _HAND = _sizing(flat, cat)
    js = jobs(flat, lay, cat)
    flat._port_edges = port_edges(flat, lay, cat)   # 2.0.48: a port is an edge of its frame
    for name, job in js.items():                      # 2.0.49: a stack cell's nets go straight first
        job.straight = any(name.startswith(f"{p}/") and name.count("/") > p.count("/") + 1
                           for p in getattr(flat, "stacks", {}))
    terrain = frozenset(tuple(t) for t in blocked)
    have = {e.owner[1:] for e in lay.entities.values() if e.owner.startswith("~")}
    patch = {n: ents for n, ents in (patch or {}).items() if n in js and n not in have}
    todo = [n for n in js if n not in have and n not in patch and js[n].skip is None
            and not js[n].fluid]
    frames = set()
    owned = {}                                   # instance path -> its frame's tiles
    owned_rect = {}                              # instance path -> its frame (x0, y0, x1, y1)
    sealed = {}                                  # module path -> its frame, for a sealed module
    for path, rec in lay.insts.items():
        if rec.kind in ("Literal", "Bus") or path not in flat.insts:
            continue
        x0, y0, x1, y1 = frame_of(flat, lay, path, cat)
        mine = {(x, y) for x in range(x0, x1) for y in range(y0, y1)}
        frames |= mine
        owned[path] = mine
        owned_rect[path] = (x0, y0, x1, y1)
        if path in flat.sealed:
            sealed[path] = (x0, y0, x1, y1, mine)
    w, h = lay.size or (0, 0)
    box = bounds or (0, 0, w + margin, h + margin)
    # the joint carriers a route may hand over into: a sealed module's port belts and a bus's load and
    # mouth stubs (2.0.36, PLAN-MODULES phase 3)
    joints = set()
    for path, rec in lay.insts.items():
        if path not in flat.insts:
            continue
        for key in flat.insts[path].machines:
            m = flat.machines.get(key)
            if m is not None and (m.extra.get("port") in ("load", "mouth")
                                  or m.extra.get("belt_end")):
                joints.add(key)         # 2.1.19: a Literal's named belt is one too
            if m is not None and m.extra.get("mouth"):
                # a mouth is a belt end of the bus itself: its carrier is the bus's own belt
                name = m.extra.get("at") or ""
                if name.startswith("in") and name[2:].isdigit():
                    joints.add(f"{path}/b{int(name[2:])}_0")
    joints |= _crossings(flat)          # 2.0.48: a route's belt may point across a port's edge
    # two ports that meet on one edge (a stack's cells): the net between them lays nothing
    abut = {n for n in todo if _abuts(js[n])}
    todo = [n for n in todo if n not in abut]

    frames_of_unit = {}
    rect_at = {}                     # 2.1.33: 64-tile bucket -> the instance frames over it
    for path, (a0, b0, a1, b1) in owned_rect.items():
        for bx in range(int(a0) // 64, int(a1 - 1) // 64 + 1):
            for by in range(int(b0) // 64, int(b1 - 1) // 64 + 1):
                rect_at.setdefault((bx, by), []).append(path)

    def net_frames(net):
        """The blocked frames for ``net`` and its box: a net inside a sealed module routes in that
        module's frame (bounded by the innermost one); every other net keeps every frame blocked,
        so a crossing can only be a port (PLAN-MODULES phase 3). Only the frames of the modules
        the net is inside are free: a template's frame or a sealed child's inside them stays
        blocked, as it is for a net outside (2.0.40: the child's frame was freed with its
        parent's, and a parent net ran straight across a sealed child).

        Two sealed frames that overlap (neither inside the other) share the tiles they both
        cover (2.0.75): a net of either may use them, and what the other has laid there is in
        its way as an entity; the rest of the other's frame, and every template frame in it,
        stays blocked."""
        parts = net.split("/")[:-1]
        inside = []
        for i in range(1, len(parts) + 1):
            pre = "/".join(parts[:i])
            if pre in sealed:
                inside.append(pre)
        if not inside:
            return frames, box
        inner = inside[-1]
        # 2.1.32: the same for every net of one innermost module (worked out again for each net,
        # 12 s of a 3-band megabase grid's route)
        if inner in frames_of_unit:
            return frames_of_unit[inner]
        x0, y0, x1, y1, mine = sealed[inner]
        # 2.1.32: only the tiles inside the module's own box (the net routes in it, and its grid
        # blocks all else), frame by frame as rectangles: every frame's tiles for every module
        # was most of a megabase grid's route setup
        blocked = set()
        near = {p for bx in range(int(x0) // 64, int(x1 - 1) // 64 + 1)
                for by in range(int(y0) // 64, int(y1 - 1) // 64 + 1)
                for p in rect_at.get((bx, by), ())}
        for path in sorted(near):
            a0, b0, a1, b1 = owned_rect[path]
            if path in inside:
                continue
            if path in sealed and not path.startswith(inner + "/") \
                    and not inner.startswith(path + "/"):
                continue                              # a foreign sealed frame: the overlap is shared
            c0, d0, c1, d1 = max(a0, x0), max(b0, y0), min(a1, x1), min(b1, y1)
            if c0 < c1 and d0 < d1:
                blocked |= {(x, y) for x in range(c0, c1) for y in range(d0, d1)}
        frames_of_unit[inner] = frozenset(blocked), (x0, y0, x1, y1)
        return frames_of_unit[inner]
    base = lay.copy()
    standing, patched, again, held = [], {}, {}, {}
    if patch:
        claims = _direct_claims(cat, js, todo, list(base.entities.values()), frames, box, terrain)
        standing, patched, again, held = _standing(flat, base, cat, js, patch, frames, box, limit,
                                                   terrain, set().union(*claims.values()),
                                                   net_frames)
        todo += [n for n in again if js[n].skip is None and not js[n].fluid]
    kept = sorted(n for n in js if n in have or n in standing)
    # 3.0: the nets the build script leaves to the player (route --skip)
    manual = sorted(n for n in todo if _named(n, skip))
    todo = [n for n in todo if n not in manual]
    todo.sort(key=lambda n: (-len(js[n].ends()), n))
    claims = _direct_claims(cat, js, todo, list(base.entities.values()), frames, box, terrain)
    # 2.1: ribbons (a net each, a bundle, a wide net) are routed as one, first: they need the
    # widest ways. A ribbon is routed whole only when every net of it is laid this run.
    from . import ribbons as ribmod
    pending = set(todo) | {n for n in js if n not in have and n not in patch and n not in abut
                           and not js[n].fluid}
    pending = {n for n in pending if js[n].m2m is None}     # 2.1 --m2m: lanes, not a ribbon
    ribs = ribmod.plan(flat, cat, js, pending)
    ribs = {k: r for k, r in ribs.items() if any(n in todo for n in r.nets)}
    rib_of = {n: k for k, r in ribs.items() for n in r.nets}
    rib_seen = {}
    budget = GROUP_BUDGET if group_budget is None else float(group_budget)
    gmax = GROUP_MAX if group_max is None else int(group_max)
    memo = {} if retry == "always" else (memo or {})
    seeds = seeds or {}

    def ranked(keys):
        """3.0: the build script's order (route --first): the nets it names, in its order, then
        the rest as before."""
        if not first:
            return list(keys)

        def rank(key):
            names = ribs[key].nets if key in ribs else [key]
            return next((i for i, p in enumerate(first) if any(_named(m, (p,)) for m in names)),
                        len(first))
        return sorted(keys, key=rank)

    def same(net, key):
        """Its last failure stands: ``--retry-failed never``, or its ground is as it was."""
        return retry == "never" or memo[net].get("key") == key

    def unrouted(key, failed):
        if key in ribs:
            return any(n in failed for n in ribs[key].nets)
        return key in failed

    claim_at = {}                               # 2.1.33: tile -> the nets that claim it
    for other, tiles in claims.items():
        for t in tiles:
            claim_at.setdefault(t, set()).add(other)

    def avoid_of(net, bound):
        """The tiles other nets' direct inserters claim, inside ``bound`` (the net's search never
        leaves it). 2.1.33: was every claim of the layout, unioned again for every net."""
        x0, y0, x1, y1 = bound
        if (x1 - x0) * (y1 - y0) < len(claim_at):
            tiles = ((x, y) for x in range(x0, x1) for y in range(y0, y1) if (x, y) in claim_at)
        else:
            tiles = (t for t in claim_at if x0 <= t[0] < x1 and y0 <= t[1] < y1)
        return {t for t in tiles
                if any(o != net and rib_of.get(o) != net for o in claim_at[t])}

    def avoid(net):
        """``avoid_of`` in the box of ``net`` (a net, or a ribbon's key)."""
        return avoid_of(net, net_frames(ribs[net].nets[0] if net in ribs else net)[1])

    # -- the routing state every unit adds to ------------------------------------------------------
    ents = list(base.entities.values())          # the ground: the layout and every carrier laid
    added, failed, how, swaps, rlines = {}, {}, {}, [], {}

    # -- FK_NEGOTIATE=1 (3.0.1): negotiation proposes a unit's routes; ``lay_net`` lays a
    # proposed route when it still fits the ground, so seeds, holds, the memo and the group try
    # apply to negotiated nets as to any. Empty unless FK_NEGOTIATE=1.
    proposed = {}

    # -- 3.0: each net once, in order, keeping what it lays -----------------------------------------
    def unsearched(net, why):
        why = why.split("; not searched again:")[0]          # the net file's why has it already
        when = ("the route step's --retry-failed never" if retry == "never"
                else "it failed on this same ground last build")
        return f"{why}; not searched again: {when} (route --again {net} tries it)"
    fresh = tuple(fresh) + tuple(seeds)     # a seeded net is searched, whatever it did last
    remembered, last_key, known = {}, {}, {}
    held_now = set()                   # nets the player held while this build ran (on_reach)
    groups = []

    def landed(net, took):
        """Tell ``on_net`` how ``net`` (or each net of a ribbon) landed."""
        if on_net is None:
            return
        for m in (ribs[net].nets if net in ribs else [net]):
            if m in added:
                on_net(m, "routed", added[m], how.get(m, ""), took)
            elif m in failed:
                on_net(m, "unrouted", (), failed[m], took)

    def key_of(net, ents):
        _fnet, bnet = net_frames(net)
        return _ground_key(cat, js[net], ents, bnet, terrain, avoid(net), (limit, budget, gmax))

    def lay_net(net, ents, k, steps, which, promo=False):
        """Lay ``net`` (a net, or a ribbon's key) on ``ents``, into added / failed / how: -> the
        entities with its carriers. ``promo``: only the promoting search (the plain one failed on
        this ground already)."""
        _progress(0, k, steps, net, which=which)
        if on_reach is not None and net not in ribs:
            mine = on_reach(net, which, k, len(steps))
            if mine is not None:
                taken = {t for e in ents for t in fkpmod.tiles_of(cat, e)}
                mine = [e for e in mine if not fkpmod.tiles_of(cat, e) & taken]
                added[net], how[net] = mine, "held: your net file, as it stands"
                failed.pop(net, None)
                held_now.add(net)
                return ents + mine
        av = avoid(net)
        if net in ribs:
            rib = ribs[net]
            fnet, bnet = net_frames(rib.nets[0])
            # the same ground as a try before (the same nets laid first): the same answer
            seen = (net, tuple(sorted(added)), tuple(s[2] for s in swaps))
            if seen not in rib_seen:
                rib_seen[seen] = ribmod.route(cat, rib, ents, fnet, js, bnet, limit,
                                              terrain, av, joints)
            got, why, summary = rib_seen[seen]
            if got is None:
                for m in rib.nets:
                    if js[m].skip is None:
                        failed[m] = why
                return ents
            for m, laid in got.items():
                added[m] = laid
                how[m] = why[m]
                failed.pop(m, None)
                ents = ents + laid
            rlines[net] = summary
            return ents
        fnet, bnet = net_frames(net)
        if net in seeds:
            got, why = _route_seeded(cat, js[net], ents, fnet, js, bnet, limit, terrain, av,
                                     seeds[net], allow=joints)
            if got is None:
                failed[net] = f"through your seed: {why}"
                return ents
            failed.pop(net, None)
            added[net], how[net] = got, why
            return ents + got
        offer = None if promo else proposed.pop(net, None)
        if offer is not None:
            taken = {t for e in ents for t in fkpmod.tiles_of(cat, e)}
            if any(fkpmod.tiles_of(cat, e) & taken for e in offer[0]):
                offer = None                  # the ground changed under it: searched as any net
        if offer is not None:
            got, why = offer
        else:
            got, why = (None, failed.get(net)) if promo else \
                _route_one(cat, js[net], ents, fnet, js, bnet, limit, terrain, av, allow=joints)
        if got is None and promo:
            # once more, crossing other nets' straight belts by promoting them
            prom = _route_promoted(cat, js[net], ents, fnet, js, bnet, limit, terrain, av,
                                   joints)
            if prom is not None:
                got, why, mine = prom
                gone = {k for s in mine for k in s[0]}
                ents = [e for e in ents if e.key not in gone] + [e for s in mine for e in s[1]]
                _swap_added(added, mine)
                swaps.extend(mine)
        if got is None:
            failed[net] = why
            return ents
        failed.pop(net, None)
        if js[net].wide or js[net].pin:
            got = _serving_only(flat, cat, js[net], ents, got)
        added[net] = got
        how[net] = why
        return ents + got

    def inside(other, unit):
        """True when net ``other`` belongs to compile unit ``unit`` or to a unit nested in it (the
        top level, ``""``, holds them all)."""
        if not unit:
            return True
        mine = _unit(other, sealed)
        return mine == unit or mine.startswith(unit + "/")

    def in_way(net, unit):
        """The nets laid this run in ``unit`` or a unit nested in it that stand in ``net``'s way:
        ``net`` searched once with them lifted, and the nets whose carriers are on or beside that
        way, most carriers first, at most ``gmax`` (never a ribbon's: a ribbon is laid whole).
        None when ``net`` finds no way even then: the ground, not the order, stops it. A sibling
        unit's routes are never lifted (a unit's failed net never rips up another unit's
        routes); a nested one's may be, laid again in its own frame (a top-level net past a
        routing.fk Waypoint lifts the Waypoint's belt), but only a nested unit whose frame the
        way enters (3.0.1): a module the way only runs beside keeps its routes."""
        lift = {e.key: other for other, laid in added.items() if inside(other, unit)
                and other not in rib_of and other not in held_now for e in laid}
        fnet, bnet = net_frames(net)
        _progress(0, 0, [net], net, which=f"the way of {net}")
        got, _why = _route_one(cat, js[net], [e for e in ents if e.key not in lift], fnet, js,
                               bnet, limit, terrain, avoid(net), allow=joints)
        if got is None:
            return None
        path, way = set(), set()
        for e in got:
            path |= fkpmod.tiles_of(cat, e)
        for x, y in path:
            way |= {(x + dx, y + dy) for dx in range(-GROUP_MARGIN, GROUP_MARGIN + 1)
                    for dy in range(-GROUP_MARGIN, GROUP_MARGIN + 1)}

        ends = set(js[net].ends())

        def entered(v):
            """The way enters ``v``'s frame, or the net ends at it (a port on its edge)."""
            x0, y0, x1, y1 = sealed[v][:4]
            return any(x0 <= x < x1 and y0 <= y < y1 for x, y in path) or \
                any(x0 - 1 <= x <= x1 and y0 - 1 <= y <= y1 for x, y in ends)
        units_in = {}

        def liftable(other):
            """``other``'s unit is ``unit``, or every sealed unit from it out to ``unit`` is one
            the way enters."""
            mine = _unit(other, sealed)
            if mine == unit:
                return True
            if mine not in units_in:
                chain, v = [], mine
                while v and v != unit:
                    chain.append(v)
                    v = "/".join(v.split("/")[:-1])
                    while v and v not in sealed and v != unit:
                        v = "/".join(v.split("/")[:-1])
                units_in[mine] = all(entered(c) for c in chain)
            return units_in[mine]
        hits = {}
        for e in ents:
            other = lift.get(e.key)
            if other is not None and other != net and fkpmod.tiles_of(cat, e) & way \
                    and liftable(other):
                hits[other] = hits.get(other, 0) + 1
        return sorted(hits, key=lambda o: (-hits[o], o))[:gmax]

    def route_once(order, unit):
        """3.0: lay compile unit ``unit``'s ``order`` (net names and ribbon keys) once each, in
        order; a net that failed gets its group try on the budget, then one promoting search.
        -> passes (1 or 2)."""
        nonlocal ents
        # a net that failed last build, on ground as it stands now, is not searched again; one
        # whose ground differs waits for the end of the pass, where its last search was
        later = []
        for k, net in enumerate(order + [None]):
            if net is None:
                for j, net in enumerate(later):
                    if same(net, key_of(net, ents)):
                        failed[net] = memo[net]["why"]
                        remembered[net] = memo[net]
                    else:
                        t0 = time.monotonic()
                        ents = lay_net(net, ents, j, later, "pass 1, the nets that failed last build")
                        landed(net, time.monotonic() - t0)
                break
            if memo.get(net) and net not in ribs and not _named(net, fresh):
                if same(net, key_of(net, ents)):
                    failed[net] = memo[net]["why"]
                    remembered[net] = memo[net]
                else:
                    later.append(net)
                continue
            t0 = time.monotonic()
            ents = lay_net(net, ents, k, order, "pass 1")
            landed(net, time.monotonic() - t0)
        # a net that failed: laid again together with the nets in its way, for up to the budget
        for net in [n for n in order if n in failed and n not in ribs and n not in remembered]:
            if budget <= 0 or net not in failed:
                continue
            t0 = time.monotonic()
            members = in_way(net, unit)
            if members is None:
                groups.append((net, None, False, time.monotonic() - t0))
                continue
            if not members:
                continue
            steps, tried, settled = [net] + members, set(), False
            while tuple(steps) not in tried:
                tried.add(tuple(steps))
                saved = (dict(added), dict(failed), dict(how), dict(rlines), ents)
                members = [m for m in steps if m != net]
                gone = {e.key for m in members for e in added[m]}
                trial = [e for e in ents if e.key not in gone]
                for m in members:
                    added.pop(m)
                    how.pop(m, None)
                hopeless = False
                for k, m in enumerate(steps):
                    trial = lay_net(m, trial, k, steps, f"the group of {net}")
                    if m not in added and m == net:
                        # the failed net, with the others lifted, fails: no order of them helps
                        hopeless = True
                        break
                    if time.monotonic() - t0 >= budget and k < len(steps) - 1:
                        hopeless = True               # the budget is spent: no half-laid group
                        break
                if not hopeless and all(m in added for m in steps):
                    ents, settled = trial, True
                    for m in steps:
                        landed(m, time.monotonic() - t0)
                    break
                lost = [m for m in steps if m not in added]
                for d, was in zip((added, failed, how, rlines), saved):
                    d.clear()
                    d.update(was)
                ents = saved[4]
                if hopeless or time.monotonic() - t0 >= budget:
                    break
                # a net of the group that failed: what stands in its way joins the group
                for m in lost:
                    if m != net and len(steps) <= gmax:
                        more = in_way(m, unit) or []
                        steps += [o for o in more if o not in steps][:gmax + 1 - len(steps)]
                # the failed net stays first (the group is for it), then the ones that failed now
                steps = [net] + [m for m in lost if m != net] + \
                    [m for m in steps if m not in lost and m != net]
            groups.append((net, len(steps) - 1, settled, time.monotonic() - t0))
        # 2.0.78: last, a net still unrouted may promote other nets' belts (its own search only)
        passes = 1
        left = [n for n in order if n in failed and n not in ribs and n not in remembered]
        for k, net in enumerate(left):
            passes = 2
            t0 = time.monotonic()
            ents = lay_net(net, ents, k, left, "the promoting pass", promo=True)
            landed(net, time.monotonic() - t0)
            if net in failed:
                last_key[net] = key_of(net, ents)      # the ground of its last search
        return passes

    # PLAN-3.0 §4 (sealed modules compile once): each sealed module is a compile unit. Its own
    # nets (those whose innermost sealed module it is) are laid on their own, the innermost
    # units first, then the nets outside every sealed module; a unit's failed net never rips up
    # another unit's routes. A unit whose twin (the same module, the same ground in its frame)
    # routed earlier in this build, or in an earlier build (``store``), takes the twin's routes
    # moved to its frame, and the judge (``_repair``) checks them as any route.
    unit_keys = {}
    for key in list(ribs) + [n for n in todo if n not in rib_of]:
        unit_keys.setdefault(_unit(ribs[key].nets[0] if key in ribs else key, sealed),
                             []).append(key)
    nets_of = {u: [m for k in keys for m in (dict.fromkeys(ribs[k].nets) if k in ribs else [k])]
               for u, keys in unit_keys.items()}
    units = sorted(unit_keys, key=lambda u: (-(u.count("/") + 1) if u else 1, u))
    gidx = _Buckets(ents)            # 2.1.33: the ground by place, for the per-unit reads
    order, passes = [], 1
    claimed = {}
    for other, tiles in claims.items():
        for t in tiles:
            claimed.setdefault(t, set()).add(other)
    # 3.1: the one compile store (fkcore.compiled); a path names the game folder's
    if isinstance(store, str):
        store = compiledmod.Store(store)
    from . import fluid_route as fluidmod
    settings = (tuple(_HANDS), tuple(getattr(ribmod, "_UG", ()) or ()), tuple(fluidmod._LAY),
                _M2M)
    twins, compiled, reused, keys_of, cinfo = {}, {}, {}, {}, {}
    for u in units:
        mine = nets_of[u]
        ckey = None
        if u:
            child_keys = [(c[len(u) + 1:], k) for c, k in keys_of.items()
                          if c.startswith(u + "/")]
            ckey = unit_key(flat, lay, cat, js, u, sealed, mine, gidx, terrain, child_keys,
                            seeds, settings)
            keys_of[u] = ckey
            cinfo[u] = {"module": _module_of(flat, lay, u), "key": ckey, "nets": list(mine),
                        "from": None, "stored": 0, "refused": []}
        if ckey is not None:
            got, src = None, None
            if ckey in twins:
                src = twins[ckey]
                if lay.insts[src].rot % 4 == lay.insts[u].rot % 4:
                    got = _moved_routes(added, nets_of[src], src, sealed[src], u, sealed[u])
                elif all(n in added for n in nets_of[src]):
                    # a twin turned the other way (2.1.32): through the unturned terms
                    rel = _unit_rel({n: added[n] for n in nets_of[src]}, src, sealed[src],
                                    lay.insts[src].rot)
                    got = None if rel is None else _unit_abs(rel, u, sealed[u], lay.insts[u].rot)
            elif store:
                got = _store_load(store, ckey, cinfo[u]["module"], u, sealed[u],
                                  lay.insts[u].rot)
                src = "the compiled store" if got is not None else None
                cinfo[u]["refused"] = store.refused.get((cinfo[u]["module"], ckey), [])
            if got is not None and set(got) == set(mine) \
                    and _fits(cat, got, gidx, sealed[u], net_frames(mine[0])[0], terrain,
                              claimed, set(mine)):
                for net in mine:
                    added[net] = got[net]
                    how[net] = f"laid as {src} laid it (compiled once, PLAN-3.0 §4)"
                    failed.pop(net, None)
                    ents = ents + got[net]
                    gidx.extend(got[net])
                order += mine
                reused[u] = src
                cinfo[u]["from"] = "store" if src == "the compiled store" else f"twin {src}"
                for net in mine:
                    landed(net, 0.0)
                continue
        keys = ranked(unit_keys[u])
        swaps_before = len(swaps)
        if negotiate_on():
            plain = [n for n in keys if n not in ribs and n not in seeds
                     and not (memo.get(n) and not _named(n, fresh))]
            if plain:
                neg = _negotiate(flat, cat, js, plain, {}, list(ents), net_frames, claims,
                                 rib_of, joints, limit, terrain)
                _score, n_added, _f, n_how = neg[:4]
                proposed.update({n: (laid, n_how.get(n, "")) for n, laid in n_added.items()})
        u_order, u_passes = keys, route_once(keys, u)
        proposed.clear()
        gidx = _Buckets(ents)
        order += [m for n in u_order for m in (dict.fromkeys(ribs[n].nets) if n in ribs else [n])]
        passes = max(passes, u_passes)
        if ckey is not None and not any(n in failed for n in mine) \
                and len(swaps) == swaps_before:
            twins.setdefault(ckey, u)
            compiled[u] = ckey
            cinfo[u]["from"] = "routed"
    added, failed, how, rib_lines = dict(added), dict(failed), dict(how), dict(rlines)
    new = base.copy()
    for gone, _new, _text in swaps:
        for k in gone:
            new.entities.pop(k, None)
    for _gone, more, _text in swaps:
        for e in more:
            if e.owner[1:] not in added:
                new.add(e)         # a standing net's (an added one's are in its carriers now)
    for net in todo:
        for e in added.get(net, ()):
            new.add(e)
    _repair(flat, new, cat, js, order, added, failed, how, frames, box, limit, terrain, swaps,
            (ribs, rib_of, rib_lines, net_frames, claims, joints))
    for net in sorted(failed):
        if net in rib_of or net in remembered:
            continue          # a ribbon's net is never tried alone (it would be a loose belt)
        guard = _guarded(cat, js[net], list(new.entities.values()), frames, js, box, limit,
                         terrain)
        if guard:
            failed[net] = f"{failed[net]}; {guard}"
    # 3.0: remembered while the ground of its last search stands as it does now
    final = list(new.entities.values())
    for net, key in last_key.items():
        if net in failed and key_of(net, final) == key:
            known[net] = {"key": key, "why": failed[net]}
    for net, was in remembered.items():
        known[net] = was
        failed[net] = unsearched(net, was["why"])
    failed.update(held)
    fluid_todo = sorted(n for n in js if js[n].fluid and n not in have and n not in kept
                        and js[n].skip is None)

    # a fluid net is searched in the whole box: its key is the ground as the fluid router finds it
    # (the fluid nets it lays this run are not in it), one hash for all of them
    if fluid_todo:
        world = _ground_key(cat, NetJob(""), list(new.entities.values()), box, terrain, (), (),
                            whole=True)
    f_keys = {net: hashlib.sha1(repr((world, js[net].signature(), sorted(js[net].ends()),
                                      "fluid", limit, budget)).encode()).hexdigest()
              for net in fluid_todo}
    for net in list(fluid_todo):
        if memo.get(net) and not _named(net, fresh) and same(net, f_keys[net]):
            known[net] = memo[net]
            failed[net] = unsearched(net, memo[net]["why"])
            fluid_todo.remove(net)
    plugin = _fluid_plugin() if fluid_todo else None
    if plugin is not None:
        f_added, f_how, f_failed = plugin.route(flat, new, cat, fluid_todo, terrain, box, limit,
                                                budget=budget)
        for net in fluid_todo:
            for e in f_added.get(net, ()):
                new.add(e)
        added.update(f_added)
        how.update(f_how)
        failed.update(f_failed)
        for net in f_failed:
            if net in js:
                known[net] = {"key": f_keys.get(net), "why": f_failed[net]}
    xs = [t[0] + 1 for e in new.entities.values() for t in fkpmod.tiles_of(cat, e)]
    ys = [t[1] + 1 for e in new.entities.values() for t in fkpmod.tiles_of(cat, e)]
    new.size = (max(xs + [w]), max(ys + [h]))
    lines = []
    for net in sorted(js, key=fkpmod.natural_key):
        job = js[net]
        if net in abut:
            if job.direct:
                lines.append(f"routed    {net}: {len(job.direct)} -> "
                             f"{len({c for _d, c in job.direct})}, nothing to lay "
                             "(dropped straight in)")
            else:
                lines.append(f"routed    {net}: {job.drivers()} -> {job.consumers()}, nothing "
                             "to lay (its ports meet on one edge)")
        elif net in kept:
            lines.append(f"kept      {net}: its carriers are already laid (pinned)")
        elif net in patched:
            lines.append(f"patched   {net}: {patched[net]}")
        elif job.skip:
            lines.append(f"skipped   {net}: {job.skip}")
        elif net in manual:
            lines.append(f"manual    {net}: the build script leaves it to you (route --skip)")
        elif net in added and job.fluid:
            lines.append(f"routed    {net}: {how[net]}")
        elif net in added:
            lines.append(f"routed    {net}: {job.drivers()} -> {job.consumers()}, {how[net]}")
        else:
            lines.append(f"FAILED    {net}: {failed.get(net)}")
    for gone, _more, text in swaps:
        lines.append(f"promoted  {text.split(' crossed ', 1)[0]}: {gone[0]}, {gone[1]}, {gone[2]} "
                     f"are an underground pair now, so another net's belt crosses it")
    for key in sorted(rib_lines, key=fkpmod.natural_key):
        if all(n in added for n in ribs[key].nets):
            lines.append(rib_lines[key])
    short = {}
    for net in sorted(js, key=fkpmod.natural_key):
        if js[net].m2m is not None and net in added and net in todo:
            from . import m2m as m2mmod
            text, why = m2mmod.report(cat, js[net], added[net], list(new.entities.values()))
            lines.append(text)
            if why:
                short[net] = why
    sealed_units = [u for u in units if u]
    if sealed_units:
        stored = 0
        for u, ckey in compiled.items():
            if store and all(n in added and n not in failed for n in nets_of[u]):
                x0, y0, x1, y1, _t = sealed[u]
                inputs = {"frame": [x1 - x0, y1 - y0],
                          "nets": [_renamed(n, u, "") for n in nets_of[u]],
                          "children": sorted((c[len(u) + 1:], k) for c, k in keys_of.items()
                                             if c.startswith(u + "/")),
                          "seeded": sorted(_renamed(n, u, "") for n in nets_of[u]
                                           if seeds.get(n)),
                          "settings": repr(settings)}
                cinfo[u]["stored"] = _store_save(
                    store, ckey, cinfo[u]["module"], u, sealed[u],
                    {n: [new.entities.get(e.key, e) for e in added[n]] for n in nets_of[u]},
                    lay.insts[u].rot, inputs)
                stored += cinfo[u]["stored"]
        twin = sum(1 for s in reused.values() if s != "the compiled store")
        lines.append(f"compile   {len(sealed_units)} sealed module{'s' if len(sealed_units) != 1 else ''}"
                     f", each routed on its own in its frame "
                     f"({sum(len(nets_of[u]) for u in sealed_units)} nets): {twin} laid as a twin "
                     "laid this build, "
                     f"{len(reused) - twin} from the compiled store"
                     + (f", {stored} stored" if store else ""))
        if store and store.out:
            lines += compiledmod.old_lines(store.out)
    for net, size, settled, took in groups:
        if size is None:
            lines.append(f"group     {net}: no way even with the nets laid this run lifted: the "
                         f"ground stops it, not the order ({took:.1f} s)")
            continue
        lines.append(f"group     {net}: laid again with {size} net{'s' if size != 1 else ''} in its "
                     f"way, {'all routed' if settled else 'not settled in the budget, left alone'}"
                     f" ({took:.1f} s)")
    lines += seal_checks(flat, new, cat)
    lines += port_checks(flat, new, cat)
    lines += _filter_loads(flat, new, cat)
    return Routed(new, lines, sorted(set(added) | abut), failed, kept, passes, patched, again,
                  short, known, cinfo)


def seal_checks(flat, lay, cat):
    """crosses-frame (PLAN-MODULES phase 3): a net's carrier inside a sealed module's frame that the net does
    not belong to -- a crossing that is not at a port -- or one of the module's own nets' carriers
    outside its frame. One line per carrier at fault; none when every sealed module is sealed.
    FRAME (2.0.40): an entity of the module's own (a machine, a template's belt) outside its frame.
    A carrier of another sealed module's net on the tiles the two frames share is not a crossing
    (2.0.75: overlapping frames share those tiles)."""
    out = []
    boxes = {}
    for path in lay.insts:
        if path in flat.insts and path in flat.sealed:
            try:
                x0, y0, x1, y1 = frame_of(flat, lay, path, cat)
            except Exception:
                continue
            boxes[path] = (x0, y0, x1, y1)

    def shared(path, net, tiles):
        """True when ``net`` is a net of a sealed module foreign to ``path`` (neither inside the
        other) whose frame holds ``tiles`` too."""
        scopes = [p for p in boxes if net.startswith(p + "/")]
        if not scopes:
            return False
        q = max(scopes, key=len)
        return not q.startswith(path + "/") and not path.startswith(q + "/") \
            and all(_in_box(t, boxes[q]) for t in tiles)

    for path, rec in sorted(lay.insts.items()):
        if path not in boxes:
            continue
        x0, y0, x1, y1 = boxes[path]
        frame = {(x, y) for x in range(x0, x1) for y in range(y0, y1)}
        for key, e in lay.entities.items():
            net = e.attrs.get("net")
            tiles = fkpmod.tiles_of(cat, e)
            inside = all(t in frame for t in tiles)
            if key.startswith(path + "/") and not inside:
                # FRAME (2.0.40): the module's own contents past its declared size (2.0.37's rib:
                # a row one tile past the south edge, its port under the assembler)
                out.append(f"FRAME     {key} ({e.name}) is part of sealed module {path} but stands "
                           f"outside its frame {x0},{y0}..{x1 - 1},{y1 - 1}: grow its size or "
                           "move it in")
                continue
            if not net:
                continue
            mine = net == path or net.startswith(path + "/")
            if inside and not mine and not shared(path, net, tiles):
                out.append(f"crosses-frame      {key} ({net}) stands inside sealed module {path}: a net "
                           "crosses a sealed frame only at a port's edge")
            elif mine and not inside:
                out.append(f"crosses-frame      {key} ({net}) belongs to sealed module {path} but stands "
                           "outside its frame")
    return out


def _in_box(t, box):
    return box[0] <= t[0] < box[2] and box[1] <= t[1] < box[3]


def port_checks(flat, lay, cat):
    """port, warn and never gate.

    2.0.48: a port is an edge of its module's frame, and the routes meet across it: the module's
    own on the tile just inside, the parent's on the tile just outside. A machine standing on
    either of those tiles (the rib's assembler against its port, say) leaves the route nowhere to
    meet the port. One line per taken tile; another module's route there is fine (a stack's next
    cell meets the port on the same edge).

    2.0.41 (PLAN-GLOBAL phase A): a committed port record and a pin must agree; the pin wins, since
    the source is what someone decided (the rebuild then records the pinned tile)."""
    out = []
    for key in sorted(getattr(flat, "port_records", ())):
        port = flat.ports.get(key)
        if port is None:
            continue
        rec = flat.port_records[key]
        why = record_misfit(port, rec)
        if why is not None:
            out.append(f"port      {key}: the committed tile {rec.x},{rec.y} is dropped: {why}")
    occ = {}
    for key, e in lay.entities.items():
        for tile in fkpmod.tiles_of(cat, e):
            occ.setdefault(tile, key)
    edges = getattr(flat, "_port_edges", None) or port_edges(flat, lay, cat)
    for key in sorted(edges, key=fkpmod.natural_key):
        pe = edges[key]
        name = f"{key.rsplit('/', 1)[0]}.{key.rsplit('/', 1)[1]}"
        for where, tile in (("inside", pe.inside), ("outside", pe.outside)):
            other = occ.get(tuple(tile))
            if other is None or other.startswith("~"):
                continue
            out.append(f"port      {name}: the tile {where} its edge, {tile[0]},{tile[1]}, is "
                       f"taken by {other}")
    return out


def _ports(flat):
    """The sealed modules' port edges of the layout being routed (``expand.port_edges``)."""
    return getattr(flat, "_port_edges", None) or {}


def _crossings(flat):
    """{(from tile, to tile)}: the edges a belt crosses at a port; a route's belt may point across
    one into the other side's belt (2.0.48)."""
    return frozenset((tuple(pe.src), tuple(pe.dst)) for pe in _ports(flat).values())


def _abuts(job):
    """A net whose one driver's belt points straight into its one consumer's first belt: two
    ports meeting on one edge (2.0.48, a stack's cells). It is connected and lays nothing."""
    if job.direct and not (job.taps or job.sources or job.starts or job.goals):
        return True                     # 3.0: every driver drops straight into its consumer
    if job.taps or job.sources or len(job.starts) != 1 or len(job.goals) != 1:
        return False
    (st, sh), (gt, gh) = job.starts[0], job.goals[0]
    return sh == gh and tuple(st) == (gt[0] + _DX[gh], gt[1] + _DY[gh])


def _repair(flat, new, cat, js, order, added, failed, how, frames, box, limit, terrain,
            swaps=None, ribbons=None):
    """Judge the belt routes laid this run; re-route a net that leaks or does not connect around
    the tiles at fault (promoting other nets' belts when nothing else routes, into ``swaps``),
    else refuse it (its carriers go, the judge's line is the reason). A ribbon's net (2.1,
    ``ribbons``: the ribbons, net -> ribbon, their report lines, net_frames, claims, joints) is
    re-routed with its whole ribbon, or the whole ribbon is refused."""
    avoid = {}
    ribs, rib_of, rib_lines, net_frames, claims, joints = ribbons or ({}, {}, {}, None, {}, ())
    for round_ in range(_REPAIRS + 1):
        mine = [n for n in order if n in added]
        bad = _judge(flat, new, cat, mine, js)
        if not bad:
            return
        seen = set()
        for net in sorted(bad, key=order.index):
            reason, tiles = bad[net]
            if net in rib_of:
                key = rib_of[net]
                if key in seen:
                    continue
                seen.add(key)
                rib = ribs[key]
                why_all = "; ".join(f"{m}: {bad[m][0]}" for m in dict.fromkeys(rib.nets)
                                    if m in bad)
                for m in dict.fromkeys(rib.nets):
                    if m in bad:
                        avoid.setdefault(key, set()).update(bad[m][1])
                    for e in added.pop(m, ()):
                        new.entities.pop(e.key, None)
                    how.pop(m, None)
                rib_lines.pop(key, None)
                if round_ == _REPAIRS or not avoid.get(key):
                    for m in rib.nets:
                        failed[m] = f"ribbon {key}: {why_all}"
                    continue
                fnet, bnet = net_frames(rib.nets[0])
                near = set(avoid[key])
                for other, ts in claims.items():
                    if other not in rib.nets:
                        near |= ts
                from . import ribbons as ribmod
                got, why, summary = ribmod.route(cat, rib, list(new.entities.values()), fnet, js,
                                                 bnet, limit, terrain, near, joints)
                if got is None:
                    for m in rib.nets:
                        failed[m] = f"ribbon {key}: {why_all}; re-routed around it: {why}"
                    continue
                for m, laid in got.items():
                    added[m] = laid
                    how[m] = why[m]
                    for e in laid:
                        new.add(e)
                rib_lines[key] = summary
                continue
            for e in added.pop(net):
                del new.entities[e.key]
            how.pop(net, None)
            if round_ == _REPAIRS or not tiles:
                failed[net] = reason
                continue
            avoid.setdefault(net, set()).update(tiles)
            got, why = _route_one(cat, js[net], list(new.entities.values()), frames, js, box,
                                  limit, terrain, avoid[net], allow=_crossings(flat))
            if got is None and swaps is not None:
                prom = _route_promoted(cat, js[net], list(new.entities.values()), frames, js, box,
                                       limit, terrain, avoid[net], _crossings(flat))
                if prom is not None:
                    got, why, mine = prom
                    gone = {k for s in mine for k in s[0]}
                    for k in gone:
                        del new.entities[k]
                    for s in mine:
                        for e in s[1]:
                            new.add(e)
                    _swap_added(added, mine)
                    swaps += mine
            if got is None:
                failed[net] = f"{reason}; re-routed around it: {why}"
                continue
            added[net] = got
            how[net] = why
            for e in got:
                new.add(e)
