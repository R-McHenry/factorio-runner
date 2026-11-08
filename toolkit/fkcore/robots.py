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
where none reaches and never moves the network that stands. 3.1 (Rory, 2026-11-08: the world
never feeds a step): the roboports that stand in the game do not decide where new ones go (2.1.3
had chained new roboports from the last diff's read); the design says, and :func:`report` (``fk
robots``) is where the standing roboports are judged against it.

:func:`report` is that read's pure half: the roboports standing on the surface (``fk robots``
reads them with :func:`lua_standing`) against the design's logistic chests, in an area or all of
them: which chests a powered roboport's logistic square covers and which not, which roboports
are redundant (every chest they reach another kept one reaches too, and taking them out splits
no network), and how many roboports :func:`cover_robots` would lay for the same chests on free
ground. It changes nothing.

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
from dataclasses import dataclass

from . import fkp
from .compose import turn
from .cover import _Ground
from .elaborate import Check
from .fkp import Entity, natural_key

__all__ = ["LOGISTIC_CHESTS", "RobotReport", "cover_robots", "apply_robots", "networks",
           "served", "check_surface", "reach", "lua_roboports", "lua_standing", "Standing",
           "StandingReport", "report", "minimal"]

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
                 storage=None, pinned=()):
    """-> (new Entities: roboports keyed ``<owner>/r<k>`` and, with ``storage`` (a chest
    prototype), a storage chest beside each keyed ``<owner>/s<k>``; RobotReport). ``served``:
    [(x, y, radius)] logistic squares of the surface's network in this factory's frame.
    ``pinned``: the committed .fkp's own ``r<k>`` / ``s<k>`` (1.7, pin and patch): a roboport
    that still fits and still reaches a chest of the factory stays where it stands, and so does
    its storage chest where that still fits; new roboports go only where no kept one reaches.
    Pure: nothing of the world comes in (3.1; whether robots can reach a new roboport is the
    diff's ``UNREACHED`` and report_bom's ``REACH`` to say)."""
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
                n = sum(1 for c in left if _inside(float(c.x), float(c.y), px, py, r))
                if not n:
                    continue
                score = (-n, (px - cx) ** 2 + (py - cy) ** 2, j, i)
                if best is None or score < best[0]:
                    best = (score, (i, j))
        if best is None:
            break
        spot = best[1]
        ground.place(spot)
        px, py = ground.centre(spot)
        placed.append((spot, px, py))
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
    why = "no free 4x4 spot inside the factory's frame reaches it"
    uncovered = [(c.key, why) for c in left]
    rep = RobotReport(owner, [e.key for e in news], len(chests), len(chests) - len(left),
                      uncovered, served=len(outside), storage=[e.key for e in chests_out],
                      no_room=no_room, pinned=len(kept))
    return news + chests_out, rep


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
                 storage=None, pinned=()):
    """A new Layout: ``lay`` without ``owner``'s previous roboports and storage chests, with the
    new ones (the pinned ones that stay among them)."""
    news, rep = cover_robots(lay, cat, owner, keep_out, roboport, served, storage, pinned)
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


# -- fk robots: what stands against the design (3.1, a pure read) ---------------------------------
@dataclass
class Standing:
    """One roboport standing on the surface, as ``fk robots`` reads it (:func:`lua_standing`)."""
    name: str
    x: float
    y: float
    lr: float                       # logistic radius (tiles each way from the centre)
    cr: float                       # construction radius
    powered: bool                   # its logistic cell transmits (robots work from it)

    @property
    def at(self):
        return f"{fkp.fmt(self.x)},{fkp.fmt(self.y)}"


def lua_standing(surface, force, tag, area=None):
    """Lua writing ``fk5/<tag>.json``: [[name, x, y, logistic radius, construction radius,
    powered]] of every roboport of ``force`` on ``surface`` (inside ``area`` (x0, y0, x1, y1), when
    given); it replies with the count. A free read, nothing changed."""
    q = (lambda t: "'" + str(t).replace("\\", "\\\\").replace("'", "\\'") + "'")
    filt = f"type = 'roboport', force = f"
    if area is not None:
        x0, y0, x1, y1 = (float(v) for v in area)
        filt += f", area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}}"
    return f"""
local s, f = game.get_surface({q(surface)}), game.forces[{q(force)}]
if not s or not f then rcon.print('no surface or force') return end
local out = {{}}
for _, r in pairs(s.find_entities_filtered{{{filt}}}) do
  local c = r.logistic_cell
  local lr, cr, on = 0, 0, false
  if c then lr, cr, on = c.logistic_radius, c.construction_radius, c.transmitting and true or false end
  out[#out + 1] = {{r.name, r.position.x, r.position.y, lr, cr, on}}
end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json(out), false)
rcon.print(#out)
"""


def standing_rows(rows, cat):
    """The read's rows -> [Standing]; a radius the game gave as 0 (no cell) is the catalog's."""
    out = []
    for row in rows if isinstance(rows, list) else []:
        if len(row) < 6:
            continue
        name, x, y, lr, cr, on = row[:6]
        lr = float(lr) or (_radius(cat, name, "logistic_radius") if cat.has_entity(name) else 0.0)
        cr = float(cr) or (_radius(cat, name, "construction_radius") if cat.has_entity(name)
                           else 0.0)
        out.append(Standing(str(name), float(x), float(y), lr, cr, bool(on)))
    return sorted(out, key=lambda p: (p.y, p.x))


def _square_meets(p, area):
    x0, y0, x1, y1 = area
    return p.x - p.lr < x1 and p.x + p.lr > x0 and p.y - p.lr < y1 and p.y + p.lr > y0


def _groups_of(ports):
    """Logistic networks among ``ports`` [Standing]: joined when their logistic squares meet."""
    parent = list(range(len(ports)))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i, a in enumerate(ports):
        for j in range(i + 1, len(ports)):
            b = ports[j]
            if abs(a.x - b.x) <= a.lr + b.lr and abs(a.y - b.y) <= a.lr + b.lr:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[max(ri, rj)] = min(ri, rj)
    return len({find(i) for i in range(len(ports))})


@dataclass
class StandingReport:
    area: tuple | None
    standing: list                  # [Standing] that serve the area (their square meets it)
    chests: list                    # [(key, name, x, y)] the design's logistic chests there
    covered: dict                   # chest key -> [Standing] powered ones whose square holds it
    redundant: list                 # [Standing] powered ones the rest cover for
    minimal: list                   # [(x, y)] cover_robots' roboports for these chests alone
    networks: int                   # among the powered ones

    @property
    def powered(self):
        return [p for p in self.standing if p.powered]

    @property
    def unpowered(self):
        return [p for p in self.standing if not p.powered]

    @property
    def uncovered(self):
        return [c for c in self.chests if not self.covered.get(c[0])]

    def lines(self, surface, show=8):
        def few(items):
            return "; ".join(items[:show]) + (f"; ... {len(items) - show} more"
                                               if len(items) > show else "")
        where = f" area {','.join(str(int(v)) for v in self.area)}" if self.area else ""
        n, on = len(self.standing), len(self.powered)
        out = [f"robots {surface}{where}: {n} roboport{'s' if n != 1 else ''} stand ({on} with "
               f"power, {self.networks} network{'s' if self.networks != 1 else ''}); "
               f"{len(self.chests)} logistic chests in the design"]
        if not self.chests:
            out.append("  no logistic chest to cover here: every roboport is redundant for them")
        else:
            bad = self.uncovered
            out.append(f"  covered: {len(self.chests) - len(bad)} of {len(self.chests)} chests "
                       f"inside a powered roboport's logistic square"
                       + ("" if not bad else "; uncovered: " + few(
                           [f"{k.split(':', 1)[-1]} at {fkp.fmt(x)},{fkp.fmt(y)}"
                            for k, _n, x, y in bad])))
        idle = [p for p in self.redundant if not any(p in v for v in self.covered.values())]
        out.append(f"  redundant: {len(self.redundant)} of the {on} powered (every chest they "
                   f"reach another reaches too; taking them out splits no network)"
                   + (f", {len(idle)} of them reach no chest" if idle else "")
                   + (": " + few([p.at for p in self.redundant]) if self.redundant else ""))
        if self.chests:
            out.append(f"  minimal: {len(self.minimal)} roboport{'s' if len(self.minimal) != 1 else ''}"
                       f" would cover every chest (cover robots on these chests alone, free "
                       f"ground): " + few([f"{fkp.fmt(x)},{fkp.fmt(y)}" for x, y in self.minimal]))
        if self.unpowered:
            out.append(f"  without power: {len(self.unpowered)} (they serve nothing): "
                       + few([p.at for p in self.unpowered]))
        out.append("  nothing changed: the design says where roboports go (cover robots); a "
                   "roboport the design does not have is the diff's difference")
        return out


def minimal(chests, cat, roboport="roboport"):
    """[(x, y)] the roboports :func:`cover_robots` lays for ``chests`` [(key, name, x, y)]
    alone, on free ground round them (nothing else of the design or the world in the way)."""
    if not chests:
        return []
    r = _radius(cat, roboport, "logistic_radius")
    w, h = cat.size(roboport)
    pad = int(r) + max(w, h) + 1
    x0 = int(min(float(c[2]) for c in chests)) - pad
    y0 = int(min(float(c[3]) for c in chests)) - pad
    x1 = int(max(float(c[2]) for c in chests)) + pad + 1
    y1 = int(max(float(c[3]) for c in chests)) + pad + 1
    text = f"fkp 1 factory chests\nsize {x1 - x0}x{y1 - y0}\ninst m Literal at 0,0 rot 0 sig -\n"
    for k, (_key, name, x, y) in enumerate(chests):
        name = name if name in LOGISTIC_CHESTS else "passive-provider-chest"
        text += f"e m/c{k} {name} {fkp.fmt(float(x) - x0)},{fkp.fmt(float(y) - y0)}\n"
    news, _rep = cover_robots(fkp.loads(text), cat, roboport=roboport)
    return sorted((float(e.x) + x0, float(e.y) + y0) for e in news
                  if cat.kind(e.name) == "roboport")


def report(standing, chests, cat, area=None, roboport="roboport") -> StandingReport:
    """The surface's roboports ``standing`` [Standing] against the design's logistic ``chests``
    [(key, name, x, y)], both in map coordinates. With ``area`` (x0, y0, x1, y1): the chests
    inside it and the roboports whose logistic square meets it. Coverage counts powered
    roboports; a redundant one is a powered roboport every chest of which another kept one
    covers, taken out one by one (fewest chests first) while the network count stays. The
    minimal set is :func:`minimal` on the same chests. Pure, changes nothing."""
    if area is not None:
        x0, y0, x1, y1 = area
        chests = [c for c in chests if x0 <= float(c[2]) < x1 and y0 <= float(c[3]) < y1]
        standing = [p for p in standing if _square_meets(p, area)]
    chests = sorted(chests, key=lambda c: natural_key(c[0]))
    kept = [p for p in standing if p.powered]

    def covers(p, c):
        return _inside(float(c[2]), float(c[3]), p.x, p.y, p.lr)

    covered = {c[0]: [p for p in kept if covers(p, c)] for c in chests}
    nets = _groups_of(kept)
    redundant, again = [], True
    while again:                       # to a fixed point: a bridge to one that went is idle now
        again = False
        for p in sorted(kept, key=lambda p: (sum(1 for c in chests if covers(p, c)), p.y, p.x)):
            rest = [q for q in kept if q is not p]
            if all(any(covers(q, c) for q in rest) for c in chests if covers(p, c)) \
                    and _groups_of(rest) <= nets:
                kept = rest
                redundant.append(p)
                again = True
    return StandingReport(tuple(area) if area else None, list(standing), chests, covered,
                          redundant, minimal(chests, cat, roboport), nets)


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
