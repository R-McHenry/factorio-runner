"""fkcore.circuits: circuits in the source (2.0): signals, wire nets, gated nets, conditions, the
makers' limits and the recipe guard, checked on paper and then laid onto a factory's layout.

Rory (2026-10-21): "circuit wiring in the source, with all circuit and logic conditions in the .fk
code, inserter limits included", and "Recipe-switch smarts (inserters that don't carry the wrong
item) belong in circuits in the game, not in scripted toolkit actions". The draft is
drafts\\reactor-1.1\\CIRCUITS-1.1.md, with fnet's rules (CIRCUITS-ROADMAP.md): a wire net is one
colour; a signal is declared, pinned (``signal temp = signal-T``) or left to the build (``signal
x``: the first free virtual signal of fnet's auto pool, at normal quality); checks warn, never gate.

What the source says, and what the layout gets:

- ``wire W red: drivers -> consumers``: a net of signals. A driver is an instance that puts
  signals on it (a chest its contents, ``Reactor<read=temperature, signal=S>`` its temperature,
  ``Constant<signals=...>`` its signals, a crafter with ``guard`` its ingredients) or a net (one of
  its belts reads its contents, ``read=hold``). A consumer is an instance with a ``when`` on W, or
  a gated net. A ``when`` that reads W adds its net or instance to W's consumers.
- ``net N: ... when W.S < 550``: a gated net. Every inserter and pump that carries N (the route's
  and its rows' own) gets the condition (``if=S<550``) and joins W. ``when network.S < 100`` is a
  logistic condition (``logistic=S<100``, no wire); ``when I.S < 100`` reads instance I on a red
  wire of its own, ``I.signals``.
- ``x = Assembler<...> when ...``: the machine's own enable condition, the same three ways.
- ``set SEL: attr=value, ...`` (2.0.27, Rory 2026-10-25): a property on the object a selector
  names. ``NAME`` the instance's machines, ``NAME.out`` / ``NAME.in`` its hands, ``-> NAME`` the
  hands whose sink is that instance. Properties are the codec's ``if`` / ``logistic`` / ``filter`` /
  ``read`` / ``signal`` / ``signals``. This replaced the ``keep=`` / ``fill=`` macros: they chose
  hands by where they pointed (two output hands on a shared belt both took one number, and the belt
  one held a consumer row back), which is what the run hit on 2026-10-25. A ``set`` reports the
  hands it hit and a selector that matches nothing is a ``no-match`` line, never a silent no-op.
- ``guard`` on a crafter: it reads its recipe's ingredients (``read=ingredients``) onto a red wire
  to every inserter that feeds it, which sets its filters from them (``filter=circuit``). A
  recipe changed in the game changes what they carry, with no script and no new paste of them.

Open circuits (2.1, PLAN-2.1 "Circuits that reach any building", Rory 2026-10-31):

- **Raw settings.** ``cb.FIELD=VALUE`` (a field of the entity's control behaviour) and
  ``bp.FIELD=VALUE`` (any other blueprint field), as a ``set`` property or an instance's or kind's
  parameter, so a building the toolkit never modelled can be set up from source. The value is
  written as the game's blueprint has it, with two conveniences: a string in a field that holds a
  signal (``*_signal``, ``*signal_id``, ``signal``, ``icon``) becomes the game's SignalID, typed
  from the game's signal list, and a string in a field that holds a condition (``*condition``) is
  ``S<N`` / ``S>=T`` as ``if=`` takes it. An object is taken as written. The rate model takes a
  raw condition as always open, as it does a ``when`` with no ``cycle``.
- **Signals from the game** (``fkcore.facts``): every item, fluid, virtual signal, recipe, entity,
  quality, space location and asteroid chunk the game puts on a wire. A signal that never goes out
  on a wire (``signal-each``, the parameters, ...) is refused where it is written (a
  ``Constant``, a reactor's reading, a raw output signal); the parameters and ``signal-unknown``
  anywhere.
- **Any building with a connector** can be a wire's end; one without (a pipe, a beacon) is a
  ``no-wire`` line.
- **Wires carried on poles.** The wires of a wire net are a minimum spanning tree over its
  entities, centre to centre. An edge longer than both ends reach (9 tiles; a big pole 32) hops
  along the layout's poles that no other net of its colour uses, with a pole added on a free tile
  where there is a gap (``circuit-poles/p<k>``, the power cover's pole), the fewest new poles
  first. Only when no chain exists is it a ``wire-too-far`` line.

``behave`` blocks keep their lines: a ``require`` with
numbers from the catalog is checked (``require-not-met``), a gated net with no ``cycle`` on its wire is a
``gate-open`` line (the rate model takes it as always open), ``expect`` lines are reported.

:func:`resolve` (elaborate, on paper) -> :class:`Circuit` and its check lines; :func:`lay` (the
build, before the floorplan composes) -> the layout with its circuit settings and ``wire`` / ``w``
records, laid again from the source on every build. Pure, stdlib only.
"""
from __future__ import annotations

import heapq
import json
import math
import os
import re
from dataclasses import dataclass, field

from . import codec
from . import facts
from . import fkp as fkpmod
from . import lang

__all__ = ["Circuit", "WireDef", "CondDef", "resolve", "lay", "NON_TRANSMITTING",
           "auto_pool", "FILTER_HANDS"]

_WILDCARDS = frozenset({"signal-each", "signal-everything", "signal-anything",
                        "signal-any-quality"})
#: every signal the game takes and never puts on a wire (2.1: the game's list, ``facts.json``)
NEVER_SENT = facts.never_sent()
#: those of them no condition can use either: the parameters and ``signal-unknown`` (a wildcard
#: reads, it only never goes out)
NON_TRANSMITTING = frozenset(NEVER_SENT - _WILDCARDS)
#: the owner of the poles a long wire adds where it has a gap (2.1), and its cover record's kind
POLE_OWNER = "circuit-poles"
#: tiles a long wire's chain may stray outside the box of its two ends
HOP_PAD = 6
#: inserters that can filter (2.0: the fast and bulk inserters took over the filter ones)
FILTER_HANDS = frozenset({"fast-inserter", "bulk-inserter", "stack-inserter"})
#: entity types a condition can enable
_ENABLE = frozenset({"inserter", "pump", "offshore-pump", "assembling-machine", "furnace",
                     "mining-drill", "lamp", "transport-belt", "power-switch"})
#: what a gated net's route enables: its inserters and pumps
_GATED = frozenset({"inserter", "pump"})
#: catalog facts a ``require`` line may read (2.0 prototype values)
FACTS = {"heat-exchanger": {"min_temperature": 500.0, "max_temperature": 500.0},
         "nuclear-reactor": {"max_temperature": 1000.0}}
#: an implicit wire's name: what instance I puts out, read by a ``when I.S``, ``fill`` or ``guard``
IMPLICIT = "{}.signals"
_CHEST_ROLES = ("provider", "storage", "buffer", "plain")
#: what a ``set`` statement may write on an object (2.0.27): the codec's circuit
#: attributes, nothing else
PROP_ATTRS = ("if", "logistic", "filter", "read", "signal", "signals")
#: the combinators with an input and an output side (2.1: ``Decider``, ``Arithmetic``)
COMBINATORS = frozenset({"decider-combinator", "arithmetic-combinator"})
#: what has an input and an output side (2.1.31: a captured selector combinator is a wire end)
SIDED = COMBINATORS | {"selector-combinator"}
#: the wildcards as a combinator string may write them
_EACH = {"each": "signal-each", "anything": "signal-anything", "everything": "signal-everything"}
#: an arithmetic combinator's operations, as the game names them
OPERATIONS = ("*", "/", "+", "-", "%", "^", "<<", ">>", "AND", "OR", "XOR")
_CTOKEN = re.compile(r"\s*(?:(?P<p>[()])|(?P<op><<|>>|<=|>=|!=|==|[<>=*/+%^-](?![0-9]))"
                     r"|(?P<num>-?\d+)(?![\w.-])|(?P<word>[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)?))")


def _ctokens(text):
    out, i = [], 0
    while i < len(text):
        if text[i:].strip() == "":
            break
        m = _CTOKEN.match(text, i)
        if not m or m.end() == i:
            raise ValueError(f"{text!r}: can't read {text[i:].strip()!r}")
        kind = m.lastgroup
        out.append((kind, int(m.group(kind)) if kind == "num" else m.group(kind)))
        i = m.end()
    return out


def parse_decider(text):
    """A decider's condition string -> [[(left, op, right)]]: ``or`` between groups of ``and``,
    as 2.0's decider takes it (``and`` binds first). A group may stand in parentheses; an ``or``
    inside them is refused (the game has no such grouping). ``left`` is a word (``red.S``,
    ``green.S``, ``S``), ``right`` a word or a whole number. Raises ValueError."""
    toks = _ctokens(text)
    groups, group, i, depth = [], [], 0, 0
    if not toks:
        raise ValueError("an empty condition")
    while i < len(toks):
        kind, v = toks[i]
        if kind == "p" and v == "(":
            if depth or group:
                raise ValueError(f"{text!r}: parentheses hold one group of and, after an or")
            depth, i = 1, i + 1
            continue
        if kind != "word" or v in ("and", "or"):
            raise ValueError(f"{text!r}: expected a signal, got {v!r}")
        if i + 2 >= len(toks):
            raise ValueError(f"{text!r}: a comparison is S < N or S < T")
        (k1, op), (k2, right) = toks[i + 1], toks[i + 2]
        if k1 != "op" or op not in ("<", ">", "<=", ">=", "=", "==", "!="):
            raise ValueError(f"{text!r}: expected a comparison after {v}, got {op!r}")
        if k2 not in ("num", "word") or right in ("and", "or"):
            raise ValueError(f"{text!r}: expected a number or a signal after {v} {op}")
        group.append((v, "=" if op == "==" else op, right))
        i += 3
        if i < len(toks) and toks[i] == ("p", ")"):
            if not depth:
                raise ValueError(f"{text!r}: a ) with no (")
            depth, i = 0, i + 1
        if i == len(toks):
            break
        kind, v = toks[i]
        if kind != "word" or v not in ("and", "or"):
            raise ValueError(f"{text!r}: expected and or or, got {v!r}")
        if v == "or":
            if depth:
                raise ValueError(f"{text!r}: 2.0's decider takes or between groups of and; "
                                 "(A or B) and C is written A and C or B and C")
            groups.append(group)
            group = []
        i += 1
    if depth:
        raise ValueError(f"{text!r}: a ( is not closed")
    if not group or toks[-1] in (("word", "and"), ("word", "or")):
        raise ValueError(f"{text!r}: it ends with and or or")
    groups.append(group)
    return groups


def parse_outputs(text):
    """A combinator's ``out`` -> [(signal word, constant or None)]: ``S=1`` sends 1, ``S`` the
    input's count of S; several separated by commas."""
    out = []
    for part in [t.strip() for t in text.split(",")]:
        m = re.fullmatch(r"([A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)?)\s*(?:=\s*(-?\d+))?", part)
        if not m:
            raise ValueError(f'out="{text}": each output is S=N (a constant) or S (the input\'s '
                             'count of S)')
        out.append((m.group(1), None if m.group(2) is None else int(m.group(2))))
    return out


def parse_calc(text):
    """An arithmetic combinator's ``calc`` -> (left, operation, right), each side a word or a
    whole number: ``A * 2``, ``red.A + green.A``, ``each / 4``."""
    m = re.fullmatch(r"\s*(\S+)\s+(\*|/|\+|-|%|\^|<<|>>|AND|OR|XOR)\s+(\S+)\s*", text)
    if not m:
        raise ValueError(f'calc="{text}": one operation, A * 2 (spaces around it; one of '
                         f'{" ".join(OPERATIONS)})')
    sides = []
    for v in (m.group(1), m.group(3)):
        if re.fullmatch(r"-?\d+", v):
            sides.append(int(v))
        elif re.fullmatch(r"[A-Za-z_][\w-]*(?:\.[A-Za-z_][\w-]*)?", v):
            sides.append(v)
        else:
            raise ValueError(f'calc="{text}": {v!r} is no signal or whole number')
    return sides[0], m.group(2), sides[1]


def auto_pool():
    """fnet's auto pool, at normal quality: the virtual signals that are not wildcards, letters,
    digits or reserved, sorted."""
    letters = {f"signal-{c}" for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
    return [n for n in sorted(facts.signals().get("virtual") or ())
            if n not in _WILDCARDS and n not in letters and n not in NEVER_SENT]


@dataclass
class CondDef:
    kind: str                  # "wire" | "network"
    wire: str | None           # the wire net (an implicit one for I.S)
    signal: str                # the game signal
    op: str                    # a lang.COMPARATORS
    right: object              # int or a game signal (on the same wire)
    text: str                  # as written
    line: int

    def attr(self):
        """The .fkp setting: (``if`` | ``logistic``, ``S<N``)."""
        return ("logistic" if self.kind == "network" else "if",
                f"{self.signal}{self.op}{self.right}")


@dataclass
class WireDef:
    name: str
    colour: str
    drivers: list = field(default_factory=list)      # [("machine", key) | ("net", name)]
    consumers: list = field(default_factory=list)    # [("machine", key) | ("net", name)]
    line: int = 0
    implicit: bool = False
    signals: set | None = None   # what its drivers put on it, when every driver's is known


@dataclass
class Circuit:
    signals: dict = field(default_factory=dict)      # scoped name -> game signal
    autos: dict = field(default_factory=dict)        # scoped name -> game signal (the build's)
    wires: dict = field(default_factory=dict)        # name -> WireDef
    gates: dict = field(default_factory=dict)        # net -> CondDef
    enables: dict = field(default_factory=dict)      # instance path -> CondDef
    sets: list = field(default_factory=list)         # [(lang.Props, scope)] (2.0.27)
    guards: list = field(default_factory=list)       # [instance path]
    settings: dict = field(default_factory=dict)     # machine key -> {attr: value}
    emits: dict = field(default_factory=dict)        # machine key -> {signal} it sends (2.1)
    behaves: list = field(default_factory=list)      # [lang.Behave, with its scope]
    expects: list = field(default_factory=list)      # [lang.Expect]
    notes: list = field(default_factory=list)        # report lines from resolving

    @property
    def empty(self):
        return not (self.wires or self.gates or self.enables or self.sets or self.guards
                    or self.settings)


# -- on paper --------------------------------------------------------------------------------------
def _join(*parts):
    return "/".join(p for p in parts if p)


def _chain(scope):
    """``scope`` and every enclosing one, innermost first."""
    parts = scope.split("/") if scope else []
    return ["/".join(parts[:i]) for i in range(len(parts), -1, -1)]


def _known(name, cat):
    """A signal the game has (2.1: its own list, any type), or an item or fluid of the catalog."""
    return facts.is_signal(name) or cat.is_fluid(name) or cat.is_item(name)


class _Resolver:
    def __init__(self, flat, cat):
        from .elaborate import Check
        self.flat, self.cat, self.Check = flat, cat, Check
        self.c = Circuit()
        self.declared = {}                 # scoped name -> lang.Signal
        self.lit_proto = {}                # a Literal's wire end -> its entity (2.1.31)

    def check(self, code, where, msg, line=0):
        self.flat.checks.append(self.Check(code, where, msg, line))

    # signals
    def signals(self):
        taken = set()
        autos = []
        for sig, scope in self.flat.circuit_src.get("signals", ()):
            full = _join(scope, sig.name)
            if full in self.declared:
                self.check("no-signal", f"signal {full}", "declared twice", sig.line)
                continue
            self.declared[full] = sig
            if sig.game is None:
                autos.append(full)
                continue
            if not _known(sig.game, self.cat):
                self.check("no-signal", f"signal {full}", f"{sig.game} is no game signal (a virtual "
                           "signal like signal-T, an item or a fluid)", sig.line)
            elif sig.game in NON_TRANSMITTING:
                self.check("no-signal", f"signal {full}", f"{sig.game} never goes out on a wire "
                           "(the game takes it and sends nothing)", sig.line)
            self.c.signals[full] = sig.game
            taken.add(sig.game)
        pool = [n for n in auto_pool() if n not in taken]
        for k, full in enumerate(autos):
            if k >= len(pool):
                self.check("no-signal", f"signal {full}", "no free virtual signal left for it")
                continue
            self.c.signals[full] = self.c.autos[full] = pool[k]
        if self.c.autos:
            self.c.notes.append("signals the build picked: " + ", ".join(
                f"{n} = {g}" for n, g in sorted(self.c.autos.items())))

    def game(self, name, scope, where, line, written=False):
        """A signal as written -> its game name (a declared one, else the name itself).
        ``written``: the signal is put on a wire (a Constant's, a reading's), so a wildcard is
        refused too."""
        for s in _chain(scope):
            full = _join(s, name)
            if full in self.c.signals:
                got = self.c.signals[full]
                # its declaration was checked; a wildcard may be declared to read with
                if written and got in NEVER_SENT and got not in NON_TRANSMITTING:
                    self.check("no-signal", where, f"{name} ({got}) never goes out on a wire",
                               line)
                return got
        if not _known(name, self.cat):
            self.check("no-signal", where, f"{name} is no declared signal and no game signal",
                       line)
        elif name in NON_TRANSMITTING or (written and name in NEVER_SENT):
            self.check("no-signal", where, f"{name} never goes out on a wire", line)
        return name

    # wires
    def combinator(self, key):
        m = self.flat.machines.get(key)
        if m is None:
            return self.lit_proto.get(key) in SIDED
        return m.prototype in COMBINATORS

    def literal_end(self, parts, scope):
        """2.1.31: ``E2.pcomb``, a named entity of Literal E2 that is no machine of the netlist (a
        combinator, a chest, a pole): a wire end, its entity key. None when ``parts`` names none."""
        if len(parts) != 2:
            return None
        ends = getattr(self.flat, "_lit_ends", {})
        for s in _chain(scope):
            got = ends.get(_join(s, parts[0]), {}).get(parts[1])
            if got is not None:
                self.lit_proto[got[0]] = got[1]
                return got[0]
        return None

    def member(self, ref, scope, where, line, role="driver"):
        """A wire end as written -> [("machine", key), ...] or [("net", name)]; [] and a no-wire
        line when it names nothing. 2.1: a combinator's side, ``X.in`` or ``X.out``: a driver
        puts its signals on the wire from its output side, a consumer reads at its input side, so
        the side written must be the one its role uses."""
        lit = None if ref.star else self.literal_end(list(ref.parts), scope)
        if lit is not None:
            return [("machine", lit)]
        if len(ref.parts) >= 2 and ref.parts[-1] in ("in", "out") and not ref.star:
            lit = self.literal_end(list(ref.parts[:-1]), scope)
            keys = [lit] if lit is not None else \
                self.flat.resolve_ref(lang.Ref(ref.parts[:-1], False, line), scope)[1]
            if keys and all(self.combinator(k) for k in keys):
                want = "out" if role == "driver" else "in"
                if ref.parts[-1] != want:
                    self.check("no-wire", where, f"{ref}: a combinator reads a wire at its .in and "
                               f"drives one from its .out, so as a {role} it is "
                               f"{'.'.join(ref.parts[:-1])}.{want}", line)
                return [("machine", k) for k in keys]
        node, keys = self.flat.resolve_ref(ref, scope)
        if keys:
            return [("machine", k) for k in keys]
        if len(ref.parts) == 1:
            for s in _chain(scope):
                net = _join(s, ref.parts[0])
                if net in self.flat.nets:
                    return [("net", net)]
        self.check("no-wire", where, f"{ref} names no instance or net", line)
        return []

    def wires(self):
        for w, scope in self.flat.circuit_src.get("wires", ()):
            full = _join(scope, w.name)
            if full in self.c.wires:
                self.check("no-wire", f"wire {full}", "declared twice", w.line)
                continue
            d = WireDef(full, w.colour, line=w.line)
            for ref in w.drivers:
                d.drivers += self.member(ref, scope, f"wire {full}", w.line, "driver")
            for ref in w.consumers:
                d.consumers += self.member(ref, scope, f"wire {full}", w.line, "consumer")
            self.c.wires[full] = d

    def wire_named(self, name, scope):
        """A wire by its name in ``scope`` or an enclosing one; 2.1: a dotted name reaches a wire
        inside a module instance (``shedder.hold``)."""
        name = name.replace(".", "/")
        for s in _chain(scope):
            full = _join(s, name)
            if full in self.c.wires:
                return full
        return None

    def implicit(self, path, keys):
        name = IMPLICIT.format(path)
        if name not in self.c.wires:
            self.c.wires[name] = WireDef(name, "red", [("machine", k) for k in keys],
                                         implicit=True)
        return name

    # conditions
    def cond(self, cond, scope, consumer, where):
        """lang.Cond -> CondDef (None when it can't be read), joining ``consumer`` to its wire."""
        left = cond.left
        if left.source == ["network"]:
            kind, wire = "network", None
        else:
            wire = self.wire_named(".".join(left.source), scope)
            kind = "wire"
            if wire is None:
                node, keys = self.flat.resolve_ref(lang.Ref(left.source, False, cond.line), scope)
                if not keys:
                    self.check("no-signal", where, f"when {cond}: {'.'.join(left.source)} names no "
                               "wire, instance or network", cond.line)
                    return None
                wire = self.implicit(node.path if node is not None else keys[0], keys)
        sig = self.game(left.signal, scope, where, cond.line)
        right = cond.right
        if isinstance(right, lang.Operand):
            if right.source != left.source:
                self.check("no-signal", where, f"when {cond}: a condition compares two signals on "
                           "one wire", cond.line)
            right = self.game(right.signal, scope, where, cond.line)
        elif not isinstance(right, int):
            self.check("no-signal", where, f"when {cond}: the game compares with whole numbers",
                       cond.line)
            right = int(right)
        if kind == "network" and not self.cat.is_item(sig):
            self.check("no-signal", where, f"when {cond}: the logistic network counts items, and "
                       f"{sig} is none", cond.line)
        if wire is not None and consumer not in self.c.wires[wire].consumers:
            self.c.wires[wire].consumers.append(consumer)
        return CondDef(kind, wire, sig, cond.op, right, str(cond), cond.line)

    def conditions(self):
        for name, net in self.flat.nets.items():
            if net.when is not None:
                got = self.cond(net.when[0], net.when[1], ("net", name), f"net {name}")
                if got is not None:
                    self.c.gates[name] = got
        for path, inst in self.flat.insts.items():
            if inst.when is None:
                continue
            if inst.module is not None:
                self.check("no-signal", path, "a module instance has no condition of its own; "
                           "write when on its machines", inst.line)
                continue
            if not inst.machines:
                continue
            got = self.cond(inst.when[0], inst.when[1], ("machine", inst.machines[0]), path)
            if got is None:
                continue
            self.c.enables[path] = got
            if got.wire is not None:
                w = self.c.wires[got.wire]
                w.consumers += [("machine", k) for k in inst.machines[1:]
                                if ("machine", k) not in w.consumers]

    # settings, limits, guard
    def instance_settings(self):
        for path, inst in self.flat.insts.items():
            if inst.module is not None or not inst.machines:
                continue
            scope = path.rsplit("/", 1)[0] if "/" in path else ""
            # 2.1: a kind's instance reads as its core kind, with the kinds' defaults
            p = getattr(inst, "kparams", None) or inst.params
            core = getattr(inst, "core", None) or inst.kind
            raw = {k: v for k, v in p.items() if lang.is_raw(k)}
            if raw:
                # 2.1: an instance's (or its kind's) raw settings, as a set line on its machines
                self.c.sets.append((lang.Props(path, None, None, raw, inst.line, inline=True),
                                    ""))
            if core == "Reactor" and p.get("read") == "temperature":
                sig = self.game(str(p.get("signal", "signal-T")), scope, path, inst.line,
                                written=True)
                for key in inst.machines:
                    self.c.settings.setdefault(key, {}).update(read="temperature", signal=sig)
            if core in ("Decider", "Arithmetic"):
                got = (self.decider if core == "Decider" else self.arithmetic)(p, scope, path,
                                                                               inst.line)
                if got is not None:
                    field_, value, emits = got
                    self.c.sets.append((lang.Props(path, None, None, {f"cb.{field_}": lang.Raw(value)},
                                                   inst.line, inline=True), ""))
                    for key in inst.machines:
                        self.c.emits[key] = emits
            if core == "Entity" and p.get("read") is not None:
                # 2.1: an accumulator's charge (0-100) on its wire, its cb.output_signal
                sig = self.game(str(p["read"]), scope, path, inst.line, written=True)
                self.c.sets.append((lang.Props(path, None, None, {"cb.output_signal": sig},
                                               inst.line, inline=True), ""))
                for key in inst.machines:
                    self.c.emits[key] = {sig}
            if core == "Constant" and p.get("signals") is not None:
                rows = self.constant(str(p["signals"]), scope, path, inst.line)
                for key in inst.machines:
                    self.c.settings.setdefault(key, {})["signals"] = "|".join(
                        f"{s}:{n}" for s, n in rows)
            for kind in ("keep", "fill"):
                if p.get(kind) is None:
                    continue
                # 2.0.27 (Rory, 2026-10-25): keep= and fill= are shorthands now, lowered to the
                # ``set`` properties they always meant. The primitive is the property on a named
                # object; these two just spare writing it out. A fill= reads the chest its product
                # goes into (a red wire from that chest), and holds the machine's input hands on
                # the same wire, so the machine stops at most one craft past N - the gold standard
                # for limiting production, and no robots needed.
                from .elaborate import ElabError
                n = p[kind]
                if not isinstance(n, int) or n < 1:
                    raise ElabError(self.flat.path, inst.line,
                                    f"{path}: {kind}= is a whole number from 1, got {n!r}")
                made = next((i for k in inst.machines for i in self.flat.machines[k].makes), None)
                if made is None:
                    self.check("LIMIT", path, f"{kind}={n}: {inst.kind} makes no item to limit",
                               inst.line)
                    continue
                if kind == "keep":
                    self.c.sets.append((lang.Props(path, "out", None,
                                                   {"logistic": f"{made}<{n}"}, inst.line),
                                        "",))
                    continue
                chests = []
                for net in self.flat.nets.values():
                    if not any(k in inst.machines for k, _i in net.drivers):
                        continue
                    for k, _i in net.consumers:
                        if self.flat.machines[k].sink and self.flat.machines[k].inst not in chests:
                            chests.append(self.flat.machines[k].inst)
                if not chests:
                    self.check("LIMIT", path, f"fill={n}: no chest takes its product, so there is "
                               "no chest to count; keep= counts the logistic network", inst.line)
                    continue
                for chest in chests:
                    self.c.sets.append((lang.Props(path, "out", chest,
                                                   {"if": f"{made}<{n}"}, inst.line, chest),
                                        "",))
                    self.c.sets.append((lang.Props(path, "in", None,
                                                   {"if": f"{made}<{n}"}, inst.line, chest),
                                        "",))
            if p.get("guard") is not None:
                if p["guard"] is not True:
                    self.check("cannot-filter", path, "guard is a flag: write it bare, Assembler<recipe=x, "
                               "guard>", inst.line)
                recipes = {self.flat.machines[k].recipe for k in inst.machines}
                if recipes == {None}:
                    self.check("cannot-filter", path, f"guard: {inst.kind} has no recipe to read",
                               inst.line)
                    continue
                self.c.guards.append(path)
                self.implicit(path, inst.machines)
                for key in inst.machines:
                    self.c.settings.setdefault(key, {})["read"] = "ingredients"

    # combinators (2.1, power domains)
    def operand(self, word, scope, where, line, written=False):
        """``red.S``, ``green.S`` or ``S`` -> (game signal, networks or None for both)."""
        colour, dot, name = word.partition(".")
        if dot and colour in ("red", "green"):
            nets = {"red": colour == "red", "green": colour == "green"}
        else:
            name, nets = word, None
        name = _EACH.get(name, name)
        return self.game(name, scope, where, line, written=written), nets

    def decider(self, p, scope, where, line):
        """A Decider's ``if`` and ``out`` -> (``decider_conditions``, its value in the game's form
        as the game exports it, the signals it sends); None and a CIRCUIT line when they don't
        read."""
        text, out = str(p["if"]), str(p["out"])
        try:
            groups = parse_decider(text)
            outs = parse_outputs(out)
        except ValueError as exc:
            self.check("CIRCUIT", where, f"Decider: {exc}", line)
            return None
        conds = []
        for g, group in enumerate(groups):
            for k, (a, op, b) in enumerate(group):
                sig, nets = self.operand(a, scope, where, line)
                c = {"first_signal": sig}
                if op != "<":
                    c["comparator"] = codec.comparator_json(op)
                if isinstance(b, int):
                    if b != 0:
                        c["constant"] = b
                else:
                    sig2, nets2 = self.operand(b, scope, where, line)
                    c["second_signal"] = sig2
                    if nets2 is not None:
                        c["second_signal_networks"] = nets2
                if nets is not None:
                    c["first_signal_networks"] = nets
                if conds and k > 0:
                    c["compare_type"] = "and"
                conds.append(c)
        rows, emits = [], set()
        for name, value in outs:
            sig, nets = self.operand(name, scope, where, line, written=True)
            emits.add(sig)
            o = {"signal": sig}
            if value is not None:
                o["copy_count_from_input"] = False
                if value != 1:
                    o["constant"] = value
            if nets is not None:
                o["networks"] = nets
            rows.append(o)
        return "decider_conditions", {"conditions": conds, "outputs": rows}, emits

    def arithmetic(self, p, scope, where, line):
        """An Arithmetic's ``calc`` and ``out`` -> (``arithmetic_conditions``, its value, the
        signal it sends)."""
        try:
            a, op, b = parse_calc(str(p["calc"]))
            outs = parse_outputs(str(p["out"]))
            if len(outs) != 1 or outs[0][1] is not None:
                raise ValueError(f'out="{p["out"]}": an arithmetic combinator sends one signal, '
                                 'out="S"')
        except ValueError as exc:
            self.check("CIRCUIT", where, f"Arithmetic: {exc}", line)
            return None
        got = {}
        for side, v in (("first", a), ("second", b)):
            if isinstance(v, int):
                got[f"{side}_constant"] = v
                continue
            sig, nets = self.operand(v, scope, where, line)
            got[f"{side}_signal"] = sig
            if nets is not None:
                got[f"{side}_signal_networks"] = nets
        got["operation"] = op
        sig, _nets = self.operand(outs[0][0], scope, where, line, written=True)
        got["output_signal"] = sig
        return "arithmetic_conditions", got, {sig}

    def constant(self, text, scope, where, line):
        rows = []
        for part in [t.strip() for t in text.split(",") if t.strip()]:
            name, eq, count = part.partition("=")
            if not eq or not re.fullmatch(r"-?\d+", count.strip()):
                from .elaborate import ElabError
                raise ElabError(self.flat.path, line, f'{where}: signals="S=N, T=M", got {text!r}')
            rows.append((self.game(name.strip(), scope, where, line, written=True), int(count)))
        return rows

    # what each wire carries, and the paper checks
    def driven(self):
        for w in self.c.wires.values():
            sigs = set()
            for kind, ref in w.drivers:
                got = self.driver_signals(kind, ref)
                if got is None:
                    sigs = None
                    break
                sigs |= got
            w.signals = sigs

    def driver_signals(self, kind, ref):
        if kind == "net":
            return set(self.flat.nets[ref].items)
        m = self.flat.machines.get(ref)
        if m is None:
            return None                 # a Literal's combinator: what its own circuit makes
        if ref in self.c.emits:
            return set(self.c.emits[ref])
        got = self.c.settings.get(ref, {})
        if got.get("read") == "temperature":
            return {got["signal"]}
        if got.get("signals"):
            return {p.rsplit(":", 1)[0] for p in got["signals"].split("|")}
        if got.get("read") == "ingredients" and m.recipe:
            return set(m.needs)
        return None                     # a chest: whatever it holds

    def paper_checks(self):
        behaved = {}
        for b, scope in self.flat.circuit_src.get("behaves", ()):
            wire = self.wire_named(b.wire, scope)
            if wire is None:
                self.check("no-wire", f"behave {b.name}", f"on {b.wire}: no such wire", b.line)
            behaved.setdefault(wire, []).append(b)
            self.c.behaves.append((b, scope))
            for kind, text, line in b.lines:
                if kind == "require":
                    self.require(b, text, scope, line)
        self.c.expects = [e for e, _scope in self.flat.circuit_src.get("expects", ())]
        for node, scope in self.flat.circuit_src.get("sets", ()):
            self.c.sets.append((node, scope))
        for name, w in sorted(self.c.wires.items()):
            if w.implicit:
                continue
            if not w.drivers:
                self.check("no-wire", f"wire {name}", "nothing drives it", w.line)
            if not w.consumers:
                self.check("no-wire", f"wire {name}", "nothing reads it", w.line)
            for kind, ref in w.consumers:
                if kind == "net" and (ref not in self.c.gates or self.c.gates[ref].wire != name):
                    self.check("no-wire", f"wire {name}", f"net {ref} reads it but has no when on "
                               f"it (net {ref}: ... when {name}.no-signal < N)", w.line)
        for where, cd in [(f"net {n}", g) for n, g in self.c.gates.items()] + \
                         [(p, e) for p, e in self.c.enables.items()]:
            if cd.wire is None:
                continue
            sigs = self.c.wires[cd.wire].signals
            for s in [cd.signal] + ([cd.right] if isinstance(cd.right, str) else []):
                if sigs is not None and s not in sigs and s not in _WILDCARDS:
                    self.check("no-signal", where, f"when {cd.text}: no driver on {cd.wire} puts "
                               f"{s} on it (it carries {', '.join(sorted(sigs)) or 'nothing'})",
                               cd.line)
        self.switch_never()
        for name, g in sorted(self.c.gates.items()):
            cycles = [t for b in behaved.get(g.wire, ()) for k, t, _l in b.lines if k == "cycle"]
            if cycles:
                self.c.notes.append(f"gated net {name} ({g.text}): " + "; ".join(cycles))
            else:
                self.check("gate-open", f"net {name}", f"gated ({g.text}): the rate model takes it as "
                           "always open" + ("" if g.wire is None else
                                            f"; a behave block on {g.wire} with a cycle says how "
                                            "often it runs"), self.flat.nets[name].line)

    def moving(self):
        """The wires whose signals can change: one with a driver that isn't a constant
        combinator (a chest, a belt, an accumulator, a reactor, a crafter), or a combinator that
        reads a wire that moves. A wire driven only by constants, or by combinators that read
        only such wires, never changes."""
        live = set()
        reads = {}                                     # combinator key -> the wires it reads
        for name, w in self.c.wires.items():
            for kind, ref in w.consumers:
                if kind == "machine" and self.combinator(ref):
                    reads.setdefault(ref, set()).add(name)
        changed = True
        while changed:
            changed = False
            for name, w in self.c.wires.items():
                if name in live:
                    continue
                for kind, ref in w.drivers:
                    if kind == "net":
                        ok = True
                    elif self.combinator(ref):
                        ok = bool(reads.get(ref, set()) & live)
                    else:
                        ok = self.flat.machines[ref].prototype != "constant-combinator"
                    if ok:
                        live.add(name)
                        changed = True
                        break
        return live

    def switch_never(self):
        """``switch-never`` (2.1, power domains): a power switch whose condition can't change,
        because nothing on its wire moves (or nothing puts the signal it reads there)."""
        live = None
        for path, cd in sorted(self.c.enables.items()):
            inst = self.flat.insts[path]
            if not any(self.flat.machines[k].prototype == "power-switch" for k in inst.machines):
                continue
            if cd.wire is None:
                continue                                # the logistic network's count moves
            if live is None:
                live = self.moving()
            w = self.c.wires[cd.wire]
            why = None
            if cd.wire not in live:
                why = (f"nothing on {cd.wire} moves" + (
                    " (only constants drive it)" if w.drivers else " (nothing drives it)"))
            elif w.signals is not None and cd.signal not in w.signals \
                    and cd.signal not in _WILDCARDS:
                why = f"no driver on {cd.wire} puts {cd.signal} on it"
            if why:
                self.check("switch-never", path, f"when {cd.text}: {why}, so the switch stays "
                           "as it starts", cd.line)

    def require(self, b, text, scope, line):
        m = re.fullmatch(r"(\S+)\s*(<=|>=|!=|==|=|<|>)\s*(\S+)", text)
        vals = []
        for side in (m.group(1), m.group(3)) if m else ():
            vals.append(self.fact(side, scope))
        if not m or None in vals:
            self.c.notes.append(f"behave {b.name}: require {text}: checked in the game")
            return
        a, op, z = vals[0], m.group(2), vals[1]
        ok = {"<": a < z, ">": a > z, "<=": a <= z, ">=": a >= z, "=": a == z, "==": a == z,
              "!=": a != z}[op]
        if not ok:
            self.check("require-not-met", f"behave {b.name}", f"require {text}: {a:g} {op} {z:g} does "
                       "not hold", line)
        else:
            self.c.notes.append(f"behave {b.name}: require {text}: holds ({a:g} {op} {z:g})")

    def fact(self, text, scope):
        try:
            return float(text)
        except ValueError:
            pass
        inst, dot, fact = text.rpartition(".")
        if not dot:
            return None
        node, keys = self.flat.resolve_ref(lang.Ref(inst.split("."), False, 0), scope)
        if not keys:
            return None
        return FACTS.get(self.flat.machines[keys[0]].prototype, {}).get(fact)


def resolve(flat, cat) -> Circuit:
    """The factory's circuits from its source (``flat.circuit_src``, the nets' and instances'
    ``when``, the instances' circuit parameters); check lines go to ``flat.checks``."""
    r = _Resolver(flat, cat)
    r.signals()
    r.wires()
    r.instance_settings()
    r.conditions()
    r.driven()
    r.paper_checks()
    return r.c


# -- on the layout ---------------------------------------------------------------------------------
def _distance(a, b):
    return math.hypot(float(a.x) - float(b.x), float(a.y) - float(b.y))


def _clean(v):
    return int(v) if float(v).is_integer() else v


class _Layer:
    """One pass over a layout: the settings to write, the wires to lay, the report."""

    def __init__(self, flat, out, cat, keep_out=()):
        from .elaborate import Check
        from . import connect
        self.flat, self.lay, self.cat, self.Check = flat, out, cat, Check
        self.c = flat.circuit
        self.wires = dict(self.c.wires)                # the declared and implicit, plus fill's
        self.lines, self.checks = [], []
        self.members = {}                              # wire -> [(entity key, port)]
        self.net_read = {}                             # 2.1: net -> its reader belt's read mode
        self.g = connect.graph(out, cat)
        self.keep_out = set(keep_out)
        self.ground = None                             # where a hop pole may stand (2.1)

    def check(self, code, where, msg):
        self.checks.append(self.Check(code, where, msg))

    def kind(self, key):
        try:
            return self.cat.kind(self.lay.entities[key].name)
        except KeyError:
            return ""

    def set(self, key, attr, value, why):
        e = self.lay.entities[key]
        have = e.attrs.get(attr)
        if have is not None and have != value:
            self.check("CIRCUIT", key, f"{attr}={have} and {attr}={value} ({why}): the entity "
                       f"takes one; it keeps {attr}={have}")
            return False
        e.attrs[attr] = value
        return True

    def join(self, wire, key, port=None):
        got = self.members.setdefault(wire, [])
        if (key, port) not in got:
            got.append((key, port))

    def side(self, key, role):
        """A wire end's port: a combinator drives from its output side and reads at its input
        side (2.1); anything else has one side."""
        m = self.flat.machines.get(key)
        proto = m.prototype if m is not None else \
            (self.lay.entities[key].name if key in self.lay.entities else None)   # a Literal's
        if proto not in SIDED:
            return None
        return "out" if role == "driver" else "in"

    def hops_out(self, key):
        """The inserters that pick from machine ``key``: [(inserter, what it drops into)]."""
        return [(k, self.drop(k)) for k, via in self.g.succ.get(key, ()) if via == "pickup"]

    def hops_in(self, key):
        return [k for k, via in self.g.pred.get(key, ()) if via == "drop"]

    def drop(self, ins):
        got = [k for k, via in self.g.succ.get(ins, ()) if via == "drop"]
        return got[0] if got else None

    def store(self, key):
        """Machine ``key`` is a chest that takes what reaches it."""
        m = self.flat.machines.get(key)
        return m is not None and m.sink and m.extra.get("role") in _CHEST_ROLES

    # the parts
    def machine_settings(self):
        for key, attrs in sorted(self.c.settings.items()):
            if key in self.lay.entities:
                for attr, value in attrs.items():
                    self.set(key, attr, value, "its instance")

    def gates(self):
        for net, cd in sorted(self.c.gates.items()):
            carriers = sorted((k for k, e in self.lay.entities.items()
                               if e.attrs.get("net") == net and self.kind(k) in _GATED),
                              key=fkpmod.natural_key)
            if not carriers:
                # 3.0.2-lab (Rory 11-08: "connect a wire right to a belt and enable or disable
                # flow"): a belt-only route is switched at one belt, the first every driver's
                # items pass through
                belt = self.trunk_belt(net)
                carriers = [belt] if belt else []
            if not carriers:
                self.check("gate-open", f"net {net}", f"gated ({cd.text}), but its route has no "
                           "inserter, pump or belt to gate: it runs ungated")
                continue
            attr, value = cd.attr()
            for k in carriers:
                if self.set(k, attr, value, f"net {net}") and cd.wire:
                    self.join(cd.wire, k)
            self.lines.append(f"gated net {net}: {len(carriers)} carrier"
                              f"{'s' if len(carriers) != 1 else ''} {attr}={value}"
                              + (f" on {cd.wire}" if cd.wire else ""))

    def trunk_belt(self, net):
        """The first transport belt of net ``net``'s route that every head of the route reaches
        (past the joins of all its drivers), or None."""
        mine = {k for k, e in self.lay.entities.items() if e.attrs.get("net") == net}
        if not mine:
            return None
        heads = [k for k in mine if not any(p in mine for p, _v in self.g.pred.get(k, ()))]
        reach = None
        order = {}
        for h in sorted(heads, key=fkpmod.natural_key):
            seen, todo = {h: 0}, [h]
            while todo:
                nxt = []
                for k in todo:
                    for b, _v in self.g.succ.get(k, ()):
                        if b in mine and b not in seen:
                            seen[b] = seen[k] + 1
                            nxt.append(b)
                todo = nxt
            reach = set(seen) if reach is None else reach & set(seen)
            if not order:
                order = seen
        belts = [k for k in (reach or ()) if self.kind(k) == "transport-belt"]
        return min(belts, key=lambda k: (order.get(k, 1 << 30), fkpmod.natural_key(k))) if belts else None

    def enables(self):
        for path, cd in sorted(self.c.enables.items()):
            attr, value = cd.attr()
            for key in self.flat.insts[path].machines:
                if key not in self.lay.entities:
                    continue
                if self.kind(key) not in _ENABLE:
                    self.check("CIRCUIT", path, f"when {cd.text}: a {self.lay.entities[key].name} "
                               "has no enable condition")
                    continue
                if self.set(key, attr, value, f"{path} when") and cd.wire:
                    self.join(cd.wire, key)
            self.lines.append(f"{path}: {attr}={value}" + (f" on {cd.wire}" if cd.wire else ""))

    def setprops(self):
        """``set SEL: attr=value, ...`` (2.0.27, Rory 2026-10-25): the source names the object -
        an instance's machines, its hands (``NAME.out`` / ``NAME.in``), narrowed to the hand whose
        sink is an instance (``-> NAME``) - and sets its properties. Nothing is guessed from where
        the router happened to put a hand; a selector that matches nothing is a check line."""
        for p, scope in self.c.sets:
            path = _join(scope, p.target)
            label = ("" if getattr(p, "inline", False) else "set ") + p.target + (
                ("." + p.side) if p.side else "") + ((" -> " + p.into) if p.into else "")
            unknown = [a for a in p.attrs if a not in PROP_ATTRS and not lang.is_raw(a)]
            if unknown:
                self.check("no-match", label, f"unknown property {unknown[0]}: one of "
                           f"{', '.join(PROP_ATTRS)}, or a raw cb.FIELD or bp.FIELD")
                continue
            inst = self.flat.insts.get(path)
            if inst is None and p.side is None and p.into is None and set(p.attrs) == {"read"}:
                # 2.1: `set NET: read=hold-all`, how a wire reads a net's belt (a sushi loop's gate
                # reads the whole belt); `explicit` sets it on the belt the wire reads
                net = next((n for n in (_join(s, p.target) for s in _chain(scope))
                            if n in self.flat.nets), None)
                if net is not None:
                    if p.attrs["read"] not in ("hold", "pulse", "hold-all"):
                        self.check("no-match", label, f"read={p.attrs['read']}: a net's belt reads "
                                   "hold, hold-all or pulse")
                    else:
                        self.net_read[net] = p.attrs["read"]
                    continue
            if inst is None:
                self.check("no-match", label, f"no instance {p.target} to set at {path}")
                continue
            keys = []
            if p.side is None:
                keys = [k for k in inst.machines if k in self.lay.entities]
            elif p.side == "out":
                for mk in inst.machines:
                    keys += [k for k, _dst in self.hops_out(mk) if k in self.lay.entities]
            else:
                for mk in inst.machines:
                    keys += [k for k in self.hops_in(mk) if k in self.lay.entities]
            sink, chest = None, None
            if p.into is not None:
                sink = _join(scope, p.into)
                if p.side is None:
                    self.check("no-match", label, "-> narrows hands: write NAME.out -> CHEST, not a "
                               "set on the machine itself")
                    continue
                sinks = set(self.flat.insts[sink].machines) if sink in self.flat.insts else {sink}
                keys = [k for k in keys if self.drop(k) in sinks]
                if not keys and p.side == "out":
                    # the product left on a belt: the hand that carries it into the named sink is
                    # further down a net this maker drives (2.0.27)
                    driven = {n for n, net in self.flat.nets.items()
                              if any(k in inst.machines for k, _i in net.drivers)}
                    keys = [k for k, e in self.lay.entities.items()
                            if e.attrs.get("net") in driven and "inserter" in e.name
                            and self.drop(k) in sinks]
                chest = next((k for k in sinks if self.store(k)), None)
            if not keys:
                self.check("no-match", label, "matches no hand or machine: the route has nothing "
                           "there")
                continue
            wire = None
            if "if" in p.attrs:
                if p.wire is not None and chest is None:
                    # a lowered fill=: the chest its product goes into, whatever the route
                    keys_ = list(self.flat.insts[p.wire].machines)
                    chest = next((k for k in keys_ if self.store(k)), None)
                if chest is None:
                    self.check("no-match", label, "if= reads a wire: name the chest it reads "
                               "(set NAME.out -> CHEST: if=...) or use logistic=")
                    continue
                wire = IMPLICIT.format(self.flat.machines[chest].inst)
                if wire not in self.wires:
                    self.wires[wire] = WireDef(wire, "red", [("machine", chest)], implicit=True)
            for k in keys:
                for attr, value in p.attrs.items():
                    if lang.is_raw(attr):
                        self.raw(k, attr, value, label)
                    else:
                        self.set(k, attr, value, label)
                if wire:
                    self.join(wire, k)
            kind = "hand" if p.side else "machine"
            self.lines.append(f"{label}: " + ", ".join(f"{a}={v}" for a, v in p.attrs.items())
                              + f" on {len(keys)} {kind}" + ("s" if len(keys) != 1 else "")
                              + f" ({', '.join(keys[:4])}" + (" ..." if len(keys) > 4 else "") + ")")

    # raw settings (2.1)
    def raw(self, key, attr, value, why):
        """``cb.FIELD=VALUE`` / ``bp.FIELD=VALUE`` on entity ``key``: the field, in the game's
        form, into the entity's ``cb`` / ``bp`` attr (a JSON object)."""
        which, _dot, name = attr.partition(".")
        if which == "bp" and name in codec.BP_REFUSED:
            self.check("CIRCUIT", why, f"bp.{name}: the codec writes {name} from the design"
                       + ("; a control behaviour field is cb.FIELD" if name == "control_behavior"
                          else ""))
            return
        v = value.value if isinstance(value, lang.Raw) else value
        got = self.convert(name, v, f"{why}: {attr}")
        e = self.lay.entities[key]
        have = json.loads(e.attrs.get(which) or "{}")
        if name in have and have[name] != got:
            self.check("CIRCUIT", key, f"{attr} is set twice ({why} wants "
                       f"{codec.config_text(got)}): it keeps {codec.config_text(have[name])}")
            return
        have[name] = got
        e.attrs[which] = codec.config_text(have)

    def convert(self, key, v, where):
        """A raw value in the game's form: a signal name in a signal field becomes a SignalID, a
        condition string in a condition field the game's condition; the rest as written."""
        if isinstance(v, dict):
            return {k: self.convert(k, x, where) for k, x in v.items()}
        if isinstance(v, list):
            return [self.convert(key, x, where) for x in v]
        if not isinstance(v, str):
            return v
        if codec.is_signal_key(key):
            self.raw_signal(v, where, written=key.endswith("output_signal"))
            return codec.signal_json(v)
        if codec.is_condition_key(key):
            v = re.sub(r"\s*([<>=!]+)\s*", r"\1", v.strip())     # "signal-A < 50" as if= takes it
            try:
                a, _op, b = codec.parse_condition(v, where)
            except codec.CodecError as exc:
                self.check("CIRCUIT", where, str(exc))
                return v
            for s in [a] + ([b] if isinstance(b, str) else []):
                self.raw_signal(s, where)
            return codec.condition_json(v, where)
        return v

    def raw_signal(self, name, where, written=False):
        if not _known(name, self.cat):
            self.check("no-signal", where, f"{name} is no game signal")
        elif name in NON_TRANSMITTING or (written and name in NEVER_SENT):
            self.check("no-signal", where, f"{name} never goes out on a wire")

    def raw_overlaps(self):
        """A ``cb.`` field that a typed setting of the same entity also writes: the ``cb.`` one
        wins in the blueprint, and a line says so."""
        for key in sorted(self.lay.entities, key=fkpmod.natural_key):
            e = self.lay.entities[key]
            if not e.attrs.get("cb"):
                continue
            typed = {a: e.attrs[a] for a in codec.CIRCUIT_ATTRS if e.attrs.get(a)}
            try:
                made = codec.control_behavior(typed, key).get("control_behavior") or {}
            except codec.CodecError:
                continue
            both = sorted(set(made) & set(json.loads(e.attrs["cb"])))
            if both:
                self.check("CIRCUIT", key, f"cb.{both[0]} and a typed setting "
                           f"({', '.join(f'{a}={v}' for a, v in typed.items())}) both write "
                           f"{both[0]}: the cb. one goes in the blueprint")

    def guards(self):
        for path in self.c.guards:
            wire = IMPLICIT.format(path)
            hands, plain = 0, []
            for key in self.flat.insts[path].machines:
                if key not in self.lay.entities:
                    continue
                for ins in self.hops_in(key):
                    if self.lay.entities[ins].name not in FILTER_HANDS:
                        plain.append(ins)
                        continue
                    if self.set(ins, "filter", "circuit", f"{path} guard"):
                        self.join(wire, key)
                        self.join(wire, ins)
                        hands += 1
            self.lines.append(f"{path} guard: {hands} input inserter"
                              + ("s carry" if hands != 1 else " carries")
                              + " only what its recipe takes (filters from its ingredients)")
            if plain:
                self.check("cannot-filter", path, f"{', '.join(plain)}: a "
                           f"{self.lay.entities[plain[0]].name} can't filter; a fast inserter "
                           "can (the router's default hand)")

    def connectors(self, key):
        """The entity's circuit connectors (the catalog's, from the game), None when the catalog
        doesn't say."""
        try:
            row = self.cat.entity(self.lay.entities[key].name)
        except KeyError:
            return None
        got = (row.get("circuit") or {}).get("connectors")
        return None if got is None else list(got)

    def explicit(self):
        """The declared wires' members: their machines, the gated nets' carriers (joined by
        ``gates``), a driving net's belt. 2.1: any building with a circuit connector; one with
        none is a ``no-wire`` line."""
        for name, w in sorted(self.wires.items()):
            for role, side in (("driver", w.drivers), ("consumer", w.consumers)):
                for kind, ref in side:
                    if kind == "machine" and ref in self.lay.entities:
                        if self.connectors(ref) == []:
                            self.check("no-wire", f"wire {name}", f"{ref}: a "
                                       f"{self.lay.entities[ref].name} has no circuit connector")
                            continue
                        self.join(name, ref, self.side(ref, role))
            for kind, ref in w.drivers:
                if kind != "net":
                    continue
                belt = self.reader(ref, name)
                if belt is None:
                    self.check("no-wire", f"wire {name}", f"net {ref} has no belt to read")
                elif self.set(belt, "read", self.net_read.get(ref, "hold"), f"wire {name}"):
                    self.join(name, belt)
                    if ref in self.net_read:
                        self.lines.append(f"wire {name}: net {ref}'s belt {belt} reads "
                                          f"{self.net_read[ref]}")

    def reader(self, net, wire):
        belts = [e for e in self.lay.entities.values()
                 if e.attrs.get("net") == net and self.kind(e.key) == "transport-belt"]
        if not belts:
            return None
        others = [self.lay.entities[k] for k, _p in self.members.get(wire, ())]
        if not others:
            return min(belts, key=lambda e: fkpmod.natural_key(e.key)).key
        return min(belts, key=lambda e: (min(_distance(e, o) for o in others),
                                         fkpmod.natural_key(e.key))).key

    def wire_up(self):
        for name in sorted(self.members, key=fkpmod.natural_key):
            ends = self.members[name]
            w = self.wires.get(name)
            colour = w.colour if w is not None else "red"
            if len(ends) < 2:
                continue
            self.lay.wirenets[name] = fkpmod.WireNet(name, colour)
            edges = []
            for i in range(len(ends)):
                for j in range(i + 1, len(ends)):
                    a, b = self.lay.entities[ends[i][0]], self.lay.entities[ends[j][0]]
                    edges.append((round(_distance(a, b), 6), fkpmod.natural_key(a.key),
                                  fkpmod.natural_key(b.key), i, j))
            edges.sort()
            parent = list(range(len(ends)))

            def find(k):
                while parent[k] != k:
                    parent[k] = parent[parent[k]]
                    k = parent[k]
                return k
            laid, hopped, added = 0, [], 0
            for d, _ka, _kb, i, j in edges:
                ri, rj = find(i), find(j)
                if ri == rj:
                    continue
                parent[max(ri, rj)] = min(ri, rj)
                a, b = ends[i][0], ends[j][0]
                # 2.1: a combinator's end names its side (latch.out to latch.in is its memory)
                ea = a if ends[i][1] is None else f"{a}.{ends[i][1]}"
                eb = b if ends[j][1] is None else f"{b}.{ends[j][1]}"
                reach = min(self.cat.circuit_reach(self.lay.entities[a].name),
                            self.cat.circuit_reach(self.lay.entities[b].name))
                if d > reach + 1e-9:
                    # 2.1: a long wire hops along poles, a new one where there is a gap
                    chain = self.hops(a, b, colour, name)
                    if chain is None:
                        self.check("wire-too-far", f"wire {name}", f"{a} to {b} is {d:.1f} tiles; "
                                   f"a wire reaches {reach:g}, and no chain of poles or free "
                                   "tiles joins them: move them closer, or make room for a pole "
                                   "between")
                    else:
                        new = [k for k in chain if k.startswith(POLE_OWNER + "/")]
                        added += len(new)
                        hopped.append(f"{a} to {b} {d:.1f} tiles over {len(chain)} pole"
                                      f"{'s' if len(chain) != 1 else ''}"
                                      + (f" ({len(new)} added)" if new else ""))
                        for x, y in zip([ea] + chain, chain + [eb]):
                            self.lay.wires.append(fkpmod.Wire(name, colour, x, y).canonical())
                            laid += 1
                        continue
                self.lay.wires.append(fkpmod.Wire(name, colour, ea, eb).canonical())
                laid += 1
            if (w is not None and not w.implicit) or hopped:
                self.lines.append(f"wire {name} {colour}: {len(ends)} entities, {laid} wire"
                                  f"{'s' if laid != 1 else ''}"
                                  + (f"; on poles: {'; '.join(hopped)}" if hopped else ""))
        self.lay.wires = fkpmod.sort_wires(self.lay.wires)

    # wires carried on poles (2.1)
    def _pole_used(self, key, colour, net):
        """Pole ``key`` already carries a wire of ``colour`` for another net (their signals
        would add)."""
        for w in self.lay.wires:
            if w.colour == colour and w.net != net and key in (fkpmod.split_end(w.a)[0],
                                                               fkpmod.split_end(w.b)[0]):
                return True
        for other, ends in self.members.items():
            if other != net and any(k == key for k, _p in ends) and \
                    (self.wires[other].colour if other in self.wires else "red") == colour:
                return True
        return colour in self._literal_colours().get(key, ())

    def _literal_colours(self):
        """2.1.31: {entity key: wire colours} of the wires a Literal brought (``wire`` attrs,
        ``c,k,d``; an odd connector is red, an even one green). A hop over a pole of a captured
        sorter's green chain joined the new wire's signals to it."""
        if getattr(self, "_litcol", None) is None:
            got = {}
            for e in self.lay.entities.values():
                text = e.attrs.get("wire")
                if not text:
                    continue
                head = e.key.rsplit("/", 1)[0]
                for part in str(text).split(";"):
                    try:
                        c, k, d = part.split(",")
                        c, d = int(c), int(d)
                    except ValueError:
                        continue
                    got.setdefault(e.key, set()).add("red" if c % 2 else "green")
                    got.setdefault(f"{head}/{k}", set()).add("red" if d % 2 else "green")
            self._litcol = got
        return self._litcol

    def _hop_pole(self):
        """The pole a gap gets: the power cover's, else a medium pole."""
        cover = self.lay.covers.get("power")
        pole = (cover.params.get("pole") if cover is not None else None) or "medium-electric-pole"
        if not self.cat.has_entity(pole) or self.cat.kind(pole) != "electric-pole":
            pole = "medium-electric-pole"
        return pole

    def _ground(self, pole):
        if self.ground is None:
            from . import cover
            taken = {}
            for e in self.lay.entities.values():
                try:
                    for t in fkpmod.tiles_of(self.cat, e):
                        taken[t] = e.key
                except (KeyError, ValueError):
                    continue
            self.ground = cover._Ground(taken, self.keep_out, self.lay.size, self.cat.size(pole))
        return self.ground

    def hops(self, a, b, colour, net):
        """A chain of poles carrying a wire from entity ``a`` to entity ``b``: [pole key], each
        step within both its ends' circuit reach. The layout's poles that carry no other net of
        ``colour`` cost nothing; a new pole on a free tile (inside the frame, on no entity and
        no keep-out tile, within ``HOP_PAD`` of the box around a and b) costs one, and the chain
        with the fewest new ones wins, then the fewest steps. The new poles are added to the
        layout (``circuit-poles/p<k>``). None when no chain exists."""
        ents = self.lay.entities
        ea, eb = ents[a], ents[b]
        ax, ay, bx, by = float(ea.x), float(ea.y), float(eb.x), float(eb.y)
        x0, x1 = min(ax, bx) - HOP_PAD, max(ax, bx) + HOP_PAD
        y0, y1 = min(ay, by) - HOP_PAD, max(ay, by) + HOP_PAD
        pole = self._hop_pole()
        ground = self._ground(pole)
        pw, ph = ground.pw, ground.ph
        new_reach = self.cat.circuit_reach(pole)
        rb = self.cat.circuit_reach(eb.name)
        poles = []
        for k in sorted(ents, key=fkpmod.natural_key):
            e = ents[k]
            if k in (a, b) or self.kind(k) != "electric-pole":
                continue
            if not (x0 <= float(e.x) <= x1 and y0 <= float(e.y) <= y1):
                continue
            if self._pole_used(k, colour, net):
                continue
            poles.append((k, float(e.x), float(e.y), self.cat.circuit_reach(e.name)))
        # nodes: ("a",) the start, ("p", index) a pole, ("s", spot) a free spot
        start = ("a",)
        info = {start: (ax, ay, self.cat.circuit_reach(ea.name))}
        best = {start: (0, 0, 0.0)}
        back = {start: None}
        heap = [(0, 0, 0.0, 0, start)]
        tie = 0
        done = set()
        offsets = {}
        while heap:
            new, steps, length, _t, node = heapq.heappop(heap)
            if node in done:
                continue
            done.add(node)
            x, y, r = info[node]
            if math.hypot(x - bx, y - by) <= min(r, rb) + 1e-9 and node != start:
                chain, n = [], node
                while n != start:
                    chain.append(n)
                    n = back[n]
                return self._lay_chain(chain[::-1], poles, pole)
            cands = []
            for i, (_k, px, py, pr) in enumerate(poles):
                if math.hypot(x - px, y - py) <= min(r, pr) + 1e-9:
                    cands.append((("p", i), 0, (px, py, pr)))
            step = min(r, new_reach)
            ox, oy = x - pw / 2, y - ph / 2
            bi, bj = math.floor(ox), math.floor(oy)
            fx, fy = ox - bi, oy - bj
            offs = offsets.get((step, fx, fy))
            if offs is None:
                d = int(math.ceil(step)) + 1
                lim2 = step * step + 1e-9
                offs = [(di, dj) for dj in range(-d, d + 1) for di in range(-d, d + 1)
                        if (di - fx) ** 2 + (dj - fy) ** 2 <= lim2]
                offsets[(step, fx, fy)] = offs
            for di, dj in offs:
                spot = (bi + di, bj + dj)
                cx, cy = spot[0] + pw / 2, spot[1] + ph / 2
                if not (x0 <= cx <= x1 and y0 <= cy <= y1):
                    continue
                if ("s", spot) in done or not ground.free(spot):
                    continue
                cands.append((("s", spot), 1, (cx, cy, new_reach)))
            for nxt, cost, where in cands:
                if nxt in done:
                    continue
                # the fewest new poles, then the fewest steps, then the shortest (the straightest)
                score = (new + cost, steps + 1,
                         round(length + math.hypot(where[0] - x, where[1] - y), 6))
                if nxt in best and best[nxt] <= score:
                    continue
                best[nxt], back[nxt], info[nxt] = score, node, where
                tie += 1
                heapq.heappush(heap, (score[0], score[1], score[2], tie, nxt))
        return None

    def _lay_chain(self, chain, poles, pole):
        """The chain's nodes as pole keys; its new spots become poles of the layout."""
        ground = self.ground
        used = [int(m.group(1)) for k in self.lay.entities
                if (m := re.fullmatch(re.escape(POLE_OWNER) + r"/p(\d+)", k))]
        n = max(used) + 1 if used else 1
        out = []
        for node in chain:
            if node[0] == "p":
                out.append(poles[node[1]][0])
                continue
            spot = node[1]
            key = f"{POLE_OWNER}/p{n}"
            n += 1
            cx, cy = spot[0] + ground.pw / 2, spot[1] + ground.ph / 2
            e = fkpmod.Entity(key, pole, _clean(cx), _clean(cy))
            self.lay.add(e)
            ground.place(spot)
            for t in fkpmod.tiles_of(self.cat, e):
                ground.taken[t] = key
            out.append(key)
        if POLE_OWNER not in self.lay.covers and any(k.startswith(POLE_OWNER + "/") for k in out):
            self.lay.covers[POLE_OWNER] = fkpmod.Cover(POLE_OWNER, "wires", {"pole": pole})
        return out

    def unwired(self):
        """A belt keeps its reading only on a wire (the game keeps no setting on an unwired belt,
        not even on its ghost: measured on a laptop test server, 2.0.77, 2026-10-22)."""
        wired = {fkpmod.split_end(end)[0] for w in self.lay.wires for end in (w.a, w.b)}
        for key in sorted(self.lay.entities, key=fkpmod.natural_key):
            e = self.lay.entities[key]
            if e.attrs.get("read") in ("hold", "pulse", "hold-all") and key not in wired \
                    and self.kind(key) == "transport-belt":
                self.check("no-wire", key, f"read={e.attrs['read']} on a belt that is on no wire: "
                           "its reading reaches nothing until the belt is wired to something")

    def leaks(self):
        """``wires-share``: two wire nets of one colour on one entity are one network in the game,
        so their signals add; a line when a consumer would see a driver its net doesn't have."""
        touch = {}
        for w in self.lay.wires:
            if w.colour == "copper":
                continue
            for end in (w.a, w.b):
                # a combinator's two sides are two networks (2.1)
                key, port = fkpmod.split_end(end)
                touch.setdefault((key, w.colour, port or ""), set()).add(w.net)
        drivers = {}
        for name, w in self.wires.items():
            drivers[name] = {ref for kind, ref in w.drivers if kind == "machine"}
        for (key, colour, _port), nets in sorted(touch.items()):
            if len(nets) < 2:
                continue
            union = set().union(*(drivers.get(n, set()) for n in nets))
            if any(drivers.get(n, set()) != union for n in nets):
                self.check("wires-share", key, f"the {colour} wires of {', '.join(sorted(nets))} "
                           "meet here, so they are one network and their signals add; give one "
                           "of them the other colour")


def lay(flat, layout, cat, keep_out=()):
    """``layout`` (not changed) with the factory's circuits laid from its source: a new Layout,
    report lines and check lines. Every circuit setting and wire record is written again from
    the source, so a changed limit reaches the .fkp (and the next paste) on the next build; a
    Literal keeps its own settings (``config``) and wires. The poles a long wire added on the last
    build (``circuit-poles``) are taken out and laid again; ``keep_out``: tiles no such pole may
    take (the terrain's)."""
    import copy
    out = copy.deepcopy(layout)
    out.entities = {k: e for k, e in out.entities.items() if e.owner != POLE_OWNER}
    out.covers.pop(POLE_OWNER, None)
    literal = {path for path, inst in flat.insts.items() if inst.kind == "Literal"}
    for e in out.entities.values():
        if e.owner in literal or any(e.owner.startswith(p + "/") for p in literal):
            continue
        for attr in codec.CIRCUIT_ATTRS + codec.RAW_ATTRS:
            e.attrs.pop(attr, None)
    # 2.1: the copper lines between power domains are the power step's, and stay
    out.wirenets = {n: w for n, w in out.wirenets.items() if w.colour == "copper"}
    out.wires = [w for w in out.wires if w.colour == "copper"]
    cut_lines, cut_checks = cut_wires(flat, out, cat)
    c = getattr(flat, "circuit", None)
    if c is None or c.empty:
        return out, cut_lines, cut_checks
    L = _Layer(flat, out, cat, keep_out)
    L.machine_settings()
    L.gates()
    L.enables()
    L.setprops()
    L.guards()
    L.raw_overlaps()
    L.explicit()
    L.wire_up()
    L.unwired()
    L.leaks()
    return out, c.notes + cut_lines + L.lines, cut_checks + L.checks


_COMBINATORS = frozenset({"arithmetic-combinator", "decider-combinator", "selector-combinator"})


def _literal_fit(lay, keys, rows):
    """The one rigid move (quarter turns ``r`` clockwise, then an offset) that takes a Literal's
    capture positions to where its entities stand in ``lay``, or None. ``keys`` {row: key}."""
    pairs = [(rows[k], lay.entities[key]) for k, key in sorted(keys.items())
             if key in lay.entities][:24]
    if not pairs:
        return None
    for r in range(4):
        def turn(x, y, r=r):
            for _ in range(r):
                x, y = -y, x
            return x, y
        fx, fy = turn(pairs[0][0][1], pairs[0][0][2])
        off = (pairs[0][1].x - fx, pairs[0][1].y - fy)
        if all(abs(e.x - (turn(row[1], row[2])[0] + off[0])) < 1e-6 and
               abs(e.y - (turn(row[1], row[2])[1] + off[1])) < 1e-6 for row, e in pairs):
            return r, round(off[0], 6), round(off[1], 6)
    return None


def cut_wires(flat, lay, cat):
    """2.1.33 (lab FRICTION 20): a captured circuit wire whose two ends a box cut put in two
    Literals of one file, laid again when both stand as the capture did (one rigid move takes
    both). The demo's green wire from the petroleum chest to the hub sorter's combinator crossed
    the E1/E2 cut, stage 1 never moved a barrel, and the hub train made 141 visits without
    unloading. Two Literals that don't line up get a ``wire-cut`` line naming the wire. Appends to
    ``lay.wires`` and ``lay.wirenets`` (net ``cut/<file>``) -> (lines, checks)."""
    from . import literal
    from .elaborate import Check
    by_file = {}
    for path, (fpath, keys) in sorted(getattr(flat, "_lit_rows", {}).items()):
        by_file.setdefault(fpath, []).append((path, keys))
    lines, checks = [], []
    for fpath, lits in sorted(by_file.items()):
        if len(lits) < 2:
            continue
        try:
            rows, wires = literal.read_entities(fpath)
        except (literal.LiteralError, OSError):
            continue
        where = {}
        for path, keys in lits:
            for k, key in keys.items():
                where.setdefault(k, []).append((path, key))
        fits = {path: _literal_fit(lay, keys, rows) for path, keys in lits}
        net = f"cut/{os.path.basename(fpath)}"
        laid, lost, done = 0, [], set()

        def end(key, c):
            if lay.entities[key].name in _COMBINATORS:
                return f"{key}.{'out' if c > 2 else 'in'}"
            return key
        for i, ci, j, cj in wires:
            if ci in codec.COPPER or cj in codec.COPPER:
                continue
            colour = "red" if ci % 2 else "green"
            # a twin (the same box placed again) holds the same rows: each end is wired to the
            # other end's Literal that stands with it, and none other
            for (pa, ka), others, c_own, c_other in (
                    (a, where.get(j, ()), ci, cj) for a in where.get(i, ())):
                if ka not in lay.entities or any(p == pa for p, _k in others):
                    continue                    # the Literal keeps this wire itself
                cands = [(pb, kb) for pb, kb in others if kb in lay.entities]
                if not cands:
                    continue                    # the other end is in no Literal: cut on purpose
                ok = [(pb, kb) for pb, kb in cands if fits[pa] is not None and fits[pb] == fits[pa]]
                if not ok:
                    lost.append(f"{ka} to {cands[0][1]}")
                    continue
                for _pb, kb in ok:
                    w = fkpmod.Wire(net, colour, end(ka, c_own), end(kb, c_other)).canonical()
                    if (w.a, w.b, colour) in done:
                        continue
                    done.add((w.a, w.b, colour))
                    lay.wires.append(w)
                    lay.wirenets.setdefault(net, fkpmod.WireNet(net, colour))
                    laid += 1
        if laid:
            lines.append(f"wire {net}: {laid} captured wire{'s' if laid != 1 else ''} across "
                         "Literal cuts, laid as the capture had them")
        if lost:
            checks.append(Check("wire-cut", f"file {os.path.basename(fpath)}",
                                f"{len(lost)} captured wire{'s' if len(lost) != 1 else ''} cross "
                                "between Literals that don't stand as the capture did, so "
                                f"{'they are' if len(lost) != 1 else 'it is'} not laid: "
                                + ", ".join(lost[:4]) + (" ..." if len(lost) > 4 else "")
                                + "; declare a wire, or place the Literals as captured"))
    if lines or checks:
        lay.wires = fkpmod.sort_wires(lay.wires)
    return lines, checks
