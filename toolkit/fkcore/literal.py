"""fkcore.literal: entities from a blueprint or an entity list, as a Literal's pinned entities.

``Literal<file="plant-w3.json", box="x0,y0,x1,y1">`` (CORE-V1.md §5): every entity is written by
hand somewhere else, here a file in the game codebase. Two file shapes are read:

- the kickoff's entity list (``v4-hand/kickoff/plant-w3.json``): ``{"entities": [{name, wx, wy,
  d}, ...]}`` in world positions (``bx``/``by`` or ``x``/``y`` are accepted too). When the file
  also holds its blueprint (``"bp": "0e..."``), each entity takes its game settings from the
  blueprint entity of the same name at its blueprint position (``bx``/``by``, else its world
  position);
- a blueprint string (a ``.txt`` holding one, or a JSON ``{"bp": "0e..."}`` without an entity
  list), read with ``fkcore.codec``.

A bare JSON list of entities is the entity list too (1.4.2: the 2005 player's ``[{name, x, y}]``
file read as a blueprint string, "a blueprint string starts with a version digit"). A listed entity
may set itself (2.0.16): ``recipe``, ``ug``, ``config`` (the game's fields as JSON text), or the
sugar ``request`` ({"logistic-robot": 50}) and ``request_filters``, so a two-entity fixture needs no
blueprint at all.

The game settings travel verbatim (``fkcore.codec``): ``recipe``, an underground's ``ug``, every
other game field as ``config`` JSON (a requester's request, an inserter's circuit condition, a
reactor's temperature read), and each circuit wire between two entities inside the box as
``wire``. V1 has no circuit model, so only a Literal carries them. Copper wires are not kept: the
codec derives them.

``box`` keeps the entities whose position lies inside it (tile coordinates, x0 <= x < x1) and
makes its corner the Literal's frame origin, so a Literal placed at its plot's corner sits where
the file says. Without a box the frame starts at the entities' top-left tile.

2.1 (PLAN-2.1 H): a JSON file may name its machines, ``"names": {"asm10": 37}``, a name to the
index of an entity in the file's list (from 0; a blueprint's entity order when the file holds
only ``bp``). ``automall.asm10`` is then a net end like any machine's (``names_of``, read by
``fkcore.elaborate``). ``fk capture`` writes a name for each machine it captures.

Keys are ``<inst>/<prototype><k>`` in file order. Carriers get a net by connectivity, since a file
has no netlist: each group of touching pipes is ``<inst>/fluid<k>``, each group of touching belts
``<inst>/belt<k>``, each inserter ``<inst>/hand<k>`` (one inserter is a route). Rename them in source
when they matter. Pure, stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re

from . import codec
from . import fkp as fkpmod
from . import snapshot

__all__ = ["LiteralError", "read_entities", "import_entities", "signature", "names_of",
           "machine_names"]

_DIRS = {0, 4, 8, 12}
_PIPES = ("pipe", "pipe-to-ground", "pump")
_BELTS = ("transport-belt", "underground-belt", "splitter", "loader", "loader-1x1")
#: the attrs of a blueprint row that are the game's own settings (net and tags.fkp are not)
_SETTINGS = ("recipe", "ug", "config")


class LiteralError(ValueError):
    pass


def read_entities(path):
    """-> (rows, wires) from an entity-list JSON or a blueprint string file. rows: [(name, x, y,
    dir, settings)] in file order, ``settings`` the entity's game settings (``recipe``, ``ug``,
    ``config``); wires: the circuit wires [(i, ci, j, cj)] between rows, as row indices.

    A listed entity may set itself: ``recipe``, ``ug`` and ``config`` verbatim, or the sugar
    ``request`` ({"logistic-robot": 50}) / ``request_filters`` (the game's own fields), turned into
    ``config`` (2.0.16: before this, a two-entity fixture -- a requester and an inserter -- could
    not say what the chest asks for in an entity list at all, so every player hand-wrote a
    blueprint string; the 0928 player generated one from the toolkit's source, over ssh). The
    blueprint, if the file also holds one, sets what the list leaves unset."""
    text = snapshot.read(path)
    data = None
    if path.lower().endswith(".json"):
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise LiteralError(f"{path}: not JSON ({exc})") from None
    if isinstance(data, list):
        data = {"entities": data}
    listed = isinstance(data, dict) and isinstance(data.get("entities"), list)
    if isinstance(data, dict) and not listed and not data.get("bp"):
        raise LiteralError(f'{path}: a Literal file is a blueprint string (.txt), JSON {{"bp": '
                           f'"0e..."}}, or an entity list: [{{"name", "x", "y", "direction"}}, '
                           f'...] (or {{"entities": [...]}})')
    bp = data.get("bp") if isinstance(data, dict) else text.strip()
    got, wires = [], []
    if bp or not listed:
        try:
            got, _desc, wires = codec.parse_blueprint(bp)
        except codec.CodecError as exc:
            raise LiteralError(f"{path}: {exc}") from None
    if not listed:
        return [(r["name"], float(r["x"]), float(r["y"]), r["direction"], _settings(r["attrs"]))
                for r in got], wires
    # the list says what stands where; the blueprint, matched by name and position, how it is set
    at = {(r["name"], float(r["x"]), float(r["y"])): k for k, r in enumerate(got)}
    rows, row_of = [], {}
    for e in data["entities"]:
        x = e.get("wx", e.get("bx", e.get("x")))
        y = e.get("wy", e.get("by", e.get("y")))
        if x is None and isinstance(e.get("position"), dict):
            x, y = e["position"]["x"], e["position"]["y"]
        d = e.get("d", e.get("direction"))
        k = at.get((e["name"], float(e.get("bx", x)), float(e.get("by", y))))
        if k is not None:
            row_of[k] = len(rows)
        own = _own_settings(e)
        rows.append((e["name"], float(x), float(y), None if d is None else int(d),
                     own or (_settings(got[k]["attrs"]) if k is not None else {})))
    return rows, [(row_of[i], ci, row_of[j], cj) for i, ci, j, cj in wires
                  if i in row_of and j in row_of]


def _own_settings(e):
    """The settings a listed entity writes itself: ``recipe``/``ug``/``config`` verbatim, or
    ``request`` / ``request_filters`` as the ``config`` text the game takes. ``{}`` when it sets
    nothing (then the file's blueprint, if any, decides)."""
    out = {k: e[k] for k in _SETTINGS if k in e}
    if "config" in out:
        return out
    fields = None
    if isinstance(e.get("request_filters"), dict):
        fields = {"request_filters": e["request_filters"]}
    elif isinstance(e.get("request"), dict):
        filters = [{"comparator": "=", "count": int(count), "index": i, "name": item,
                    "quality": "normal"}
                   for i, (item, count) in enumerate(sorted(e["request"].items()), 1)]
        if filters:
            fields = {"request_filters": {"sections": [{"filters": filters, "index": 1}]}}
    if fields is not None:
        out["config"] = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return out


def _settings(attrs):
    """A blueprint row's game settings: ``recipe``/``ug``/``config``, and its circuit settings,
    which the codec reads back typed (``if``, ``read``, ...; 2.1: they were dropped before)."""
    return {k: attrs[k] for k in _SETTINGS + codec.CIRCUIT_ATTRS if k in attrs}


def _box(text):
    try:
        x0, y0, x1, y1 = (int(v) for v in str(text).split(","))
    except ValueError:
        raise LiteralError(f"box must be \"x0,y0,x1,y1\", got {text!r}") from None
    return x0, y0, x1, y1


def import_entities(path, inst, cat, box=None):
    """-> (entities in the Literal's own frame, frame size, frame origin in file coordinates)."""
    rows, wires = read_entities(path)
    kept = list(range(len(rows)))
    if box is not None:
        x0, y0, x1, y1 = _box(box)
        kept = [k for k in kept if x0 <= math.floor(rows[k][1]) < x1
                and y0 <= math.floor(rows[k][2]) < y1]
    pos = {k: n for n, k in enumerate(kept)}
    wires = [(pos[i], ci, pos[j], cj) for i, ci, j, cj in wires if i in pos and j in pos]
    rows = [rows[k] for k in kept]
    if not rows:
        raise LiteralError(f"{path}: no entities" + (f" inside box {box}" if box else ""))
    unknown = sorted({r[0] for r in rows if not cat.has_entity(r[0])})
    if unknown:
        raise LiteralError(f"{path}: prototypes the catalog lacks: {', '.join(unknown)}")
    tiles = [(r, cat.tiles(r[0], r[1], r[2], r[3] if r[3] is not None else 0)) for r in rows]
    if box is None:
        x0 = min(t[0] for _r, ts in tiles for t in ts)
        y0 = min(t[1] for _r, ts in tiles for t in ts)
        x1 = max(t[0] for _r, ts in tiles for t in ts) + 1
        y1 = max(t[1] for _r, ts in tiles for t in ts) + 1
    count, out = {}, []
    for (name, x, y, d, settings), _ts in tiles:
        k = count.get(name, 0)
        count[name] = k + 1
        out.append(fkpmod.Entity(f"{inst}/{name}{k}", name, _clean(x - x0), _clean(y - y0),
                                 d if d in _DIRS or d is None else d, dict(settings)))
    for n, text in codec.wire_attrs([e.key for e in out], wires).items():
        out[n].attrs["wire"] = text
    _name_nets(out, inst, cat)
    return out, (x1 - x0, y1 - y0), (x0, y0)


def _name_nets(ents, inst, cat):
    """Give every carrier one net: touching pipes / belts group, each inserter alone."""
    by_tile = {}
    for e in ents:
        for t in fkpmod.tiles_of(cat, e):
            by_tile[t] = e
    groups = {"fluid": [], "belt": []}
    seen = set()
    for e in ents:
        kind = cat.kind(e.name)
        if kind == "inserter":
            e.attrs["net"] = f"{inst}/hand{sum(1 for x in ents[:ents.index(e)] if cat.kind(x.name) == 'inserter')}"
            continue
        family = "fluid" if e.name in _PIPES or kind in _PIPES else \
            "belt" if kind in _BELTS else None
        if family is None or e.key in seen:
            continue
        group, todo = [], [e]
        while todo:
            cur = todo.pop()
            if cur.key in seen:
                continue
            seen.add(cur.key)
            group.append(cur)
            for tx, ty in fkpmod.tiles_of(cat, cur):
                for nx, ny in ((tx + 1, ty), (tx - 1, ty), (tx, ty + 1), (tx, ty - 1)):
                    o = by_tile.get((nx, ny))
                    if o is None or o.key in seen:
                        continue
                    ok = cat.kind(o.name)
                    if (family == "fluid" and (o.name in _PIPES or ok in _PIPES)) or \
                            (family == "belt" and ok in _BELTS):
                        todo.append(o)
        net = f"{inst}/{family}{len(groups[family])}"
        groups[family].append(net)
        for g in group:
            g.attrs["net"] = net


_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
#: 2.1, ``fk capture``'s names: a machine's prototype (or its kind) -> the stem of its name
_STEMS = {"chemical-plant": "chem", "oil-refinery": "refinery", "centrifuge": "centrifuge",
          "rocket-silo": "silo"}
_KIND_STEMS = {"assembling-machine": "asm", "furnace": "furnace", "mining-drill": "drill",
               "lab": "lab", "rocket-silo": "silo"}


def machine_names(names, cat):
    """``{name: index}`` for the machines among ``names`` (entity names in file order): ``asm1``,
    ``asm2``, ... ``furnace1``, ... ``drill1``, ``lab1``, numbered from 1 per stem, as ``fk
    capture`` writes them (2.1). Crafters, drills and labs: what a Literal makes machines of."""
    out, count = {}, {}
    for k, name in enumerate(names):
        try:
            kind = cat.kind(name)
        except KeyError:
            continue
        stem = _STEMS.get(name) or _KIND_STEMS.get(kind)
        if stem is None:
            continue
        count[stem] = count.get(stem, 0) + 1
        out[f"{stem}{count[stem]}"] = k
    return out


def names_of(path, ents, box=None):
    """``{name: entity key}`` from a JSON Literal file's ``"names"`` (2.1): each name an index
    into the file's entities (from 0), mapped to the key ``import_entities`` gave it (``ents``,
    after ``box``). {} when the file names nothing."""
    if not path.lower().endswith(".json"):
        return {}
    try:
        data = json.loads(snapshot.read(path))
    except ValueError:
        return {}
    if not isinstance(data, dict) or not data.get("names"):
        return {}
    names = data["names"]
    if not isinstance(names, dict):
        raise LiteralError(f'{path}: "names" maps a name to an entity\'s index, like '
                           '{"asm10": 37}')
    rows, _wires = read_entities(path)
    kept = list(range(len(rows)))
    if box is not None:
        x0, y0, x1, y1 = _box(box)
        kept = [k for k in kept if x0 <= math.floor(rows[k][1]) < x1
                and y0 <= math.floor(rows[k][2]) < y1]
    pos = {k: n for n, k in enumerate(kept)}
    out = {}
    for name, idx in names.items():
        if not _NAME.match(str(name)):
            raise LiteralError(f"{path}: names {name}: a name is a word without dashes (asm10), "
                               "so a net can name it")
        if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx < len(rows):
            raise LiteralError(f"{path}: names {name}: {idx!r} is no entity's index (the file has "
                               f"{len(rows)}, from 0)")
        if idx not in pos:
            raise LiteralError(f"{path}: names {name}: entity {idx} ({rows[idx][0]}) lies outside "
                               f"box {box}")
        out[str(name)] = ents[pos[idx]].key
    return out


def signature(ents, size):
    """Over keys, positions, directions, attrs (nets and game settings) and the frame: a file
    edit that changes any of them makes the instance stale."""
    rows = sorted((e.key, e.name, float(e.x), float(e.y), e.dir, sorted(e.attrs.items()))
                  for e in ents)
    blob = json.dumps([rows, list(size)], separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


def _clean(v):
    return int(v) if float(v).is_integer() else v


def resolve(fk_path, file):
    """A Literal's file, relative to the .fk that names it (inside the game codebase)."""
    return os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(fk_path)), file))
