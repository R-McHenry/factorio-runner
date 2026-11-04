"""fkcore.robots: robot coverage, the flow's ``cover robots`` step and its surface check (CORE-V1 §4.2).

A logistic chest (requester, provider, storage, buffer) works only inside a roboport's logistic
area, and robots build only inside a construction area of a network that holds the materials. The
V1 rehearsal found it (2026-10-20): a gear factory's requester stood half a tile outside the kit
roboport's square and got nothing.

:func:`cover_robots` puts roboports into a factory so every logistic chest of it is inside some
roboport's logistic square (``logistic_radius`` each way from its centre, catalog), a greedy set
cover over free 4x4 spots of the factory frame: the spot covering the most uncovered chests, ties
to the spot nearest the chests' centre, then north, then west. Roboports already in the layout
that are not the owner's own ``r<k>`` count as coverage and stay, and so do ``served`` squares:
the surface's main network in the factory's frame (:func:`served`, 1.2: the 0534 run's ``boot``
stood inside the kit roboport's square and got a roboport of its own anyway, from a stock of 4).
A factory without logistic chests gets none (building it is the network's job). With ``storage``
(the flow's default, ``storage-chest``) each roboport it places gets a storage chest beside it,
``<owner>/s<k>``, so what robots take up nearby is stored nearby (1.2: the run's rebuilds far
from spawn sent every taken-up item 110 tiles to the kit's chests). Run it before ``cover
power``, so the poles power the roboports too. :func:`apply_robots` returns the layout with them
and ``cover robots robots``. 1.7 (Rory, 2026-10-21: pin and patch): the committed roboports
(``pinned``) stay where they still fit and still reach a chest, so a rebuild adds roboports only
where none reaches and never moves the network that stands.

:func:`check_surface` judges a composed surface: roboports join one logistic network when their
logistic squares meet (centres at most twice the radius apart on both axes); the **main** network
is the one holding a roboport outside every plot (shared infra: the kit's). Each factory's
logistic chests must be inside a square of the main network, and each of its entities inside the
main network's construction area, else a ``no-robots`` check line (never a gate). Pure, stdlib only.
"""
from __future__ import annotations

import copy
import re
from collections import namedtuple

from . import fkp
from .compose import turn
from .cover import _Ground
from .elaborate import Check
from .fkp import Entity, natural_key

__all__ = ["LOGISTIC_CHESTS", "RobotReport", "cover_robots", "apply_robots", "networks",
           "served", "check_surface", "reach", "lua_roboports", "in_frame"]

LOGISTIC_CHESTS = frozenset({"requester-chest", "passive-provider-chest", "active-provider-chest",
                             "storage-chest", "buffer-chest"})
_OWN = re.compile(r"[rs]\d+")          # the cover's own: roboports r<k>, their storage chests s<k>
_Port = namedtuple("_Port", "key name x y plot")


class RobotReport:
    def __init__(self, owner, roboports, chests, covered, uncovered, served=0, storage=(),
                 no_room=0, pinned=0):
        self.owner, self.roboports, self.chests, self.covered, self.uncovered = (
            owner, roboports, chests, covered, uncovered)
        self.served, self.storage, self.no_room = served, list(storage), no_room
        self.pinned = pinned           # of ``roboports``, the committed ones kept where they stand
        self.checks = [Check("no-robots", key, why) for key, why in uncovered]

    def summary(self):
        text = f"cover {self.owner} robots: {len(self.roboports)} roboports"
        if self.pinned:
            text += f" ({self.pinned} kept where they stand)"
        if self.storage or self.no_room:
            text += f", {len(self.storage)} storage chests beside them"
            if self.no_room:
                text += f" ({self.no_room} with no free tile beside it)"
        text += f"; {self.covered}/{self.chests} logistic chests inside a logistic area"
        if self.served:
            text += f" ({self.served} of them in the surface network's)"
        return text


def _radius(cat, name, field):
    row = cat.entity(name)
    return float(row.get(field) or 0)


def _inside(x, y, rx, ry, r):
    return abs(x - rx) <= r and abs(y - ry) <= r


def _is_own(e, owner):
    return e.owner == owner and _OWN.fullmatch(e.key[len(owner) + 1:]) is not None


def cover_robots(lay, cat, owner="robots", keep_out=(), roboport="roboport", served=(),
                 storage=None, pinned=(), reach=None):
    """-> (new Entities: roboports keyed ``<owner>/r<k>`` and, with ``storage`` (a chest
    prototype), a storage chest beside each keyed ``<owner>/s<k>``; RobotReport). ``served``:
    [(x, y, radius)] logistic squares of the surface's network in this factory's frame.
    ``pinned``: the committed .fkp's own ``r<k>`` / ``s<k>`` (1.7, pin and patch): a roboport
    that still fits and still reaches a chest of the factory stays where it stands, and so does
    its storage chest where that still fits; new roboports go only where no kept one reaches.

    ``reach`` (2.1.3): [(x, y, logistic radius, construction radius)] of the roboports that stand
    and have power, in this factory's frame (the last diff's read), or None when none was read.
    With it, a roboport goes only where one that stands, or one this step lays in reach of them,
    builds it and takes it into its network (inside its construction area, logistic squares
    meeting); where no such spot covers a chest, it lays roboports one from the next toward the
    chests. The runs' roboports placed out of every construction area stood as ghosts for ever,
    and one built 52 tiles from the hub made a network with no robots."""
    r = _radius(cat, roboport, "logistic_radius")
    ents = sorted(lay.entities.values(), key=lambda e: natural_key(e.key))
    # 2.0.63: a DNP chest asks for no robots, and a DNP roboport serves none (never built)
    chests = [e for e in ents if e.name in LOGISTIC_CHESTS and not _is_own(e, owner)
              and not e.attrs.get("dnp")]
    existing = [e for e in ents if cat.kind(e.name) == "roboport" and not _is_own(e, owner)
                and not e.attrs.get("dnp")]
    outside = [c for c in chests
               if any(_inside(float(c.x), float(c.y), sx, sy, sr) for sx, sy, sr in served)]
    taken = {}
    for e in ents:
        if _is_own(e, owner):
            continue
        for t in fkp.tiles_of(cat, e):
            taken[t] = e.key
    w, h = cat.size(roboport)
    ground = _Ground(taken, set(map(tuple, keep_out)), lay.size, (w, h))
    avoid = _hands_and_fronts(cat, ents) if storage else set()
    old = {e.key[len(owner) + 1:]: e for e in pinned if _is_own(e, owner)}
    kept, kept_chests = [], {}
    for local in sorted((k for k in old if k.startswith("r")), key=natural_key):
        e = old[local]
        try:
            tiles = fkp.tiles_of(cat, e)
        except (KeyError, ValueError):
            continue
        spot = (min(x for x, _y in tiles), min(y for _x, y in tiles))
        if e.name != roboport or not ground.free(spot) or not any(
                _inside(float(c.x), float(c.y), float(e.x), float(e.y), r) for c in chests):
            continue
        ground.place(spot)
        kept.append((int(local[1:]), spot, float(e.x), float(e.y)))
    links = None if reach is None else _Links(reach, r)
    if links is not None:
        links.grow([(float(e.x), float(e.y)) for e in existing]
                   + [(x, y) for _k, _s, x, y in kept])
    for k, _spot, _x, _y in kept:
        c = old.get(f"s{k}")
        if c is None or not storage or c.name != storage:
            continue
        try:
            tile = min(fkp.tiles_of(cat, c))
        except (KeyError, ValueError):
            continue
        if _free_tile(ground, tile, avoid):
            ground.placed.add(tile)
            kept_chests[k] = tile
    left = [c for c in chests if c not in outside
            and not any(_inside(float(c.x), float(c.y), float(p.x), float(p.y), r) for p in existing)
            and not any(_inside(float(c.x), float(c.y), x, y, r) for _k, _s, x, y in kept)]
    placed = []
    if left:
        cx = sum(float(c.x) for c in left) / len(left)
        cy = sum(float(c.y) for c in left) / len(left)
    steps = 0
    while left:
        best = None
        x0 = int(min(float(c.x) for c in left) - r - w) - 1
        x1 = int(max(float(c.x) for c in left) + r) + 1
        y0 = int(min(float(c.y) for c in left) - r - h) - 1
        y1 = int(max(float(c.y) for c in left) + r) + 1
        for j in range(y0, y1 + 1):
            for i in range(x0, x1 + 1):
                if not ground.free((i, j)):
                    continue
                px, py = ground.centre((i, j))
                if links is not None and not links.joins(px, py):
                    continue
                n = sum(1 for c in left if _inside(float(c.x), float(c.y), px, py, r))
                if not n:
                    continue
                score = (-n, (px - cx) ** 2 + (py - cy) ** 2, j, i)
                if best is None or score < best[0]:
                    best = (score, (i, j))
        if best is None and links is not None and steps < _CHAIN:
            # no spot in reach covers a chest: one roboport more toward them, in reach
            best = links.step(ground, left)
            steps += 1
        if best is None:
            break
        spot = best[1]
        ground.place(spot)
        px, py = ground.centre(spot)
        placed.append((spot, px, py))
        if links is not None:
            links.grow([(px, py)])
        left = [c for c in left if not _inside(float(c.x), float(c.y), px, py, r)]
    placed.sort(key=lambda p: (p[2], p[1]))
    top = max([k for k, *_rest in kept] + [-1]) + 1      # new ones are numbered after the kept
    ports = [(k, spot, x, y) for k, spot, x, y in kept] + \
            [(top + k, spot, x, y) for k, (spot, x, y) in enumerate(placed)]
    news = [Entity(f"{owner}/r{k}", roboport, _clean(x), _clean(y), None, {})
            for k, _spot, x, y in ports]
    chests_out, no_room = [], 0
    if storage:
        for k, spot, _x, _y in ports:
            tile = kept_chests.get(k) or _beside(ground, spot, avoid)
            if tile is None:
                no_room += 1
                continue
            ground.placed.add(tile)
            chests_out.append(Entity(f"{owner}/s{k}", storage, tile[0] + 0.5, tile[1] + 0.5,
                                     None, {}))
    why = ("no free 4x4 spot inside the factory's frame reaches it" if links is None else
           "no free 4x4 spot inside the factory's frame reaches it from a roboport that stands "
           "(or one laid in reach of them): a roboport out of reach stays a ghost")
    uncovered = [(c.key, why) for c in left]
    rep = RobotReport(owner, [e.key for e in news], len(chests), len(chests) - len(left),
                      uncovered, served=len(outside), storage=[e.key for e in chests_out],
                      no_room=no_room, pinned=len(kept))
    return news + chests_out, rep


#: roboports ``cover robots`` lays one from the next toward chests out of reach, at most
_CHAIN = 12


class _Links:
    """The roboports that stand (with power) and those laid in reach of them, for ``cover robots``
    (2.1.3): a spot joins when it is inside one's construction area (robots build it there) and
    their logistic squares meet (it joins that network, whose robots then work from it)."""

    def __init__(self, standing, radius):
        self.r = radius                                  # the new roboports' logistic radius
        self.ports = [(float(x), float(y), float(lr), float(cr)) for x, y, lr, cr in standing]
        self.cr = max([cr for *_rest, cr in self.ports] + [0.0])

    def joins(self, px, py):
        return any(abs(px - x) <= min(cr, lr + self.r) and abs(py - y) <= min(cr, lr + self.r)
                   for x, y, lr, cr in self.ports)

    def grow(self, points):
        """Add the roboports at ``points`` that join, each in turn (one joins through another)."""
        todo, cr = list(points), self.cr or self.r
        while True:
            hit = [p for p in todo if self.joins(*p)]
            if not hit:
                return
            for p in hit:
                todo.remove(p)
                self.ports.append((p[0], p[1], self.r, cr))

    def step(self, ground, chests):
        """The free spot in reach nearest to a chest out of reach, when it is nearer to it than
        every roboport in reach: ((score), spot), or None."""
        def gap(x, y):
            return min(max(abs(float(c.x) - x), abs(float(c.y) - y)) for c in chests)
        near = min(self.ports, key=lambda p: gap(p[0], p[1]), default=None)
        if near is None:
            return None
        x, y, lr, cr = near
        d = min(cr, lr + self.r)
        best = None
        for j in range(int(y - d - ground.ph / 2) - 1, int(y + d) + 2):
            for i in range(int(x - d - ground.pw / 2) - 1, int(x + d) + 2):
                if not ground.free((i, j)):
                    continue
                px, py = ground.centre((i, j))
                if not self.joins(px, py):
                    continue
                score = (gap(px, py), j, i)
                if best is None or score < best[0]:
                    best = (score, (i, j))
        if best is None or best[0][0] >= gap(x, y):
            return None
        return best


def reach(rows, standing, cat):
    """REACH (2.1.3): of ``rows`` [(key, name, x, y)], what the paste lays, the ones outside every
    construction area that stands or that the paste's own roboports give it. ``standing``: [(x, y,
    logistic radius, construction radius)] of the roboports that stand with power. A roboport of
    the paste gives its construction area once it is inside one that stands (robots build it) and
    its logistic square meets that network's (it joins it, and its robots work from it), and so on
    outward. -> the rows out of reach, the paste's roboports among them first."""
    ports = [(float(x), float(y), float(lr), float(cr)) for x, y, lr, cr in standing]

    def radii(name):
        row = cat.entity(name) if cat.has_entity(name) else {}
        return float(row.get("logistic_radius") or 0), float(row.get("construction_radius") or 0)

    def built(x, y):
        return any(abs(x - px) <= cr and abs(y - py) <= cr for px, py, _lr, cr in ports)

    todo = [row for row in rows if cat.has_entity(row[1]) and cat.kind(row[1]) == "roboport"]
    grew = True
    while grew:
        grew = False
        for row in list(todo):
            _key, name, x, y = row
            lr, cr = radii(name)
            if built(float(x), float(y)) and any(
                    abs(float(x) - px) <= lr + plr and abs(float(y) - py) <= lr + plr
                    for px, py, plr, _cr in ports):
                ports.append((float(x), float(y), lr, cr))
                todo.remove(row)
                grew = True
    out = [row for row in rows if not built(float(row[2]), float(row[3]))]
    return sorted(out, key=lambda row: (row not in todo, natural_key(row[0] or "")))


def lua_roboports(surface, force, tag):
    """Lua writing ``fk5/<tag>.json``: [[x, y, logistic radius, construction radius]] of every
    roboport of ``force`` on ``surface`` that stands with power (its cell transmits); it replies
    with the count. A free read."""
    q = (lambda t: "'" + str(t).replace("\\", "\\\\").replace("'", "\\'") + "'")
    return f"""
local s, f = game.get_surface({q(surface)}), game.forces[{q(force)}]
if not s or not f then rcon.print('no surface or force') return end
local out = {{}}
for _, r in pairs(s.find_entities_filtered{{type = 'roboport', force = f}}) do
  local c = r.logistic_cell
  if c and c.transmitting and c.construction_radius > 0 then
    out[#out + 1] = {{r.position.x, r.position.y, c.logistic_radius, c.construction_radius}}
  end
end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json(out), false)
rcon.print(#out)
"""


def _free_tile(ground, t, avoid):
    """True when a 1x1 chest fits on tile ``t``: inside the frame, on no entity, keep-out or
    placed tile, and off ``avoid``."""
    size = ground.size
    if size is not None and not (0 <= t[0] < size[0] and 0 <= t[1] < size[1]):
        return False
    return not (t in ground.taken or t in ground.keep_out or t in ground.placed or t in avoid)


def _hands_and_fronts(cat, ents):
    """Tiles a chest beside a roboport keeps off: where an inserter picks or drops, where a drill
    drops, where a belt points (``fkcore.route``'s ground rules)."""
    from . import route
    return set(route._hand_tiles(cat, ents)) | set(route._belt_fronts(cat, ents))


def _beside(ground, spot, avoid):
    """The first free tile around a roboport's footprint (north row west to east, then the east,
    south and west sides), inside the frame, on no entity, keep-out or placed tile; else None."""
    i, j, w, h = spot[0], spot[1], ground.pw, ground.ph
    ring = ([(x, j - 1) for x in range(i - 1, i + w + 1)]
            + [(i + w, y) for y in range(j, j + h)]
            + [(x, j + h) for x in range(i + w, i - 2, -1)]
            + [(i - 1, y) for y in range(j + h - 1, j - 1, -1)])
    for t in ring:
        if _free_tile(ground, t, avoid):
            return t
    return None


def _clean(v):
    return int(v) if float(v).is_integer() else v


def apply_robots(lay, cat, owner="robots", keep_out=(), roboport="roboport", served=(),
                 storage=None, pinned=(), reach=None):
    """A new Layout: ``lay`` without ``owner``'s previous roboports and storage chests, with the
    new ones (the pinned ones that stay among them)."""
    news, rep = cover_robots(lay, cat, owner, keep_out, roboport, served, storage, pinned, reach)
    out = copy.deepcopy(lay)
    out.entities = {k: e for k, e in out.entities.items() if not _is_own(e, owner)}
    if news or owner in out.covers:
        old = out.covers.get(owner)
        params = dict(old.params) if old is not None else {}
        params["roboport"] = roboport
        out.covers[owner] = fkp.Cover(owner, "robots", params)
    for e in news:
        out.add(e)
    return out, rep


def served(places, infra, layouts, factory, cat):
    """[(x, y, logistic radius)] in ``factory``'s frame: the roboports of the surface's main
    network (the one holding a roboport outside every plot, as :func:`check_surface` judges it)
    that stand outside ``factory`` and whose logistic square reaches its frame. ``places``:
    {factory: surface.Place}; ``infra``: the surface .fkp's own entities; ``layouts``: {factory:
    Layout} as laid out so far."""
    ports = [_Port(e.key, e.name, float(e.x), float(e.y), None) for e in infra
             if cat.has_entity(e.name) and cat.kind(e.name) == "roboport"]
    for name, lay in sorted(layouts.items()):
        p = places.get(name)
        if p is None or not lay.size:
            continue
        for e in lay.entities.values():
            if cat.has_entity(e.name) and cat.kind(e.name) == "roboport" \
                    and not e.attrs.get("dnp"):
                x, y, _d = turn(float(e.x), float(e.y), None, p.rot, lay.size)
                ports.append(_Port(f"{name}/{e.key}", e.name, x + p.x, y + p.y, name))
    main = next((n for n in networks(ports, cat) if any(q.plot is None for q in n)), None)
    me, lay = places.get(factory), layouts.get(factory)
    if main is None or me is None or lay is None or not lay.size:
        return []
    w, h = lay.size
    turned = (h, w) if me.rot % 2 else (w, h)
    out = []
    for q in main:
        if q.plot == factory:
            continue
        fx, fy, _d = turn(q.x - me.x, q.y - me.y, None, (4 - me.rot) % 4, turned)
        r = _radius(cat, q.name, "logistic_radius")
        if fx + r >= 0 and fx - r <= w and fy + r >= 0 and fy - r <= h:
            out.append((fx, fy, r))
    return out


def in_frame(places, layouts, factory, standing):
    """``standing`` [(x, y, logistic radius, construction radius)] in map coordinates, as
    ``factory``'s frame has them (``cover robots``' ``reach``), or None when either is unknown."""
    me, lay = places.get(factory), layouts.get(factory)
    if standing is None or me is None or lay is None or not lay.size:
        return None
    w, h = lay.size
    turned = (h, w) if me.rot % 2 else (w, h)
    out = []
    for x, y, lr, cr in standing:
        fx, fy, _d = turn(float(x) - me.x, float(y) - me.y, None, (4 - me.rot) % 4, turned)
        out.append((fx, fy, float(lr), float(cr)))
    return out


def networks(ents, cat):
    """Logistic networks of roboports: [[keys]], joined when their logistic squares meet."""
    ports = sorted((e for e in ents if cat.kind(e.name) == "roboport"), key=lambda e: natural_key(e.key))
    parent = list(range(len(ports)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, a in enumerate(ports):
        ra = _radius(cat, a.name, "logistic_radius")
        for j in range(i + 1, len(ports)):
            b = ports[j]
            reach = ra + _radius(cat, b.name, "logistic_radius")
            if abs(float(a.x) - float(b.x)) <= reach and abs(float(a.y) - float(b.y)) <= reach:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    groups = {}
    for i, p in enumerate(ports):
        groups.setdefault(find(i), []).append(p)
    return [groups[k] for k in sorted(groups)]


def check_surface(s, cat):
    """(lines, checks) for a composed surface: every factory's chests and entities against the
    main logistic network (the one holding a roboport outside every plot)."""
    nets = networks(list(s.entities.values()), cat)
    main = next((n for n in nets if any(p.plot is None for p in n)), None)
    if main is None:
        return ["robots: no roboport outside the plots (the kit's): nothing to judge against"], []
    lines = [f"robots: {len(nets)} logistic network{'s' if len(nets) != 1 else ''}; the main one "
             f"has {len(main)} roboports"]
    checks = []
    for plot in sorted(s.plots):
        mine = [e for e in s.entities.values() if e.plot == plot]
        chests = [e for e in mine if e.name in LOGISTIC_CHESTS]
        out_log = [c for c in chests if not any(
            _inside(float(c.x), float(c.y), float(p.x), float(p.y),
                    _radius(cat, p.name, "logistic_radius")) for p in main)]
        out_con = [e for e in mine if not any(
            _inside(float(e.x), float(e.y), float(p.x), float(p.y),
                    _radius(cat, p.name, "construction_radius")) for p in main)]
        lines.append(f"  {plot}: {len(chests) - len(out_log)}/{len(chests)} logistic chests in the "
                     f"main network, {len(mine) - len(out_con)}/{len(mine)} entities in its "
                     "construction area")
        for c in out_log:
            checks.append(Check("no-robots", f"{s.name} {c.key}", "outside every logistic area of "
                                "the main network: robots will not serve it"))
        if out_con:
            checks.append(Check("no-robots", f"{s.name} {plot}", f"{len(out_con)} entities are "
                                "outside the main network's construction area: robots will not "
                                "build them (a roboport within reach first)"))
    return lines, checks
