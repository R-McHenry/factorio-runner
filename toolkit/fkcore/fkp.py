"""fkcore.fkp: the layout record (.fkp v1) for a factory or a surface. Format: CORE.md "`.fkp` v1".

    fkp 1 factory <name> | fkp 1 surface <name>        the first line
    size <W>x<H>                                        a factory's frame: tiles 0..W-1, 0..H-1
    inst <path> <Kind> [k=v ...] at <x>,<y> rot <r> sig <sig>
    cover <name> <kind> [k=v ...]                       a coverage object (power, robots)
    plot <factory> at <x>,<y> rot <r> size <W>x<H> [k=v ...]
    port <inst>.<name> at <x>,<y> dir <DIR> lane <L|R|LR> items <A>[,<B>][,..][|<C>...]
    claim <name> box <x0>,<y0> <x1>,<y1> [until <tick>] ["reason"]
    mark <actor@tick> tiles <x>,<y> ... [until <tick>]
    wire <name> red|green                               a wire net (2.0, circuits)
    w <net> red|green <key>[.in|.out] <key>[.in|.out]  one circuit wire between two entities
    e <key> <prototype> <x>,<y> [<DIR>] [k=v ...]       one entity; DIR is N, NNE, NE ... NNW

A key is ``<owner>/<local>``; the owner is an inst path, ``~<net>`` or a cover name. The
canonical form (what :func:`dumps` writes) is the header, ``size``, then the records by kind and
name, then the ``e`` lines in natural key order; comments are dropped, because the .fkp is
tool-written. ``loads(dumps(x)) == x`` and ``dumps(loads(t)) == t`` for canonical ``t``.
A ``k=v`` value with whitespace, a quote, a backslash or ``#`` in it (a Literal's ``config=`` JSON)
is written in shell-style single quotes, and a line is read the shell way, so a quoted ``#`` is
data, not a comment.

Circuits (2.0, drafts\\reactor-1.1\\CIRCUITS-1.1.md §3): a ``wire`` line is a wire net, a ``w``
line one physical wire of it between two entities of the layout (``.in`` / ``.out`` name a
combinator's input or output connectors; without, a red wire takes connector 1 and a green one
2). The entity
lines carry the settings (``if=``, ``logistic=``, ``read=``, ``filter=``, ``signals=``: see
``fkcore.codec``). Copper wires are not recorded: the codec derives them. The ``w`` lines are
canonical with their two ends in natural key order, sorted by net and ends.

A carrier's ``net=`` (2.0, CORE-V2.md "Nets, carriers and the record": a net is one lane) is one
net, ``iron``, on whichever lanes its items fall (every V1 layout reads as before), or a belt's two
lanes, ``iron|copper`` (``iron`` on the left lane looking downstream, ``copper`` on the right),
``iron|`` or ``|copper`` for one lane only, and ``ore+fuel|`` for two nets on one lane. An
inserter, a pipe or a pump has no lanes: ``ore+fuel`` is two nets through one inserter into its
one target. :func:`net_names` and :func:`net_lanes` read it, :func:`pair_net` and :func:`hand_net`
write it; a ``~<net>`` owner may name such a carrier's nets (``~ore|fuel``: the route of two nets
that share a belt).

A sealed module's **port tile** is a record of its own (2.0.41, PLAN-GLOBAL phase A):

    port rib.iron at 0,4 dir E lane L items iron-plate
    port both.plate at 19,5 dir E lane LR items iron-plate|iron-plate
    port burn.feed at 6,9 dir N lane LR items iron-ore|coal

``<inst>.<name>`` names the port (the instance path, then the port's name), ``at`` the tile **in
the module's own frame** (0,0 is the frame's top-left, whatever the parent does with it), ``dir``
the port carrier's direction in that frame, ``lane`` the lane(s) the port occupies (one, or ``LR``
for both lanes) and ``items`` what it carries (``|`` splits a pair's left and right lane, ``,``
several items on one lane). The build writes it when it chooses the tile, and a rebuild reads it
back, so a parent change never moves a port: the tile is kept like a committed route, and the
module's own routes with it. A pinned port (``@w4``) whose record disagrees is a ``port`` line,
and the pin wins.

:func:`invariants` lists what makes a layout malformed: two entities on a tile, an entity outside
the factory's size, an owner that does not resolve, a carrier with no net, a net written with a
comma, more than two lanes, or lanes on a carrier that has none. The writer (:func:`dumps` with
``cat``) refuses a layout that has any. Pure, stdlib only.
"""
from __future__ import annotations

import functools
import math
import re
import shlex
from dataclasses import dataclass, field

__all__ = ["FkpError", "InvariantError", "Entity", "Inst", "Cover", "Plot", "PortRec", "Claim",
           "Mark", "WireNet", "Wire", "Layout", "loads", "dumps", "load", "save", "invariants",
           "tiles_of", "DIRS", "CARRIER_TYPES", "LANE_TYPES", "COLOURS", "natural_key", "fmt",
           "split_end", "net_names", "net_lanes", "pair_net", "hand_net", "map_nets", "port_key",
           "port_record_key"]

DIRS = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
        "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
_DIR = {d: i for i, d in enumerate(DIRS)}

#: Entity types that move a net's items: each must carry exactly one net (CORE.md invariants).
def machine_pump(cat, e):
    """A pump an instance owns with no ``net=`` (3.0, ``Pump<fluid=...>``): an end of the two nets
    it joins, as a machine is, not a carrier of one."""
    return cat.kind(e.name) == "pump" and not e.net and not str(e.owner).startswith("~")


CARRIER_TYPES = frozenset({"transport-belt", "underground-belt", "splitter", "inserter", "pipe",
                           "pipe-to-ground", "pump", "loader", "loader-1x1", "linked-belt"})
#: the colours of a `w` wire: a circuit wire, or (2.1) a copper line between power domains (a
#: pole spanning tree inside a domain stays the codec's, derived)
COLOURS = ("red", "green", "copper")      # copper (2.1): a copper line between power domains
#: the connector a ``w`` end may name: a combinator's input or output side
_PORTS = ("in", "out", "left", "right")   # a combinator's sides; a power switch's (2.1)
#: The carriers with two lanes, whose ``net=`` may name a net per lane (``iron|copper``).
LANE_TYPES = frozenset({"transport-belt", "underground-belt", "splitter", "loader", "loader-1x1",
                        "linked-belt"})


# -- a carrier's nets (2.0: a net is one lane) ----------------------------------------------------
def net_names(net):
    """Every net a ``net=`` names, in order and once each: ``iron|copper`` -> [iron, copper]."""
    return list(_net_names(net or ""))


@functools.lru_cache(maxsize=1 << 16)
def _net_names(net):
    out = []
    for part in re.split(r"[|+]", net):
        if part and part not in out:
            out.append(part)
    return tuple(out)


def net_lanes(net):
    """``{"L": [nets], "R": [nets]}`` of a ``net=`` written with lanes (``iron|copper``, ``iron|``),
    None for one written without (``iron``: V1, on whichever lanes its items fall)."""
    if not net or "|" not in net:
        return None
    left, right = net.split("|", 1)
    return {"L": [p for p in left.split("+") if p], "R": [p for p in right.split("+") if p]}


def pair_net(left, right):
    """The ``net=`` of a belt with ``left`` on its left lane and ``right`` on its right."""
    return f"{left}|{right}"


def hand_net(net):
    """The ``net=`` of a carrier without lanes (an inserter) that carries what ``net`` does:
    ``ore|fuel`` -> ``ore+fuel``."""
    return "+".join(net_names(net)) if net else net


def map_nets(net, fn):
    """``net`` with every name turned by ``fn``, its lanes kept: the surface's ``<factory>/<net>``
    of ``ore|fuel`` is ``main/ore|main/fuel``."""
    if not net:
        return net
    return re.sub(r"[^|+]+", lambda m: fn(m.group()), net)


class FkpError(ValueError):
    def __init__(self, where, msg):
        super().__init__(f"{where}: {msg}")
        self.where, self.msg = where, msg


class InvariantError(ValueError):
    def __init__(self, problems):
        super().__init__("refusing to write a malformed layout:\n  " + "\n  ".join(problems))
        self.problems = problems


@dataclass
class Entity:
    key: str
    name: str
    x: float
    y: float
    dir: int | None = None
    attrs: dict = field(default_factory=dict)       # recipe, net, ug, ... -> str

    @property
    def owner(self):
        return self.key.rsplit("/", 1)[0] if "/" in self.key else ""

    @property
    def net(self):
        return self.attrs.get("net")


@dataclass
class Inst:
    path: str
    kind: str
    params: dict
    x: float
    y: float
    rot: int
    sig: str


@dataclass
class Cover:
    name: str
    kind: str
    params: dict


@dataclass
class Plot:
    factory: str
    x: int
    y: int
    rot: int
    w: int
    h: int
    params: dict = field(default_factory=dict)

    def box(self):
        return (self.x, self.y, self.x + self.w, self.y + self.h)


@dataclass
class PortRec:
    """A sealed module's port tile, recorded (2.0.41, PLAN-GLOBAL phase A): the tile the build
    chose **in the module's own frame**, its direction there, the lane(s) it occupies and what it
    carries. Kept like a committed route: a rebuild reads the tile back, so a parent change never
    moves a port."""
    key: str                                      # "<inst>.<name>"
    x: int
    y: int
    dir: int                                      # the port carrier's direction, own frame
    lane: str                                     # "L" | "R" | "LR"
    items: str                                    # "iron-plate", "iron-ore|coal", "a,b"


def port_key(rec_key):
    """``<inst>.<name>`` (a port record's key) -> ``<inst>/<name>`` (the netlist's port key)."""
    inst, _dot, name = rec_key.rpartition(".")
    return f"{inst}/{name}"


def port_record_key(key):
    """``<inst>/<name>`` (the netlist's port key) -> ``<inst>.<name>`` (its record's)."""
    inst, _slash, name = key.rpartition("/")
    return f"{inst}.{name}"


@dataclass
class Claim:
    name: str
    x0: int
    y0: int
    x1: int
    y1: int
    until: int | None = None
    reason: str = ""


@dataclass
class Mark:
    name: str                                     # actor@tick
    tiles: list                                   # [(x, y)]
    until: int | None = None


@dataclass
class WireNet:
    name: str
    colour: str                                   # "red" | "green"


@dataclass(frozen=True)
class Wire:
    net: str
    colour: str
    a: str                                        # an entity key, maybe with ".in" / ".out"
    b: str

    def canonical(self):
        """The same wire with its ends in natural key order."""
        if natural_key(self.b) < natural_key(self.a):
            return Wire(self.net, self.colour, self.b, self.a)
        return self


def split_end(end):
    """A ``w`` end -> (entity key, "in" | "out" | None)."""
    key, dot, port = end.rpartition(".")
    if dot and port in _PORTS and "/" in key:
        return key, port
    return end, None


@dataclass
class Layout:
    level: str                                    # "factory" | "surface"
    name: str
    size: tuple | None = None                     # (W, H), factories only
    insts: dict = field(default_factory=dict)
    covers: dict = field(default_factory=dict)
    plots: dict = field(default_factory=dict)
    claims: dict = field(default_factory=dict)
    marks: dict = field(default_factory=dict)
    entities: dict = field(default_factory=dict)  # key -> Entity
    wirenets: dict = field(default_factory=dict)  # name -> WireNet (2.0, circuits)
    wires: list = field(default_factory=list)     # [Wire], canonical
    ports: dict = field(default_factory=dict)     # "<inst>.<name>" -> PortRec (2.0.41)

    def add(self, e: Entity):
        if e.key in self.entities:
            raise FkpError(e.key, "duplicate key")
        self.entities[e.key] = e
        return e

    def copy(self, **changes):
        """A copy with ``changes`` applied, every record travelling with it: a step that writes a
        new layout (route, hands, tiers, unfold) keeps the port tiles (2.0.41)."""
        fields = dict(level=self.level, name=self.name, size=self.size, insts=dict(self.insts),
                      covers=dict(self.covers), plots=dict(self.plots),
                      claims=dict(self.claims), marks=dict(self.marks),
                      entities=dict(self.entities), wirenets=dict(self.wirenets),
                      wires=list(self.wires), ports=dict(self.ports))
        fields.update(changes)
        return Layout(**fields)


# -- formatting -----------------------------------------------------------------------------------
def fmt(v):
    v = float(v)
    if v == int(v):
        return str(int(v))
    return repr(v)


def _num(s, where):
    try:
        v = float(s)
    except ValueError:
        raise FkpError(where, f"not a number: {s!r}") from None
    return int(v) if v == int(v) and "." not in s else v


def _pair(s, where):
    parts = s.split(",")
    if len(parts) != 2:
        raise FkpError(where, f"expected x,y, got {s!r}")
    return _num(parts[0], where), _num(parts[1], where)


def _int(s, where):
    v = _num(s, where)
    if not isinstance(v, int):
        raise FkpError(where, f"expected a whole number, got {s!r}")
    return v


#: The lanes a port record may name: one, or both of a belt end's two (2.0.41).
PORT_LANES = ("L", "R", "LR")


def _tile(s, where):
    """``x,y`` of a whole tile (a port's), unlike :func:`_pair`'s position."""
    parts = s.split(",")
    if len(parts) != 2:
        raise FkpError(where, f"expected x,y, got {s!r}")
    return _int(parts[0], where), _int(parts[1], where)


def _size(s, where):
    m = re.fullmatch(r"(\d+)x(\d+)", s)
    if not m:
        raise FkpError(where, f"expected WxH, got {s!r}")
    return int(m.group(1)), int(m.group(2))


def _kv(tokens, where):
    out = {}
    for t in tokens:
        if "=" not in t or t.startswith("="):
            raise FkpError(where, f"expected key=value, got {t!r}")
        k, v = t.split("=", 1)
        if k in out:
            raise FkpError(where, f"{k} given twice")
        out[k] = v
    return out


def _kv_text(d, first=()):
    keys = [k for k in first if k in d] + sorted(k for k in d if k not in first)
    return [f"{k}={_quote(d[k])}" for k in keys]


_PLAIN = re.compile(r"[^\s'\"\\#]*")


def _quote(v):
    """``v`` as one shell word: as it is when nothing in it is special, else single-quoted."""
    v = str(v)
    if _PLAIN.fullmatch(v):
        return v
    return "'" + v.replace("'", "'\"'\"'") + "'"


def natural_key(s):
    return list(_natural_key(s))


@functools.lru_cache(maxsize=1 << 17)
def _natural_key(s):
    # 2.1.28: remembered; route's patches sorted ~700k keys on the 20261030-2352 world
    return tuple(int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s))


# -- parse ----------------------------------------------------------------------------------------
def loads(text, path="<fkp>") -> Layout:
    lay = None
    for n, raw in enumerate(text.splitlines(), 1):
        where = f"{path}:{n}"
        try:
            t = shlex.split(raw, comments=True, posix=True)
        except ValueError as exc:
            raise FkpError(where, str(exc)) from None
        if not t:
            continue
        if lay is None:
            if len(t) != 4 or t[0] != "fkp" or t[1] != "1" or t[2] not in ("factory", "surface"):
                raise FkpError(where, "the first line must be: fkp 1 factory|surface <name>")
            lay = Layout(t[2], t[3])
            continue
        head = t[0]
        try:
            if head == "size":
                if lay.level != "factory" or len(t) != 2:
                    raise FkpError(where, "size <W>x<H> belongs to a factory")
                lay.size = _size(t[1], where)
            elif head == "inst":
                i = t.index("at")
                if t[i + 2] != "rot" or t[i + 4] != "sig" or len(t) != i + 6:
                    raise FkpError(where, "inst <path> <Kind> [k=v ...] at x,y rot r sig s")
                x, y = _pair(t[i + 1], where)
                rec = Inst(t[1], t[2], _kv(t[3:i], where), x, y, _int(t[i + 3], where), t[i + 5])
                _put(lay.insts, rec.path, rec, where)
            elif head == "cover":
                _put(lay.covers, t[1], Cover(t[1], t[2], _kv(t[3:], where)), where)
            elif head == "plot":
                if lay.level != "surface" or t[2] != "at" or t[4] != "rot" or t[6] != "size":
                    raise FkpError(where, "plot <factory> at x,y rot r size WxH (surfaces only)")
                x, y = _pair(t[3], where)
                w, h = _size(t[7], where)
                _put(lay.plots, t[1], Plot(t[1], x, y, _int(t[5], where), w, h,
                                           _kv(t[8:], where)), where)
            elif head == "port":
                if len(t) != 10 or t[2] != "at" or t[4] != "dir" or t[6] != "lane" \
                        or t[8] != "items":
                    raise FkpError(where, "port <inst>.<name> at x,y dir D lane L|R|LR items "
                                          "ITEM[|ITEM]")
                if lay.level != "factory":
                    raise FkpError(where, "a port record belongs to a factory")
                if t[5] not in _DIR:
                    raise FkpError(where, f"dir: expected a compass direction, got {t[5]!r}")
                if t[7] not in PORT_LANES:
                    raise FkpError(where, f"lane: expected L, R or LR, got {t[7]!r}")
                if not t[1].rpartition(".")[0] or not t[1].rpartition(".")[2]:
                    raise FkpError(where, f"{t[1]}: a port record's key is <inst>.<name>")
                x, y = _tile(t[3], where)
                _put(lay.ports, t[1], PortRec(t[1], x, y, _DIR[t[5]], t[7], t[9]), where)
            elif head == "claim":
                if t[2] != "box":
                    raise FkpError(where, "claim <name> box x0,y0 x1,y1 [until t] [\"reason\"]")
                x0, y0 = _pair(t[3], where)
                x1, y1 = _pair(t[4], where)
                rest, until, reason = t[5:], None, ""
                if rest[:1] == ["until"]:
                    until, rest = _int(rest[1], where), rest[2:]
                if rest:
                    reason = rest[0]
                    if len(rest) > 1:
                        raise FkpError(where, "a claim takes one quoted reason")
                _put(lay.claims, t[1], Claim(t[1], x0, y0, x1, y1, until, reason), where)
            elif head == "mark":
                if t[2] != "tiles":
                    raise FkpError(where, "mark <actor@tick> tiles x,y ... [until t]")
                rest, until = t[3:], None
                if "until" in rest:
                    i = rest.index("until")
                    until, rest = _int(rest[i + 1], where), rest[:i]
                tiles = [_pair(p, where) for p in rest]
                _put(lay.marks, t[1], Mark(t[1], tiles, until), where)
            elif head == "wire":
                if len(t) != 3 or t[2] not in COLOURS:
                    raise FkpError(where, "wire <name> red|green")
                _put(lay.wirenets, t[1], WireNet(t[1], t[2]), where)
            elif head == "w":
                if len(t) != 5 or t[2] not in COLOURS:
                    raise FkpError(where, "w <net> red|green <key> <key>")
                lay.wires.append(Wire(t[1], t[2], t[3], t[4]).canonical())
            elif head == "e":
                x, y = _pair(t[3], where)
                rest, d = t[4:], None
                if rest and rest[0] in _DIR:
                    d, rest = _DIR[rest[0]], rest[1:]
                e = Entity(t[1], t[2], x, y, d, _kv(rest, where))
                if e.key in lay.entities:
                    raise FkpError(where, f"duplicate key {e.key}")
                lay.entities[e.key] = e
            else:
                raise FkpError(where, f"unknown line {head!r}")
        except IndexError:
            raise FkpError(where, f"incomplete {head} line") from None
    if lay is None:
        raise FkpError(path, "empty file: the first line must be: fkp 1 factory|surface <name>")
    lay.wires = sort_wires(lay.wires)
    return lay


def sort_wires(wires):
    """``w`` records in canonical order, each once."""
    return sorted({w.canonical() for w in wires},
                  key=lambda w: (natural_key(w.net), natural_key(w.a), natural_key(w.b), w.colour))


def _put(table, key, rec, where):
    if key in table:
        raise FkpError(where, f"{key} given twice")
    table[key] = rec


# -- write ----------------------------------------------------------------------------------------
def record_lines(lay: Layout) -> list:
    """Every line except the entities, in canonical order."""
    out = [f"fkp 1 {lay.level} {lay.name}"]
    if lay.size is not None:
        out.append(f"size {lay.size[0]}x{lay.size[1]}")
    for path in sorted(lay.insts, key=natural_key):
        r = lay.insts[path]
        out.append(" ".join(["inst", r.path, r.kind] + _kv_text(r.params)
                            + ["at", f"{fmt(r.x)},{fmt(r.y)}", "rot", str(r.rot), "sig", r.sig]))
    for name in sorted(lay.covers, key=natural_key):
        r = lay.covers[name]
        out.append(" ".join(["cover", r.name, r.kind] + _kv_text(r.params)))
    for name in sorted(lay.plots, key=natural_key):
        p = lay.plots[name]
        out.append(" ".join(["plot", p.factory, "at", f"{p.x},{p.y}", "rot", str(p.rot), "size",
                             f"{p.w}x{p.h}"] + _kv_text(p.params)))
    for name in sorted(lay.ports, key=natural_key):
        r = lay.ports[name]
        out.append(f"port {r.key} at {fmt(r.x)},{fmt(r.y)} dir {DIRS[r.dir % 16]} lane {r.lane} "
                   f"items {r.items}")
    for name in sorted(lay.claims, key=natural_key):
        c = lay.claims[name]
        parts = ["claim", c.name, "box", f"{fmt(c.x0)},{fmt(c.y0)}", f"{fmt(c.x1)},{fmt(c.y1)}"]
        if c.until is not None:
            parts += ["until", str(c.until)]
        if c.reason:
            parts.append('"' + c.reason.replace('"', "'") + '"')
        out.append(" ".join(parts))
    for name in sorted(lay.marks, key=natural_key):
        m = lay.marks[name]
        parts = ["mark", m.name, "tiles"] + [f"{fmt(x)},{fmt(y)}" for x, y in m.tiles]
        if m.until is not None:
            parts += ["until", str(m.until)]
        out.append(" ".join(parts))
    for name in sorted(lay.wirenets, key=natural_key):
        out.append(f"wire {name} {lay.wirenets[name].colour}")
    for w in sort_wires(lay.wires):
        out.append(f"w {w.net} {w.colour} {w.a} {w.b}")
    return out


def entity_line(e: Entity) -> str:
    parts = ["e", e.key, e.name, f"{fmt(e.x)},{fmt(e.y)}"]
    if e.dir is not None:
        parts.append(DIRS[e.dir % 16])
    return " ".join(parts + _kv_text(e.attrs, first=("recipe", "net", "ug")))


def dumps(lay: Layout, cat=None) -> str:
    """The canonical text. With ``cat``, refuses a layout that breaks an invariant."""
    if cat is not None:
        problems = invariants(lay, cat)
        if problems:
            raise InvariantError(problems)
    lines = record_lines(lay)
    for key in sorted(lay.entities, key=natural_key):
        lines.append(entity_line(lay.entities[key]))
    return "\n".join(lines) + "\n"


def load(path) -> Layout:
    with open(path, encoding="utf-8") as fh:
        return loads(fh.read(), path)


def save(lay: Layout, path, cat):
    text = dumps(lay, cat)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# -- invariants -----------------------------------------------------------------------------------
def tiles_of(cat, e: Entity):
    return cat.tiles(e.name, float(e.x), float(e.y), e.dir if e.dir is not None else 0)


def invariants(lay: Layout, cat, nets=None) -> list:
    """Every invariant the layout breaks, as text lines (empty = well-formed).

    ``nets`` (the factory's net names, from its source) makes ``~net`` owners strict; without it a
    ``~net`` owner resolves when some carrier in the file carries that net.
    """
    problems = []
    carried = {n for e in lay.entities.values() for n in net_names(e.net)}
    known_nets = set(nets) if nets is not None else carried
    owners = set(lay.insts) | set(lay.covers)
    taken = {}
    for key in sorted(lay.entities, key=natural_key):
        e = lay.entities[key]
        if not cat.has_entity(e.name):
            problems.append(f"{key}: unknown prototype {e.name}")
            continue
        owner = e.owner
        if not owner:
            problems.append(f"{key}: a key is <owner>/<local>")
        elif owner.startswith("~"):
            if not net_names(owner[1:]) or any(n not in known_nets
                                               for n in net_names(owner[1:])):
                problems.append(f"{key}: owner {owner} is not a net of this factory")
        elif owner not in owners and not any(owner.startswith(p + "/") for p in lay.insts):
            problems.append(f"{key}: owner {owner} is not an inst or cover line")
        if cat.kind(e.name) in CARRIER_TYPES and not machine_pump(cat, e):
            net = e.net
            if not net or not net_names(net):
                problems.append(f"{key}: a {e.name} carries no net")
            elif "," in net:
                problems.append(f"{key}: a {e.name} carries two nets ({net}): a belt's lanes "
                                "are written a|b, an inserter's nets a+b")
            elif net.count("|") > 1:
                problems.append(f"{key}: a {e.name} has two lanes, not {net.count('|') + 1} "
                                f"({net})")
            elif "|" in net and cat.kind(e.name) not in LANE_TYPES:
                problems.append(f"{key}: a {e.name} has no lanes ({net}): its nets are written "
                                f"{hand_net(net)}")
        try:
            tiles = tiles_of(cat, e)
        except ValueError as exc:
            problems.append(f"{key}: {exc}")
            continue
        for t in sorted(tiles):
            if t in taken:
                problems.append(f"{key}: tile {t[0]},{t[1]} is already {taken[t]}")
            else:
                taken[t] = key
            if lay.size is not None and not (0 <= t[0] < lay.size[0] and 0 <= t[1] < lay.size[1]):
                problems.append(f"{key}: tile {t[0]},{t[1]} is outside the factory's "
                                f"{lay.size[0]}x{lay.size[1]} frame")
    for name in sorted(lay.ports, key=natural_key):
        r = lay.ports[name]
        inst, _dot, _port = name.rpartition(".")
        rec = lay.insts.get(inst)
        if rec is None:
            problems.append(f"port {name}: no inst line {inst} in the layout")
        # 2.0.48: a port is an edge of its module's frame and lays no carrier; its record names
        # the tile just inside that edge, which must lie in the factory's frame
        if lay.size is not None and rec is not None and rec.rot % 2 == 0 \
                and not (0 <= r.x + int(rec.x) < lay.size[0]
                         and 0 <= r.y + int(rec.y) < lay.size[1]):
            problems.append(f"port {name}: tile {r.x + int(rec.x)},{r.y + int(rec.y)} is outside "
                            f"the factory's {lay.size[0]}x{lay.size[1]} frame")
    for w in lay.wires:
        for end in (w.a, w.b):
            if split_end(end)[0] not in lay.entities:
                problems.append(f"w {w.net}: {end} is not an entity of the layout")
        net = lay.wirenets.get(w.net)
        if net is None:
            problems.append(f"w {w.net}: no wire line names {w.net}")
        elif net.colour != w.colour:
            problems.append(f"w {w.net}: a {w.colour} wire on the {net.colour} wire net {w.net}")
    return problems
