"""fkcore.elaborate: a factory's .fk source -> the flat logical netlist, plus the netlist checks.

Elaboration expands every instance (built-in template or sub-module) into machines keyed
``<instance path>/<local>`` (``gears/m0``, ``iron/smelt/m3``), then resolves every net to endpoint
sets (CORE.md "Objects"):

1. explicit drivers, 2. ``X.*`` drivers (free output endpoints only), 3. the net's items (declared,
else the drivers' products), 4. explicit consumers, 5. ``X.*`` consumers (free input endpoints).

A ref adds only endpoints of the net's items. The checks are report lines and never stop anything:
cannot-carry, SUSHI_SINK, no-feed, no-taker, two-nets, no-rate. Malformed source (an unknown template, a
ref that resolves to nothing, a bad parameter) raises ElabError: no netlist exists for it.

An ``n=auto`` row (1.6, ``AUTO_KINDS``) is elaborated with the count ``sizes`` gives it (the
build's ``size`` step, ``fkcore.size``), else the count the factory's .fkp laid out last, else its
low bound; its Inst keeps the bounds (``auto``) and its params the count, so everything after
elaboration sees a plain row. The netlist's structure (nets, endpoints) is the same at any count.

A ``Bus`` instance is a net of its own, named as the instance (its belts and splitters carry it):
the loads that nets feed drive it, the taps that nets draw from consume it (``_bus_nets``). So a
net ends at a load (``net iron1: smelt -> bus.load1``) or starts at a tap (``net fe: bus.tap1 ->
gears``), and the bus joins them. A port no net names is in none, and no check line names it.

Circuits (2.0): the ``signal``, ``wire``, ``behave`` and ``expect`` statements of every scope, a
net's ``when`` and an instance's ``when`` are kept with their scope, and ``fkcore.circuits``
resolves them into ``Flat.circuit`` after the nets, with its own checks (no-signal, no-wire, gate-open,
require-not-met, LIMIT, cannot-filter).

``lanes A | B`` (2.0, Rory 2026-10-21: a net is one lane, so two nets can share a belt) pairs two
nets of the same module: ``Flat.pairs`` maps the pair's ``.fkp`` net (``A|B``, A on the left lane)
to (A, B), and ``Flat.lane_of`` each of them to it. A pair is two single nets on one belt, never a
sushi net, so it draws no ``CLOG`` of its own. A net shares a belt with one other net at most, and
a fluid net or a Bus has no lanes to share: either is malformed source (ElabError).
Pure, stdlib only.
"""
from __future__ import annotations

import dataclasses
import math
import os
import re
from dataclasses import dataclass, field

from . import facts as _facts
from . import lang
from . import snapshot
from .templates import (CONTACT, INF, SINGLE, TEMPLATES, MSpec, TemplateError, literal_machines,
                        machines as template_machines)

__all__ = ["ElabError", "Machine", "FlatNet", "PortInst", "Check", "Flat", "load", "elaborate",
           "elaborate_file", "AUTO_KINDS", "PRELUDE", "prelude_path", "preludes", "core_of", "params_of"]

#: the templates whose count may be ``n=auto``: the rows
AUTO_KINDS = ("MinerRow", "SmelterRow", "SmelterArray", "AsmRow", "LabRow")


class ElabError(ValueError):
    def __init__(self, path, line, msg):
        super().__init__(f"{path}:{line}: {msg}")
        self.path, self.line, self.msg = path, line, msg


@dataclass
class Machine:
    key: str
    inst: str                  # instance path, "/"-joined
    kind: str                  # template kind
    prototype: str
    recipe: str | None
    needs: dict
    makes: dict
    takes: frozenset | None
    sink: bool
    drain: float | None
    source: bool
    duty: float
    extra: dict = field(default_factory=dict)

    def accepts(self, item):
        return self.takes is None or item in self.takes


@dataclass
class FlatNet:
    name: str                  # path-qualified: "gear", "iron/ore"
    items: list
    drivers: list              # [(machine key, item)]
    consumers: list            # [(machine key, item)]
    declared: bool
    line: int
    limited: bool = False      # `net NAME limited:` the source limits its makers (1.7)
    when: tuple | None = None  # a gated net (2.0): (lang.Cond, its scope)
    scope: str = ""            # the path of the body it is written in (2.0.28)
    width: int = 1             # 2.0.54, `net NAME width 2:` both lanes of one belt
    lane: str | None = None    # 2.0.54, `net NAME lane L:` its lane where it ends on a belt
    why: str | None = None     # 2.1, `net NAME limited "...":` what limits it, as written
    #: 2.1, `net each NAME`: the group's path-qualified name (``flat.each`` lists its nets in
    #: cell order), None for any other net
    group: str | None = None
    #: 3.0, a chained net's segment: the chain's path-qualified name (its first segment's), None
    #: for any other net. ``gear``, ``gear@wp``, ``gear@sp.a`` are one chain, ``gear``.
    chain: str | None = None

    @property
    def sushi(self):
        return len(self.items) > 1


@dataclass
class Check:
    code: str
    where: str
    msg: str
    line: int = 0

    def __str__(self):
        # 2.0.52: no fixed column. The codes run from `ok` to `not-where-designed`, and a padded
        # column of them was the loud look this release is about; the code now reads as the first
        # word of the line
        return f"{self.code} {self.where}: {self.msg}"


@dataclass
class Inst:
    path: str
    kind: str
    params: dict
    hints: dict
    machines: list             # machine keys, all of them (recursively for a module)
    module: str | None         # module name when this is a sub-module instance
    scope: dict | None         # name -> Inst for a sub-module instance
    line: int
    literal: tuple | None = None   # Literal<file>: (entities, frame size, file origin, sig)
    auto: tuple | None = None      # n=auto: (lo, hi), the bounds of the count in params["n"]
    when: tuple | None = None      # its machines' own enable condition (2.0): (lang.Cond, scope)
    size: tuple | None = None      # a module's declared frame (2.0.28): (W, H)
    conns: list | None = None      # a Bus's connection list (2.0.32): [(name, lang.Ref)]
    #: 2.1, a kind's instance (``a = Assembler<...>``): the core kind it resolves to (``Machine``)
    #: and its parameters with the kinds' defaults under the written ones. ``kind`` and
    #: ``params`` stay as written. None for an instance of a core kind or a module
    core: str | None = None
    kparams: dict | None = None


def core_of(inst):
    """The core kind an instance is built as (2.1): ``Machine`` for an ``Assembler``."""
    return getattr(inst, "core", None) or inst.kind


def params_of(inst):
    """An instance's parameters with its kinds' defaults (2.1): what its core kind is given."""
    kp = getattr(inst, "kparams", None)
    return inst.params if kp is None else kp


@dataclass
class PortInst:
    """A port of a sealed module instance (2.0.29, PLAN-MODULES): a **lane** of the module's belt
    end, keyed ``<instance>/<port>``, the module's own endpoint in the netlist. Its machine is a
    conduit: it makes and takes the port's items at no rate of its own, and the two nets that name it
    are one pool in the rates model, so a crossing net is one net whose two segments meet at the
    port. ``items`` is the left lane's items and ``right`` the right lane's when the port is the
    pair ``A | B``; ``lane`` is a one-lane port's pinned lane (``@s4L``), else None."""
    key: str
    name: str
    dir: str             # "in" | "out" | "inout"
    items: list          # the left lane's items
    side: str            # "n" | "e" | "s" | "w"
    pos: int | None      # the tile along the side as written (`@w4`), or None: chosen at layout
    scope: str           # the sealed instance's path
    module: str          # the module type's name
    size: tuple | None   # the module's declared frame, or None (computed at layout)
    line: int
    src: str
    lane: str | None = None    # "L" | "R" of a one-lane port as pinned, or None: chosen at layout
    right: list | None = None  # the right lane's items: the port is the pair `A | B`
    inout: bool = False  # 3.0: declared inout, ``dir`` the way its nets run (_settle_inout)

    @property
    def all_items(self):
        return list(self.items) + list(self.right or ())

    @property
    def both(self):
        """The port is the whole belt end: ``X | X``, the same items on both lanes (2.0.41)."""
        return self.right is not None and list(self.right) == list(self.items)

    def width_of(self, item):
        """How many of the port's lanes carry ``item``: 2 for ``X | X``, 1 for a pair's lane or a
        one-lane port (the capacity ``too-thin`` rates the port at)."""
        lanes = self.lanes()
        return sum(1 for _lane, items in lanes if item in items)

    def lanes(self):
        """[(lane, items)] the port occupies: both for a pair, else its one lane (None if chosen)."""
        if self.right is not None:
            return [("L", self.items), ("R", self.right)]
        return [(self.lane, self.items)]


@dataclass
class Flat:
    name: str
    path: str
    machines: dict = field(default_factory=dict)    # key -> Machine
    nets: dict = field(default_factory=dict)        # name -> FlatNet
    insts: dict = field(default_factory=dict)       # path -> Inst
    asserts: list = field(default_factory=list)     # [(lang.Assert, scope path)]
    covers: list = field(default_factory=list)      # [(lang.Cover, scope path)]
    coppers: list = field(default_factory=list)     # [lang.Copper] (2.1, the top level's)
    checks: list = field(default_factory=list)      # [Check]
    scopes: dict = field(default_factory=dict)      # scope path -> {name: Inst}
    circuit_src: dict = field(default_factory=dict)  # kind -> [(lang node, scope path)] (2.0)
    circuit: object = None                          # fkcore.circuits.Circuit (2.0)
    pairs: dict = field(default_factory=dict)       # "A|B" -> (A, B): two nets on one belt
    lane_of: dict = field(default_factory=dict)     # net -> its pair's "A|B"
    ports: dict = field(default_factory=dict)       # "<instance>/<port>" -> PortInst (2.0.28)
    port_tiles: dict = field(default_factory=dict)  # port key -> (x, y, out, lane) in its frame
                                                    # (recorded, or chosen at layout; 2.0.41)
    port_records: dict = field(default_factory=dict)  # port key -> fkp.PortRec (the committed .fkp)
    sealed: set = field(default_factory=set)        # instance paths whose module declares ports
    stacks: dict = field(default_factory=dict)      # stack path -> (cell, n, head) (2.0.48)
    stack_alias: dict = field(default_factory=dict)  # stack path -> {port name: its child}
    #: 2.0.60, `modules=auto` / `machine=auto`: choice key -> Choice (the size step's to make)
    choices: dict = field(default_factory=dict)
    stack_ends: set = field(default_factory=set)    # port keys a stack leaves open: dead ends
    #: 2.0.63, do not populate: the instance paths that are DNP (each with all it holds)
    dnp: set = field(default_factory=set)
    #: 2.1, the buffers: instance path -> "chest" (a Buffer, a Chest<hold>) or "module" (an
    #: instance of a `module NAME buffer`)
    buffers: dict = field(default_factory=dict)
    #: 2.1, a Literal's names (its file's "names"): instance path -> {name: machine key}
    literal_names: dict = field(default_factory=dict)
    #: 2.1, `net each NAME`: group name -> its nets, cell 0 first (one belt each, routed as one
    #: ribbon: ``fkcore.ribbons``)
    each: dict = field(default_factory=dict)
    #: 2.1, `bundle NAME: a, b`: bundle name -> its nets in ribbon order (belt k carries the k-th)
    bundles: dict = field(default_factory=dict)
    #: 2.1: the port keys of every Stack cell's (and head's) `stack` pairs, where a belt runs on
    #: from one cell to the next (the rate model serves a short line's cells in that order)
    chain: set = field(default_factory=set)

    def is_dnp(self, key):
        """A machine key or instance path inside a DNP instance (2.0.63)."""
        return any(key == p or key.startswith(p + "/") for p in self.dnp)

    def resolve_ref(self, ref, scope=""):
        """``Ref`` -> (Inst or None, machine keys); the Inst is None for a single machine or a port.
        A ref into a sealed module names one of its ports, or elaboration refuses it (2.0.28)."""
        return _resolve(self, ref, scope)


# -- loading --------------------------------------------------------------------------------------
def load(path):
    """Parse ``path`` and the modules it imports (relative to its folder). -> (File, modules).
    2.1: the kinds and tiers of every file it loads come with them (``f.kinds``, ``f.tiers``, its
    own first), so a module from another file finds the kinds it was written with."""
    f = lang.parse(snapshot.read(path), path)
    modules = dict(f.modules)
    _imports(f, os.path.dirname(os.path.abspath(path)), modules, {os.path.abspath(path)},
             f.kinds, f.tiers, {os.path.abspath(path): f})
    return f, modules


def _imports(f, folder, modules, seen, kinds=None, tiers=None, parsed=None):
    kinds = f.kinds if kinds is None else kinds
    tiers = f.tiers if tiers is None else tiers
    parsed = {} if parsed is None else parsed
    for names, rel, line in f.imports:
        target = os.path.abspath(os.path.join(folder, rel))
        if not os.path.exists(target):
            raise ElabError(f.path, line, f"import: no file {rel}")
        # 3.0 (U9): each file is parsed once per load, so a module imported by two paths (a
        # diamond: world.fk and d5.fk both import FurnacePair from cells.fk) is one module
        g = parsed.get(target)
        if g is None:
            g = parsed[target] = lang.parse(snapshot.read(target), target)
        if target not in seen:
            seen.add(target)
            _imports(g, os.path.dirname(target), modules, seen, kinds, tiers, parsed)
        for name, k in g.kinds.items():     # 2.1: an imported file's kinds come with it
            kinds.setdefault(name, k)
        for name, t in g.tiers.items():     # and its tiers
            tiers.setdefault(name, t)
        for name in names:
            mod = g.modules.get(name)
            if mod is None and name in g.kinds:
                # a kind: in ``kinds`` now. 2.1, the library: a kind of a module brings that
                # module and what it is built of (GearCell its AsmCell1), unless the importer
                # has its own of that name
                n, kseen = name, set()
                while n in g.kinds and n not in kseen:
                    kseen.add(n)
                    n = getattr(g.kinds[n], "target", n)
                if n in g.modules:
                    for other in [n] + _uses(g, n):
                        if other not in modules:
                            dep = g.modules[other]
                            dep._brought = True
                            modules[other] = dep
                continue
            if mod is None:
                body = g.body
                if g.modules and not _body_written(body):
                    raise ElabError(f.path, line,
                                    f"import: {rel} has no module {name} (it defines "
                                    f"{', '.join(sorted(g.modules))}) and no body of its own")
                # 2.0.43 (PLAN-GLOBAL phase B): the file defines no module NAME, so its body
                # **is** the module: `import Outpost from "outpost.fk"` needs no wrapping, and a
                # `size WxH` at the top of the file is its frame (an empty body is an empty plot)
                mod = lang.Module(name, body.line, insts=body.insts, nets=body.nets,
                                  asserts=body.asserts, covers=body.covers, signals=body.signals,
                                  wires=body.wires, behaves=body.behaves, expects=body.expects,
                                  lanes=body.lanes, sets=body.sets, ports=body.ports,
                                  size=body.size, stacks=body.stacks,
                                  bundles=getattr(body, "bundles", []))
                # 2.1: the body uses its own file's modules, so they come with it (a world.fk
                # body placed by nauvis.fk finds the Mall module world.fk defines)
                for other, m in g.modules.items():
                    modules.setdefault(other, m)
            if name in modules and modules[name] is not mod and \
                    not getattr(modules[name], "_brought", False):
                raise ElabError(f.path, line, f"import: module {name} is defined twice")
            modules[name] = mod
            # 2.1, cells within cells: a module brings the modules of its file it is built of (a
            # SmeltColumn its FurnaceCell), unless the importer has its own of that name
            for other in _uses(g, name):
                if other not in modules:
                    dep = g.modules[other]
                    dep._brought = True
                    modules[other] = dep


def _uses(g, name):
    """The modules of file ``g`` that its module ``name`` is built of, deepest last: its instances'
    kinds, a Stack's ``cell=`` and ``head=``, and the modules its file's kinds name."""
    out, todo = [], [name]
    while todo:
        mod = g.modules.get(todo.pop())
        if mod is None:
            continue
        for inst in mod.insts:
            names = [inst.kind] + [v for k, v in inst.params.items()
                                   if k in ("cell", "head") and isinstance(v, str)]
            for n in names:
                seen = set()
                while n in g.kinds and n not in seen:     # a kind of a module: its module
                    seen.add(n)
                    n = g.kinds[n].target if hasattr(g.kinds[n], "target") else n
                if n in g.modules and n != name and n not in out:
                    out.append(n)
                    todo.append(n)
    return out


def _placement_hints(inst):
    """An instance's hints, with the placement its own line gives it (2.0.43, PLAN-GLOBAL phase
    B): ``gap`` (tiles kept free around a plot) and ``lifecycle`` (``retired`` / ``temporary
    until ...``), which the root design's plot list reads."""
    hints = dict(inst.hints)
    if getattr(inst, "gap", 0):
        hints["gap"] = inst.gap
    if getattr(inst, "lifecycle", None):
        hints["lifecycle"] = inst.lifecycle
    return hints


def _body_written(body):
    """Whether a file's top-level body holds anything (so it is a factory file, not a file of
    ``module`` definitions only)."""
    return any(getattr(body, k) for k in ("insts", "nets", "asserts", "covers", "signals",
                                          "wires", "behaves", "expects", "lanes", "sets"))


# -- 2.1: the prelude and kinds -------------------------------------------------------------------
#: the toolkit's prelude: the everyday kinds every file gets without an import
PRELUDE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prelude.fk")
_PRELUDES = {}                   # path -> (mtime, lang.File)
#: core kinds a kind may not name: each is written as itself
_NOT_KINDABLE = ("Literal", "Bus")


def prelude_path(src):
    """The prelude a file at ``src`` gets: the nearest ``prelude.fk`` or ``game/prelude.fk`` in its
    folder or above it (a run's own), else the toolkit's ``fkcore/prelude.fk``."""
    if src and not str(src).startswith("<"):
        d = os.path.dirname(os.path.abspath(src))
        while True:
            for cand in (os.path.join(d, "prelude.fk"), os.path.join(d, "game", "prelude.fk")):
                if os.path.isfile(cand):
                    return cand
            up = os.path.dirname(d)
            if up == d:
                break
            d = up
    return PRELUDE


def preludes(src):
    """The preludes a file at ``src`` gets, nearest first: a run's own (``prelude_path``) over the
    toolkit's (2.1.9). A run's prelude changes or adds kinds; whatever it doesn't name comes from
    the toolkit's, so a kind added in a later release (``Buffer``, 2.1.8) reaches a run whose
    prelude was copied before it."""
    own = prelude_path(src)
    if os.path.normcase(os.path.abspath(own)) == os.path.normcase(PRELUDE):
        return [load_prelude(PRELUDE)]
    return [load_prelude(own), load_prelude(PRELUDE)]


def load_prelude(path=PRELUDE):
    """The prelude at ``path``, parsed (read again when the file changes). It holds kind lines
    only: a module belongs in a file that is imported."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        raise ElabError(path, 0, "the prelude is missing") from None
    hit = _PRELUDES.get(path)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    with open(path, encoding="utf-8") as fh:
        f = lang.parse(fh.read(), path)
    if f.modules or f.imports or _body_written(f.body):
        raise ElabError(path, 1, "a prelude holds kind and tiers lines only: put modules and "
                                 "instances in a file of their own")
    _PRELUDES[path] = (mtime, f)
    return f


class _Kinds:
    """Kind lookup (2.1, PLAN-2.1 "B. kind"): ``layers`` are {name: lang.Kind}, the file's own (with
    its imports') first and the prelude's last. A module of the name wins over any kind; a kind
    whose target is its own name (``kind Furnace = Furnace<fuel=wood>``) builds on the layer
    below."""

    def __init__(self, layers, modules):
        self.layers, self.modules = layers, modules

    def find(self, name, start=0):
        for i in range(start, len(self.layers)):
            k = self.layers[i].get(name)
            if k is not None:
                return i, k
        return None, None

    def resolve(self, name, src, line):
        """-> (core, [params of each kind, innermost first], [names walked, the written first]).
        ``core`` is a module's name, a core kind, or (not found) the name itself, which the caller
        reports as before (``unknown template``)."""
        names, plist, seen, layer, cur = [name], [], set(), 0, name
        while cur not in self.modules:
            i, k = self.find(cur, layer)
            if k is None:
                if len(names) > 1 and cur not in TEMPLATES and cur != "Stack":
                    last = names[-2]
                    raise ElabError(src, line, f"{name}: kind {last} names {cur}, which is no "
                                               "kind, module or core kind")
                break
            if (i, cur) in seen:
                raise ElabError(k.path, k.line, f"kind {k.name}: a cycle ("
                                + " -> ".join(names) + ")")
            seen.add((i, cur))
            if k.target in _NOT_KINDABLE:
                raise ElabError(k.path, k.line, f"kind {k.name}: a {k.target} is written as "
                                                "itself, not through a kind")
            plist.insert(0, k.params)
            layer = i + 1 if k.target == k.name else i
            cur = k.target
            names.append(cur)
        return cur, plist, names


class _Tiers:
    """``tiers`` lines (2.1, PLAN-2.1 "C. tiers"): ``layers`` are {kind name: lang.Tiers}, the
    file's own (with its imports') first and the prelude's last."""

    def __init__(self, layers):
        self.layers = layers

    def find(self, name):
        return next((layer[name] for layer in self.layers if name in layer), None)

    def names(self):
        return {n for layer in self.layers for n in layer}


def _box_sig(cat, name):
    """An entity's fluid boxes as positions and flows (every facing), and whether the game opens
    them only for a fluid recipe (an assembling-machine-2's)."""
    row = ((getattr(cat, "_fluid", None) or {}).get("entities") or {}).get(name) or {}
    boxes = tuple(sorted(
        (b.get("production"), tuple(sorted((c.get("flow"), c.get("type"), c.get("dir"),
                                            tuple(tuple(a) for a in c.get("at") or ()))
                                           for c in b.get("connections") or ())))
        for b in row.get("boxes") or ()))
    return boxes, bool(row.get("boxes_need_fluid_recipe"))


def _tier_checks(tiers, cat):
    """Every entity on a ``tiers`` line has the same size, the same energy and the same fluid boxes
    at the same positions, since a cell's belts and pipes are laid for one machine (2.1). An entity
    without boxes goes with ones the game opens only for a fluid recipe: on any other recipe
    neither has a box, and a fluid recipe leaves the boxless one out of the menu."""
    for layer in tiers.layers:
        for t in layer.values():
            for e in t.entities:
                if not cat.has_entity(e):
                    raise ElabError(t.path, t.line, f"tiers {t.name}: unknown entity {e}")
            first = t.entities[0]
            for e in t.entities[1:]:
                why = None
                if cat.size(e) != cat.size(first):
                    why = (f"{first} is {'x'.join(map(str, cat.size(first)))} and {e} "
                           f"{'x'.join(map(str, cat.size(e)))}")
                elif (cat.entity(e).get("energy") or {}).get("source") != \
                        (cat.entity(first).get("energy") or {}).get("source"):
                    why = (f"{first} runs on {(cat.entity(first).get('energy') or {}).get('source')}"
                           f" and {e} on {(cat.entity(e).get('energy') or {}).get('source')}")
                else:
                    (a, a_opt), (b, b_opt) = _box_sig(cat, first), _box_sig(cat, e)
                    if a != b and not ((not a and b_opt) or (not b and a_opt)):
                        why = f"{first} and {e} have different fluid boxes"
                if why:
                    raise ElabError(t.path, t.line, f"tiers {t.name}: {why}: one tier line is one "
                                                    "footprint, one energy and one set of fluid "
                                                    "boxes")


def _kind_merge(core, plist, src, line, path):
    """The kinds' parameters and the instance's, later ones over earlier. ``machine=`` is
    ``entity=`` on a Machine, so an instance's ``machine=`` overrides its kind's ``entity=``."""
    out = {}
    for p in plist:
        p = dict(p)
        if core == "Machine":
            if "machine" in p and "entity" in p:
                raise ElabError(src, line, f"{path}: entity= and machine= are one parameter: "
                                           "give one")
            if "machine" in p:
                p["entity"] = p.pop("machine")
        out.update(p)
    return out


def elaborate_file(path, cat, name=None, layout=None, sizes=None):
    f, modules = load(path)
    return elaborate(f, modules, cat, name or os.path.splitext(os.path.basename(path))[0],
                     layout, sizes)


# -- elaboration ----------------------------------------------------------------------------------
def _join(*parts):
    return "/".join(p for p in parts if p)


#: what every instance takes besides its kind's own parameters: its share of full speed, and
#: 2.0.63's do-not-populate flag
META = ("duty", "dnp")


def _dnp(params, path, line, src):
    """``<dnp>`` / ``dnp=1`` (2.0.63) -> True; ``dnp=0`` -> False."""
    v = params.get("dnp", False)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)) and v in (0, 1):
        return bool(v)
    raise ElabError(src, line, f"{path}: dnp is a flag (<dnp>, or dnp=1 / dnp=0), got {v!r}")


def _duty(params, path, line, src):
    v = params.get("duty")
    if v is None:
        return 1.0
    if not isinstance(v, (int, float)) or not 0 <= v <= 1:
        raise ElabError(src, line, f"{path}: duty must be between 0 and 1, got {v!r}")
    return float(v)


def elaborate(f, modules, cat, name="main", layout=None, sizes=None) -> Flat:
    """``layout`` (the factory's .fkp) is needed by Literal instances, and gives an ``n=auto``
    row the count it laid out; it also carries the committed port tiles (2.0.41), which a rebuild
    keeps. ``sizes`` ({instance path: n}, the size step's) overrides it. 2.1: the kinds are the
    file's own (and its imports') over the preludes' (``preludes``)."""
    flat = Flat(name, f.path)
    pre = preludes(f.path)
    flat._kinds = _Kinds([getattr(f, "kinds", {}) or {}] + [p.kinds for p in pre], modules)
    flat._tiers = _Tiers([getattr(f, "tiers", {}) or {}] + [p.tiers for p in pre])
    _tier_checks(flat._tiers, cat)
    flat._layout = layout
    flat._sizes = dict(sizes or {})
    flat._lanes = []
    flat._bundles = []
    flat._port_named = {}
    flat._dnp_named = {}
    flat._auto_n = set()        # 2.1: the Stacks whose n=auto the size step counts
    _expand(flat, f.body, "", modules, cat, 1.0, f.path, ())
    for path, (line, src) in sorted(flat._dnp_named.items()):
        if path not in flat.insts:
            raise ElabError(src, line, f"dnp {path.replace('/', '.')}: no such instance")
    _bus_conns(flat, cat)
    _resolve_nets(flat, cat)
    _split_nets(flat, cat)
    _bus_nets(flat)
    _resolve_lanes(flat, cat)
    _resolve_bundles(flat, cat)
    _port_pairs(flat, cat)
    _width_checks(flat, cat)
    _recorded_ports(flat, layout)
    _port_checks(flat)
    _endpoint_checks(flat, cat)
    _auto_gates(flat, cat)
    from . import circuits
    flat.circuit = circuits.resolve(flat, cat)
    if flat.dnp:
        # 2.0.63: a DNP piece is not built, so what would be wrong with it built says nothing now
        flat.checks = [c for c in flat.checks
                       if not any(flat.is_dnp(w) for w in str(c.where).split())]
    return flat


#: the library's gate (2.1): `when W.ITEM < auto` on a net holds its loop to this many seconds of
#: what the loop's machines take of ITEM at full speed, and at least GATE_CRAFTS crafts' worth of
#: it for each machine. Measured on the lab (2.1, tests/library/mall.fk, 30 game minutes): four
#: MallCells at auto 160 iron, 40 copper and 40 gears made 90-100% of the model and never stopped,
#: with iron on its own lane (GateCellR). On one shared lane the first source takes every gap.
GATE_SECONDS = 40
GATE_CRAFTS = 2


def _by_key(flat):
    """{machine key: the nets it is an end of}."""
    out = {}
    for n, net in flat.nets.items():
        for k, _i in net.drivers + net.consumers:
            out.setdefault(k, set()).add(n)
    return out


def loop_nets(flat, start, by_key=None):
    """The nets of ``start``'s loop (2.1, the library): the nets joined to it through ports
    (a cell's belt runs on into the next cell's), and through buffers, a chest's every net and a
    buffer module's other ports (a loop's end buffer feeds its head)."""
    by_key = _by_key(flat) if by_key is None else by_key
    ports_of = {}
    for k, m in flat.machines.items():
        if m.extra.get("port") and m.extra.get("buffer"):
            ports_of.setdefault(m.inst, []).append(k)
    seen, todo = {start}, [start]
    while todo:
        net = flat.nets[todo.pop()]
        for k, _i in net.drivers + net.consumers:
            m = flat.machines[k]
            hop = set()
            if m.extra.get("port"):
                hop |= by_key.get(k, set())
                if m.extra.get("buffer"):
                    for k2 in ports_of.get(m.inst, ()):
                        hop |= by_key.get(k2, set())
            elif m.extra.get("buffer"):
                hop |= by_key.get(k, set())
            for n in sorted(hop - seen):
                seen.add(n)
                todo.append(n)
    return seen


def _auto_gates(flat, cat):
    """``when W.ITEM < auto`` (2.1, the library): the amount a loop's machines need of ITEM,
    worked out from their recipes into a constant (``GATE_SECONDS``, ``GATE_CRAFTS``). The
    machines are those on the gated net's loop (``loop_nets``) that take ITEM by a recipe. The
    condition gets the number, and ``flat.gates`` keeps how: {net: (item, amount, take/s,
    [machine instances])}. A loop where no machine takes it by a recipe (an auto mall's recipes
    change by circuit, so the netlist fixes none) is a ``gate-auto`` line, and its gate is 0."""
    flat.gates = {}
    for path, inst in flat.insts.items():
        if inst.when is not None and inst.when[0].right == "auto":
            cond = inst.when[0]
            raise ElabError(flat.path, cond.line, f"{path} when {cond}: auto is the amount a "
                            "loop's recipes need, for a gated net; a machine's own condition "
                            "takes a number")
    for name, net in flat.nets.items():
        if net.when is None or net.when[0].right != "auto":
            continue
        cond, scope = net.when
        item = cond.left.signal
        rows, total, amount = [], 0.0, 0
        if cat.is_item(item):
            for n in sorted(loop_nets(flat, name)):
                for k, i in flat.nets[n].consumers:
                    m = flat.machines[k]
                    if i != item or m.extra.get("port") or not m.recipe or \
                            any(r[0] == k for r in rows):
                        continue
                    rate = m.needs.get(item, 0.0)
                    if rate <= 0:
                        continue
                    rec = cat.recipe(m.recipe)
                    per = sum(g["amount"] for g in rec.get("ingredients", ())
                              if g["name"] == item)
                    rows.append((k, m.inst))
                    total += rate
                    amount += max(math.ceil(GATE_SECONDS * rate - 1e-9), GATE_CRAFTS * per)
        if not rows:
            flat.checks.append(Check("gate-auto", f"net {name}",
                                     f"when {cond}: no machine on its loop takes {item} by a "
                                     "recipe, so auto has nothing to work out: write the amount "
                                     "(the gate holds at 0 until then)", cond.line))
        net.when = (dataclasses.replace(cond, right=int(amount)), scope)
        flat.gates[name] = (item, int(amount), total,
                            list(dict.fromkeys(inst for _k, inst in rows)))


def _recorded_ports(flat, layout):
    """Keep the committed .fkp's ``port`` lines (2.0.41, PLAN-GLOBAL phase A): the tile the build
    chose stays where it is, so a parent change never moves a port (``expand`` reads these back
    into the module's own frame and a rebuild lays the same tile)."""
    from . import fkp as fkpmod
    for rec in getattr(layout, "ports", {}).values() if layout is not None else ():
        flat.port_records[fkpmod.port_key(rec.key)] = rec


def _expand(flat, mod, prefix, modules, cat, duty, src, stack, dnp=False):
    scope = {}
    flat.scopes[prefix] = scope
    # 2.0.63: `dnp A, B.c` names instances in this body or below; they are DNP when expanded
    for ref in getattr(mod, "dnps", ()):
        flat._dnp_named.setdefault(_join(prefix, "/".join(ref.parts)), (ref.line, src))
    ports = {}
    for p in mod.ports:
        if prefix == "":
            raise ElabError(src, p.line, f"port {p.name}: a port is a module's interface; declare "
                                        "it inside `module NAME { ... }`")
        key = _join(prefix, p.name)
        if key in flat.ports:
            raise ElabError(src, p.line, f"port {p.name} is defined twice")
        for item in p.all_items:            # a pipe has no lanes (as `lanes` says of a fluid)
            if cat.is_fluid(item) and p.right is not None:
                raise ElabError(src, p.line,
                                f"port {p.name}: a pair is the two lanes of one belt end, and "
                                f"{item} is a fluid: pipes have no lanes")
            if cat.is_fluid(item) and p.lane is not None:
                raise ElabError(src, p.line,
                                f"port {p.name}: {item} is a fluid, and a pipe has no lanes: drop "
                                f"the {p.lane}")
        fluid = _fluid_port(p, cat, src)
        ports[p.name] = key
        items = p.all_items
        flat.ports[key] = PortInst(key=key, name=p.name, dir=p.dir, items=list(p.items),
                                   side=p.side, pos=p.pos, scope=prefix, module=mod.name,
                                   size=mod.size, line=p.line, src=src, lane=p.lane,
                                   right=None if p.right is None else list(p.right))
        # a conduit: makes and takes every item on either lane at no rate of its own (rates._pools
        # joins the two nets that name it into one pool), so a crossing net meets itself here
        flat.machines[key] = Machine(key, prefix, "Port", "pipe" if fluid else "transport-belt",
                                     None, {},
                                     {item: 0.0 for item in items}, frozenset(items),
                                     False, None, False, 1.0,
                                     {"port": p.dir, "items": items,
                                      "port_name": p.name, "module": mod.name, "lane": p.lane})
    # 2.0.40 (Rory, 2026-10-26): ``size`` seals a module, ports or none, so a block (a plot) that
    # connects to nothing keeps its frame; with no ports only robots reach it
    if prefix and (ports or getattr(mod, "size", None) is not None):
        flat.sealed.add(prefix)
    for inst in mod.insts:
        path = _join(prefix, inst.name)
        if inst.name in ports:
            raise ElabError(src, inst.line, f"{inst.name} is a port of module {mod.name}: name the "
                                            "instance otherwise")
        if inst.name in scope:
            raise ElabError(src, inst.line, f"{inst.name} is defined twice")
        # 2.1: a kind is its target with some parameters set. A module's or a Stack's instance
        # becomes one (its own name kept); a core kind's keeps its kind and parameters as written,
        # with the core kind and the merged parameters beside them (``core``, ``kparams``)
        core, kdefaults, knames = flat._kinds.resolve(inst.kind, src, inst.line)
        if kdefaults and (core in modules or core == "Stack"):
            inst = dataclasses.replace(inst, kind=core, params=_kind_merge(
                core, kdefaults + [inst.params], src, inst.line, path))
            kdefaults = []
        allp = _kind_merge(core, kdefaults + [inst.params], src, inst.line, path) \
            if kdefaults else inst.params
        d = min(duty, _duty(allp, path, inst.line, src))
        # 2.0.63 (Rory, 2026-10-29), do not populate: laid out and routed as ever, nothing of it
        # in the model (duty 0: it makes, takes and asks for nothing), and never pasted
        dn = dnp or _dnp(allp, path, inst.line, src) or path in flat._dnp_named
        if dn:
            d = 0.0
            flat.dnp.add(path)
        stacked, stack_auto = None, None
        if inst.kind == "Stack":
            # 2.0.48: a line of cells, built as a module of its own (its head, its cells, the nets
            # between them) and expanded like any module instance
            stacked = inst
            # 2.0.59: `n=auto`, the size step's count of cells
            stack_auto = _auto(flat, path, inst, src)
            outer = next((s[len("Stack:"):] for s in stack if s.startswith("Stack:")), None)
            if stack_auto is not None and outer is not None:
                # 2.1, cells within cells: every copy of a cell is the same, so a Stack inside
                # one has a count of its own; the outer Stack's n=auto counts the cells
                raise ElabError(src, inst.line, f"{path.replace('/', '.')}: {stack_auto[3]}=auto "
                                                f"inside a cell of Stack {outer.replace('/', '.')}: "
                                                "every cell is the same, so give this Stack a "
                                                f"number; {outer.replace('/', '.')}'s own n=auto "
                                                "counts its cells")
            if stack_auto is not None and stack_auto[3] == "n":
                flat._auto_n.add(path)
            if stack_auto is not None:
                inst = dataclasses.replace(inst, params=dict(inst.params,
                                                             **{stack_auto[3]: stack_auto[2]}))
            synth = _stack_module(flat, inst, path, modules, src, cat=cat)
            modules[synth.name] = synth
            inst = lang.Inst(inst.name, synth.name,
                             {k: v for k, v in inst.params.items() if k in META},
                             inst.hints, inst.line)
        if inst.kind in modules:
            if inst.kind in stack:
                raise ElabError(src, inst.line, f"module {inst.kind} contains itself")
            # 2.1 (PLAN-2.1 "D. Module parameters"): the instance's parameters, the defaults
            # under them, substituted into a copy of the module's body
            body, values = _with_args(modules[inst.kind], inst.params, path, src, inst.line)
            if stacked is None and values:
                inst = dataclasses.replace(inst, params=dict(
                    {k: v for k, v in values.items() if k not in inst.params}, **inst.params))
            before = set(flat.machines)
            _expand(flat, body, path, modules, cat, d, src, stack + (inst.kind,), dn)
            keys = [k for k in flat.machines if k not in before and k not in flat.ports]
            if getattr(modules[inst.kind], "buffer", False):
                _buffer_module(flat, modules[inst.kind], path, inst, src)
            # 2.0.43 (PLAN-GLOBAL phase B): an instance line's own `size WxH` is the frame a plot
            # gets from its placement, and it seals the instance as the module's own `size` does
            size = getattr(inst, "size", None) or modules[inst.kind].size
            if size is not None:
                flat.sealed.add(path)
                for key, port in flat.ports.items():
                    if port.scope == path:
                        port.size = size
            node = Inst(path, inst.kind, inst.params, _placement_hints(inst), keys, inst.kind,
                        flat.scopes[path], inst.line, size=size, conns=inst.conns)
            if stacked is not None:
                node.kind, node.params = "Stack", dict(stacked.params)
                if stack_auto is not None:
                    node.params[stack_auto[3]] = stack_auto[2]
                    node.auto = stack_auto[:2]
        else:
            params = {k: v for k, v in inst.params.items() if k not in META}
            auto = _auto(flat, path, inst, src, core)
            if auto is not None:
                params["n"] = auto[2]
            picked = _choose(flat, mod, inst, path, params, cat, src, core, knames, kdefaults)
            kp = _kind_merge(core, kdefaults + [params], src, inst.line, path) \
                if kdefaults else params
            imported = None
            try:
                if inst.kind == "Literal":
                    ents = _literal_entities(flat, path, params, cat, src)
                    if isinstance(ents, tuple):
                        imported, ents = ents, ents[0]
                    specs = literal_machines(ents, cat)     # a power plant may have none
                elif inst.conns and inst.kind == "Bus":
                    # its connection list builds the ports and their positions (_bus_conns, 2.0.32)
                    specs = []
                else:
                    specs = template_machines(core, kp, cat)
            except TemplateError as exc:
                raise ElabError(src, inst.line, f"{path} = {inst.kind}: {exc}") from None
            keys = []
            for spec in specs:
                m = _machine(spec, path, inst.kind, d)
                if dn:
                    m.extra["dnp"] = True
                flat.machines[m.key] = m
                keys.append(m.key)
            if core == "Chest" and any(flat.machines[k].extra.get("buffer") for k in keys):
                flat.buffers[path] = "chest"
            if imported is not None and path in getattr(flat, "_lit_names", {}):
                _literal_names(flat, path, imported, keys, inst, src, d)
            node = Inst(path, inst.kind, inst.params, _placement_hints(inst), keys, None, None,
                        inst.line, imported, conns=inst.conns)
            if auto is not None:
                node.params = dict(inst.params, n=auto[2])
                node.auto = auto[:2]
            if picked:
                node.params = dict(node.params, **picked)
                node.params = {k: v for k, v in node.params.items() if v is not None}
            if kdefaults or core != inst.kind:
                node.core = core
                node.kparams = _kind_merge(core, kdefaults + [node.params], src, inst.line, path)
        if getattr(inst, "when", None) is not None:
            node.when = (inst.when, prefix)
        scope[inst.name] = node
        flat.insts[path] = node
    for net in mod.nets:
        full = _join(prefix, net.name)
        if full in flat.nets or full in flat.each:
            raise ElabError(src, net.line, f"net {net.name} is defined twice")
        if full in flat.insts and flat.insts[full].kind == "Bus":
            raise ElabError(src, net.line, f"net {net.name}: {net.name} is a Bus, which is a net "
                                           "of its own (its belts carry it); name this net "
                                           "otherwise")
        if getattr(net, "each", False):
            # 2.1, cells within cells: one net per cell, kept together as a group
            group = []
            for one in _each_nets(flat, net, prefix, src):
                name = _join(prefix, one.name)
                if name in flat.nets:
                    raise ElabError(src, net.line, f"net each {net.name}: its cell nets are "
                                                   f"{net.name}_0, {net.name}_1, ..., and a net "
                                                   f"{one.name} exists")
                flat.nets[name] = _Pending(one, prefix, src, full)
                group.append(name)
            flat.each[full] = group
            continue
        if getattr(net, "stages", None):
            # 3.0 chained nets: one segment net per stretch between pieces, linked as one chain
            for one in _chain_nets(flat, net, scope, src):
                name = _join(prefix, one.name)
                if name in flat.nets:
                    raise ElabError(src, net.line, f"net {net.name}: its segment {one.name} is "
                                                   "defined twice")
                flat.nets[name] = _Pending(_net_refs(flat, one, prefix, src), prefix, src,
                                           chain=full)
            continue
        flat.nets[full] = _Pending(_net_refs(flat, net, prefix, src), prefix, src)
    for a in mod.asserts:
        flat.asserts.append((a, prefix))
    for c in mod.covers:
        flat.covers.append((c, prefix))
    for c in getattr(mod, "coppers", ()):
        if prefix:
            raise ElabError(src, c.line, f"copper {c.name}: power domains and their copper wires "
                                         "are written at the top level, not inside a module")
        flat.coppers.append(c)
    for kind in ("signals", "wires", "behaves", "expects", "sets"):
        flat.circuit_src.setdefault(kind, []).extend((node, prefix)
                                                     for node in getattr(mod, kind, ()))
    for ln in getattr(mod, "lanes", ()):
        flat._lanes.append((ln, prefix, src))
    for b in getattr(mod, "bundles", ()):
        flat._bundles.append((b, prefix, src))


def _auto(flat, path, inst, src, core=None):
    """(lo, hi, n, param) of an ``n=auto`` row, None for any other instance: n is the size step's
    (``sizes``), else what the factory's .fkp laid out last, else the low bound. 2.0.63: a Stack's
    ``built=auto`` (``param`` "built") is the count of built cells among its fixed ``n``, bounded
    by n; the size step sizes it as it sizes ``n=auto``, and the cells past it are DNP."""
    autos = [k for k, v in inst.params.items() if isinstance(v, lang.Auto) and k not in CHOICES]
    if not autos:
        return None
    if inst.kind == "Stack" and autos == ["built"]:
        n = inst.params.get("n")
        if not isinstance(n, (int, float)) or isinstance(n, bool):
            raise ElabError(src, inst.line, f"{path}: built=auto counts the built cells of a "
                                            "fixed n=, so n is a number")
        a = inst.params["built"]
        lo, top = min(a.lo, int(n)), min(a.top, int(n))
        key = "built"
    elif autos != ["n"] or (core or inst.kind) not in AUTO_KINDS + ("Stack",):
        raise ElabError(src, inst.line, f"{path}: {autos[0]}=auto: auto is the count of a row "
                                        f"or a Stack, n=auto on {', '.join(AUTO_KINDS)} or Stack "
                                        "(or built=auto on a Stack)")
    else:
        a = inst.params["n"]
        lo, top, key = a.lo, a.top, "n"
    n = flat._sizes.get(path)
    rec = flat._layout.insts.get(path) if flat._layout is not None else None
    if n is None and rec is not None and rec.kind == inst.kind:
        try:
            n = int(rec.params.get(key, ""))
        except ValueError:
            n = None
    n = lo if n is None else min(max(int(n), lo), top)
    return lo, top, n, key


# -- 2.0.60: modules=auto and machine=auto, the size step's choices -------------------------------
#: the parameters a choice can be made for (``entity`` is a Machine's ``machine``, 2.1)
CHOICES = ("modules", "machine", "entity")
#: 2.1: the machines one source line may be built of are a ``tiers`` line's (the prelude's, or the
#: file's own): the same size, energy and fluid boxes, so a cell's geometry never changes (an
#: electric furnace is 3x3, a stone one 2x2). ``_tier_checks`` holds every line to that.
#: recipes productivity modules go into: the game's (``allowed_effects``, facts.json)
PRODUCTIVE = _facts.productive()


@dataclass
class Choice:
    """One source line's ``modules=auto`` or ``machine=auto``: its menu and what it got. Every
    instance the line makes (each cell of a Stack) takes the same."""
    key: str                    # "<module>.<instance>.<param>", the size step's key
    param: str
    menu: tuple
    pick: object
    paths: list = field(default_factory=list)


def _menu(names, param, params, cat, tiers=None, merged=None, core=None):
    """The menu of one choice. ``names``: the kinds an instance's kind resolves through, its own
    first (2.1): the first with a ``tiers`` line gives ``machine=auto`` its menu. A Machine's menu
    leaves out the tiers whose fluid boxes its recipe doesn't fit (``no-fluidbox``)."""
    if param in ("machine", "entity"):
        line = next((tiers.find(n) for n in names if tiers is not None and tiers.find(n)), None)
        if line is None:
            return ()
        menu = tuple(line.entities)
        recipe = (merged or {}).get("recipe")
        if core == "Machine" and isinstance(recipe, str):
            from .templates import fluid_misfit
            menu = tuple(e for e in menu if not fluid_misfit(cat, e, recipe)) or menu
        return menu
    from .templates import MODULE_SLOTS
    machine = params.get("machine", params.get("entity"))
    if isinstance(machine, str) and not MODULE_SLOTS.get(machine):
        return (None,)                                   # a machine with no module slots
    recipe = params.get("recipe")
    if recipe is None and params.get("ore"):
        try:
            recipe = cat.smelting_recipe(params["ore"])
        except KeyError:
            recipe = None
    out = [None, "speed-module"]
    if recipe in PRODUCTIVE:
        out.append("productivity-module")
    return tuple(out)


def _choose(flat, mod, inst, path, params, cat, src, core=None, names=None, kdefaults=()):
    """``params`` with each ``modules=auto`` / ``machine=auto`` made: the size step's pick for the
    line (``sizes``, keyed by the Choice's key), else the first of its menu (no modules, the lowest
    tier). Modules a machine has no slots for are left out. -> {param: pick} or {}."""
    from .templates import MODULE_SLOTS
    core = core or inst.kind
    picked = {}
    for param in CHOICES:
        if not isinstance(params.get(param), lang.Auto):
            continue
        merged = _kind_merge(core, list(kdefaults) + [params], src, inst.line, path)
        menu = _menu(names or [inst.kind], param, params, cat, flat._tiers, merged, core)
        if not menu:
            raise ElabError(src, inst.line, f"{path}: {param}=auto: no choice for a {inst.kind}"
                            + (" (machine=auto: " + ", ".join(sorted(flat._tiers.names())) + ")"
                               if param in ("machine", "entity") else ""))
        key = f"{mod.name}.{inst.name}.{param}"
        if getattr(mod, "args", None):
            # 2.1: a parameterised module's line is one choice per set of values (an iron and a
            # copper line of one cell may want other modules)
            key = f"{mod.name}<{_args_text(mod.args)}>.{inst.name}.{param}"
        pick = flat._sizes.get(key, menu[0])
        pick = pick if pick in menu else menu[0]
        ch = flat.choices.setdefault(key, Choice(key, param, menu, pick))
        ch.paths.append(path)
        picked[param] = pick
    if "modules" in picked:
        machine = picked.get("machine", picked.get("entity",
                                                   params.get("machine", params.get("entity"))))
        default = _kind_merge(core, list(kdefaults), src, inst.line, path).get("entity") \
            if kdefaults else None
        if picked["modules"] is not None and not MODULE_SLOTS.get(
                machine or default or {"SmelterRow": "stone-furnace"}.get(
                    core, "assembling-machine-1")):
            picked["modules"] = None                  # this machine has no slots for them
            flat.choices[f"{mod.name}.{inst.name}.modules"].pick = None
    for param, pick in picked.items():
        if pick is None:
            params.pop(param, None)
        else:
            params[param] = pick
    return picked


def _buffer_module(flat, mod, path, inst, src):
    """An instance of ``module NAME buffer`` (2.1): its ports are the buffer's ends. The model
    takes each in port as an unlimited sink and each out port as an unlimited supply of its items
    (``fkcore.rates``), on the nets outside; inside, it is what the designer chose."""
    ports = [k for k, p in flat.ports.items() if p.scope == path]
    if not ports:
        raise ElabError(src, inst.line, f"{path} = {inst.kind}: module {inst.kind} is a buffer, "
                                        "and a buffer's ends are its ports: declare them (`port "
                                        "NAME : in ITEM @SIDE`, and one out)")
    for key in ports:
        flat.machines[key].extra["buffer"] = path
    flat.buffers[path] = "module"


def _literal_names(flat, path, imported, keys, inst, src, duty=1.0):
    """A Literal's ``names`` (2.1, PLAN-2.1 H): ``{name: entity key}`` from its file, each name of
    a machine a net end like any machine's (``automall.asm10``). A name of an entity that is no
    machine of the netlist (a pole, a crafter without a recipe, a chest without a role) is kept
    apart, so a ref to it is refused with what it names.

    2.1.19 (Rory's 42-lab block, ~4115k): a named belt is a **belt end**, a machine whose key is
    the belt's own: a net that ends at it is routed into the belt from behind (name the first belt
    of the Literal's line), and one that starts at it leaves from its front (the last). As a
    consumer it takes what reaches it, like a chest; what the Literal does with it is its own."""
    names, other = {}, {}
    for name, ekey in flat._lit_names[path].items():
        if ekey in keys:
            names[name] = ekey
            continue
        ent = next((e for e in imported[0] if e.key == ekey), None)
        if ent is not None and ent.name.endswith("transport-belt") \
                and _join(path, ekey.rsplit("/", 1)[-1]) == ekey:
            spec = MSpec(ekey.rsplit("/", 1)[-1], ent.name, None, {}, {}, None, sink=True,
                         extra={"belt_end": True})
            flat.machines[ekey] = _machine(spec, path, inst.kind, duty)
            keys.append(ekey)
            names[name] = ekey
        else:
            other[name] = ent.name if ent is not None else "?"
    flat.literal_names[path] = names
    if other:
        flat._lit_other = getattr(flat, "_lit_other", {})
        flat._lit_other[path] = other


def _literal_entities(flat, path, params, cat, src):
    """A Literal's entities: imported from ``file=`` (-> the import tuple), else pinned in the .fkp.
    2.1: the file's ``names`` ({name: entity key}) go to ``flat._lit_names[path]``."""
    from . import literal
    extra = sorted(set(params) - {"file", "box"})
    if extra:
        raise TemplateError(f"a Literal takes file=, box=, duty= and dnp, not {', '.join(extra)}")
    if "file" in params:
        try:
            ents, size, origin = literal.import_entities(literal.resolve(src, params["file"]),
                                                         path, cat, params.get("box"))
            names = literal.names_of(literal.resolve(src, params["file"]), ents,
                                     params.get("box"))
        except (literal.LiteralError, OSError) as exc:
            raise TemplateError(str(exc)) from None
        if names:
            flat._lit_names = getattr(flat, "_lit_names", {})
            flat._lit_names[path] = names
        return ents, size, origin, literal.signature(ents, size)
    if flat._layout is None:
        raise TemplateError("a Literal without file= needs the factory's .fkp")
    ents = [e for e in flat._layout.entities.values() if e.owner == path]
    if not ents:
        raise TemplateError("the .fkp has no entities under it")
    return ents


def _machine(spec: MSpec, path, kind, duty):
    return Machine(_join(path, spec.local), path, kind, spec.prototype, spec.recipe,
                   dict(spec.needs), dict(spec.makes), spec.takes, spec.sink, spec.drain,
                   spec.source, duty, dict(spec.extra))


@dataclass
class _Pending:
    net: lang.Net
    scope: str
    src: str
    group: str | None = None     # 2.1: the `net each` group it is one cell's net of
    chain: str | None = None     # 3.0: the chained net it is a segment of


@dataclass
class _KeyRef(lang.Ref):
    """A ref that names one endpoint already resolved (2.1, `net each`: port k of a list of ports
    on one side). ``parts`` keep the text for messages."""
    key: str = ""


def _port_items(p):
    """A port's items as written: ``iron-ore | coal`` for a pair, ``iron-plate`` for one lane."""
    right = getattr(p, "right", None)
    return ", ".join(p.items) + (f" | {', '.join(right)}" if right else "")


def _ports_text(flat, node):
    ports = [p for p in flat.ports.values() if p.scope == node.path]
    return "; ".join(f"{p.name} ({p.dir} {_port_items(p)})" for p in ports) or "none"


def _sealed_msg(flat, node, ref, items, side):
    """The refusal for a ref that reaches inside a sealed module: name the port to add."""
    ports = [p for p in flat.ports.values() if p.scope == node.path]
    item = ", ".join(items) if items else "ITEM"
    want = "out" if side == "driver" else "in"
    example = f"{node.path}.{ports[0].name}" if ports else f"{node.path}.port"
    return (f"{ref}: {node.path} is a sealed module ({node.kind}), and its ports are "
            f"{_ports_text(flat, node)}. Name a port (e.g. {example}), or add one to module "
            f"{node.kind}: `port NAME : {want} {item} @SIDE`, and give it a net inside")


def _sealed_ref(flat, node, ref, used, items, side, src):
    """``ref`` reached sealed instance ``node`` after ``used`` parts.

    A port's name may follow; a bare ref or ``.*`` is the ports themselves (2.0.31: ``gears.*`` is
    the module's free ports). Either way the side picks the ports that fit it: a driver takes
    ``out``/``inout`` ports, a consumer ``in``/``inout``. Nothing deeper is nameable."""
    left = ref.parts[used:]
    if not left:                            # `gears` or `gears.*`: its ports
        fit = {"driver": ("out", "inout"), "consumer": ("in", "inout")}.get(side)
        keys = [p.key for p in flat.ports.values()
                if p.scope == node.path and (fit is None or p.dir in fit)]
        if not keys:
            who = {"driver": "output", "consumer": "input"}.get(side, "port")
            raise ElabError(src, ref.line,
                            f"{ref}: {node.path} is a sealed module ({node.kind}) and has no "
                            f"{who} port: its ports are {_ports_text(flat, node)}")
        return None, keys
    if not ref.star:
        key = _join(node.path, left[0])
        if key in flat.ports:
            if len(left) > 1:
                raise ElabError(src, ref.line,
                                f"{ref}: {key} is a port of module {node.kind}, and a port is the "
                                "module's whole interface: nothing is inside it")
            return None, [key]
    raise ElabError(src, ref.line, _sealed_msg(flat, node, ref, items, side))


def _resolve(flat, ref, scope, items=None, side=None, src=None):
    """``Ref`` -> (Inst or None, machine keys); the Inst is None for a single machine or a port.

    A ref into a sealed module (one whose module declares ports) may name a port and nothing
    deeper: everything else is refused with the port it would need (2.0.28, PLAN-MODULES).
    """
    src = src or flat.path
    if isinstance(ref, _KeyRef):
        return None, [ref.key]
    names = flat.scopes.get(scope, {})
    node = names.get(ref.parts[0])
    if node is None:
        key = _join(scope, ref.parts[0])
        if key in flat.ports:
            if ref.star or len(ref.parts) > 1:
                raise ElabError(src, ref.line,
                                f"{ref}: {key} is a port of module {flat.ports[key].module}, and a "
                                "port is the module's whole interface: nothing is inside it")
            return None, [key]
        return None, []
    used = 1
    while used < len(ref.parts):
        part = ref.parts[used]
        if lang.is_index(part):         # 2.1: `cols[2]`, one cell of a Stack
            node = _cell(flat, node, part, ref, src)
            used += 1
            continue
        if node.path in flat.sealed:
            break
        if node.scope is not None and part in node.scope:
            node = node.scope[part]
            used += 1
            continue
        alias = flat.stack_alias.get(node.path, {})
        if node.scope is not None and part in alias:     # a stack's port: its head's (2.0.48)
            node = node.scope[alias[part]]
            continue
        key = _join(node.path, part)
        if node.scope is None and key in flat.machines:
            return None, [key]
        named = flat.literal_names.get(node.path) if node.scope is None else None
        if named and part in named and used == len(ref.parts) - 1 and not ref.star:
            return None, [named[part]]      # 2.1: a Literal's name for one of its machines
        other = getattr(flat, "_lit_other", {}).get(node.path, {})
        if node.scope is None and part in other:
            raise ElabError(src, ref.line, f"{ref}: {part} names a {other[part]} of Literal "
                            f"{node.path}, which is no machine of the netlist (a crafter with a "
                            "recipe, a drill, a lab, a chest with a role)")
        return None, []
    if node.path in flat.sealed:
        return _sealed_ref(flat, node, ref, used, items, side, src)
    return node, list(node.machines)


def _cell(flat, node, part, ref, src):
    """Stack ``node``'s cell ``part`` (``[k]``, ``[-1]``): its Inst. 2.1, cells within cells."""
    if node.path not in flat.stacks:
        what = node.kind if node.scope is None or node.kind != node.module else \
            f"module {node.kind}"
        raise ElabError(src, ref.line, f"{ref}: {node.path.replace('/', '.')} is a {what}, not a "
                                       "Stack: [k] names one of a Stack's cells")
    n = flat.stacks[node.path][1]
    if part == "[*]":
        raise ElabError(src, ref.line, f"{ref}: [*] is every cell, which a net names (`net NAME: "
                                       f"{ref} -> ...`, or `net each`); here name one, [0] to "
                                       f"[{n - 1}]")
    k = int(part[1:-1])
    kk = k + n if k < 0 else k
    if not 0 <= kk < n:
        raise ElabError(src, ref.line, f"{ref}: {node.path.replace('/', '.')} has {n} cells, [0] "
                                       f"to [{n - 1}] (or [-1] to [-{n}] from the end)")
    return node.scope[str(kk)]


def _stars(flat, ref, scope, src, what):
    """``ref`` with every ``[*]`` spelled out -> [(k of its first [*], concrete Ref)] in cell order,
    or None when it has no ``[*]`` (2.1). Each ``[*]`` follows a Stack; ``what`` names the net."""
    if isinstance(ref, _KeyRef) or "[*]" not in ref.parts:
        return None
    i = ref.parts.index("[*]")
    head = lang.Ref(ref.parts[:i], False, ref.line)
    node, _keys = _resolve(flat, head, scope, src=src)
    if node is None or node.path not in flat.stacks:
        kind = "nothing" if node is None else node.kind
        raise ElabError(src, ref.line, f"{what}: {ref}: {head} is {'a ' if node else ''}{kind}, not a "
                                       "Stack: [*] names every cell of a Stack")
    out = []
    for k in range(flat.stacks[node.path][1]):
        one = lang.Ref(ref.parts[:i] + [f"[{k}]"] + ref.parts[i + 1:], ref.star, ref.line)
        out += [(k, r) for _k, r in (_stars(flat, one, scope, src, what) or [(k, one)])]
    return out


def _star_auto(flat, ref, scope, src):
    """The Stack an ``n=auto`` makes that ``ref``'s first ``[*]`` follows, or None."""
    i = ref.parts.index("[*]")
    node, _keys = _resolve(flat, lang.Ref(ref.parts[:i], False, ref.line), scope, src=src)
    return node.path if node is not None and node.path in flat._auto_n else None


def _net_refs(flat, net, scope, src):
    """An ordinary net's refs with ``cols[*].P`` spelled out: every cell's P an endpoint (2.1)."""
    def spread(refs):
        out = []
        for ref in refs:
            got = _stars(flat, ref, scope, src, f"net {net.name}")
            out += [ref] if got is None else [r for _k, r in got]
        return out
    drivers, consumers = spread(net.drivers), spread(net.consumers)
    if drivers == net.drivers and consumers == net.consumers:
        return net
    return dataclasses.replace(net, drivers=drivers, consumers=consumers)


def _chain_nets(flat, net, names, src):
    """A chained net -> [lang.Net], its segments (3.0, PLAN-3.0 §3e).

    ``net gear: gears -> wp -> red`` goes through piece ``wp``, an instance whose module has inout
    ports: the first one declared is its way in, the rest its ways out. The segments are ``gear``
    (gears -> the way in) and ``gear@wp`` (the way out -> red); a piece with several ways out
    gives ``gear@sp.a``, ``gear@sp.b``. A branch line ``sp.a -> wp2 -> cable`` takes way a on to
    its own consumers; the ways no branch takes share the next stage, consumer k to way k (in
    their written order), unless that stage is a piece, which takes one way only."""
    what = f"net {net.name}"

    def ways(ref):
        node = names.get(ref.parts[0])
        if node is None:
            raise ElabError(src, ref.line, f"{what}: {ref} is a chain's piece, and no instance is "
                                           f"named {ref.parts[0]}")
        ports = [p for p in flat.ports.values() if p.scope == node.path]
        keys = [p.name for p in ports if p.dir == "inout"]
        if len(keys) < 2:
            raise ElabError(src, ref.line,
                            f"{what}: {ref} is in a chain, so its module ({node.kind}) needs "
                            f"inout ports, a way in and at least one way out: its ports are "
                            f"{_ports_text(flat, node)}")
        return node.path, keys[0], keys[1:]

    def port(piece, name, line):
        return lang.Ref([piece, name], False, line)

    taken = {}                                   # piece -> {way out: branch}
    for b in net.branches:
        head = b[0][0]
        piece, _in, outs = ways(lang.Ref(head.parts[:1], False, head.line))
        if head.parts[1] not in outs:
            raise ElabError(src, head.line, f"{what}: branch {head}: {head.parts[0]}'s ways out "
                                            f"are {', '.join(outs)} (its first inout port, "
                                            f"{_in}, is its way in)")
        if head.parts[1] in taken.setdefault(head.parts[0], {}):
            raise ElabError(src, head.line, f"{what}: branch {head} is written twice")
        taken[head.parts[0]][head.parts[1]] = b
    out, seen = [], set()

    def segment(name, drivers, consumers, line):
        out.append(dataclasses.replace(net, name=name, drivers=drivers, consumers=consumers,
                                       line=line, stages=None, branches=[]))

    def run(name, drivers, stages, line):
        """Segments from ``drivers`` through ``stages`` (pieces, then the consumers)."""
        if not stages:
            return
        nxt = stages[0]
        last_piece = (len(stages) == 1 and len(nxt) == 1 and len(nxt[0].parts) == 1
                      and nxt[0].parts[0] in taken)  # `net gear: gears -> sp` and branches
        if len(stages) == 1 and not last_piece:  # the consumers
            segment(name, drivers, nxt, line)
            return
        ref = nxt[0]
        piece = ref.parts[0]
        if piece in seen:
            raise ElabError(src, ref.line, f"{what}: {piece} is in the chain twice")
        seen.add(piece)
        _path, way_in, outs = ways(ref)
        segment(name, drivers, [port(piece, way_in, ref.line)], line)
        free = [o for o in outs if o not in taken.get(piece, {})]
        for o, b in taken.get(piece, {}).items():
            run(f"{net.name}@{piece}.{o}", [port(piece, o, b[0][0].line)], b[1:], b[0][0].line)
        rest = stages[1:]
        if not free:
            if rest:
                raise ElabError(src, ref.line, f"{what}: every way out of {piece} has a branch "
                                               "line, so nothing is left for the stage after it")
            return
        if not rest:
            raise ElabError(src, ref.line, f"{what}: {piece}'s ways out {', '.join(free)} go "
                                           f"nowhere: take each on with a branch line "
                                           f"({piece}.{free[0]} -> ...)")
        if len(free) == 1:
            run(f"{net.name}@{piece}" if len(outs) == 1 else f"{net.name}@{piece}.{free[0]}",
                [port(piece, free[0], ref.line)], rest, ref.line)
            return
        if len(rest) > 1:
            raise ElabError(src, ref.line,
                            f"{what}: {piece} has {len(free)} ways out ({', '.join(free)}) and "
                            f"the next stage is a piece, which takes one: name each way with a "
                            f"branch line ({piece}.{free[0]} -> ...)")
        consumers = rest[0]
        if len(consumers) < len(free):
            raise ElabError(src, ref.line,
                            f"{what}: {piece} has {len(free)} ways out ({', '.join(free)}) and "
                            f"{len(consumers)} consumers after it: give every way one, or take a "
                            f"way on with a branch line ({piece}.{free[-1]} -> ...)")
        for k, o in enumerate(free):
            run(f"{net.name}@{piece}.{o}", [port(piece, o, ref.line)],
                [consumers[k::len(free)]], ref.line)

    run(net.name, net.stages[0], net.stages[1:], net.line)
    for piece, ways_taken in taken.items():
        if piece not in seen:
            raise ElabError(src, net.line, f"{what}: branch {piece}.{next(iter(ways_taken))}: "
                                           f"{piece} is not a piece of this chain")
    return out


def _each_nets(flat, net, scope, src):
    """``net each NAME: A[*].P -> B[*].Q`` -> [lang.Net] (2.1): one net per cell, ``NAME_k``
    joining cell k of each side. A side's ref without ``[*]`` names a list of ports (a sealed
    module's loads, its ports in their written order), port k going to net k."""
    what = f"net each {net.name}"
    if net.split:
        raise ElabError(src, net.line, f"{what}: split and each don't go together yet; split the "
                                       "cells' nets inside the cell")
    if not any("[*]" in ref.parts for ref in net.drivers + net.consumers):
        raise ElabError(src, net.line, f"{what}: an each net names a Stack's cells with [*] "
                                       "(like cols[*].pl_out)")
    rows, counts = [], []
    for side, refs in (("driver", net.drivers), ("consumer", net.consumers)):
        for ref in refs:
            if ref.star:
                raise ElabError(src, ref.line, f"{what}: {ref}: name the ports (X[*].P), not .*")
            got = _stars(flat, ref, scope, src, what)
            if got is not None:
                if sum(1 for p in ref.parts if p == "[*]") > 1:
                    raise ElabError(src, ref.line, f"{what}: {ref}: one [*] a ref, so cell k "
                                                   "meets cell k")
                auto = _star_auto(flat, ref, scope, src)
                if auto is not None:
                    raise ElabError(src, ref.line, f"{what}: {ref}: {auto.replace('/', '.')} is "
                                                   "n=auto, and an each net needs a fixed count "
                                                   "on both sides; give it n=")
                rows.append((side, ref, [r for _k, r in got]))
                counts.append((str(ref), len(got), "cells"))
                continue
            _node, keys = _resolve(flat, ref, scope, side=side, src=src)
            ports = [k for k in keys if k in flat.ports]
            if len(keys) < 2 or len(ports) != len(keys):
                raise ElabError(src, ref.line, f"{what}: {ref} is one endpoint; an each net pairs "
                                               "cell k with cell k, so each ref is X[*].P or names "
                                               "a list of ports (a sealed module's)")
            rows.append((side, ref, [_KeyRef(ref.parts + [flat.ports[k].name], False, ref.line,
                                             key=k) for k in keys]))
            counts.append((str(ref), len(keys), "ports"))
    if len({n for _r, n, _w in counts}) > 1:
        have = ", ".join(f"{r} {n} {w}" for r, n, w in counts)
        raise ElabError(src, net.line, f"{what}: both sides need the same count, and they have "
                                       f"{have}")
    out = []
    for k in range(counts[0][1]):
        drivers = [refs[k] for side, _ref, refs in rows if side == "driver"]
        consumers = [refs[k] for side, _ref, refs in rows if side == "consumer"]
        out.append(dataclasses.replace(net, name=f"{net.name}_{k}", drivers=drivers,
                                       consumers=consumers, each=False))
    return out


def _conn_direction(flat, keys, ref, item, src):
    """'load' if the endpoint makes the bus's item, 'tap' if it takes it (a port says so itself)."""
    ports = [flat.ports[k] for k in keys if k in flat.ports]
    if len(ports) == len(keys):
        dirs = {p.dir for p in ports}
        if dirs == {"out"}:
            return "load"
        if dirs == {"in"}:
            return "tap"
        raise ElabError(src, ref.line,
                        f"connection {ref}: its port is {'/'.join(sorted(dirs))}; a bus connection "
                        "needs an in port (a tap) or an out port (a load)")
    makes = any(item in flat.machines[k].makes for k in keys)
    takes = any(item in flat.machines[k].needs
                or (flat.machines[k].sink and (flat.machines[k].takes is None
                                               or item in flat.machines[k].takes))
                for k in keys)
    if makes and takes:
        raise ElabError(src, ref.line,
                        f"connection {ref}: it both makes and takes {item}, so the bus cannot tell a "
                        "load from a tap; name a port instead")
    if makes:
        return "load"
    if takes:
        return "tap"
    raise ElabError(src, ref.line, f"connection {ref}: it neither makes nor takes {item}")


def _fluid_port(p, cat, src):
    """The fluid a port carries (2.1, fluid ports), or None for a belt's port. A fluid port is one
    pipe connection on its frame's edge: one fluid, in or out (its net's way), no lanes (the
    pair and lane refusals above)."""
    fluids = [i for i in p.all_items if cat.is_fluid(i)]
    if not fluids:
        return None
    if len(set(p.all_items)) > 1:
        raise ElabError(src, p.line, f"port {p.name}: a fluid port is one pipe, and a pipe holds "
                                     f"one fluid: {', '.join(p.all_items)} need a port each")
    return fluids[0]


def _stack_module(flat, inst, path, modules, src, cat=None):
    """``Stack<cell=CELL, n=N, head=HEAD>`` (2.0.48, Rory 2026-10-26: "a more generic linear
    template, with each unit programmed routed and validated like any other module ... matching
    input and output ports on opposite sides so that it can stack linearly") -> the lang.Module it
    is: HEAD (optional) and N cells, each a sealed instance of CELL, placed edge to edge, and for
    each of CELL's ``stack NEAR -> FAR`` pairs one net between each cell's FAR and the next one's
    NEAR (the head plays the cell before the first: it has a FAR port of each pair). The items flow
    as the ports' directions say. The last cell's FAR ports are dead ends (``flat.stack_ends``),
    and a ref ``STACK.P`` names the head's port P, or with no head the first cell's NEAR port P or
    the last cell's FAR port P (``flat.stack_alias``)."""
    p = inst.params
    # 2.1: every parameter but the Stack's own goes to each cell (and to the head, where it
    # declares it); a cell or head may be a kind of a module, whose parameters come first
    fwd = {k: v for k, v in p.items() if k not in lang.RESERVED}
    cname, hname = p.get("cell"), p.get("head")
    cname, cfwd = _module_kind(flat, cname, fwd, modules, src, inst.line, path)
    if not isinstance(cname, str) or cname not in modules:
        raise ElabError(src, inst.line, f"{path} = Stack: cell= names a module, got {cname!r}")
    hfwd = None
    if hname is not None:
        hname, hfwd = _module_kind(flat, hname, fwd, modules, src, inst.line, path)
    if hname is not None and (not isinstance(hname, str) or hname not in modules):
        raise ElabError(src, inst.line, f"{path} = Stack: head= names a module, got {hname!r}")
    extra = sorted(set(cfwd) - {q.name for q in modules[cname].params})
    if extra and (hname is None or set(extra) - {q.name for q in modules[hname].params}):
        have = ", ".join(q.name for q in modules[cname].params)
        raise ElabError(src, inst.line, f"{path} = Stack: takes cell=, n=, head=, built=, duty= "
                                        f"and dnp, and passes the cell's own parameters on ("
                                        f"{cname}: {have or 'none'}), not {', '.join(extra)}")
    if hname is not None:
        hdecl = {q.name for q in modules[hname].params}
        hfwd = {k: v for k, v in hfwd.items() if k in hdecl}
        cfwd = {k: v for k, v in cfwd.items() if k not in extra}
    n = p.get("n")
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ElabError(src, inst.line, f"{path} = Stack: n= a whole number of cells, got {n!r}")
    # 2.0.63 (Rory: "a great way to implement a scaling row"): built=K, the first K cells built and
    # the rest DNP, laid out and routed all the same, so raising K builds them where they stand
    built = p.get("built", n)
    if isinstance(built, float) and built.is_integer():
        built = int(built)
    if not isinstance(built, int) or isinstance(built, bool) or not 0 <= built <= n:
        raise ElabError(src, inst.line, f"{path} = Stack: built= a whole number of cells from 0 "
                                        f"to n={n}, got {built!r}")
    # substituted before the ports are checked, so both ends of a pair carry the same items
    cell, _values = _with_args(modules[cname], cfwd, f"{path}/0", src, inst.line)
    if cell.size is None or not cell.stacks:
        raise ElabError(src, inst.line, f"{path} = Stack: cell {cname} needs a size and its "
                                        "`stack NEAR -> FAR` pairs")
    ports = {q.name: q for q in cell.ports}
    opposite = {"n": "s", "s": "n", "e": "w", "w": "e"}
    sides = set()
    for near, far, line in cell.stacks:
        a, b = ports.get(near), ports.get(far)
        if a is None or b is None:
            raise ElabError(src, line, f"stack {near} -> {far}: module {cname} has no port "
                                       f"{near if a is None else far}")
        if opposite[a.side] != b.side or a.pos is None or a.pos != b.pos:
            raise ElabError(src, line, f"stack {near} -> {far}: the two ports stand on opposite "
                                       "edges at the same pinned tile (like @s3 and @n3), so one "
                                       "cell's meets the next one's")
        wet = [i for i in a.all_items + b.all_items if cat is not None and cat.is_fluid(i)]
        if wet and a.all_items != b.all_items:
            raise ElabError(src, line, f"stack {near} -> {far}: a fluid port's two ends carry the "
                                       f"same fluid, so one cell's pipe joins the next one's (got "
                                       f"{', '.join(a.all_items)} and {', '.join(b.all_items)})")
        if {a.dir, b.dir} != {"in", "out"} or a.all_items != b.all_items:
            raise ElabError(src, line, f"stack {near} -> {far}: one port in and one out, "
                                       "carrying the same items")
        sides.add(b.side)
    if len(sides) != 1:
        raise ElabError(src, inst.line, f"{path} = Stack: every FAR port of {cname} stands on one "
                                        f"edge, so the cells stack one way (got {sorted(sides)})")
    grow = sides.pop()
    head = _with_args(modules[hname], hfwd, f"{path}/head", src, inst.line)[0] if hname else None
    if head is not None:
        hports = {q.name: q for q in head.ports}
        for near, far, line in cell.stacks:
            q = hports.get(far)
            if q is None or q.side != grow or q.pos != ports[far].pos or q.dir != ports[far].dir:
                raise ElabError(src, line, f"{path}: head {hname} needs port {far} like {cname}'s "
                                           f"({ports[far].dir} @{grow}{ports[far].pos}): the head "
                                           "meets the first cell as a cell before it would")
        if head.size is None:
            raise ElabError(src, inst.line, f"{path}: head {hname} needs a size")
    w, h = cell.size
    hw, hh = head.size if head is not None else (0, 0)
    at = {"n": lambda k: (0, (n - 1 - k) * h), "s": lambda k: (0, hh + k * h),
          "e": lambda k: (hw + k * w, 0), "w": lambda k: ((n - 1 - k) * w, 0)}[grow]
    insts = [lang.Inst(str(k), cname, dict(cfwd, dnp=True) if k >= built else dict(cfwd),
                       {"at": (*at(k), 0)}, inst.line) for k in range(n)]
    if head is not None:
        hat = {"n": (0, n * h), "s": (0, 0), "e": (0, 0), "w": (n * w, 0)}[grow]
        insts.insert(0, lang.Inst("head", hname, dict(hfwd), {"at": (*hat, 0)}, inst.line))
    nets = []
    for near, far, _line in cell.stacks:
        items = list(dict.fromkeys(ports[far].all_items))
        # 2.0.55: a paired port's two items cross the edge on their own lanes, so the crossing
        # can't clog (`limited` quiets CLOG and nothing else)
        p = ports[far]
        paired = p.right is not None and not (set(p.items) & set(p.right)) and \
            len(p.items) == len(p.right) == 1
        for k in range(n):
            prev = ("head" if head is not None else None) if k == 0 else str(k - 1)
            if prev is None:
                continue
            a = lang.Ref([prev, far], False, inst.line)
            b = lang.Ref([str(k), near], False, inst.line)
            drv, con = (a, b) if ports[far].dir == "out" else (b, a)
            nets.append(lang.Net(f"{far}_{k}", items, [drv], [con], inst.line, paired))
        flat.stack_ends.add(_join(path, str(n - 1), far))
        if head is None:
            flat.stack_ends.add(_join(path, "0", near))
        else:
            flat.chain.add(_join(path, "head", far))
        flat.chain.update(_join(path, str(k), q) for k in range(n) for q in (near, far))
    alias = {}
    if head is not None:
        for q in head.ports:
            alias.setdefault(q.name, "head")
    else:
        for near, far, _line in cell.stacks:
            alias[near], alias[far] = "0", str(n - 1)
    flat.stack_alias[path] = alias
    flat.stacks[path] = (cname, n, hname)
    return lang.Module(f"Stack:{path}", inst.line, insts=insts, nets=nets)


# -- 2.1: module parameters ------------------------------------------------------------------------
_PARAM = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)(?:\s*\[\s*(\d+)\s*\])?")


def _args_text(values):
    return ", ".join(f"{k}={_value_text(v)}" for k, v in values.items())


def _value_text(v):
    return "|".join(map(str, v)) if isinstance(v, list) else str(v)


def _module_kind(flat, name, given, modules, src, line, path):
    """A Stack's ``cell=`` or ``head=`` that names a kind of a module (``kind CopperCell =
    FurnaceCell<plate=...>``) -> (the module, the kind's parameters under ``given``)."""
    if not isinstance(name, str) or name in modules:
        return name, dict(given)
    core, plist, _names = flat._kinds.resolve(name, src, line)
    if core not in modules:
        return name, dict(given)
    return core, _kind_merge(core, plist + [given], src, line, path)


def _with_args(mod, given, path, src, line):
    """(the module's body with its parameters substituted, the values) for an instance that gives
    ``given`` (2.1, PLAN-2.1 "D. Module parameters"). A module without parameters is itself.
    Refused: a parameter it doesn't declare, a required one left out, and a flag for a value."""
    given = {k: v for k, v in given.items() if k not in META}
    decl = {q.name: q for q in mod.params}
    unknown = sorted(set(given) - set(decl))
    if unknown:
        have = ", ".join(decl)
        raise ElabError(src, line, f"{path}: module {mod.name} has no parameter "
                                   f"{', '.join(unknown)} (" + (f"its parameters: {have}; "
                                                              if have else "it takes none; ")
                        + "every module also takes duty= and dnp)")
    missing = [q.name for q in mod.params if q.default is None and q.name not in given]
    if missing:
        raise ElabError(src, line, f"{path}: module {mod.name} needs "
                                   + ", ".join(f"{m}=" for m in missing)
                        + (" (a parameter with no default)" if len(missing) == 1
                           else " (parameters with no default)"))
    for k, v in given.items():
        if v is True:
            raise ElabError(src, line, f"{path}: {mod.name}'s parameter {k} takes a value, like "
                                       f"{k}=iron-plate")
    if not mod.params:
        return mod, {}
    values = {q.name: given.get(q.name, q.default) for q in mod.params}
    return _Subst(mod, values, src).module(), values


class _Subst:
    """A module's body with ``$p`` and ``$p[k]`` replaced by the values (2.1): in port, net and
    ``items`` lists (a list value spreads), parameter values, signals, ``set`` values, the text of
    ``behave`` and ``expect`` lines, and inside strings."""

    def __init__(self, mod, values, src):
        self.mod, self.values, self.src = mod, values, src

    def one(self, name, idx, line):
        if name not in self.values:
            raise ElabError(self.src, line, f"${name}: module {self.mod.name} has no parameter "
                                            f"{name} (its parameters: {', '.join(self.values)})")
        v = self.values[name]
        if idx is None:
            return v
        items = v if isinstance(v, list) else [v]
        if int(idx) >= len(items):
            raise ElabError(self.src, line, f"${name}[{idx}]: {name} is a list of {len(items)} "
                                            f"({_value_text(v)}) in this {self.mod.name}, so "
                                            f"${name}[0] to ${name}[{len(items) - 1}]")
        return items[int(idx)]

    def value(self, v, line):
        """A value as written -> its value: ``$p`` alone is p's value as given (a word, a number,
        a list), ``$p`` inside a string is its text, and a list spreads a list."""
        if isinstance(v, list):
            out = []
            for x in v:
                y = self.value(x, line)
                out.extend(y if isinstance(y, list) else [y])
            return out
        if isinstance(v, lang.Raw):
            return lang.Raw(self.raw(v.value, line))
        if not isinstance(v, str) or "$" not in v:
            return v
        m = _PARAM.fullmatch(v)
        if m:
            return self.one(m.group(1), m.group(2), line)
        return self.text(v, line)

    def raw(self, v, line):
        """Inside a raw setting's value (``cb.`` / ``bp.``): ``$p`` alone is p's value, ``$p``
        in a string its text, at any depth of objects and lists."""
        if isinstance(v, dict):
            return {k: self.raw(x, line) for k, x in v.items()}
        if isinstance(v, list):
            return [self.raw(x, line) for x in v]
        if not isinstance(v, str) or "$" not in v:
            return v
        m = _PARAM.fullmatch(v)
        return self.one(m.group(1), m.group(2), line) if m else self.text(v, line)

    def text(self, v, line):
        if isinstance(v, lang.Raw):
            return lang.Raw(self.raw(v.value, line))
        if not isinstance(v, str) or "$" not in v:
            return v
        return _PARAM.sub(lambda m: _value_text(self.one(m.group(1), m.group(2), line)), v)

    def items(self, items, line):
        if items is None:
            return None
        out = self.value(list(items), line)
        return [_value_text(x) for x in out]

    def cond(self, c):
        if c is None:
            return None
        op = lambda o: o if not isinstance(o, lang.Operand) else dataclasses.replace(
            o, signal=_value_text(self.value(o.signal, c.line)))
        right = c.right
        if isinstance(right, str) and right.startswith("$"):
            # `when W.S < $amount` (the library, 2.1): a number, or auto (the loop's recipes)
            v = self.value(right, c.line)
            if isinstance(v, lang.Auto) or v == "auto":
                right = "auto"
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                right = int(v) if float(v).is_integer() else v
            else:
                raise ElabError(self.src, c.line, f"when {c.left} {c.op} {right}: a condition "
                                f"compares with a number or auto, and {right} is "
                                f"{_value_text(v)}")
        return dataclasses.replace(c, left=op(c.left), right=op(right))

    def module(self):
        mod, R = self.mod, dataclasses.replace
        insts = []
        for i in mod.insts:
            hints = dict(i.hints)
            if isinstance(hints.get("patch"), str):
                hints["patch"] = self.text(hints["patch"], i.line)
            insts.append(R(i, params={k: self.value(v, i.line) for k, v in i.params.items()},
                           hints=hints, when=self.cond(i.when)))
        return R(mod, params=[], args=dict(self.values), insts=insts,
                 ports=[R(p, items=self.items(p.items, p.line), right=self.items(p.right, p.line))
                        for p in mod.ports],
                 nets=[R(n, items=self.items(n.items, n.line), when=self.cond(n.when))
                       for n in mod.nets],
                 covers=[R(c, params={k: self.value(v, c.line) for k, v in c.params.items()})
                         for c in mod.covers],
                 signals=[R(s, game=None if s.game is None
                            else _value_text(self.value(s.game, s.line))) for s in mod.signals],
                 sets=[R(s, attrs={k: self.text(v, s.line) for k, v in s.attrs.items()})
                       for s in mod.sets],
                 behaves=[R(b, lines=[(k, self.text(t, ln), ln) for k, t, ln in b.lines])
                          for b in mod.behaves],
                 expects=[R(e, text=self.text(e.text, e.line)) for e in mod.expects])


def _conn_want(flat, bus, keys):
    """Where along ``bus`` (its own x, from its head) a connection's endpoint stands, from the
    placement hints, or None (2.0.46). A port of a sealed sibling module: its tile's coordinate along
    the bus (a port on the side facing up the bus is that side's coordinate, one along it the
    module's middle); a template sibling: its head. Only a bus running east or south (rot 0 or 1),
    and siblings in its own scope that stand beside it on its staircases' side (north of a bus
    running east, east of one running south), are placed this way; anything else keeps the packed
    order, so a bus never grows into something standing in line with it.

    Rory, 2026-10-26: a bus base, with its spine factories spread along the bus. The list packed
    every staircase at the head, so each spine's plates ran back along the bus to it."""
    at = bus.hints.get("at") if bus.hints else None
    if at is None or int(at[2]) % 4 not in (0, 1):
        return None
    axis = int(at[2]) % 4                       # 0: the bus runs east (x), 1: south (y)
    head = at[0] if axis == 0 else at[1]
    scope = bus.path.rsplit("/", 1)[0] if "/" in bus.path else ""
    got = []
    for key in keys:
        if key in flat.ports:
            port = flat.ports[key]
            inst = flat.insts.get(port.scope)
            side = port.side
        else:
            path = key.rsplit("/", 1)[0]
            while path and path not in flat.insts:
                path = path.rsplit("/", 1)[0] if "/" in path else ""
            inst, side = flat.insts.get(path), None
        if inst is None or (inst.path.rsplit("/", 1)[0] if "/" in inst.path else "") != scope:
            continue
        hint = inst.hints.get("at") if inst.hints else None
        if hint is None:
            continue
        x, y, rot = int(hint[0]), int(hint[1]), int(hint[2]) % 4
        w, h = inst.size if inst.size is not None and rot % 2 == 0 else \
            inst.size[::-1] if inst.size is not None else (1, 1)
        if axis == 0 and y + h > int(at[1]) or axis == 1 and x < int(at[0]) + 1:
            continue                            # not beside the bus on its staircases' side
        lo = x if axis == 0 else y
        if inst.size is not None and side is not None and rot == 0:
            w, h = inst.size
            n = w if axis == 0 else h
            first, last = ("w", "e") if axis == 0 else ("n", "s")
            pinned = flat.ports[key].pos if key in flat.ports else None
            lo += 0 if side == first else n - 1 if side == last else \
                pinned if pinned is not None else n // 2
        got.append(lo - int(head))
    return min(got) if got else None


def _bus_conns(flat, cat):
    """A Bus's connection list (2.0.32, PLAN-MODULES §4): the named entries are nets, written in port
    order. Direction comes from the netlist — an out port or a maker is a load, an in port or a taker
    is a tap — the staircases take their positions from the order (each band clears the one before by
    two tiles), `length` follows, and a tap written before any load is an ``tap-too-early`` line."""
    shared = {}                                 # scope -> how many of its buses have a mouth
    for path, inst in flat.insts.items():
        if any(c.startswith("in") and c[2:].isdigit() for c, _r, _k in inst.conns or ()):
            key = path.rsplit("/", 1)[0] if "/" in path else ""
            shared[key] = shared.get(key, 0) + 1
    for path, inst in list(flat.insts.items()):
        if not inst.conns:
            continue
        if inst.kind != "Bus":
            raise ElabError(flat.path, inst.line,
                            f"{path}: a connection list is a Bus's port map; {inst.kind} has no ports")
        if inst.params.get("load") or inst.params.get("tap"):
            raise ElabError(flat.path, inst.line, f"{path}: a connection list replaces load=/tap=")
        item = inst.params.get("item")
        if not isinstance(item, str):
            raise ElabError(flat.path, inst.line, f"{path}: a Bus needs item= one item")
        try:
            belts = int(inst.params.get("belts", 4))
        except (TypeError, ValueError):
            raise ElabError(flat.path, inst.line, f"{path}: belts= must be a whole number") from None
        scope = path.rsplit("/", 1)[0] if "/" in path else ""
        bus = path.rsplit("/", 1)[-1]
        kinds = []
        for cname, ref, ckind in inst.conns:
            _node, keys = _resolve(flat, ref, scope, items=[item], src=flat.path)
            if not keys:
                raise ElabError(flat.path, ref.line, f"connection {cname}: {ref} names nothing")
            kind = _conn_direction(flat, keys, ref, item, flat.path)
            if cname.startswith("in") and cname[2:].isdigit():
                if not 1 <= int(cname[2:]) <= belts:
                    raise ElabError(flat.path, ref.line,
                                    f"connection {cname}: this bus has belts 1..{belts}")
                if kind != "load":
                    raise ElabError(flat.path, ref.line,
                                    f"connection {cname}: a mouth is an input, so its endpoint must "
                                    f"make {item} (or be an out port)")
                kind = "mouth"
            if ckind is not None:
                if kind != "tap":
                    raise ElabError(flat.path, ref.line,
                                    f"connection {cname}: a {ckind} kind is for a tap, and this one "
                                    f"is a {kind} (a load is dual; a mouth is a belt end)")
            kinds.append((cname, ref, kind, ckind, _conn_want(flat, inst, keys)))
        upstream = any(k == "mouth" for _c, _r, k, _t, _w in kinds)  # a mouth is at the head: first
        for cname, ref, kind, _t, _w in kinds:
            if kind in ("load", "mouth"):
                upstream = True
            elif not upstream:
                flat.checks.append(Check("tap-too-early", f"bus {path}",
                                         f"{cname} is a tap before any load: nothing upstream feeds "
                                         "the bus there", ref.line))
        # 2.0.46: a staircase stands where its endpoint stands along the bus (``_conn_want``), never
        # before the band ahead of it, so the list order is still the order along the bus
        bound, placed = 1, []
        for cname, ref, kind, tkind, want in kinds:
            if kind == "mouth":
                placed.append((cname, ref, kind, None, tkind))
                continue
            if kind == "tap":
                x = max(bound + 2 + belts, want if want is not None else 0)
                band = (x - belts, x)
            else:
                x = max(bound + 2, want if want is not None else 0)
                band = (x, x + belts)
            placed.append((cname, ref, kind, x, tkind))
            bound = band[1]
        length = bound + 1
        given = inst.params.get("length")
        if given is not None and int(given) < length:
            raise ElabError(flat.path, inst.line,
                            f"{path}: length={given} is short of the connection list ({length})")
        inst.params["length"] = int(given) if given is not None else length
        loads = [f"n{x}" for _c, _r, kind, x, _t in placed if kind == "load"]
        taps = [f"n{x}" for _c, _r, kind, x, _t in placed if kind == "tap"]
        tapkinds = [t or "dual" for _c, _r, kind, _x, t in placed if kind == "tap"]
        mouths = [c for c, _r, kind, _x, _t in placed if kind == "mouth"]
        if loads:
            inst.params["load"] = loads
        if taps:
            inst.params["tap"] = taps
        if any(t != "dual" for t in tapkinds):
            inst.params["tapkind"] = tapkinds
        if mouths:
            inst.params["mouth"] = mouths
        try:
            specs = template_machines("Bus", {k: v for k, v in inst.params.items() if k not in META},
                                      cat)
        except TemplateError as exc:
            raise ElabError(flat.path, inst.line, f"{path} = Bus: {exc}") from None
        for spec in specs:
            m = _machine(spec, path, "Bus", 1.0)
            flat.machines[m.key] = m
            inst.machines.append(m.key)
        li = ti = 0
        for cname, ref, kind, _x, _t in placed:
            if kind == "mouth":
                port = lang.Ref([bus, cname], False, ref.line)
                if shared.get(scope, 0) > 1:
                    # 2.0.46: several buses of one scope have mouths (each its own in1): every
                    # mouth's net is named by its bus then (febus_in1, febus_in2), and a single
                    # bus keeps the plain names
                    cname = f"{bus}_{cname}"
                net = lang.Net(cname, [item], [ref], [port], ref.line)
            elif kind == "load":
                li += 1
                port = lang.Ref([bus, f"load{li}"], False, ref.line)
                net = lang.Net(cname, [item], [ref], [port], ref.line)
            else:
                ti += 1
                port = lang.Ref([bus, f"tap{ti}"], False, ref.line)
                net = lang.Net(cname, [item], [port], [ref], ref.line)
            full = _join(scope, cname)
            if full in flat.nets:
                raise ElabError(flat.path, ref.line, f"connection {cname}: net {cname} already exists")
            flat.nets[full] = _Pending(net, scope, flat.path)


def _fluids_taken(flat, p, cat):
    """A net with no ``items`` whose drivers make more than one fluid (an advanced-oil refinery,
    a cracking plant): a pipe holds one fluid, so the net takes the fluids its consumers take, and
    what else the drivers make stays free for the other nets (2.0.53; FRICTION 2026-11-02). None
    when that changes nothing."""
    try:
        drivers = [k for ref in p.net.drivers
                   for k in _resolve(flat, ref, p.scope, side="driver", src=p.src)[1]]
        consumers = [k for ref in p.net.consumers
                     for k in _resolve(flat, ref, p.scope, side="consumer", src=p.src)[1]]
    except ElabError:
        return None                     # the main pass reports it
    if any(k not in flat.machines for k in drivers + consumers):
        return None                     # a port: its items are the interface's
    made = list(dict.fromkeys(i for k in drivers for i in flat.machines[k].makes))
    fluids = [i for i in made if cat.is_fluid(i)]
    if len(fluids) < 2:
        return None
    taken = [i for i in fluids if any(i in flat.machines[k].needs for k in consumers)]
    if not taken or len(taken) == len(fluids):
        return None
    return [i for i in made if not cat.is_fluid(i) or i in taken]


def _resolve_nets(flat, cat):
    pending = list(flat.nets.items())
    work = {}
    out_hold, in_hold = {}, {}

    def machines_of(p, ref, side):
        _node, keys = _resolve(flat, ref, p.scope, items=p.net.items, side=side, src=p.src)
        if not keys:
            raise ElabError(p.src, ref.line, f"net {p.net.name}: {ref} names nothing")
        return keys

    def port_items_check(name, ref, w, keys):
        """``port``: a port named by a net that carries none of the port's items (2.0.28)."""
        port = flat.ports[keys[0]]
        flat._port_named.setdefault(keys[0], set()).add(name)
        flat.checks.append(Check(
            "port", f"port {ref}",
            f"net {name} carries {_items_text(w['items'])}, and port {port.name} is "
            f"{port.dir} {_port_items(port)}", ref.line))

    for name, p in pending:
        work[name] = {"items": list(p.net.items) if p.net.items else _fluids_taken(flat, p, cat),
                      "drivers": [], "consumers": []}
    _buffer_items(flat, pending, cat)

    def add_drivers(name, p, ref, free_only):
        w = work[name]
        added = 0
        keys = machines_of(p, ref, "driver")
        for key in keys:
            m = flat.machines[key]
            if m.extra.get("belt_end") and not m.makes and w["items"]:
                # 2.1.19: a Literal's named belt drives what its net says it carries, without
                # bound, as a requester does (what the Literal puts on it is its own)
                m.makes, m.source = {i: INF for i in w["items"]}, True
            for item in m.makes:
                if w["items"] is not None and item not in w["items"]:
                    continue
                if free_only and (key, item) in out_hold:
                    continue
                if (key, item) in w["drivers"]:
                    continue
                w["drivers"].append((key, item))
                out_hold.setdefault((key, item), []).append(name)
                added += 1
        if not added:
            if keys and all(k in flat.ports for k in keys) and not free_only:
                port_items_check(name, ref, w, keys)
            else:
                flat.checks.append(Check("cannot-carry", f"net {name}", f"driver {ref} makes no "
                                         + _items_text(w["items"]) + (" that is still free"
                                                                      if free_only else ""),
                                         ref.line))

    def add_consumers(name, p, ref, free_only):
        w = work[name]
        added = 0
        keys = machines_of(p, ref, "consumer")
        for key in keys:
            m = flat.machines[key]
            for item in w["items"]:
                if not m.accepts(item):
                    continue
                if free_only and (key, item) in in_hold:
                    continue
                if (key, item) in w["consumers"]:
                    continue
                w["consumers"].append((key, item))
                in_hold.setdefault((key, item), []).append(name)
                added += 1
        if not added:
            if keys and all(k in flat.ports for k in keys) and not free_only:
                port_items_check(name, ref, w, keys)
            else:
                flat.checks.append(Check("cannot-carry", f"net {name}", f"consumer {ref} takes no "
                                         + _items_text(w["items"]) + (" that is still free"
                                                                      if free_only else ""),
                                         ref.line))

    for star in (False, True):
        for name, p in pending:
            for ref in p.net.drivers:
                if ref.star == star:
                    add_drivers(name, p, ref, star)
    for name, p in pending:
        w = work[name]
        if w["items"] is None:
            items = []
            for _key, item in w["drivers"]:
                if item not in items:
                    items.append(item)
            w["items"] = items
        else:
            made = {item for _key, item in w["drivers"]}
            for item in w["items"]:
                if item not in made:
                    flat.checks.append(Check("cannot-carry", f"net {name}",
                                             f"expects {item} but no driver makes it", p.net.line))
    for star in (False, True):
        for name, p in pending:
            for ref in p.net.consumers:
                if ref.star == star:
                    add_consumers(name, p, ref, star)

    flat._split_src = {name: (p.src, p.net.line) for name, p in pending
                       if getattr(p.net, "split", False)}
    for name, p in pending:
        w = work[name]
        when = getattr(p.net, "when", None)
        flat.nets[name] = FlatNet(name, w["items"], w["drivers"], w["consumers"],
                                  p.net.items is not None, p.net.line,
                                  getattr(p.net, "limited", False),
                                  None if when is None else (when, p.scope), p.scope,
                                  getattr(p.net, "width", 1), getattr(p.net, "lane", None),
                                  getattr(p.net, "why", None), group=p.group, chain=p.chain)
    flat._out_hold, flat._in_hold = out_hold, in_hold
    _settle_inout(flat)


def _settle_inout(flat):
    """An inout port takes the way its nets run (3.0, chained nets): ``in`` when the nets outside
    its module only feed it, ``out`` when they only take from it. The router and the frame see an
    in or an out port like any other; ``inout`` stays true, so reports show the piece's ways. A
    port used both ways, or not at all, stays inout."""
    for key, port in flat.ports.items():
        if port.dir != "inout":
            continue
        feeds = takes = False
        for item in port.all_items:
            for n in flat._in_hold.get((key, item), []):
                if n in flat.nets and flat.nets[n].scope != port.scope:
                    feeds = True
            for n in flat._out_hold.get((key, item), []):
                if n in flat.nets and flat.nets[n].scope != port.scope:
                    takes = True
        if feeds != takes:
            port.dir, port.inout = ("in" if feeds else "out"), True
        elif flat.machines[key].prototype == "pipe" and (feeds or takes):
            # a pipe has one way at a time: a fluid port fed and taken from outside is two
            raise ElabError(port.src, port.line,
                            f"port {port.name}: a fluid inout port takes the way its net outside "
                            "runs, and nets outside both feed it and take from it: make it in or "
                            "out")


def _buffer_items(flat, pending, cat):
    """What each buffer chest without ``item=`` holds (2.1): the items of the nets into it, their
    declared items or what their drivers make (another buffer's, once it is known), so a buffer
    drives what it is filled with ("driver gbuf makes no electronic-circuit" was a plain chest in
    the middle, 20261030-2352). Explicit refs only; a fluid stays out (a chest holds none)."""
    bufs = {k for k, m in flat.machines.items()
            if m.extra.get("buffer") is True and m.source and not m.makes}
    if not bufs:
        return

    def keys_of(p, refs, side):
        out = []
        for ref in refs:
            if ref.star:
                continue
            try:
                out += _resolve(flat, ref, p.scope, items=p.net.items, side=side, src=p.src)[1]
            except ElabError:
                continue                # the main pass reports it
        return out
    for _round in range(len(bufs) + 1):
        changed = False
        for _name, p in pending:
            into = [k for k in keys_of(p, p.net.consumers, "consumer") if k in bufs]
            if not into:
                continue
            if p.net.items:
                items = list(p.net.items)
            else:
                items = [i for k in keys_of(p, p.net.drivers, "driver")
                         for i in flat.machines[k].makes if k in flat.machines]
            for k in into:
                m = flat.machines[k]
                for item in dict.fromkeys(items):
                    if item not in m.makes and not cat.is_fluid(item):
                        m.makes[item] = float("inf")
                        changed = True
        if not changed:
            break


def _bus_nets(flat):
    """Each Bus instance's own net, named as the instance: its item, driven by every load a net
    feeds, consumed by every tap a net draws from (Rory's bus: what the loads bring, the taps
    share). The bus's belts and splitters carry it (``fkcore.fragments.bus``)."""
    for path, inst in flat.insts.items():
        if inst.kind != "Bus":
            continue
        item = inst.params.get("item")
        drivers, consumers = [], []
        for key in inst.machines:
            port = flat.machines[key].extra.get("port")
            if port == "load" and (key, item) in flat._in_hold:
                drivers.append((key, item))
                flat._out_hold.setdefault((key, item), []).append(path)
            elif port == "tap" and (key, item) in flat._out_hold:
                consumers.append((key, item))
                flat._in_hold.setdefault((key, item), []).append(path)
        flat.nets[path] = FlatNet(path, [item], drivers, consumers, True, inst.line,
                                  False, None, path.rsplit("/", 1)[0] if "/" in path else "")


def _split_name(name, item):
    """A split net's per-item net: ``blue`` + ``engine-unit`` -> ``blue_engine_unit`` (net names
    have no dashes)."""
    return f"{name}_{item.replace('-', '_')}"


def _split_nets(flat, cat):
    """``net NAME split:`` (2.0.76, Rory 2026-10-31: shared belts kept jamming; load each item onto
    a lane of its own by its own filtered inserter, and merge the lanes): the net becomes one net
    per item, ``NAME_<item>``, paired two to a belt as ``lanes`` pairs in item order (an odd last
    item has a belt to itself). A full lane then holds only its own item, so it never stops the
    others; ``route`` filters each loading inserter to its item (``route._filter_loads``), and
    ``fkcore.pairs`` side-loads the two lanes together. ``flat.split`` maps NAME to its nets."""
    flat.split = {}
    for name, net in list(flat.nets.items()):
        src_net = flat._split_src.get(name)
        if src_net is None:
            continue
        src, line = src_net
        if len(net.items) < 2:
            flat.checks.append(Check("split", f"net {name}", f"carries {_items_text(net.items)}: "
                                     "one item has nothing to split from", line))
            continue
        if any(cat.is_fluid(i) for i in net.items):
            raise ElabError(src, line, f"net {name} split: a fluid's net has no lanes")
        if net.width > 1 or net.lane is not None:
            raise ElabError(src, line, f"net {name} split: split gives each item a lane, so it "
                            "takes no width or lane of its own")
        subs = []
        for item in net.items:
            sub = _split_name(name, item)
            if sub in flat.nets:
                raise ElabError(src, line, f"net {name} split: its {item} net would be {sub}, "
                                "and a net of that name exists")
            drivers = [(k, i) for k, i in net.drivers if i == item]
            consumers = [(k, i) for k, i in net.consumers if i == item]
            for hold, ends in ((flat._out_hold, drivers), (flat._in_hold, consumers)):
                for end in ends:
                    hold[end] = [sub if n == name else n for n in hold.get(end, [])]
            flat.nets[sub] = FlatNet(sub, [item], drivers, consumers, True, net.line, net.limited,
                                     net.when, net.scope, why=net.why)
            subs.append(sub)
        del flat.nets[name]
        flat.split[name] = subs
        for a, b in zip(subs[0::2], subs[1::2]):
            flat.pairs[f"{a}|{b}"] = (a, b)
            flat.lane_of[a] = flat.lane_of[b] = f"{a}|{b}"


def _resolve_lanes(flat, cat):
    """``lanes A | B`` -> ``flat.pairs`` and ``flat.lane_of`` (the module's own nets)."""
    for ln, prefix, src in flat._lanes:
        a, b = _join(prefix, ln.left), _join(prefix, ln.right)
        for name, short in ((a, ln.left), (b, ln.right)):
            if name not in flat.nets:
                raise ElabError(src, ln.line, f"lanes {ln.left} | {ln.right}: no net {short}")
            if name in flat.insts and flat.insts[name].kind == "Bus":
                raise ElabError(src, ln.line, f"lanes {ln.left} | {ln.right}: {short} is a Bus, "
                                "several belts of its own")
            if any(cat.is_fluid(i) for i in flat.nets[name].items):
                raise ElabError(src, ln.line, f"lanes {ln.left} | {ln.right}: net {short} is a "
                                "fluid's, and pipes have no lanes")
            if flat.nets[name].width > 1:
                w = flat.nets[name].width
                raise ElabError(src, ln.line, f"lanes {ln.left} | {ln.right}: net {short} is "
                                + ("width 2, both lanes of its belt, so it shares a belt with none"
                                   if w == 2 else f"width {w}, a ribbon of {w // 2} belts, so it "
                                   "shares a belt with none"))
            if name in flat.lane_of:
                raise ElabError(src, ln.line, f"lanes {ln.left} | {ln.right}: net {short} "
                                f"already shares a belt ({flat.lane_of[name]}), and a belt has "
                                "two lanes")
        key = f"{a}|{b}"
        flat.pairs[key] = (a, b)
        flat.lane_of[a] = flat.lane_of[b] = key


def _resolve_bundles(flat, cat):
    """``bundle NAME: a, b, c`` (2.1) -> ``flat.bundles``: the module's own nets, routed as one
    ribbon in that order. Refused: a name a net or another ribbon has, a net that isn't one of the
    module's, a fluid's net (pipes), a wide net (a ribbon of its own), a net that shares a belt
    (``lanes``) and a net that is in a ribbon already (a bundle or a ``net each``)."""
    taken = {n: f"net each {g.rsplit('/', 1)[-1]}" for g, ns in flat.each.items() for n in ns}
    for b, prefix, src in getattr(flat, "_bundles", ()):
        full = _join(prefix, b.name)
        what = f"bundle {b.name}"
        if full in flat.nets or full in flat.each or full in flat.bundles:
            raise ElabError(src, b.line, f"{what}: {b.name} names a net or a ribbon already; "
                                         "give the bundle a name of its own")
        nets = []
        for short in b.nets:
            name = _join(prefix, short)
            if name not in flat.nets:
                raise ElabError(src, b.line, f"{what}: no net {short} in this module")
            net = flat.nets[name]
            if name in flat.insts and flat.insts[name].kind == "Bus":
                raise ElabError(src, b.line, f"{what}: {short} is a Bus, belts of its own")
            if net.items and any(cat.is_fluid(i) for i in net.items):
                raise ElabError(src, b.line, f"{what}: net {short} is a fluid's, and a ribbon "
                                             "is belts")
            if net.width > 2:
                raise ElabError(src, b.line, f"{what}: net {short} is width {net.width}, a "
                                             "ribbon of its own; a bundle's nets are a belt "
                                             "each")
            if name in flat.lane_of:
                raise ElabError(src, b.line, f"{what}: net {short} shares a belt "
                                             f"({flat.lane_of[name]}); a bundle's nets are a "
                                             "belt each")
            if name in taken:
                raise ElabError(src, b.line, f"{what}: net {short} is in a ribbon already "
                                             f"({taken[name]})")
            nets.append(name)
        for name in nets:
            taken[name] = what
        flat.bundles[full] = nets


def ribbons(flat):
    """{ribbon name: [net, ...]} (2.1): every ``net each`` group and every ``bundle``, in belt
    order. A wide net (``width`` above 2) is a ribbon of one net, which the router deals out."""
    out = {g: list(ns) for g, ns in getattr(flat, "each", {}).items()}
    out.update({b: list(ns) for b, ns in getattr(flat, "bundles", {}).items()})
    return out


def _port_pairs(flat, cat):
    """Two nets that meet at a cell's paired port (``port f_in : in iron-ore | coal``), one on each
    item, share a belt as the port's lanes say: an implied ``lanes A | B`` (2.1.3: the module's
    own ``lanes`` line is enough; the runs needed a top-level one too). A net that already shares
    a belt, or that carries more than its lane's item, is left as written."""
    for key in sorted(flat.ports):
        port = flat.ports[key]
        if port.right is None or port.both or len(port.items) != 1 or len(port.right) != 1:
            continue
        hold = flat._in_hold if port.dir == "in" else flat._out_hold
        nets = []
        for item in (port.items[0], port.right[0]):
            outer = [n for n in hold.get((key, item), []) if not n.startswith(port.scope + "/")]
            nets.append(outer[0] if len(outer) == 1 else None)
        a, b = nets
        if a is None or b is None or a == b:
            continue
        if any(n in flat.lane_of or n not in flat.nets or n in flat.insts or
               flat.nets[n].width > 1 or len(flat.nets[n].items) != 1 or
               any(cat.is_fluid(i) for i in flat.nets[n].items) for n in (a, b)):
            continue
        pk = f"{a}|{b}"
        flat.pairs[pk] = (a, b)
        flat.lane_of[a] = flat.lane_of[b] = pk


def _width_checks(flat, cat):
    """``width 2`` (2.0.54) where it can't give a second lane: a fluid's net (pipes have no
    lanes), and a net with one driver whose belt carries one lane (anything but a two-sided
    MinerRow, which fills both): the router side-loads two drivers onto the two lanes."""
    for name, net in flat.nets.items():
        if net.lane is not None:
            why = ("it is a fluid's: pipes have no lanes"
                   if net.items and all(cat.is_fluid(i) for i in net.items) else
                   "it is width 2, on both lanes" if net.width > 1 else
                   f"it shares a belt ({flat.lane_of[name]}), whose lanes say where it goes"
                   if name in flat.lane_of else None)
            if why:
                flat.checks.append(Check("width", f"net {name}", f"lane {net.lane} does "
                                         f"nothing: {why}", net.line))
        if net.width < 2:
            continue
        if net.items and all(cat.is_fluid(i) for i in net.items):
            flat.checks.append(Check("width", f"net {name}", f"width {net.width} on a fluid's "
                                     "net: pipes have no lanes, so it is one pipe", net.line))
            continue
        if net.width > 2:
            # 2.1: a wide net is width/2 belts side by side, its drivers and consumers dealt out
            belts = net.width // 2
            for side, ends in (("drivers", net.drivers), ("consumers", net.consumers)):
                have = len({flat.machines[k].inst if k in flat.machines else k
                            for k, _i in ends})
                if have < belts:
                    flat.checks.append(Check(
                        "width", f"net {name}",
                        f"width {net.width} is {belts} belts side by side, and it has {have} "
                        f"{side[:-1] if have == 1 else side}: the router deals them out, one belt "
                        f"each at least, so it needs {belts}", net.line))
            continue
        insts = list(dict.fromkeys(flat.machines[k].inst for k, _i in net.drivers))
        if len(insts) == 1:
            inst = flat.insts.get(insts[0])
            two_sided = inst is not None and core_of(inst) == "MinerRow" and len(inst.machines) > 1
            if not two_sided:
                flat.checks.append(Check(
                    "width", f"net {name}",
                    f"width 2 with one driver, {insts[0]}: its belt brings one lane, and the "
                    "second lane comes from a second driver side-loaded onto it", net.line))


def _items_text(items):
    if items is None:
        return "item"
    return items[0] if len(items) == 1 else "{" + ", ".join(items) + "}"


def _group(rows):
    """[(inst, item, machine)] -> [(inst, item, count)] in first-seen order."""
    out = {}
    for inst, item, _m in rows:
        out[(inst, item)] = out.get((inst, item), 0) + 1
    return [(inst, item, n) for (inst, item), n in out.items()]


#: chests that put what reaches them into the logistic network
_STORES = ("provider", "storage")
#: the 1.1 check lines about limited production, and 2.0.1's UNDERFED (a rate fact about the
#: design): advice that tests of other things, and fk stage check's list, leave out
ADVICE = ("CLOG", "no-limit", "SHARED_SINK", "SUSHI_LIMIT", "short-feed", "could-pair",
          "limited")


def advice(flat):
    """``fk check``'s advice voice (2.1), lines that are not checks of the design and leave the
    netlist's own check lines as they were: a bare ``limited`` on a sushi net is asked what
    limits it (``limited-why``)."""
    out = []
    chain = getattr(flat, "chain", set())
    for name, net in flat.nets.items():
        ends = net.drivers + net.consumers
        if ends and all(k in chain for k, _i in ends):
            continue            # a Stack's own net from one cell to the next: no source line
        if net.sushi and net.limited and not getattr(net, "why", None) \
                and net.drivers and net.consumers:
            out.append(Check("limited-why", f"net {name}",
                             "limited says something limits what goes on it: say what, so a "
                             "reader sees it where CLOG would be (`net "
                             f"{name.rsplit('/', 1)[-1]} limited \"the end buffer gates the "
                             "sources\":`)", net.line))
    out += _row_cells(flat)
    for name, (item, amount, rate, insts) in sorted(getattr(flat, "gates", {}).items()):
        if insts:
            out.append(Check("gate-auto", f"net {name}",
                             f"auto holds {item} below {amount}: {GATE_SECONDS} s of the "
                             f"{rate:g}/s its loop's machines take ({', '.join(insts)}), and at "
                             f"least {GATE_CRAFTS} crafts each", flat.nets[name].line))
    return out


#: the library cell that replaces each row kind (2.1, PLAN-2.1 "Rows in 2.1"), and how to write it
ROW_CELLS = {
    "SmelterArray": ("SmelterPair", "Stack<cell=SmelterPair, n={half}, plate={plate}, ore={ore}>"
                                    " (two furnaces a cell)"),
    "SmelterRow": ("StoneFurnaceCell", "Stack<cell=StoneFurnaceCell, n={n}, plate={plate}, "
                                       "ore={ore}>"),
    "AsmRow": ("AsmCell", "Stack<cell=AsmCell, n={n}, recipe={recipe}, ins={ins}, out={out}> (two "
                          "ingredients; AsmCell1 for one)"),
    "LabRow": ("LabCell", "Stack<cell=LabCell, n={n}, packs=A|B>, or a Stack of LabColumns for a "
                          "field"),
    "CircuitStack": ("CircuitCell", "Stack<cell=CircuitCell, n={quarter}> (four circuit "
                                    "assemblers a cell)"),
}


def _row_cells(flat):
    """``row-cell`` (2.1, the library): a row the committed layout hasn't laid yet could be a Stack
    of a library cell from the kickoff template's cells.fk. A row already built stays (rows are
    frozen, and a cell's footprint differs), so it gets no line."""
    lay = getattr(flat, "_layout", None)
    built = set(getattr(lay, "insts", {}) or {}) if lay is not None else set()
    out = []
    for path, inst in sorted(flat.insts.items()):
        kind = getattr(inst, "core", None) or inst.kind
        if kind not in ROW_CELLS or path in built:
            continue
        p = params_of(inst)
        ms = [flat.machines[k] for k in inst.machines if k in flat.machines]
        n = len(ms) or int(p.get("n") or 1)
        recipe = next((m.recipe for m in ms if m.recipe), p.get("recipe"))
        ore = plate = out_item = None
        if recipe:
            plate = recipe
            ore = next(iter(ms[0].needs), None) if ms and ms[0].needs else p.get("ore")
            out_item = next(iter(ms[0].makes), recipe) if ms else recipe
        machine = ms[0].prototype if ms else None
        cell, how = ROW_CELLS[kind]
        ins = "A|B"
        if kind == "SmelterRow" and machine == "electric-furnace":
            cell, how = "FurnaceCell", "Stack<cell=FurnaceCell, n={n}, plate={plate}, ore={ore}> " \
                                       "(or FurnacePair, two a cell)"
        elif kind == "AsmRow" and ms:
            got = [i for i in ms[0].needs]
            if len(got) == 1:
                cell, how = "AsmCell1", "Stack<cell=AsmCell1, n={n}, recipe={recipe}, ins={ins}, " \
                                        "out={out}>"
            elif len(got) != 2:
                continue            # no library cell takes three ingredients: a cell of your own
            ins = "|".join(got)
            how = how.replace(" (two ingredients; AsmCell1 for one)", "")
        elif kind == "LabRow":
            packs = sorted({i for n2 in flat.nets.values() for k, i in n2.consumers
                            if k in inst.machines})
            if len(packs) == 2:
                ins = "|".join(packs)
            how = how.replace("packs=A|B", "packs={ins}")
        text = how.format(n=n, half=(n + 1) // 2, quarter=(n + 3) // 4, plate=plate, ore=ore,
                          recipe=recipe, out=out_item, ins=ins)
        out.append(Check("row-cell", path,
                         f"a new {kind} could be {text}, a Stack of the library's {cell} "
                         "(import it from the kickoff template's game\\cells.fk). Rows still "
                         "build, and new lines are Stacks of cells", inst.line))
    return out
#: chests that take any item that reaches them
_TAKE_ALL = ("provider", "storage", "buffer", "plain")


#: templates that already share their own feed lanes, two nets side-loaded onto one belt
#: (``fragments.smelter_array``, ``fragments.circuit_stack``): they need their two nets apart, and
#: pairing them hands the fragment one belt where it needs two
_SELF_PAIRED = frozenset(("SmelterArray", "CircuitStack"))


def _self_paired(flat, key):
    """Whether machine ``key`` is a template that side-loads two nets onto its own feed belts."""
    inst = flat.insts.get(flat.machines[key].inst)
    return getattr(inst, "kind", None) in _SELF_PAIRED


def _unlimited(m):
    return m.sink and m.extra.get("role") in _STORES and m.extra.get("limit") is None


def _clog_checks(flat, in_hold, fed):
    """1.1 (Rory, 2026-10-20): in the game a sink takes what reaches it first, never a share. The
    lines say what can happen, not how to build: mixed belts and chests are fine where production
    is limited (a one-off crafter on single inserters and a requester chest is).

    - ``CLOG``: a sushi net, or an unlimited chest fed two or more items by separate nets: an
      item that comes faster than it is used can fill the belt or the chest and stop the others.
      2.0 (Rory, 2026-10-21): no tool can tell in general whether a source is limited, so a sushi
      net's CLOG goes away in exactly two ways. (A) Its route is one inserter from the source
      straight into each sink, which takes only what the sink accepts: the route step clears the
      line when it lays one (``connect.one_inserter``); a belt or a second inserter in between
      loses that. (B) The source writes the net ``limited``. Nothing else: 1.1 counted a net that
      only requester chests drive as limited, and 1.7 a net into chests that take
      anything; both went, a requester refills what its belt takes and a full chest stops the
      belt.
    - ``no-limit``: provider or storage chests without ``limit=`` (one line per factory): what
      reaches them goes into the logistic network without end (the 0534 run's network held 4800
      iron and 5300 copper plates). 1.7 (Rory, 2026-10-21: a multi-item sink chest should
      generally have no limit; the limiting happens at the makers): a chest a mixed belt of
      makers feeds gets its own line, which says so, and none when its nets are ``limited``.
    - ``SHARED_SINK``: such a chest on a net with machines: whoever stands first on the belt gets
      its fill, so the machines after it can starve (the 0534 run's red science stood at 0%
      behind a copper chest).
    - ``SUSHI_LIMIT`` (1.7, Rory 2026-10-21: a chest slot limit doesn't limit a sushi net): a
      ``limit=`` on a chest that a mixed belt of makers feeds. When the chest holds that much the
      belt backs up with every item on it, so the limit only brings the stop sooner.

    A net written ``limited`` (1.7) says the source limits each of its makers: no ``CLOG`` and
    no ``no-limit`` for it. It only silences them; no tool can tell whether a source is limited.

    2.1 (Rory, 2026-10-31: no sushi clog model): ``limited "..."`` says what limits it, and that
    string is the ``limited`` line where ``CLOG`` would have been, so a reader sees the mechanism.
    A bare ``limited`` builds as before, with no line of its own (old files check unchanged);
    ``fk check``'s advice (``advice``) asks for the string.
    A buffer chest a limited sushi net fills draws no ``SUSHI_LIMIT``: the limit is the design's.
    """
    sushi_in = {}                          # chest key -> the mixed nets of makers it takes
    for name, net in flat.nets.items():
        made = any(not flat.machines[k].extra.get("port")
                   and flat.machines[k].extra.get("role") != "requester" for k, _i in net.drivers)
        if net.limited and getattr(net, "why", None):
            flat.checks.append(Check("limited", f"net {name}", net.why, net.line))
        if net.sushi and net.drivers and net.consumers and not net.limited:
            flat.checks.append(Check(
                "CLOG", f"net {name}",
                f"{', '.join(net.items)} share one belt: one that comes faster than it is used can "
                "fill it and stop the others. Not where one inserter takes from the source "
                "straight into each sink (the route step says so when it lays that), or where "
                "your design limits what goes on it (`net NAME limited:`). `net "
                f"{name.rsplit('/', 1)[-1]} split:` gives each item a lane of its own, loaded by "
                "its own filtered inserter", net.line))
        if not (net.sushi and made):
            continue
        for k, _i in net.consumers:
            m = flat.machines[k]
            if m.sink and m.extra.get("role") in _TAKE_ALL:
                sushi_in.setdefault(k, set()).add(name)
    for key in sorted(sushi_in, key=lambda k: flat.machines[k].inst):
        m = flat.machines[key]
        if m.extra.get("buffer") and all(flat.nets[n].limited for n in sushi_in[key]):
            continue
        if m.extra.get("limit") is not None:
            nets = sorted(sushi_in[key])
            flat.checks.append(Check(
                "SUSHI_LIMIT", m.inst,
                f"limit={m.extra['limit']} on the mixed belt of net {', '.join(nets)}: when the "
                "chest holds that much, the belt backs up with every item on it. A chest of many "
                "items works best unlimited, with the limit at each maker",
                flat.nets[nets[0]].line))
    items = {}
    for (key, item), nets in in_hold.items():
        items.setdefault(key, {})[item] = nets
    mixed = set()
    for key, got in items.items():
        m = flat.machines[key]
        nets = sorted({n for ns in got.values() for n in ns})
        if _unlimited(m) and m.takes is None and len(got) > 1 and len(nets) > 1:
            mixed.add(key)
            flat.checks.append(Check(
                "CLOG", m.inst,
                f"takes {', '.join(sorted(got))} from nets {', '.join(nets)} without a limit: "
                "whichever comes faster can fill it and stop the others. A limit= keeps what feeds "
                "it to what is used"))
    open_ = sorted({m.inst for key, m in flat.machines.items()
                    if _unlimited(m) and key in fed and key not in mixed and key not in sushi_in})
    sushi_open = sorted({flat.machines[key].inst for key, nets in sushi_in.items()
                         if _unlimited(flat.machines[key]) and key not in mixed
                         and not all(flat.nets[n].limited for n in nets)})
    if sushi_open:
        flat.checks.append(Check(
            "no-limit", ", ".join(sushi_open),
            "a mixed belt of makers fills " + ("them" if len(sushi_open) > 1 else "it") + " without "
            "end, unless each maker is limited: what one makes faster than it is taken fills the "
            "chest, and then the belt stops for the others too. A slot limit only brings that sooner "
            "(SUSHI_LIMIT); `net NAME limited:` says the source limits the makers"))
    if open_:
        flat.checks.append(Check(
            "no-limit", ", ".join(open_),
            "no limit=: what reaches " + ("them" if len(open_) > 1 else "it") + " goes into the "
            "logistic network without end. A limit= stops what feeds a chest once it holds that "
            "much"))
    for name, net in flat.nets.items():
        chests = sorted({flat.machines[k].inst for k, _i in net.consumers
                         if _unlimited(flat.machines[k])})
        machines = sorted({flat.machines[k].inst for k, _i in net.consumers
                           if not flat.machines[k].sink
                           and not flat.machines[k].extra.get("port")})
        if chests and machines:
            flat.checks.append(Check(
                "SHARED_SINK", f"net {name}",
                f"{', '.join(chests)} has no limit= and shares the net with "
                f"{', '.join(machines)}: whoever stands first on the belt gets its fill, so the "
                "machines can starve. A limit=, or a net of its own, avoids it", net.line))


def _lane_checks(flat, cat):
    """2.0.17 (Rory, 2026-10-25): two single-item nets feeding the same machine set on belts of
    their own waste half of each belt; one belt carries both, one lane each (``lanes A | B``).
    The 0928 player laid a smelter's ore and its coal on two belts, a lane each, and the build said
    nothing: the lane router existed, the design never reached for it. Only pairs with a known,
    finite rate that fits a lane (a requester without ``rate=`` is unbounded, and closing it to one
    lane would throttle it), and only nets not already paired.

    2.0.50 (Rory, 2026-10-27): it is about the *nets*, not about who drives them. 2.0.17 named only
    pairs a chest drove, because pairing a machine's product with a net of its own puts that
    product on two nets; but the waste is the same whoever makes the item, and the runs' lab rows
    (red packs on one belt, green packs on another) were the case it kept quiet. So a pair of
    single-item nets into the same machines is named machine-driven or not: pair the nets, and the
    machine stays on the one net it makes. What stays out is a pair that feeds chests only (two
    products into one provider chest are not a run's feed, and one belt of two products buys
    nothing but a mixed chest)."""
    from .lanes import lane_rate
    cap = lane_rate(cat, "transport-belt")
    by_consumers = {}
    for name, net in flat.nets.items():
        if name in flat.lane_of or net.sushi or len(net.items) != 1:
            continue
        item = net.items[0]
        if cat.is_fluid(item) or item in CONTACT or not net.drivers or not net.consumers:
            continue
        consumers = frozenset(k for k, _i in net.consumers)
        if all(cat.kind(flat.machines[k].prototype) in ("logistic-container", "container")
               for k in consumers):
            continue                  # a chest takes both: nothing is fed, nothing is wasted
        if any(_self_paired(flat, k) for k in consumers):
            continue                  # it shares the lanes of its own feed belts already
        rate = 0.0
        for key, got in net.drivers:
            m = flat.machines[key]
            if m.source:
                rate = None
                break
            rate += float(m.makes.get(got, 0.0))
        if rate is None or rate > cap:
            continue
        by_consumers.setdefault(consumers, []).append((name, item, rate))
    for consumers, nets in sorted(by_consumers.items(), key=lambda kv: sorted(n for n, _i, _r in kv[1])):
        nets.sort()
        first = nets[0]
        second = next((n for n in nets[1:] if n[1] != first[1]), None)
        if second is None:
            continue
        # sorted, never a frozenset's own order: the same design must give the same line in every
        # process (the elaboration baseline is a hash of it)
        insts = sorted({flat.machines[k].inst for k in consumers})
        where = ", ".join(insts)
        if len(consumers) > 1:
            where += f" ({len(consumers)} machines)"
        flat.checks.append(Check(
            "could-pair", f"net {first[0]} and {second[0]}",
            f"both feed {where} on belts of their own, a lane each: one belt carries both "
            f"(`lanes {first[0]} | {second[0]}`), one lane each, and each machine takes from both. "
            "Two half-empty belts otherwise", flat.nets[first[0]].line))


def _port_checks(flat):
    """``port`` (2.0.28, lane-level in 2.0.29; PLAN-MODULES), warn and never gate: a port no net
    uses on a side, a use against its direction, a declared item nothing carries, an item on one
    side only, a tile off its module's frame, and two ports on one lane of one tile.

    A port's nets are the one written inside its module (scope = the instance path) and the one
    written outside that names it; an ``in`` port is fed from outside and feeds inside, an ``out``
    port the other way round. A pair (``A | B``) is two lanes of one belt end: two ports on one tile
    is the point, two on one lane is the collision.
    """
    outside_legal = {"in": ("consumers",), "out": ("drivers",), "inout": ("drivers", "consumers")}
    inside_legal = {"in": ("drivers",), "out": ("consumers",), "inout": ("drivers", "consumers")}
    named = getattr(flat, "_port_named", {})
    # a lane holds one port: a pair takes both lanes of its tile, a pinned one-lane port takes the
    # one it names; an unpinned lane is left to the build, so it cannot collide yet
    lanes_taken = {}                       # (scope, side, pos, lane) -> the port that owns it
    for key, port in flat.ports.items():
        for lane, _items in port.lanes():
            if port.pos is None or lane is None:
                continue
            at = (port.scope, port.side, port.pos, lane)
            other = lanes_taken.get(at)
            if other is not None:
                flat.checks.append(Check(
                    "port", f"port {key}",
                    f"@{port.side}{port.pos}{lane} is already {other}'s lane: one port a lane, "
                    "two lanes a tile", port.line))
            else:
                lanes_taken[at] = key
    for key, port in flat.ports.items():
        uses = []
        for name, net in flat.nets.items():
            for side, rows in (("drivers", net.drivers), ("consumers", net.consumers)):
                for k, item in rows:
                    if k == key and item in port.all_items:
                        uses.append((name, side, item))
        inside = [u for u in uses if flat.nets[u[0]].scope == port.scope]
        outside = [u for u in uses if flat.nets[u[0]].scope != port.scope]
        named_in = {n for n in named.get(key, ()) if flat.nets[n].scope == port.scope}
        named_out = {n for n in named.get(key, ()) if flat.nets[n].scope != port.scope}
        if not uses and not named.get(key):
            flat.checks.append(Check("port", f"port {key}",
                                     f"no net names it: {port.dir} {_port_items(port)}", port.line))
            continue
        if not inside and not named_in:
            flat.checks.append(Check("port", f"port {key}",
                                     f"no net inside {port.module} uses it, so nothing carries "
                                     f"{_port_items(port)} through it", port.line))
        if not outside and not named_out and key not in flat.stack_ends:
            flat.checks.append(Check("port", f"port {key}",
                                     f"no net outside {port.scope} uses it: the interface is "
                                     "declared but not wired yet", port.line))
        bad = []
        for name, side, _item in uses:
            own = flat.nets[name].scope == port.scope
            legal = inside_legal if own else outside_legal
            if side not in legal[port.dir]:
                bad.append(f"net {name} {'drives' if side == 'drivers' else 'takes from'} it "
                           f"{'inside' if own else 'outside'}")
        if bad:
            flat.checks.append(Check("port", f"port {key}",
                                     f"used against its direction ({port.dir}): " + "; ".join(bad),
                                     port.line))
        carried = {item for _n, _s, item in uses}
        declared = list(dict.fromkeys(port.all_items))     # one item twice is both lanes
        missing = [i for i in declared if i not in carried]
        if missing:
            flat.checks.append(Check("port", f"port {key}",
                                     f"nothing carries {', '.join(missing)} through it", port.line))
        # an item on one side and not the other is a hole in the interface, not a dead port: the
        # outside must bring what the inside takes, and take what the inside brings
        inside_items = {item for _n, _s, item in inside}
        outside_items = {item for _n, _s, item in outside}
        if inside and outside:
            for item in declared:
                if item in inside_items and item not in outside_items and not named_out:
                    flat.checks.append(Check("port", f"port {key}",
                                             f"nothing outside brings {item} to it", port.line))
                elif item in outside_items and item not in inside_items and not named_in:
                    flat.checks.append(Check("port", f"port {key}",
                                             f"nothing inside {port.module} takes {item} from it",
                                             port.line))
        if port.pos is not None and port.size is not None:
            w, h = port.size
            along = w if port.side in "ns" else h
            if not 0 <= port.pos < along:
                flat.checks.append(Check(
                    "port", f"port {key}",
                    f"@{port.side}{port.pos} is off the module's frame: {port.module} is "
                    f"{w}x{h}, so its {port.side} edge runs 0..{along - 1}", port.line))
    # 2.0.41 (PLAN-GLOBAL phase A): a committed port tile whose port is gone
    for key in sorted(getattr(flat, "port_records", ())):
        if key not in flat.ports:
            flat.checks.append(Check(
                "port", f"port {key}",
                "the committed .fkp records its tile, but this design declares no such port: "
                "drop the port line"))


def _endpoint_checks(flat, cat):
    out_hold, in_hold = flat._out_hold, flat._in_hold
    for side, hold in (("out", out_hold), ("in", in_hold)):
        for (key, item), nets in hold.items():
            # 2.0.58: a single machine takes an item from several nets, a role each (a circuit
            # assembler handed cable by two cable assemblers): fine while each of those nets
            # carries that item alone. One product on two nets is still a line: where it goes is
            # the question
            # 2.1: a single machine is one whose kind resolves to a core single (templates.SINGLE)
            single = core_of(flat.insts.get(flat.machines[key].inst)
                             or flat.machines[key]) in SINGLE
            if side == "in" and len(nets) > 1 and single and \
                    all(flat.nets[n].items == [item] for n in nets if n in flat.nets):
                continue
            if len(nets) > 1:
                flat.checks.append(Check("two-nets", key, f"{item} ({side}) is on {len(nets)} "
                                         "nets: " + ", ".join(nets) + f". Name each net's items "
                                         f"(`net {nets[0]} items {item}: ...`) so one carries it"))
    fed = {key for key, _item in in_hold}
    unfed, unconsumed = [], []
    for key, m in flat.machines.items():
        if m.extra.get("port"):
            continue                    # a bus port: one no net names is a spare, not a fault
        for item in m.needs:
            if (key, item) not in in_hold:
                unfed.append((m.inst, item, key))
        # a roboport with no robot net is a coverage roboport: it takes robots when it needs them
        if m.sink and key not in fed and cat.kind(m.prototype) != "roboport":
            unfed.append((m.inst, "anything", key))
        for item in m.makes:
            if (key, item) not in out_hold and not m.extra.get("buffer"):
                unconsumed.append((m.inst, item, key))     # 2.1: a buffer may only fill
    for inst, item, n in _group(unfed):
        flat.checks.append(Check("no-feed", inst, f"{item} has no net"
                                 + (f" ({n} machines)" if n > 1 else "")))
    for inst, item, n in _group(unconsumed):
        flat.checks.append(Check("no-taker", inst, f"{item} goes on no net"
                                 + (f" ({n} machines)" if n > 1 else "")))
    by_key = None
    for name, net in flat.nets.items():
        if not net.sushi:
            continue
        if any(flat.machines[k].extra.get("buffer") for k, _i in net.drivers + net.consumers):
            continue        # 2.1: what no machine takes ends in (or goes round by) the buffer
        if any(flat.machines[k].extra.get("port") for k, _i in net.consumers):
            # 2.1, the library: a cell's belt that runs on into a loop with a buffer on it (a
            # MallCell's): what its machine doesn't take goes round
            by_key = by_key if by_key is not None else _by_key(flat)
            if any(flat.machines[k].extra.get("buffer") for n in loop_nets(flat, name, by_key)
                   for k, _i in flat.nets[n].drivers + flat.nets[n].consumers):
                continue
        rows = []
        for key, _item in net.consumers:
            m = flat.machines[key]
            if m.takes is None:
                continue
            missing = [i for i in net.items if i not in m.takes]
            if missing and all(r[2] != key for r in rows):
                rows.append((m.inst, ", ".join(missing), key))
        for inst, missing, n in _group(rows):
            flat.checks.append(Check("SUSHI_SINK", f"net {name}",
                                     f"{inst} does not take {missing}"
                                     + (f" ({n} machines)" if n > 1 else ""), net.line))
    _clog_checks(flat, in_hold, fed)
    _lane_checks(flat, cat)
    seen = set()
    driving = {key for key, _item in out_hold}
    for key, m in flat.machines.items():
        if m.source and m.inst not in seen and key in driving and not m.extra.get("buffer"):
            seen.add(m.inst)
            item = next(iter(m.makes))
            nets = out_hold.get((key, item)) or []
            pairs = [(k, i) for n in nets for k, i in flat.nets[n].consumers if i == item]
            msg = (f"{item} from the logistic network has no rate=: everything downstream of it "
                   "is unbounded")
            if pairs:
                take = sum(flat.machines[k].needs.get(item, 0.0) for k, _i in pairs)
                n = len(pairs)
                msg += (f". The {n} machine{'s' if n > 1 else ''} on it "
                        f"take{'s' if n == 1 else ''} {take:g}/s: `fk stock` says what the network "
                        "holds, so compare that with this need before you size it")
            flat.checks.append(Check("no-rate", m.inst, msg))
