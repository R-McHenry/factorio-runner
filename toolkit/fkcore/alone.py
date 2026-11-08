"""fkcore.alone: a module built alone on a slot of a surface (3.1, PLAN-3.1 31-surface).

    python -m fkcore alone GAME_DIR NAME [--surface S] [--from DIR] [--size WxH] [--keep]
                                         [--with 'STEP OPTS']            NAME built alone on a slot of S
    python -m fkcore alone GAME_DIR NAME [--surface S] --measure SECONDS  what it really makes there
    python -m fkcore alone GAME_DIR NAME [--surface S] --clear            its slot freed (emptied, if S is free)
    python -m fkcore alone GAME_DIR [--surface S] --list                  the slots in use on S

This was the lab's machinery (``fkcore.lab``, 2.0.3). Rory (11-08): the lab is just another
surface where things are free; a module built alone at a place is a general build option, and
a slots file belongs to a surface, not to "the lab". So:

- **The surface.** ``--surface S`` (default ``lab``) names a surface file of the game codebase,
  ``<game>/S.surface``, whose property lines (``surface``, ``free``, ``force``, ``catalog``,
  ``notify``, ``watch``, ``remind``: what is true of the surface itself, ``fkcore.surface``)
  the module's own surface file copies. The lab needs no file: every game has it (the scenario
  makes it), and its properties are known (``DEFAULTS``: ``free``, ``force lab``).
- **The workspace.** ``NAME`` is a factory of the game codebase, or of ``--from DIR`` (a stage
  folder, a scratch copy; relative to the run folder). Its source goes into ``<run>/S/NAME``
  (never ``game/``, whose build script and surface stay the run's), with a surface file placing
  it on its own slot and a build script (``SCRIPT``, or the source's own ``S.build.fkb``). A file
  that only defines a sealed module is wrapped (``alone_wrap.fk``): the module alone with ``PAD``
  tiles round it, so a stand-in can feed its ports (``fkcore.standin``).
- **The slot.** Sized to the factory in whole cells of ``SLOT`` tiles with ``SPARE`` to spare; a
  new one goes to the free cells nearest the middle, ring by ring, and there is no last one (the
  lab has no edge, 2.0.12). A factory keeps its slot while it fits; one that outgrew it gets a
  new one. The slots are ``<run>/S/slots.json``.
- **Free or not.** On a free surface the slot is emptied first (a direct destroy: free things),
  the chunks under it are generated, and the apply revives the ghosts (``fkcore.apply``). On a
  surface that is not free nothing is destroyed and robots build the ghosts: what stood there
  is the diff's business, as for any build.
- **``--keep``** (3.0.2-lab, Rory 11-08: a base in the lab is changed a region at a time): the
  slot is not emptied, so what stands (trains, a hub's stock, the things placed by hand) stays
  and the paste lays the design over it (``--with "apply --area A"`` for one region); only the
  stale nets route again (``route --only stale`` in the script).
- **``--measure SECONDS``** reads what its machines make over that time against the rate model
  (``fkcore.measure``, on any surface): let it run a minute or two first, or a backlog that
  piled up while it started reads as speed.

Power, stock, fuel, infinity feeds and ore are not part of a build: they are stand-ins the agent
asks for by name (``fkcore.standin``), and ``fk lab`` (``fkcore.lab``) is the alias that asks for
the lab's usual ones after the build. Every game call is one RCON command with the link's 5 s
timeout, and nothing is registered.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field

from . import flow, link, measure, rates
from . import surface as surfacemod

__all__ = ["AloneError", "Built", "DEFAULTS", "SCRIPT", "SLOT", "PAD", "workspace", "slots",
           "slot_of", "cells", "prepare", "build", "measure_alone", "clear", "layout",
           "surface_of", "props_of", "lua_clear", "box"]

LAB = "lab"
SLOT = 200                         # tiles a cell spans; a slot is one or more cells each way
ORIGIN = -900                      # a cell's corner is ORIGIN + SLOT * i (2.0.3's grid, extended)
SPARE = 10                         # tiles a slot keeps free beyond its factory
DEFAULT_SIZE = (100, 100)          # a factory no surface file places
PAD = 4                            # tiles round a sealed module built alone: its ports' feeds
#: what the workspace copies from the source: the modules, their layouts, Literals' files, models
_COPY = (".fk", ".fkp", ".json", ".py")
_SKIP = ("map.terrain.json",)
#: the sealed module built alone is wrapped in this file, PAD tiles in
WRAP = "alone_wrap"
#: the surfaces every game has, and their properties, when the game codebase has no file for them
DEFAULTS = {LAB: "surface  lab\nfree\nforce    lab\nterrain  none\ncatalog  base\n"}

SCRIPT = """# {surface}.build.fkb (fkcore.alone): the default flow for a module built alone on {surface}
read_surface    {surface}
read_terrain    none
run_models      --stale
elaborate
size
report_rates
place           --keep pinned
route           --only stale --effort normal
cover power     --pole medium-electric-pole
floorplan
report_checks
report_bom      --stock none
write_layout
apply           --scope all               # a free surface: chunks generated, ore seeded, revived at once
diff --scope all               # all ok is 1:1
stop_on         invariant
"""


class AloneError(ValueError):
    pass


@dataclass
class Built:
    """What a build did: its report lines, the flow's exit code and context, the workspace, the
    slot (x, y, w, h), the plot (x, y, w, h) and the surface's properties."""
    lines: list = field(default_factory=list)
    code: int = 0
    ctx: object = None
    workspace: str = ""
    slot: tuple = (0, 0, 0, 0)
    plot: tuple = (0, 0, 0, 0)
    surface: str = LAB
    force: str = surfacemod.DEFAULT_FORCE
    free: bool = False


def _run_dir(game):
    return os.path.dirname(os.path.abspath(game))


def compiled_store(game):
    """The game folder's compiled sealed modules (PLAN-3.0 §4), which its alone builds share."""
    return os.path.join(os.path.abspath(game), "out", "compiled")


def workspace(game, name, surface=LAB):
    """``<run>/<surface>/NAME``: the workspace of NAME's build alone on ``surface``."""
    return os.path.join(_run_dir(game), surface, name)


def _slots_file(game, surface):
    return os.path.join(_run_dir(game), surface, "slots.json")


def slots(game, surface=LAB):
    """{name: [x, y, w, h]}: the slots held on ``surface`` by this run's alone builds (origin
    and span in tiles; a 2.0.3 slot, [x, y], spans one cell)."""
    try:
        with open(_slots_file(game, surface), encoding="utf-8") as fh:
            held = dict(json.load(fh))
    except (OSError, ValueError):
        return {}
    return {n: (list(v) + [SLOT, SLOT])[:4] for n, v in held.items()}


def _save(game, surface, held):
    os.makedirs(os.path.dirname(_slots_file(game, surface)), exist_ok=True)
    with open(_slots_file(game, surface), "w", encoding="utf-8", newline="\n") as fh:
        json.dump(held, fh, indent=0, sort_keys=True)
        fh.write("\n")


def cells(w, h):
    """The cells (each way) a w x h factory's slot spans: whole cells, SPARE tiles to spare."""
    return -(-(int(w) + SPARE) // SLOT), -(-(int(h) + SPARE) // SLOT)


def _free(spot, held):
    x, y, w, h = spot
    return all(x + w <= hx or hx + hw <= x or y + h <= hy or hy + hh <= y
               for hx, hy, hw, hh in held)


def _spots(cw, ch):
    """Slot origins for a slot of cw x ch cells, ring by ring outward from the middle; no end."""
    ring = 0
    while True:
        ring_spots = [(i, j) for j in range(-ring, ring + 1) for i in range(-ring, ring + 1)
                      if max(abs(i), abs(j)) == ring]
        for i, j in sorted(ring_spots, key=lambda ij: (ij[1], ij[0])):
            # cell 4 of 2.0.3's grid holds the middle (-100..100): the rings are around it
            yield (ORIGIN + SLOT * (4 + i - (cw - 1) // 2), ORIGIN + SLOT * (4 + j - (ch - 1) // 2),
                   cw * SLOT, ch * SLOT)
        ring += 1


def slot_of(game, name, size=DEFAULT_SIZE, surface=LAB):
    """NAME's slot (x, y, w, h) on ``surface`` for a factory of ``size``, the same each time while
    the factory fits it; a new name, or one that outgrew its slot, takes the free spot nearest
    the middle. -> (slot, the slot it outgrew or None)."""
    held = slots(game, surface)
    want = cells(*size)
    old = held.get(name)
    if old is not None and old[2] >= want[0] * SLOT and old[3] >= want[1] * SLOT:
        return tuple(old), None
    others = [tuple(v) for n, v in held.items() if n != name]
    spot = next(s for s in _spots(*want) if _free(s, others))
    held[name] = list(spot)
    _save(game, surface, held)
    return spot, (tuple(old) if old else None)


def box(x, y, w=SLOT, h=SLOT):
    """The area a slot's game calls cover: a little round the slot, short of the next."""
    return (x - 2, y - 2, x + w - 8, y + h - 8)


def _source(game, src):
    """The folder NAME's source comes from: game/, or ``src`` relative to the run folder."""
    if not src:
        return os.path.abspath(game)
    path = os.path.normpath(os.path.join(_run_dir(game), src))
    if os.path.commonpath([path, _run_dir(game)]) != _run_dir(game):
        raise AloneError(f"--from {src}: a folder inside the run, such as stages\\3 or scratch\\mall")
    if not os.path.isdir(path):
        raise AloneError(f"--from {src}: no such folder ({path})")
    return path


def props_of(game, surface=LAB):
    """The property lines of ``surface`` for a workspace's surface file: from the game codebase's
    ``<surface>.surface``, else ``DEFAULTS`` (the lab). The terrain is ``none``: a slot is bare
    ground, and the world build is what is checked against the terrain."""
    path = os.path.join(os.path.abspath(game), f"{surface}.surface")
    if os.path.exists(path):
        sf = surfacemod.load(path, derive=False)
        sf.terrain = "none"
        return surfacemod.properties(sf)
    if surface in DEFAULTS:
        return DEFAULTS[surface]
    raise AloneError(f"{surface}: no {surface}.surface in {game}: write one (surface {surface}, "
                     f"free if things are free there, force F, terrain none, catalog base)")


def surface_of(game, name, surface=LAB) -> surfacemod.SurfaceFile:
    """The surface file of NAME's workspace on ``surface`` (its properties and its one plot)."""
    path = os.path.join(workspace(game, name, surface), f"{surface}.surface")
    if not os.path.exists(path):
        raise AloneError(f"{name}: not built alone on {surface} yet (fk alone {name} --surface "
                         f"{surface})")
    return surfacemod.load(path)


def prepare(game, name, src=None, size=None, surface=LAB):
    """Write NAME's workspace on ``surface``: its source, a surface file placing it on its slot,
    the build script. -> (workspace, (x, y, w, h) of the plot)."""
    props = props_of(game, surface)
    source = _source(game, src)
    module, (w, h) = name, size or DEFAULT_SIZE
    try:
        sf = surfacemod.load_game(source)
    except (OSError, surfacemod.SurfaceError):
        sf = None
    if sf is not None and name in sf.places:
        p = sf.places[name]
        module, w, h = p.module, (size or (p.w, p.h))[0], (size or (p.w, p.h))[1]
    if not os.path.exists(os.path.join(source, f"{module}.fk")):
        raise AloneError(f"{name}: no {module}.fk in {source}"
                         + ("" if sf is None else f" (its surface places {', '.join(sorted(sf.places))})"))
    alone = _sealed_alone(os.path.join(source, f"{module}.fk"), name)
    if alone is not None and size is None:
        w, h = alone[1][0] + 2 * PAD, alone[1][1] + 2 * PAD
    (x, y, _sw, _sh), _old = slot_of(game, name, (w, h), surface)
    ws = workspace(game, name, surface)
    # 2.1.31: the last build's layouts (write_layout's MODULE.fkp) are what `route --only stale`
    # pins; emptying the workspace threw them away, so every rebuild routed every net again
    kept = {}
    if os.path.isdir(ws):
        for f in os.listdir(ws):
            # the modules' layouts, not the surface's own (it holds the old slot, and a slot
            # moved took the old slot's area into the next apply and diff)
            if f.endswith(".fkp") and f != f"{surface}.fkp" \
                    and not os.path.exists(os.path.join(source, f)):
                with open(os.path.join(ws, f), encoding="utf-8") as fh:
                    kept[f] = fh.read()
        shutil.rmtree(ws)
    os.makedirs(ws)
    for f, text in kept.items():
        with open(os.path.join(ws, f), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    skip = set(_SKIP) | ({f"{sf.name}.fkp"} if sf is not None else set())
    for f in sorted(os.listdir(source)):
        full = os.path.join(source, f)
        if os.path.isfile(full) and f.endswith(_COPY) and f not in skip \
                and not f.endswith((".surface", ".build.fkb")):
            shutil.copy2(full, os.path.join(ws, f))
    if alone is not None:
        mod, _size = alone
        with open(os.path.join(ws, f"{WRAP}.fk"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"# {WRAP}.fk (fkcore.alone): {mod} from {module}.fk alone, {PAD} tiles round "
                     f"it for its ports' stand-ins\nimport {mod} from \"{module}.fk\"\n"
                     f"{re.sub(r'[^a-z0-9_]', '_', mod.lower())} = {mod} @({PAD}, {PAD})\n")
        module = WRAP
    with open(os.path.join(ws, f"{surface}.surface"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# {surface}.surface (fkcore.alone): {name} from "
                 f"{os.path.relpath(source, _run_dir(game))} alone on its slot of {surface}\n"
                 f"{props}\nplace {name} = {module} plot {x},{y} {w}x{h}\n")
    # 2.1.31: a build script of the source's own (game\\ or --from) replaces the default flow,
    # so a design can be built with its tier and hands steps (express belts, bulk inserters)
    own = os.path.join(source, f"{surface}.build.fkb")
    script = SCRIPT.format(surface=surface)
    if os.path.isfile(own):
        with open(own, encoding="utf-8") as fh:
            script = fh.read()
    with open(os.path.join(ws, f"{surface}.build.fkb"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(script)
    return ws, (x, y, w, h)


def _sealed_alone(path, name):
    """(module, (w, h)) when the file at ``path`` places nothing and defines the sealed module
    built alone, its ports fed by a stand-in (2.2): the one sealed module, or the one named like
    ``name`` (any case). None for a file with instances of its own."""
    from . import lang
    try:
        with open(path, encoding="utf-8") as fh:
            f = lang.parse(fh.read(), path)
    except (OSError, lang.FkSyntaxError):
        return None
    if f.body.insts:
        return None
    sealed = {n: m for n, m in f.modules.items() if m.size is not None}
    pick = [n for n in sealed if n.lower() == name.lower()] or (list(sealed) if len(sealed) == 1
                                                                 else [])
    if not pick:
        return None
    m = sealed[pick[0]]
    if any(p.default is None for p in m.params):
        raise AloneError(f"{name}: {m.name} takes parameters ({', '.join(p.name for p in m.params)}):"
                         f" write a file that places it with them")
    return m.name, m.size


def lua_clear(surface, area):
    """Destroy what stands in ``area`` of ``surface`` but characters and resources (a free surface
    only: the caller checks). -> how many."""
    x0, y0, x1, y1 = area
    return f"""
local s = game.get_surface('{surface}')
local n = 0
for _, e in pairs(s.find_entities_filtered{{area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}}}}) do
  if e.valid and e.type ~= 'character' and e.type ~= 'resource' then e.destroy() n = n + 1 end
end
rcon.print(n)
"""


def clear(r, game, name, surface=LAB):
    """Free NAME's slot on ``surface``, emptied first where things are free. -> the report lines."""
    held = slots(game, surface)
    if name not in held:
        return [f"alone {name} on {surface}: no slot (fk alone --surface {surface} --list)"]
    x, y, w, h = held.pop(name)
    try:
        free = surface_of(game, name, surface).free
    except (AloneError, surfacemod.SurfaceError):
        free = surface == LAB
    _save(game, surface, held)
    if not free:
        return [f"alone {name} on {surface}: slot {x},{y} freed; {surface} is not free, so what "
                "stands there stays (fk decon --area takes it up, or the surface's next build "
                "orders it out)"]
    got = r.lua(lua_clear(surface, box(x, y, w, h))).strip()
    return [f"alone {name} on {surface}: slot {x},{y} emptied ({got} entities) and freed"]


def build(r, game, name, src=None, size=None, cat=None, extra=None, keep=False,
          surface=LAB) -> Built:
    """NAME alone on its slot of ``surface``: workspace, the slot emptied where things are free
    (unless ``keep``), the chunks under it generated (free), the flow. -> :class:`Built`."""
    before = slots(game, surface).get(name)
    ws, (x, y, w, h) = prepare(game, name, src, size, surface)
    sf = surface_of(game, name, surface)
    sx, sy, sw, sh = slots(game, surface)[name]
    got = Built(workspace=ws, slot=(sx, sy, sw, sh), plot=(x, y, w, h), surface=surface,
                force=sf.force_name, free=sf.free)
    lines = got.lines
    lines.append(f"alone {name} on {surface}{' (free)' if sf.free else ''}: slot {x},{y} "
                 f"({sw}x{sh} tiles for {w}x{h}), workspace "
                 f"{os.path.relpath(ws, _run_dir(game))} (from {src or 'game'})")
    if keep and before is None:
        raise AloneError(f"{name}: --keep keeps a slot that stands; it has none yet on {surface}")
    if keep and list(before) != [sx, sy, sw, sh]:
        raise AloneError(f"{name}: --keep: it outgrew its slot at {before[0]},{before[1]}; a "
                         "build without --keep moves it")
    if before is not None and list(before) != [sx, sy, sw, sh] and sf.free:
        moved = r.lua(lua_clear(surface, box(*before))).strip()
        lines.append(f"  it outgrew its slot at {before[0]},{before[1]}: that one emptied "
                     f"({moved} entities)")
    if sf.free:
        chunks = r.lua(link.lua_generate_area(surface, sf.force_name, box(sx, sy, sw, sh))).strip()
        if keep:
            lines.append(f"  kept: the slot stands as it is; {chunks} chunks generated under it")
        else:
            gone = r.lua(lua_clear(surface, box(sx, sy, sw, sh))).strip()
            lines.append(f"  emptied first: {gone} entities; {chunks} chunks generated under the "
                         "slot")
    else:
        lines.append(f"  {surface} is not free: nothing destroyed, robots build the ghosts, and "
                     "what stood on the slot is the diff's business")
    steps = []
    # PLAN-3.0 §4: a sealed module routed here is compiled into the game folder's store, so the
    # world build (and the next build alone) lays it from there
    code, ctx = flow.run(os.path.join(ws, f"{surface}.build.fkb"), cat=cat, echo=steps.append,
                         link=r, extra=extra, compiled=compiled_store(game))
    got.code, got.ctx = code, ctx
    if any(re.search(r"\]\s+apply\b", s) for s in steps):
        # 3.1: the slot is a placement of the game's (fkcore.placements): fk look NAME, fk diff
        # NAME, fk capture --from NAME read it there
        lines.append("  " + record_placement(game, name, surface, sf, (x, y, w, h), (sx, sy, sw, sh)))
    for want in ("elaborate", "route", "report_checks", "apply", "diff"):
        hit = next((s for s in steps if re.search(rf"\]\s+{want}\b", s)), None)
        if hit:
            lines.append("  " + " ".join(hit.split("]", 1)[1].split()))
    if code != 0:
        lines.append(f"  stopped: {steps[-1] if steps else 'the flow'} (out\\*.txt in the workspace)")
    return got


def record_placement(game, name, surface, sf, plot, slot, tick=None):
    """Record NAME's build alone as a placement in the game's registry for ``surface``: id NAME,
    its module, origin the plot's corner, area the slot's box, the compile store's current key
    when the module has one. -> one report line."""
    from . import compiled, placements
    module = sf.places[name].module if name in sf.places else name
    if module == WRAP:
        module = next((p.module for p in sf.places.values()), name)
    key = compiled.current(os.path.join(os.path.abspath(game), "out"), module)
    x, y, w, h = plot
    rec = placements.Record(name, surface, module, name, f"store:{key}" if key else None,
                           [x, y], 0, list(box(*slot)), tick, f"fk alone {name} --surface {surface}",
                           "alone", None, 0)
    placements.put_all(game, surface, [rec])
    return f"placement {name}: recorded in out/placements/{surface}.json (fk look {name}, fk diff {name})"


def layout(game, name, surface=LAB, cat=None):
    """NAME's workspace design laid out again, to ``report_checks`` (no game): the flow's context,
    for a measure or a stand-in that needs the design's ports and machines."""
    ws = workspace(game, name, surface)
    if name not in slots(game, surface) or not os.path.exists(os.path.join(ws, f"{surface}.build.fkb")):
        raise AloneError(f"{name}: not built alone on {surface} yet (fk alone {name} --surface "
                         f"{surface})")
    code, ctx = flow.run(os.path.join(ws, f"{surface}.build.fkb"), to="report_checks", cat=cat,
                         echo=lambda _l: None, compiled=compiled_store(game))
    if code != 0 or ctx.composed is None:
        raise AloneError(f"{name}: its design didn't lay out again (out\\*.txt in {ws})")
    return ctx


def measure_alone(r, game, name, seconds, cat=None, surface=LAB):
    """What NAME's machines make on its slot of ``surface`` over ``seconds``, against the rate
    model (``fkcore.measure``). -> the report lines."""
    ctx = layout(game, name, surface, cat)
    x, y, w, h = slots(game, surface)[name]
    models = {f: rates.solve(flat) for f, flat in ctx.flats.items()}
    got = measure.measure(r, ctx.composed, ctx.cat, box(x, y, w, h), ctx.flats, models, seconds)
    return [f"alone {name} on {surface} (slot {x},{y}):"] + got.lines()
