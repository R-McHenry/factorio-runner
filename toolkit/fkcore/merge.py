"""fkcore.merge: merge, insert, chests-to-net and net-to-chests (CORE-V1.md §6.3), on paper.

All four edit only source (.fk), layout (.fkp) and the surface file, print what they changed, and
leave the build to the flow. Nothing in the world moves.

- ``merge(game, [a, b], into)``: one factory ``into`` in one codebase. Each factory's top-level
  body becomes a sub-module (``module Iron { ... }``) instanced at its old plot's offset inside the
  new plot (the bounding box of the old ones), so its fragments come out where they were. Its
  layout moves into ``into.fkp`` with every key and net renamed under the instance (``smelt/m0`` ->
  ``iron/smelt/m0``, net ``plates`` -> ``iron/plates``, ``~plates/b3`` -> ``~iron/plates/b3``) and its
  poles kept as existing poles (``power/iron-p3``), so the next build pins all of it. Plots must be
  unturned (rot 0) in V1.
- ``insert(game, component_dir, name, into, at)``: copy a component (a factory .fk, and its .fkp
  if it has one) from the components codebase into ``into`` the same way, at ``at``.
- ``chests_to_net(game, factory, provider, requester, net)``: replace a robot link with a net. The
  provider chest, the requester chest and the nets that only joined them to their machines are
  removed; one new net joins the provider net's drivers to the requester net's consumers. Only
  that net is unrouted afterwards.
- ``net_to_chests(game, factory, net, provider=..., requester=...)``: the opposite, a full
  decouple. A provider chest goes in at the drivers, a requester chest at the consumers, each with
  its own net, and the net's route is dropped.
- ``split_to_chest(game, factory, net, name, at, role=...)``: a belt split to a chest (Rory,
  2026-10-20), first class. The net keeps its belt and every endpoint; a chest joins it as one
  more endpoint: a provider as a consumer (it takes what the other consumers leave, in the rate
  model), a requester as a driver (it feeds the line). Only that net's route is dropped, so the
  next build lays the branch (a splitter, or an inserter off the passing belt) and pins the rest.

The .fk edits work on whole statement lines (V1 source is one statement per line). Pure apart
from reading and writing the game codebase's files.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from . import fkp as fkpmod
from . import lang
from . import surface as surfacemod

__all__ = ["MergeError", "Change", "merge", "insert", "chests_to_net", "net_to_chests",
           "split_to_chest"]


class MergeError(ValueError):
    pass


@dataclass
class Change:
    lines: list = field(default_factory=list)

    def add(self, text):
        self.lines.append(text)

    def __str__(self):
        return "\n".join(self.lines)


# -- .fk text helpers -----------------------------------------------------------------------------
def _split_fk(text):
    """(imports, module blocks, body lines) of a factory file, comments kept with the body."""
    imports, modules, body = [], [], []
    depth, block = 0, None
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if block is not None:
            block.append(line)
            depth += stripped.count("{") - stripped.count("}")
            if depth <= 0:
                modules.append("\n".join(block))
                block = None
            continue
        if re.match(r"^module\s+\w+", stripped):
            block = [line]
            depth = stripped.count("{") - stripped.count("}")
            if depth <= 0 and "{" in stripped:
                modules.append(line)
                block = None
            continue
        if stripped.startswith("import "):
            imports.append(line)
            continue
        body.append(line)
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return imports, modules, body


def _module_name(factory, taken=()):
    """``iron`` -> ``IronFactory``; ``IronFactory2`` and so on when the name is taken (a factory
    merged again under its own name already holds its first ``CoalFactory``: the stone designer's
    second merge of 2026-10-21 wrapped that module in a new one of the same name)."""
    base = "".join(p.capitalize() for p in re.split(r"[_\-]", factory)) + "Factory"
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}{n}"
    return name


def _module_names(blocks):
    return {m.group(1) for b in blocks if (m := re.match(r"^\s*module\s+(\w+)", b))}


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


def _rename(e, prefix, nets, dx, dy, owner_cover="power"):
    """An entity of a merged factory, renamed under ``prefix`` and moved by (dx, dy)."""
    owner, local = e.key.rsplit("/", 1)
    attrs = dict(e.attrs)
    if attrs.get("net"):                    # each net of a belt of two (a|b: prefix/a|prefix/b)
        attrs["net"] = fkpmod.map_nets(attrs["net"], lambda n: f"{prefix}/{n}")
    if owner.startswith("~"):
        key = f"~{fkpmod.map_nets(owner[1:], lambda n: f'{prefix}/{n}')}/{local}"
    elif owner in nets.get("covers", ()):
        key = f"{owner}/{prefix}-{local}"
    else:
        key = f"{prefix}/{e.key}"
    return fkpmod.Entity(key, e.name, _clean(float(e.x) + dx), _clean(float(e.y) + dy), e.dir,
                         attrs)


def _clean(v):
    return int(v) if float(v).is_integer() else v


def _fold(game, sources, into, plot, change, existing=None):
    """Write ``into``.fk/.fkp from ``sources`` [(name, fk text, fkp Layout or None, dx, dy)]."""
    imports, modules, insts, bodies = [], [], [], []
    lay = existing or fkpmod.Layout("factory", into, (plot.w, plot.h))
    lay.size = (plot.w, plot.h)
    for name, text, sub, dx, dy in sources:
        imp, mods, body = _split_fk(text)
        imports += [i for i in imp if i not in imports]
        modules += mods
        mod = _module_name(name, _module_names(modules + bodies))
        bodies.append(f"module {mod} {{\n" + "\n".join(("    " + l) if l.strip() else ""
                                                     for l in body) + "\n}")
        insts.append(f"{name} = {mod} @({dx}, {dy})")
        if sub is None:
            change.add(f"{name}: no layout yet; its rows are placed at the offset on the next build")
            continue
        covers = set(sub.covers)
        for path, rec in sub.insts.items():
            lay.insts[f"{name}/{path}"] = fkpmod.Inst(f"{name}/{path}", rec.kind, dict(rec.params),
                                                     _clean(rec.x + dx), _clean(rec.y + dy), rec.rot,
                                                     rec.sig)
        for c in sub.covers.values():
            lay.covers.setdefault(c.name, fkpmod.Cover(c.name, c.kind, dict(c.params)))
        for e in sub.entities.values():
            lay.add(_rename(e, name, {"covers": covers}, dx, dy))
        change.add(f"{name}: {len(sub.insts)} instances and {len(sub.entities)} entities moved "
                   f"into {into}.fkp under {name}/ (offset {dx},{dy}), keys renamed, nothing moved "
                   "in the world")
    head = [f"# {into}.fk: merged by fkcore.merge from " + ", ".join(f"{s[0]}.fk" for s in sources)
            + ". Each is a sub-module at its",
            "# old plot's offset, so its rows come out where they were; nothing is rewired."]
    text = "\n".join(head + imports + [""] + modules + bodies + [""] + insts) + "\n"
    return text, lay


def merge(game, names, into) -> Change:
    sfile = _surface_file(game)
    sf = surfacemod.load(sfile)
    change = Change()
    for n in names:
        if n not in sf.places:
            raise MergeError(f"{n} is not placed on {sf.name}")
        if sf.places[n].rot:
            raise MergeError(f"{n}'s plot is turned (rot {sf.places[n].rot}); V1 merges rot 0 only")
    if into in sf.places and into not in names:
        raise MergeError(f"{into} is already placed; insert into it instead")
    ps = [sf.places[n] for n in names]
    x0, y0 = min(p.x for p in ps), min(p.y for p in ps)
    x1, y1 = max(p.x + p.w for p in ps), max(p.y + p.h for p in ps)
    plot = surfacemod.Place(into, into, x0, y0, x1 - x0, y1 - y0)
    for other, p in sf.places.items():
        if other in names or p.lifecycle == "retired":
            continue
        if p.x < x1 and x0 < p.x + p.w and p.y < y1 and y0 < p.y + p.h:
            raise MergeError(f"the merged plot {x0},{y0} {x1 - x0}x{y1 - y0} would overlap {other}")
    sources = []
    for n, p in zip(names, ps):
        fk = os.path.join(game, f"{p.module}.fk")
        fkp_path = os.path.join(game, f"{p.module}.fkp")
        sub = fkpmod.load(fkp_path) if os.path.exists(fkp_path) else None
        sources.append((n, _read(fk), sub, p.x - x0, p.y - y0))
    text, lay = _fold(game, sources, into, plot, change)
    _write(os.path.join(game, f"{into}.fk"), text)
    _write(os.path.join(game, f"{into}.fkp"), fkpmod.dumps(lay))
    # a module another plot still places stays (2026-10-21: merging relay into south took
    # relays.fk away from relay2 and relay3, and the next build stopped with INVARIANT)
    still = {p.module for other, p in sf.places.items() if other not in names}
    for n, p in zip(names, ps):
        for ext in (".fk", ".fkp"):
            path = os.path.join(game, p.module + ext)
            if os.path.exists(path) and p.module != into and p.module not in still:
                os.remove(path)
    kept = sorted({p.module for p in ps if p.module in still})
    if kept:
        change.add("kept " + ", ".join(f"{m}.fk" for m in kept) + ": other plots still place it")
    _rewrite_surface(sfile, names, plot)
    change.add(f"surface: {', '.join(names)} -> {into} plot {x0},{y0} {x1 - x0}x{y1 - y0}; "
               f"robot covers renamed; the old .fk/.fkp files are folded into {into}")
    return change


def insert(game, component_dir, name, into, at) -> Change:
    """Copy component ``name`` (``<component_dir>/<name>.fk`` and its .fkp) into ``into`` at ``at``."""
    change = Change()
    fk = os.path.join(game, f"{into}.fk")
    if not os.path.exists(fk):
        raise MergeError(f"no factory {into} in {game}")
    target = _read(fk)
    if re.search(rf"^\s*{re.escape(name)}\s*=", target, re.M):
        raise MergeError(f"{into} already has an instance called {name}")
    comp = _read(os.path.join(component_dir, f"{name}.fk"))
    comp_fkp = os.path.join(component_dir, f"{name}.fkp")
    sub = fkpmod.load(comp_fkp) if os.path.exists(comp_fkp) else None
    lay_path = os.path.join(game, f"{into}.fkp")
    lay = fkpmod.load(lay_path) if os.path.exists(lay_path) else None
    imp, mods, body = _split_fk(comp)
    imports_t, modules_t, body_t = _split_fk(target)
    mod = _module_name(name, _module_names(modules_t + mods))
    block = (f"module {mod} {{\n" + "\n".join(("    " + l) if l.strip() else "" for l in body)
             + "\n}")
    text = "\n".join(imports_t + [i for i in imp if i not in imports_t] + [""] + modules_t + mods
                     + [block, ""] + body_t + [f"{name} = {mod} @({at[0]}, {at[1]})"]) + "\n"
    _write(fk, text)
    change.add(f"{into}.fk: component {name} inserted as module {mod} at {at[0]},{at[1]}")
    if lay is not None and sub is not None:
        covers = set(sub.covers)
        for path, rec in sub.insts.items():
            lay.insts[f"{name}/{path}"] = fkpmod.Inst(f"{name}/{path}", rec.kind, dict(rec.params),
                                                     _clean(rec.x + at[0]),
                                                     _clean(rec.y + at[1]), rec.rot, rec.sig)
        for e in sub.entities.values():
            lay.add(_rename(e, name, {"covers": covers}, at[0], at[1]))
        _write(lay_path, fkpmod.dumps(lay))
        change.add(f"{into}.fkp: its {len(sub.entities)} entities copied under {name}/")
    return change


def _surface_file(game):
    found = [f for f in sorted(os.listdir(game)) if f.endswith(".surface")]
    if len(found) != 1:
        raise MergeError(f"{game}: expected one .surface file, found {len(found)}")
    return os.path.join(game, found[0])


def _rewrite_surface(path, names, plot):
    out, placed = [], False
    for line in _read(path).splitlines():
        m = re.match(r"^\s*place\s+(\S+)\s*=", line)
        if m and m.group(1) in names:
            if not placed:
                out.append(f"place {plot.factory} = {plot.module} plot {plot.x},{plot.y} "
                           f"{plot.w}x{plot.h}    # merged from {', '.join(names)}")
                placed = True
            continue
        r = re.match(r"^(\s*robots\s+\S+\s+cover\s+)(.*)$", line)
        if r:
            items = [w.strip() for w in r.group(2).split("#", 1)[0].split(",") if w.strip()]
            items = list(dict.fromkeys(plot.factory if w in names else w for w in items))
            line = r.group(1) + ", ".join(items)
        out.append(line)
    _write(path, "\n".join(out) + "\n")


# -- rewiring -------------------------------------------------------------------------------------
def _parse(game, factory):
    path = os.path.join(game, f"{factory}.fk")
    text = _read(path)
    return path, text, lang.parse(text, path)


def _find_inst(f, dotted):
    """(module AST, Inst AST, module instance name or "") for ``iron.out`` or ``out``."""
    parts = dotted.split(".")
    if len(parts) == 1:
        for i in f.body.insts:
            if i.name == parts[0]:
                return f.body, i, ""
        raise MergeError(f"no instance {dotted}")
    holder = next((i for i in f.body.insts if i.name == parts[0]), None)
    if holder is None or holder.kind not in f.modules:
        raise MergeError(f"{parts[0]} is not a sub-module instance")
    mod = f.modules[holder.kind]
    inst = next((i for i in mod.insts if i.name == parts[1]), None)
    if inst is None:
        raise MergeError(f"module {holder.kind} has no instance {parts[1]}")
    return mod, inst, parts[0]


def _nets_touching(mod, name):
    return [n for n in mod.nets if any(r.parts[0] == name for r in n.drivers + n.consumers)]


def _unpaired(mod, *nets):
    """A robot link replaces a net, and a net that shares a belt (``lanes A | B``, 2.0) would
    leave its partner's lanes line naming nothing: refused, with what to do."""
    for ln in getattr(mod, "lanes", ()):
        for net in nets:
            if net in (ln.left, ln.right):
                raise MergeError(f"net {net} shares a belt (lanes {ln.left} | {ln.right}, line "
                                 f"{ln.line}): take that line out first")


def chests_to_net(game, factory, provider, requester, net="link") -> Change:
    path, text, f = _parse(game, factory)
    lines = text.split("\n")
    change = Change()
    pmod, pinst, pholder = _find_inst(f, provider)
    rmod, rinst, rholder = _find_inst(f, requester)
    for inst, role in ((pinst, "provider"), (rinst, "requester")):
        if inst.kind != "Chest" or not inst.params.get(role, role == "provider"):
            raise MergeError(f"{inst.name} is not a {role} Chest")
    pnets, rnets = _nets_touching(pmod, pinst.name), _nets_touching(rmod, rinst.name)
    if len(pnets) != 1 or len(rnets) != 1:
        raise MergeError("each chest must sit on exactly one net")
    pnet, rnet = pnets[0], rnets[0]
    _unpaired(pmod, pnet.name)
    _unpaired(rmod, rnet.name)
    drivers = [r for r in pnet.drivers if r.parts[0] != pinst.name]
    consumers = [r for r in rnet.consumers if r.parts[0] != rinst.name]
    if [r for r in pnet.consumers if r.parts[0] != pinst.name] or \
            [r for r in rnet.drivers if r.parts[0] != rinst.name]:
        raise MergeError("a chest's net has other endpoints; split it first")

    def qualified(holder, refs):
        return ", ".join(".".join(([holder] if holder else []) + r.parts) + (".*" if r.star else "")
                         for r in refs)
    drop = {pinst.line, rinst.line, pnet.line, rnet.line}
    for n in sorted(drop, reverse=True):
        change.add(f"{factory}.fk line {n} removed: {lines[n - 1].strip()}")
        del lines[n - 1]
    new_line = f"net {net}: {qualified(pholder, drivers)} -> {qualified(rholder, consumers)}"
    lines = _append_top(lines, new_line + "    # chests-to-net: was a robot link")
    change.add(f"{factory}.fk: + {new_line}")
    _write(path, "\n".join(lines))

    lay_path = os.path.join(game, f"{factory}.fkp")
    if os.path.exists(lay_path):
        lay = fkpmod.load(lay_path)
        old = {_qual(pholder, pnet.name), _qual(rholder, rnet.name)}
        chests = {_qual(pholder, pinst.name), _qual(rholder, rinst.name)}
        gone = [k for k, e in lay.entities.items()
                if e.owner in chests or (e.owner.startswith("~") and e.owner[1:] in old)]
        for k in gone:
            del lay.entities[k]
        for c in chests:
            lay.insts.pop(c, None)
        relabeled = 0
        for e in lay.entities.values():
            if e.net in old:
                e.attrs["net"] = net
                relabeled += 1
        _write(lay_path, fkpmod.dumps(lay))
        change.add(f"{factory}.fkp: removed {len(gone)} entities (the two chests and the routes "
                   f"of {', '.join(sorted(old))}); {relabeled} row carriers now on {net}; "
                   f"{net} is unrouted until the next build")
    return change


def net_to_chests(game, factory, net, provider, requester) -> Change:
    """``provider`` / ``requester``: dicts {name, at: (x, y), net, rate?} for the new chests."""
    path, text, f = _parse(game, factory)
    lines = text.split("\n")
    change = Change()
    target = next((n for n in f.body.nets if n.name == net), None)
    if target is None:
        raise MergeError(f"no top-level net {net} in {factory}.fk")
    _unpaired(f.body, net)
    holders_d = {r.parts[0] for r in target.drivers if len(r.parts) > 1}
    holders_c = {r.parts[0] for r in target.consumers if len(r.parts) > 1}
    if len(holders_d) != 1 or len(holders_c) != 1:
        raise MergeError("V1 puts each chest into the one sub-module its endpoints are in")
    dh, ch = holders_d.pop(), holders_c.pop()
    dmod = next(i.kind for i in f.body.insts if i.name == dh)
    cmod = next(i.kind for i in f.body.insts if i.name == ch)
    del lines[target.line - 1]
    change.add(f"{factory}.fk line {target.line} removed: net {net}")
    items = target.items
    flat_items = None
    if items is None:
        flat_items = provider.get("item")
    req_item = requester.get("item") or flat_items
    if not req_item:
        raise MergeError("say which item the requester asks for (requester['item'])")

    def unq(refs):
        return ", ".join(".".join(r.parts[1:]) + (".*" if r.star else "") for r in refs)
    rate = f", rate={requester['rate']}" if requester.get("rate") else ""
    adds = [
        (dmod, f"{provider['name']} = Chest<provider> @({provider['at'][0]}, {provider['at'][1]})"),
        (dmod, f"net {provider['net']}: {unq(target.drivers)} -> {provider['name']}"),
        (cmod, f"{requester['name']} = Chest<requester, item={req_item}{rate}> "
               f"@({requester['at'][0]}, {requester['at'][1]})"),
        (cmod, f"net {requester['net']}: {requester['name']} -> {unq(target.consumers)}"),
    ]
    for mod, line in adds:
        lines = _append_module(lines, mod, line + "    # net-to-chests")
        change.add(f"{factory}.fk module {mod}: + {line}")
    _write(path, "\n".join(lines))
    lay_path = os.path.join(game, f"{factory}.fkp")
    if os.path.exists(lay_path):
        lay = fkpmod.load(lay_path)
        gone = [k for k, e in lay.entities.items() if e.owner == f"~{net}"]
        for k in gone:
            del lay.entities[k]
        dkeys = {f"{dh}/{r.parts[1]}" for r in target.drivers}
        moved = 0
        for e in lay.entities.values():
            if e.net == net:
                inst = e.owner
                e.attrs["net"] = (f"{dh}/{provider['net']}" if any(inst.startswith(k) for k in dkeys)
                                  else f"{ch}/{requester['net']}")
                moved += 1
        _write(lay_path, fkpmod.dumps(lay))
        change.add(f"{factory}.fkp: removed {len(gone)} carriers of {net}; {moved} row carriers "
                   "back on the chests' nets; both chest nets are unrouted until the next build")
    return change


def split_to_chest(game, factory, net, name, at, role="provider", item=None, rate=None) -> Change:
    """Add chest ``name`` at ``at`` (in the net's own scope) as one more endpoint of ``net``.

    ``net`` is the netlist's name: ``gear`` for a top-level net, ``iron/plates`` for a net inside
    sub-module instance ``iron``. A requester needs ``item`` (and takes an optional ``rate``,
    e.g. "0.5/s")."""
    if role not in ("provider", "requester"):
        raise MergeError("role is provider (one more consumer) or requester (one more driver)")
    path, text, f = _parse(game, factory)
    holder, _sep, short = net.rpartition("/")
    if holder:
        inst = next((i for i in f.body.insts if i.name == holder and i.kind in f.modules), None)
        if inst is None:
            raise MergeError(f"{holder} is not a sub-module instance of {factory}")
        scope, mod = f.modules[inst.kind], inst.kind
    else:
        scope, mod = f.body, None
    target = next((n for n in scope.nets if n.name == short), None)
    if target is None:
        raise MergeError(f"no net {net} in {factory}.fk")
    if any(i.name == name for i in scope.insts):
        raise MergeError(f"{name} is already an instance there")
    if role == "requester" and not item:
        raise MergeError("a requester chest needs the item it asks the network for")
    lines = text.split("\n")
    code, hash_, comment = lines[target.line - 1].partition("#")
    if "->" not in code:
        raise MergeError(f"line {target.line} is not a one-line net")
    if role == "provider":
        code = code.rstrip() + f", {name}"
    else:
        left, right = code.split("->", 1)
        code = left.rstrip() + f", {name} ->" + right.rstrip()
    lines[target.line - 1] = code + ("    " + hash_ + comment if hash_ else "")
    params = "provider" if role == "provider" else (
        f"requester, item={item}" + (f", rate={rate}" if rate else ""))
    chest = f"{name} = Chest<{params}> @({at[0]}, {at[1]})    # split-to-chest on {short}"
    lines = _append_module(lines, mod, chest) if mod else _append_top(lines, chest)
    _write(path, "\n".join(lines))
    change = Change()
    change.add(f"{factory}.fk: + {chest.split('#')[0].strip()}")
    change.add(f"{factory}.fk line {target.line}: {name} is now a "
               f"{'consumer' if role == 'provider' else 'driver'} of {net}")
    lay_path = os.path.join(game, f"{factory}.fkp")
    if os.path.exists(lay_path):
        lay = fkpmod.load(lay_path)
        gone = [k for k, e in lay.entities.items() if e.owner == f"~{net}"]
        for k in gone:
            del lay.entities[k]
        _write(lay_path, fkpmod.dumps(lay))
        change.add(f"{factory}.fkp: {len(gone)} route carriers of {net} dropped; the next build "
                   "lays the net with its branch to the chest, every other net pinned")
    return change


def _qual(holder, name):
    return f"{holder}/{name}" if holder else name


def _append_top(lines, line):
    while lines and not lines[-1].strip():
        lines.pop()
    return lines + [line, ""]


def _append_module(lines, mod, line):
    start = next(i for i, l in enumerate(lines) if re.match(rf"^\s*module\s+{re.escape(mod)}\b", l))
    depth = 0
    for i in range(start, len(lines)):
        depth += lines[i].split("#", 1)[0].count("{") - lines[i].split("#", 1)[0].count("}")
        if depth == 0 and i > start or (depth == 0 and "}" in lines[i]):
            return lines[:i] + ["    " + line] + lines[i:]
    raise MergeError(f"module {mod} is not closed")
