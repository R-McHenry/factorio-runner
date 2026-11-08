"""fkcore.connect: the directed carrier graph of a layout, and per net: connected yes/no, SPILL.

:func:`graph` takes an entity list (a factory :class:`fkcore.fkp.Layout`, a composed
:class:`fkcore.compose.Surface`, or any entities with key, name, x, y, dir, attrs) and the catalog.
Every entity is a node; an edge is one way items move from one entity into another:

- ``belt``: a belt, an underground output or a splitter half moves items onto the tile ahead. A
  transport belt there takes them unless it faces back (head-on), an underground input only from
  behind, a splitter only from behind.
- ``curve`` / ``sideload``: the same into the side of a belt. A curve when it is that belt's only
  input (nothing from behind), else a side-load.
- ``ug_sideload``: the same into the side of an underground (either end). Only the incoming lane
  on the open side gets through (Rory); this graph has no lanes, so the edge counts, and the lane
  graph (``fkcore.lanes``, the routed rates' ``WRONG-LANE`` line) says when a net's items are on the
  closed side. 1.x warned ``UG_SIDELOAD`` on every one; 2.0 tracks lanes, and that line went.
- ``ug``: an underground input to the output the game pairs it with. Both ends face the flow. The
  input looks ahead, up to the prototype's ``max_distance`` (catalog), through any other entity;
  the first underground of the same name facing the same way decides: an output pairs, anything
  else leaves it alone (v4's rule, C:\\fk-lead\\repo\\fk\\sight\\flow.py d6018d3).
- ``pickup`` / ``drop``: an inserter faces the side it picks from (v4 convention) and reaches one
  tile, a long-handed one two (geometry from fk\\lanes.py). It picks from a belt, an underground, a
  splitter or a machine, and drops onto the same, an underground output too (measured on 2.0.77,
  as lanes.py has it; v4's B-120 said no, and the walker found 96 game drops it missed).
- ``mine``: a mining drill drops just past its front edge (the game's vector_to_place_result:
  centred, half a tile to its left on an even width) onto a carrier or straight into a machine.
  3.0: so does anything else with a drop vector (``Catalog.drop_tile``: a recycler).

Fluids are a second graph over **fluid boxes** (``Graph.fluid``), one node per live box
(``(key, box)``: a pipe has one, a boiler two, a chemical plant what its recipe makes live), with
the connections as the game makes them (``Catalog.fluid_boxes_at``, read from the game):

- ``pipe``: two normal connections facing each other across a tile edge (each one's target is
  the other's tile) join, whatever entity holds them: pipe to pipe, pipe to machine, machine to
  machine (a boiler straight onto a steam engine). A pipe beside a pipe-to-ground's side does not
  join it: that side has no connection.
- ``pipe_ug``: a pipe-to-ground's underground side pairs within its max distance (10, end to end);
  the first pipe-to-ground on the same axis decides (facing back: its partner; facing the same
  way: none); one at right angles and anything else in between is passed (measured in 2.0.77).

Fluid moves along a join from an output (or input-output) connection into an input (or
input-output) one, and through every box with more than one connection (a boiler's water box
passes water on to the next boiler). A fluid net's endpoint is the box of its machine that holds
the net's fluid.

A **carrier** (belt, underground, splitter, inserter, pipe ..., or anything with ``net=``) belongs
to the nets its ``net=`` names (``Node.nets``): one, or in 2.0 a belt's two lanes (``ore|fuel``)
or an inserter's several nets (``ore+fuel``, ``fkcore.fkp``); a **machine** (assembler, furnace,
drill, lab, chest ...) is an endpoint, reached but never passed through (a fluid box of an
endpoint passes its fluid on). On a surface a carrier's net is ``<factory>/<net>``: nets never
leave their factory.

:func:`connect` checks every net (CORE-V1.md §4.5). Its drivers and consumers are the netlist's
endpoints (:func:`endpoints`) or, without a netlist, the machines its carriers pick from and drop
into. A net is **connected** when every driver reaches every consumer through the net's own
carriers. Report lines (``elaborate.Check``), never raised: ``no-route`` (no driver reaches a
consumer; ``unused`` when the net has no driver, consumer or carrier), ``partly-connected`` (some pairs
connect, not all), ``SPILL`` (an edge from one net's carrier into a carrier that does not carry
that net: a belt or side-load feeding another net's belt, an inserter picking from another net's
belt, an underground the game pairs with another net's; with a netlist
also a carrier moving items into a machine that is not a consumer of any of its nets, or out of
one that drives none of them). A net's belt side-loaded onto a belt of two nets that carries it
(``ore`` onto ``ore|fuel``) is no SPILL: that is how two nets come to share a belt. A fluid SPILL
is any join between two nets: a pipe or pipe-to-ground of one fluid net joined to another's, and
with a netlist also to a machine box that is not its endpoint, or two machines' boxes of
different nets joined directly.

A **joint** is where one net hands its items to another on purpose: a bus port (Rory's bus,
``fkcore.fragments.bus``), a belt that is a consumer of one net and a driver of the next. A load's
belt is its net's last carrier and feeds the bus's first splitter; the bus's last splitter feeds a
tap's belt, its net's first. With a netlist, an edge between two nets' carriers is no SPILL where
one of its ends is such a port of both nets (a consumer of the net the items leave, a driver of
the one they enter), and a net's consumer that is another net's carrier counts as reached.

A net of contact items (2.0: heat, ``templates.CONTACT``) has no carriers: it is connected when
each driver's tiles touch each consumer's (a reactor beside its heat exchanger).
Pure, stdlib only, deterministic.
"""
from __future__ import annotations

import json
import math
import re
from collections import deque
from dataclasses import dataclass, field

from . import arms as armsmod
from .catalog import FluidBox
from .elaborate import Check
from .fkp import CARRIER_TYPES, map_nets, net_names, rails_share
# 2.1.30: every use here is a sort key: the remembered tuple, not a fresh list per call (route's
# standing nets sorted ~1.7M keys building graphs on the 20261030-2352 world)
from .fkp import _natural_key as natural_key
from .templates import CONTACT

__all__ = ["Node", "Graph", "Leak", "NetReport", "Report", "graph", "connect", "endpoints",
           "endpoint_box", "one_inserter", "cut_off", "HOLDER_TYPES", "REACH"]

_STEP = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
_BELTS = frozenset({"transport-belt", "underground-belt", "splitter"})
_FLUID = frozenset({"pipe", "pipe-to-ground", "pump"})
_GIVES_FLUID = frozenset({"output", "input-output"})
_TAKES_FLUID = frozenset({"input", "input-output"})
#: Entity types an inserter drops into or picks from: the machine endpoints.
HOLDER_TYPES = frozenset({"assembling-machine", "furnace", "mining-drill", "lab", "container",
                          "logistic-container", "boiler", "reactor", "ammo-turret", "roboport",
                          # Space Age: inserters take from and drop into these too
                          "rocket-silo", "asteroid-collector", "fusion-reactor",
                          "cargo-landing-pad", "space-platform-hub", "cargo-bay",
                          "agricultural-tower"})
#: Tiles from an inserter to its pickup and drop tile; every other inserter reaches 1.
REACH = armsmod.REACH
_FLUID_DRILLS = frozenset({"pumpjack"})
_UG_REACH = 5                      # a tier the catalog gives no max_distance
_SHOW = 4
_VERB = {"belt": "feeds", "curve": "feeds", "sideload": "side-loads onto",
         "ug_sideload": "side-loads onto", "ug": "pairs underground with", "drop": "drops onto",
         "mine": "drops onto"}


@dataclass
class Node:
    key: str
    name: str
    kind: str                      # the prototype's entity type
    net: str | None                # its net= as written; on a surface: <factory>/<net>
    x: float
    y: float
    dir: int                       # 0..15, N = 0; no direction counts as north
    tiles: tuple
    ug: str | None = None          # "in" | "out" for an underground end
    recipe: str | None = None      # a crafting machine's recipe (it decides its fluid boxes)
    prio: tuple | None = None      # a splitter's (input, output) priority: left, right or None
    resource: str | None = None    # a drill's ore (2.1: on uranium ore its fluid box is live)
    arms: dict | None = None       # an inserter's own vectors: {"pick": "dx,dy", "drop": ...}

    @property
    def carrier(self):
        # 3.0: a pump with no net= is a Pump, an end of its two nets (fkp.machine_pump)
        return (self.kind in CARRIER_TYPES and not (self.kind == "pump" and self.net is None)) \
            or self.net is not None

    @property
    def nets(self):
        """The nets it carries: ``ore|fuel`` -> (ore, fuel)."""
        return tuple(net_names(self.net))


@dataclass
class Graph:
    nodes: dict = field(default_factory=dict)       # key -> Node, natural key order
    at: dict = field(default_factory=dict)          # (x, y) -> key
    succ: dict = field(default_factory=dict)        # key -> [(key, via)]
    pred: dict = field(default_factory=dict)        # key -> [(key, via)]
    pairs: dict = field(default_factory=dict)       # underground end -> its partner, both ways
    problems: list = field(default_factory=list)    # entities the graph could not place
    boxes: dict = field(default_factory=dict)       # (key, box) -> catalog.FluidBox, every live box
    fluid: dict = field(default_factory=dict)       # (key, box) -> [((key, box), via)] fluid moves
    joins: list = field(default_factory=list)       # [((key, box), (key, box), via)] each join once
    fluid_pairs: dict = field(default_factory=dict)  # pipe-to-ground -> its partner, both ways

    @property
    def unpaired(self):
        return [k for k, n in self.nodes.items() if n.kind == "underground-belt"
                and k not in self.pairs]

    def fluid_reach(self, starts, net, owners):
        """Every fluid box reached from ``starts`` (``(key, box)``) along the fluid moves, passing
        only through carriers of ``net`` and boxes ``owners`` gives to ``net`` (a boiler's water
        box passes water on); any other box is reached, never passed through."""
        seen = set(starts)
        todo = deque(sorted(seen, key=lambda b: (natural_key(b[0]), b[1])))
        while todo:
            for nid, _via in self.fluid.get(todo.popleft(), ()):
                if nid in seen:
                    continue
                seen.add(nid)
                n = self.nodes[nid[0]]
                if (net in n.nets) if n.carrier else owners.get(nid) == net:
                    todo.append(nid)
        return seen

    def edges(self):
        """Every edge as (from, to, via), in natural key order."""
        for a in self.nodes:
            for b, via in self.succ.get(a, ()):
                yield a, b, via

    def reach(self, starts, net, forward=True, ends=()):
        """Every node reached from ``starts`` along the edges (against them when not
        ``forward``), passing only through carriers of ``net``; a machine is reached, never
        passed through, and so is a node of ``ends`` (a bus port: another net's carrier)."""
        adj = self.succ if forward else self.pred
        seen = set(starts)
        todo = deque(sorted(seen, key=natural_key))
        while todo:
            for k, _via in adj.get(todo.popleft(), ()):
                n = self.nodes[k]
                if k in seen:
                    continue
                if k in ends:
                    seen.add(k)
                    continue
                if n.carrier and net not in n.nets:
                    continue
                seen.add(k)
                if n.carrier:
                    todo.append(k)
        return seen

    def inserter_tiles(self, key):
        """(pickup tile, drop tile) of an inserter, or None when it faces no cardinal."""
        return _hand(self.nodes[key])


@dataclass
class Leak:
    net: str | None                # the net whose items escape
    src: str
    dst: str
    via: str
    text: str


@dataclass
class NetReport:
    name: str
    drivers: list                  # machine keys
    consumers: list
    missing: list                  # endpoints with no entity in the layout
    pairs: int                     # driver -> consumer pairs
    reached: int                   # of them connected
    unreached: list                # [(driver, consumer)]
    segments: int                  # weakly connected pieces of the net's carriers
    carriers: int
    unpaired: list                 # the net's undergrounds the game leaves alone
    fluid: bool = False            # a fluid net: judged over the fluid boxes
    leaks: list = field(default_factory=list)
    ug_sideloads: list = field(default_factory=list)    # [(belt, underground)]: lane-dependent

    @property
    def connected(self):
        return self.pairs > 0 and self.reached == self.pairs

    @property
    def status(self):
        return "connected" if self.connected else "open" if self.reached else "unrouted"


@dataclass
class Report:
    graph: Graph
    nets: dict                     # name -> NetReport
    leaks: list                    # [Leak]
    checks: list                   # [Check]: per net UNROUTED/OPEN and its LEAKs

    def lines(self):
        return [str(c) for c in self.checks]

    def summary(self):
        count = {s: sum(r.status == s for r in self.nets.values())
                 for s in ("connected", "open", "unrouted")}
        edges = sum(len(v) for v in self.graph.succ.values())
        text = (f"{len(self.nets)} nets: {count['connected']} connected, {count['open']} open, "
                f"{count['unrouted']} unrouted; {len(self.leaks)} SPILL; "
                f"{len(self.graph.nodes)} entities, {edges} edges, "
                f"{len(self.graph.pairs) // 2} underground pairs")
        if self.graph.boxes:
            text += (f"; {len(self.graph.boxes)} fluid boxes, {len(self.graph.joins)} fluid joins, "
                     f"{len(self.graph.fluid_pairs) // 2} pipe-to-ground pairs")
        return text


# -- the graph ------------------------------------------------------------------------------------
def graph(ents, cat, ports=None) -> Graph:
    """The directed carrier graph of a Layout, a Surface or a list of entities. ``ports``
    ({port key: expand.PortEdge}, 2.0.48): sealed modules' ports, each a node on the edge its
    belt crosses (``_port_joints``)."""
    g = Graph()
    rows = ents.entities.values() if hasattr(ents, "entities") else ents
    for e in sorted(rows, key=lambda e: natural_key(e.key)):
        _add_node(g, e, cat)
    _belt_edges(g)
    _pair_undergrounds(g, cat)
    _fluid_joins(g, cat)
    for key, n in g.nodes.items():
        if n.kind == "inserter":
            hand = _hand(n)
            if hand is None:
                continue
            src, dst = g.at.get(hand[0]), g.at.get(hand[1])
            if src is not None and _gives(g.nodes[src]):
                _edge(g, src, key, "pickup")
            if dst is not None and _takes(g.nodes[dst]):
                _edge(g, key, dst, "drop")
        elif n.name not in _FLUID_DRILLS and n.dir in _STEP and hasattr(cat, "drop_tile") \
                and cat.drop_tile(n.name, n.x, n.y, n.dir) is not None:
            dst = g.at.get(cat.drop_tile(n.name, n.x, n.y, n.dir))
            if dst is not None and dst != key and _takes(g.nodes[dst]):
                _edge(g, key, dst, "mine")
    if ports:
        _port_joints(g, ports)
    order = {k: i for i, k in enumerate(g.nodes)}
    for adj in (g.succ, g.pred):
        for k in adj:
            adj[k].sort(key=lambda kv: order[kv[0]])
    return g


def _port_joints(g, ports):
    """Thread every sealed module's port into the edge its belt crosses (2.0.48, Rory
    2026-10-26: "our boundaries should be along an edge so that a port occupies zero area"). A port
    is no entity: the belt on one side of the frame's edge moves its items onto the belt on the
    other. That one edge becomes belt -> port -> belt, with the port a node of its own, keyed as
    the netlist keys it, so it is the consumer of the net its items leave and the driver of the
    one they enter: a joint, not a SPILL. Two ports on one edge (stacked cells, one's out port
    meeting the next one's in port) chain, the out port first."""
    by_edge = {}
    for key in sorted(ports, key=natural_key):
        pe = ports[key]
        by_edge.setdefault((tuple(pe.src), tuple(pe.dst)), []).append(key)
    for (src, dst), keys in by_edge.items():
        keys.sort(key=lambda k: (tuple(ports[k].src) != tuple(ports[k].inside), natural_key(k)))
        if any(getattr(ports[k], "fluid", None) for k in keys):
            _fluid_port_joints(g, ports, src, dst, keys)
            continue
        a, b = g.at.get(src), g.at.get(dst)
        if a is not None and b is not None:
            g.succ[a] = [kv for kv in g.succ.get(a, []) if kv[0] != b]
            g.pred[b] = [kv for kv in g.pred.get(b, []) if kv[0] != a]
        for key in keys:
            g.nodes[key] = Node(key, "port", "port", None, dst[0] + 0.5, dst[1] + 0.5, 0, ())
        chain = [a] + keys + [b]
        for u, v in zip(chain, chain[1:]):
            if u is not None and v is not None:
                _edge(g, u, v, "belt")


#: a fluid port's two boxes (2.1): box 1 is its outside net's end, box 2 its inside net's
PORT_OUTSIDE, PORT_INSIDE = 1, 2


def port_box(pe, which):
    """The FluidBox of fluid port ``pe``'s ``which`` side (``PORT_OUTSIDE`` / ``PORT_INSIDE``): it
    gives what crosses the edge to the side the fluid goes, and takes it from the side it comes
    from, so it drives the net on the side the fluid enters and ends the one it leaves."""
    inbound = tuple(pe.src) == tuple(pe.outside)             # an in port: the fluid comes in
    gives = (which == PORT_INSIDE) == inbound
    return FluidBox(which, pe.fluid, "output" if gives else "input", ())


def _fluid_port_joints(g, ports, src, dst, keys):
    """A fluid port's edge (2.1, fluid ports): the pipe (or machine box) on each side of the
    frame's edge joins the other there; that join becomes box -> port -> box, the port a node of
    its own with two boxes, one per net it ends (``port_box``), each joined only to its own side.
    So the port is the outside net's consumer and the inside net's driver (an in port), never a
    join between two nets. Two ports on one edge (stacked cells) chain, the out port first: the
    net between them joins their two outside boxes and lays nothing."""
    def facing(tile, other):
        out = []
        for nid, b in g.boxes.items():
            if nid[0] in keys:
                continue
            for c in b.conns:
                if c.type == "normal" and c.tile == tile and c.target == other:
                    out.append((nid, c.flow))
        return out
    a_side, b_side = facing(src, dst), facing(dst, src)
    a_ids, b_ids = {nid for nid, _f in a_side}, {nid for nid, _f in b_side}
    gone = [(x, y, v) for x, y, v in g.joins if v == "pipe" and
            ((x in a_ids and y in b_ids) or (x in b_ids and y in a_ids))]
    for x, y, v in gone:
        g.joins.remove((x, y, v))
        for u, w in ((x, y), (y, x)):
            if u in g.fluid:
                g.fluid[u] = [kv for kv in g.fluid[u] if kv[0] != w]
    chain = []
    for key in keys:
        pe = ports[key]
        g.nodes[key] = Node(key, "port", "port", None, dst[0] + 0.5, dst[1] + 0.5, 0, ())
        first = PORT_INSIDE if tuple(pe.src) == tuple(pe.inside) else PORT_OUTSIDE
        last = PORT_OUTSIDE if first == PORT_INSIDE else PORT_INSIDE
        for which in (PORT_OUTSIDE, PORT_INSIDE):
            g.boxes[(key, which)] = port_box(pe, which)
        chain.append(((key, first), (key, last)))
    done = {(min(x, y, key=_box_key), max(x, y, key=_box_key), v) for x, y, v in g.joins}
    for nid, flow in a_side:
        head = chain[0][0]
        _fluid_join(g, (nid, flow), (head, g.boxes[head].production), "pipe", done)
    for (_s, end), (start, _e) in zip(chain, chain[1:]):
        _fluid_join(g, (end, g.boxes[end].production), (start, g.boxes[start].production),
                    "pipe", done)
    for nid, flow in b_side:
        tail = chain[-1][1]
        _fluid_join(g, (tail, g.boxes[tail].production), (nid, flow), "pipe", done)


def _box_key(nid):
    return (natural_key(nid[0]), nid[1])


def _add_node(g, e, cat):
    try:
        kind = cat.kind(e.name)
    except KeyError:
        g.problems.append(f"{e.key}: unknown prototype {e.name}")
        return
    d = int(e.dir or 0) % 16
    try:
        tiles = tuple(sorted(getattr(e, "tiles", None)
                             or cat.tiles(e.name, float(e.x), float(e.y), d)))
    except ValueError as exc:
        g.problems.append(f"{e.key}: {exc}")
        return
    net, plot = e.attrs.get("net"), getattr(e, "plot", None)
    if net is not None and plot:
        net = map_nets(net, lambda n: f"{plot}/{n}")
    ug = None
    if kind == "underground-belt":
        ug = e.attrs.get("ug")
        if ug not in ("in", "out"):
            g.problems.append(f"{e.key}: an underground with no ug=in|out")
            ug = None
    g.nodes[e.key] = Node(e.key, e.name, kind, net, float(e.x), float(e.y), d, tiles, ug,
                          e.attrs.get("recipe"), _priority(e) if kind == "splitter" else None,
                          e.attrs.get("resource") if kind == "mining-drill" else None,
                          {k: e.attrs[k] for k in ("pick", "drop") if k in e.attrs}
                          if kind == "inserter" else None)
    for t in tiles:
        if t in g.at and g.at[t] in g.nodes and \
                rails_share(e.name, g.nodes[g.at[t]].name):
            continue
        if t in g.at:
            g.problems.append(f"{e.key}: tile {t[0]},{t[1]} is already {g.at[t]}")
        else:
            g.at[t] = e.key


def _priority(e):
    """A splitter's (input, output) priority from its game settings (``config``): "left",
    "right" or None each; None for a splitter without any."""
    try:
        fields = json.loads(e.attrs.get("config") or "{}")
    except ValueError:
        return None
    if not isinstance(fields, dict):
        return None
    got = tuple(fields.get(k) if fields.get(k) in ("left", "right") else None
                for k in ("input_priority", "output_priority"))
    return got if any(got) else None


def _edge(g, a, b, via):
    if a == b or any(k == b for k, _v in g.succ.get(a, ())):
        return
    g.succ.setdefault(a, []).append((b, via))
    g.pred.setdefault(b, []).append((a, via))


def _entry(m, d):
    """How items moving in direction ``d`` enter ``m``: "straight", "side" or None."""
    if m.kind not in _BELTS or m.dir not in _STEP or (m.kind == "underground-belt" and not m.ug):
        return None
    if m.dir == d:                                   # from behind; an output's back is its hood
        return None if m.ug == "out" else "straight"
    if m.dir == (d + 8) % 16 or m.kind == "splitter":    # head-on; a splitter has no side entry
        return None
    return "side"


def _belt_edges(g):
    raw = []
    for key, n in g.nodes.items():
        step = _STEP.get(n.dir)
        if n.kind not in _BELTS or step is None or (n.kind == "underground-belt" and n.ug != "out"):
            continue
        for tx, ty in n.tiles:
            dst = g.at.get((tx + step[0], ty + step[1]))
            if dst is not None and dst != key:
                rel = _entry(g.nodes[dst], n.dir)
                if rel:
                    raw.append((key, dst, rel))
    behind = {b for _a, b, rel in raw if rel == "straight"}
    sides = {}
    for _a, b, rel in raw:
        if rel == "side":
            sides[b] = sides.get(b, 0) + 1
    for a, b, rel in raw:
        if rel == "straight":
            via = "belt"
        elif g.nodes[b].kind == "underground-belt":
            via = "ug_sideload"
        elif b not in behind and sides[b] == 1:
            via = "curve"
        else:
            via = "sideload"
        _edge(g, a, b, via)


def _pair_undergrounds(g, cat):
    for key, n in g.nodes.items():
        step = _STEP.get(n.dir)
        if n.kind != "underground-belt" or n.ug != "in" or step is None:
            continue
        reach = int(cat.entity(n.name).get("max_distance") or _UG_REACH)
        tx, ty = n.tiles[0]
        for k in range(1, reach + 1):
            other = g.at.get((tx + k * step[0], ty + k * step[1]))
            m = g.nodes.get(other)
            if m is None or m.name != n.name or m.dir != n.dir:
                continue                             # passed through
            if m.ug == "out":
                g.pairs[key], g.pairs[other] = other, key
                _edge(g, key, other, "ug")
            break


def _fluid_joins(g, cat):
    """Every live fluid box, and the joins between them as the game makes them."""
    if not hasattr(cat, "fluid_boxes_at"):
        return
    normal, under = {}, {}
    for key, n in g.nodes.items():
        if not cat.has_fluid_boxes(n.name):
            continue
        for b in cat.fluid_boxes_at(n.name, n.x, n.y, n.dir, n.recipe, resource=n.resource):
            nid = (key, b.box)
            g.boxes[nid] = b
            for c in b.conns:
                table = normal if c.type == "normal" else under
                table.setdefault((c.tile, c.dir), []).append((nid, c))
    done = set()
    for (_tile, d), rows in sorted(normal.items()):
        for nid, c in rows:
            for mid, mc in normal.get((c.target, (d + 8) % 16), ()):
                if mid[0] != nid[0] and set(c.categories) & set(mc.categories):
                    _fluid_join(g, (nid, c.flow), (mid, mc.flow), "pipe", done)
    ends = {}
    for (tile, d), rows in under.items():
        for nid, c in rows:
            ends.setdefault(tile, []).append((d, nid, c))
    for (tile, d), rows in sorted(under.items()):
        step = _STEP.get(d)
        for nid, c in rows:
            for k in range(1, int(c.reach or 0) + 1) if step else ():
                t = (tile[0] + k * step[0], tile[1] + k * step[1])
                hits = [h for h in ends.get(t, ()) if h[0] % 8 == d % 8]
                if not hits:
                    continue                         # passed under it
                md, mid, mc = hits[0]
                if md == (d + 8) % 16 and set(c.categories) & set(mc.categories):
                    g.fluid_pairs[nid[0]], g.fluid_pairs[mid[0]] = mid[0], nid[0]
                    _fluid_join(g, (nid, c.flow), (mid, mc.flow), "pipe_ug", done)
                break
    order = {k: i for i, k in enumerate(g.nodes)}
    for k in g.fluid:
        g.fluid[k].sort(key=lambda kv: (order[kv[0][0]], kv[0][1]))


def _fluid_join(g, a, b, via, done):
    (na, fa), (nb, fb) = sorted((a, b), key=lambda x: (natural_key(x[0][0]), x[0][1]))
    if (na, nb, via) in done:
        return
    done.add((na, nb, via))
    g.joins.append((na, nb, via))
    if fa in _GIVES_FLUID and fb in _TAKES_FLUID:
        g.fluid.setdefault(na, []).append((nb, via))
    if fb in _GIVES_FLUID and fa in _TAKES_FLUID:
        g.fluid.setdefault(nb, []).append((na, via))


def _hand(n):
    """(pickup tile, drop tile) of an inserter node: its own vectors (``pick=``/``drop=``, 3.0:
    Bob's adjustable inserters, ``fkcore.arms``) where it has them, else its direction and reach."""
    return armsmod.tiles(n.name, n.dir, n.arms, n.tiles[0])


def _gives(m):
    """An inserter can pick from ``m``."""
    if m.kind in _BELTS:
        return m.kind != "underground-belt" or m.ug is not None
    return m.kind in HOLDER_TYPES and m.kind != "mining-drill"


def _takes(m):
    """An inserter or a drill can drop into ``m``."""
    return m.kind in _BELTS or m.kind in HOLDER_TYPES


# -- the checks -----------------------------------------------------------------------------------
def endpoints(flat, surface=None, factory=None):
    """{net: (drivers, consumers)} of (machine key, item) from a flat netlist. With ``surface``
    and ``factory``, keys and net names as a composed surface has them (``nauvis:main/red/m0``,
    ``main/gear``)."""
    pre = f"{surface}:{factory}/" if surface and factory else ""
    netpre = f"{factory}/" if surface and factory else ""
    table = {netpre + name: (Dealt if getattr(n, "width", 1) > 2 or getattr(n, "any", False)
                             else tuple)(
                 ([(pre + k, i) for k, i in n.drivers], [(pre + k, i) for k, i in n.consumers]))
             for name, n in flat.nets.items()}
    # A bus's mouth has no entity of its own: the bus's own belt under the mouth is its carrier.
    # Swap the machine key for the carrier key, so the net reaches it and a crossing there is a
    # joint (a consumer of the net the items leave, a driver of the one they enter), not a LEAK.
    # A sealed module's port is a node of the graph itself (2.0.48, ``_port_joints``).
    alias = {}
    for path, inst in getattr(flat, "insts", {}).items():
        if inst.kind != "Bus":
            continue
        for key in inst.machines:
            m = flat.machines.get(key)
            if m is None or not m.extra.get("mouth"):
                continue
            name = m.extra.get("at") or ""
            if name.startswith("in") and name[2:].isdigit():
                alias[key] = f"{path}/b{int(name[2:])}_0"
    if alias:
        for name, ends in table.items():
            drivers, consumers = ends
            table[name] = type(ends)(([(alias.get(k, k), i) for k, i in drivers],
                                      [(alias.get(k, k), i) for k, i in consumers]))
    return table


class Dealt(tuple):
    """(drivers, consumers) of a wide net (2.1, ``width`` above 2: a ribbon of belts, its drivers
    and consumers dealt out to them, ``fkcore.ribbons``) or an ``any`` net (3.0: a pass-through,
    a split output): it is connected when every driver reaches a consumer and every consumer is
    reached, not every driver every consumer."""
    dealt = True


def _keys(ends):
    keys = {e if isinstance(e, str) else e[0] for e in ends}
    return sorted(keys, key=natural_key)


def _inferred(g, carried):
    out = {}
    for net in sorted(carried, key=natural_key):
        drivers, consumers = set(), set()
        for k in carried[net]:
            drivers |= {m for m, _v in g.pred.get(k, ()) if not g.nodes[m].carrier}
            consumers |= {m for m, _v in g.succ.get(k, ()) if not g.nodes[m].carrier}
        out[net] = (sorted(drivers, key=natural_key), sorted(consumers, key=natural_key))
    return out


def _segments(g, keys):
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for a in keys:
        for b, _via in g.succ.get(a, ()):
            if b in parent:
                parent[find(a)] = find(b)
    for a, b, _via in g.joins:
        if a[0] in parent and b[0] in parent:
            parent[find(a[0])] = find(b[0])
    return len({find(k) for k in keys})


# -- fluid nets -----------------------------------------------------------------------------------
def _with_items(ends):
    return [(e, None) if isinstance(e, str) else (e[0], e[1]) for e in ends]


def _fluid_nets(g, cat, table, items, carried):
    """The nets judged over fluid boxes: every item a fluid, or (no items) pipes as carriers."""
    out = set()
    for name in table:
        named = [i for _k, i in items.get(name, ((), ()))[0] + items.get(name, ((), ()))[1]
                 if i is not None]
        if named:
            if all(cat.is_fluid(i) for i in named):
                out.add(name)
        elif any(g.nodes[k].kind in _FLUID for k in carried.get(name, ())):
            out.add(name)
    return out


def _boxes_by_key(g):
    out = {}
    for nid in g.boxes:
        out.setdefault(nid[0], []).append(nid)
    return out


def endpoint_box(boxes, item, side):
    """The FluidBox of a machine (``boxes``: ``Catalog.fluid_boxes_at``) that is its endpoint for
    fluid ``item`` on ``side`` (out: it drives the net, in: it consumes): the box holding that
    fluid, else the first box with no fixed fluid that can give (or take) it. None if none."""
    for b in boxes:
        if item is not None and b.fluid == item:
            return b
    fits = {"out": _GIVES_FLUID | {"none"}, "in": _TAKES_FLUID | {"none"}}[side]
    return next((b for b in boxes if b.fluid is None and b.production in fits), None)


def _endpoint_boxes(g, mine, key, item, side):
    """The (key, box) of machine ``key`` that holds a fluid net's ``item`` (out: a driver). A fluid
    port (2.1) has a box per net it ends: the one that gives for a driver, takes for a consumer."""
    nids = mine.get(key, ())
    n = g.nodes.get(key)
    if n is not None and n.kind == "port":
        want = "output" if side == "out" else "input"
        return [nid for nid in nids if g.boxes[nid].production == want][:1]
    b = endpoint_box([g.boxes[nid] for nid in nids], item, side)
    return [] if b is None else [(key, b.box)]


def _box_owners(g, fluid, items, inferred):
    """(key, box) -> the fluid net whose endpoint it is. From the netlist's (machine, item); with
    no netlist (``inferred``), a machine box joined to one fluid net's carriers is that net's."""
    owners, mine = {}, _boxes_by_key(g)
    for name in sorted(fluid, key=natural_key):
        drivers, consumers = items.get(name, ((), ()))
        for side, ends in (("out", drivers), ("in", consumers)):
            for key, item in ends:
                for nid in _endpoint_boxes(g, mine, key, item, side):
                    owners.setdefault(nid, name)
    if inferred:
        near = {}
        for a, b, _via in g.joins:
            for x, y in ((a, b), (b, a)):
                nx, ny = g.nodes[x[0]], g.nodes[y[0]]
                if not nx.carrier and ny.carrier and ny.net in fluid and x not in owners:
                    near.setdefault(x, set()).add(ny.net)
        owners.update({nid: next(iter(n)) for nid, n in near.items() if len(n) == 1})
    return owners


def _inferred_fluid(g, fluid, owners):
    """{net: (drivers, consumers)} of the fluid nets from their boxes: an output box drives."""
    out = {}
    for nid, net in owners.items():
        prod = g.boxes[nid].production
        if net in fluid and prod in ("output", "input"):
            out.setdefault(net, (set(), set()))[0 if prod == "output" else 1].add(nid[0])
    return {n: (sorted(d, key=natural_key), sorted(c, key=natural_key))
            for n, (d, c) in out.items()}


def _box_word(g, nid):
    fluid = g.boxes[nid].fluid
    return f"its {fluid} box" if fluid else f"fluid box {nid[1]}"


def _fluid_leaks(g, owners, table):
    """The Leak of every join between two nets (carriers always; boxes with a netlist)."""
    out = []
    for a, b, via in g.joins:
        na, nb = g.nodes[a[0]], g.nodes[b[0]]
        oa = na.net if na.carrier else owners.get(a)
        ob = nb.net if nb.carrier else owners.get(b)
        if oa == ob:
            continue
        verb = "pairs underground with" if via == "pipe_ug" else "joins"
        if na.carrier and nb.carrier:
            out.append(Leak(oa, a[0], b[0], via, f"{a[0]} {verb} {b[0]} ({_net_word(nb)})"))
        elif table is None:
            continue
        elif na.carrier or nb.carrier:
            (c, oc), m = ((a, oa), b) if na.carrier else ((b, ob), a)
            inst = c[0].rsplit("/", 1)[0]
            if oc in table and m[0].rsplit("/", 1)[0] == inst and any(
                    k.rsplit("/", 1)[0] == inst for k in list(table[oc][0]) + list(table[oc][1])):
                continue      # 3.0: a Literal port's own carriers into its own machine: its inside
            if oc in table:
                out.append(Leak(oc, c[0], m[0], via, f"{c[0]} joins {m[0]} ({_box_word(g, m)}), "
                                                     f"which is not an endpoint of {oc}"))
        else:
            net, other, nid = (oa, b, b) if oa in table else (ob, a, a)
            if net in table:
                there = owners.get(other)
                out.append(Leak(net, a[0], b[0], via, f"{a[0]} joins {b[0]} directly "
                                f"({_box_word(g, nid)}, " + (f"net {there})" if there else
                                                             "no net)")))
    out.sort(key=lambda lk: (natural_key(lk.src), natural_key(lk.dst)))
    return out


def _net_word(n):
    return f"net {n.net}" if n.net else "no net"


def _joint(a, b, na, nb, table):
    """An edge from one net's carrier into another's that is a bus port's: one of its ends is a
    consumer of the net the items leave and a driver of the one they enter."""
    if table is None:
        return False
    return any(k in table[x][1] and k in table[y][0] for x in na.nets if x in table
               for y in nb.nets if y in table for k in (a, b))


_LIT_NET = re.compile(r"^(.+)/(hand|belt|fluid)\d+$")


def _one_literal(na, nb):
    """2.1.31: both carriers are one Literal's own (``fkcore.literal`` nets a captured build by
    connectivity: ``<inst>/hand<k>``, ``<inst>/belt<k>``, ``<inst>/fluid<k>``), so the edge is the
    build working as it was built, not a spill: a captured hub's 600 inserters all read SPILL."""
    insts = set()
    for n in (na, nb):
        for x in n.nets or [None]:
            m = _LIT_NET.match(x or "")
            if m is None:
                return False
            insts.add(m.group(1))
    return len(insts) == 1


def _leak(g, a, b, via, table, cat=None):
    """The Leak an edge makes, or None. Between two carriers, the items of a net the second one
    does not carry escape (``ore|fuel`` into a belt of ``ore``: the fuel)."""
    na, nb = g.nodes[a], g.nodes[b]
    if na.carrier and nb.carrier and _one_literal(na, nb):
        return None
    if na.carrier and nb.carrier:
        if na.nets:
            out = [x for x in na.nets if x not in nb.nets]
        else:
            out = [None] if nb.nets else []
        if not out or _joint(a, b, na, nb, table) or _lit_inside(g, a, b, table):
            return None
        if table is not None and (all(x in table and b in table[x][1] for x in out)
                                  or (nb.nets and all(y in table and a in table[y][0]
                                                      for y in nb.nets))):
            return None     # 2.1.19: into the belt its net ends at, or out of the one it starts
                            # at (a Literal's named belt)
        if via == "pickup":
            return Leak(out[0], a, b, via, f"{b} ({_net_word(nb)}) picks from {a}")
        return Leak(out[0], a, b, via, f"{a} {_VERB[via]} {b} ({_net_word(nb)})")
    if table is None:
        return None
    mine = [x for x in na.nets if x in table] if na.carrier else []
    if mine and not any(b in table[x][1] for x in mine) and not _relay(g, b, mine, table, cat):
        return Leak(mine[0], a, b, via, f"{a} drops into {b}, which is not a consumer of "
                                        f"{_either(mine)}")
    mine = [x for x in nb.nets if x in table] if nb.carrier else []
    if mine and not any(a in table[x][0] for x in mine) and not _relay(g, a, mine, table, cat):
        if via == "mine":
            return Leak(mine[0], a, b, via, f"{a} drops onto {b}, but does not drive "
                                            f"{_either(mine)}")
        return Leak(mine[0], a, b, via, f"{b} picks from {a}, which does not drive "
                                        f"{_either(mine)}")
    return None


def _lit_inside(g, a, b, table):
    """3.0: an edge inside one Literal between its port's belts and its own inserters: the port's
    net ends at a carrier of the same instance (a Literal port's key is its carrier's), so what
    its inserters take from or put on that belt is the Literal's inside."""
    if table is None or "/" not in a or "/" not in b:
        return False
    inst = a.rsplit("/", 1)[0]
    if b.rsplit("/", 1)[0] != inst:
        return False
    for k in (a, b):
        for x in g.nodes[k].nets:
            if x in table and any(isinstance(e, str) and e in g.nodes and g.nodes[e].carrier
                                  and e.rsplit("/", 1)[0] == inst
                                  for e in list(table[x][0]) + list(table[x][1])):
                return True
    return False


def _relay(g, key, nets, table, cat):
    """A template's own intermediate maker (2.0.47: a CircuitStack's cable assemblers, between its
    copper feed and its circuit assemblers): no endpoint of the netlist, standing in the instance
    of a consumer of one of ``nets``, and making an ingredient of that consumer's recipe. It hands
    the net's items on inside the template, so items into or out of it don't leave the net."""
    if cat is None or not isinstance(key, str) or "/" not in key or key not in g.nodes:
        return False
    if any(key in table[x][0] or key in table[x][1] for x in table):
        return False                         # a netlist machine: judged as one
    mine = g.nodes[key].recipe
    if not mine or not cat.has_recipe(mine):
        return False
    makes = set(cat.recipe_rates(mine, 1)[1])
    inst = key.rsplit("/", 1)[0]
    for x in nets:
        for c in table[x][1]:
            if not isinstance(c, str) or c.rsplit("/", 1)[0] != inst or c not in g.nodes:
                continue
            theirs = g.nodes[c].recipe
            if theirs and cat.has_recipe(theirs) and makes & set(cat.recipe_rates(theirs, 1)[0]):
                return True
    return False


def _either(nets):
    return nets[0] if len(nets) == 1 else " or ".join(nets)


def _status_line(r):
    if r.pairs == 0:
        msg = (f"no driver->consumer pair to connect ({len(r.drivers)} drivers, "
               f"{len(r.consumers)} consumers)")
    else:
        msg = f"{r.reached} of {r.pairs} driver->consumer pairs connected"
    if r.reached and r.unreached:
        shown = ", ".join(f"{d} -> {c}" for d, c in r.unreached[:_SHOW])
        more = len(r.unreached) - _SHOW
        msg += f"; unreached {shown}" + (f" ... {more} more" if more > 0 else "")
    msg += (f"; {r.segments} segment{'s' if r.segments != 1 else ''}, "
            f"{r.carriers} carrier{'s' if r.carriers != 1 else ''}")
    if r.unpaired:
        msg += f"; unpaired {', '.join(r.unpaired)}"
    if r.missing:
        msg += f"; not in the layout: {', '.join(r.missing)}"
    if not r.drivers and not r.consumers and not r.carriers:
        # 2.0.12: a declared net that nothing uses has nothing to route; as UNROUTED it would
        # hold every paste now that the build script stops on UNROUTED by default
        return Check("unused", f"net {r.name}", msg)
    return Check("no-route" if r.reached == 0 else "partly-connected", f"net {r.name}", msg)


def cut_off(rep, makers):
    """The makers among ``makers`` (machine keys) whose product reaches none of the consumers of a
    net they drive, from a :func:`connect` report: [(maker, net, consumers)], in net order. One
    consumer reached is enough (the net's partly-connected line names the pairs it misses). A net with no
    consumers is no-route already; a maker not in the layout is the net's "not in the layout".

    2.0.12 (the solo run, 2026-10-23 23:23): Mall4 went out with its products belt unrouted, and
    its makers had no output."""
    out = []
    for name, r in rep.nets.items():
        if not r.consumers:
            continue
        missed = {}
        for d, _c in r.unreached:
            missed[d] = missed.get(d, 0) + 1
        out += [(d, name, len(r.consumers)) for d in r.drivers
                if d in makers and d not in r.missing and missed.get(d, 0) >= len(r.consumers)]
    return out


def one_inserter(ents, cat, nets):
    """The nets of ``nets`` ({net: (drivers, consumers)}, :func:`endpoints`) laid as one inserter
    from source to sink: every carrier of the net an inserter that picks from one of its drivers
    and drops straight into one of its consumers, and every consumer reached that way. -> {net}.

    2.0 (Rory, 2026-10-21): the only route on which a mixed net can't clog. An inserter takes only
    what its sink accepts, but only when the multi-item source and the sink both touch it; a belt
    in between, or a second inserter (a chain, a hub chest), loses that."""
    g = ents if isinstance(ents, Graph) else graph(ents, cat)
    carried = {}
    for key, n in g.nodes.items():
        if n.carrier:
            for net in n.nets:
                carried.setdefault(net, []).append(key)
    out = set()
    for net, (drivers, consumers) in nets.items():
        drivers, consumers = set(_keys(drivers)), set(_keys(consumers))
        keys = carried.get(net)
        if not keys or not drivers or not consumers:
            continue
        reached = set()
        for key in keys:
            src = [m for m, via in g.pred.get(key, ()) if via == "pickup"]
            dst = [m for m, via in g.succ.get(key, ()) if via == "drop"]
            if g.nodes[key].kind != "inserter" or len(src) != 1 or len(dst) != 1 \
                    or src[0] not in drivers or dst[0] not in consumers:
                break
            reached.add(dst[0])
        else:
            if reached == consumers:
                out.add(net)
    return out


#: 3.0: a pipe segment (pipes, pipes-to-ground and tanks joined, a pump or a machine ends one)
#: that spreads outside a 320x320-tile area (10x10 chunks) carries nothing until a pump breaks it
#: up (Factorio 2.0, wiki "Fluid system": inside a segment flow has no length limit)
PIPE_EXTENT = 320
_RUN_KINDS = frozenset({"pipe", "pipe-to-ground", "storage-tank"})


def pipe_runs(g):
    """[(keys, (x0, y0, x1, y1), nets)] of the layout's pipe segments: the pipes, pipes-to-ground
    and tanks of graph ``g`` joined to each other; a pump or a machine between two is a boundary.
    The box is in tiles, inclusive."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    runs = [k for k, n in g.nodes.items() if n.kind in _RUN_KINDS]
    for k in runs:
        find(k)
    for (a, _ba), (b, _bb), _via in g.joins:
        if g.nodes[a].kind in _RUN_KINDS and g.nodes[b].kind in _RUN_KINDS:
            parent[find(a)] = find(b)
    for a, b in g.fluid_pairs.items():
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    groups = {}
    for k in runs:
        groups.setdefault(find(k), []).append(k)
    out = []
    for keys in groups.values():
        tiles = [t for k in keys for t in g.nodes[k].tiles]
        xs, ys = [t[0] for t in tiles], [t[1] for t in tiles]
        nets = sorted({n for k in keys for n in g.nodes[k].nets})
        out.append((sorted(keys, key=natural_key), (min(xs), min(ys), max(xs), max(ys)), nets))
    return out


def long_pipes(g):
    """``pipe-too-long`` checks: each pipe segment wider or taller than ``PIPE_EXTENT`` tiles."""
    out = []
    for keys, (x0, y0, x1, y1), nets in pipe_runs(g):
        w, h = x1 - x0 + 1, y1 - y0 + 1
        if max(w, h) <= PIPE_EXTENT:
            continue
        where = "net " + ", ".join(nets) if nets else f"pipes from {keys[0]}"
        need = -(-max(w, h) // PIPE_EXTENT) - 1
        out.append(Check("pipe-too-long", where,
                         f"one pipe segment of {len(keys)} pipes spans {w}x{h} tiles "
                         f"({x0},{y0} to {x1},{y1}): past {PIPE_EXTENT}x{PIPE_EXTENT} the game "
                         f"moves no fluid through it at all. At least {need} pump"
                         f"{'s' if need != 1 else ''} in it: routing.fk's InlinePump in the net's "
                         "chain (net N: a -> pp -> b), or a Pump between two nets"))
    return out


def connect(ents, cat, nets=None, ports=None) -> Report:
    """Per net: connected yes/no and its LEAKs, over :func:`graph` of ``ents`` (or a Graph).

    ``nets``: {net: (drivers, consumers)} of machine keys or (key, item) pairs (see
    :func:`endpoints`), or a flat netlist; None infers them from the carriers. ``ports``: the
    sealed modules' port edges (2.0.48, ``expand.port_edges``), for a graph built here."""
    g = ents if isinstance(ents, Graph) else graph(ents, cat, ports)
    if nets is not None and hasattr(nets, "nets"):
        nets = endpoints(nets)
    carried = {}
    for key, n in g.nodes.items():
        if n.carrier:
            for net in n.nets:
                carried.setdefault(net, []).append(key)
    items = {}
    if nets is None:
        table = _inferred(g, carried)
    else:
        table = {}
        for name, ends in nets.items():
            d, c = (ends.drivers, ends.consumers) if hasattr(ends, "drivers") else ends
            table[name] = (Dealt if getattr(ends, "dealt", False) else tuple)((_keys(d), _keys(c)))
            items[name] = (_with_items(d), _with_items(c))
    fluid = _fluid_nets(g, cat, table, items, carried)
    owners = _box_owners(g, fluid, items, nets is None)
    if nets is None:
        table.update({n: ends for n, ends in _inferred_fluid(g, fluid, owners).items()})
    leaks = [lk for a, b, via in g.edges()
             if (lk := _leak(g, a, b, via, None if nets is None else table, cat)) is not None]
    leaks += _fluid_leaks(g, owners, None if nets is None else table)
    ug_sides = [(a, b) for a, b, via in g.edges() if via == "ug_sideload"]
    mine_boxes = _boxes_by_key(g)
    reports, checks = {}, []
    for name in sorted(table, key=natural_key):
        drivers, consumers = table[name]
        missing = [k for k in _keys(drivers + consumers) if k not in g.nodes]
        reached, unreached, reach_of = 0, [], {}
        contact = bool(items.get(name)) and all(
            i in CONTACT for _k, i in items[name][0] + items[name][1] if i is not None)
        for d in drivers:
            if name in fluid:
                starts = [nid for nid in mine_boxes.get(d, ()) if owners.get(nid) == name]
                got = {nid[0] for nid in g.fluid_reach(starts, name, owners)
                       if owners.get(nid) == name}
            elif contact:
                got = {c for c in consumers if d in g.nodes and c in g.nodes
                       and _touch(g.nodes[d], g.nodes[c])}
            else:
                got = g.reach({d}, name, ends=set(consumers)) if d in g.nodes else set()
            for c in consumers:
                if c in got:
                    reached += 1
                else:
                    unreached.append((d, c))
            if getattr(table[name], "dealt", False):
                reach_of[d] = got
        if getattr(table[name], "dealt", False) and drivers and consumers:
            # a wide net: each driver on one of its belts, each consumer off one
            served = set().union(*reach_of.values()) if reach_of else set()
            dead = {d for d in drivers if not reach_of.get(d, set()) & set(consumers)}
            unreached = [(d, c) for d in drivers for c in consumers
                         if d in dead or c not in served]
            reached = len(drivers) * len(consumers) - len(unreached)
        mine = carried.get(name, [])
        r = NetReport(name, drivers, consumers, missing, len(drivers) * len(consumers), reached,
                      unreached, _segments(g, mine), len(mine),
                      [k for k in mine if (g.nodes[k].kind == "underground-belt"
                                           and k not in g.pairs)
                       or (g.nodes[k].kind == "pipe-to-ground" and k not in g.fluid_pairs)],
                      name in fluid,
                      [lk for lk in leaks if lk.net == name],
                      [(a, b) for a, b in ug_sides if name in g.nodes[a].nets])
        reports[name] = r
        if not r.connected:
            checks.append(_status_line(r))
        checks += [Check("SPILL", f"net {name}", lk.text) for lk in r.leaks]
    checks += [Check("SPILL", f"net {lk.net}" if lk.net else "no net", lk.text)
               for lk in leaks if lk.net not in reports]
    return Report(g, reports, leaks, checks)


def _touch(a, b):
    """Two nodes' tiles share an edge (2.0: heat passes by contact)."""
    tiles = set(b.tiles)
    return any((x + dx, y + dy) in tiles for x, y in a.tiles
               for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)))

