"""fkcore.pairs: two nets that share a belt, laid as one pair (2.0, CORE-V2.md "The router in 2.0").

``lanes A | B`` (Rory, 2026-10-21: a net is one lane, so two nets can share a belt) puts A on the
left lane and B on the right of every belt they are on. ``fkcore.expand`` binds every consumer on
either net to the pair (``A|B``: one input belt of both lanes), so the router's job is to bring the
two nets together once and lay them on together from there. It lays the pair as one job, ``A|B``
(``merge``, from ``route.jobs``), as a lane goal (PLAN-LANES, 2.1.35): A on the left lane and B
on the right at every consumer, judged by the lane graph (``fkcore.lanes`` over
``fkcore.lanephys``'s rules), not by a construct.

- **the trunk** (``both``): one route from a root tile H, heading h, to every consumer of the
  pair, both lanes. Every carrier of it is ``net=A|B``; a tap inserter ``net=A+B`` (it takes from
  both lanes what its machine accepts).
- **A and B**: each net from its drivers (a tree, merges where it has several) to the side of a
  belt of the trunk's stem (its plain belts from H to its first tap or split), A from the left,
  B from the right: a side-load puts a net onto the stem's near lane. Either both side-load H
  (laid first, then the trunk, which ``NetJob.lanes`` keeps to carriers that hold A|B there),
  or the trunk first and each part at the stem belt where the lane graph says the lanes come out
  right; when nothing side-loads H, H is an empty belt (``~A|B/z0``) that makes the first
  side-load one. The cheapest layout the lane graph accepts is laid.

So each net is one lane: at most 7.5/s on yellow, and the routed rates' ``too-thin`` says so where
a net brings more (a two-sided MinerRow's ore side-loaded onto one lane). Every carrier is keyed
under the pair (``~A|B/b3``; A's own ``~A|B/lb3``, B's ``~A|B/rb3``), so a rebuild keeps or lays
again the three parts together (``route --only stale``: one signature). The roots are ranked by
the walking distance from A's drivers to H's left, from B's to its right and from the tile ahead
of H to every consumer (``joins``). A pair
whose drivers are all single machines and whose consumers are all single machines is laid as
direct inserters where each fits, with no belt and so no lanes at all. Pure, stdlib only.
"""
from __future__ import annotations


from . import fkp as fkpmod
from . import route as routemod

__all__ = ["merge", "route", "joins"]

_TRIES = 6           # trunk roots tried at most (all of them for a wide net or a pin)
_JOINS = 12          # root candidates ranked


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
            job.drops.update(part.drops)
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
    """Lay a pair's job (``merge``) by the lane goal: -> (carriers, report text) or (None, the
    reason). PLAN-LANES phase 5 retired the join construct (a join tile, a stub, no_bend)."""
    return route_lanes(cat, job, ents, frames, js, box, limit, terrain, avoid, spans, allow)


# -- the pair as a lane goal (PLAN-LANES phases 3 to 5) -----------------------------------------
#: Rory, 2026-11-08: "a more general system that relies entirely on the belt model instead of
#: leaning on rules like no bend and constructs like a two belt sideload merge tile". The pair's
#: shared belt (the trunk) is routed first from a root to every consumer; A's route then ends
#: side-loading the trunk from its left side and B's from its right, at any belt of its stem (the
#: run from the root to its first tap or split), the points ranked by walking distance. Whether a
#: layout holds A on the left lane and B on the right at every consumer is the lane graph's call
#: (``fkcore.lanes``, by ``fkcore.lanephys``'s rules): no straight-on rule. The old join's layout
#: is one of the candidates (both at the root, ``_laid_sides_first``), and an empty root belt
#: makes a side-load of the next stem belt (named ``z0``, so a rebuild keeps it).
_SIDES = 4           # side-load points tried per net
_BUDGET = 48         # part routes tried per pair, over every trunk root: the cheapest valid wins


def _stem(g, root_key, mine):
    """[(tile, heading 0-3)] the trunk's plain belts from its root, in flow order, up to its first
    tap, split or end; a belt the trunk turns onto (a curve) is skipped as a side-load target (a
    second side feed would make it side-load the trunk itself)."""
    out, key, seen = [], root_key, set()
    while key is not None and key in mine and key not in seen:
        seen.add(key)
        n = g.nodes[key]
        if n.kind == "splitter" or any(via == "pickup" for _b, via in g.succ.get(key, ())):
            break
        curve = any(via == "curve" for _a, via in g.pred.get(key, ()))
        if n.kind == "transport-belt" and not curve:
            out.append(((int(n.x // 1), int(n.y // 1)), n.dir // 4))
        nxt = [b for b, via in g.succ.get(key, ()) if via in ("belt", "curve", "ug") and b in mine]
        key = nxt[0] if len(nxt) == 1 else None
    return out


def _clip(ents, laid, cat, margin=4):
    """The entities near ``laid`` (the lane graph of the pair's neighbourhood)."""
    xs = [e.x for e in laid]
    ys = [e.y for e in laid]
    x0, x1, y0, y1 = min(xs) - margin, max(xs) + margin, min(ys) - margin, max(ys) + margin
    return [e for e in ents if x0 - 6 <= e.x <= x1 + 6 and y0 - 6 <= e.y <= y1 + 6]


def _lanes_hold(cat, ents, laid_a, laid_b, trunk, part_a, part_b, need, goals=()):
    """Does the lane graph put A's items on the left lane and B's on the right at every place the
    trunk hands to a consumer, losing none? -> (ok, reason)."""
    from . import lanes as lanesmod
    near = _clip(ents, trunk + laid_a + laid_b, cat)
    lg = lanesmod.build(near, cat)
    g = lg.graph
    seeds = []
    for label, laid, part in (("l", laid_a, part_a), ("r", laid_b, part_b)):
        for e in laid:
            if cat.has_entity(e.name) and cat.kind(e.name) == "inserter":
                seeds.append(((e.key, ""), label))
        for t, _h in part.starts:           # a belt end the part starts from: both its lanes
            key = g.at.get((int(t[0]), int(t[1])))
            if key is not None:
                seeds += [((key, "L"), label), ((key, "R"), label)]
    got = lanesmod.reach(lg, seeds)
    for node, _ug in lg.closed:
        if got.get(node, set()) & {"l", "r"}:
            return False, "a side-load into an underground drops a lane"
    mine = {e.key for e in trunk}
    ends = []
    for e in trunk:
        if cat.has_entity(e.name) and cat.kind(e.name) == "inserter":
            ends += [ed.src for ed in lg.inn.get((e.key, ""), ()) if ed.via == "pickup"
                     and ed.src[0] in mine]
    for t, _h in goals:
        key = g.at.get((int(t[0]), int(t[1])))
        if key in mine:
            ends += [(key, "L"), (key, "R")]
    if not ends:
        return False, "no consumer picks from the trunk"
    for node in set(ends):
        has = got.get(node, set()) & {"l", "r"}
        want = need["L"] if node[1] == "L" else need["R"]
        if has != want:
            return False, (f"{node[0]}'s {'left' if node[1] == 'L' else 'right'} lane holds "
                           f"{sorted(has) or 'nothing'}, not {sorted(want) or 'nothing'}")
    return True, ""


def _sides(cat, grid, stem, part, left_side, taken):
    """[(score, i, side tile, heading into the stem)] where ``part`` may side-load the stem: the
    tile beside stem belt i on its left (A) or right (B), free, ranked by the summed walking
    distance from the part's drivers."""
    seeds = [s for s in _seeds(grid, part, "drivers") if s]
    walks = [routemod._walk(grid, s) for s in seeds]
    out = []
    for i, (t, h) in enumerate(stem):
        side = (h + 3) % 4 if left_side else (h + 1) % 4
        s = _step(t, side)
        if grid.blocked(s) or s in taken:
            continue
        if any(s not in w for w in walks):
            continue
        out.append((sum(w[s] for w in walks), i, s, (side + 2) % 4))
    out.sort()
    return out[:_SIDES]


def route_lanes(cat, job, ents, frames, js, box, limit, terrain=(), avoid=(), spans=None,
                allow=frozenset()):
    """Lay a pair's job by the lane goal: -> (carriers, report text) or (None, the reason)."""
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
        return None, (f"no free tile roots the trunk of {left.net} and {right.net}, reached from "
                      "both nets' drivers and every consumer")
    need = {"L": {"l"} if left.drivers() else set(), "R": {"r"} if right.drivers() else set()}
    one = job.wide or job.pin
    why, best, budget = None, None, [_BUDGET]
    for hub, h in cands[:len(cands) if one else _TRIES]:
        if budget[0] <= 0:
            break
        for lay in (_laid_lanes, _laid_sides_first):
            got = lay(cat, job, hub, h, ents, frames, js, box, limit, terrain, avoid, spans,
                      allow, need, budget)
            if isinstance(got, str):
                why = why or f"trunk {hub[0]},{hub[1]} {'NESW'[h]}: {got}"
                continue
            if best is None or _cost(cat, got[0]) < _cost(cat, best[0]):
                best = got
    if best is not None:
        laid, text = best[0], best[1]
        empty = best[2] if len(best) > 2 else None
        for e in laid:
            if e.key == empty:
                e.key = f"~{net}/z0"
                e.attrs["net"] = net
        return _one_net(cat, job, laid, text) if one else (laid, text)
    return None, f"none of {min(len(cands), _TRIES)} trunk roots routes; first: {why}"


def _cost(cat, laid):
    """A layout's price, as the search prices it: belts, undergrounds by the pair, splitters."""
    from . import steiner
    k = steiner.COSTS
    n = {"transport-belt": 0, "underground-belt": 0, "splitter": 0}
    for e in laid:
        if e.name in n:
            n[e.name] += 1
    return (n["transport-belt"] * k["belt"] + n["underground-belt"] / 2 * k["ug"]
            + n["splitter"] * k["split"], len(laid))


def _laid_lanes(cat, job, hub, h, ents, frames, js, box, limit, terrain, avoid, spans, allow,
                need, budget):
    """The trunk from ``hub`` heading ``h``, then A and B side-loading its stem: the cheapest
    layout the lane graph accepts -> (carriers, text) or a reason. ``budget`` [part routes left]
    is shared over the pair's roots."""
    left, right, pair = job.parts
    back = _step(hub, (h + 2) % 4)
    trunk_job = routemod.NetJob(job.net, starts=[(hub, h)], goals=list(pair.goals),
                                taps=list(pair.taps), owners=list(pair.owners), reserved=[back])
    sub = {k: v for k, v in js.items() if k != job.net}
    sub[job.net] = trunk_job
    trunk, ttext = routemod._route_one(cat, trunk_job, list(ents), frames, sub, box, limit,
                                       terrain, avoid, spans, allow)
    if trunk is None:
        return f"the trunk: {ttext}"
    for e in trunk:
        e.key = f"~{job.net}/{e.key.rsplit('/', 1)[1]}"
        if cat.has_entity(e.name) and cat.kind(e.name) not in fkpmod.LANE_TYPES:
            e.attrs["net"] = fkpmod.hand_net(job.net)
    now = list(ents) + trunk
    from . import connect as connectmod
    g = connectmod.graph(_clip(now, trunk, cat), cat)
    root = g.at.get(hub)
    stem = _stem(g, root, {e.key for e in trunk})
    if not stem:
        return "the trunk has no plain belt before its first consumer"
    gb = routemod._ground(cat, now, frames, js, job.net, terrain) | (set(avoid) - set(job.ends()))
    sgrid = routemod._Bounded(gb, box)
    parts = [(left, "l", True), (right, "r", False)]
    why, best = None, None
    for first in ((0, 1), (1, 0)) if (job.wide or job.pin) else ((0, 1),):
        p1, p2 = parts[first[0]], parts[first[1]]
        for c1 in _sides(cat, sgrid, stem, p1[0], p1[2], set()) if p1[0].drivers() else [None]:
            if budget[0] <= 0:
                break
            budget[0] -= 1
            got1 = _side_part(cat, job, p1, c1, now, frames, sub, box, limit, terrain, avoid,
                              spans, allow)
            if isinstance(got1, str):
                why = why or got1
                continue
            now1 = now + got1[0]
            g1 = routemod._Bounded(gb | {t for e in got1[0] for t in fkpmod.tiles_of(cat, e)},
                                   box)
            taken = {c1[2]} if c1 else set()
            for c2 in _sides(cat, g1, stem, p2[0], p2[2], taken) if p2[0].drivers() else [None]:
                if budget[0] <= 0:
                    break
                budget[0] -= 1
                got2 = _side_part(cat, job, p2, c2, now1, frames, sub, box, limit, terrain,
                                  avoid, spans, allow)
                if isinstance(got2, str):
                    why = why or got2
                    continue
                laid_a, laid_b = (got1[0], got2[0]) if p1[1] == "l" else (got2[0], got1[0])
                ok, bad = _lanes_hold(cat, now1 + got2[0], laid_a, laid_b, trunk, left, right,
                                      need, pair.goals)
                if not ok:
                    why = why or f"lanes: {bad}"
                    continue
                ta, tb = (got1[1], got2[1]) if p1[1] == "l" else (got2[1], got1[1])
                ia, ib = (c1, c2) if p1[1] == "l" else (c2, c1)
                where = "; ".join(f"{p.net} side-loads stem belt {c[1]} at {c[2][0]},{c[2][1]}"
                                  for p, c in ((left, ia), (right, ib)) if c)
                text = (f"trunk {hub[0]},{hub[1]} {'NESW'[h]} ({left.net} on the left lane, "
                        f"{right.net} on the right), {where}; {left.net}: {ta}; "
                        f"{right.net}: {tb}; both: {ttext}")
                laid = trunk + laid_a + laid_b
                # nothing side-loads stem belt 0: the root is an empty belt, what makes the
                # first side-load one; named a stub so a rebuild keeps it (``route._is_stub``)
                empty = root if all(c is None or c[1] != 0 for c in (ia, ib)) else None
                if best is None or _cost(cat, laid) < _cost(cat, best[0]):
                    best = (laid, text, empty)
    if best is not None:
        return best
    return why or f"no side-load point on the stem of {len(stem)} belts routes"


def _laid_sides_first(cat, job, hub, h, ents, frames, js, box, limit, terrain, avoid, spans,
                      allow, need, budget):
    """The other order: A and B first, side-loading the root's two flanks, then the trunk from
    the root, the root's carriers by what lanephys says its feeds allow (``NetJob.lanes``); the
    lane graph judges it as any layout. -> (carriers, text) or a reason."""
    left, right, pair = job.parts
    if not (left.drivers() and right.drivers()) or budget[0] <= 0:
        return "both sides first needs drivers on both"   # a lone side feed turns
    budget[0] -= 1
    lt, rt = _step(hub, (h + 3) % 4), _step(hub, (h + 1) % 4)
    sub = {k: v for k, v in js.items() if k != job.net}
    now, laid, texts = list(ents), {}, {}
    for p, prefix, s, into in ((left, "l", lt, (h + 1) % 4), (right, "r", rt, (h + 3) % 4)):
        got = _side_part(cat, job, (p, prefix, prefix == "l"), (0, 0, s, into), now, frames,
                         sub, box, limit, terrain, avoid, spans, allow)
        if isinstance(got, str):
            return got
        laid[prefix], texts[prefix] = got
        now += got[0]
    feeds = [((h + 1) % 4, {"L": {"l"}, "R": {"l"}}), ((h + 3) % 4, {"L": {"r"}, "R": {"r"}})]
    back = _step(hub, (h + 2) % 4)
    trunk_job = routemod.NetJob(job.net, starts=[(hub, h)], goals=list(pair.goals),
                                taps=list(pair.taps), owners=list(pair.owners), reserved=[back],
                                lanes={hub: (need, feeds)})
    sub[job.net] = trunk_job
    trunk, ttext = routemod._route_one(cat, trunk_job, now, frames, sub, box, limit, terrain,
                                       avoid, spans, allow)
    if trunk is None:
        return f"the trunk after both sides: {ttext}"
    for e in trunk:
        e.key = f"~{job.net}/{e.key.rsplit('/', 1)[1]}"
        if cat.has_entity(e.name) and cat.kind(e.name) not in fkpmod.LANE_TYPES:
            e.attrs["net"] = fkpmod.hand_net(job.net)
    ok, bad = _lanes_hold(cat, now + trunk, laid["l"], laid["r"], trunk, left, right, need,
                          pair.goals)
    if not ok:
        return f"lanes: {bad}"
    text = (f"trunk {hub[0]},{hub[1]} {'NESW'[h]} ({left.net} on the left lane, {right.net} on "
            f"the right), both side-load stem belt 0, laid first; {left.net}: {texts['l']}; "
            f"{right.net}: {texts['r']}; both: {ttext}")
    return trunk + laid["l"] + laid["r"], text


def _side_part(cat, job, part, cand, now, frames, sub, box, limit, terrain, avoid, spans, allow):
    """One part routed to its side-load point ``cand``: -> (carriers, text) or a reason."""
    p, prefix, _left = part
    if cand is None:
        return [], "no drivers (nothing side-loads this side)"
    _score, _i, s, into = cand
    pj = routemod.NetJob(p.net, starts=list(p.starts), sources=list(p.sources),
                         drops=dict(p.drops), goals=list(p.goals) + [(s, into)],
                         taps=list(p.taps), owners=list(p.owners))
    sub2 = dict(sub)
    sub2[p.net] = pj
    joint = frozenset(allow) | {(s, _step(s, into))}     # it points into the trunk on purpose
    got, text = routemod._route_one(cat, pj, list(now), frames, sub2, box, limit, terrain, avoid,
                                    spans, joint)
    if got is None:
        return f"{p.net}: {text}"
    for e in got:
        e.key = f"~{job.net}/{prefix}{e.key.rsplit('/', 1)[1]}"
    return got, text


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
    for name, group in ((left, "the left group"), (right, "the right group")):
        text = text.replace(f"{name} side-loads", f"{group} side-loads")
    return laid, text.replace(f"{left}:", "left group:").replace(f"{right}:", "right group:")
