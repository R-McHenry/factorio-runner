"""fkcore.expand: template instances -> the factory's .fkp (visible expansion), explain, unfold.

``expand_factory`` writes every placed instance's Fragment into one factory layout under the
instance's keys (``red/m0``, ``red/f0``, ``red/in1b3``): from then on they are data, not code. A
role is bound to the net its machines' endpoints sit on, which gives every carrier its ``net=``.
Placement in V1 is manual: an instance's ``@(x, y, rot r)`` hint is the top-left of its turned
frame, relative to its sub-module's own hint; a sub-module instance's ``rot`` turns its whole
contents inside the module's frame, the way a plot turns a factory (``_origin``). A MinerRow with
``@patch(resource)`` and no ``@(x, y)`` is placed on its ore by ``fkcore.placer`` (given the
terrain in the factory frame, ``ground``), after every pinned instance, so it keeps clear of them.
Unplaced instances are listed, never guessed; the routes between attach points are the router's
(the next step), so a factory expanded here has its template segments and none of the nets' own
carriers yet.

A sealed module's ports (2.0.28–2.0.41) are carriers on its frame's edge: a port's tile is the
declaration's pinned ``@side[pos]``, a committed record (``flat.port_records``: the ``.fkp``'s
``port`` lines, in the module's own frame), or — 2.0.41, PLAN-GLOBAL phase A — the tile of its
declared side **facing its partner**, chosen after every instance is placed and recorded in the
layout (``lay.ports``). An ``X | X`` port is the whole belt end, so its carrier says ``net=A|A``.
A rebuild lays the same tiles, so a parent change (a move, a turn) moves neither a port nor the
module's inside routes.

Two nets that share a belt (``lanes A | B``, 2.0) are one input belt: a consumer on either of them
gets one role bound to the pair's ``A|B``, so its belt carries both lanes (and its inserters
``A+B``: each takes from both lanes what its machine accepts). A template's own belt of two nets is
a role written ``in1|in2``: its carriers carry the two roles' nets, in1's on the left lane
(``SmelterArray``'s feed belts, where the template side-loads ore and fuel).

``explain`` prints one instance both ways: the template line and the plain entities, nets and
attach points it stands for. ``unfold`` replaces the template line with ``Literal`` and keeps every
.fkp key and line, so the world does not change; the Literal's machines are read back from its
entities. Pure, stdlib only.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from . import arms as armsmod
from . import avail as availmod
from . import fkp as fkpmod
from . import placer
from .codec import config_text
from .compose import turn
from .fragments import E, N, S, W, FEnt, Fragment, FragmentError, Role, _finish, fragment
from .templates import CONTACT, SINGLE
from .terrain import fmt_amount

__all__ = ["belts", "fragment_of", "expand_factory", "explain", "explain_net", "unfold",
           "attach_points", "frame_of", "planned_frame", "frame_overlaps"]

#: 2.1: the core kinds that are one machine each (``templates.SINGLE``); an instance is single when
#: its kind resolves to one of them (``Assembler`` is a ``Machine``)
_SINGLE = SINGLE
_NO_DIRECTION = ("infinity-pipe",)          # fluid prototypes the game never turns
_H = {0: 0, 4: 1, 8: 2, 12: 3}
_DX = (0, 1, 0, -1)
_DY = (-1, 0, 1, 0)


def _core(inst):
    """The core kind an instance is built as (2.1, ``elaborate.core_of``)."""
    return getattr(inst, "core", None) or inst.kind


def _params(inst):
    """An instance's parameters with its kinds' defaults (2.1, ``elaborate.params_of``)."""
    kp = getattr(inst, "kparams", None)
    return inst.params if kp is None else kp


def belts(flat, key, side):
    """[(net, [items])] for machine ``key``: one entry per distinct net, recipe order first. A
    net of contact items (2.0: heat, ``templates.CONTACT``) is left out: it has no route, so no
    role and no attach point. An input on a net that shares a belt (``lanes``) is on the pair's
    ``A|B``: one belt."""
    hold = flat._in_hold if side == "in" else flat._out_hold
    m = flat.machines[key]
    order = list(m.needs if side == "in" else m.makes)
    lane_of = getattr(flat, "lane_of", {}) if side == "in" else {}
    # 2.0.58: one item may come from several nets (a circuit assembler handed cable by two cable
    # assemblers, each its own net): a role per net, not only the first
    # 2.1.32: the hold indexed by machine once (a scan of it all for every machine was most of a
    # megabase grid's route setup); rebuilt if the hold has changed size since
    memo = flat.__dict__.setdefault("_hold_by_key", {})
    got = memo.get(side)
    if got is None or got[0] is not hold or got[1] != len(hold):
        idx = {}
        for (k, item), nets in hold.items():
            idx.setdefault(k, []).append((item, nets))
        got = memo[side] = (hold, len(hold), idx)
    pairs = [(item, lane_of.get(net, net)) for item, nets in got[2].get(key, ())
             if item not in CONTACT for net in nets]
    pairs.sort(key=lambda p: order.index(p[0]) if p[0] in order else len(order))
    out = []
    for item, net in pairs:
        for b in out:
            if b[0] == net:
                b[1].append(item)
                break
        else:
            out.append((net, [item]))
    return out


_PORT_OUT = {"n": N, "e": E, "s": S, "w": W}
_PORT_IN = {N: S, E: W, S: N, W: E}
_LEFT = {N: W, E: N, S: E, W: S}          # the lane on the left of a belt heading d
_STEP = {N: (0, -1), E: (1, 0), S: (0, 1), W: (-1, 0)}


def _unturn(x, y, size, rot):
    """A point in the factory frame -> the instance's own frame (the inverse of ``compose.turn``):
    ``turn`` of this is ``(x, y)`` again."""
    w, h = size
    for _ in range(rot % 4):
        x, y = y, w - x
        w, h = h, w
    return x, y


def _port_tile(port, w, h, choice=None):
    """(x, y, the outward direction) of a port's tile, in the module's own frame: the recorded or
    chosen tile (``choice``), else ``pos`` along the side (0-based, as the ``port`` check and
    2.0.29's ``@n0L`` count) when the declaration pinned it, else the middle of that side.
    2.0.41: the build chooses an unpinned port's tile facing its partner
    (``_choose_port_tiles``) and records it; this reads the record back."""
    if choice is not None:
        return choice[0], choice[1], choice[2]
    side = port.side
    along = h if side in ("e", "w") else w
    k = port.pos if port.pos is not None else along // 2
    if not 0 <= k < along:
        raise FragmentError(f"port {port.name}: @{side}{port.pos} is off the frame (its {side} edge "
                            f"runs 0..{along - 1})")
    return _edge_tile(side, k, w, h) + (_PORT_OUT[side],)


def _edge_tile(side, k, w, h):
    """The tile ``k`` along ``side`` of a ``w`` x ``h`` frame, in its own frame."""
    if side in ("e", "w"):
        return (w - 1 if side == "e" else 0, k)
    return (k, 0 if side == "n" else h - 1)


def record_misfit(port, rec):
    """Why a committed port record no longer fits its port's declaration, or None (2.0.42). A pin
    wins over a record, and a record must stand on the declared side's edge of the module's frame
    as it is now: a port moved to another side, or a frame that shrank under it, is chosen again."""
    if port.size is None:
        return None
    w, h = port.size
    if port.pos is not None:
        pin = _edge_tile(port.side, port.pos, w, h)
        if (rec.x, rec.y) != pin:
            return f"the pin @{port.side}{port.pos} moves it to {pin[0]},{pin[1]}"
        return None
    along = h if port.side in ("e", "w") else w
    k = rec.y if port.side in ("e", "w") else rec.x
    if not 0 <= k < along or (rec.x, rec.y) != _edge_tile(port.side, k, w, h):
        return f"it is not on the {port.side} edge of the {w}x{h} frame: the port is chosen again"
    return None


def _port_belt(port, out):
    """The direction of a port carrier: into the module for an ``in`` port, out for an ``out``."""
    return _PORT_IN[out] if port.dir == "in" else out


def _port_out(port, belt):
    """The outward direction a port carrier's ``belt`` direction means: ``_port_belt``'s inverse
    (``_PORT_IN`` is its own inverse)."""
    return _PORT_IN[belt] if port.dir == "in" else belt


def ports_of(flat, path):
    """The port keys an instance ``path`` has, sorted (its own ports, not a child's)."""
    # 2.1.32: the keys indexed by their instance once (a scan of every port for every module was
    # most of a megabase grid's fragments); rebuilt when the ports change
    memo = flat.__dict__.get("_ports_by_inst")
    if memo is None or memo[0] != len(flat.ports) or memo[1] is not flat.ports:
        idx = {}
        for k in flat.ports:
            if "/" in k:
                idx.setdefault(k.rsplit("/", 1)[0], []).append(k)
        memo = flat.__dict__["_ports_by_inst"] = (len(flat.ports), flat.ports,
                                                   {p: sorted(ks) for p, ks in idx.items()})
    return list(memo[2].get(path, ()))


def _port_choice(flat, key):
    """The recorded tile of port ``key`` in its module's own frame, from ``flat.port_records``
    (the committed .fkp's ``port`` lines): (x, y, out, lane), or None. The record is already in
    that frame, so a parent change (a move, a turn) does not touch it."""
    if key in flat.port_tiles:
        return flat.port_tiles[key]
    rec = flat.port_records.get(key)
    if rec is None:
        return None
    port = flat.ports[key]
    if record_misfit(port, rec) is not None:      # the source moved it: chosen again, recorded anew
        return None
    choice = (int(rec.x), int(rec.y), _port_out(port, rec.dir % 16), rec.lane)
    flat.port_tiles[key] = choice
    return choice


def _point_of(flat, lay, key, cat):
    """Where a netlist endpoint stands in the factory frame: its entity's position, or for a port
    (2.0.48: an edge, no entity of its own) the tile just inside its edge."""
    if key in flat.ports:
        if cat is None:
            return None
        # 2.1.28: only the port's own module (port_edges of every module, for each partner, was
        # ~30 s of the 20261030-2352 world's place step)
        edge = _module_edges(flat, lay, key.rsplit("/", 1)[0], cat, {}).get(key)
        return None if edge is None else (edge.inside[0] + 0.5, edge.inside[1] + 0.5)
    e = lay.entities.get(key)
    return None if e is None else (float(e.x), float(e.y))


def _partner_points(flat, lay, key, cat=None):
    """The factory positions of the other ends of the nets that name port ``key``: what the port
    faces, and so which tile of its side the build chooses. The **outside** nets come first (the
    parent's end is the partner a plot's port lines up with; the module's own route bends inside
    its frame), the inside ones only when no outside end is placed."""
    outside, inside = [], []
    scope = flat.ports[key].scope
    for item in flat.ports[key].all_items:
        for hold in (getattr(flat, "_in_hold", {}), getattr(flat, "_out_hold", {})):
            for net in hold.get((key, item), ()):
                n = flat.nets.get(net)
                if n is None:
                    continue
                mine = flat.nets[net].scope == scope
                for k, _it in list(n.drivers) + list(n.consumers):
                    if k == key:
                        continue
                    pt = _point_of(flat, lay, k, cat)
                    if pt is not None:
                        (inside if mine else outside).append(pt)
    return outside or inside


def _side_tiles(port, w, h, taken):
    """The free tiles along a port's side, in a fixed order: what the build may choose from."""
    if port.side in ("e", "w"):
        x = w - 1 if port.side == "e" else 0
        return [(x, y) for y in range(h) if (x, y) not in taken]
    y = 0 if port.side == "n" else h - 1
    return [(x, y) for x in range(w) if (x, y) not in taken]


def _lane_facing(belt, center, targets):
    """The lane of a one-lane port that faces its partner: L when the partner lies on the left of
    the belt's travel, R when it lies on the right (as ``fkcore.lanes`` names the lanes)."""
    if not targets:
        return "L"
    tx = sum(t[0] for t in targets) / len(targets)
    ty = sum(t[1] for t in targets) / len(targets)
    lx, ly = _STEP[_LEFT[belt]]
    return "L" if (tx - center[0]) * lx + (ty - center[1]) * ly > 0 else "R"


def _port_items_text(port):
    """A port's items as its record writes them: ``iron-plate``, ``iron-plate|iron-plate``."""
    left = ",".join(port.items)
    if port.right is None:
        return left
    return f"{left}|{','.join(port.right)}"


def _choose_port_tiles(flat, lay, cat=None):
    """Choose the tile of every port the declaration does not pin and no record holds, **facing its
    partner** (2.0.41, PLAN-GLOBAL phase A): the tile of the declared side nearest the other end's
    attach point, and for a one-lane port the lane on the partner's side. A recorded tile is kept as
    it is; a pinned one is kept too (a record that disagrees is a ``port`` line). Fills
    ``flat.port_tiles`` (the module's own frame), so what is laid is what the record will keep
    (2.0.48: a port is an edge, with no carrier to move; its attach points follow the choice)."""
    tiles = getattr(flat, "port_tiles", None)
    if tiles is None:
        return
    for path in sorted(lay.insts, key=fkpmod.natural_key):
        inst = flat.insts.get(path)
        if inst is None or inst.module is None or inst.size is None or path not in lay.insts:
            continue
        w, h = inst.size
        rec = lay.insts[path]
        at = (rec.x, rec.y, rec.rot)
        mine = ports_of(flat, path)
        used = set()                            # the tiles a record or a pin already holds
        for key in mine:
            if key not in tiles:
                _port_choice(flat, key)
            if key in tiles:
                used.add((tiles[key][0], tiles[key][1]))
        for key in mine:
            if key in tiles:                    # recorded, or chosen above
                continue
            port = flat.ports[key]
            x, y, out = _port_tile(port, w, h)
            own = [_unturn(tx - at[0], ty - at[1], (w, h), at[2])
                   for tx, ty in _partner_points(flat, lay, key, cat)]
            if port.pos is None:
                cands = _side_tiles(port, w, h, used) or _side_tiles(port, w, h, set())
                if own:
                    x, y = min(cands, key=lambda t: (round(_dist(t, own), 6), t[1], t[0]))
                elif (x, y) in used:            # the middle is another port's: step aside
                    x, y = cands[0]
                out = _PORT_OUT[port.side]
            belt = _port_belt(port, out)
            if port.right is not None:
                lane = "LR"
            elif port.lane is not None:
                lane = port.lane
            else:
                lane = _lane_facing(belt, (x + 0.5, y + 0.5), own)
            tiles[key] = (x, y, out, lane)
            used.add((x, y))


def _dist(tile, points):
    """The squared distance from a tile's center to the nearest of ``points``."""
    cx, cy = tile[0] + 0.5, tile[1] + 0.5
    return min((cx - x) ** 2 + (cy - y) ** 2 for x, y in points)


def _port_records(flat, lay):
    """Every placed port as its ``.fkp`` record (2.0.41): the tile **in the module's own frame**,
    the carrier's direction there, the lane(s) and the items. A rebuild reads the tile back
    (``flat.port_records``), so a parent change never moves a port."""
    recs = {}
    for path, rec in lay.insts.items():
        inst = flat.insts.get(path)
        if inst is None or inst.module is None or inst.size is None:
            continue
        w, h = inst.size
        for key in ports_of(flat, path):
            port = flat.ports[key]
            try:
                x, y, out = _port_tile(port, w, h, _port_choice(flat, key))
            except FragmentError:
                continue
            belt = _port_belt(port, out)
            choice = flat.port_tiles.get(key)
            lane = (choice[3] if choice else None) or port.lane \
                or ("LR" if port.right is not None else "L")
            key_rec = fkpmod.port_record_key(key)
            recs[key_rec] = fkpmod.PortRec(key_rec, int(x), int(y), int(belt), lane,
                                           _port_items_text(port))
    return recs


@dataclass
class PortEdge:
    """Where a port crosses its module's frame (2.0.48): the tile just ``inside`` the edge and the
    one just ``outside`` it, in the factory frame, and the crossing as the items go, ``src`` ->
    ``dst`` (outside -> inside for an ``in`` port, inside -> outside for an ``out`` one).
    ``fluid`` (2.1): the fluid of a fluid port, whose pipe crosses the edge there; None for a
    belt's port."""
    inside: tuple
    outside: tuple
    src: tuple
    dst: tuple
    fluid: str | None = None


def port_edges(flat, lay, cat):
    """{port key: PortEdge} of every placed sealed module's ports (2.0.48, Rory 2026-10-26: "our
    boundaries should be along an edge so that a port occupies zero area"). A port is no entity:
    the module's own route reaches the tile inside the edge and the parent's the tile outside it,
    and the belt crosses the edge between them."""
    out = {}
    for path in lay.insts:
        _module_edges(flat, lay, path, cat, out)
    return out


def _module_edges(flat, lay, path, cat, out):
    """``port_edges`` of the one placed module ``path``, added to ``out``: -> ``out``."""
    inst = flat.insts.get(path)
    if path in lay.insts and inst is not None and inst.module is not None \
            and inst.size is not None:
        try:
            points = attach_points(flat, lay, path, cat)
        except FragmentError:
            return out
        for role, _side, _net, (x, y, d) in points:
            key = f"{path}/{role}"
            port = flat.ports.get(key)
            if port is None or d is None:
                continue
            dx, dy = _STEP[d]
            here = (math.floor(x), math.floor(y))           # the inside role's virtual belt: outside
            ahead = (here[0] + dx, here[1] + dy)
            behind = (here[0] - dx, here[1] - dy)
            fluid = port.items[0] if len(port.items) == 1 and port.right is None \
                and cat is not None and cat.is_fluid(port.items[0]) else None
            if port.dir == "in":
                out[key] = PortEdge(ahead, here, here, ahead, fluid)
            else:
                out[key] = PortEdge(behind, here, behind, here, fluid)
    return out


def _module_fragment(flat, path, cat):
    """A sealed module's ports as the router meets them: two roles per port and no entity
    (2.0.48: a port is an edge of the frame, occupying no tile). The inside role, bound to the
    module's own net, is attached at the tile just **outside** the edge, as the virtual belt that
    feeds it (an ``in`` port's driver) or that it feeds (an ``out`` port's consumer); the outside
    role, bound to the parent's net, at the tile just **inside**, the same way round. So the
    module's route reaches the inside tile, the parent's the outside one, and the belt crosses the
    edge between them; two ports that meet on one edge (a stack's cells) need nothing laid.

    The tile comes from the .fkp's port record when there is one (``_port_choice``, 2.0.41:
    recorded tiles are in the module's own frame, so a parent move keeps them). 2.0.36-2.0.47 laid
    a carrier belt on the edge tile itself: a port took a tile of the module and one each side."""
    inst = flat.insts[path]
    w, h = inst.size
    ents, roles, binding, seen = [], {}, {}, {}
    for key in ports_of(flat, path):
        port = flat.ports[key]
        item = port.items[0] if port.items else None
        choice = _port_choice(flat, key)
        x, y, out = _port_tile(port, w, h, choice)
        if (x, y) in seen and choice is None and port.pos is None:
            # 2.1.19 (the live run's LabArea, ~4480k): three `@e` ports all took the middle of
            # the side here and the module was left unplaced. An unpinned port's tile is chosen
            # after placing (_choose_port_tiles); until then it takes the free tile nearest the
            # middle
            free = _side_tiles(port, w, h, set(seen))
            if free:
                mid = (w if port.side in ("n", "s") else h) // 2
                x, y = min(free, key=lambda t: (abs((t[0] if port.side in ("n", "s") else t[1])
                                                    - mid), t))
        if (x, y) in seen:
            raise FragmentError(f"{path}: ports {seen[(x, y)]} and {port.name} share the tile "
                                f"{x},{y}: give one a position along the side")
        seen[(x, y)] = port.name
        belt = _port_belt(port, out)          # the way the items cross the edge
        ox, oy = x + _STEP[out][0], y + _STEP[out][1]
        inside_role, outside_role = port.name, port.name + "!"
        # 2.1.31: a paired port's nets are looked up by each of its items, so a net on its right
        # lane alone binds it too (a line's far end whose left lane runs out there: the PU bay's
        # red | processing-unit belt, whose units leave and whose red circuits end)
        def held(hold):
            for it in list(port.items or [None]) + list(port.right or []):
                got = hold.get((key, it), [])
                if got:
                    return got
            return []
        if port.dir == "in":
            inner, outer = held(flat._out_hold), held(flat._in_hold)
            inside_side, outside_side = "out", "in"
        else:
            inner, outer = held(flat._in_hold), held(flat._out_hold)
            inside_side, outside_side = "in", "out"
        roles[inside_role] = Role(inside_role, inside_side, [item] if item else [], [key],
                                  [(ox + 0.5, oy + 0.5, belt)])
        roles[outside_role] = Role(outside_role, outside_side, [item] if item else [], [key],
                                   [(x + 0.5, y + 0.5, belt)])
        # 2.0.48: a paired port's lanes are a ``lanes A | B`` pair on one side: its role is the
        # pair's (both lanes come in, or go out, on the one belt across the edge). A one-item
        # port that drives one lane of a pair drives that lane's own net, as a machine does
        # (2.1.3: bound to the pair, the route took it for a belt already paired and failed)
        lane_of = getattr(flat, "lane_of", {})
        paired = port.right is not None and not port.both
        for role, nets, side in ((inside_role, inner, inside_side),
                                 (outside_role, outer, outside_side)):
            net = (nets or [None])[0]
            binding[role] = lane_of.get(net, net) if paired or side == "in" else net
    params = {k: v for k, v in inst.params.items() if k not in ("duty", "dnp")}
    return _finish(Fragment(inst.kind, params, ents, roles, (w, h))), binding


def fragment_of(flat, path, cat):
    """(Fragment, {role: net}) for template instance ``path``."""
    inst = flat.insts[path]
    if inst.kind == "Bus":
        return _bus_fragment(flat, path)
    if inst.module is not None:
        return _module_fragment(flat, path, cat)
    keys = inst.machines
    ins, outs = belts(flat, keys[0], "in"), belts(flat, keys[0], "out")
    for k in keys[1:]:
        if belts(flat, k, "in") != ins or belts(flat, k, "out") != outs:
            raise FragmentError(f"{path}: its machines sit on different nets, so no single row "
                                "belt can serve them")
    m0 = flat.machines[keys[0]]
    kind = _core(inst)
    if kind in _SINGLE:
        frag = _single(flat, keys, cat, ins, outs)
    else:
        params = {k: v for k, v in _params(inst).items() if k not in ("duty", "dnp")}
        if params.get("inserter") is not None:
            params["inserter"] = _hand_param(path, params["inserter"], cat)
        elif kind != "SmelterArray":
            # 2.0.15 (Rory, 2026-10-25): a row laid without inserter= comes in on the lowest hand
            # the network can build, and the hands step raises the connectors the flow needs more
            # of. A SmelterArray keeps its own plain default (Rory's stack).
            params["inserter"] = availmod.choose("hand", "any", need=_row_hands(inst))[0]
        if kind in ("SmelterRow", "SmelterArray"):
            params["_recipe"] = m0.recipe
            fuel = [i for i in m0.needs if i not in cat.recipe_rates(m0.recipe, 1)[0]]
            params["_fuel"] = fuel[0] if fuel else None
        if kind == "SmelterArray":
            params["_per"] = _array_column(cat, m0)
        frag = fragment(kind, params, [b[1] for b in ins], [b[1] for b in outs])
        if kind == "MinerRow" and _row_belt_leaves(flat, path, frag):
            frag = _drills_only(frag)
        _row_modules(frag, flat, path, cat)
    binding = {}
    names_in = [r for r in frag.roles if frag.roles[r].side == "in"]
    names_out = [r for r in frag.roles if frag.roles[r].side == "out"]
    for name, (net, _items) in zip(names_in, ins):
        binding[name] = net
    for name, (net, _items) in zip(names_out, outs):
        binding[name] = net
    return frag, binding


def _port_carriers(flat, path, ents):
    """3.0, Literal ports: {carrier group net: the net its port is on}. A port's whole group of
    touching pipes (or belts) carries the net that names the port, so the router keeps every
    other net off it, as off any net's pipe."""
    ports = getattr(flat, "_lit_ports", {}).get(path)
    if not ports:
        return {}
    group = {e.key: e.attrs.get("net") for e in ents}
    out = {}
    for key, _side in ports.values():
        nets = {n for hold in (getattr(flat, "_out_hold", {}), getattr(flat, "_in_hold", {}))
                for (k, _i), ns in hold.items() if k == key for n in ns}
        if len(nets) == 1 and group.get(key):
            out[group[key]] = nets.pop()
    return out


def _row_belt_leaves(flat, path, frag):
    """3.0 (drills in cells): True when a MinerRow's own out belt would run out of the frame its
    module declares (``size WxH``): a drill in a cell, where the cell's through-belt is the
    drills' belt. A row anywhere else keeps its belt."""
    if "/" not in path:
        return False
    parent = flat.insts.get(path.rsplit("/", 1)[0])
    hint = flat.insts[path].hints.get("at")
    if parent is None or parent.size is None or hint is None:
        return False
    w, h = parent.size
    for ax, ay, ad in frag.roles["out"].attach:
        x, y, d = turn(float(ax), float(ay), ad, int(hint[2]) % 4, frag.size)
        hd = int(d) // 4
        tx = math.floor(x + hint[0]) + (0, 1, 0, -1)[hd]
        ty = math.floor(y + hint[1]) + (-1, 0, 1, 0)[hd]
        if not (0 <= tx < w and 0 <= ty < h):
            return True
    return False


def _drills_only(frag):
    """A MinerRow without its belt: its drills drive the out net by dropping on their drop tiles,
    and the router lays the belt over them (``route.DropSink``)."""
    ents = [e for e in frag.entities if e.role != "out"]
    out = frag.roles["out"]
    roles = dict(frag.roles, out=Role("out", "out", out.items, out.machines,
                                       [(frag.size[0] / 2, frag.size[1] / 2, None)]))
    return _finish(Fragment(frag.kind, dict(frag.params), ents, roles, frag.size))


#: the module inventory of each machine kind, as the paste's ``items`` names it (2.0)
_MODULE_INVENTORY = {"assembling-machine": 4, "furnace": 4, "mining-drill": 2, "lab": 3,
                     "beacon": 1}


def _module_fields(m, cat):
    """``modules=`` (``templates.with_modules``) as the paste's item requests: robots bring the
    modules and put them in the machine's slots (Codex's lab run, 2026-10-28)."""
    mods = m.extra.get("modules")
    if not mods:
        return {}
    inv = _MODULE_INVENTORY.get(cat.kind(m.prototype), 4)
    per = {}
    for slot, name in enumerate(mods):
        per.setdefault(name, []).append({"inventory": inv, "stack": slot, "count": 1})
    return {"items": [{"id": {"name": name, "quality": "normal"},
                       "items": {"in_inventory": spots}} for name, spots in per.items()]}


def _row_modules(frag, flat, path, cat):
    """A row's machines get their modules' requests, as a single machine's do (``_machine_attrs``)."""
    for e in frag.entities:
        m = flat.machines.get(f"{path}/{e.local}")
        if e.role is None and m is not None:
            fields = _module_fields(m, cat)
            if fields:
                e.attrs["config"] = config_text(fields)


def _row_hands(inst):
    """How many short hands a row instance lays, at least: one per machine, which is what the
    availability choice must be able to build (two for a two-handed row, but the row's machine count
    is the number that matters for stock)."""
    return max(len(inst.machines), 1)


def _hand_param(path, want, cat):
    """A row's ``inserter=``: the prototype it names, checked, or the one ``any`` picks now
    (``fkcore.avail``: the best hand the network can build, else the best it can make)."""
    got, _note = availmod.choose("hand", want)
    if not cat.has_entity(got):
        raise FragmentError(f"{path}: inserter={want}: no prototype {got!r} in the catalog")
    if cat.kind(got) != "inserter":
        raise FragmentError(f"{path}: inserter={want}: {got} is a {cat.kind(got)}, not an inserter "
                            "(inserter, fast-inserter, long-handed-inserter, bulk-inserter, ... "
                            "or any)")
    return str(got)


def _array_column(cat, m):
    """How many of machine ``m`` one SmelterArray column holds: as many as one yellow lane of
    their main ingredient feeds (24 stone furnaces on ore: 7.5/s at 0.3125 each, Rory's stack)."""
    from .lanes import lane_rate
    main = [r for i, r in m.needs.items() if i in cat.recipe_rates(m.recipe, 1)[0]]
    if not main or max(main) <= 0:
        return 24
    return max(1, int(lane_rate(cat, "transport-belt") / max(main) + 1e-9))


def _bus_fragment(flat, path):
    """A Bus: its ports' sides turned by its own rot (they are compass sides of the frame its
    hint is written in: the factory's, or its module's, which turns them with everything in it),
    a stub for each port a net names, bound to that net; its belts and splitters on its own net
    (``elaborate._bus_nets``)."""
    inst = flat.insts[path]
    item = inst.params.get("item")
    binding, used = {"pool": path}, set()
    for key in inst.machines:
        port = flat.machines[key].extra["port"]
        hold = flat._in_hold if port == "load" else flat._out_hold
        nets = [n for n in hold.get((key, item), ()) if n != path]
        if nets:
            local = key.rsplit("/", 1)[1]
            binding[local] = nets[0]
            used.add(local)
    params = {k: v for k, v in inst.params.items() if k not in ("duty", "dnp")}
    params.update(_rot=int((inst.hints.get("at") or (0, 0, 0))[2]), _used=used,
                  _mouths=sorted(local for local in used if local.startswith("in")))
    return fragment("Bus", params, [], []), binding


def _rot_of(flat, path):
    """An instance's absolute rot: its own and every enclosing module's (0 where unplaced)."""
    parts = path.split("/")
    rot = 0
    for i in range(len(parts), 0, -1):
        hint = flat.insts["/".join(parts[:i])].hints.get("at")
        if hint is not None:
            rot += int(hint[2])
    return rot % 4


def _single(flat, keys, cat, ins, outs):
    m = flat.machines[keys[0]]
    w, h = cat.size(m.prototype)
    local = keys[0].rsplit("/", 1)[1]
    attrs = _machine_attrs(m)
    fields = _module_fields(m, cat)
    if fields:
        attrs["config"] = config_text(fields)
    ents = [FEnt(local, m.prototype, w / 2, h / 2, _facing(m, cat), None, attrs)]
    roles = {}
    for i, (_net, items) in enumerate(ins):
        roles[f"in{i + 1}"] = Role(f"in{i + 1}", "in", items, [local], [(w / 2, h / 2, None)])
    for i, (_net, items) in enumerate(outs):
        name = "out" if i == 0 else f"out{i + 1}"
        roles[name] = Role(name, "out", items, [local], [(w / 2, h / 2, None)])
    return _finish(Fragment(flat.insts[keys[0].rsplit("/", 1)[0]].kind, {}, ents, roles, (w, h)))


def _facing(m, cat):
    """N for a machine on a fluid net (its pipe connections turn with it, so ``rot`` in the hint
    turns it); None, as before, for every other single machine."""
    fluid = any(cat.is_fluid(i) for i in list(m.needs) + list(m.makes))
    if fluid and cat.has_fluid_boxes(m.prototype) and m.prototype not in _NO_DIRECTION:
        return 0
    return None


def _machine_attrs(m):
    attrs = {}
    if m.recipe:
        attrs["recipe"] = m.recipe
    for k in ("resource", "role", "fuel", "fluid"):
        if k in m.extra:
            attrs[k] = m.extra[k]
    if m.drain is not None:
        attrs["rate"] = fkpmod.fmt(m.drain)
    if m.makes and not m.recipe and not m.source and "resource" not in m.extra:
        attrs["rate"] = fkpmod.fmt(next(iter(m.makes.values())))
    if m.source and "item" not in attrs and m.makes and not m.extra.get("buffer"):
        attrs["item"] = next(iter(m.makes))
    if m.takes is not None and m.sink and len(m.takes) == 1:
        attrs["item"] = next(iter(m.takes))
    fields = {}
    if m.extra.get("role") in _REQUESTS and m.makes:
        item = next(iter(m.makes))
        attrs["item"] = item
        fields.update(_request_fields(list(m.makes), m.extra.get("request"), m.makes[item],
                                      m.extra.get("max")))
        if m.extra.get("trash"):
            fields["request_filters"]["trash_not_requested"] = True
    if m.extra.get("limit") is not None:
        fields["bar"] = int(m.extra["limit"])    # the chest's inventory bar: its usable slots
    if fields:
        attrs["config"] = config_text(fields)
    return attrs


#: logistic chests that ask the network for their item: the request has to be in the paste, or
#: the chest asks for nothing (the V1 rehearsal's gear factory got no plates, 2026-10-20)
_REQUESTS = ("requester", "buffer")


def _request_fields(item, request, rate, most=None):
    """The game's ``request_filters`` for one item, or a list of them (``item="a,b"``): each at
    the same count, ``request=`` if given, else a minute of the chest's ``rate`` (at least 10),
    else 100; ``most`` (2.0, ``max=``) is each filter's upper bound, ``max_count``, which the
    count is clamped to (the game refuses a request with count > max_count)."""
    if request is not None:
        count = int(request)
    elif rate is not None and not math.isinf(rate):
        count = max(10, math.ceil(rate * 60))
    else:
        count = 100
    items = item if isinstance(item, list) else [item]
    top = {} if most is None else {"max_count": int(most)}
    if top:
        count = min(count, top["max_count"])
    return {"request_filters": {"sections": [{"index": 1, "filters": [
        dict({"index": k + 1, "name": it, "quality": "normal", "comparator": "=", "count": count},
             **top)
        for k, it in enumerate(items)]}]}}


def _request_config(item, request, rate):
    """``_request_fields`` as the ``config`` attr the codec writes."""
    return config_text(_request_fields(item, request, rate))


def _param_text(v):
    if v is True:
        return "true"
    if isinstance(v, list):
        return "|".join(str(x) for x in v)
    if isinstance(v, float):
        return fkpmod.fmt(v)
    return str(v)


#: 3.0.1 (Rory, 2026-11-08: "everything should be done by origins"): ``@(x, y, rot r)`` puts the
#: instance's own origin, its (0, 0), at (x, y) and turns its contents r quarter turns clockwise
#: about that point. A turned module that grows keeps every piece where it was; before 3.0.1 the
#: turned frame's top-left corner landed on (x, y), so a turned module that grew moved all its
#: contents. A single machine (a Pump, a Chest) can't grow: its (x, y) stays its top-left tile,
#: turned or not, as in the game. False: the 3.0.0 reading (``fk migrate-origins`` reads the old
#: hints with it).
ORIGIN_HINTS = True


def _turn_offset(rot, w, h):
    """What ``compose.turn`` adds to a turn about the origin, for a frame of w x h."""
    return ((0, 0), (h, 0), (w, h), (0, w))[rot % 4]


def _about_origin(x, y, rot):
    """(x, y) turned ``rot`` quarter turns clockwise about (0, 0)."""
    for _ in range(rot % 4):
        x, y = -y, x
    return x, y


def corner_hint(hint, frame):
    """An origin hint (x, y, rot) of an instance whose own frame is ``frame`` (x0, y0, x1, y1, in
    its unturned coordinates) -> the corner hint the layout code places by: the one that puts
    every point p of the instance where turning p about its origin and moving it by (x, y) does."""
    x, y, rot = hint
    rot = int(rot) % 4
    if not rot:
        return hint
    fx, fy = frame[0], frame[1]
    rx, ry = _about_origin(fx, fy, rot)
    sx, sy = _turn_offset(rot, frame[2] - fx, frame[3] - fy)
    return _clean(x + rx - fx - sx), _clean(y + ry - fy - sy), hint[2]


def origin_hint(hint, frame):
    """The inverse of ``corner_hint``: a corner hint -> the origin hint that places the same."""
    x, y, rot = hint
    rot = int(rot) % 4
    if not rot:
        return hint
    fx, fy = frame[0], frame[1]
    rx, ry = _about_origin(fx, fy, rot)
    sx, sy = _turn_offset(rot, frame[2] - fx, frame[3] - fy)
    return _clean(x - rx + fx + sx), _clean(y - ry + fy + sy), hint[2]


def literal_frame(inst):
    """A Literal's frame (x0, y0, x1, y1) in its file's coordinates."""
    _ents, (w, h), (fx, fy) = inst.literal[:3]
    return fx, fy, fx + w, fy + h


def turns_in_place(inst):
    """A single machine: its hint is its top-left tile at every rot (it has no frame to grow)."""
    return inst.module is None and inst.kind != "Literal" and _core(inst) in _SINGLE


def origin_hints(flat, cat, final=True):
    """3.0.1: every turned instance's ``@(x, y, rot r)`` read as its origin (``ORIGIN_HINTS``),
    turned into the corner hint the layout code places by, innermost first (an instance's frame
    is its children's, placed). The hint as written stays in ``hints["origin"]``; an instance
    that has one is done. A turned instance waits while a turned instance inside it waits.
    ``final`` False (elaboration's early pass, before the nets are resolved): an instance whose
    frame isn't known yet waits for the final pass. -> notes, one per turned instance the final
    pass can't place by its origin (its hint is left as written)."""
    notes = []
    if not ORIGIN_HINTS:
        return notes
    frames, waiting = {}, []
    for path in sorted(flat.insts, key=lambda p: (-p.count("/"), p)):
        inst = flat.insts[path]
        hint = (inst.hints or {}).get("at")
        if hint is None or "origin" in inst.hints:
            continue
        if inst.kind == "Literal" and inst.literal is not None:
            # a Literal's origin is its file's (0, 0) (a box cut's too), not its top-left tile:
            # a blueprint that grows west or north keeps every entity where it was
            frame = literal_frame(inst)
            x, y, rot = corner_hint(hint, frame)
            inst.hints = dict(inst.hints, at=(_clean(x + frame[0]), _clean(y + frame[1]), rot),
                              origin=hint)
            continue
        if not int(hint[2]) % 4 or turns_in_place(inst):
            continue
        if any(w.startswith(path + "/") for w in waiting):
            waiting.append(path)
            if final:
                notes.append(f"{path}: @({hint[0]}, {hint[1]}, rot {hint[2]}) is placed by its "
                             "corner, not its origin: a turned piece inside it is")
            continue
        try:
            frame = _own_frame(flat, path, cat, frames)
        except Exception as exc:             # noqa: BLE001 - a frame the layout can't know yet
            frame, why = None, str(exc)
        else:
            why = "its frame isn't known"
        if frame is None:
            waiting.append(path)
            if final:
                notes.append(f"{path}: @({hint[0]}, {hint[1]}, rot {hint[2]}) is placed by its "
                             f"corner, not its origin: {why}")
            continue
        inst.hints = dict(inst.hints, at=corner_hint(hint, frame), origin=hint)
    return notes


def _origin(flat, path, cat=None, size=(0, 0), frames=None):
    """Absolute (x, y, rot) of an instance, None when it has no ``@(x, y)`` hint. ``size`` is
    its own frame (w, h), unturned.

    A module instance ``@(x, y, rot r)`` turns its whole contents r quarter turns clockwise inside
    its frame (``_module_frame``), the way a plot turns a factory: a child's turned frame lands
    where turning the module's frame puts it, then moves by (x, y), and the child's rot adds the
    module's. Under unturned modules only the hints add up, so ``size`` and ``cat`` (for the
    module frames; ``frames`` caches them) matter only under a turned one."""
    hint = flat.insts[path].hints.get("at")
    if hint is None:
        return None
    rot = int(hint[2]) % 4
    w, h = size if rot % 2 == 0 else size[::-1]
    box = (hint[0], hint[1], hint[0] + w, hint[1] + h)
    parts = path.split("/")
    for i in range(len(parts) - 1, 0, -1):            # the innermost module first
        mod = "/".join(parts[:i])
        mx, my, mr = flat.insts[mod].hints.get("at") or (0, 0, 0)
        mr = int(mr) % 4
        if mr:
            box = _turn_box(box, mr, _module_frame(flat, mod, cat, frames))
        box = (box[0] + mx, box[1] + my, box[2] + mx, box[3] + my)
        rot += mr
    return _clean(box[0]), _clean(box[1]), rot % 4


def _turn_box(box, rot, frame):
    """``box`` (x0, y0, x1, y1) inside ``frame`` (the same) turned ``rot`` quarter turns
    clockwise, the turned frame's top-left on the frame's own."""
    fx, fy = frame[0], frame[1]
    size = (frame[2] - fx, frame[3] - fy)
    ax, ay, _d = turn(box[0] - fx, box[1] - fy, None, rot, size)
    bx, by, _d = turn(box[2] - fx, box[3] - fy, None, rot, size)
    return min(ax, bx) + fx, min(ay, by) + fy, max(ax, bx) + fx, max(ay, by) + fy


def _module_frame(flat, path, cat, frames=None):
    """(x0, y0, x1, y1): module instance ``path``'s frame in its own coordinates, the box of its
    origin and every placed child's turned frame, or the frame the module declares with ``size
    WxH`` (2.0.28: a port list needs a frame before anything is placed). A child with no ``@(x, y)``
    hint (unplaced, or
    ``@patch``: placed in the factory frame), a pinned Literal (its entities stay where the
    .fkp has them) and one whose fragment fails (its note says why) are not in it."""
    if frames is not None and path in frames:
        return frames[path]
    if flat.insts[path].size is not None:      # a module that declares its frame (2.0.28: ports)
        w, h = flat.insts[path].size
        if frames is not None:
            frames[path] = (0, 0, w, h)
        return 0, 0, w, h
    x0 = y0 = x1 = y1 = 0
    for child in flat.scopes[path].values():
        hint = child.hints.get("at")
        own = None if hint is None else _own_frame(flat, child.path, cat, frames)
        if own is None:
            continue
        w, h = own[2] - own[0], own[3] - own[1]
        if int(hint[2]) % 2:
            w, h = h, w
        bx, by = hint[0] + own[0], hint[1] + own[1]
        x0, y0, x1, y1 = min(x0, bx), min(y0, by), max(x1, bx + w), max(y1, by + h)
    if frames is not None:
        frames[path] = (x0, y0, x1, y1)
    return x0, y0, x1, y1


def _own_frame(flat, path, cat, frames):
    """An instance's unturned frame (x0, y0, x1, y1) in its own coordinates, or None."""
    inst = flat.insts[path]
    if inst.module is not None:
        return _module_frame(flat, path, cat, frames)
    if inst.kind == "Literal":
        return None if inst.literal is None else (0, 0) + tuple(inst.literal[1])
    try:
        frag, _binding = fragment_of(flat, path, cat)
    except FragmentError:
        return None
    return (0, 0) + tuple(frag.size)


def expand_factory(flat, cat, previous=None, ground=None, log=None):
    """-> (factory Layout, notes). ``previous`` supplies a Literal's pinned entities. ``ground``
    (a ``terrain.FrameGround``: blocked tiles, ore and the plot, in the factory frame) places the
    ``@patch`` rows; ``log`` (a list) gets one line per row placed that way."""
    lay = fkpmod.Layout("factory", flat.name)
    notes = []
    xmax = ymax = 0
    on_patch = []
    frames = {}                                 # module path -> its frame, for turned modules
    for path, inst in flat.insts.items():
        if inst.module is not None:
            # 2.0.36: a sealed module's ports are carriers and attach points, so the router meets it
            # at its frame's tiles; its children are placed on their own below (frame_of knows the
            # module's frame either way).
            if inst.size is None:
                continue
            at = _origin(flat, path, cat, inst.size, frames) or (0, 0, 0)
            try:
                frag, binding = _module_fragment(flat, path, cat)
            except FragmentError as exc:
                notes.append(f"{path}: {exc}")
                continue
            for e in _put(lay, path, inst, frag, binding, at, notes, cat):
                xmax, ymax = _grow(cat, e, xmax, ymax)
            # 2.0.48: a port lays nothing, so the frame itself counts toward the layout's size
            _fx0, _fy0, fx1, fy1 = frame_of(flat, lay, path, cat)
            xmax, ymax = max(xmax, fx1), max(ymax, fy1)
            continue
        if inst.kind == "Literal" and inst.literal is not None:
            # imported from a file: placed by its hint like any fragment, keys from the import
            ents, size, _file_corner, sig = inst.literal
            hint = _origin(flat, path, cat, size, frames) or (0, 0, 0)
            x0, y0, rot = hint
            params = {k: _param_text(v) for k, v in inst.params.items()}
            lay.insts[path] = fkpmod.Inst(path, "Literal", params, x0, y0, rot, sig)
            carry = _port_carriers(flat, path, ents)
            for e in ents:
                x, y, d = turn(float(e.x), float(e.y), e.dir, rot, size)
                attrs = dict(armsmod.turned(e.attrs, rot))
                if attrs.get("net") in carry:
                    attrs["net"] = carry[attrs["net"]]
                placed = fkpmod.Entity(e.key, e.name, _clean(x + x0), _clean(y + y0), d, attrs)
                lay.add(placed)
                xmax, ymax = _grow(cat, placed, xmax, ymax)
            continue
        if inst.kind == "Literal":
            if previous is None or path not in previous.insts:
                notes.append(f"{path}: a Literal needs its entities from the previous .fkp")
                continue
            lay.insts[path] = previous.insts[path]
            for key, e in previous.entities.items():
                if e.owner == path:
                    lay.add(fkpmod.Entity(e.key, e.name, e.x, e.y, e.dir, dict(e.attrs)))
                    xmax, ymax = _grow(cat, e, xmax, ymax)
            continue
        if "at" not in inst.hints and "patch" in inst.hints:
            on_patch.append(path)
            continue
        if "at" not in inst.hints:
            notes.append(f"{path}: unplaced (no @(x, y) hint)")
            continue
        try:
            frag, binding = fragment_of(flat, path, cat)
        except FragmentError as exc:
            notes.append(str(exc) if str(exc).startswith(path) else f"{path}: {exc}")
            continue
        at = _origin(flat, path, cat, frag.size, frames)
        for e in _put(lay, path, inst, frag, binding, at, notes, cat):
            xmax, ymax = _grow(cat, e, xmax, ymax)
    for path in on_patch:
        at, line, frag, binding = _on_patch(flat, lay, path, cat, ground)
        if at is None:
            notes.append(line)
            continue
        if log is not None:
            log.append(line)
        for e in _put(lay, path, flat.insts[path], frag, binding, at, notes, cat):
            xmax, ymax = _grow(cat, e, xmax, ymax)
    # 2.0.41 (PLAN-GLOBAL phase A): every port's tile, chosen facing its partner where neither a
    # pin nor a committed record holds it, and recorded in the .fkp under the port's own key
    _choose_port_tiles(flat, lay, cat)
    lay.ports = _port_records(flat, lay)
    lay.size = (xmax, ymax)
    return lay, notes


def role_net(binding, role):
    """The ``net=`` of a carrier that works ``role``: its net, or for a template's belt of two
    nets (``in1|in2``) both, in1's on the left lane; None when a role has none."""
    if "|" not in role:
        return binding.get(role)
    nets = [binding.get(r) for r in role.split("|")]
    return None if None in nets else fkpmod.pair_net(*nets)


def _put(lay, path, inst, frag, binding, at, notes, cat=None):
    """Write one placed fragment into ``lay`` under its keys; -> the entities added. A carrier
    without lanes (an inserter) on a belt of two nets carries both (``ore+fuel``)."""
    x0, y0, rot = at
    params = {k: _param_text(v) for k, v in inst.params.items()}
    lay.insts[path] = fkpmod.Inst(path, inst.kind, params, x0, y0, rot, frag.sig)
    added = []
    for fe in frag.entities:
        x, y, d = turn(float(fe.x), float(fe.y), fe.dir, rot, frag.size)
        attrs = dict(armsmod.turned(fe.attrs, rot))
        if fe.role is not None:
            net = role_net(binding, fe.role)
            if net is None:
                notes.append(f"{path}/{fe.local}: role {fe.role} has no net (no-feed/no-taker)")
                continue
            if "|" in net and cat is not None and cat.has_entity(fe.name) \
                    and cat.kind(fe.name) not in fkpmod.LANE_TYPES:
                net = fkpmod.hand_net(net)
            attrs["net"] = net
        e = fkpmod.Entity(f"{path}/{fe.local}", fe.name, _clean(x + x0), _clean(y + y0), d, attrs)
        lay.add(e)
        added.append(e)
    return added


def _taken(flat, lay, cat):
    """Tiles a row placed now must keep clear of: every entity, every placed instance's frame
    and the tiles its routes start or end on."""
    taken = set()
    for e in lay.entities.values():
        taken |= fkpmod.tiles_of(cat, e)
    for path, rec in lay.insts.items():
        if rec.kind == "Literal" or path not in flat.insts:
            continue
        x0, y0, x1, y1 = frame_of(flat, lay, path, cat)
        taken |= {(x, y) for x in range(x0, x1) for y in range(y0, y1)}
        for _role, side, _net, (x, y, d) in attach_points(flat, lay, path, cat):
            if d is None:
                continue
            k, sign = _H[int(d)], (1 if side == "out" else -1)
            taken.add((math.floor(x) + sign * _DX[k], math.floor(y) + sign * _DY[k]))
    return taken


def _on_patch(flat, lay, path, cat, ground):
    """(at, report line, fragment, binding) for a ``@patch`` row; at is None when it can't be
    placed, and the line says why (and how many drills would fit: n is never changed)."""
    inst = flat.insts[path]
    res = inst.hints["patch"]
    if _core(inst) != "MinerRow":
        return None, f"{path}: @patch places MinerRow rows only in V1; give it an @(x, y) hint", \
            None, None
    mines = _params(inst).get("resource")
    if mines != res:
        return None, f"{path}: @patch({res}), but the row mines {mines}", None, None
    if ground is None:
        return None, (f"{path}: @patch({res}) needs the terrain (read_terrain <name>) and the "
                      "factory's plot"), None, None
    try:
        frag, binding = fragment_of(flat, path, cat)
    except FragmentError as exc:
        return None, f"{path}: {exc}", None, None
    n = int(_params(inst)["n"])
    ore = ground.ore.get(res, {})
    where = "the plot" + (f" (patch {ground.patch})" if ground.patch else "")
    if not ore:
        return None, f"{path}: @patch({res}): no {res} inside {where}", None, None
    taken = _taken(flat, lay, cat)
    spot = placer.place_on_patch(frag, cat, ore, ground.blocked, taken, ground.box)
    xs, ys = [t[0] for t in ore], [t[1] for t in ore]
    w, h = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
    span = f"the {res} in {where} spans {w}x{h}, long axis {'E-W' if w >= h else 'N-S'}"
    if spot is None:
        outs = [b[1] for b in belts(flat, inst.machines[0], "out")]

        def make(k):
            return fragment("MinerRow", dict(frag.params, n=k), [], outs)

        k, alt = placer.largest_fit(make, n, cat, ore, ground.blocked, taken, ground.box)
        line = (f"{path}: @patch({res}): no spot in {where} puts all {n} drills on {res} with "
                f"no row entity on blocked or taken ground ({span}); "
                + (f"n={k} would fit, at {alt.x},{alt.y} rot {alt.rot}; n stays {n}" if k
                   else "not even one drill fits"))
        return None, line, None, None
    line = (f"{path}: @patch({res}) at {spot.x},{spot.y} rot {spot.rot}, the row runs "
            f"{'E-W' if spot.rot % 2 == 0 else 'N-S'} ({span}): {spot.drills}/{n} drills on "
            f"{res}, {spot.under} ore tiles under them ({fmt_amount(spot.under_amount)}), "
            f"{spot.reach} in their mining areas ({fmt_amount(spot.reach_amount)})")
    return (spot.x, spot.y, spot.rot), line, frag, binding


def _grow(cat, e, xmax, ymax):
    for tx, ty in fkpmod.tiles_of(cat, e):
        xmax, ymax = max(xmax, tx + 1), max(ymax, ty + 1)
    return xmax, ymax


def _clean(v):
    return int(v) if float(v).is_integer() else v


def attach_points(flat, lay, path, cat):
    """[(role, side, net, (x, y, dir))] of a placed instance, in the factory frame."""
    rec = lay.insts[path]
    frag, binding = fragment_of(flat, path, cat)
    out = []
    for name, role in frag.roles.items():
        net = binding.get(name)
        for ax, ay, ad in role.attach:
            x, y, d = turn(float(ax), float(ay), ad, rec.rot, frag.size)
            out.append((name, role.side, net, (_clean(x + rec.x), _clean(y + rec.y), d)))
    return out


def planned_frame(flat, path, cat, frames=None):
    """(x0, y0, x1, y1): where instance ``path``'s turned frame lands in the factory frame by its
    ``@(x, y)`` hint, before anything is placed (the size step's does-not-fit); None without a hint (an
    ``@patch`` row, an unplaced one), for a module or a pinned Literal, and when its fragment
    fails. ``frames`` caches the module frames, as for ``_origin``."""
    inst = flat.insts[path]
    if inst.module is not None or "at" not in inst.hints:
        return None
    own = _own_frame(flat, path, cat, frames)
    if own is None:
        return None
    size = (own[2] - own[0], own[3] - own[1])
    x0, y0, rot = _origin(flat, path, cat, size, frames)
    w, h = size if rot % 2 == 0 else size[::-1]
    return x0, y0, x0 + w, y0 + h


def frame_of(flat, lay, path, cat):
    """(x0, y0, x1, y1): the tiles of a placed instance's turned frame."""
    rec = lay.insts[path]
    frag, _binding = fragment_of(flat, path, cat)
    w, h = frag.size if rec.rot % 2 == 0 else frag.size[::-1]
    return (int(rec.x), int(rec.y), int(rec.x) + w, int(rec.y) + h)


def frame_overlaps(flat, lay, cat):
    """[(a, b, (x0, y0, x1, y1))]: every two placed sealed modules whose frames overlap, neither
    inside the other, with the box they share (2.0.75: a warning, never a stop; the router lets
    either module's nets use the shared tiles, each in the way of the other's)."""
    boxes = {}
    for path in sorted(lay.insts, key=fkpmod.natural_key):
        if path in flat.insts and path in flat.sealed:
            try:
                boxes[path] = frame_of(flat, lay, path, cat)
            except FragmentError:
                continue
    names, out = list(boxes), []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if b.startswith(a + "/") or a.startswith(b + "/"):
                continue
            p, q = boxes[a], boxes[b]
            x0, y0, x1, y1 = max(p[0], q[0]), max(p[1], q[1]), min(p[2], q[2]), min(p[3], q[3])
            if x0 < x1 and y0 < y1:
                out.append((a, b, (x0, y0, x1, y1)))
    return out


def _span(keys):
    """'red/m0..m4' style summary of a key list."""
    if not keys:
        return "-"
    groups = {}
    for k in keys:
        owner, local = k.rsplit("/", 1)
        m = re.fullmatch(r"([a-z_]+\d*[a-z]+|[a-z]+)(\d+)", local)
        stem = m.group(1) if m else local
        groups.setdefault((owner, stem), []).append(local)
    out = []
    for (owner, stem), locs in groups.items():
        out.append(f"{owner}/{locs[0]}" + (f"..{locs[-1]}" if len(locs) > 1 else ""))
    return ", ".join(out)


def explain(flat, lay, path, cat) -> str:
    inst = flat.insts[path]
    rec = lay.insts.get(path)
    params = ", ".join(f"{k}={_param_text(v)}" for k, v in inst.params.items())
    head = f"{path} = {inst.kind}<{params}>"
    if rec is not None:
        head += f" at {fkpmod.fmt(rec.x)},{fkpmod.fmt(rec.y)} rot {rec.rot} sig {rec.sig}"
    if getattr(inst, "auto", None):
        head += f"   (n=auto {inst.auto[0]}..{inst.auto[1]}: the size step's count)"
    lines = [head]
    mine = sorted((k for k, e in lay.entities.items() if e.owner == path), key=fkpmod.natural_key)
    machines = [k for k in mine if lay.entities[k].net is None]
    protos = sorted({lay.entities[k].name for k in machines})
    recipes = sorted({lay.entities[k].attrs.get("recipe", "") for k in machines} - {""})
    lines.append(f"  machines  {_span(machines)}  ({len(machines)} x {', '.join(protos)}"
                 + (f", recipe {', '.join(recipes)}" if recipes else "") + ")")
    if inst.kind == "Literal" or rec is None:
        nets = sorted({lay.entities[k].net for k in mine if lay.entities[k].net})
        lines.append(f"  nets      {', '.join(nets) or '-'}")
        for name, key in sorted(getattr(flat, "literal_names", {}).get(path, {}).items()):
            lines.append(f"  name      {path}.{name} = {key}")
        return "\n".join(lines + _limited_lines(flat, inst))
    frag, binding = fragment_of(flat, path, cat)
    if inst.kind == "Bus":
        return "\n".join(lines[:1] + _explain_bus(flat, lay, path, frag, binding))
    for name, role in frag.roles.items():
        net = binding.get(name, "?")
        carriers = [k for k in mine if lay.entities[k].net == net
                    and k.rsplit("/", 1)[1] in {e.local for e in frag.entities if e.role == name}]
        (ax, ay, ad), = role.attach[:1] or [(0, 0, None)]
        x, y, d = turn(ax, ay, ad, rec.rot, frag.size)
        where = f"{fkpmod.fmt(x + rec.x)},{fkpmod.fmt(y + rec.y)}"
        if d is not None:
            where += " " + fkpmod.DIRS[d]
        lines.append(f"  {role.side:<4} {name:<4} net {net:<14} items {', '.join(role.items):<30} "
                     f"attach {where:<11} carriers {_span(carriers)}")
    lines.append(f"  size      {frag.size[0]}x{frag.size[1]} (its own frame)")
    return "\n".join(lines + _limited_lines(flat, inst))


def _limited_lines(flat, inst):
    """2.1: what limits each ``limited`` net the instance is an end of, as its source says it."""
    keys = set(inst.machines)
    out = []
    for name in sorted(flat.nets):
        net = flat.nets[name]
        if not net.limited or not any(k in keys for k, _i in net.drivers + net.consumers):
            continue
        out.append(f"  limited   net {name}: " + (net.why if getattr(net, "why", None) else
                                                   "(no reason given: limited \"...\" says what)"))
    return out


def explain_net(flat, name) -> str:
    """``fk explain FILE NET`` (2.1): one net, its items and ends, and what limits it."""
    net = flat.nets[name]
    lines = [f"net {name}: {', '.join(net.items) or 'no items'}"
             + (" (declared)" if net.declared else " (from its drivers)")
             + (" sushi" if net.sushi else "")]
    for side, rows in (("drivers", net.drivers), ("consumers", net.consumers)):
        insts = list(dict.fromkeys(flat.machines[k].inst for k, _i in rows))
        lines.append(f"  {side:<9} {', '.join(insts) or '-'}")
    if name in getattr(flat, "lane_of", {}):
        lines.append(f"  lanes     {flat.lane_of[name]}")
    bufs = sorted({b for k, _i in net.drivers + net.consumers
                   for b in [_buffer_end(flat, k)] if b})
    if bufs:
        lines.append(f"  buffers   {', '.join(bufs)} (an unlimited supply downstream, an unlimited "
                     "sink upstream: fk rates gives each one's BUFFER line)")
    if net.limited:
        lines.append("  limited   " + (net.why if getattr(net, "why", None) else
                                       "(no reason given: limited \"...\" says what)"))
    return "\n".join(lines)


def _buffer_end(flat, key):
    m = flat.machines[key]
    b = m.extra.get("buffer")
    if b is True:
        return m.inst
    return b if isinstance(b, str) else None


def _explain_bus(flat, lay, path, frag, binding):
    """A Bus's lines for ``explain``: its own net, then each port: its side and tile as written,
    its net, where the router meets it (the stub's tile and the way items cross it) and its
    staircase."""
    inst, rec = flat.insts[path], lay.insts[path]
    item = inst.params.get("item")
    mine = [k for k, e in lay.entities.items() if e.owner == path]
    belts = sum(1 for k in mine if lay.entities[k].name.endswith("transport-belt")
                and k.rsplit("/", 1)[1].startswith("b"))
    splitters = sum(1 for k in mine if lay.entities[k].name.endswith("splitter"))
    w, h = frag.size
    runs = ("east", "south", "west", "north")[_rot_of(flat, path)]
    out = [f"  bus       {item} on {h - 2} belts, {w} tiles, running {runs}: net {path} "
           f"(its own: {belts} belts, {splitters} splitters)"]
    for key in inst.machines:
        m = flat.machines[key]
        local = key.rsplit("/", 1)[1]
        side = "in" if m.extra["port"] == "load" else "out"
        stair = [k for k in mine if k.rsplit("/", 1)[1].startswith(local + "s")]
        role = frag.roles.get(local)
        if role is None:
            out.append(f"  {side:<4} {local:<5} {m.extra['at']:<4} no net names it: its staircase "
                       f"{_span(sorted(stair, key=fkpmod.natural_key))} only")
            continue
        (ax, ay, ad), = role.attach
        x, y, d = turn(ax, ay, ad, rec.rot, frag.size)
        where = f"{fkpmod.fmt(x + rec.x)},{fkpmod.fmt(y + rec.y)} {fkpmod.DIRS[d]}"
        out.append(f"  {side:<4} {local:<5} {m.extra['at']:<4} net {binding[local]:<14} attach "
                   f"{where:<11} carriers {path}/{local}, staircase "
                   f"{_span(sorted(stair, key=fkpmod.natural_key))}")
    out.append(f"  size      {w}x{h} (its own frame: the belts are its rows 1..{h - 2}, the ports "
               "come in and go out at rows 0 and " + str(h - 1) + ")")
    return out


def unfold(fk_text, flat, lay, path):
    """-> (new .fk text, new layout): ``path`` becomes a Literal; every .fkp line stays."""
    inst = flat.insts[path]
    if inst.kind == "Bus":
        raise ValueError(f"{path} is a Bus: its ports are what nets name, and a Literal has none")
    if "/" in path:
        raise ValueError(f"{path} is inside a sub-module; unfold the module's instance instead")
    lines = fk_text.split("\n")
    i = inst.line - 1
    m = re.match(rf"^(\s*{re.escape(path)}\s*=\s*){re.escape(inst.kind)}(<[^>]*>)?(.*)$", lines[i])
    if not m:
        raise ValueError(f"line {inst.line} is not the instance line of {path}")
    was = inst.kind + (m.group(2) or "")
    duty = inst.params.get("duty")
    kept = f"<duty={_param_text(duty)}>" if duty is not None else ""
    rest = m.group(3)
    lines[i] = f"{m.group(1)}Literal{kept}{rest}   # unfolded from {was}: pinned in the .fkp"
    new = lay.copy()
    rec = lay.insts[path]
    params = {"from": inst.kind}
    params.update(rec.params)
    new.insts[path] = fkpmod.Inst(rec.path, "Literal", params, rec.x, rec.y, rec.rot, rec.sig)
    return "\n".join(lines), new
