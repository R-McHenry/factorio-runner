"""fkcore.origins: ``fk migrate-origins`` (3.0.1), turned hints from corners to origins.

Before 3.0.1 ``@(x, y, rot r)`` put the turned frame's top-left corner on (x, y). Since 3.0.1 it
puts the instance's own origin there and turns about it (``expand.ORIGIN_HINTS``), so a turned
module that grows keeps its pieces where they were. A design written for the corner reading
places its turned instances elsewhere under the origin one; this rewrites each turned hint so
the design lays exactly what it laid before.

It reads the design the old way (corners), finds each turned instance's frame, and writes the
origin hint that lands it on the same tiles, on the instance's own source line, in its own file.
A hint inside a module body is written once for every instance of the module: when two
instances of it would want different hints (a parametric module whose frame changes with its
parameters), the first is written and a note says so. Pure but for reading and writing files.
"""
from __future__ import annotations

import os
import re

from . import expand, lang, snapshot

__all__ = ["migrate", "MigrateError"]

_AT = re.compile(r"@\(\s*(-?[0-9.]+)\s*,\s*(-?[0-9.]+)\s*(?:,\s*rot\s*(-?[0-9]+)\s*)?\)")


class MigrateError(Exception):
    pass


def _files(root):
    """{abs path: parsed File} of ``root`` and every file it imports."""
    out, todo = {}, [os.path.abspath(root)]
    while todo:
        path = todo.pop()
        if path in out:
            continue
        f = out[path] = lang.parse(snapshot.read(path), path)
        for _names, rel, _line in f.imports:
            todo.append(os.path.abspath(os.path.join(os.path.dirname(path), rel)))
    return out


def _num(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else repr(v)


def migrate(root, cat, layout=None):
    """-> ({file: new text} for the files that change, notes). ``root``: the factory's .fk;
    ``layout``: its .fkp (for Literals), or None."""
    from .elaborate import elaborate_file
    files = _files(root)
    where = {}                                       # module name -> its file
    for path, f in files.items():
        for name in f.modules:
            where.setdefault(name, path)
    for path, f in files.items():                    # a factory file imported whole: its body
        for names, rel, _line in f.imports:
            target = os.path.abspath(os.path.join(os.path.dirname(path), rel))
            for name in names:
                if name not in files[target].modules:
                    where.setdefault(name, target)
    was = expand.ORIGIN_HINTS
    expand.ORIGIN_HINTS = False
    try:
        flat = elaborate_file(root, cat, layout=layout)
    finally:
        expand.ORIGIN_HINTS = was
    want, notes, frames = {}, [], {}
    for path in sorted(flat.insts):
        inst = flat.insts[path]
        hint = (inst.hints or {}).get("at")
        lit = hint is not None and inst.kind == "Literal" and inst.literal is not None
        if not lit and (hint is None or not int(hint[2]) % 4 or expand.turns_in_place(inst)):
            continue
        if "/" in path:
            parent = flat.insts.get(path.rsplit("/", 1)[0])
            src = where.get(parent.module) if parent is not None else None
        else:
            src = os.path.abspath(root)
        if src is None:
            notes.append(f"{path}: written in no file this design loads (a generated module): "
                         "left as it is")
            continue
        if lit:
            # the old placement put the file's top-left tile on the corner hint
            frame = expand.literal_frame(inst)
            new = expand.origin_hint((hint[0] - frame[0], hint[1] - frame[1], hint[2]), frame)
        else:
            frame = expand._own_frame(flat, path, cat, frames)
            if frame is None:
                notes.append(f"{path}: its frame isn't known: left as it is")
                continue
            new = expand.origin_hint(hint, frame)
        key = (src, inst.line, path.rsplit("/", 1)[-1])
        if key in want and want[key][0] != new:
            notes.append(f"{path}: wants @({_num(new[0])}, {_num(new[1])}, rot {new[2]}) but "
                         f"{want[key][1]} (the same line) wants @({_num(want[key][0][0])}, "
                         f"{_num(want[key][0][1])}, rot {want[key][0][2]}): the first is written")
            continue
        want.setdefault(key, (new, path, hint))
    out = {}
    for (src, line, name), (new, path, hint) in sorted(want.items()):
        lines = out.get(src)
        if lines is None:
            lines = out[src] = snapshot.read(src).split("\n")
        text = lines[line - 1]
        hits = list(_AT.finditer(text))
        if len(hits) != 1:
            notes.append(f"{path}: line {line} of {os.path.basename(src)} has {len(hits)} "
                         "@(x, y) hints: left as it is")
            continue
        m = hits[0]
        rot = f", rot {m.group(3)}" if m.group(3) is not None else ""
        lines[line - 1] = (text[:m.start()] + f"@({_num(new[0])}, {_num(new[1])}{rot})"
                           + text[m.end():])
        if (new[0], new[1]) != (hint[0], hint[1]):
            notes.append(f"{path}: @({_num(hint[0])}, {_num(hint[1])}, rot {hint[2]}) -> "
                         f"@({_num(new[0])}, {_num(new[1])}, rot {hint[2]})")
    texts = {src: "\n".join(lines) for src, lines in out.items()}
    return {src: t for src, t in texts.items() if t != snapshot.read(src)}, notes
