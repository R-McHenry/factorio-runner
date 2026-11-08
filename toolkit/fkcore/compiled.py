"""The compile store (3.1, PLAN-3.1 31-store): sealed modules compile once, into one store.

A sealed module is a compile unit: its own nets (those whose innermost sealed module it is) are
routed alone in its frame, so what they lay depends only on what stands in the frame. The router
(``route._route_layout``) keys each unit by that, and a unit whose key the store holds takes the
stored routes, moved to its frame, with nothing searched: a twin later in the build, a module
moved, the next build, the world build after a lab build.

Layout, under the game folder's ``out``::

    out/compiled/<module>/<key>.json     one entry per key, never rewritten
    out/compiled/<module>/current        the key the last build wrote or laid from

``<module>`` is the module's name (``Gears``); ``<key>`` is ``route.unit_key``'s sha (20 hex).
An entry is JSON: ``router`` (the ROUTER it was laid by), ``module``, ``key``, ``unit`` (the
instance it was first laid from), ``inputs`` (what the key covers, for the reader: see below) and
``nets``: {net name relative to the unit: [[tag, name, x, y, dir, attrs, own net], ...]}, the
carriers in the unit's unturned frame (its top-left is 0,0; a unit turned 180 degrees stores
unturned, ``route._UnitTurn``), a lane pair's inserter with its ``a+b`` own net.

The key (one rule, ``route.unit_key``) covers everything the unit's nets are laid to:

- ``ROUTER``, the one router version: a change to what the router lays raises it, and every
  stored unit is laid again once;
- the module's source and parameters, through what they elaborate to: the frame's size, the
  instances placed in it (kind, parameters, place, turn), every entity standing in the frame
  (machines, Literals, the children's routes, a route the player holds), and each net's ends,
  items and lanes;
- its children's keys (the sealed units inside it, keyed first);
- the catalog and terrain facts it was laid against: the catalog's version and the footprint of
  every entity name in the frame, the terrain tiles in the frame, the router's settings (the
  hand, the ribbon tiers, the pipe material);
- the player's route-file inputs (3.0's per-net files, ``fkcore.design``): a net held (``fk
  hold``, ``status: held``) stands in the frame and is in the key as what stands; a seed (``fk
  seed``) is in the key as the net's seed. Edit one and the key changes, and the build reads it.

Old stores are not read: the 2.1.31 router store (``out/compiled/<key>.json``, flat) and the
3.0 design store (``out/design/modules/<module>/<key>.fkp``) stay as reference, and a build
that finds either says so in one line (``old_lines``). Nothing is migrated.

``Store`` is what the route step hands the router: the game folder's store (read and written,
``fk lab`` builds share their game's) and, with ``route --reuse-lab``, the lab builds' own stores
(read only). ``locked(name)`` refuses an entry holding an entity the force can't build yet
(``fkcore.avail``). Pure, stdlib only.
"""
from __future__ import annotations

import json
import os
import re

__all__ = ["ROUTER", "Store", "store_dir", "entry_path", "module_name", "old_material",
           "old_lines", "status_lines"]

#: the router generation a stored unit was laid by: a change to what the router lays for the
#: same inside raises it, and every stored unit is laid again once (3.1: one store, one version)
ROUTER = "3.1"


def store_dir(out):
    """The store under a game folder's ``out``."""
    return os.path.join(out, "compiled")


def entry_path(store, module, key):
    """``module``'s entry under ``key`` in the store folder ``store`` (``store_dir``)."""
    return os.path.join(store, module_name(module), f"{key}.json")


def module_name(module):
    """A module's name as its folder: anything a file name won't take becomes ``_``."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(module or "")) or "_"


class Store:
    """The compile store a route step reads and writes: the store folder ``dir`` (``out/compiled``,
    read and written) and ``more`` (other builds' store folders, read only). Names are listed
    once per module and folder, entries are read once (a megabase's ~700 units once opened a
    file each for every miss)."""

    def __init__(self, dir, more=(), locked=None):
        self.dir = os.path.abspath(dir) if dir else None
        self.more = [os.path.abspath(d) for d in more if d]
        self.locked = locked
        self.refused = {}            # (module, key) -> the names the force can't build
        self._names = {}             # (store folder, module) -> the file names in that folder
        self._data = {}              # an entry's path -> what it holds

    @property
    def dirs(self):
        return ([self.dir] if self.dir else []) + self.more

    @property
    def out(self):
        """The game folder's ``out`` the store lies under, or None."""
        return os.path.dirname(self.dir) if self.dir else None

    def _listed(self, store, module):
        got = self._names.get((store, module))
        if got is None:
            try:
                got = set(os.listdir(os.path.join(store, module_name(module))))
            except OSError:
                got = set()
            self._names[(store, module)] = got
        return got

    def find(self, module, key):
        """The path of ``module``'s entry under ``key`` in the first folder that has it, or
        None."""
        for store in self.dirs:
            if f"{key}.json" in self._listed(store, module):
                return entry_path(store, module, key)
        return None

    def load(self, module, key):
        """The stored ``nets`` of ``module`` under ``key`` (the unit's unturned frame terms), or
        None: not stored, laid by another ROUTER, or holding an entity the force can't build
        (``refused``)."""
        path = self.find(module, key)
        if path is None:
            return None
        data = self._data.get(path)
        if data is None:
            try:
                with open(path, encoding="utf-8") as fh:
                    data = self._data[path] = json.load(fh)
            except (OSError, ValueError):
                return None
        if not isinstance(data, dict) or data.get("router") != ROUTER:
            return None
        nets = data.get("nets") or {}
        if self.locked is not None:
            bad = sorted({e[1] for ents in nets.values() for e in ents if self.locked(e[1])})
            if bad:
                self.refused[(module, key)] = bad
                return None
        self._point(module, key)
        return nets

    def save(self, module, key, unit, nets, inputs=None):
        """Write ``module``'s entry under ``key`` (``nets`` in the unit's frame terms). -> 1 when
        written, 0 when the store has it, is read only or can't be written."""
        if self.dir is None or self.find(module, key) is not None:
            return 0
        path = entry_path(self.dir, module, key)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"router": ROUTER, "module": module, "key": key, "unit": unit,
                           "inputs": inputs or {}, "nets": nets}, fh)
            os.replace(tmp, path)
        except OSError:
            return 0
        self._listed(self.dir, module).add(f"{key}.json")
        self._point(module, key)
        return 1

    def _point(self, module, key):
        """``current``: the key the last build wrote or laid from."""
        if self.dir is None:
            return
        try:
            path = os.path.join(self.dir, module_name(module), "current")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(key + "\n")
        except OSError:
            pass


def current(out, module):
    """The key ``module``'s ``current`` points at, or None."""
    try:
        with open(os.path.join(store_dir(out), module_name(module), "current"),
                  encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def modules(out):
    """{module: [keys]} of the store under ``out``."""
    root = store_dir(out)
    got = {}
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return got
    for name in names:
        folder = os.path.join(root, name)
        if os.path.isdir(folder):
            got[name] = sorted(f[:-5] for f in os.listdir(folder) if f.endswith(".json"))
    return got


def old_material(out):
    """(entries of the 2.1.31 router store, entries of the 3.0 design store) under ``out``:
    neither is read."""
    flat = 0
    try:
        flat = sum(1 for f in os.listdir(store_dir(out)) if f.endswith(".json"))
    except OSError:
        pass
    old = 0
    root = os.path.join(out, "design", "modules")
    for _here, _dirs, files in os.walk(root):
        old += sum(1 for f in files if f.endswith(".fkp"))
    return flat, old


def old_lines(out):
    """One line when an old store lies under ``out``, else none."""
    flat, old = old_material(out)
    if not flat and not old:
        return []
    parts = []
    if flat:
        parts.append(f"{flat} of the 2.1 router store (out/compiled/*.json)")
    if old:
        parts.append(f"{old} of the 3.0 design store (out/design/modules)")
    return [f"compiled  old entries not read, reference only: {' and '.join(parts)}; the 3.1 "
            f"store is out/compiled/<module>/<key>.json (router {ROUTER})"]


def status_lines(out):
    """``fk status``: the store's modules, their entries and current keys."""
    mods = modules(out)
    if not mods:
        return [f"compiled: no store yet ({store_dir(out)})"]
    total = sum(len(k) for k in mods.values())
    lines = [f"compiled: {len(mods)} module{'s' if len(mods) != 1 else ''}, "
             f"{total} entr{'ies' if total != 1 else 'y'} (router {ROUTER}) in {store_dir(out)}"]
    for name, keys in mods.items():
        cur = current(out, name)
        lines.append(f"  {name}: {len(keys)} entr{'ies' if len(keys) != 1 else 'y'}"
                     + (f", current {cur}" if cur else ""))
    lines += [f"  {ln.split('  ', 1)[1]}" for ln in old_lines(out)]
    return lines
