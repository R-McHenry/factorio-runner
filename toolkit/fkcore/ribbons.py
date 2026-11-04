"""fkcore.ribbons: several belts routed as one ribbon (2.1, PLAN-2.1 "Bus routing: ribbons").

A ribbon is several belts laid as one path, side by side and in a fixed order, belt k always k
tiles from belt 0. Three things make one:

- **``net each NAME``**: its nets ``NAME_0 .. NAME_k`` (``flat.each``), belt k carrying net k;
- **``bundle NAME: a, b, c``** (``flat.bundles``): any nets that run the same way, in that order;
- **a wide net**, ``width N`` above 2: N/2 belts of one net. Its drivers are dealt out to the belts
  at the ribbon's head (``_deal``: in groups along the way they stand, the groups' rates as even as
  they come, no belt more than its two lanes' worth), and its consumers likewise at its tail, so
  each belt is a driver group's way to a consumer group.

**The route** (``route``) is one search, ``fkcore.steiner.ribbon``: the ribbon's cross-section
moves as one footprint, straight, through bus turns (nested, the inner belt turning first) and
over or under what it crosses: each belt goes under only what blocks its own line, with a tunnel
of its own, and the rest stay on the surface (a crossing that blocks them all is every belt under
together). A tunnel reaches as far as the fastest underground the ribbon may lay (``_UG``, set by
``route.route_layout``'s ``ug``: yellow's 5 tiles unless a faster tier is allowed), and each one
is laid with the slowest tier that reaches it, so the tier pass (``fkcore.tiers``) never gives it
one that falls short. It starts
where every belt's driver starts in one row (an aligned head), or, searched from the other end,
where every consumer's first belt is in one row; with neither, anywhere the drivers' belts can
reach, ranked by how far they walk to it. A belt ends where its one consumer's belt starts at its
own offset (the rest go on at theirs: the ribbon doesn't close up), or every belt still in the
ribbon leaves it together onto loose belts to its consumers. The loose parts, the drivers' belts
to the head and the belts from the tail on, are plain routes (``route._route_one``), laid after
the ribbon. Both orders of the belts across the ribbon are tried (belt 0 on the left or on the
right), and the cheapest that lays whole is kept.

A ribbon that finds no path is reported as unrouted, every net of it, never split into loose
belts (PLAN-2.1, decided: that is what the feature exists to avoid).

Refused (the route's reason): bundle nets that run different ways, a ribbon with a net that has no
driver or consumer placed, a net that shares a belt (``lanes``), and a wide net with fewer drivers
or consumers than belts, or whose drivers bring more than its belts carry. Pure, stdlib only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import fkp as fkpmod
from .steiner import DX, DY, ribbon as _search, _cross
from .tiers import TIERS, tier_of

__all__ = ["Ribbon", "plan", "route"]

LOOSE = 2.0          # a loose belt's tile costs this much against a ribbon belt's
_HEADS = 4000        # head cross-sections a ribbon with no aligned end may start from, at most
_LIMIT = 300000      # cross-sections the search looks at, at most
_NESW = "NESW"
#: the undergrounds a ribbon may lay, slowest first: [(name, reach)] (``ug_tiers``)
_UG = (("underground-belt", 5),)


def ug_tiers(cat, tier="yellow"):
    """[(underground name, reach)] for every tier up to ``tier`` (a tier name, yellow red blue
    turbo, or an underground's name), slowest first: what ``_UG`` holds."""
    from .tiers import FAMILY, TIERS
    names = FAMILY["underground-belt"]
    top = names.index(tier) if tier in names else TIERS.index(tier)
    return tuple((name, int(cat.entity(name).get("max_distance") or 5))
                 for name in names[:top + 1] if cat.has_entity(name))


def _ug_name(dist):
    """The slowest underground of ``_UG`` that reaches ``dist`` tiles."""
    for name, reach in _UG:
        if reach >= dist:
            return name
    return _UG[-1][0]


@dataclass
class Ribbon:
    name: str                      # the each group, the bundle or the wide net
    kind: str                      # "each" | "bundle" | "wide"
    nets: list                     # per belt: its net
    options: list = field(default_factory=list)   # [[NetJob per belt]]: ways to deal (wide)
    skip: str | None = None

    @property
    def n(self):
        return len(self.nets)


# -- which ribbons -------------------------------------------------------------------------------
def plan(flat, cat, js, todo):
    """{ribbon name: Ribbon} for the ribbons all of whose nets are routed this run (``todo``)."""
    from .elaborate import ribbons as groups
    from .route import NetJob
    out = {}
    todo = set(todo)
    for name, nets in sorted(groups(flat).items()):
        if not nets or not all(n in todo for n in nets):
            continue
        kind = "each" if name in getattr(flat, "each", {}) else "bundle"
        rib = Ribbon(name, kind, list(nets), [[js[n] for n in nets]])
        rib.skip = _refused(flat, rib, js)
        out[name] = rib
    for net in sorted(todo):
        fn = flat.nets.get(net)
        if fn is None or fn.width <= 2 or net in out:
            continue
        job = js[net]
        belts = fn.width // 2
        rib = Ribbon(net, "wide", [net] * belts)
        if job.parts or job.fluid:
            rib.skip = f"net {net} width {fn.width}: a wide net is belts, one net on all of them"
        else:
            got = _deal(flat, cat, fn, job, belts, NetJob)
            if isinstance(got, str):
                rib.skip = got
            else:
                rib.options = got
        out[net] = rib
    return out


def _centre(job, side):
    pts = []
    if side == "in":
        pts += [t for t, _h in job.starts]
        pts += [((r[0] + r[2]) / 2, (r[1] + r[3]) / 2) for _k, r in job.sources]
    else:
        pts += [t for t, _h in job.goals]
        pts += [((r[0] + r[2]) / 2, (r[1] + r[3]) / 2) for _k, r in job.taps]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def _compass(v):
    if abs(v[0]) >= abs(v[1]):
        return "east" if v[0] > 0 else "west"
    return "south" if v[1] > 0 else "north"


def _refused(flat, rib, js):
    """Why a bundle or each group can't be one ribbon, or None."""
    what = f"{'net each' if rib.kind == 'each' else 'bundle'} {rib.name}"
    jobs = rib.options[0]
    for net, job in zip(rib.nets, jobs):
        if job.skip:
            return f"{what}: net {net}: {job.skip}"
        if job.fluid:
            return f"{what}: net {net} is a fluid's, and a ribbon is belts"
        if job.parts or net in getattr(flat, "lane_of", {}):
            return f"{what}: net {net} shares a belt with another net; a ribbon's nets are a belt each"
        if not job.drivers():
            return f"{what}: net {net} has no driver placed"
        if not job.consumers():
            return f"{what}: net {net} has no consumer placed"
    ways = []
    for net, job in zip(rib.nets, jobs):
        a, b = _centre(job, "in"), _centre(job, "out")
        v = (b[0] - a[0], b[1] - a[1])
        if v != (0, 0):
            ways.append((net, v))
    for i, (na, va) in enumerate(ways):
        for nb, vb in ways[i + 1:]:
            if va[0] * vb[0] + va[1] * vb[1] <= 0:
                return (f"{what}: its nets run different ways ({na} runs {_compass(va)}, {nb} "
                        f"{_compass(vb)}); a ribbon's belts run the same way")
    return None


def _partition(weights, m):
    """Cut ``weights`` (in order) into ``m`` contiguous non-empty groups, the largest group's sum
    as small as it comes: -> [group sizes]."""
    n = len(weights)
    pre = [0.0]
    for w in weights:
        pre.append(pre[-1] + w)
    best = {(0, 0): (0.0, None)}
    for k in range(1, m + 1):
        for i in range(k, n - (m - k) + 1):
            cand = None
            for j in range(k - 1, i):
                if (j, k - 1) not in best:
                    continue
                v = max(best[j, k - 1][0], pre[i] - pre[j])
                if cand is None or v < cand[0] - 1e-12:
                    cand = (v, j)
            if cand is not None:
                best[i, k] = cand
    sizes, i = [], n
    for k in range(m, 0, -1):
        j = best[i, k][1]
        sizes.append(i - j)
        i = j
    return list(reversed(sizes))


def _along(entries, where):
    """``entries`` sorted along the axis they spread on (x when they spread wider in x)."""
    pts = [where(e) for e in entries]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    axis = 0 if max(xs) - min(xs) >= max(ys) - min(ys) else 1
    return [e for _p, e in sorted(zip(pts, entries), key=lambda pe: (pe[0][axis],
                                                                       pe[0][1 - axis]))]


def _deal(flat, cat, fn, job, belts, NetJob):
    """A wide net's drivers and consumers dealt out to its belts: [[NetJob per belt]] (the
    consumer groups in both orders against the driver groups), or the reason it can't be."""
    from .route import _driver_rate, _lane_cap
    what = f"net {job.net} width {fn.width}"
    cap = 2 * _lane_cap(cat)

    def at(entry):
        e = entry[0]
        if isinstance(e[0], str):
            r = e[1]
            return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)
        return e[0]

    drv = [(entry, path) for entry, path in job.drv]
    if len(drv) < belts:
        return (f"{what} is {belts} belts, and {len(drv)} driver"
                f"{' is' if len(drv) == 1 else 's are'} placed: each belt needs one of its own")
    cons = [(t, "goal") for t in job.goals] + [(t, "tap") for t in job.taps]
    if len(cons) < belts:
        return (f"{what} is {belts} belts, and {len(cons)} consumer"
                f"{' is' if len(cons) == 1 else 's are'} placed: each belt needs one of its own")
    drv = _along(drv, at)
    rates = [_driver_rate(flat, fn, entry, path) for entry, path in drv]
    big = [r for r in rates if r > cap + 1e-9]
    if big:
        return (f"{what}: a driver makes {max(big):.1f}/s, more than one belt's two lanes "
                f"({cap:.1f}/s)")
    sizes = _partition(rates, belts)
    groups, i = [], 0
    for s in sizes:
        groups.append(drv[i:i + s])
        i += s
    worst = max(sum(_driver_rate(flat, fn, e, p) for e, p in grp) for grp in groups)
    if worst > cap + 1e-9:
        return (f"{what}: its drivers make {sum(rates):.1f}/s, and dealt out to {belts} belts one "
                f"gets {worst:.1f}/s, more than its two lanes ({cap:.1f}/s)")

    def cat_at(c):
        t, kind = c
        if kind == "goal":
            return t[0]
        r = t[1]
        return ((r[0] + r[2]) / 2, (r[1] + r[3]) / 2)

    cons = _along(cons, cat_at)
    csizes = _partition([1.0] * len(cons), belts)
    cgroups, i = [], 0
    for s in csizes:
        cgroups.append(cons[i:i + s])
        i += s
    out = []
    for order in (cgroups, list(reversed(cgroups))):
        jobs = []
        for grp, cg in zip(groups, order):
            j = NetJob(job.net, owners=list(job.owners))
            for (entry, path) in grp:
                (j.sources if isinstance(entry[0], str) else j.starts).append(entry)
                j.drv.append((entry, path))
            for t, kind in cg:
                (j.goals if kind == "goal" else j.taps).append(t)
            jobs.append(j)
        out.append(jobs)
    return out


# -- the route -----------------------------------------------------------------------------------
def _row(tiles_heads, n):
    """[(x, y, h, offsets)] the cross-sections ``tiles_heads`` (per belt (tile, heading)) stand in:
    one row, one heading, belt b at offset b or n-1-b."""
    hs = {h for _t, h in tiles_heads}
    if len(hs) != 1:
        return []
    (h,) = hs
    out = []
    for off in (list(range(n)), list(range(n - 1, -1, -1))):
        b0 = off.index(0)
        x, y = tiles_heads[b0][0]
        if all(tuple(tiles_heads[b][0]) == _cross(x, y, h, off[b]) for b in range(n)):
            out.append((x, y, h, off))
    return out


def _one(jobs, side):
    """Per belt (tile, heading) of its one belt end on ``side`` ("in": its start, "out": its
    goal), or None where a belt has another kind of end or several."""
    out = []
    for j in jobs:
        if side == "in":
            if len(j.starts) != 1 or j.sources:
                return None
            out.append(j.starts[0])
        else:
            if len(j.goals) != 1 or j.taps:
                return None
            out.append(j.goals[0])
    return out


def _seeds(grid, job, side):
    from .route import _beside
    if side == "in":
        return [t for t, _h in job.starts] + [t for _k, r in job.sources for t in _beside(grid, r)]
    return [t for t, _h in job.goals] + [t for _k, r in job.taps for t in _beside(grid, r)]


def _ground(cat, ents, frames, js, nets, terrain, avoid):
    from .route import _belt_fronts, _hand_tiles
    mine = set(nets)
    blocked = set(frames) | set(terrain)
    for e in ents:
        blocked |= fkpmod.tiles_of(cat, e)
    for tile, owner in _belt_fronts(cat, ents).items():
        if owner not in mine:
            blocked.add(tile)
    for tile, owner in _hand_tiles(cat, ents).items():
        if owner not in mine:
            blocked.add(tile)
    for other, job in js.items():
        if other not in mine:
            blocked |= set(job.ends())
    return blocked | set(avoid)


def _dir(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx == 0:
        return 0 if dy < 0 else 2
    return 1 if dx > 0 else 3


def _flow_line(line, head, reverse):
    """A belt's search line as plan entities in flow order: [(tile, name, heading, ug)]."""
    if reverse:
        line = [[t, {"in": "out", "out": "in"}.get(k, k)] for t, k in reversed(line)]
        last = (head + 2) % 4
    else:
        last = head
    out = []
    for i, (t, kind) in enumerate(line):
        if kind == "out":
            h = _dir(line[i - 1][0], t)
        elif i + 1 < len(line):
            h = _dir(t, line[i + 1][0])
        else:
            h = last
        if kind == "belt":
            out.append((tuple(t), "transport-belt", h, None))
        else:
            out.append((tuple(t), "underground-belt", h, kind))
    return out


def route(cat, rib, ents, frames, js, box, limit, terrain=(), avoid=(), allow=frozenset()):
    """Lay ribbon ``rib``: -> ({net: [carriers]}, {net: report text}, summary) or
    (None, reason, None)."""
    from .route import _Bounded, _ug_spans, _walk
    if rib.skip:
        return None, rib.skip, None
    n = rib.n
    terrain = frozenset(terrain)
    spans = _ug_spans(cat, ents)
    tries, why = [], None
    for jobs in rib.options:
        ends = set()
        for j in jobs:
            ends |= set(j.ends())
        base = _ground(cat, ents, frames, js, rib.nets, terrain, avoid) - ends
        wgrid = _Bounded(base, box, spans)
        dwalk = [_walk(wgrid, _seeds(wgrid, j, "in")) for j in jobs]
        cwalk = [_walk(wgrid, _seeds(wgrid, j, "out")) for j in jobs]
        heads, tails = _one(jobs, "in"), _one(jobs, "out")
        modes = []
        if heads and _row(heads, n):
            for x, y, h, off in _row(heads, n):
                modes.append(("forward", [((x, y, h), 0.0)], off, {tuple(t) for t, _h in heads}))
        elif tails and _row(tails, n):
            for x, y, h, off in _row(tails, n):
                # searched from the tail: heading back, so belt 0's side is the other one
                hs = (h + 2) % 4
                off_s = [n - 1 - o for o in off]
                b0 = off_s.index(0)
                sx, sy = tails[b0][0]
                modes.append(("reverse", [((sx, sy, hs), 0.0)], off_s,
                              {tuple(t) for t, _h in tails}))
        else:
            for off in (list(range(n)), list(range(n - 1, -1, -1))):
                modes.append(("forward", None, off, set()))
        for mode, sources, off, open_ in modes:
            inv = [off.index(o) for o in range(n)]           # offset -> belt
            fixed, walks = [None] * n, [None] * n
            for b, j in enumerate(jobs):
                if mode == "forward":
                    if len(j.goals) == 1 and not j.taps:
                        fixed[off[b]] = (tuple(j.goals[0][0]), j.goals[0][1])
                    walks[off[b]] = cwalk[b]
                else:
                    if len(j.starts) == 1 and not j.sources:
                        fixed[off[b]] = (tuple(j.starts[0][0]), (j.starts[0][1] + 2) % 4)
                    walks[off[b]] = dwalk[b]
            free = set(open_) | {f[0] for f in fixed if f is not None}
            grid = _Bounded(base | (ends - free), box, spans)
            if sources is None:
                sources = _candidates(grid, n, inv, dwalk)
                if not sources:
                    why = why or "no free row of tiles for its head is reached from every driver"
                    continue
            own = {f[0]: (o, f[1]) for o, f in enumerate(fixed) if f is not None}
            got = _search(grid, n, sources, fixed, walks, loose=LOOSE, limit=_LIMIT, own=own,
                          costs={"max_ug": max(r for _n, r in _UG)})
            if "fail" in got:
                why = why or got["fail"]
                continue
            tries.append((got["cost"], len(tries), mode, off, jobs, got))
    if not tries:
        return None, f"ribbon {rib.name} ({n} belts): {why}", None
    tries.sort(key=lambda t: (t[0], t[1]))
    for _cost, _i, mode, off, jobs, got in tries:
        laid = _lay(cat, rib, mode, off, jobs, got, ents, frames, js, box, limit, terrain, avoid,
                    allow)
        if isinstance(laid, str):
            why = laid
            continue
        return laid
    return None, f"ribbon {rib.name} ({n} belts): {why}", None


def _candidates(grid, n, inv, dwalk):
    """Head cross-sections for a ribbon whose drivers don't start in one row: [((x, y, h), g0)],
    every belt's tile and the tile behind it free, g0 what the drivers' loose belts walk to them."""
    x0, y0, x1, y1 = grid.box
    rows = []
    for x in range(x0, x1):
        for y in range(y0, y1):
            for h in range(4):
                g0 = 0.0
                for o in range(n):
                    t = _cross(x, y, h, o)
                    back = (t[0] - DX[h], t[1] - DY[h])
                    if grid.blocked(t) or grid.blocked(back):
                        break
                    w = dwalk[inv[o]].get(back)
                    if w is None:
                        break
                    g0 += LOOSE * (w + 1)
                else:
                    rows.append((g0, (x, y, h)))
    rows.sort()
    return [(s, g0) for g0, s in rows[:_HEADS]]


def _lay(cat, rib, mode, off, jobs, got, ents, frames, js, box, limit, terrain, avoid, allow):
    """The search's ribbon as belts, then its loose parts: -> (carriers, texts, summary) or a
    reason."""
    from .route import NetJob, _route_one
    n = rib.n
    reverse = mode == "reverse"
    body, tiles = {}, set()
    for b in range(n):
        o = off[b]
        flow = _tiered(_flow_line(got["lines"][o], got["start"][2] if reverse else
                                  got["heads"][o], reverse))
        for t, _name, _h, _ug in flow:
            if t in tiles:
                return f"ribbon {rib.name}: its path crosses itself at {t[0]},{t[1]}"
            tiles.add(t)
        body[b] = flow
    # the loose parts: drivers' belts to the head (a head not in one row, or belts that join from
    # the side when searched from the tail), belts on from the tail to the consumers
    parts, new = [], set()
    for b, j in enumerate(jobs):
        o = off[b]
        end = got["ends"][o]
        first = body[b][0]
        if mode == "forward":
            if not (len(j.starts) == 1 and not j.sources and tuple(j.starts[0][0]) == first[0]):
                back = (first[0][0] - DX[first[2]], first[0][1] - DY[first[2]])
                parts.append((b, "in", NetJob(j.net, starts=list(j.starts), sources=list(j.sources),
                                              goals=[(back, first[2])], owners=list(j.owners))))
                new.add(back)
            if end[0] == "loose":
                new.add(end[1])
                parts.append((b, "out", NetJob(j.net, starts=[(end[1], got["heads"][o])],
                                               goals=list(j.goals), taps=list(j.taps),
                                               owners=list(j.owners))))
        else:
            if end[0] == "loose":
                new.add(end[1])
                into = (got["heads"][o] + 2) % 4
                parts.append((b, "in", NetJob(j.net, starts=list(j.starts), sources=list(j.sources),
                                              goals=[(end[1], into)], owners=list(j.owners))))
    hit = sorted(new & tiles)
    if hit:
        return f"ribbon {rib.name}: its path takes {hit[0][0]},{hit[0][1]}, where a loose belt starts"
    carriers = {b: [] for b in range(n)}
    for b in range(n):
        for t, name, h, ug in body[b]:
            attrs = {"net": rib.nets[b]}
            if ug:
                attrs["ug"] = ug
            carriers[b].append(fkpmod.Entity(f"~{rib.nets[b]}/r", name, t[0] + 0.5, t[1] + 0.5,
                                             h * 4, attrs))
    now = list(ents) + [e for b in range(n) for e in carriers[b]]
    loose = {b: {} for b in range(n)}
    why = None
    for order in _orders(parts, off):
        laid, how, ok, now2 = {}, {}, True, list(now)
        for i, (b, side, p) in enumerate(order):
            sub = {k: v for k, v in js.items() if k not in rib.nets}
            for m, (_b2, _s2, q) in enumerate(order):
                if m != i:
                    sub[f"{q.net}#{m}"] = q
            sub[p.net] = p
            got_p, text = _route_one(cat, p, now2, frames, sub, box, limit, terrain, avoid,
                                     None, allow)
            if got_p is None:
                why = f"belt {b} ({rib.nets[b]}), {'to the head' if side == 'in' else 'from the tail'}: {text}"
                ok = False
                break
            laid[i] = got_p
            how[i] = text
            now2 += got_p
        if ok:
            for i, (b, side, _p) in enumerate(order):
                loose[b][side] = (laid[i], how[i])
            break
    else:
        if parts:
            return f"ribbon {rib.name}: {why}"
    out, texts = {}, {}
    c = got["counts"]
    for b in range(n):
        ents_b = []
        if "in" in loose[b]:
            ents_b += loose[b]["in"][0]
        ents_b += carriers[b]
        if "out" in loose[b]:
            ents_b += loose[b]["out"][0]
        out.setdefault(rib.nets[b], []).extend(ents_b)
        belts = sum(1 for _t, name, _h, _u in body[b] if name == "transport-belt")
        ugs = sum(1 for _t, name, _h, _u in body[b] if name != "transport-belt")
        text = (f"belt {off[b]} of the ribbon {rib.name} ({n} belts side by side): {belts} belts, "
                f"{ugs} underground ends")
        if "in" in loose[b]:
            text += f"; its driver's way in: {loose[b]['in'][1]}"
        if "out" in loose[b]:
            text += f"; on from the ribbon: {loose[b]['out'][1]}"
        texts.setdefault(rib.nets[b], []).append(text)
    for net in out:
        out[net] = _keyed(net, out[net])
    texts = {net: " | ".join(t) for net, t in texts.items()}
    return out, texts, _summary(rib, body, off, got, parts, reverse)


def _tiered(flow):
    """``flow`` with each tunnel's two ends named the slowest underground of ``_UG`` that reaches
    it (a yellow one up to 5 tiles)."""
    out, at = list(flow), None
    for i, (t, name, h, ug) in enumerate(out):
        if ug == "in":
            at = i
        elif ug == "out" and at is not None:
            a = out[at][0]
            pick = _ug_name(abs(t[0] - a[0]) + abs(t[1] - a[1]))
            out[at] = (a, pick, out[at][2], "in")
            out[i] = (t, pick, h, "out")
            at = None
    return out


def _tunnels(rib, body, off, reverse):
    """The report's words for the belts that went under: "belt 2 13,10 to 18,10, ..."."""
    rows = []
    for b in range(rib.n):
        flow, at = body[b], None
        for t, name, _h, ug in flow:
            if ug == "in":
                at = t
            elif ug == "out" and at is not None:
                d = abs(t[0] - at[0]) + abs(t[1] - at[1])
                tier = "" if name == "underground-belt" else \
                    f" {TIERS[(tier_of(name) or (None, 0))[1]]}"
                rows.append((off[b], at, f"belt {off[b]} {at[0]},{at[1]} to {t[0]},{t[1]}"
                             + (f" ({d} tiles{tier})" if tier else "")))
                at = None
    rows.sort(key=lambda r: (r[1][0] + r[1][1], r[0]))
    words = [w for _o, _a, w in rows]
    return ", ".join(words[:8]) + (f" and {len(words) - 8} more" if len(words) > 8 else "")


def _summary(rib, body, off, got, parts, reverse=False):
    """The route report's ``ribbon`` line."""
    n, c = rib.n, got["counts"]
    head, tail = body[off.index(0)][0], body[off.index(0)][-1]
    joined = len({b for b, s, _p in parts if s == "in"})
    left = len({b for b, s, _p in parts if s == "out"})
    what = (", ".join(rib.nets) if rib.kind != "wide" else
            f"net {rib.nets[0]} width {2 * n}, its drivers dealt out")
    # looking along the flow; searched from the tail, the search's left is the flow's right
    order = " ".join(str(off.index(o)) for o in (range(n - 1, -1, -1) if reverse else range(n)))

    def s(k, one, many):
        return f"{k} {one if k == 1 else many}"

    return (f"ribbon    {rib.name}: {n} belts side by side ({what}), belts {order} from left to "
            f"right looking along it; head {head[0][0]},{head[0][1]} {_NESW[head[2]]}, tail {tail[0][0]},"
            f"{tail[0][1]} {_NESW[tail[2]]}; {s(c['turns'], 'bus turn', 'bus turns')}, "
            f"{s(c['crossings'], 'crossing', 'crossings')}"
            + (f" ({s(c['under'], 'belt', 'belts')} under: {_tunnels(rib, body, off, reverse)})"
               if c.get("under") else "")
            + f", {c['belts']} belts, {c['undergrounds']} underground ends; "
            f"{s(n - joined, 'belt starts', 'belts start')} where its driver's belt does, "
            f"{joined} from a belt laid to it; {s(n - left, 'belt ends', 'belts end')} where its "
            f"consumer's belt starts, {left} on in a belt of its own")


def _orders(parts, off):
    """The orders the loose parts are tried in: nearest the ribbon's middle first, then by offset
    one way and the other."""
    if not parts:
        return [[]]
    by_off = sorted(parts, key=lambda p: (off[p[0]], p[1]))
    return [by_off, list(reversed(by_off)),
            sorted(parts, key=lambda p: (abs(off[p[0]] - (len(off) - 1) / 2), p[1]))]


def _keyed(net, ents):
    """``ents`` keyed ``~<net>/<letter><k>`` in order (b belts, u undergrounds, s splitters, i the
    rest), as ``route._carriers`` keys a tree."""
    count = {}
    out = []
    for e in ents:
        letter = next((v for k, v in (("underground-belt", "u"), ("transport-belt", "b"),
                                       ("splitter", "s")) if e.name.endswith(k)), "i")
        k = count.get(letter, 0)
        count[letter] = k + 1
        out.append(fkpmod.Entity(f"~{net}/{letter}{k}", e.name, _clean(e.x), _clean(e.y),
                                 int(e.dir), dict(e.attrs)))
    return out


def _clean(v):
    return int(v) if float(v).is_integer() else v
