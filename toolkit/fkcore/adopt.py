"""fkcore.adopt: a foreign change taken into the source (CORE-V1.md §6.4, §8 ``adopt``).

``sense`` reports a foreign carrier next to a net as "looks like a fix to it". ``adopt`` writes such
carriers into the owning factory's .fkp as the net's own carriers (``~<net>/a<k>``, ``net=<net>``),
moved from surface coordinates into the factory frame through its plot, and returns the marks
that only covered them, so the caller can drop those from the surface .fkp. The next ``sense`` of
the area says ok instead of marked, and the next ``apply`` keeps the fix because it is now design.
V1 adopts carriers into an existing net of a factory on an unturned plot; new endpoints (a machine
someone added) still go into the .fk by hand. Pure, stdlib only.
"""
from __future__ import annotations

import math

from . import fkp as fkpmod

__all__ = ["AdoptError", "adopt"]

_UG = {"input": "in", "output": "out"}


class AdoptError(ValueError):
    pass


def adopt(lay, plot, things, net, cat, marks=None):
    """Add ``things`` (scene Things on the surface) to factory layout ``lay`` as carriers of
    ``net``. -> (new keys, names of the marks that covered only them)."""
    if plot.rot:
        raise AdoptError(f"plot {plot.factory} is turned; V1 adopts into unturned plots only")
    used = [int(k.rsplit("/a", 1)[1]) for k in lay.entities
            if k.startswith(f"~{net}/a") and k.rsplit("/a", 1)[1].isdigit()]
    k = max(used) + 1 if used else 0
    keys, tiles = [], set()
    for t in things:
        if not cat.has_entity(t["name"]) or cat.kind(t["name"]) not in fkpmod.CARRIER_TYPES:
            raise AdoptError(f"{t['name']} at {t['x']},{t['y']} is not a carrier; put a machine "
                             "into the .fk by hand")
        attrs = {"net": net}
        if t.get("ug"):
            attrs["ug"] = _UG.get(t["ug"], t["ug"])
        e = fkpmod.Entity(f"~{net}/a{k}", t["name"], _clean(t["x"] - plot.x),
                          _clean(t["y"] - plot.y), int(t.get("direction") or 0), attrs)
        lay.add(e)
        keys.append(e.key)
        k += 1
        tiles |= {(int(a), int(b)) for a, b in t["tiles"]}
    covered = []
    for name, m in (marks or {}).items():
        mt = {(math.floor(x), math.floor(y)) for x, y in m.tiles}
        if mt and mt <= tiles:
            covered.append(name)
    return keys, covered


def _clean(v):
    v = float(v)
    return int(v) if v.is_integer() else v
