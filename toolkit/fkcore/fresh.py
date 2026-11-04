"""fkcore.fresh: connectors that hold stale items are taken up, and the next build lays them fresh.

    python -m fkcore fresh GAME_DIR --area X0,Y0,X1,Y1        (with the game link)

1.1 (Rory, 2026-10-20): a rebuilt factory can leave items its net doesn't carry on belts laid on
the same tiles, and a recipe switch can leave an item in an inserter's hand that its machine no
longer takes; both jam the new machines. A scripted fix is fine when it goes through the game: it
breaks the connectors with stale items and replaces them. ``run`` looks at the area as ``fk look``
does and orders deconstruction of every carrier with a ``other-item`` (an item its net doesn't
carry) and, 1.1.1, of every inserter holding an item the assembling machine it feeds doesn't use
(``link.lua_stuck_hands``). 1.7 (Rory, 2026-10-21: ``fk fresh`` also deals with a one-off crafter
whose output slot is full of the old product): an assembling machine whose output holds what its
recipe no longer makes, and a chest its machines' inserters fill that holds what none of them
makes now (``link.lua_stale_crafters``). Robots carry them and what they hold to storage, nothing
is moved or created by script, and the next ``fk build`` lays them again (they are ``not-placed`` by
then). They are found by name and position, in batches that fit one RCON command. Keeping the
wrong items out in the first place is the game's job (an inserter's filter or condition), not
this one's.
"""
from __future__ import annotations

import json

from . import link, look
from . import surface as surfacemod
from .apply import force_for

__all__ = ["run"]

#: connectors per RCON command (pastes go in 3000-character chunks for the same reason)
BATCH = 60
#: connectors listed in the report
SHOWN = 40


def _n(k, word):
    return f"{k} {word}{'' if k == 1 else 's'}"


def run(r, game, cat, area):
    """Order the stale belts and stuck inserters in ``area`` up; -> the report lines."""
    sf = surfacemod.load_game(game)
    force = getattr(sf, "force", None) or force_for(sf.mode)
    lk = look.run(r, game, cat, area=area, notify="off", tag="fresh")
    stale = [v for v in lk.rep.verdicts if v.wrong_items]
    try:
        hands = json.loads(r.lua(link.lua_stuck_hands(sf.name, area)).strip() or "[]")
    except ValueError:
        hands = []
    hands = hands if isinstance(hands, list) else []
    try:
        left = json.loads(r.lua(link.lua_stale_crafters(sf.name, area)).strip() or "[]")
    except ValueError:
        left = []
    left = left if isinstance(left, list) else []
    a = ",".join(str(int(v)) for v in area)
    if not stale and not hands and not left:
        return [f"fresh {sf.name} area {a}: no belt, inserter, crafter or chest here holds an item "
                "it shouldn't"]
    things = ([(v.name, v.x, v.y) for v in stale] + [(h[0], h[1], h[2]) for h in hands]
              + [(c[0], c[1], c[2]) for c in left])
    ordered = 0
    for i in range(0, len(things), BATCH):
        got = r.lua(link.lua_decon_list(sf.name, force, things[i:i + BATCH])).strip()
        ordered += int(got) if got.isdigit() else 0
    items = sorted({i for v in stale for i in v.wrong_items} | {h[3] for h in hands}
                   | {i for c in left for i in c[4]})
    crafters = sum(1 for c in left if c[3] == "crafter")
    out = [f"fresh {sf.name} area {a}: {ordered} of {len(things)} ordered up "
           f"({_n(len(stale), 'belt')}, {_n(len(hands), 'inserter')}, "
           f"{_n(crafters, 'crafter')}, {_n(len(left) - crafters, 'chest')}; {', '.join(items)}). "
           "Robots carry them and what they hold to storage; the next fk build lays them fresh"]
    out += [f"  {v}" for v in stale[:SHOWN]]
    out += [f"  {h[0]} {h[1]},{h[2]} holds {h[3]}, which its machine doesn't use"
            for h in hands[:SHOWN]]
    out += [(f"  {c[0]} {c[1]},{c[2]} holds {', '.join(c[4])}, which its recipe no longer makes"
             if c[3] == "crafter" else
             f"  {c[0]} {c[1]},{c[2]} holds {', '.join(c[4])}, which none of its machines makes now")
            for c in left[:SHOWN]]
    shown = len(stale[:SHOWN]) + len(hands[:SHOWN]) + len(left[:SHOWN])
    if len(things) > shown:
        out.append(f"  ... and {len(things) - shown} more")
    out += _contested(r, sf.name, force, things)
    return out


def _contested(r, surface, force, things):
    """A line per kind taken up that ghosts elsewhere wait for, more of them than the network
    holds: robots carry the taken-up ones to storage, and those ghosts may get them first (the
    first lead run, 2026-10-21: the shop's two fast inserters went to the mall's ghosts, and the
    shop stood ghost SHORT, unable to make inserters)."""
    taken = {}
    for name, _x, _y in things:
        taken[name] = taken.get(name, 0) + 1
    try:
        got = json.loads(r.lua(link.lua_stock_and_ghosts(surface, force, taken)).strip() or "{}")
    except ValueError:
        return []
    if not isinstance(got, dict):
        return []
    out = []
    for name, k in sorted(taken.items()):
        stock, ghosts = (list(got.get(name) or [0, 0]) + [0, 0])[:2]
        if ghosts > stock:
            out.append(f"  {name}: {k} taken up here, {int(stock)} in the network, {int(ghosts)} "
                       "ghosts of it wait: robots may build those with the ones taken up, and the "
                       "next build's here stay ghost SHORT until more are made")
    return out
