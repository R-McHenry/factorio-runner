"""fkcore.pairs: two nets that share a belt, laid as one pair (2.0, CORE-V2.md "The router in 2.0").

``lanes A | B`` (Rory, 2026-10-21: a net is one lane, so two nets can share a belt) puts A on the
left lane and B on the right of every belt they are on. ``fkcore.expand`` binds every consumer on
either net to the pair (``A|B``: one input belt of both lanes), so the router's job is to bring the
two nets together once and lay them on together from there. It lays the pair as one job, ``A|B``
(``merge``, from ``route.jobs``), in three parts (``route``, from ``route._route_one``):

- **the join**: a free tile H and a heading h. The pair's route starts on H; A's route ends on the
  tile at H's left, pointing into it, and B's on the tile at its right. A belt fed from both sides
  with nothing behind it takes each side as a side-load, a 2-to-1 merge onto its near lane (CORE-V2
  "Lane physics", measured in the game): all of A goes onto H's left lane, all of B onto its right,
  as at the head of each column of Rory's smelter stack. The tiles behind and ahead of H stay
  clear for the pair: nothing may straighten the join, and its first belt has room to go on.
- **A and B**: each net from its drivers to its side of the join (a tree, merges where it has
  several drivers), laid first, so that their last belts point into an empty tile;
- **both**: one route from H to every consumer of the pair, both lanes: belts, turns,
  undergrounds and splitters keep every item on its lane (the router lays no side-loads), so A
  stays on the left lane all the way. Every carrier of it is ``net=A|B``; a tap inserter
  ``net=A+B`` (it takes from both lanes what its machine accepts).

So each net is one lane: at most 7.5/s on yellow, and the routed rates' ``too-thin`` says so where
a net brings more (a two-sided MinerRow's ore side-loaded onto one lane). Every carrier is keyed
under the pair (``~A|B/b3``; A's own ``~A|B/lb3``, B's ``~A|B/rb3``), so a rebuild keeps or lays
again the three parts together (``route --only stale``: one signature). The joins are tried best
first, ranked by the walking distance from A's drivers to H's left, from B's to its right and from
the tile ahead of H to every consumer; the first join where all three parts route is laid. A pair
whose drivers are all single machines and whose consumers are all single machines is laid as
direct inserters where each fits, with no belt and so no lanes at all. Pure, stdlib only.
"""
from __future__ import annotations

from . import fkp as fkpmod
from . import route as routemod

__all__ = ["merge", "route", "joins"]

_TRIES = 6           # joins tried at most
_JOINS = 12          # join candidates ranked


def merge(flat, js):
    """Fold the jobs of every pair's two nets into the pair's own job, in place: A's and B's
    drivers, the pair's consumers, every anchor (one signature for the three parts)."""
    for key, (a, b) in flat.pairs.items():
        pair = js.pop(key, None) or routemod.NetJob(key)
        left = js.pop(a, None) or routemod.NetJob(a)
        right = js.pop(b, None) or routemod.NetJob(b)
        job = routemod.NetJob(key)
        for part in (left, right, pair):
            job.starts += part.starts
            job.sources += part.sources
            job.goals += part.goals
            job.taps += part.taps
            job.owners += part.owners
            job.anchors.update(part.anchors)
        job.parts = (left, right, pair)
        # 2.0.48: a pair that comes in paired (a stack cell's paired port drives the pair itself)
        # needs no driver of each lane's own
        missing = [] if pair.starts else [p.net for p in (left, right) if not p.drivers()]
        if missing:
            job.skip = (f"net {' and '.join(missing)} of the pair {key} has no driver role "
                        "placed")
        js[key] = job


def _step(t, h, k=1):
    return t[0] + routemod._DX[h] * k, t[1] + routemod._DY[h] * k


def _seeds(grid, job, side):
    """Where a part's walking distances start: its belt ends and the free tiles beside its
    machines (``side``: "drivers" or "consumers")."""
    if side == "drivers":
        return [[t] for t, _h in job.starts] + \
            [routemod._beside(grid, r) for _k, r in job.sources]
    return [[t] for t, _h in job.goals] + [routemod._beside(grid, r) for _k, r in job.taps]


def joins(grid, left, right, pair, ends=(), most=_JOINS):
    """[(H, h)] join candidates, best first: H and the tiles at its left (L), right (R), behind
    and ahead (F) free; ranked by the summed walking distance from every driver of A to L, of B to
    R, and from F to every consumer of the pair; a join within 2 tiles of an end of a part with
    several ends ranks last (as the router's hubs); picked 3 tiles apart."""
    da = [routemod._walk(grid, s) for s in _seeds(grid, left, "drivers") if s]
    db = [routemod._walk(grid, s) for s in _seeds(grid, right, "drivers") if s]
    dc = [routemod._walk(grid, s) for s in _seeds(grid, pair, "consumers") if s]
    # a lane pin's empty side (2.0.54) asks only that its tile be free, for the stub
    if (not da and left.drivers()) or (not db and right.drivers()) or not dc:
        return []
    ends = set(ends)
    rows = []
    for t in set.intersection(*(set(d) for d in dc)) - ends:
        if grid.blocked(t):
            continue
        for h in range(4):
            lt, rt = _step(t, (h + 3) % 4), _step(t, (h + 1) % 4)
            back, ahead = _step(t, (h + 2) % 4), _step(t, h)
            if any(grid.blocked(x) or x in ends for x in (lt, rt, back, ahead)):
                continue
            if any(lt not in d for d in da) or any(rt not in d for d in db) or \
                    any(ahead not in d for d in dc):
                continue
            a, b, c = [d[lt] for d in da], [d[rt] for d in db], [d[ahead] for d in dc]
            short = any(len(x) > 1 and min(x) < 3 for x in (a, b, c))
            rows.append((short, sum(a) + sum(b) + sum(c), t[1], t[0], h))
    rows.sort()
    out = []
    for _short, _score, y, x, h in rows:
        if any(abs(x - o[0]) + abs(y - o[1]) < 3 for o, _h in out):
            continue
        out.append(((x, y), h))
        if len(out) >= most:
            break
    return out


def _direct(cat, job, grid):
    """Machines only, each driver beside each consumer: one inserter each, or None."""
    left, right, pair = job.parts
    if left.starts or right.starts or pair.goals or not pair.taps:
        return None
    out, taken = [], set(job.ends())
    for _k, src in left.sources + right.sources:
        for _t, dst in pair.taps:
            got = routemod._direct_pair(src, dst, grid, taken)
            if got is None:
                return None
            taken.add((int(got["x"] // 1), int(got["y"] // 1)))
            out.append(got)
    return out


def route(cat, job, ents, frames, js, box, limit, terrain=(), avoid=(), spans=None,
          allow=frozenset()):
    """Lay a pair's job (``merge``): -> (carriers, report text) or (None, the reason)."""
    left, right, pair = job.parts
    net = job.net
    blocked = routemod._ground(cat, ents, frames, js, net, terrain) | \
        (set(avoid) - set(job.ends()))
    grid = routemod._Bounded(blocked, box, routemod._ug_spans(cat, ents) if spans is None
                             else spans)
    direct = _direct(cat, job, grid)
    if direct is not None:
        laid = routemod._carriers(job, direct, net)
        for e in laid:
            e.attrs["net"] = fkpmod.hand_net(net)
        return laid, f"{len(laid)} inserters (direct insertion), no belt"
    cands = joins(grid, left, right, pair, job.ends())
    if not cands:
        return None, (f"no free tile joins {left.net} and {right.net}: a join needs a tile with "
                      "free tiles on all four sides, reached from both nets' drivers and every "
                      "consumer")
    why = None
    # 2.0.54: a net's own two groups (or a lane pin's one) try every ranked join, each group laid
    # first in turn: the one laid first can wall the other off its side
    one = job.wide or job.pin
    tries = len(cands) if one else _TRIES
    for hub, h in cands[:tries]:
        for flip in ((False, True) if one else (False,)):
            got = _laid(cat, job, hub, h, ents, frames, js, box, limit, terrain, avoid, spans,
                        allow, flip)
            if isinstance(got, str):
                why = why or f"join {hub[0]},{hub[1]} {'NESW'[h]}: {got}"
                continue
            return _one_net(cat, job, *got) if one else got
    return None, f"none of {min(len(cands), tries)} joins routes; first: {why}"


def _one_net(cat, job, laid, text):
    """A net on both lanes (2.0.54, ``route._widen``): its two groups' carriers are the net's own
    (``X``, the lanes their drops fall on), the route on from the join both its lanes (``X|X``)."""
    net = job.net
    on = fkpmod.pair_net(net, net) if job.wide else \
        fkpmod.pair_net(net, "") if job.pin == "L" else fkpmod.pair_net("", net)
    for e in laid:
        lane = cat.has_entity(e.name) and cat.kind(e.name) in fkpmod.LANE_TYPES
        local = e.key.rsplit("/", 1)[1]
        e.attrs["net"] = on if lane and local[:1] not in "lrz" else net
    left, right = job.parts[0].net, job.parts[1].net
    text = text.replace(f"({left} on the left lane, {right} on the right)",
                        "(a group of drivers onto each lane)" if job.wide else
                        f"(onto its {'left' if job.pin == 'L' else 'right'} lane, lane {job.pin})")
    return laid, text.replace(f"{left}:", "left group:").replace(f"{right}:", "right group:")


def _laid(cat, job, hub, h, ents, frames, js, box, limit, terrain, avoid, spans,
          allow=frozenset(), flip=False):
    """The three parts through join ``hub`` heading ``h``: -> (carriers, text) or a reason.
    ``flip``: B's part laid before A's. A part with no driver (a lane pin's empty side, 2.0.54)
    lays one empty belt into the join instead, the stub that makes it a side-load."""
    left, right, pair = job.parts
    lt, rt = _step(hub, (h + 3) % 4), _step(hub, (h + 1) % 4)
    back, ahead = _step(hub, (h + 2) % 4), _step(hub, h)
    a = routemod.NetJob(left.net, starts=list(left.starts), sources=list(left.sources),
                        goals=list(left.goals) + [(lt, (h + 1) % 4)], taps=list(left.taps),
                        owners=list(left.owners))
    b = routemod.NetJob(right.net, starts=list(right.starts), sources=list(right.sources),
                        goals=list(right.goals) + [(rt, (h + 3) % 4)], taps=list(right.taps),
                        owners=list(right.owners))
    both = routemod.NetJob(job.net, starts=[(hub, h)], goals=list(pair.goals),
                           taps=list(pair.taps), owners=list(pair.owners), reserved=[back, ahead])
    sub = {k: v for k, v in js.items() if k != job.net}
    sub.update({a.net: a, b.net: b, both.net: both})
    laid, how, now = [], {}, list(ents)
    order = ((b, "r"), (a, "l"), (both, "")) if flip else ((a, "l"), (b, "r"), (both, ""))
    for part, prefix in order:
        if part is not both and not part.drivers():
            side = lt if part is a else rt
            into = (h + 1) % 4 if part is a else (h + 3) % 4
            stub = fkpmod.Entity(f"~{job.net}/z0", "transport-belt", side[0] + 0.5,
                                 side[1] + 0.5, into * 4, {"net": job.net})
            laid.append(stub)
            now.append(stub)
            how[part.net] = "an empty stub belt"
            continue
        got, text = routemod._route_one(cat, part, now, frames, sub, box, limit, terrain, avoid,
                                        spans, allow)
        if got is None:
            return f"{part.net if part is not both else 'both lanes'}: {text}"
        for e in got:
            local = e.key.rsplit("/", 1)[1]
            e.key = f"~{job.net}/{prefix}{local}"
            if part is both and cat.has_entity(e.name) and \
                    cat.kind(e.name) not in fkpmod.LANE_TYPES:
                e.attrs["net"] = fkpmod.hand_net(job.net)
        laid += got
        now += got
        how[part.net] = text
    text = (f"join {hub[0]},{hub[1]} {'NESW'[h]} ({left.net} on the left lane, {right.net} on the "
            f"right); {left.net}: {how[left.net]}; {right.net}: {how[right.net]}; both: "
            f"{how[job.net]}")
    return laid, text
