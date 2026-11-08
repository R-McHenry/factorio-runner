"""fkcore.learned: a run's own catalog overlay, the machines its player learned from the game (3.0).

The toolkit's catalog is the base game's (``fkcore/data``) or a pack's (``FK_PACK``,
:mod:`fkcore.pack`). A player who wants a machine the catalog lacks, or lacks something of (a
Space Age foundry's fluid boxes, a mod's electrolyser), reads it from the running game with
``fk catalog learn NAME`` (:mod:`fkcore.learn`), which writes ``learned.json`` in the run's
``game`` folder. Every fkcore command runs there (on the laptop and on the host alike), so the
overlay is found beside the design and layered over the catalog when it loads:

    {"version", "mods", "learned": {entity: "how it was learned"},
     "entities": [catalog row], "recipes": [catalog recipe row],
     "fluidboxes": {"entities": {name: row}, "recipes": {}},
     "facts": {"entities": {name: row}, "resources": {}, "fuels": {}, "fluids": {}, "recipes": {},
               "modules": {}}}

A row in the overlay replaces the base's row of the same name; the rest are added. ``FK_LEARNED``
names another file, and ``FK_LEARNED=`` (empty) turns the overlay off. :func:`Catalog.load` and
:func:`fkcore.facts.load` take it only on their default paths, so a test that loads a fixture by
path gets the fixture alone. Pure, stdlib only.
"""
from __future__ import annotations

import json
import os

__all__ = ["NAME", "path", "load", "merge_catalog", "merge_facts", "merge"]

#: the overlay's file name, in the folder fkcore runs in (the run's ``game``)
NAME = "learned.json"

_CACHE = {}
_PATHS = {}


def path():
    """The overlay's path, or None: ``FK_LEARNED`` if set (empty: none), else ``learned.json`` in
    the current folder if there is one. Looked up once per setting and folder (facts.load asks on
    every fact read)."""
    env = os.environ.get("FK_LEARNED")
    key = (env, os.getcwd())
    if key not in _PATHS:
        if env is not None:
            _PATHS[key] = os.path.abspath(env) if env else None
        else:
            here = os.path.abspath(NAME)
            _PATHS[key] = here if os.path.isfile(here) else None
    return _PATHS[key]


def load(p=None):
    """The overlay as a dict, or None (no overlay). Read once per path and modification time."""
    p = p or path()
    if not p or not os.path.isfile(p):
        return None
    key = (p, os.path.getmtime(p))
    if key not in _CACHE:
        with open(p, encoding="utf-8") as fh:
            _CACHE[key] = json.load(fh)
    return _CACHE[key]


def merge_catalog(data, ov):
    """``data`` (a catalog.json dict, with ``fluidboxes`` if any) with the overlay's entities,
    recipes and fluid boxes laid over it. -> a new dict; ``data`` is untouched."""
    if not ov:
        return data
    out = dict(data)
    for key in ("entities", "recipes"):
        rows = {r["name"]: r for r in data.get(key) or ()}
        for r in ov.get(key) or ():
            rows[r["name"]] = r
        out[key] = list(rows.values())
    fb = ov.get("fluidboxes") or {}
    if fb.get("entities") or fb.get("recipes"):
        base = dict(data.get("fluidboxes") or {})
        base["entities"] = dict(base.get("entities") or {}, **(fb.get("entities") or {}))
        recs = dict(base.get("recipes") or {})
        for machine, by in (fb.get("recipes") or {}).items():
            recs[machine] = dict(recs.get(machine) or {}, **by)
        base["recipes"] = recs
        out["fluidboxes"] = base
    return out


#: the facts sections an overlay adds to, by name
FACT_SECTIONS = ("entities", "resources", "fuels", "fluids", "recipes", "modules")


def merge_facts(facts, ov):
    """``facts`` (facts.json's dict) with the overlay's ``facts`` laid over it, section by
    section. -> a new dict."""
    extra = (ov or {}).get("facts") or {}
    if not extra:
        return facts
    out = dict(facts)
    for sec in FACT_SECTIONS:
        if extra.get(sec):
            out[sec] = dict(facts.get(sec) or {}, **extra[sec])
    return out


def merge(old, new):
    """Two overlays as one, ``new`` winning: what a second ``fk catalog learn`` writes."""
    if not old:
        return new
    out = merge_catalog(old, new)
    out["facts"] = merge_facts(old.get("facts") or {}, new)
    out["learned"] = dict(old.get("learned") or {}, **(new.get("learned") or {}))
    for k in ("version", "mods", "source"):
        if new.get(k) is not None:
            out[k] = new[k]
    return out
