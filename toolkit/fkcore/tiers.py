"""fkcore.tiers: a belt tier per connector (CORE-V2.md "Tier per connector", Rory).

The tiers come from what the design asks of every lane: the flow solver's required flows with the
machines at V1's nominal rates and no capacities (``fkcore.routed.required``), so a lane that binds
today still shows what it would carry. Every belt, underground pair and splitter on a net gets:

- ``minimize``: the slowest allowed tier whose lane carries its required flow (the fastest allowed
  where none does: ``too-thin`` reports it after);
- ``maximize``: the fastest allowed tier, on every one that carries anything;
- ``yellow``: only yellow, what the router lays.

``minimize`` and ``maximize`` never put an entity below the tier it has (its name in the layout
passed in) unless ``downgrade``, so a rebuild with slightly different flows keeps what is built.
``yellow`` is asked for and sets yellow wherever the tunnel allows.

Budgets (``{"red": 10}``): at most that many entities gain a tier. Where a mode wants more, the ones
with the highest required ratio gain it and the rest take the next allowed tier down. An entity
that already has the tier keeps it, over the budget if need be (a note says so). The flow peaks
between the last driver and the first consumer, so on a line of drills into furnaces the red belts
land in the middle (Rory's test case).

An underground pair changes tier as one and counts as two. A tier reaches its underground's
``max_distance`` (5, 7, 9, 11 tiles), and a pair never gets one that falls short of its tunnel.
After that the pairing is checked as the game does it (``connect.graph``, old names against new):
where a change would give an underground another partner or none (a yellow pair turned red with a
red entrance between its ends), both ends of that pair, and of any pair it would now involve, go
back to their old names, until the pairing matches. A Literal's entities are pinned and keep
theirs, and so does an underground whose partner is pinned; entities on no net (a cover's, a
claim's) are not touched. Pure.
"""
from __future__ import annotations

from dataclasses import replace

from . import fkp as fkpmod

__all__ = ["TIERS", "FAMILY", "assign", "choose", "tier_of"]

TIERS = ("yellow", "red", "blue", "turbo")
FAMILY = {
    "transport-belt": ("transport-belt", "fast-transport-belt", "express-transport-belt",
                       "turbo-transport-belt"),
    "underground-belt": ("underground-belt", "fast-underground-belt", "express-underground-belt",
                         "turbo-underground-belt"),
    "splitter": ("splitter", "fast-splitter", "express-splitter", "turbo-splitter"),
}
_INDEX = {name: (kind, i) for kind, names in FAMILY.items() for i, name in enumerate(names)}
_EPS = 1e-6


def tier_of(name):
    """(family, tier index) of a belt-type prototype, or None."""
    return _INDEX.get(name)


def _rate(cat, tier):
    return float(cat.entity(FAMILY["transport-belt"][tier])["belt_speed"]) * 60 * 4


def _reach(cat, tier):
    """Tiles from an underground entrance to the farthest exit it pairs with."""
    return int(cat.entity(FAMILY["underground-belt"][tier]).get("max_distance") or 5)


def _many(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def _keys(keys, show=4):
    keys = sorted(keys, key=fkpmod.natural_key)
    return ", ".join(keys[:show]) + (f" and {len(keys) - show} more" if len(keys) > show else "")


def choose(lg, required, cat, entities, mode="minimize", allow=TIERS, budgets=None, fixed=(),
           required_edge=None, downgrade=False, every=False):
    """The tier of every belt-type entity of ``entities`` ({key: entity}) on a net:
    -> ({key: tier index}, notes). ``required``: the solver's required flows per node
    ({(key, lane): {item: items/s}}); ``required_edge`` per edge, which gives a splitter what one
    of its halves carries (else half its lane's); ``fixed``: keys that keep their tier;
    ``downgrade``: minimize and maximize may go below the tier an entity has; ``every`` (2.1.31,
    ``tier maximize --all``): maximize takes the top tier on belts the model sees nothing on too
    (a design whose feeds have no rate on paper: Literal belts, a lab's fed ports, beacons)."""
    if mode not in ("minimize", "maximize", "yellow"):
        raise ValueError(f"tier mode is minimize, maximize or yellow, got {mode!r}")
    allowed = sorted({TIERS.index(t) for t in allow} | {0})
    rates = {t: _rate(cat, t) for t in range(len(TIERS))}
    reach = {t: _reach(cat, t) for t in range(len(TIERS))}
    pair = dict(lg.graph.pairs)
    on = {k for k, e in entities.items()
          if tier_of(e.name) is not None and k not in fixed and e.attrs.get("net")}
    need, units, weight = {}, {}, {}
    for key, e in entities.items():
        info = tier_of(e.name)
        if key not in on or (key in pair and pair[key] not in on):
            continue                                # pinned, or its partner is
        if info[0] == "splitter" and required_edge is not None:
            lanes = [sum(required_edge.get((e.src, e.dst), {}).values())
                     for lane in ("L", "R") for e in lg.inn.get((key, lane), []) +
                     lg.out.get((key, lane), []) if e.cap != float("inf")] or [0.0]
        else:
            halves = 2 if info[0] == "splitter" else 1
            lanes = [sum(required.get((key, lane), {}).values()) / halves for lane in ("L", "R")]
        need[key] = max(lanes)
    cur, low = {}, {}                               # the tier a unit has; the lowest it may get
    for key in need:
        unit = tuple(sorted({key, pair[key]})) if key in pair else (key,)
        units[unit] = max(need[k] for k in unit)
        weight[unit] = len(unit)
        cur[unit] = max(tier_of(entities[k].name)[1] for k in unit)

    def fit(unit, t):                               # the lowest it may have from t up
        t = max(t, low[unit])
        return min([x for x in set(allowed) | {cur[unit]} if x >= t] or [t])
    want, notes = {}, []
    over, long_ = [], []
    for unit, n in units.items():
        if mode == "yellow":
            t = 0
        elif mode == "maximize":
            t = allowed[-1] if n > _EPS or every else 0
        else:
            fits = [x for x in allowed if rates[x] >= n - _EPS]
            t = fits[0] if fits else allowed[-1]
            if not fits:
                over.append(unit)
        keep = cur[unit] if mode != "yellow" and not downgrade else 0
        tunnel = 0
        if len(unit) == 2:
            a, b = (entities[k] for k in unit)
            tunnel = abs(float(a.x) - float(b.x)) + abs(float(a.y) - float(b.y))
        reaches = min([x for x in reach if reach[x] >= tunnel - _EPS] or [cur[unit]])
        low[unit] = max(keep, reaches)
        want[unit] = fit(unit, t)
        if reaches > max(t, keep):
            long_.append((unit, tunnel))
    if over:
        top = max(units[u] for u in over)
        notes.append(f"{sum(weight[u] for u in over)} entities need more than "
                     f"{TIERS[allowed[-1]]} carries (up to {top:.2f}/s a lane): "
                     + ", ".join(k for u in sorted(over, key=lambda u: -units[u])[:4] for k in u))
    if long_:
        notes.append(f"{_many(len(long_), 'underground pair')} at the slowest tier that reaches "
                     "its tunnel: " + ", ".join(f"{u[0]} ({d:g} tiles, {TIERS[want[u]]})"
                                            for u, d in long_[:4])
                     + (f" and {len(long_) - 4} more" if len(long_) > 4 else ""))
    budgets = {TIERS.index(t): n for t, n in (budgets or {}).items()}
    for t in sorted((t for t in budgets if t), reverse=True):     # nothing is below yellow
        name, cap = TIERS[t], budgets[t]
        mine = sorted((u for u in units if want[u] == t), key=lambda u: (-units[u], u))
        had = sum(weight[u] for u in mine if cur[u] >= t)   # never counted against the budget
        used, rest = had, set()
        lower = max([x for x in allowed if x < t] or [0])
        for unit in mine:
            if cur[unit] >= t:
                continue
            if used + weight[unit] <= cap:
                used += weight[unit]
            else:
                want[unit] = fit(unit, lower)
                rest.add(want[unit])
        if had > cap:
            notes.append(f"{had} entities already {name} keep it: the {name} budget ({cap}) is "
                         f"over by {had - cap}")
        if rest:
            notes.append(f"the {name} budget ({cap}) takes {used} of the "
                         f"{sum(weight[u] for u in mine)} entities that want {name}: the rest "
                         f"are {', '.join(TIERS[x] for x in sorted(rest))}")
    out = {}
    for unit, t in want.items():
        for key in unit:
            out[key] = t
    back = _keep_pairing(entities, out, cat)
    if back:
        notes.append(f"reverted {_many(len(back), 'underground pair')} whose pairing a tier "
                     f"change would alter: {_keys({k for p in back for k in p})}")
    return out, notes


def _keep_pairing(entities, out, cat):
    """Pair the undergrounds as the game does, with the old names and with ``out``'s, and put
    back the old tier of every pair whose pairing changed, until the two match (``out`` is
    changed in place). -> the pairs put back, as sets of keys. Only undergrounds pair, so only
    they go into the graph."""
    from . import connect
    ugs = {k: e for k, e in entities.items()
           if (tier_of(e.name) or (None,))[0] == "underground-belt"}
    had = {k: tier_of(e.name)[1] for k, e in ugs.items()}
    changed = {k for k in ugs if out.get(k, had[k]) != had[k]}
    if not changed:
        return []
    old = connect.graph(list(ugs.values()), cat).pairs
    back = set()
    while changed:
        new = connect.graph([replace(e, name=FAMILY["underground-belt"][out.get(k, had[k])])
                             for k, e in ugs.items()], cat).pairs
        bad = {k for k in set(old) | set(new) if old.get(k) != new.get(k)}
        if not bad:
            break
        undo = {x for k in bad for x in (k, old.get(k), new.get(k)) if x in changed}
        if not undo:                                # a change elsewhere on the line opened a path
            lines = {_line(ugs[k]) for k in bad}
            undo = {k for k in changed if _line(ugs[k]) in lines} or set(changed)
        for k in undo:
            out[k] = had[k]
        changed -= undo
        back |= undo
    return sorted({frozenset((k, old.get(k, k))) for k in back},
                  key=lambda p: fkpmod.natural_key(min(p, key=fkpmod.natural_key)))


def _line(e):
    """The row or column an underground looks along."""
    d = int(e.dir or 0) % 8
    return (d, float(e.y) if d == 4 else float(e.x))


def assign(flat, lay, cat, mode="minimize", allow=TIERS, budgets=None, hand=1, bulk_hand=1,
           downgrade=False, model=None, every=False):
    """-> (new layout, report lines, {key: (old name, new name)}). A Literal's entities are
    ``fixed``. ``model``: ``flat``'s rates.solve, if the caller has it."""
    from . import routed
    lg, res = routed.required(flat, lay, cat, model, hand=hand, bulk_hand=bulk_hand)
    fixed = {k for k, e in lay.entities.items()
             if e.owner in flat.insts and flat.insts[e.owner].kind == "Literal"}
    tiers, notes = choose(lg, res.required, cat, lay.entities, mode, allow, budgets, fixed,
                          res.required_edge, downgrade, every)
    new = lay.copy()
    changed, count = {}, {}
    for key, t in sorted(tiers.items()):
        e = lay.entities[key]
        family, _now = tier_of(e.name)
        name = FAMILY[family][t]
        count[TIERS[t]] = count.get(TIERS[t], 0) + 1
        if name != e.name:
            changed[key] = (e.name, name)
            new.entities[key] = fkpmod.Entity(e.key, name, e.x, e.y, e.dir, dict(e.attrs))
    head = (f"tier {mode} (allowed: {', '.join(t for t in TIERS if t in allow or t == 'yellow')}"
            + (f"; budgets {', '.join(f'{t} {n}' for t, n in budgets.items())}" if budgets else "")
            + ("; downgrade" if downgrade else "")
            + f"): {len(tiers)} belts, undergrounds and splitters on nets; "
            + ", ".join(f"{count[t]} {t}" for t in TIERS if t in count)
            + f"; {len(changed)} changed")
    lines = [head] + [f"  {n}" for n in notes]
    by_net = {}
    for key, (_old, name) in changed.items():
        net = lay.entities[key].attrs.get("net")
        by_net.setdefault(net, []).append((key, name))
    for net, rows in sorted(by_net.items()):
        tiers_ = sorted({tier_of(n)[1] for _k, n in rows})
        lines.append(f"  {net}: {len(rows)} to {', '.join(TIERS[t] for t in tiers_)} ("
                     + ", ".join(k for k, _n in sorted(rows, key=lambda r: fkpmod.natural_key(r[0]))[:4])
                     + (f" and {len(rows) - 4} more" if len(rows) > 4 else "") + ")")
    return new, lines, changed
