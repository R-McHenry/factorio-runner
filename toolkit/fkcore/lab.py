"""fkcore.lab: `fk lab`, the lab alias (3.1; the lab's own machinery since 2.0.3 is gone).

    python -m fkcore lab GAME_DIR NAME [--from DIR] [--size WxH] [--keep] [--with 'STEP OPTS']
    python -m fkcore lab GAME_DIR NAME --measure SECONDS
    python -m fkcore lab GAME_DIR NAME --clear
    python -m fkcore lab GAME_DIR --list
    python -m fkcore lab GAME_DIR NAME --ore RESOURCE --area A | --copy-ore A [--to X,Y] | --clear-ore

Rory (11-08): the lab is just another game surface where things are free. It is a surface file
like any base's (``surface lab``, ``free``, ``force lab``; ``fkcore.surface``), and a module is
built on it the way a module is built alone on any surface (``fkcore.alone``). What is left here
is the alias the lab scripts call (``lab-session\\labq.sh``, ``region.py``):

- ``fk lab NAME [--size] [--keep] [--with]`` is ``fk alone NAME --surface lab``, and then, unless
  ``--keep`` (what stands has them already), the stand-ins the lab always gave (``fkcore.standin``,
  god-mode conveniences: ``supply`` for power, the requesters' items and burners' coal, ``feed`` for
  a source or sink at every open port). First it takes the lab's edge off (2.0.12).
- ``--measure SECONDS`` tops the requesters up (``supply``) and measures (``alone.measure_alone``).
- ``--clear``, ``--list`` and the ore flags forward to ``alone`` and ``standin.ore`` on the lab.

Rory watches the lab with ``/lab``. FAIR-PLAY.md: on the lab none of the game's rules apply.
"""
from __future__ import annotations

import json

from . import alone, standin

__all__ = ["SURFACE", "FORCE", "LabError", "build", "measure", "clear", "slots", "ore"]

SURFACE, FORCE = alone.LAB, "lab"
LabError = alone.AloneError


def slots(game):
    """The slots in use on the lab."""
    return alone.slots(game, SURFACE)


def clear(r, game, name):
    return alone.clear(r, game, name, SURFACE)


def build(r, game, name, src=None, size=None, cat=None, extra=None, keep=False):
    """NAME alone on its lab slot, then the lab's stand-ins (unless ``keep``). -> report lines."""
    edge = r.lua(standin.lua_unbounded(SURFACE)).strip()
    got = alone.build(r, game, name, src, size, cat, extra=extra, keep=keep, surface=SURFACE)
    lines = got.lines
    if edge.startswith("was"):
        lines.insert(1, f"  the lab has no edge now (it {edge})")
    if got.code != 0:
        return lines
    if keep:
        lines.append("  kept: no power, stock or fuel added (what stands has them)")
        return lines
    x, y, w, h = got.plot
    fed = standin.feed(r, got.ctx, name, SURFACE, got.force, x, y)
    line, _got = standin.supply(r, SURFACE, got.force, alone.box(*got.slot))
    lines.append(line)
    lines += fed
    lines.append(f"  next: let it run a minute or two, then `fk lab {name} --measure 60`; Rory sees "
                 f"it with /lab at {x + w // 2},{y + h // 2}")
    return lines


def measure(r, game, name, seconds, cat=None):
    """What NAME's machines make on its lab slot over ``seconds``, the requesters topped up first."""
    held = slots(game)
    if name not in held:
        raise LabError(f"{name}: not built in the lab yet (fk lab {name})")
    sf = alone.surface_of(game, name, SURFACE)
    top = json.loads(r.lua(standin.lua_supply(SURFACE, sf.force_name, alone.box(*held[name])))
                     .strip() or "{}")
    stocked = top.get("stocked") if isinstance(top.get("stocked"), dict) else {}
    lines = alone.measure_alone(r, game, name, seconds, cat, SURFACE)
    if stocked:
        lines[0] = lines[0].rstrip(":") + ", requesters topped up first: " + ", ".join(
            f"{n} {k}" for n, k in sorted(stocked.items())) + ":"
    return lines


def ore(r, game, name, resource=None, area=None, amount=100000, copy=None, to=(0, 0),
        clear_ore=False):
    return standin.ore(r, game, name, SURFACE, resource, area, amount, copy, to, clear_ore)


def workspace(game, name):
    return alone.workspace(game, name, SURFACE)
