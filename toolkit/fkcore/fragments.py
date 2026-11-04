"""fkcore.fragments: the geometry of a template instance, as a Fragment (CORE-V1.md §5).

    Fragment = {kind, params, entities: [FEnt], roles: {name: Role}, size: (w, h), sig}

Every template is authored in a local frame: the row runs east, the top-left tile is (0, 0).
Each input or output belt is a **role**: the belt tiles and the inserters that work it. At
expansion a role is bound to the one net its endpoints sit on, so every carrier gets ``net=``; the
router later lays only the carriers between a role's attach point and the rest of its net.

Geometry is v4's proven stamps (C:\\fk-lead\\repo\\fk\\stamps\\assembler_row.py, miner_row.py,
feed_rows.py lab_row, d6018d3) with every pole removed (power is a coverage step) and fast
inserters throughout. Rows are counterflow: the product belt runs back west, so every attach point
of a row is at its head (x = 0). Input belts carry one net each (a net may be sushi). AsmRow takes
up to three: two north of the machines as in v4, and a third south of the product belt, reached
by long-handed inserters across it (Rory, 2026-10-19; for the inserter, engine and blue recipes).
SmelterRow and LabRow take up to two; more raises FragmentError rather than inventing a layout.
SmelterRow is new geometry in AsmRow's pattern: ore by fast inserter from the near belt, fuel by
long-handed inserter from the far one, at the furnaces' pitch 2.

Machine entities carry what a Literal needs to rebuild them (recipe, resource, fuel, role, rate),
so ``unfold`` can turn any row into a Literal with the same keys and the same netlist.

``SmelterArray`` (2.0) is Rory's smelter stack (games/v2-bench/smelter-stack-ghosts.json, his ghosts
on nauvis, 2026-10-20) at any count: each column of furnaces is fed from one belt with ore on one
lane and fuel on the other, side-loaded at its foot. A role written ``in1|in2`` is such a belt: its
carriers carry both roles' nets, in1's on the left lane (``fkcore.expand.role_net``).

The ``Bus`` (Rory's 4-belt bus, 2026-10-21) is no row: its belts and splitters are the bus's own
net (role ``pool``), and each load and tap is a port, one belt beside the bus (its stub) whose
role is the net a ``.fk`` net binds to it, with one attach point where the router meets it. Its
staircases are Rory's exactly: at his parameters the template is his demo's 210 bus entities, the
splitters' priorities in ``config`` (the codec writes them into the paste). Pure, stdlib only.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

__all__ = ["FragmentError", "FEnt", "Role", "Fragment", "fragment", "FRAGMENTS", "bus",
           "bus_ports", "bus_check", "bus_side", "smelter_array", "array_shape"]

E, S, W, N = 4, 8, 12, 0
_INSERTER = "fast-inserter"
_LONG = "long-handed-inserter"


class FragmentError(ValueError):
    pass


@dataclass
class FEnt:
    local: str
    name: str
    x: float
    y: float
    dir: int | None
    role: str | None = None              # the role a carrier works; None for a machine
    attrs: dict = field(default_factory=dict)


@dataclass
class Role:
    name: str
    side: str                            # "in" | "out"
    items: list
    machines: list                       # machine locals it serves
    attach: list                         # [(x, y, dir)] where the route meets it


@dataclass
class Fragment:
    kind: str
    params: dict
    entities: list
    roles: dict
    size: tuple
    sig: str = ""


def _belt(prefix, role, x0, y, n, d=E):
    return [FEnt(f"{prefix}{j}", "transport-belt", x0 + j + 0.5, y + 0.5, d, role)
            for j in range(n)]


def _finish(frag: Fragment) -> Fragment:
    rows = sorted((e.local, e.name, float(e.x), float(e.y), e.dir, e.role) for e in frag.entities)
    roles = sorted((r.name, r.side, sorted(r.machines), r.attach) for r in frag.roles.values())
    blob = json.dumps([frag.kind, rows, roles, list(frag.size)], separators=(",", ":"))
    frag.sig = hashlib.sha1(blob.encode()).hexdigest()[:10]
    return frag


def _inputs(inputs, kind, most=2):
    if not 1 <= len(inputs) <= most:
        raise FragmentError(f"{kind} takes 1 to {most} input belts in V1, got {len(inputs)}: "
                            + "; ".join(", ".join(b) for b in inputs)
                            + " (put two items on one sushi net, or split the row)")
    return inputs


def asm_row(n, recipe, inputs, outputs, machine="assembling-machine-1", counter=True):
    """Rows north to south: in2, in1, feeders, machines (3), output inserters + in3 feeders,
    product belt, in3. Without in2 everything shifts up one; in3 needs in2."""
    inputs = _inputs(inputs, "AsmRow", most=3)
    two, three = len(inputs) >= 2, len(inputs) == 3
    shift = 0 if two else -1
    in2_y, in1_y, feed_y = 0, 1 + shift, 2 + shift
    mach_y, out_ins_y, out_y = 4 + shift, 6 + shift, 7 + shift
    ents = _belt("in1b", "in1", 0, in1_y, 3 * n - 1)
    ents.append(FEnt(f"in1b{3 * n - 1}", "transport-belt", 3 * n - 0.5, in1_y + 0.5, S, "in1"))
    if two:
        ents += _belt("in2b", "in2", 0, in2_y, 3 * n - 1)
    if counter:
        ents += [FEnt(f"outb{j}", "transport-belt", j + 0.5, out_y + 0.5, W, "out")
                 for j in range(3 * n)]
    else:
        ents += _belt("outb", "out", 0, out_y, 3 * n)
    in3_y = out_y + 1
    if three:
        ents += _belt("in3b", "in3", 0, in3_y, 3 * n - 1)    # the last feeder is at x = 3n - 3
    machines = []
    for k in range(n):
        cx = 3 * k + 0.5
        ents.append(FEnt(f"f{k}", _INSERTER, cx, feed_y + 0.5, N, "in1"))
        if two:
            ents.append(FEnt(f"l{k}", _LONG, cx + 1, feed_y + 0.5, N, "in2"))
        ents.append(FEnt(f"m{k}", machine, cx + 1, mach_y + 0.5, None, None, {"recipe": recipe}))
        ents.append(FEnt(f"o{k}", _INSERTER, cx + 1, out_ins_y + 0.5, N, "out"))
        if three:
            # picks two tiles south (in3, over the product belt), drops two north (the machine)
            ents.append(FEnt(f"s{k}", _LONG, cx, out_ins_y + 0.5, S, "in3"))
        machines.append(f"m{k}")
    roles = {"in1": Role("in1", "in", inputs[0], machines, [(0.5, in1_y + 0.5, E)])}
    if two:
        roles["in2"] = Role("in2", "in", inputs[1], machines, [(0.5, in2_y + 0.5, E)])
    if three:
        roles["in3"] = Role("in3", "in", inputs[2], machines, [(0.5, in3_y + 0.5, E)])
    out_at = (0.5, out_y + 0.5, W) if counter else (3 * n - 0.5, out_y + 0.5, E)
    roles["out"] = Role("out", "out", outputs[0] if outputs else [], machines, [out_at])
    return ents, roles, (3 * n, (in3_y if three else out_y) + 1)


#: furnaces 3x3 wide: a SmelterRow of them is laid as an AsmRow is, at pitch 3, ore in, plates out
_BIG_FURNACES = ("electric-furnace",)


def smelter_row(n, recipe, inputs, outputs, machine="stone-furnace", fuel=None):
    inputs = _inputs(inputs, "SmelterRow")
    if machine in _BIG_FURNACES:          # 2.0.53: no fuel, so one belt in (two for a sushi row)
        return asm_row(n, recipe, inputs, outputs, machine)
    two = len(inputs) == 2
    shift = 0 if two else -1
    in2_y, in1_y, feed_y = 0, 1 + shift, 2 + shift
    out_y = feed_y + 4
    ents = _belt("in1b", "in1", 0, in1_y, 2 * n - 1)
    ents.append(FEnt(f"in1b{2 * n - 1}", "transport-belt", 2 * n - 0.5, in1_y + 0.5, S, "in1"))
    if two:
        ents += _belt("in2b", "in2", 0, in2_y, 2 * n)          # the last long inserter reaches it
    ents += [FEnt(f"outb{j}", "transport-belt", j + 0.5, out_y + 0.5, W, "out")
             for j in range(2 * n)]
    machines = []
    attrs = {"recipe": recipe}
    if fuel:
        attrs["fuel"] = fuel
    for k in range(n):
        ents.append(FEnt(f"f{k}", _INSERTER, 2 * k + 0.5, feed_y + 0.5, N, "in1"))
        if two:
            ents.append(FEnt(f"l{k}", _LONG, 2 * k + 1.5, feed_y + 0.5, N, "in2"))
        ents.append(FEnt(f"m{k}", machine, 2 * k + 1, feed_y + 2, None, None, dict(attrs)))
        ents.append(FEnt(f"o{k}", _INSERTER, 2 * k + 0.5, feed_y + 3.5, N, "out"))
        machines.append(f"m{k}")
    roles = {"in1": Role("in1", "in", inputs[0], machines, [(0.5, in1_y + 0.5, E)])}
    if two:
        roles["in2"] = Role("in2", "in", inputs[1], machines, [(0.5, in2_y + 0.5, E)])
    roles["out"] = Role("out", "out", outputs[0] if outputs else [], machines,
                        [(0.5, out_y + 0.5, W)])
    return ents, roles, (2 * n, out_y + 1)


def miner_row(n, resource, outputs, drill="electric-mining-drill"):
    north, south = (n + 1) // 2, n // 2
    ents, machines = [], []
    for k in range(north):
        ents.append(FEnt(f"m{k}", drill, 3 * k + 1.5, 1.5, S, None, {"resource": resource}))
    for k in range(south):
        ents.append(FEnt(f"m{north + k}", drill, 3 * k + 1.5, 5.5, N, None,
                         {"resource": resource}))
    machines = [e.local for e in ents]
    belt_end = 3 * north - 1
    ents += _belt("outb", "out", 1, 3, belt_end)
    roles = {"out": Role("out", "out", outputs[0] if outputs else [resource], machines,
                         [(belt_end + 0.5, 3.5, E)])}
    return ents, roles, (3 * north, 7 if south else 4)


def burner_miner_row(n, resource, inputs, outputs, drill="burner-mining-drill"):
    """A row of 2x2 burner drills (Rory's kit, 2026-10-21: burner drills mine on the kit's coal
    before there are plates for electric ones). One-sided: the fuel belt north (in1), one fast
    inserter into each drill, the drills facing south onto the product belt (counterflow, west),
    which runs under every drill (facing south, a burner drill drops below its east half).
    ``MinerRow<n, resource, drill=burner-mining-drill, fuel=coal>``; the fuel net binds to in1."""
    inputs = _inputs(inputs, "MinerRow (burner)", most=1)
    ents = _belt("in1b", "in1", 0, 0, 2 * n)
    machines = []
    for k in range(n):
        ents.append(FEnt(f"f{k}", _INSERTER, 2 * k + 0.5, 1.5, N, "in1"))
        ents.append(FEnt(f"m{k}", drill, 2 * k + 1, 3, S, None, {"resource": resource}))
        machines.append(f"m{k}")
    ents += [FEnt(f"outb{j}", "transport-belt", j + 0.5, 4.5, W, "out") for j in range(2 * n)]
    roles = {"in1": Role("in1", "in", inputs[0], machines, [(0.5, 0.5, E)]),
             "out": Role("out", "out", outputs[0] if outputs else [resource], machines,
                         [(0.5, 4.5, W)])}
    return ents, roles, (2 * n, 5)


def lab_row(n, inputs):
    """Labs west to east. in1 north (inserters), in2 south. A third belt (1.1, from the 0534
    player's own patch) runs north of in1: long-handed inserters reach over in1 to it, and
    everything else shifts down one."""
    inputs = _inputs(inputs, "LabRow", most=3)
    three = len(inputs) == 3
    d = 1 if three else 0
    ents = _belt("in1b", "in1", 0, d, 3 * n)
    if three:
        ents += _belt("in3b", "in3", 0, 0, 3 * n)
    machines = []
    for k in range(n):
        ents.append(FEnt(f"m{k}", "lab", 3 * k + 1.5, 3.5 + d, None))
        ents.append(FEnt(f"f{k}", _INSERTER, 3 * k + 0.5, 1.5 + d, N, "in1"))
        if three:
            ents.append(FEnt(f"t{k}", _LONG, 3 * k + 1.5, 1.5 + d, N, "in3"))
        machines.append(f"m{k}")
    roles = {"in1": Role("in1", "in", inputs[0], machines, [(0.5, 0.5 + d, E)])}
    if three:
        roles["in3"] = Role("in3", "in", inputs[2], machines, [(0.5, 0.5, E)])
    h = 5 + d
    if len(inputs) >= 2:
        ents += _belt("in2b", "in2", 0, 6 + d, 3 * n)
        ents += [FEnt(f"g{k}", _INSERTER, 3 * k + 0.5, 5.5 + d, S, "in2") for k in range(n)]
        roles["in2"] = Role("in2", "in", inputs[1], machines, [(0.5, 6.5 + d, E)])
        h = 7 + d
    return ents, roles, (3 * n, h)


# -- the smelter array (Rory's smelter stack, 2026-10-20: games/v2-bench) -------------------------
_PLAIN = "inserter"


def array_shape(n, per, units=None):
    """(units, furnaces per unit west to east) of a SmelterArray of ``n`` furnaces: the fewest
    units that keep each column at most ``per`` furnaces (one yellow lane of ore), unless
    ``units`` is given; the furnaces as even as they go, the extra ones in the western units."""
    k = int(units) if units else max(1, -(-n // (2 * max(1, per))))
    if not 1 <= k <= n:
        raise FragmentError(f"SmelterArray: {k} units for {n} furnaces (a unit has 1 or more)")
    return k, [n // k + (1 if u < n % k else 0) for u in range(k)]


def smelter_array(n, recipe, inputs, outputs, machine="stone-furnace", fuel=None, per=24,
                  units=None):
    """Rory's smelter stack: units 11 tiles wide, west to east, each two columns of 2x2
    furnaces around a plate belt. Local rows, M = 2 x the tallest column:

    - rows 0..M-1, the furnaces, at the foot of their column (furnace b from the foot: rows
      M-2-2b and M-1-2b, its two inserters on its upper row when b is even, its lower row when
      odd: Rory's ghost). x+0 the west feed belt (north), x+1 its inserters, x+2..3 the furnaces,
      x+4 their plate inserters, x+5 the plate belt (south), x+6..10 the same mirrored.
    - row M: x+4 a belt south, x+5 a belt west, x+6 the coal splitter (west, rows M and M+1);
    - row M+1: x+1 and x+2 the coal's pass-through under the west column (an entrance and the
      exit from x+5), x+3..4 the coal splitter south, x+5 an entrance, x+7 the exit from the
      unit to the east (from the entrance at x 11k on the eastmost unit: the coal's port);
    - row M+2: the two feed belts running apart from x+3|x+4, west and east, side-loaded with
      ore from the south and coal from the north: ore on the west belt's left lane and coal on
      its right, coal on the east belt's left and ore on its right (2-to-1 merges, measured);
      they turn north into the columns at x+0 and x+10;
    - row M+3: the ore splitter north (x+3..4) and the plate belt's exit (x+5, from its entrance
      on row M-2: the bottom furnaces' inserters drop onto it);
    - rows M+4, M+5: the ore's belt in (x+3, north) and the plates' out (x+5, south): each
      unit's two ports, side by side, open to the south.

    The westmost unit has no pass-through (its coal splitter's west output stays empty, so it
    sends all on to its own furnaces). Every inserter is a plain one, as in Rory's ghost: a stone
    furnace takes 0.34/s of ore and coal and a plain inserter moves 0.86/s. -> (entities, roles,
    size)"""
    if len(inputs) != 2:
        raise FragmentError("SmelterArray takes its ore and its fuel on two nets that share no "
                            "belt: it side-loads them onto the lanes of its own feed belts (got "
                            + "; ".join(", ".join(b) for b in inputs) + ")")
    k, counts = array_shape(n, per, units)
    tall = max((c + 1) // 2 for c in counts)
    m_ = 2 * tall
    ents, machines = [], []
    ore_at, out_at = [], []
    attrs = {"recipe": recipe}
    if fuel:
        attrs["fuel"] = fuel
    m = 0
    for u, c in enumerate(counts):
        x0 = 11 * u
        cols = ((c + 1) // 2, c // 2)
        for side, f in enumerate(cols):
            fx, feed_x, out_x = (x0 + 3, x0 + 1.5, x0 + 4.5) if side == 0 else \
                (x0 + 8, x0 + 9.5, x0 + 6.5)
            face = W if side == 0 else E
            for b in range(f):
                row = m_ - 2 - 2 * b if b % 2 == 0 else m_ - 1 - 2 * b
                ents.append(FEnt(f"m{m}", machine, fx, m_ - 1 - 2 * b, None, None, dict(attrs)))
                ents.append(FEnt(f"f{m}", _PLAIN, feed_x, row + 0.5, face,
                                 "in1|in2" if side == 0 else "in2|in1"))
                ents.append(FEnt(f"o{m}", _PLAIN, out_x, row + 0.5, face, "out"))
                machines.append(f"m{m}")
                m += 1
            top = m_ - 2 * f
            feed = x0 if side == 0 else x0 + 10
            role = "in1|in2" if side == 0 else "in2|in1"
            ents += [FEnt(f"feed{u}{'we'[side]}{y}", "transport-belt", feed + 0.5, y + 0.5, N,
                          role) for y in range(top, m_ + 3)]
        for y in range(m_ - 2 * max(cols), m_ - 2):          # the plate belt, south
            ents.append(FEnt(f"out{u}b{y}", "transport-belt", x0 + 5.5, y + 0.5, S, "out"))
        ents += [FEnt(f"out{u}u0", "underground-belt", x0 + 5.5, m_ - 1.5, S, "out", {"ug": "in"}),
                 FEnt(f"out{u}u1", "underground-belt", x0 + 5.5, m_ + 3.5, S, "out",
                      {"ug": "out"}),
                 FEnt(f"out{u}x0", "transport-belt", x0 + 5.5, m_ + 4.5, S, "out"),
                 FEnt(f"out{u}x1", "transport-belt", x0 + 5.5, m_ + 5.5, S, "out")]
        out_at.append((x0 + 5.5, m_ + 5.5, S))
        ents += [FEnt(f"ore{u}s", "splitter", x0 + 4, m_ + 3.5, N, "in1"),
                 FEnt(f"ore{u}b0", "transport-belt", x0 + 3.5, m_ + 4.5, N, "in1"),
                 FEnt(f"ore{u}b1", "transport-belt", x0 + 3.5, m_ + 5.5, N, "in1")]
        ore_at.append((x0 + 3.5, m_ + 5.5, N))
        ents += [FEnt(f"feed{u}m{j}", "transport-belt", x0 + 1.5 + j, m_ + 2.5, W, "in1|in2")
                 for j in range(3)]
        ents += [FEnt(f"feed{u}n{j}", "transport-belt", x0 + 4.5 + j, m_ + 2.5, E, "in2|in1")
                 for j in range(6)]
        ents += [FEnt(f"coal{u}s0", "splitter", x0 + 6.5, m_ + 1, W, "in2"),
                 FEnt(f"coal{u}s1", "splitter", x0 + 4, m_ + 1.5, S, "in2"),
                 FEnt(f"coal{u}b0", "transport-belt", x0 + 4.5, m_ + 0.5, S, "in2"),
                 FEnt(f"coal{u}b1", "transport-belt", x0 + 5.5, m_ + 0.5, W, "in2"),
                 FEnt(f"coal{u}u0", "underground-belt", x0 + 7.5, m_ + 1.5, W, "in2",
                      {"ug": "out"})]
        if u > 0:                     # the pass-through on to the unit to the west
            ents += [FEnt(f"coal{u}u1", "underground-belt", x0 + 5.5, m_ + 1.5, W, "in2",
                          {"ug": "in"}),
                     FEnt(f"coal{u}u2", "underground-belt", x0 + 2.5, m_ + 1.5, W, "in2",
                          {"ug": "out"}),
                     FEnt(f"coal{u}u3", "underground-belt", x0 + 1.5, m_ + 1.5, W, "in2",
                          {"ug": "in"})]
    ents.append(FEnt("coalin", "underground-belt", 11 * k + 0.5, m_ + 1.5, W, "in2",
                     {"ug": "in"}))
    roles = {"in1": Role("in1", "in", inputs[0], machines, ore_at),
             "in2": Role("in2", "in", inputs[1], machines, [(11 * k + 0.5, m_ + 1.5, W)]),
             "out": Role("out", "out", outputs[0] if outputs else [], machines, out_at)}
    return ents, roles, (11 * k + 1, m_ + 6)


def circuit_stack(n, inputs, outputs, machine="assembling-machine-1"):
    """Green circuits on both sides of one output belt (2.0.47, Rory 2026-10-26: "use smelter and
    green circuit factories that fill both sides of the output belt"): the smelter stack's shape
    for 3x3 assemblers, one unit 13 tiles wide, two columns of circuit assemblers around the
    output belt. Local rows, M = 12 x the taller column:

    - rows 0..M-1, the columns, cell b from the foot at rows r..r+11 (r = M-12(b+1)): a cable
      assembler (r..r+2), an inserter handing its cable south (r+3), the circuit assembler
      (r+4..r+6), an inserter handing cable north (r+7), a cable assembler (r+8..r+10). x+0 the
      west feed belt (north), x+1 its inserters (copper into the cable assemblers, iron into the
      circuit one), x+2..4 the machines, x+5 the circuit's output inserter, x+6 the output belt
      (south), x+7..12 the same mirrored. Two cable assemblers per circuit: 4 cable/s for its 3.
    - rows M..M+5, the foot: the smelter stack's (``smelter_array``), widened: the iron (in1) comes
      up at x+3 and side-loads both feed belts from the south, the copper (in2) comes in from the
      east at row M+1 and side-loads them from the north, so each feed belt carries iron on one
      lane and copper on the other; the output leaves at x+6.

    Only the circuit assemblers are machines of the netlist (each takes iron and the copper its
    two cable assemblers make into cable); the cable assemblers are entities of the stack. ->
    (entities, roles, size)"""
    if len(inputs) != 2:
        raise FragmentError("CircuitStack takes its iron and its copper on two nets that share no "
                            "belt: it side-loads them onto the lanes of its own feed belts")
    cols = ((n + 1) // 2, n // 2)
    m_ = 12 * cols[0]
    ents, machines, cable = [], [], 0
    m = 0
    for side, c in enumerate(cols):
        mx, feed_x, fin_x, out_x = (2, 0, 1, 5) if side == 0 else (8, 12, 11, 7)
        face = W if side == 0 else E
        lanes = "in1|in2" if side == 0 else "in2|in1"
        for b in range(c):
            r = m_ - 12 * (b + 1)
            for dy in (0, 8):                      # the cable assemblers above and below
                ents.append(FEnt(f"k{cable}", machine, mx + 1.5, r + dy + 1.5, None, None,
                                 {"recipe": "copper-cable"}))
                ents.append(FEnt(f"kf{cable}", _INSERTER, fin_x + 0.5, r + dy + 1.5, face, lanes))
                cable += 1
            ents.append(FEnt(f"m{m}", machine, mx + 1.5, r + 5.5, None, None,
                             {"recipe": "electronic-circuit"}))
            ents.append(FEnt(f"f{m}", _INSERTER, fin_x + 0.5, r + 5.5, face, lanes))
            ents.append(FEnt(f"o{m}", _INSERTER, out_x + 0.5, r + 5.5, face, "out"))
            # the cable hands, on the copper net: the circuit assembler takes its copper as cable
            ents.append(FEnt(f"kn{m}", _INSERTER, mx + 1.5, r + 3.5, N, "in2"))   # from above
            ents.append(FEnt(f"ks{m}", _INSERTER, mx + 1.5, r + 7.5, S, "in2"))   # from below
            machines.append(f"m{m}")
            m += 1
        top = m_ - 12 * c
        ents += [FEnt(f"feed{'we'[side]}{y}", "transport-belt", feed_x + 0.5, y + 0.5, N, lanes)
                 for y in range(top, m_ + 3)]
    for y in range(m_ - 12 * cols[0] + 4, m_ - 2):          # the output belt, south
        ents.append(FEnt(f"outb{y}", "transport-belt", 6.5, y + 0.5, S, "out"))
    ents += [FEnt("outu0", "underground-belt", 6.5, m_ - 1.5, S, "out", {"ug": "in"}),
             FEnt("outu1", "underground-belt", 6.5, m_ + 3.5, S, "out", {"ug": "out"}),
             FEnt("outx0", "transport-belt", 6.5, m_ + 4.5, S, "out"),
             FEnt("outx1", "transport-belt", 6.5, m_ + 5.5, S, "out")]
    # the iron up from the south, split onto both feed belts' south sides
    ents += [FEnt("fes", "splitter", 4, m_ + 3.5, N, "in1"),
             FEnt("feb0", "transport-belt", 3.5, m_ + 4.5, N, "in1"),
             FEnt("feb1", "transport-belt", 3.5, m_ + 5.5, N, "in1")]
    ents += [FEnt(f"feedm{j}", "transport-belt", 1.5 + j, m_ + 2.5, W, "in1|in2")
             for j in range(3)]
    ents += [FEnt(f"feedn{j}", "transport-belt", 4.5 + j, m_ + 2.5, E, "in2|in1")
             for j in range(8)]
    # the copper in from the east, split onto both feed belts' north sides
    ents += [FEnt("cuin", "underground-belt", 13.5, m_ + 1.5, W, "in2", {"ug": "in"}),
             FEnt("cuu0", "underground-belt", 9.5, m_ + 1.5, W, "in2", {"ug": "out"}),
             FEnt("cub0", "transport-belt", 8.5, m_ + 1.5, W, "in2"),
             FEnt("cus0", "splitter", 7.5, m_ + 1, W, "in2"),
             FEnt("cub1", "transport-belt", 6.5, m_ + 0.5, W, "in2"),
             FEnt("cub2", "transport-belt", 5.5, m_ + 0.5, W, "in2"),
             FEnt("cub3", "transport-belt", 4.5, m_ + 0.5, S, "in2"),
             FEnt("cus1", "splitter", 4, m_ + 1.5, S, "in2")]
    roles = {"in1": Role("in1", "in", inputs[0], machines, [(3.5, m_ + 5.5, N)]),
             "in2": Role("in2", "in", inputs[1], machines, [(13.5, m_ + 1.5, W)]),
             "out": Role("out", "out", outputs[0] if outputs else [], machines,
                         [(6.5, m_ + 5.5, S)])}
    return ents, roles, (14, m_ + 6)


# -- the bus (Rory's 4-belt bus, 2026-10-21: agent/docs/examples/bus4.json) ----------------------
#: a port's staircase climbs across the bus from the side its stream comes from to the side it goes
#: to: input priority on the first, output priority toward the second (the game's splitter fields)
_STAIR = {"s": {"input_priority": "right", "output_priority": "left"},     # climbs north
          "n": {"input_priority": "left", "output_priority": "right"}}     # climbs south
_SIDES = "nesw"


def bus_ports(params):
    """(belts, length, item, loads, taps) of ``Bus<belts=4, length, item, load=, tap=>``; a port
    is (side, position): the side a compass letter as written, the position the tile along the bus
    (from its head, x at rot 0) where its belt meets the bus. Raises FragmentError."""
    sizes = []
    for key, v in (("belts", params.get("belts", 4)), ("length", params.get("length"))):
        if isinstance(v, float) and v.is_integer():
            v = int(v)
        if not isinstance(v, int) or isinstance(v, bool) or v < 1:
            raise FragmentError(f"needs {key}= a positive whole number, got {v!r}")
        sizes.append(v)
    belts, length = sizes
    item = params.get("item")
    if not isinstance(item, str):
        raise FragmentError(f"needs item= one item, got {item!r}")
    out = []
    for kind in ("load", "tap"):
        raw = params.get(kind, [])
        ports = []
        for word in raw if isinstance(raw, list) else [raw]:
            text = str(word)
            if not isinstance(word, str) or len(text) < 2 or text[0] not in _SIDES \
                    or not text[1:].isdigit():
                raise FragmentError(f"{kind}={text}: a port is a side (n, e, s or w) and its tile "
                                    "along the bus, e.g. s11")
            ports.append((text[0], int(text[1:])))
        out.append(ports)
    loads, taps = out
    if len({_SIDES.index(s) % 2 for s, _x in loads + taps}) > 1:
        raise FragmentError("a bus has two sides: n and s where it runs east or west, e and w "
                            "where it runs south or north")
    return belts, length, item, loads, taps


def _port_tiles(belts, kind, side, x):
    """(the staircase's rows per splitter, its tiles, the stub's tile, the tile it keeps empty) of
    a port in the bus's own frame (it runs east; side n is row 0, s is row belts + 1, the belts are
    rows 1..belts). A load keeps one tile empty: its first splitter's dead end, so all it takes
    goes up the staircase."""
    b = belts
    if kind == "load":
        rows = [(b - j, b + 1 - j) if side == "s" else (j, j + 1) for j in range(b)]
        first, edge = x + 1, (b + 1 if side == "s" else 0)
        stub, empty = (x, edge), (x + 2, edge)
    else:
        rows = [(b - 1 - j, b - j) if side == "n" else (1 + j, 2 + j) for j in range(b)]
        first = x - b
        stub, empty = (x, 0 if side == "n" else b + 1), None
    cells = [(first + j, r) for j, pair in enumerate(rows) for r in pair]
    return rows, cells, stub, empty


def bus_check(belts, length, loads, taps):
    """Every port's staircase inside the bus and clear of every other port's. Sides as written,
    n or e read as the bus's own north: turned, everything mirrors alike, so the rot can't make
    two ports collide or part. Raises FragmentError."""
    owner = {}
    for kind, ports in (("load", loads), ("tap", taps)):
        for side, x in ports:
            local = "n" if side in "ne" else "s"
            _rows, cells, stub, empty = _port_tiles(belts, kind, local, x)
            name = f"{kind} {side}{x}"
            mine = cells + [stub] + ([empty] if empty else [])
            span = sorted({t[0] for t in mine})
            if span[0] < 0 or span[-1] >= length:
                raise FragmentError(f"{name}: its staircase of {belts} splitter"
                                    f"{'s' if belts > 1 else ''} takes the tiles {span[0]}.."
                                    f"{span[-1]} along the bus, which runs 0..{length - 1}")
            for t in mine:
                if owner.get(t, name) != name:
                    raise FragmentError(f"{owner[t]} and {name} overlap: {name} takes the tiles "
                                        f"{span[0]}..{span[-1]} along the bus (its belt and its "
                                        "staircase), and no two ports share a tile")
                owner[t] = name


def bus(belts, length, item, loads, taps, used=None, mouths=None, tapkinds=None):
    """Rory's bus (agent/docs/examples/bus4.json), in its own frame: ``belts`` belts of ``item``
    run east (rows 1..belts) for ``length`` tiles; row 0 (north) and row belts + 1 (south) are
    where its loads come in and its taps go out. ``loads``/``taps``: [(side, x)], side n or s in
    this frame. ``mouths`` (2.0.33): names ``in1..inN``, one belt end at the head per name, fed in
    line from outside the frame (no staircase, no entity of its own: the bus's belt is the mouth).

    A load at x: its belt (the stub, beside the bus at x) turns east into a staircase of one
    splitter per belt at x + 1 .. x + belts, climbing from its side to the far one with input
    priority on the feeder's side and output priority toward the far side, so it fills the far
    belt first, then the next. A tap at x: a staircase at x - belts .. x - 1 climbing toward its
    side, its last splitter putting out onto the stub at x first, so the tap takes from the far
    belt first. Each used port is a role with one attach point beside the bus, where the router
    meets its stub; ``used`` (port names, None for all) leaves the other stubs out and keeps their
    staircases. The belts and splitters are role ``pool``, the bus's own net.
    -> (entities, roles, size)."""
    bus_check(belts, length, loads, taps)
    b = belts
    ents, taken = [], set()
    roles = {"pool": Role("pool", "pool", [item], [], [])}
    tapkinds = list(tapkinds or ())
    for kind, ports in (("load", loads), ("tap", taps)):
        for k, (side, x) in enumerate(ports, 1):
            name = f"{kind}{k}"
            tkind = tapkinds[k - 1] if kind == "tap" and len(tapkinds) >= k else "dual"
            rows, cells, stub, _empty = _port_tiles(b, kind, side, x)
            climb = side if kind == "load" else ("s" if side == "n" else "n")
            for j, (r0, _r1) in enumerate(rows):
                ents.append(FEnt(f"{name}s{j}", "splitter", cells[2 * j][0] + 0.5, r0 + 1, E,
                                 "pool", {"config": _config_text(_STAIR[climb])}))
            taken |= set(cells)
            if used is not None and name not in used:
                continue
            sx, sy = stub
            d = E if kind == "load" else (N if side == "n" else S)
            into = (N if side == "s" else S) if kind == "load" else d
            ax, ay = sx + 0.5, sy + 0.5
            if kind == "tap" and tkind == "left":      # 2.0.34: a lone UG exit cuts a lane
                ents.append(FEnt(name, "underground-belt", ax, ay, d, name, {"ug": "out"}))
            elif kind == "tap" and tkind == "right":   # 2.0.35: an entrance + its exit, one row out
                ents.append(FEnt(name, "underground-belt", ax, ay, d, name, {"ug": "in"}))
                ey = sy - 1 if side == "n" else sy + 1
                ents.append(FEnt(name + "x", "underground-belt", ax, ey + 0.5, d, name,
                                 {"ug": "out"}))
                ay = ey + 0.5                          # the route meets the exit's tile
            else:
                ents.append(FEnt(name, "transport-belt", ax, ay, d, name))
            roles[name] = Role(name, "in" if kind == "load" else "out", [item], [name],
                               [(ax, ay, into)])
    for name in (mouths or ()):                 # 2.0.33: the belt end at the head, in line
        k = int(name[2:])
        roles[name] = Role(name, "in", [item], [name], [(0.5, k + 0.5, E)])
    for c in range(length):
        for r in range(1, b + 1):
            if (c, r) not in taken:
                ents.append(FEnt(f"b{r}_{c}", "transport-belt", c + 0.5, r + 0.5, E, "pool"))
    # a right tap's exit sits one row outside the bus's edge rows: grow the frame to hold it,
    # shifting the whole bus down for a north-side tap (2.0.35). Existing buses have none, so
    # their frames and entities are untouched.
    right_n = any(k == "right" and s == "n" for (s, _x), k in zip(taps, tapkinds))
    right_s = any(k == "right" and s == "s" for (s, _x), k in zip(taps, tapkinds))
    shift = 1 if right_n else 0
    extra = (1 if (right_n or right_s) else 0) + (1 if (right_n and right_s) else 0)
    if shift:
        for e in ents:
            e.y += shift
        for role in roles.values():
            role.attach = [(ax, ay + shift, ad) for ax, ay, ad in role.attach]
    return ents, roles, (length, b + 2 + extra)


def _config_text(fields):
    """Game fields as the ``config`` attr the codec writes (``codec.config_text``'s form)."""
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


_RUNS = ("east", "south", "west", "north")


def bus_side(side, rot):
    """A port's side as written (a compass letter, in the frame the bus is placed in) -> n or s
    in the bus's own frame, for a bus turned ``rot`` quarter turns (rot 0 runs east). Raises
    FragmentError for a side along the bus."""
    rot %= 4
    local = (_SIDES.index(side) - rot) % 4
    if local % 2:
        raise FragmentError(f"the bus runs {_RUNS[rot]} (rot {rot}): its ports go on its "
                            f"{_SIDES[rot]} and {_SIDES[(rot + 2) % 4]} sides, not {side}")
    return "n" if local == 0 else "s"


def _bus(params):
    """``fragment("Bus", ...)``: the sides turned by ``_rot`` (the instance's own), the stubs of
    the ports in ``_used`` only (None: all)."""
    belts, length, item, loads, taps = bus_ports(params)
    rot = int(params.get("_rot", 0))
    loads = [(bus_side(s, rot), x) for s, x in loads]
    taps = [(bus_side(s, rot), x) for s, x in taps]
    return bus(belts, length, item, loads, taps, params.get("_used"), params.get("_mouths"),
               params.get("tapkind"))


#: kind -> how to call the geometry from (params, inputs, outputs)
FRAGMENTS = {
    "AsmRow": lambda p, i, o: asm_row(int(p["n"]), p["recipe"], i, o,
                                      p.get("machine", "assembling-machine-1")),
    "SmelterRow": lambda p, i, o: smelter_row(int(p["n"]), p["_recipe"], i, o,
                                              p.get("machine", "stone-furnace"), p.get("_fuel")),
    "MinerRow": lambda p, i, o: (
        burner_miner_row(int(p["n"]), p["resource"], i, o, p["drill"])
        if str(p.get("drill", "")).startswith("burner") else
        miner_row(int(p["n"]), p["resource"], o, p.get("drill", "electric-mining-drill"))),
    "LabRow": lambda p, i, o: lab_row(int(p["n"]), i),
    "SmelterArray": lambda p, i, o: smelter_array(
        int(p["n"]), p["_recipe"], i, o, p.get("machine", "stone-furnace"), p.get("_fuel"),
        int(p.get("_per", 24)), p.get("units")),
    "Bus": lambda p, i, o: _bus(p),
    "CircuitStack": lambda p, i, o: circuit_stack(int(p["n"]), i, o,
                                                  p.get("machine", "assembling-machine-1")),
}


def fragment(kind, params, inputs, outputs) -> Fragment:
    """The Fragment of one instance. ``inputs``/``outputs``: the items of each belt, in order."""
    try:
        fn = FRAGMENTS[kind]
    except KeyError:
        raise FragmentError(f"{kind} has no fragment geometry yet") from None
    ents, roles, size = fn(params, [list(b) for b in inputs], [list(b) for b in outputs])
    # the common ``inserter=`` parameter (templates.COMMON_PARAMS): the row's inserters, in place
    # of the fast ones (1.1, from the 0534 player's own patch: the start has 48 plain inserters,
    # 20 fast ones and the fast recipe locked)
    if params.get("inserter"):
        default = _PLAIN if kind == "SmelterArray" else _INSERTER   # Rory's stack: plain ones
        for e in ents:
            if e.name == default:
                e.name = str(params["inserter"])
    return _finish(Fragment(kind, dict(params), ents, roles, size))
