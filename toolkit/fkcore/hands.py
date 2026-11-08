"""fkcore.hands: the short hand of every inserter the toolkit lays, by what the connector carries.

Rory, 2026-10-25: "The default should probably be lowest, and it would go higher if it senses a
bottleneck in thruput model at the inserter."

So a row or the router lays the **lowest** rung a short hand has (``inserter``, 0.86/s at hand 1),
and this step raises one only where the routed model says the connector cannot pass what the design
asks of it: the flow through an inserter with every machine at its nominal rate and **no
capacities** (``fkcore.routed.required``), against ``fkcore.lanes.inserter_rate`` of each rung with
the force's hand sizes and the drop-onto-a-belt derating. The required flows do not depend on the
hand that is laid (they are the design's demand), so the answer is not circular, and it is the same
question ``tiers`` asks of a belt lane and ``too-thin`` already reports for inserters.

Rungs, lowest first: ``inserter`` then ``fast-inserter`` (``--allow`` names others, and a rung the
network cannot build now is skipped: ``fkcore.avail``). ``minimize`` (the default) never puts a hand
below the one that stands in the committed .fkp unless ``downgrade``, so an upgraded base keeps the
fast inserters it has and only new ones come in plain; ``maximize`` uses the top rung everywhere.

What the model does not see, and why the pass is raise-only by default: an inserter picking off a
moving belt is slower and more erratic than one picking from a chest, and a connector that binds on
paper may be buffered in the game; a bulk or stack inserter is modelled (``lanes._BULK``,
``bulk_hand``) but the router does not lay one yet, so the ladder stops at fast. A connector that
binds at the top rung is named here and reported again as ``too-thin``. Pure, stdlib only.
"""
from __future__ import annotations

from . import avail as availmod
from . import fkp as fkpmod
from . import lanes as lanesmod
from . import routed as routedmod

__all__ = ["hands", "assign", "choose"]

#: the rungs of a short hand, lowest first: what a row or the router lays, and what this raises
HANDS = ("inserter", "fast-inserter")
_EPS = 1e-6
_SHOW = 4


def _rate(name, hand, bulk_hand, onto, cat, turn=0.5):
    return lanesmod.inserter_rate(name, hand, bulk_hand, onto_belt=onto, cat=cat, turn=turn)


def _many(n, word):
    return f"{n} {word}" + ("" if n == 1 else "s")


def choose(need, onto, hands=HANDS, hand=1, bulk_hand=1, cat=None, turn=0.5):
    """The lowest rung whose rate carries ``need`` items/s on a connector that drops onto a belt
    (``onto``): -> (name, rate). The top rung when none does (its rate says by how much).
    ``turn``: its swing (3.0: a corner hand's 0.25, ``lanes.inserter_rate``)."""
    fits = []
    for name in hands:
        rate = _rate(name, hand, bulk_hand, onto, cat, turn)
        if rate is None:
            continue
        if rate >= need - _EPS:
            return name, rate
        fits.append((name, rate))
    if not fits:
        return hands[0], None
    return fits[-1]


def assign(lay, flat, cat, prev=None, mode="minimize", allow=HANDS, hand=1, bulk_hand=1,
           downgrade=False, fixed=(), buildable=None, model=None):
    """Every short hand of ``lay`` by what its connector carries: -> (new layout, report lines,
    {key: (old name, new name)}).

    ``prev``: the committed .fkp, whose hand an inserter never goes below unless ``downgrade``.
    ``fixed``: keys the design itself names (a row's ``inserter=fast-inserter``, or every tap when
    the route step was given a concrete hand): left alone. ``buildable(name, n)``: whether robots
    can build ``n`` of a prototype now (``fkcore.avail``); a rung they cannot is skipped, and the
    hands that would have taken it say so. ``model``: ``flat``'s rates.solve, if the caller has it.
    """
    if mode not in ("minimize", "maximize"):
        raise ValueError(f"hands mode is minimize or maximize, got {mode!r}")
    hands = [n for n in allow if n]
    if not hands:
        raise ValueError("hands: --allow names at least one inserter")
    # sized for the inputs arriving: a shortage upstream in the model never shrinks a hand (3.0)
    lg, res = routedmod.required(flat, lay, cat, routedmod.sizing_model(flat, model), hand=hand,
                                 bulk_hand=bulk_hand)
    g = lg.graph
    short = set(hands) | set(availmod.HANDS)
    onto, need = {}, {}
    for key, node in g.nodes.items():
        if node.kind != "inserter":
            continue
        onto[key] = any(g.nodes[b].kind in lanesmod._BELTS
                        for b, via in g.succ.get(key, ()) if via == "drop")
        need[key] = sum(res.required.get((key, ""), {}).values())
    mine = {}
    for key, e in lay.entities.items():
        if key not in need or key in fixed or not cat.has_entity(e.name):
            continue
        if cat.kind(e.name) != "inserter":
            continue
        # a long-handed inserter reaches 2 tiles: it is the router's geometry, not a hand's speed,
        # and renaming it would leave a row nobody can feed
        if key not in fixed and e.name not in short:
            continue
        mine[key] = e
    want, over, rates = {}, [], {}
    for key, e in mine.items():
        turn = lanesmod._turn_of(g.nodes[key])
        rates[key] = {n: _rate(n, hand, bulk_hand, onto[key], cat, turn)
                      for n in set(hands) | {e.name}}
        if mode == "maximize":
            want[key] = hands[-1]
            continue
        got, rate = choose(need[key], onto[key], hands, hand, bulk_hand, cat, turn)
        want[key] = got
        if rate is None or rate < need[key] - _EPS:
            over.append((key, need[key], rate))
    # a rung the design asks for but robots cannot build now: those hands stay one rung down (the
    # run of 2026-10-25 pasted fast inserters LOCKED while 50 plain ones sat in the kit)
    skipped = {}
    if buildable is not None:
        for name in sorted(set(want.values()), key=hands.index):
            keys = [k for k, n in want.items() if n == name]
            if not keys or name == hands[0]:
                continue
            if buildable(name, len(keys)):
                continue
            lower = [h for h in hands[:hands.index(name)]
                     if buildable(h, len(keys))] or [hands[0]]
            for k in keys:
                want[k] = lower[-1]
            skipped.setdefault((name, lower[-1]), set()).update(keys)
    # never below what stands (the committed .fkp), unless downgrade
    kept = 0
    if prev is not None and not downgrade:
        for key in list(want):
            was = lay.entities[key].name
            old = prev.entities.get(key)
            old = old.name if old is not None else None
            cur = old if old in rates[key] else (was if was in rates[key] else None)
            if cur is not None and rates[key][cur] is not None and rates[key][want[key]] is not None \
                    and rates[key][cur] > rates[key][want[key]] + _EPS:
                want[key] = cur
                kept += 1
    new = lay.copy()
    changed, count = {}, {}
    for key, name in sorted(want.items()):
        count[name] = count.get(name, 0) + 1
        was = lay.entities[key].name
        if name != was:
            changed[key] = (was, name)
            e = lay.entities[key]
            new.entities[key] = fkpmod.Entity(e.key, name, e.x, e.y, e.dir, dict(e.attrs))
    head = (f"hand {mode} (allowed: {', '.join(hands)}): {len(want)} short hands on nets; "
            + ", ".join(f"{count[n]} {n}" for n in hands if n in count)
            + f"; {len(changed)} changed"
            + (f", {kept} kept from the committed .fkp" if kept else ""))
    lines = [head]
    for (name, lower), keys in sorted(skipped.items()):
        speed = rates[next(iter(keys))].get(name)
        lines.append(f"  {name} is not buildable now ({len(keys)} hands would take it): they stay "
                     f"{lower}" + (f" ({speed:.2f}/s a hand vs {rates[next(iter(keys))][lower]:.2f})"
                                   if speed and rates[next(iter(keys))].get(lower) else "")
                     + "; too-thin says what that costs")
    if over:
        top = max(n for _k, n, _r in over)
        lines.append(f"  {_many(len(over), 'connector')} need more than {hands[-1]} carries (up to "
                     f"{top:.2f}/s a hand): "
                     + ", ".join(k for k, _n, _r in sorted(over, key=lambda o: -o[1])[:_SHOW])
                     + (f" and {len(over) - _SHOW} more" if len(over) > _SHOW else ""))
    raised = sorted(k for k, (was, name) in changed.items()
                    if was in hands and hands.index(name) > hands.index(was))
    if raised:
        lines.append(f"  raised for the flow: "
                     + ", ".join(f"{k} ({lay.entities[k].owner or k})" for k in raised[:_SHOW])
                     + (f" and {len(raised) - _SHOW} more" if len(raised) > _SHOW else ""))
    return new, lines, changed
