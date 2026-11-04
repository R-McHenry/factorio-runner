"""fkcore.domains: power domains and the copper lines between them (2.1, PLAN-2.1 "Power domains
and switching", Rory 2026-10-31).

A file with one ``power`` line covers everything as one network, as before, and nothing here
runs. With two or more (or any ``copper`` line), each top-level ``power NAME: cover ...`` line is
a **domain**, its own electric network:

- ``cover all`` takes every electric entity no other domain names; ``cover a, b`` takes the
  entities of those instances (a module instance's whole subtree). A routed inserter belongs to
  the domain of the machine it drops into, else of the one it picks from.
- Each domain is covered on its own (``fkcore.cover.cover_power`` with ``only`` and ``deny``): its
  poles are wired only to each other, and no new pole's supply area reaches another domain's
  machines (``domain-overlap`` where only such a spot would do). The ``cover all`` domain's poles
  are the cover ``power`` (``power/p<k>``) and carry no ``domain`` attr, so they join the world's
  grid as every plot's do; another domain's are ``power-NAME/p<k>`` with ``domain=NAME``, and
  ``codec.pole_wires`` wires each domain's poles apart.
- ``copper NAME: A -> B`` joins two domains, or a domain and a power switch's side
  (``sw.left``, ``sw.right``, connectors 5 and 6): the nearest pole of the domain within reach of
  the other end, else bridge poles of that domain to it. It is a ``w`` record of colour copper.
  Two domains joined with no switch are one network, and the build says so.
- Checks: ``no-source`` (a domain with no generator and no copper line, through switches, to a
  domain that has one; the ``cover all`` domain joins the world's grid and is never one) and
  ``power-short`` (with every switch open, a group of domains that has generators of known output
  wants more at full load than they make).

Stdlib only, deterministic.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import cover as covermod
from . import fkp
from .elaborate import Check
from .fkp import natural_key

__all__ = ["Domain", "active", "assign", "cover_domains", "GENERATORS", "SOLAR_W"]

#: entity types that make power: a domain with one of them has a source of its own
GENERATORS = frozenset({"generator", "solar-panel", "electric-energy-interface",
                        "burner-generator", "fusion-generator"})
#: a solar panel's output averaged over a nauvis day (60 kW at noon, nothing at night; 2.0)
SOLAR_W = 42000.0
#: how far a power switch's copper wire reaches (its wire_max_distance, 2.0)
SWITCH_REACH = 10.0


@dataclass
class Domain:
    name: str
    all: bool                         # ``cover all``: what no other domain names
    targets: list | None              # [lang.Ref] or None
    line: int
    keys: set = field(default_factory=set)        # its entities (electric ones and its poles)
    pole: str | None = None           # its pole kind (``pole=`` on its line), else the step's

    @property
    def owner(self):
        return "power" if self.all else f"power-{self.name}"

    @property
    def attrs(self):
        return {} if self.all else {"domain": self.name}


def active(flat):
    """The top level's power lines as domains, or None when the file has fewer than two and no
    copper line (one network, as before 2.1)."""
    if flat is None:
        return None
    lines = [c for c, scope in flat.covers if c.kind == "power" and not scope]
    if len(lines) < 2 and not getattr(flat, "coppers", None):
        return None
    out = []
    for c in lines:
        out.append(Domain(c.name, c.targets is None, c.targets, c.line,
                          pole=c.params.get("pole") if c.params else None))
    return out


def _kind(cat, name):
    try:
        return cat.kind(name)
    except KeyError:
        return None


def _rect(cat, e):
    return covermod._rect(fkp.tiles_of(cat, e))


def assign(flat, lay, cat, doms):
    """Each entity of the layout to its domain: fills ``Domain.keys`` and returns the check lines
    (a domain named twice, an instance in two domains, a target that names nothing)."""
    checks = []
    by_name = {}
    for d in doms:
        if d.name in by_name:
            checks.append(Check("domain-overlap", f"power {d.name}", "two power lines of one name"))
        by_name[d.name] = d
    alls = [d for d in doms if d.all]
    for d in alls[1:]:
        checks.append(Check("domain-overlap", f"power {d.name}", f"cover all twice ({alls[0].name} "
                            f"and {d.name}): {alls[0].name} takes what no other domain names"))
    default = alls[0] if alls else None
    paths, exact = {}, {}
    for d in doms:
        for ref in d.targets or ():
            node, keys = flat.resolve_ref(ref, "")
            if not keys and node is None:
                checks.append(Check("domain-overlap", f"power {d.name}", f"{ref} names no instance"))
                continue
            if node is not None:
                if node.path in paths and paths[node.path] is not d:
                    checks.append(Check("domain-overlap", f"power {d.name}",
                                        f"{ref} is in power {paths[node.path].name} too"))
                paths.setdefault(node.path, d)
            for k in keys:
                exact.setdefault(k, d)

    def by_owner(key):
        if key in exact:
            return exact[key]
        parts = key.split("/")
        for i in range(len(parts) - 1, 0, -1):
            got = paths.get("/".join(parts[:i]))
            if got is not None:
                return got
        return None

    from . import connect
    graph = None
    for key in sorted(lay.entities, key=natural_key):
        e = lay.entities[key]
        d = by_owner(key)
        if d is None and e.owner.startswith("~"):
            if graph is None:
                graph = connect.graph(lay, cat)
            for via in ("drop", "pickup"):
                if via == "drop":
                    near = [k for k, v in graph.succ.get(key, ()) if v == "drop"]
                else:
                    near = [k for k, v in graph.pred.get(key, ()) if v == "pickup"]
                for k in near:
                    d = by_owner(k)
                    if d is not None:
                        break
                if d is not None:
                    break
        if d is None and e.attrs.get("domain"):
            d = by_name.get(e.attrs["domain"])
        d = d or default
        if d is not None:
            d.keys.add(key)
    return checks


def _electric(cat, e):
    return covermod._needs_power(cat, e)


def _watts(w):
    return f"{w / 1e6:.2f} MW" if w >= 1e6 else f"{w / 1e3:.0f} kW"


def cover_domains(flat, lay, cat, doms, pole, keep_out=(), pinned_of=None, standing=None):
    """Cover each domain apart, then lay the copper lines. -> (new layout, report lines, checks).
    ``pinned_of(owner)``: the committed poles of a cover owner."""
    checks = assign(flat, lay, cat, doms)
    lines = []
    out = lay
    for d in doms:
        dpole = d.pole or pole
        others = [_rect(cat, out.entities[k]) for o in doms if o is not d for k in sorted(o.keys)
                  if k in out.entities and _electric(cat, out.entities[k])
                  and _kind(cat, out.entities[k].name) != "electric-pole"]
        pinned = pinned_of(d.owner) if pinned_of else ()
        keys = set(d.keys)
        poles, rep = covermod.cover_power(out, cat, pole=dpole, owner=d.owner, keep_out=keep_out,
                                          pinned=pinned, standing=standing, only=keys,
                                          deny=others, attrs=d.attrs)
        out = covermod.apply_cover(out, cat, pole=dpole, owner=d.owner, poles=poles)
        d.keys |= {p.key for p in poles}
        lines.append(f"domain {d.name}" + (" (cover all)" if d.all else "") + f": {rep.summary()}")
        checks += rep.checks
        # a pole of the domain that stands already (a Pole, a Literal's) reaching another's machines
        for k in sorted(d.keys, key=natural_key):
            e = out.entities.get(k)
            if e is None or _kind(cat, e.name) != "electric-pole" or e.owner == d.owner:
                continue
            s = cat.pole_supply(e.name)
            hit = [o for o in doms if o is not d for m in o.keys if m in out.entities
                   and _kind(cat, out.entities[m].name) != "electric-pole"
                   and _electric(cat, out.entities[m])
                   and covermod._overlap(_rect(cat, out.entities[m]), float(e.x), float(e.y), s)]
            if hit:
                checks.append(Check("domain-overlap", k, f"this pole of power {d.name} reaches "
                                    f"machines of power {hit[0].name}: they get both networks"))
    out, clines, cchecks, joins = _coppers(flat, out, cat, doms, pole, keep_out)
    lines += clines
    checks += cchecks
    checks += _sources(out, cat, doms, joins)
    return out, lines, checks


def _end(flat, lay, cat, ref, names):
    """A copper end -> ("domain", Domain) | ("switch", key, side) | (None, why)."""
    if len(ref.parts) == 1 and ref.parts[0] in names:
        return ("domain", names[ref.parts[0]])
    if len(ref.parts) >= 2 and ref.parts[-1] in ("left", "right"):
        from . import lang
        _node, keys = flat.resolve_ref(lang.Ref(ref.parts[:-1], False, ref.line), "")
        sw = [k for k in keys if k in lay.entities and lay.entities[k].name == "power-switch"]
        if len(sw) == 1:
            return ("switch", sw[0], ref.parts[-1])
        return (None, f"{'.'.join(ref.parts[:-1])} is no power switch")
    return (None, f"{ref} names no power domain ({', '.join(names)}) and no power switch's side "
                  "(sw.left, sw.right)")


def _poles_of(lay, cat, d):
    return [lay.entities[k] for k in sorted(d.keys, key=natural_key)
            if k in lay.entities and _kind(cat, lay.entities[k].name) == "electric-pole"]


def _next_key(lay, owner):
    used = [int(k.rsplit("/p", 1)[1]) for k in lay.entities
            if k.startswith(owner + "/p") and k.rsplit("/p", 1)[1].isdigit()]
    return max(used) + 1 if used else 0


def _ground(lay, cat, doms, d, pole, keep_out, also=()):
    taken = {}
    for e in lay.entities.values():
        try:
            for t in fkp.tiles_of(cat, e):
                taken.setdefault(t, e.key)
        except (KeyError, ValueError):
            continue
    deny = [_rect(cat, lay.entities[k]) for o in doms if o is not d and o not in also
            for k in sorted(o.keys) if k in lay.entities and _electric(cat, lay.entities[k])
            and _kind(cat, lay.entities[k].name) != "electric-pole"]
    return covermod._Ground(taken, {(math.floor(x), math.floor(y)) for x, y in keep_out},
                            lay.size, cat.size(pole), deny, cat.pole_supply(pole))


def _join(lay, cat, doms, d, target, pole, keep_out, also=()):
    """The pole of domain ``d`` a copper line from ``target`` (a _Node: a switch, or another
    domain's pole) ends on: the nearest within reach, else the last of new bridge poles of ``d``
    (added to ``lay``). -> (pole key, [new keys]) or (None, why)."""
    best = None
    for e in _poles_of(lay, cat, d):
        reach = min(cat.pole_reach(e.name), target.reach)
        dd = (float(e.x) - target.x) ** 2 + (float(e.y) - target.y) ** 2
        if dd <= reach * reach + 1e-9 and (best is None or dd < best[0]):
            best = (dd, e.key)
    if best is not None:
        return best[1], []
    srcs = [covermod._Node(float(e.x), float(e.y), cat.pole_supply(e.name), cat.pole_reach(e.name),
                           e.key) for e in _poles_of(lay, cat, d)]
    if not srcs:
        return None, f"power {d.name} has no pole to start from"
    ground = _ground(lay, cat, doms, d, pole, keep_out, also)
    path = covermod._search_links(ground, srcs, [target], cat.pole_supply(pole),
                                  cat.pole_reach(pole))
    if not path:
        return None, (f"no chain of free tiles joins power {d.name}'s poles to it within "
                      f"{fkp.fmt(cat.pole_reach(pole))} tiles a step (away from other domains' "
                      "machines)")
    k = _next_key(lay, d.owner)
    new = []
    for n in path:
        key = f"{d.owner}/p{k}"
        k += 1
        lay.add(fkp.Entity(key, pole, covermod._clean(n.x), covermod._clean(n.y), None,
                           dict(d.attrs)))
        d.keys.add(key)
        new.append(key)
    return new[-1], new


def _coppers(flat, lay, cat, doms, pole, keep_out):
    """Lay each ``copper`` line. -> (layout, lines, checks, joins [(a, b, switch key or None)])."""
    import copy
    lay = copy.deepcopy(lay)
    names = {d.name: d for d in doms}
    lines, checks, joins = [], [], []
    for c in getattr(flat, "coppers", ()):
        where = f"copper {c.name}"
        if c.name in lay.wirenets:
            checks.append(Check("copper", where, f"{c.name} is a wire's name too"))
            continue
        ends = [_end(flat, lay, cat, r, names) for r in (c.a, c.b)]
        bad = [e[1] for e in ends if e[0] is None]
        if bad:
            checks.append(Check("copper", where, bad[0]))
            continue
        kinds = [e[0] for e in ends]
        wire = None
        added = []
        if kinds == ["switch", "switch"]:
            a, b = (lay.entities[e[1]] for e in ends)
            if math.hypot(float(a.x) - float(b.x), float(a.y) - float(b.y)) > SWITCH_REACH + 1e-9:
                checks.append(Check("copper", where, f"{a.key} and {b.key} stand more than "
                                    f"{fkp.fmt(SWITCH_REACH)} tiles apart"))
                continue
            wire = (f"{a.key}.{ends[0][2]}", f"{b.key}.{ends[1][2]}")
            joins.append((None, None, (a.key, b.key)))
        elif "switch" in kinds:
            dom = next(e[1] for e in ends if e[0] == "domain")
            _k, sw, side = next(e for e in ends if e[0] == "switch")
            s = lay.entities[sw]
            target = covermod._Node(float(s.x), float(s.y), 0.0, SWITCH_REACH, sw)
            got, new = _join(lay, cat, doms, dom, target, dom.pole or pole, keep_out)
            if got is None:
                checks.append(Check("copper", where, f"{sw}.{side}: {new}"))
                continue
            added = new
            wire = (got, f"{sw}.{side}")
            joins.append((dom.name, side, sw))
        else:
            da, db = ends[0][1], ends[1][1]
            if da is db:
                checks.append(Check("copper", where, f"joins power {da.name} to itself"))
                continue
            got = None
            for eb in _poles_of(lay, cat, db):
                target = covermod._Node(float(eb.x), float(eb.y), cat.pole_supply(eb.name),
                                        cat.pole_reach(eb.name), eb.key)
                k, new = _join(lay, cat, doms, da, target, da.pole or pole, keep_out)
                if k is not None and not new:
                    d2 = (float(lay.entities[k].x) - target.x) ** 2 + \
                         (float(lay.entities[k].y) - target.y) ** 2
                    if got is None or d2 < got[0]:
                        got = (d2, k, eb.key)
            if got is None:
                pb = _poles_of(lay, cat, db)
                if not pb:
                    checks.append(Check("copper", where, f"power {db.name} has no pole"))
                    continue
                target = covermod._Node(float(pb[0].x), float(pb[0].y), cat.pole_supply(pb[0].name),
                                        cat.pole_reach(pb[0].name), pb[0].key)
                k, new = _join(lay, cat, doms, da, target, da.pole or pole, keep_out, also=(db,))
                if k is None:
                    checks.append(Check("copper", where, new))
                    continue
                got, added = (0, k, pb[0].key), new
            wire = (got[1], got[2])
            joins.append((da.name, db.name, None))
            lines.append(f"copper {c.name}: power {da.name} and power {db.name} are one network "
                         "now (a copper line with no power switch between them)")
        lay.wirenets[c.name] = fkp.WireNet(c.name, "copper")
        lay.wires.append(fkp.Wire(c.name, "copper", wire[0], wire[1]).canonical())
        if added:
            lines.append(f"copper {c.name}: {wire[0]} to {wire[1]}, {len(added)} bridge pole"
                         f"{'s' if len(added) != 1 else ''} added ({', '.join(added)})")
        elif "switch" in kinds or kinds == ["switch", "switch"]:
            lines.append(f"copper {c.name}: {wire[0]} to {wire[1]}")
    lay.wires = fkp.sort_wires(lay.wires)
    return lay, lines, checks, joins


def _supply(lay, cat, keys):
    """(W the domain's generators make, None when one's output is unknown; W its machines want
    at full load, its generators' count)."""
    made, want, gens, unknown = 0.0, 0.0, 0, False
    for k in keys:
        e = lay.entities.get(k)
        if e is None or e.attrs.get("dnp"):
            continue
        kind = _kind(cat, e.name)
        try:
            energy = cat.entity(e.name).get("energy") or {}
        except KeyError:
            continue
        if kind in GENERATORS:
            gens += 1
            if kind == "solar-panel":
                made += SOLAR_W
            elif energy.get("output_w"):
                made += float(energy["output_w"])
            else:
                unknown = True
        elif kind != "accumulator" and _electric(cat, e):
            want += float(energy.get("usage_w") or 0) + float(energy.get("drain_w") or 0)
    return (None if unknown else made), want, gens


def _sources(lay, cat, doms, joins):
    """``no-source`` and ``power-short``."""
    checks = []
    parent = {d.name: d.name for d in doms}

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    switch_sides = {}
    for a, b, sw in joins:
        if sw is None and a in parent and b in parent:          # no switch: one network
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[max(ra, rb)] = min(ra, rb)
        elif isinstance(sw, str):
            switch_sides.setdefault(sw, []).append(a)
    groups = {}
    for d in doms:
        groups.setdefault(find(d.name), []).append(d)
    info = {}
    for root, ds in groups.items():
        keys = set().union(*(d.keys for d in ds))
        made, want, gens = _supply(lay, cat, keys)
        info[root] = (made, want, gens, any(d.all for d in ds))
    # fed: a group with a generator or the world's grid, and any group a switch joins to one
    fed = {r for r, (_m, _w, g, a) in info.items() if g or a}
    links = []
    for _sw, sides in switch_sides.items():
        rs = sorted({find(x) for x in sides})
        links += [(rs[i], rs[j]) for i in range(len(rs)) for j in range(i + 1, len(rs))]
    grew = True
    while grew:
        grew = False
        for a, b in links:
            if (a in fed) != (b in fed):
                fed |= {a, b}
                grew = True
    for root in sorted(groups):
        ds = groups[root]
        names = " + ".join(d.name for d in ds)
        made, want, gens, _a = info[root]
        if root not in fed:
            checks.append(Check("no-source", f"power {names}", "no generator, and no copper line "
                                "(through a switch or not) to a domain that has one: its machines "
                                "never run"))
        if gens and made is not None and want > made + 1e-6:
            checks.append(Check("power-short", f"power {names}", f"its machines want "
                                f"{_watts(want)} at full load and its {gens} generator"
                                f"{'s' if gens != 1 else ''} make {_watts(made)}, with its power "
                                "switches open" + (" (solar panels at their day's average, 42 kW)"
                                                   if "solar" in _kinds(lay, cat, ds) else "")))
    return checks


def _kinds(lay, cat, ds):
    out = set()
    for d in ds:
        for k in d.keys:
            e = lay.entities.get(k)
            if e is not None and _kind(cat, e.name) == "solar-panel":
                out.add("solar")
    return out
