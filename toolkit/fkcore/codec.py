"""fkcore.codec: keyed layout rows <-> a Factorio blueprint string, and .fkp <-> blueprint.

``blueprint`` / ``read_blueprint`` are copied from C:\\fk-lead\\repo\\fk\\trace.py (d6018d3): the same
deterministic serialisation (sorted keys, compact separators, zlib level 9, base64, a leading
"0") and the same parse. Extended for V1:

- ``direction`` is written only when the row has one (a pole or a chest has none), so a row
  without a direction comes back without one;
- an underground's ``ug=in|out`` is the game's ``type: input|output``;
- every other game field of an entity (a requester's ``request_filters``, an inserter's or a
  reactor's ``control_behavior``, ...) rides in its ``config`` attr as canonical JSON (sorted keys,
  compact, ASCII) and is written back verbatim. V1 has no circuit model: only a Literal sets them;
- a circuit wire is the ``wire`` attr of the end whose key sorts first, ``<connector>,<other end's
  local name>,<connector>`` (``;`` between wires), the other end under the same owner (a Literal's
  wires never leave it). Connectors are ``defines.wire_connector_id``: 1 red, 2 green (a
  combinator's input; 3 and 4 its output). The blueprint's ``wires`` carry them with entity
  numbers remapped; a wire whose other end is not among the rows is left out;
- the row's key rides in ``tags.src`` (as before), its net in ``tags.net``, and any attribute the
  game has no field for in ``tags.fkp``;
- the layout's record lines (header, size, inst, cover, plot, claim, mark) ride in the blueprint
  ``description``, so ``.fkp -> blueprint -> .fkp`` is byte-identical;
- copper wires between poles are **derived**, not stored: ``pole_wires`` is a minimum spanning
  tree over the poles within wire reach (at most 5 wires a pole), written as the blueprint's
  ``wires`` (``[[a, 5, b, 5]]``, 5 = ``defines.wire_connector_id.pole_copper``). The first in-game
  test (2026-10-20) showed a blueprint without wires builds every pole unwired: 40 poles, 40
  electric networks. Reading a blueprint drops copper wires again (a power switch's too: V1
  has none), so the round trip holds.

Circuits (2.0, Rory 2026-10-21: circuit wiring and every condition in the source; written by
``fkcore.circuits``): an entity's typed circuit settings become its ``control_behavior`` (and an
inserter's filters), and the layout's ``w`` records become the blueprint's ``wires``:

- ``if=S<N`` (or ``S>=T``, another signal): enabled while the condition holds (``circuit_enabled``,
  ``circuit_condition``); ``logistic=S<N``: the same against the logistic network's count
  (``connect_to_logistic_network``, ``logistic_condition``);
- ``read=temperature`` with ``signal=S`` (a reactor), ``read=hold|pulse`` (a belt's contents,
  which the game keeps only on a belt that is on a wire),
  ``read=hand`` (an inserter's hand), ``read=ingredients`` or ``read=contents`` (a crafter);
- ``filter=circuit``: an inserter's filters are set by the signals on its wire (the guard: a
  crafter that reads its ingredients tells its input inserters what to carry), ``filter=a|b``
  a fixed whitelist;
- ``signals=S:N|T:M``: a constant combinator's output (each filter with its quality, else it
  sends nothing: fnet's CLAUDE.md gotcha).

A signal is typed by its name, as the game lists its signals (``fkcore.facts``, 2.1): a virtual
signal, an item, a fluid, an entity, a recipe, a quality, a space location or an asteroid chunk;
an unknown name is an item (the game's default type). The comparators are the game's (``>=`` is
``≥``). The typed settings also ride in ``tags.fkp``, so a blueprint read back gives them back
and drops the ``control_behavior`` they make, as long as the entity's fields are what they make
(an item signal the game exported without its ``type`` counts as an item); otherwise the
entity's own fields win, in ``config``, as they do for a blueprint without typed settings (an
imported Literal). The keys were checked on a laptop test server (2.0.77, 2026-10-22): every one
survives a paste and a capture.

A ``w`` record is ``[a, c, b, c]`` with red 1 and green 2, a combinator's output 3 and 4 (fnet's
connector table); reading a blueprint that carries its records leaves the wires they explain out
of the ``wire`` attrs.

Settings changed in place (2.1): a paste over a standing entity sets its whole state, so the codec
writes the state a paste would otherwise leave. Measured on a laptop server (2.0.77, 2026-10-31):
every settable field absent from the blueprint entity is cleared by the paste (requests, trash and
buffer flags, a chest's limit, a storage chest's filter, an inserter's filters, a combinator's
signals, conditions and on/off), except a control behaviour: one the blueprint gives at the game's
defaults (``circuit_enabled=false`` and the rest, or ``{}``) is ignored, and with no control
behaviour at all a wired machine keeps its own (an inserter its filters too). One that differs
from the defaults in any field is written whole. So a machine of ``RESET_TYPES`` whose design
sets no control behaviour gets ``RESET_CB``: off, and a condition with no signal (it never
holds). Mining drills, pumpjacks and offshore pumps take no settings from a paste at all (not even
a new condition): the apply lays them fresh (``PASTE_SKIPS``). Reading a blueprint drops
``RESET_CB`` again.

Raw settings (2.1, PLAN-2.1 "Circuits that reach any building"): ``cb`` and ``bp`` are JSON
objects of blueprint fields as the game names them, set from the source's ``cb.FIELD=`` and
``bp.FIELD=`` (``fkcore.circuits`` turns signal names and condition strings into the game's
forms first). ``cb`` fields go into the entity's ``control_behavior``, over any field a typed
setting makes; ``bp`` fields go on the entity itself, except the fields the codec writes from the
row (``BP_REFUSED``). They ride in ``tags.fkp`` as the typed settings do, and come back the same
way.

Pure, stdlib only, deterministic.
"""
from __future__ import annotations

import base64
import json
import re
import zlib

from . import arms as armsmod
from . import fkp as fkpmod

__all__ = ["CodecError", "BLUEPRINT_VERSION", "rows_of", "blueprint", "read_blueprint",
           "parse_blueprint", "fkp_to_blueprint", "blueprint_to_fkp", "pole_wires",
           "circuit_wires", "wire_attrs", "POLE_COPPER", "CIRCUIT_ATTRS", "control_behavior",
           "parse_condition", "signal_type", "connector", "links_of", "RAW_ATTRS", "BP_REFUSED",
           "made_fields", "raw_fields", "condition_json", "signal_json"]

#: defines.wire_connector_id.pole_copper (read from the game, 2.0.77)
POLE_COPPER = 5
#: the connectors that carry power, not signals: pole_copper = power_switch_left_copper (5),
#: power_switch_right_copper (6)
COPPER = frozenset({5, 6})
#: copper wires one pole takes
MAX_POLE_WIRES = 5

#: The Factorio blueprint version the harness emits (an opaque 2.0.77 format marker).
BLUEPRINT_VERSION = 562949954076673
#: The blueprint's icon. The game takes a blueprint without one, but a blueprint renderer
#: (fbe.teoxoy.com) refuses it ("could not be loaded").
BLUEPRINT_ICONS = [{"index": 1, "signal": {"type": "item", "name": "transport-belt"}}]

_UG = {"in": "input", "out": "output"}
_UG_BACK = {v: k for k, v in _UG.items()}
#: blueprint entity fields the codec writes from the row itself; ``config`` holds every other one
_OWN = ("entity_number", "name", "position", "direction", "tags")

#: an entity's typed circuit settings (2.0), in the order the .fkp writes them
CIRCUIT_ATTRS = ("if", "logistic", "read", "signal", "filter", "signals")
#: an entity's raw settings (2.1): JSON objects of control behaviour (``cb``) and blueprint
#: (``bp``) fields
RAW_ATTRS = ("cb", "bp")
#: blueprint fields a ``bp.`` setting may not write: the codec writes them from the row, and a
#: control behaviour is ``cb.``
BP_REFUSED = frozenset({"entity_number", "name", "position", "direction", "tags", "wires",
                        "recipe", "type", "control_behavior", "pickup_position",
                        "drop_position"})
#: the entity fields the typed settings make (dropped on reading when ``tags.fkp`` has them)
_CIRCUIT_FIELDS = ("control_behavior", "use_filters", "filters")
#: the source's comparators -> the game's
_COMPARATOR = {"<": "<", ">": ">", "=": "=", "<=": "≤", ">=": "≥", "!=": "≠"}
_CONDITION = re.compile(r"^(?P<a>[A-Za-z0-9_\-]+?)(?P<op><=|>=|!=|<|>|=)"
                        r"(?P<b>-?\d+|[A-Za-z][A-Za-z0-9_\-]*)$")
#: belt reading modes (2.0 ``circuit_contents_read_mode``, with ``circuit_read_hand_contents``:
#: measured on a laptop test server, 2.0.77, by the lead engineer, 2026-10-22). 2.1: ``hold-all``
#: is 2.0's "hold, all belts" (``entire_belt_hold``, 2): the whole belt's contents, not one tile's,
#: as a sushi loop's gate reads it (PLAN-2.1 G; measured on a laptop test server, 2026-10-31)
_BELT_READ = {"pulse": 0, "hold": 1, "hold-all": 2}
#: where a signal ID sits in the fields the typed settings make
_SIGNAL_KEYS = ("first_signal", "second_signal", "temperature_signal")
#: connector ids by colour: (entity or combinator input, combinator output)
_CONNECTOR = {"red": (1, 3), "green": (2, 4)}
#: entity types whose control behaviour a paste writes whole when it differs from the game's
#: defaults (measured, 2.1: each one wired, with a condition set by hand, then pasted over)
RESET_TYPES = frozenset({"inserter", "transport-belt", "assembling-machine", "furnace", "pump",
                         "lamp", "power-switch", "train-stop", "agricultural-tower"})
#: the control behaviour of a ``RESET_TYPES`` machine whose design sets none: off, and a condition
#: with no signal (it never holds). The game's defaults are ignored by a paste; this is not
RESET_CB = {"circuit_enabled": False, "circuit_condition": {"comparator": "<", "constant": 1}}
#: entity types a paste writes no settings on at all (measured, 2.1): a settings change lays them
#: fresh
PASTE_SKIPS = frozenset({"mining-drill", "offshore-pump"})


class CodecError(ValueError):
    """A malformed blueprint string."""


def rows_of(lay):
    """The layout's entities as codec rows, in canonical key order."""
    out = []
    for key in sorted(lay.entities, key=fkpmod.natural_key):
        e = lay.entities[key]
        out.append({"src": e.key, "name": e.name, "x": e.x, "y": e.y, "direction": e.dir,
                    "attrs": dict(e.attrs)})
    return out


def signal_type(name):
    """A signal's type by its name, from the game's list (``fkcore.facts.signal_type``):
    ``virtual``, ``item``, ``fluid``, ``entity``, ``recipe``, ``quality``, ...; ``item`` for an
    unknown name."""
    from . import facts
    return facts.signal_type(name)


def _signal(name):
    return {"type": signal_type(name), "name": name}


def signal_json(name):
    """A signal name as the game's SignalID: ``{"type": ..., "name": ...}``."""
    return _signal(name)


def parse_condition(text, where="condition"):
    """``S<N`` / ``S>=T`` -> (signal, source comparator, number or signal)."""
    m = _CONDITION.match(str(text))
    if not m:
        raise CodecError(f"{where}: a condition is no-signal<op>NUMBER or no-signal<op>no-signal "
                         f"(op one of {', '.join(_COMPARATOR)}), got {text!r}")
    b = m.group("b")
    return m.group("a"), m.group("op"), int(b) if re.fullmatch(r"-?\d+", b) else b


def is_signal_key(key):
    """A blueprint field that holds a SignalID (2.1, for raw settings too): ``first_signal``,
    ``temperature_signal``, ``available_construction_output_signal``, ``icon_signal_id``,
    ``icon``, ..."""
    if not isinstance(key, str):
        return False
    return (key in _SIGNAL_KEYS or key.endswith("_signal") or key.endswith("signal_id")
            or key in ("signal", "icon"))


def is_condition_key(key):
    """A blueprint field that holds a condition (``circuit_condition``, ``condition``, ...)."""
    return isinstance(key, str) and key.endswith("condition")


def comparator_json(op):
    """A source comparator (``<``, ``>=``, ``!=`` ...) as the game writes it (``≥``, ``≠``)."""
    return _COMPARATOR["=" if op == "==" else op]


def condition_json(text, where="condition"):
    """``S<N`` / ``S>=T`` as the game's condition object."""
    return _condition(text, where)


def _condition(text, where):
    a, op, b = parse_condition(text, where)
    out = {"first_signal": _signal(a), "comparator": _COMPARATOR[op]}
    if isinstance(b, int):
        out["constant"] = b
    else:
        out["second_signal"] = _signal(b)
    return out


def control_behavior(attrs, where="entity"):
    """The entity fields an entity's typed circuit settings make: ``control_behavior`` and an
    inserter's ``use_filters`` / ``filters`` ({} when it has none)."""
    cb, fields = {}, {}
    if attrs.get("if"):
        cb["circuit_enabled"] = True
        cb["circuit_condition"] = _condition(attrs["if"], f"{where} if")
    if attrs.get("logistic"):
        cb["connect_to_logistic_network"] = True
        cb["logistic_condition"] = _condition(attrs["logistic"], f"{where} logistic")
    read = attrs.get("read")
    if read == "temperature":
        cb["read_temperature"] = True
        if attrs.get("signal"):
            cb["temperature_signal"] = _signal(attrs["signal"])
    elif read in _BELT_READ:
        cb["circuit_read_hand_contents"] = True
        cb["circuit_contents_read_mode"] = _BELT_READ[read]
    elif read == "hand":
        cb["circuit_read_hand_contents"] = True
    elif read == "ingredients":
        cb["read_ingredients"] = True
    elif read == "contents":
        cb["read_contents"] = True
    elif read is not None:
        raise CodecError(f"{where}: read={read}: temperature, hold, hold-all, pulse, hand, "
                         "ingredients or contents")
    flt = attrs.get("filter")
    if flt == "circuit":
        cb["circuit_set_filters"] = True
        fields["use_filters"] = True
    elif flt:
        fields["use_filters"] = True
        fields["filters"] = [{"index": k + 1, "name": it, "quality": "normal", "comparator": "="}
                             for k, it in enumerate(str(flt).split("|"))]
    if attrs.get("signals"):
        rows = []
        for k, part in enumerate(str(attrs["signals"]).split("|")):
            name, _colon, count = part.rpartition(":")
            if not name or not re.fullmatch(r"-?\d+", count):
                raise CodecError(f"{where}: signals= is S:N|T:M, got {attrs['signals']!r}")
            rows.append(dict(_signal(name), index=k + 1, quality="normal", comparator="=",
                             count=int(count)))
        cb["sections"] = {"sections": [{"index": 1, "filters": rows}]}
    if cb:
        fields["control_behavior"] = cb
    return fields


def raw_fields(attrs, where="entity"):
    """An entity's raw settings (2.1): ({cb field: value}, {bp field: value}) from its ``cb`` and
    ``bp`` attrs (JSON objects); a ``bp`` field the codec writes from the row is refused."""
    out = []
    for name in RAW_ATTRS:
        text = attrs.get(name)
        if text is None or text == "":
            out.append({})
            continue
        try:
            got = json.loads(text) if isinstance(text, str) else text
        except ValueError:
            raise CodecError(f"{where}: {name} is not JSON: {text!r}") from None
        if not isinstance(got, dict):
            raise CodecError(f"{where}: {name} must be a JSON object, got {text!r}")
        out.append(got)
    clash = sorted(k for k in out[1] if k in BP_REFUSED)
    if clash:
        raise CodecError(f"{where}: bp.{clash[0]}: the codec writes {clash[0]} from the design"
                         + (" (write cb. fields for a control behaviour)"
                            if clash[0] == "control_behavior" else ""))
    return out[0], out[1]


def made_fields(attrs, where="entity"):
    """Every entity field an entity's settings make: the typed ones' (:func:`control_behavior`)
    with the raw ``cb`` fields over them in its ``control_behavior``, and the raw ``bp`` fields."""
    fields = control_behavior(attrs, where)
    cb, bp = raw_fields(attrs, where)
    if cb:
        fields["control_behavior"] = dict(fields.get("control_behavior") or {}, **cb)
    fields.update(bp)
    return fields


def _normal(v, key=None):
    """Game fields as the codec compares them: an item signal the game exported without its
    ``type`` (item is the game's default, and it drops it) gets it back."""
    if isinstance(v, list):
        return [_normal(x, key) for x in v]
    if not isinstance(v, dict):
        return v
    out = {k: _normal(x, k) for k, x in v.items()}
    if (key in _SIGNAL_KEYS or (is_signal_key(key) and "name" in out)
            or (key == "filters" and "count" in out and "name" in out)):
        out.setdefault("type", "item")
    return out


def _settings_back(extra, fkp_attrs):
    """On reading a blueprint: the typed circuit settings in ``tags.fkp`` stand for the fields
    they make, and those fields are dropped, while the entity's fields are what they make. A
    blueprint the game exported (an item signal without its type) still matches; one whose
    fields differ (changed in the game) keeps the game's fields, and the typed settings go."""
    typed = {k: fkp_attrs[k] for k in CIRCUIT_ATTRS + RAW_ATTRS if k in fkp_attrs}
    if not typed:
        return
    try:
        want = made_fields(typed)
    except CodecError:
        for k in typed:
            del fkp_attrs[k]
        return
    game = {f: extra[f] for f in set(_CIRCUIT_FIELDS) | set(want) if f in extra}
    if _normal(game) == _normal(want):
        for f in game:
            del extra[f]
    else:
        for k in typed:
            del fkp_attrs[k]


def connector(colour, port=None):
    """The ``defines.wire_connector_id`` of a ``w`` end: red 1, green 2; a combinator's output
    side 3, 4. 2.1: copper 5 (a pole's, a power switch's left side), a power switch's right side
    6."""
    if colour == "copper":
        return 6 if port == "right" else POLE_COPPER
    return _CONNECTOR[colour][1 if port == "out" else 0]


def links_of(rows, wires):
    """[(i, ci, j, cj)] row indices for the ``w`` records ``wires`` whose two ends are among
    ``rows`` (by ``src``), the lower end first; a record with an end outside them is left out."""
    index = {r["src"]: i for i, r in enumerate(rows) if r.get("src") is not None}
    out = set()
    for w in wires:
        (ka, pa), (kb, pb) = fkpmod.split_end(w.a), fkpmod.split_end(w.b)
        if ka in index and kb in index:
            a = (index[ka], connector(w.colour, pa))
            b = (index[kb], connector(w.colour, pb))
            out.add(min(a, b) + max(a, b))
    return sorted(out)


def entity_type(name):
    """The game's type of an entity name (``fkcore.facts``), None when it has no such entity."""
    from . import facts
    return facts.entity(name).get("type")


def is_reset(cb):
    """True for ``RESET_CB`` as the codec writes it or as the game exports it (without the
    false ``circuit_enabled``)."""
    if not isinstance(cb, dict):
        return False
    got = {k: v for k, v in cb.items() if not (k == "circuit_enabled" and v is False)}
    return got == {"circuit_condition": RESET_CB["circuit_condition"]}


def _entity_json(row, number):
    entity = {
        "entity_number": number,
        "name": row["name"],
        "position": {"x": float(row["x"]), "y": float(row["y"])},
    }
    if row.get("direction") is not None:
        entity["direction"] = int(row["direction"])
    attrs = dict(row.get("attrs") or {})
    recipe = attrs.pop("recipe", None)
    if recipe:
        entity["recipe"] = recipe
    ug = attrs.pop("ug", None)
    if ug is not None:
        if ug not in _UG:
            raise CodecError(f"{row.get('src')}: ug must be in or out, got {ug!r}")
        entity["type"] = _UG[ug]
    attrs.pop("wire", None)                    # blueprint() writes it into the wires
    # 3.0: an inserter's own vectors (Bob's adjustable inserters) are game fields, not tags: they
    # come back from what the game exports (``fkcore.arms``)
    entity.update(armsmod.blueprint_fields(attrs))
    if row["name"] == "infinity-pipe" and attrs.get("fluid"):
        # 3.0: an InfinityPipe<fluid=...> pastes with its fluid set (full, at least), as the
        # lab sets the pipes it feeds ports with; fluid= stays in tags.fkp to come back
        entity["infinity_settings"] = {"name": attrs["fluid"], "percentage": 1,
                                       "mode": "at-least"}
    # the typed circuit settings make game fields, and stay in tags.fkp to come back as written;
    # the raw ones (2.1) too
    entity.update(made_fields(attrs, row.get("src")))
    config = attrs.pop("config", None)
    if config is not None:
        entity.update(_config(row, config, entity))
    if "control_behavior" not in entity and entity_type(row["name"]) in RESET_TYPES:
        entity["control_behavior"] = json.loads(json.dumps(RESET_CB))
    tags = {}
    if row.get("src") is not None:
        tags["src"] = row["src"]
    net = attrs.pop("net", None)
    if net is not None:
        tags["net"] = net
    if attrs:
        tags["fkp"] = attrs
    if tags:
        entity["tags"] = tags
    return entity


def _config(row, text, entity):
    try:
        fields = json.loads(text)
    except ValueError:
        raise CodecError(f"{row.get('src')}: config is not JSON: {text!r}") from None
    if not isinstance(fields, dict):
        raise CodecError(f"{row.get('src')}: config must be a JSON object, got {text!r}")
    clash = sorted(k for k in fields if k in _OWN or k in entity)
    if clash:
        raise CodecError(f"{row.get('src')}: config sets {', '.join(clash)}, which the codec "
                         f"writes from the row")
    _clamp_requests(fields)
    return fields


def _clamp_requests(fields):
    """A request filter's count can't exceed its ``max_count`` (the game refuses such a request):
    lower every count to its bound; a filter without one is left alone. In place, so a
    hand-written config can't write a request the game refuses."""
    rf = fields.get("request_filters")
    sections = rf.get("sections") if isinstance(rf, dict) else None
    if not isinstance(sections, list):
        return
    for section in sections:
        filters = section.get("filters") if isinstance(section, dict) else None
        if not isinstance(filters, list):
            continue
        for f in filters:
            if not isinstance(f, dict) or "max_count" not in f:
                continue
            count, most = f.get("count"), f["max_count"]
            if isinstance(count, (int, float)) and isinstance(most, (int, float)) and count > most:
                f["count"] = most


def config_text(fields):
    """Game fields as the canonical ``config`` attr: sorted keys, compact, ASCII JSON."""
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _owner(key):
    return key.rsplit("/", 1)[0] if "/" in key else ""


def parse_wire(text, where="wire"):
    """A ``wire`` attr -> [(connector, the other end's local name, its connector)]."""
    out = []
    for part in text.split(";"):
        bits = part.split(",")
        if len(bits) != 3 or not bits[0].isdigit() or not bits[2].isdigit() or not bits[1]:
            raise CodecError(f"{where}: wire must be <connector>,<local name>,<connector>[;...], "
                             f"got {text!r}")
        out.append((int(bits[0]), bits[1], int(bits[2])))
    return out


def circuit_wires(rows):
    """[(i, ci, j, cj)] row indices: the rows' ``wire`` attrs resolved under each row's owner, the
    lower (index, connector) end first. A wire whose other end is not among the rows is left out.
    Sorted, no duplicates."""
    index = {r["src"]: i for i, r in enumerate(rows) if r.get("src") is not None}
    out = set()
    for i, row in enumerate(rows):
        text = (row.get("attrs") or {}).get("wire")
        if not text or row.get("src") is None:
            continue
        owner = _owner(row["src"])
        for mine, local, theirs in parse_wire(text, row["src"]):
            j = index.get(f"{owner}/{local}" if owner else local)
            if j is not None:
                out.add(min((i, mine, j, theirs), (j, theirs, i, mine)))
    return sorted(out)


def wire_attrs(keys, wires):
    """{row index: ``wire`` attr} for circuit wires [(i, ci, j, cj)] between rows keyed ``keys``.
    Each wire goes on the end whose (key, connector) sorts first and names the other end by its
    local name; a wire with an end that has no key, or with ends under two owners, is left out."""
    links = {}
    for i, ci, j, cj in wires:
        a, b = keys[i], keys[j]
        if a is None or b is None or _owner(a) != _owner(b):
            continue
        if (fkpmod.natural_key(b), cj) < (fkpmod.natural_key(a), ci):
            i, ci, j, cj, b = j, cj, i, ci, a
        links.setdefault(i, set()).add((ci, b.rsplit("/", 1)[-1], cj))
    return {i: ";".join(f"{c},{k},{d}" for c, k, d in
                        sorted(ls, key=lambda w: (w[0], fkpmod.natural_key(w[1]), w[2])))
            for i, ls in links.items()}


def pole_domain(row):
    """The power domain a pole row belongs to (2.1): None for a pole with no ``domain`` attr (the
    main grid, which every plot's joins), else the domain's name and its plot (a composed key's
    ``surface:plot``), so two plots' domains of one name stay apart."""
    dom = (row.get("attrs") or {}).get("domain")
    if not dom:
        return None
    head = str(row.get("src") or "").split("/", 1)[0]
    return dom, head if ":" in head else ""


def pole_wires(rows, cat):
    """[(i, j)] row indices to wire: a minimum spanning forest over the electric poles, an edge
    only within both poles' wire reach, at most MAX_POLE_WIRES per pole. Deterministic. 2.1: only
    poles of one power domain (:func:`pole_domain`) are wired together; a copper line between
    domains is a ``w`` record of its own."""
    poles = [i for i, r in enumerate(rows) if cat.has_entity(r["name"])
             and cat.kind(r["name"]) == "electric-pole"]
    dom = {i: pole_domain(rows[i]) for i in poles}
    edges = []
    for a in range(len(poles)):
        for b in range(a + 1, len(poles)):
            i, j = poles[a], poles[b]
            if dom[i] != dom[j]:
                continue
            ri, rj = rows[i], rows[j]
            d2 = (float(ri["x"]) - float(rj["x"])) ** 2 + (float(ri["y"]) - float(rj["y"])) ** 2
            reach = min(cat.pole_reach(ri["name"]), cat.pole_reach(rj["name"]))
            if d2 <= reach * reach + 1e-9:
                edges.append((d2, i, j))
    edges.sort()
    parent = {i: i for i in poles}
    degree = dict.fromkeys(poles, 0)

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    out = []
    for _d2, i, j in edges:
        ri, rj = find(i), find(j)
        if ri == rj or degree[i] >= MAX_POLE_WIRES or degree[j] >= MAX_POLE_WIRES:
            continue
        parent[max(ri, rj)] = min(ri, rj)
        degree[i] += 1
        degree[j] += 1
        out.append((i, j))
    return out


def entity_json(row, number):
    """One row as the blueprint entity a paste lays (what :func:`blueprint` writes for it)."""
    return _entity_json(row, number)


def wire_list(rows, wires=None, links=None):
    """The blueprint's ``wires``: [[i, ci, j, cj]] with 1-based entity numbers. ``wires``: [(i, j)]
    row indices of poles to join by copper wire (see :func:`pole_wires`); the rows' own circuit
    wires (``wire`` attrs, see :func:`circuit_wires`) follow them, then ``links`` (2.0):
    [(i, ci, j, cj)] the layout's ``w`` records as row indices (:func:`links_of`)."""
    rows = list(rows or [])
    out = [[i + 1, POLE_COPPER, j + 1, POLE_COPPER] for i, j in wires or ()]
    own = circuit_wires(rows)
    out += [[i + 1, ci, j + 1, cj] for i, ci, j, cj in own]
    out += [[i + 1, ci, j + 1, cj] for i, ci, j, cj in sorted(set(links or ()) - set(own))]
    return out


def blueprint(rows, description=None, label=None, wires=None, links=None) -> str:
    """Rows -> a blueprint string; ``entity_number`` follows row order; ``wires`` and ``links`` as
    :func:`wire_list` takes them. Deterministic."""
    rows = list(rows or [])
    entities = [_entity_json(row, i + 1) for i, row in enumerate(rows)]
    bp = {"item": "blueprint", "entities": entities, "version": BLUEPRINT_VERSION,
          "icons": BLUEPRINT_ICONS}
    out = wire_list(rows, wires, links)
    if out:
        bp["wires"] = out
    if description:
        bp["description"] = description
    if label:
        bp["label"] = label
    text = json.dumps({"blueprint": bp}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "0" + base64.b64encode(zlib.compress(text.encode("utf-8"), 9)).decode("ascii")


def read_blueprint(s):
    """A blueprint string -> (rows ordered by ``entity_number``, description or None). Circuit
    wires come back as the rows' ``wire`` attrs, which needs the rows' ``tags.src`` keys."""
    rows, description, wires = parse_blueprint(s)
    for i, text in wire_attrs([r["src"] for r in rows], wires).items():
        rows[i]["attrs"]["wire"] = text
    return rows, description


def parse_blueprint(s):
    """A blueprint string -> (rows ordered by ``entity_number``, description or None, circuit
    wires [(i, ci, j, cj)] as row indices, the lower end first). Copper wires are dropped: the
    codec derives them."""
    if not isinstance(s, str) or not s.strip():
        raise CodecError("not a blueprint string")
    text = s.strip()
    if not text[0].isdigit():
        raise CodecError("a blueprint string starts with a version digit")
    body = text[1:]
    try:
        raw = zlib.decompress(base64.b64decode(body + "=" * (-len(body) % 4)))
        document = json.loads(raw.decode("utf-8"))
    except Exception:
        raise CodecError("payload is not base64(zlib(JSON))") from None
    section = document.get("blueprint") if isinstance(document, dict) else None
    if not isinstance(section, dict):
        raise CodecError("payload has no blueprint object")
    entities = section.get("entities") or []
    if not isinstance(entities, list):
        raise CodecError("blueprint entities is not a list")
    ordered = sorted(((i, e) for i, e in enumerate(entities) if isinstance(e, dict)),
                     key=lambda p: (p[1].get("entity_number", p[0]), p[0]))
    rows, number = [], {}
    for k, (_i, entity) in enumerate(ordered):
        if "entity_number" in entity:
            number[entity["entity_number"]] = k
        pos = entity.get("position") or {}
        tags = entity.get("tags") or {}
        extra = {f: v for f, v in entity.items() if f not in _OWN}
        attrs = {}
        if extra.get("recipe"):
            attrs["recipe"] = extra.pop("recipe")
        if "net" in tags:
            attrs["net"] = tags["net"]
        if extra.get("type") in _UG_BACK:
            attrs["ug"] = _UG_BACK[extra.pop("type")]
        attrs.update(armsmod.from_blueprint(entity.get("name"), entity.get("direction") or 0,
                                            extra))
        fkp_attrs = dict(tags.get("fkp") or {})
        if is_reset(extra.get("control_behavior")):
            del extra["control_behavior"]
        _settings_back(extra, fkp_attrs)
        if extra:
            attrs["config"] = config_text(extra)
        attrs.update(fkp_attrs)
        rows.append({"src": tags.get("src"), "name": entity.get("name"),
                     "x": _num(pos.get("x", 0.0)), "y": _num(pos.get("y", 0.0)),
                     "direction": entity.get("direction"), "attrs": attrs})
    wires = set()
    for w in section.get("wires") or []:
        if not (isinstance(w, list) and len(w) == 4 and all(isinstance(v, int) for v in w)):
            continue
        a, ca, b, cb = w
        if ca in COPPER or cb in COPPER or a not in number or b not in number:
            continue
        i, j = number[a], number[b]
        wires.add(min((i, ca, j, cb), (j, cb, i, ca)))
    return rows, section.get("description"), sorted(wires)


def _num(v):
    v = float(v)
    return int(v) if v == int(v) else v


def fkp_to_blueprint(lay, cat=None) -> str:
    """With ``cat``, the poles are wired (derived wires; reading drops them again). The layout's
    ``w`` records are its circuit wires."""
    rows = rows_of(lay)
    return blueprint(rows, description="\n".join(fkpmod.record_lines(lay)),
                     label=f"{lay.level} {lay.name}", wires=pole_wires(rows, cat) if cat else None,
                     links=links_of(rows, lay.wires))


def blueprint_to_fkp(s, path="<blueprint>"):
    rows, description, wires = parse_blueprint(s)
    if not description:
        raise CodecError("the blueprint carries no .fkp records in its description")
    lay = fkpmod.loads(description, path)
    explained = set(links_of(rows, lay.wires))
    for i, text in wire_attrs([r["src"] for r in rows],
                              [w for w in wires if w not in explained]).items():
        rows[i]["attrs"]["wire"] = text
    for row in rows:
        if row["src"] is None:
            raise CodecError(f"{row['name']} at {row['x']},{row['y']} has no tags.src key")
        lay.add(fkpmod.Entity(row["src"], row["name"], row["x"], row["y"], row["direction"],
                              row["attrs"]))
    return lay
