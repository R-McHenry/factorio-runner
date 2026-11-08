"""fkcore.adopt: a foreign change taken into the source (CORE-V1.md §6.4, §8 ``adopt``).

``sense`` reports a foreign carrier next to a net as "looks like a fix to it". ``adopt`` writes such
carriers into the owning factory's .fkp as the net's own carriers (``~<net>/a<k>``, ``net=<net>``),
moved from surface coordinates into the factory frame through its plot, and returns the marks
that only covered them, so the caller can drop those from the surface .fkp. The next ``sense`` of
the area says ok instead of marked, and the next ``apply`` keeps the fix because it is now design.
V1 adopts carriers into an existing net of a factory on an unturned plot; new endpoints (a machine
someone added) still go into the .fk by hand.

3.1 (placements, PLAN-BUILD §5): a world edit inside a placement is a difference the diff lists;
``fk adopt PLACEMENT|AREA`` turns each into source as far as a small step goes (:func:`propose`):
a changed setting on a standing design entity (a chest's limit or request, an inserter's filter,
a control behaviour) becomes a proposed ``set`` line of the module source, printed as a diff
(``--write`` appends it after the instance's line); a changed recipe is the instance's parameter,
said as such; a moved or added carrier stays V1's carrier adoption (``--into FACTORY/NET``);
anything else is "needs a Literal patch", with the capture command that makes one. No general
source rewriter. Pure, stdlib only.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from . import fkp as fkpmod

__all__ = ["AdoptError", "adopt", "Proposal", "propose", "write_proposals"]

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


#: the blueprint fields a ``set`` line writes as a raw ``bp.`` setting (2.1)
_BP_FIELDS = ("bar", "request_filters", "filters", "use_filters", "filter_mode")


@dataclass
class Proposal:
    """One source change ``fk adopt`` proposes: the module file, the instance whose line it goes
    after, the ``set`` line (or None when it can't be a set line), and a note."""
    key: str                          # the design entity's composed key
    module: str | None                # the module file's name (game/<module>.fk), None: unknown
    inst: str                         # the instance the set line names
    line: str | None                  # the proposed set line
    note: str = ""

    def diff_lines(self, text=None):
        """The proposal as a diff of the module source: the instance's line, then the new one."""
        head = f"@@ {self.module}.fk ({_local(self.key)})" if self.module else f"@@ ({_local(self.key)})"
        out = [head]
        if text is not None and self.module:
            at = _inst_line(text, self.inst)
            if at is not None:
                out.append(" " + text.splitlines()[at])
        if self.line:
            out.append("+" + self.line)
        if self.note:
            out.append(f"  {self.note}")
        return out


def _local(key):
    return (key or "").split(":", 1)[-1]


def _inst_line(text, inst):
    """The index of the line that defines ``inst`` (``inst = ...``) in a .fk text, or None."""
    pat = re.compile(rf"^\s*{re.escape(inst)}\s*=")
    for i, line in enumerate(text.splitlines()):
        if pat.match(line):
            return i
    return None


def _selector(key, kind):
    """The ``set`` selector for a composed key: the top instance of its factory; an inserter's
    hands as ``NAME.out`` / ``NAME.in`` (by its local name: AsmRow's o0 is an output hand)."""
    local = _local(key)
    parts = local.split("/")
    owner = parts[1:-1] if len(parts) > 2 else parts[:-1]
    inst = ".".join(p for p in owner if not p.startswith("~")) or parts[0]
    if kind == "inserter":
        inst += ".out" if parts[-1].startswith("o") else ".in"
    return inst


def propose(drifts, cat, modules):
    """[Proposal] for ``drifts`` (``settings.Drift`` rows of a diff, ``fields`` set): a set line
    where a small one says it, a note where it can't. ``modules``: {factory: module name}."""
    from . import codec
    out = []
    for d in drifts:
        if d.wires or not d.fields:
            continue
        factory = _local(d.key).split("/", 1)[0]
        module = modules.get(factory)
        kind = cat.kind(d.name) if cat.has_entity(d.name) else None
        sel = _selector(d.key, kind)
        props = []
        notes = []
        for f, v in d.fields.items():
            if f == "recipe":
                notes.append(f"recipe {v}: a parameter of the instance, not a set line: give "
                             f"{sel.split('.')[0]} recipe={v} in {module or 'its module'}.fk")
            elif f == "control_behavior" and isinstance(v, dict):
                props += [f"cb.{k}={codec.config_text(val)}" for k, val in sorted(v.items())]
            elif f in _BP_FIELDS:
                props.append(f"bp.{f}={codec.config_text(v)}")
            else:
                notes.append(f"{f}: needs a Literal patch (fk capture --from AREA --into FILE, "
                             f"then Literal<file=\"FILE\"> @(x, y) in the module)")
        line = f"set {sel}: {', '.join(props)}" if props else None
        out.append(Proposal(d.key, module, sel.split(".")[0], line, "; ".join(notes)))
    return out


def write_proposals(game, proposals):
    """Append each proposal's set line to its module file, after the instance's line (or at the
    end). -> [(module, line)] written."""
    import os
    done = []
    by = {}
    for p in proposals:
        if p.line and p.module:
            by.setdefault(p.module, []).append(p)
    for module, ps in by.items():
        path = os.path.join(game, f"{module}.fk")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().split("\n")
        for p in ps:
            at = _inst_line("\n".join(lines), p.inst)
            new = f"{p.line}    # fk adopt"
            if at is None:
                lines.append(new)
            else:
                lines.insert(at + 1, new)
            done.append((module, p.line))
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write("\n".join(lines))
    return done
