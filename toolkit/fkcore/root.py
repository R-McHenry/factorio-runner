"""fkcore.root: the world design (the root .fk) and the plots it places (PLAN-GLOBAL phase B).

The root file is a factory file like any other (``fkcore.lang``): it imports each plot's file as
a module and places it at **map coordinates**, with the plot's rectangle as the instance's frame:

    import Plant from "plant.fk"
    import Base from "base.fk"

    plant = Plant @(-34, -39) size 17x16
    base  = Base  @(8, 12)    size 70x56 gap 6
    boot  = Boot  @(-40, -10) size 20x26 temporary until main.running

Every **top-level sealed instance** is a plot. Its name is the plot's factory name (what a
surface ``place`` line used to name), the file it is imported from is the plot's ``.fk`` (whose
stem names both the ``.fk`` and the ``.fkp``), and ``@(x, y, rot)``, ``size``, ``gap``,
``@patch``, ``retired`` and ``temporary`` carry what the place line carried. So a surface's
``design nauvis.fk`` derives the plot list (``fkcore.surface``: ``design`` wins over ``place``
lines), and every per-factory step of the flow (``place``, ``route``, cover, apply) runs on it
unchanged.

**Keys stay as they were**: a plot's layout is its own ``.fkp`` with its keys relative to the
plot's instance (``smelt/m0``, ``~ore/b3``), so composing the surface gives the same keys as the
place lines gave (``nauvis:base/smelt/m0``) and the live world reads as unchanged.

The derivation reads the root and the files it imports (``fkcore.elaborate.load``), so it needs no
catalog: it is what a surface's plot list costs. A module declared *in* the root file, or an
instance of a template, is not a plot: it is part of the world's own level. A plot's file must be
its own, since its layout record is ``<file>.fkp``.

``migrate`` writes the root file from a surface's place lines (``fk migrate-surface``). Pure,
stdlib only.
"""
from __future__ import annotations

import os
import re

from . import elaborate as elab
from . import lang
from . import expand
from . import surface as surfacemod

__all__ = ["places", "migrate", "module_name"]


def places(path):
    """(the plot list {factory: Place}, notes) of the root design ``path``.

    A plot is a top-level instance of an imported file whose frame is known: the instance line's
    ``size WxH`` or the file's own ``size`` line (``size`` seals a module, 2.0.40). Its lifecycle,
    gap and patch ride on the line as ``retired``, ``temporary until ...``, ``gap N`` and
    ``@patch(resource)``. Notes say what could not be a plot (a module declared in the root
    itself, a second plot of one file, an instance with no ``@(x, y)`` or no frame): the plot list
    is shorter than the root's instances then."""
    try:
        f, modules = elab.load(path)
    except (lang.FkSyntaxError, elab.ElabError) as exc:
        raise surfacemod.SurfaceError(f"design {os.path.basename(path)}: {exc}") from None
    files = {}
    for names, rel, _line in f.imports:
        stem = os.path.splitext(os.path.basename(rel))[0]
        for n in names:
            files[n] = stem
    # 2.0.44: one file may hold several plots (14 relays of relay.fk), as place lines allowed:
    # each is its own factory, and the flow reads the file's .fk and .fkp for each
    out, notes = {}, []
    for inst in f.body.insts:
        if inst.kind in f.modules:
            if inst.size or f.modules[inst.kind].size:
                notes.append(f"{inst.name}: module {inst.kind} is declared in the root file, not "
                             "imported from a .fk: a plot needs its own file")
            continue
        if inst.kind not in modules:
            continue                       # a template: the root's own level, not a plot
        size = inst.size or modules[inst.kind].size
        stem = files.get(inst.kind)
        if stem is None:
            notes.append(f"{inst.name}: module {inst.kind} is declared in the root file, not "
                         "imported from a .fk: a plot needs its own file")
            continue
        if size is None:
            notes.append(f"{inst.name}: no frame: give its line `size WxH`, or `{stem}.fk` a size "
                         "line (a plot's rectangle is its module's size)")
            continue
        at = inst.hints.get("at")
        if at is None:
            notes.append(f"{inst.name}: no @(x, y) hint: a plot is placed at map coordinates")
            continue
        if expand.ORIGIN_HINTS:     # 3.0.1: a turned plot turns about its origin
            at = expand.corner_hint(at, (0, 0, size[0], size[1]))
        x, y, rot = at
        out[inst.name] = surfacemod.Place(factory=inst.name, module=stem, x=int(x), y=int(y),
                                          w=int(size[0]), h=int(size[1]), rot=int(rot) % 4,
                                          patch=inst.hints.get("patch"),
                                          lifecycle=inst.lifecycle or "placed",
                                          line=inst.line, gap=int(inst.gap or 0))
    return out, notes


def module_name(factory, stem, used):
    """The module name an import gives a plot: the file's stem in CamelCase (``iron-smelt`` ->
    ``IronSmelt``), the plot's name added if that name is taken (two files, one stem)."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", stem) if p]
    name = "".join(p[:1].upper() + p[1:] for p in parts) or "Plot"
    if not re.match(r"^[A-Za-z_]", name):
        name = "P" + name
    if name in used:
        name += "".join(p[:1].upper() + p[1:] for p in re.split(r"[^A-Za-z0-9]+", factory) if p)
    while name in used:
        name += "_"
    used.add(name)
    return name


def migrate(places, surface="nauvis"):
    """The root .fk's text for a surface's ``place`` lines (``fk migrate-surface``, 2.0.43): each
    plot becomes an import of its file and one sealed instance at its map coordinates, with the
    place line's rot, gap, patch and lifecycle."""
    imports, body, used, names = [], [], set(), {}
    for p in places.values():
        if p.module not in names:               # one import per file, however many plots
            names[p.module] = module_name(p.factory, p.module, used)
            imports.append(f'import {names[p.module]} from "{p.module}.fk"')
        modname = names[p.module]
        ox, oy, _r = expand.origin_hint((p.x, p.y, p.rot), (0, 0, p.w, p.h)) \
            if expand.ORIGIN_HINTS else (p.x, p.y, p.rot)
        at = f"@({ox}, {oy}" + (f", rot {p.rot}" if p.rot else "") + ")"
        line = f"{p.factory} = {modname} {at} size {p.w}x{p.h}"
        if p.gap:
            line += f" gap {p.gap}"
        if p.patch:
            line += f' @patch({_quoted(p.patch)})'
        if p.lifecycle == "retired":
            line += " retired"
        elif str(p.lifecycle).startswith("temporary"):
            line += " " + str(p.lifecycle)
        body.append(line)
    head = [f"# {surface}.fk: the world design (fk migrate-surface, 2.0.43). One sealed instance",
            "# per plot, at map coordinates: the file a plot is imported from is its .fk, its",
            "# size is the instance's frame, and gap/retired/temporary ride on its line."]
    return "\n".join(head + [""] + imports + [""] + body) + "\n"


def _quoted(patch):
    """A patch name as the parser reads it: quoted when it carries a ``#`` (a comment starts at
    one), plain otherwise (``iron-ore``, ``"iron-ore#2"``)."""
    return f'"{patch}"' if "#" in patch else patch
