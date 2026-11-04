"""fkcore.lanes: the lane graph of a layout (CORE-V2.md), by the lane physics measured in the game.

A **connector** is one lane of one belt, underground or splitter, ``(key, "L")`` / ``(key, "R")``
(L and R looking along the flow: the game's transport lines 1 and 2), or one inserter,
``(key, "")``. A machine (anything an inserter reaches, a drill) is an endpoint node ``(key, "")``.
An edge is one way items move from one node into another, built over ``fkcore.connect``'s
carrier graph (V1's geometry: tiles, underground pairing, inserter reach) with the rules of
docs/LAB-LANES-20261020.md (2.0.77):

- ``straight``: a belt, an underground exit or a splitter onto the next belt, underground
  entrance or splitter from behind; a ``turn`` (V1's ``curve``: fed from one side, nothing
  behind) keeps the lanes too; ``tunnel``: an underground entrance to its exit, lane by lane.
- ``side``: a belt into the side of a belt is a 2-to-1 merge: both of its lanes go onto the
  target's near lane. The lane that enters the target further upstream is served first.
- ``ug_side``: a belt into the side of an underground (either end): only its lane on the open
  side goes through (an entrance is open upstream, an exit downstream), onto the near lane; the
  lane on the closed side is blocked, so it has no edge (``LaneGraph.closed`` lists it, for the
  routed rates' ``WRONG-LANE`` line).
- ``pickup``: an inserter picks from both lanes of a belt tile, or from a machine.
- ``drop``: an inserter across a belt fills the far lane; along it (in line), always the right
  lane; onto a turning belt, the inside lane from either of its outer sides (a left turn's left
  lane) and the outside lane from its inner sides, over the belts that meet there (long-handed;
  2.1.25, all measured on 2.0.77). Onto a splitter half and both underground ends alike: an exit takes drops from
  every side, though V1's connect gives it no drop edge (v4 B-120), so this module adds them.
- ``mine``: a drill drops onto the near lane (in line: the right lane, by the inserter rule;
  not measured).
- a splitter's **priorities** (its game settings, ``config``; Rory's bus staircases): with an
  input priority, the edges into its preferred half rank before the other half's (it takes
  what comes there first); with an output priority, the edges out of its preferred half are
  ``first`` (what it passes on goes there first, the rest to the other half). Lane by lane, as
  every splitter edge.

Capacities, in items/s: a belt or underground lane carries ``belt_speed x 60 x 4`` (yellow 7.5); a
splitter's lane node stands for both halves (twice that); an inserter moves one hand per swing,
a swing taking ``2 x floor(0.5 / rotation speed)`` ticks (measured: the fast inserter makes 2.5 a
hand, not the 2.4 of the rule of thumb), less when it drops onto a belt at hand 3 and up. The
inserter's prototype values are the catalog's (``rotation_speed``, ``bulk``, ``stack_size_bonus``:
the stack inserter's own +4 hand; read from the game, 2.0.77, ``docs/LAB-LANES-20261020.md``), so
a modded or new inserter in the catalog is rated like the others; ``ROTATION`` is what the catalog
says for the six of 2.0, for a catalog without them. A machine has no capacity of its own. Pure,
stdlib only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import connect as connectmod

__all__ = ["Node", "Edge", "LaneGraph", "build", "lane_rate", "inserter_rate", "ROTATION",
           "HOLDERS", "INF"]

INF = float("inf")
_BELTS = frozenset({"transport-belt", "underground-belt", "splitter"})
_STEP = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}

#: Turns per tick, read from the game (2.0.77, tools/lab_lanes.py protos), as the catalog has them
#: (``rotation_speed``): for a catalog without them.
ROTATION = {"burner-inserter": 0.013, "inserter": 0.014, "long-handed-inserter": 0.02,
            "fast-inserter": 0.04, "bulk-inserter": 0.04, "stack-inserter": 0.04}
#: A hand's own bonus over the force's (the stack inserter's +4: ``stack_size_bonus``).
_OWN_HAND = {"stack-inserter": 4}
_BULK = frozenset({"bulk-inserter", "stack-inserter"})
#: Dropping onto a moving belt, the share of the chest rate a hand of n reaches (measured onto
#: yellow: fast hand 3 0.75, bulk hand 4 0.60; hands 1 and 2 lose nothing worth counting).
_ONTO_BELT = {1: 1.0, 2: 1.0, 3: 0.75, 4: 0.6}
#: Entity types an inserter or a drill reaches: the machines (connect's endpoints, and drills).
HOLDERS = connectmod.HOLDER_TYPES | {"mining-drill"}


def lane_rate(cat, name):
    """Items/s one lane of a belt, underground or splitter of ``name`` carries."""
    return float(cat.entity(name)["belt_speed"]) * 60 * 4


def _proto(cat, name):
    """(rotation speed, bulk, own hand bonus) of an inserter: the catalog's, else ROTATION's."""
    row = {}
    if cat is not None and cat.has_entity(name):
        row = cat.entity(name)
    rot = row.get("rotation_speed", ROTATION.get(name))
    bulk = row.get("bulk", name in _BULK)
    return rot, bool(bulk), int(row.get("stack_size_bonus", _OWN_HAND.get(name, 0)) or 0)


def inserter_rate(name, hand=1, bulk_hand=1, onto_belt=False, cat=None):
    """Items/s an inserter moves: one hand per swing. ``hand`` is the non-bulk hand size (1 + the
    force's inserter bonus), ``bulk_hand`` the bulk one (1 + the bulk bonus); a stack inserter
    adds its own 4. The prototype from ``cat`` (the catalog), else ``ROTATION``. None for an
    inserter whose rotation speed is unknown."""
    rot, bulk, own = _proto(cat, name)
    if rot is None:
        return None
    n = (bulk_hand if bulk else hand) + own
    swing = 2 * math.floor(0.5 / rot + 1e-9)            # ticks per pick-and-drop
    rate = 60.0 / swing * n
    if onto_belt:
        rate *= _ONTO_BELT.get(n, 0.6)
    return rate


@dataclass
class Node:
    id: tuple                      # (key, lane): lane "L" | "R" for a belt carrier, "" otherwise
    kind: str                      # lane | splitter | inserter | machine
    key: str
    lane: str
    cap: float                     # items/s (INF for a machine)
    name: str = ""
    tier: str = ""                 # the carrier's prototype (a belt's tier)


@dataclass
class Edge:
    src: tuple
    dst: tuple
    via: str                       # straight turn tunnel side ug_side pickup drop mine
    rank: int = 0                  # priority at dst: lower is served first
    cap: float = INF               # items/s the edge carries: one half of a splitter's lane
    first: bool = False            # a splitter's preferred output: served before its other one


@dataclass
class LaneGraph:
    nodes: dict = field(default_factory=dict)     # id -> Node
    out: dict = field(default_factory=dict)       # id -> [Edge]
    inn: dict = field(default_factory=dict)       # id -> [Edge]
    graph: object = None                          # the connect.Graph under it
    problems: list = field(default_factory=list)
    closed: list = field(default_factory=list)    # [(lane node, underground key)]: the lane of a
                                                  # belt that side-loads an underground from its
                                                  # closed side (blocked: no edge)

    def edges(self):
        for v in self.nodes:
            yield from self.out.get(v, ())

    def add(self, src, dst, via, rank=0, first=False):
        if src not in self.nodes or dst not in self.nodes or src == dst:
            return
        for e in self.out.get(src, ()):
            if e.dst == dst:
                return
        e = Edge(src, dst, via, rank, first=first)
        # a splitter's lane node stands for both halves; what goes in or out of one half is one
        # lane of its tier (a yellow splitter passes 7.5/s a lane per side, whatever feeds it)
        for end in (src, dst):
            n = self.nodes[end]
            other = self.nodes[dst if end == src else src]
            if n.kind == "splitter" and other.kind != "inserter" or \
                    n.kind == "splitter" and via == "drop":
                e.cap = min(e.cap, n.cap / 2)
        self.out.setdefault(src, []).append(e)
        self.inn.setdefault(dst, []).append(e)


def _left(d):
    """The direction on the left of heading ``d`` (0 N, 4 E, 8 S, 12 W)."""
    return (d + 12) % 16


def _right(d):
    return (d + 4) % 16


def _near(src_dir, dst_dir):
    """The lane of a belt heading ``dst_dir`` nearest to a carrier moving ``src_dir`` into its
    side: from its left side (moving towards its right) the left lane."""
    if src_dir == _right(dst_dir):
        return "L"
    if src_dir == _left(dst_dir):
        return "R"
    return None


def _halves(n):
    """{"left": tile, "right": tile} of a splitter node, looking along its flow."""
    lx, ly = _STEP[_left(n.dir)]
    return {"left": (math.floor(n.x + 0.5 * lx), math.floor(n.y + 0.5 * ly)),
            "right": (math.floor(n.x - 0.5 * lx), math.floor(n.y - 0.5 * ly))}


def _half(g, s, other, out):
    """The half of splitter node ``s`` ("left" or "right") that an edge to (``out``) or from
    node ``other`` uses; None where both do (two splitters side by side)."""
    ns, no = g.nodes[s], g.nodes[other]
    if out:
        sx, sy = _STEP[ns.dir]
        hit = [h for h, t in _halves(ns).items() if (t[0] + sx, t[1] + sy) in no.tiles]
    else:
        step = _STEP.get(no.dir)
        if step is None:
            return None
        land = {(t[0] + step[0], t[1] + step[1]) for t in no.tiles}
        hit = [h for h, t in _halves(ns).items() if t in land]
    return hit[0] if len(hit) == 1 else None


def _priorities(g, a, b):
    """(rank at b, first out of a) of a belt edge a -> b, by the splitters' priorities."""
    rank, first = 0, False
    nb, na = g.nodes[b], g.nodes[a]
    if nb.kind == "splitter" and nb.prio and nb.prio[0]:
        half = _half(g, b, a, False)
        rank = 1 if half is not None and half != nb.prio[0] else 0
    if na.kind == "splitter" and na.prio and na.prio[1]:
        first = _half(g, a, b, True) == na.prio[1]
    return rank, first


def _drop_lane(drop_dir, belt_dir):
    """The lane an inserter dropping in direction ``drop_dir`` fills on a belt heading
    ``belt_dir``: the far lane across it, the right lane in line."""
    if drop_dir % 8 == belt_dir % 8:
        return "R"
    return "R" if drop_dir == _right(belt_dir) else "L"


def build(ents, cat, hand=1, bulk_hand=1) -> LaneGraph:
    """The lane graph of a Layout, a Surface, a list of entities or a ``connect.Graph``."""
    g = ents if isinstance(ents, connectmod.Graph) else connectmod.graph(ents, cat)
    lg = LaneGraph(graph=g, problems=list(g.problems))
    belts = {}
    for key, n in g.nodes.items():
        if n.kind in _BELTS:
            if not cat.entity(n.name).get("belt_speed"):
                lg.problems.append(f"{key}: {n.name} has no belt speed")
                continue
            rate = lane_rate(cat, n.name)
            kind = "splitter" if n.kind == "splitter" else "lane"
            for lane in ("L", "R"):
                lg.nodes[(key, lane)] = Node((key, lane), kind, key, lane,
                                             rate * (2 if kind == "splitter" else 1), n.name, n.name)
            belts[key] = n
        elif n.kind == "inserter":
            onto = any(g.nodes[b].kind in _BELTS for b, via in g.succ.get(key, ()) if via == "drop")
            rate = inserter_rate(n.name, hand, bulk_hand, onto_belt=onto, cat=cat)
            if rate is None:
                lg.problems.append(f"{key}: {n.name} has no known rotation speed")
                rate = INF
            lg.nodes[(key, "")] = Node((key, ""), "inserter", key, "", rate, n.name)
        elif n.kind in HOLDERS:
            lg.nodes[(key, "")] = Node((key, ""), "machine", key, "", INF, n.name)
    drops = set()
    for a, b, via in g.edges():
        na, nb = g.nodes[a], g.nodes[b]
        if via in ("belt", "curve"):
            if a in belts and b in belts:
                rank, first = _priorities(g, a, b)
                for lane in ("L", "R"):
                    lg.add((a, lane), (b, lane), "straight" if via == "belt" else "turn", rank,
                           first)
        elif via == "ug":
            for lane in ("L", "R"):
                lg.add((a, lane), (b, lane), "tunnel", 0)
        elif via == "sideload":
            near = _near(na.dir, nb.dir)
            if near is None:
                continue
            first = "L" if _left(na.dir) == (nb.dir + 8) % 16 else "R"   # enters further upstream
            lg.add((a, first), (b, near), "side", 1)
            lg.add((a, "R" if first == "L" else "L"), (b, near), "side", 2)
        elif via == "ug_sideload":
            near = _near(na.dir, nb.dir)
            if near is None:
                continue
            open_dir = (nb.dir + 8) % 16 if nb.ug == "in" else nb.dir
            lane = "L" if _left(na.dir) == open_dir else "R"
            lg.add((a, lane), (b, near), "ug_side", 1)
            if (a, "R" if lane == "L" else "L") in lg.nodes:
                lg.closed.append(((a, "R" if lane == "L" else "L"), b))
        elif via == "pickup":
            if a in belts:
                for lane in ("L", "R"):
                    lg.add((a, lane), (b, ""), "pickup", 0)
            else:
                lg.add((a, ""), (b, ""), "pickup", 0)
        elif via == "drop":
            drops.add((a, b))
            _drop(lg, g, belts, a, b)
        elif via == "mine":
            if b in belts:
                side = (na.dir + 8) % 16                 # the half of the tile towards the drill
                lane = "L" if side == _left(nb.dir) else "R"
                lg.add((a, ""), (b, lane), "mine", 3)
            else:
                lg.add((a, ""), (b, ""), "mine", 0)
    # an underground exit takes drops too (measured; connect gives it no drop edge)
    for key, n in g.nodes.items():
        if n.kind != "inserter":
            continue
        hand_tiles = g.inserter_tiles(key)
        if hand_tiles is None:
            continue
        b = g.at.get(hand_tiles[1])
        if b is not None and (key, b) not in drops and b in belts \
                and g.nodes[b].kind == "underground-belt" and g.nodes[b].ug == "out":
            _drop(lg, g, belts, key, b)
    return lg


def _turn(g, b, side):
    """The lane a drop from ``side`` of belt ``b`` fills when ``b`` turns, or None when it runs
    straight: from an outer side the inside lane (a left turn's left lane), from an inner side,
    where the belt comes in or goes out, the outside lane."""
    for a, via in g.pred.get(b, ()):
        if via == "curve":
            come, go = g.nodes[a].dir, g.nodes[b].dir
            inside = "L" if go == _left(come) else "R"
            if side in ((come + 8) % 16, go):
                return "R" if inside == "L" else "L"
            return inside
    return None


def _drop(lg, g, belts, a, b):
    na, nb = g.nodes[a], g.nodes[b]
    if b in belts:
        # onto a turn by its sides (2.1.25, measured: 2.0.77); an inserter faces its pickup, on
        # its side of the belt
        lane = _turn(g, b, na.dir) if nb.kind == "transport-belt" else None
        lane = lane or _drop_lane((na.dir + 8) % 16, nb.dir)    # an inserter faces its pickup
        lg.add((a, ""), (b, lane), "drop", 3)
    else:
        lg.add((a, ""), (b, ""), "drop", 0)
