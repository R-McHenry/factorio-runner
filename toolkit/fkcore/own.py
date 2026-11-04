"""fkcore.own: the toolkit's own entities, the record of what the applies pasted (1.2).

The world can't say which entities are the toolkit's: robots build a pasted ghost with no
last_user, as they built the kit. ``out/own/<surface>.json`` remembers them: every composed entity
an apply pasted (its key, name, position, direction and, for a carrier, its surface net
``<factory>/<net>``). With it:

- an entity of the toolkit's that the design has dropped (a row replaced, a plot moved, shrunk or
  emptied, a place line removed) is ``unowned`` in a diff or a look, whatever the watch policy
  says, and the next apply orders it out, an unbuilt ghost included (``fkcore.sense``). A player
  who touched it (a ``last_user``) makes it a foreign change again, under the policy. The 0534 run
  (2026-10-20) kept its own dropped rows under ``watch plots: keep``, left 166 entities standing
  outside every plot after a plot move, and 28 ghosts after a factory was emptied;
- a carrier laid again under another net is known by the net it was pasted under (``nets``);
- 2.0 (circuits in the source): an entity pasted with circuit settings (a condition, a reading, a
  filter, its wires; a chest's slot limit and request too) keeps a signature of them
  (``settings``). When the
  design's differ, the next apply orders it out before the paste, so robots lay it fresh with the
  new ones: a changed limit reaches the world on the next build (the 2039 run's mall script set
  its limits again by Lua after every rebuild, and its chest bars by hand: "a limit change is not
  re-pasted"). A record written before 2.0 has no signatures (``UNKNOWN``, kept as ``?``):
  nothing is laid fresh for them until an apply writes the record again.

The record is seeded once, when there is none, from the .fkp files last written (``seed``: the
flow's write_layout does it before writing the new ones, so a run upgraded from 1.1 starts from
what it built, and drops nothing it dropped before). Each play apply updates it (``update``):
inside the apply's scope it becomes the design that was pasted, plus the toolkit's dropped
entities that still stood (ordered out; robots take them up); outside the scope it stays. Pure
but for ``load`` and ``save``; stdlib only.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from . import compose
from . import fkp as fkpmod

__all__ = ["Row", "at", "path", "load", "save", "rows_of", "seed", "dropped", "in_scope",
           "update", "nets", "settings", "resettled", "UNKNOWN"]

#: a row's settings signature in a record written before 2.0: not known
UNKNOWN = "?"


@dataclass(frozen=True)
class Row:
    key: str                          # the composed key it was pasted under
    name: str
    x: float
    y: float
    dir: int | None = None
    net: str | None = None            # a carrier's surface net, "<factory>/<net>"
    sig: str | None = None            # its circuit settings' signature (2.0), None: it has none


def at(name, x, y):
    """The record's index: name and position, as a diff matches the world."""
    return (name, round(float(x), 3), round(float(y), 3))


def path(game, surface):
    return os.path.join(game, "out", "own", f"{surface}.json")


def load(game, surface):
    """The record {at: Row}, or None when there is none yet."""
    p = path(game, surface)
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as fh:
        data = json.load(fh)
    out = {}
    known = data.get("sigs") is True
    for key, name, x, y, d, net, *rest in data.get("rows") or ():
        row = Row(key, name, x, y, d, net, rest[0] if rest else (None if known else UNKNOWN))
        out[at(name, x, y)] = row
    return out


def save(game, surface, record):
    p = path(game, surface)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    rows = sorted(record.values(), key=lambda r: (fkpmod.natural_key(r.key), r.x, r.y))
    head = json.dumps({"surface": surface,
                       "about": "the toolkit's own entities: what the applies pasted (fkcore.own)",
                       "sigs": True})
    body = ",\n".join(json.dumps([r.key, r.name, r.x, r.y, r.dir, r.net]
                                 + ([r.sig] if r.sig is not None else []))
                      for r in rows)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head[:-1] + ', "rows": [\n' + body + ("\n" if body else "") + "]}\n")


def _net(e):
    net = e.attrs.get("net")
    if not net:
        return None
    return f"{e.plot}/{net}" if e.plot else net


def settings(e, wires=()):
    """The signature of entity ``e``'s circuit settings: its typed circuit attrs, a chest's slot
    limit (``bar``) and request (``request_filters``) and the circuit wires ``wires`` (``w``
    records) that touch it, by colour and the other end's key. None when it has none of them."""
    from .codec import CIRCUIT_ATTRS, RAW_ATTRS
    got = [[k, e.attrs[k]] for k in CIRCUIT_ATTRS + RAW_ATTRS if e.attrs.get(k)]
    try:
        config = json.loads(e.attrs.get("config") or "{}")
    except (ValueError, AttributeError):
        config = {}
    if not isinstance(config, dict):
        config = {}
    if config.get("bar") is not None:
        got.append(["bar", config["bar"]])
    req = config.get("request_filters")
    if req is not None:
        got.append(["request_filters", json.dumps(req, sort_keys=True, separators=(",", ":"),
                                                  ensure_ascii=True)])
    ends = []
    for w in wires:
        a, b = fkpmod.split_end(w.a)[0], fkpmod.split_end(w.b)[0]
        if e.key in (a, b):
            ends.append([w.colour, w.a if e.key == b else w.b])
    if ends:
        got.append(["wires", sorted(ends)])
    if not got:
        return None
    return hashlib.sha1(json.dumps(got, separators=(",", ":")).encode()).hexdigest()[:10]


def rows_of(s):
    """{at: Row} of every entity of a composed surface (``compose.Surface``)."""
    out = {}
    touch = {}
    for w in getattr(s, "wires", ()):
        for end in (w.a, w.b):
            touch.setdefault(fkpmod.split_end(end)[0], []).append(w)
    for key in sorted(s.entities, key=fkpmod.natural_key):
        e = s.entities[key]
        out[at(e.name, e.x, e.y)] = Row(key, e.name, e.x, e.y, e.dir, _net(e),
                                        settings(e, touch.get(key, ())))
    return out


def resettled(record, s, verdicts):
    """The verdicts (ok, settings-changed or ghost: it stands or is pending) of entities the record shows
    pasted with other circuit settings than the design ``s`` has now: the next apply lays them
    fresh. A row the record has no signature for (``UNKNOWN``) is left alone."""
    if not record:
        return []
    design = rows_of(s)
    out = []
    for v in verdicts:
        if v.verdict not in ("ok", "settings-changed", "ghost"):
            continue
        k = at(v.name, v.x, v.y)
        row, now = record.get(k), design.get(k)
        if row is None or now is None or row.sig == UNKNOWN:
            continue
        if row.sig != now.sig:
            out.append(v)
    return out


def seed(game, surface, cat):
    """The record as the .fkp files on disk (the last written) compose: the surface .fkp's plots
    and shared infrastructure, each plot's module .fkp. {} when nothing is written yet."""
    sp = os.path.join(game, f"{surface}.fkp")
    if not os.path.exists(sp):
        return {}
    slay = fkpmod.load(sp)
    factories = {}
    for name, p in slay.plots.items():
        fp = os.path.join(game, f"{p.params.get('module', name)}.fkp")
        if os.path.exists(fp):
            lay = fkpmod.load(fp)
            lay.name = name
            factories[name] = lay
    return rows_of(compose.compose(slay, factories, cat))


def dropped(record, s):
    """The record's rows that the composed design ``s`` no longer holds (by name and position)."""
    design = {at(e.name, e.x, e.y) for e in s.entities.values()}
    return [row for k, row in sorted((record or {}).items(), key=lambda kv: kv[1].key)
            if k not in design]


def in_scope(row, cat, area=None, keys=None):
    """True when ``row`` is inside an apply's scope: a tile in ``area`` and a key prefix in
    ``keys`` (None = everywhere)."""
    if keys and not any(row.key.startswith(k) for k in keys):
        return False
    if area is None:
        return True
    try:
        tiles = cat.tiles(row.name, float(row.x), float(row.y), row.dir or 0)
    except (KeyError, ValueError):
        tiles = [(int(float(row.x) // 1), int(float(row.y) // 1))]
    return any(area[0] <= t[0] < area[2] and area[1] <= t[1] < area[3] for t in tiles)


def update(record, s, cat, area=None, keys=None, standing=()):
    """The record after an apply of ``s`` in this scope: outside it, as it was; inside it, the
    design that was pasted, plus the rows in ``standing`` (the toolkit's dropped entities that
    still stood, now ordered out: an index ``at``)."""
    record = record or {}
    out = {k: row for k, row in record.items() if not in_scope(row, cat, area, keys)}
    for k, row in rows_of(s).items():
        if in_scope(row, cat, area, keys):
            out[k] = row
    for k in standing:
        if k in record and k not in out:
            out[k] = record[k]
    return out


def nets(record):
    """{at: surface net} of the record's carriers."""
    return {k: row.net for k, row in (record or {}).items() if row.net}
