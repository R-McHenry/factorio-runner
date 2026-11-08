"""fkcore.placements: the registry of what was pasted, one record per artifact (3.1, PLAN-3.1
31-placements; Rory 11-08).

Rory: agents either track objects in the world with the toolkit, or they don't, and then a
conflicting design must produce a MISMATCH, never a silently adjusted plan or a build over or
around. "Several small adjustments at those points beat a big world-object tracking system." So
the registry is a json file per surface, ``out/placements/<surface>.json``, next to the per-entity
record ``out/own/<surface>.json`` (``fkcore.own``, which stays underneath: what the applies pasted,
entity by entity). It lives in ``out`` because it is a record of the world, not a design input
(nothing in ``game/`` reads it to lay anything out), and per surface because areas are.

One record per paste, replaced on a rebuild (the same id, or the same module pasted inside its
old area), never piled up:

    {"id": "iron", "surface": "nauvis", "module": "smelting", "instance": "iron",
     "artifact": "store:3f2a...", "origin": [40, -20], "rot": 0, "area": [40, -20, 70, 10],
     "tick": 123456, "label": "fk build nauvis.build.fkb", "source": "build",
     "file": null, "count": 212}

- ``fk build``'s apply writes one per factory (plot) in the paste, id = the factory's name on the
  surface, plus ``shared`` for the surface's own entities (cover roboports, shared infra);
- a build alone (``fkcore.alone``) writes one into the game's registry for its slot;
- ``fk paste FILE --at X,Y [--rot R] [--as NAME]`` writes one for the captured Literal file or
  blueprint it pasted as ghosts (``source: paste``, ``file``);
- ``fk register AREA MODULE[/INSTANCE] | --as NAME`` says what stands in AREA is that artifact.

The artifact key is the compile store's key when the module has one (``store:<key>``,
``fkcore.compiled``), else a hash of the pasted rows relative to the origin (``rows:<sha>``).

What reads it: ``sense`` (a standing entity inside a placement that is not this design's is
``placed``, explained, not a foreign change; on a design tile it is a MISMATCH whose owner is the
placement), ``fk look ID`` / ``fk diff ID`` (the placement's area, headed by its module and
origin), ``fk capture --from ID`` (the selection as source, anchored where it stands), ``fk adopt
ID``. Pure but for ``load`` / ``save``; stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass, field

from . import fkp as fkpmod

__all__ = ["Record", "path", "load", "save", "put", "put_all", "unique_id", "rows_key",
           "artifact_of", "from_apply", "explaining", "at", "find", "resolve", "head", "lines",
           "paste_rows", "SHARED"]

#: the id of the surface's own entities in a build's paste (no plot)
SHARED = "shared"


@dataclass
class Record:
    id: str
    surface: str
    module: str                        # the module (or --as NAME, or the surface for SHARED)
    instance: str | None = None        # the factory / instance on the surface, when it has one
    artifact: str | None = None        # store:<key> | rows:<sha> | None (register: unknown)
    origin: list = field(default_factory=lambda: [0, 0])
    rot: int = 0
    area: list = field(default_factory=lambda: [0, 0, 0, 0])
    tick: int | None = None
    label: str = ""
    source: str = "build"              # build | alone | paste | register
    file: str | None = None            # fk paste: the file pasted (relative to the game folder)
    count: int = 0                     # entities in the paste

    def contains(self, x, y):
        """True when tile (x, y) (a position works too) is inside the record's area."""
        tx, ty = math.floor(float(x)), math.floor(float(y))
        x0, y0, x1, y1 = self.area
        return x0 <= tx < x1 and y0 <= ty < y1

    def as_json(self):
        return asdict(self)


def path(game, surface):
    return os.path.join(game, "out", "placements", f"{surface}.json")


def load(game, surface):
    """{id: Record} of ``surface``'s registry; {} when there is none yet."""
    p = path(game, surface)
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
    for row in data.get("placements") or ():
        try:
            rec = Record(**{k: row.get(k) for k in Record.__dataclass_fields__ if k in row})
        except TypeError:
            continue
        out[rec.id] = rec
    return out


def save(game, surface, records):
    p = path(game, surface)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    rows = [records[k].as_json() for k in sorted(records, key=fkpmod.natural_key)]
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"surface": surface,
                   "about": "what was pasted, one record per artifact (fkcore.placements)",
                   "placements": rows}, fh, indent=1)
        fh.write("\n")


def put(records, rec):
    """``records`` with ``rec`` in it: a record of the same id goes (a rebuild replaces, it does
    not pile up). -> records."""
    records = dict(records or {})
    records[rec.id] = rec
    return records


def put_all(game, surface, recs):
    """Load, put each of ``recs``, save. -> the registry."""
    records = load(game, surface)
    for rec in recs:
        records = put(records, rec)
    save(game, surface, records)
    return records


def unique_id(records, stem, module, origin):
    """The id for a paste of ``module`` at ``origin`` named by ``stem``: an existing record of the
    same module whose area holds the origin (a rebuild: its id), else ``stem``, ``stem-2``, ...
    not yet taken."""
    for rec in records.values():
        if rec.module == module and rec.contains(*origin):
            return rec.id
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(stem)).strip("-") or "paste"
    name, n = stem, 2
    while name in records:
        name, n = f"{stem}-{n}", n + 1
    return name


def rows_key(rows, origin=(0, 0)):
    """``rows:<sha>`` over the pasted rows' names, positions relative to ``origin`` and
    directions: the artifact key of a paste no compile store entry stands for."""
    ox, oy = origin
    rel = sorted((r["name"], round(float(r["x"]) - ox, 3), round(float(r["y"]) - oy, 3),
                  r.get("direction")) for r in rows)
    blob = json.dumps(rel, separators=(",", ":"))
    return "rows:" + hashlib.sha1(blob.encode()).hexdigest()[:12]


def artifact_of(out, module, rows, origin=(0, 0)):
    """The compile store's current key for ``module`` (``store:<key>``) when it has one under
    ``out``, else :func:`rows_key`."""
    if out and module:
        from . import compiled
        key = compiled.current(out, module)
        if key:
            return f"store:{key}"
    return rows_key(rows, origin)


def _factory_of(src, s):
    """The plot (factory) a composed key belongs to, or None for the surface's own."""
    local = src.split(":", 1)[-1]
    first = local.split("/", 1)[0]
    return first if first in s.plots else None


def from_apply(s, rows, cat, modules=None, out=None, tick=None, label=""):
    """The records of a build's paste of ``rows`` (its codec rows, ``apply.scope_rows``) on the
    composed surface ``s``: one per plot with rows in the paste (id = the factory, origin and
    rot the plot's, area the plot's box grown to its rows), plus ``SHARED`` for the surface's own
    rows. ``modules``: {factory: module name} (the surface file's places)."""
    from .apply import scope_area
    by = {}
    for r in rows:
        by.setdefault(_factory_of(r["src"], s), []).append(r)
    recs = []
    for factory, mine in sorted(by.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
        box = scope_area(mine, cat, margin=0)
        if factory is None:
            recs.append(Record(SHARED, s.name, s.name, None, rows_key(mine), [box[0], box[1]], 0,
                               list(box), tick, label, "build", None, len(mine)))
            continue
        p = s.plots[factory]
        module = (modules or {}).get(factory) or p.params.get("module") or factory
        px, py, px1, py1 = p.box()
        area = [min(px, box[0]), min(py, box[1]), max(px1, box[2]), max(py1, box[3])]
        recs.append(Record(factory, s.name, module, factory,
                           artifact_of(out, module, mine, (p.x, p.y)), [p.x, p.y], int(p.rot),
                           area, tick, label, "build", None, len(mine)))
    return recs


def explaining(records, s):
    """The records that explain standing entities for a diff of the composed surface ``s``: every
    placement but the design's own (its plots' ids and ``SHARED``): those are explained by the
    design and the own record, and what stands in them and is not the design's is the diff's
    difference, as before."""
    mine = set(s.plots) | {SHARED}
    return [records[k] for k in sorted(records or {}, key=fkpmod.natural_key) if k not in mine]


def at(records, x, y):
    """The first record (by id) whose area holds tile (x, y), or None."""
    recs = list(records.values()) if isinstance(records, dict) else list(records or ())
    for rec in sorted(recs, key=lambda r: fkpmod.natural_key(r.id)):
        if rec.contains(x, y):
            return rec
    return None


def find(game, pid, surface=None):
    """(surface, Record) of placement ``pid`` in ``surface``'s registry, or in every registry of
    the game when none is named; None when there is none."""
    folder = os.path.join(game, "out", "placements")
    names = [f"{surface}.json"] if surface else (sorted(os.listdir(folder))
                                                 if os.path.isdir(folder) else [])
    for name in names:
        if not name.endswith(".json"):
            continue
        recs = load(game, name[:-5])
        if pid in recs:
            return name[:-5], recs[pid]
    return None


def is_area(text):
    return bool(re.fullmatch(r"-?\d+,-?\d+,-?\d+,-?\d+", (text or "").strip()))


def resolve(game, text, surface=None):
    """``X0,Y0,X1,Y1`` -> (None, area); a placement id -> (its Record, its area). KeyError when
    the id is unknown (its text names the ids there are)."""
    if is_area(text):
        area = tuple(int(v) for v in text.split(","))
        if area[2] <= area[0] or area[3] <= area[1]:
            raise ValueError(f"an area is X0,Y0,X1,Y1 with X1 > X0 and Y1 > Y0, got {text}")
        return None, area
    got = find(game, text, surface)
    if got is None:
        have = []
        folder = os.path.join(game, "out", "placements")
        if os.path.isdir(folder):
            for name in sorted(os.listdir(folder)):
                if name.endswith(".json"):
                    have += sorted(load(game, name[:-5]))
        raise KeyError(f"no placement {text!r}" + (f" (placements: {', '.join(have[:12])}"
                                                   f"{' ...' if len(have) > 12 else ''})"
                                                   if have else " (no placements recorded yet)"))
    return got[1], tuple(got[1].area)


def head(rec):
    """One line that says what the placement is, in source terms."""
    what = rec.module if not rec.instance or rec.instance == rec.module \
        else f"{rec.module} as {rec.instance}"
    out = (f"placement {rec.id}: {what} at {fkpmod.fmt(rec.origin[0])},{fkpmod.fmt(rec.origin[1])}"
           + (f" rot {rec.rot}" if rec.rot else "")
           + f", area {','.join(str(int(v)) for v in rec.area)}, {rec.count} entities")
    if rec.artifact:
        out += f", {rec.artifact}"
    out += f" ({rec.source}" + (f" of {rec.file}" if rec.file else "") \
        + (f", tick {rec.tick}" if rec.tick is not None else "") + ")"
    return out


def lines(records):
    """The registry as a listing."""
    if not records:
        return ["placements: none recorded yet (a build's apply, fk paste, fk register write them)"]
    return [f"placements: {len(records)}"] + [f"  {head(records[k])}"
                                              for k in sorted(records, key=fkpmod.natural_key)]


def _about_origin(x, y, rot):
    for _ in range(rot % 4):
        x, y = -y, x
    return x, y


def _clean(v):
    v = float(v)
    return int(v) if v.is_integer() else v


def paste_rows(file, inst, cat, at, rot=0):
    """The codec rows of a Literal file (``fkcore.literal``: a blueprint string, a ``{"bp"}`` or
    an entity list, a capture's file) placed with its own origin, the file's (0, 0), at ``at``
    and turned ``rot`` quarter turns clockwise about it (3.0.1: origins, not edges). Keyed
    ``<inst>/<name><k>`` as a Literal's entities are, with their settings and circuit wires."""
    from . import literal
    ents, _size, (x0, y0) = literal.import_entities(file, inst, cat)
    rot = int(rot) % 4
    rows = []
    for e in ents:
        fx, fy = float(e.x) + x0, float(e.y) + y0
        tx, ty = _about_origin(fx, fy, rot)
        d = e.dir
        if d is not None and rot:
            d = (int(d) + 4 * rot) % 16
        rows.append({"src": e.key, "name": e.name, "x": _clean(at[0] + tx),
                     "y": _clean(at[1] + ty), "direction": d, "attrs": dict(e.attrs)})
    return rows
