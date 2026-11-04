"""python -m fkcore <command> ...  (each command is one contained tool; only the flow's diff and
apply, look --source world, alerts, stats, chart, read-terrain and the game-link commands below
use the game link, and only with FK_LINK=on)

    check FILE.fk                         netlist checks (report lines, never a gate)
    rates FILE.fk [--unbounded NET,...] [--share appetite|level] [--chain INST,...]
          [--hand N] [--bulk-hand N]
                                          the nominal rate model and its asserts; n=auto rows
                                          sized first, as the build's size step sizes them;
                                          then the routed rates on FILE.fkp as last laid out
                                          (lanes and inserters: too-thin, WRONG-LANE), when it has
                                          routes
    plan ITEM RATE | plan ITEM --machines N [--asm A] [--furnace F] [--mining-productivity L]
         [--recipe ITEM=RECIPE,...] [--raw ITEM,...]
                                          the other way round: the machines, furnaces and
                                          drills a rate takes, per item of its recipe tree
    layout FILE.fk [--render]             expand placed instances into FILE.fkp (the writer
                                          refuses a layout that breaks an invariant)
    explain FILE.fk INST                  one instance as a template and as entities and nets
    explain FILE.fk NET                   one net: its items, its ends, its buffers, and what
                                          limits it (limited "...")
    unfold FILE.fk INST                   INST becomes a Literal; .fk and .fkp rewritten, keys kept
    render FILE.fkp                       ASCII of a factory layout
    build SCRIPT.fkb [--from STEP] [--to STEP]   (STEP:K for the K-th time a step runs)
                                          run the flow; reports in out/, checkpoints out/<step>/
    look GAME_DIR [--source design] [--area X0,Y0,X1,Y1]
                                          the composed surface of a game codebase, as designed
    look GAME_DIR --source world [--area X0,Y0,X1,Y1] [--notify nearby|all|off]
         [--watch ACTION] [--write-marks]
                                          the layout vs the game inside the area: verdicts,
                                          changes here, design and live renders; notify all
                                          pushes the changes to chat and out/inbox.txt
    wait GAME_DIR built [--area X0,Y0,X1,Y1] [--name ENTITY[,...]] [--max 5m] [--poll 10s]
                                          poll the world until the area has no ghosts left (or
                                          the named entity's are gone), or the bound expires:
                                          one line at the end; exit 0 clear, 1 ghosts left,
                                          2 a link error
    sense GAME_DIR CAPTURE.json --area X0,Y0,X1,Y1 [--write-marks]
                                          the layout vs a captured scene inside the area only
    alerts GAME_DIR [--force F]           the game's alert panel: new alerts, reminders (the
                                          surface file's remind), cleared ones; state in
                                          out/alerts.json; pushed to chat when notify is all
    stats GAME_DIR [--items A,B] [--window 5s|1min|10min|1h|10h|50h|250h|1000h] [--fluids]
          [--series N] [--force F]
                                          production and consumption per item (--fluids: per
                                          fluid) on the surface; --series N adds the window's
                                          graph in N points, oldest first (a free read, never an
                                          alert)
    stats GAME_DIR --sample [--now] | --history | --page
                                          the run's history (2.1, fkcore.runstats): --sample
                                          keeps the samples due (the scenario recorder's, or one
                                          RCON read every 5 game minutes) in out/stats/ and the
                                          capital every 10; --history the research, the latest
                                          window and the trend; --page out/stats/index.html
    stock GAME_DIR [--force F]            what the base holds, per item: chests and roboports
                                          apart (a free read, never an alert). A robot in a chest
                                          is idle: the line says how many are where
    chart GAME_DIR [--force F]            the surface's charted chunks: count and bounding box
                                          (a free read, never an alert)
    capital GAME_DIR [--area X0,Y0,X1,Y1] [--brief] [--force F]
                                          working capital: what the base owns, in raw units,
                                          working (machines that ran, robots in the air,
                                          research in use) against idle (stock, machines that
                                          stood, unused research); infrastructure left out
                                          (a free read, never an alert; fkcore.capital)
    capital GAME_DIR --park X0,Y0,X1,Y1 --why TEXT | --unpark N | --weight TECH=W
                                          leave an area out on purpose (always reported), or
                                          weigh a technology (1 counts it fully, 0 not at all)
    split-to-chest GAME_DIR FACTORY NET NAME --at X,Y [--requester --item I [--rate R]]
                                          a chest joins NET as one more endpoint; only NET
                                          is re-routed by the next build

    merge GAME_DIR A,B --into C           two factories become one (source, layout, plots)
    insert GAME_DIR COMPONENT_DIR NAME --into C --at X,Y
                                          a component copied into a factory
    chests-to-net GAME_DIR FACTORY PROVIDER REQUESTER [--net link]
                                          a robot link (two chests) becomes one net
    net-to-chests GAME_DIR FACTORY NET --provider P --requester R
                                          a net becomes a robot link (a full decouple)
    stage GAME_DIR list|horizon|check [N]|next [--apply]|done [N]|rebase|edit N|insert TITLE
                   |drop N|reorder N      the staged plan (2.0): main is what is built, the
                                          plan one commit per stage ahead of it (fkcore.stage)

  with the game link (FK_LINK=on, on the game host):
    measure GAME_DIR --area X0,Y0,X1,Y1 [--seconds 30]
                                          what the machines there make, against the model
    lab GAME_DIR NAME [--from DIR] [--size WxH] | NAME --measure S | NAME --clear | --list
                                          a factory (or a stage's) built on its own lab slot,
                                          powered and stocked; then what it really makes
    sim GAME_DIR start [NAME] | stop | status | save NAME | load NAME | saves | speed X |
         run N | until T | place FILE @X,Y | paste FILE @X,Y | remove AREA | decon AREA |
         play SCRIPT | env      the speedrun planner: the run's own map on this machine, paused
                                          between actions (fkcore.sim)
    lab GAME_DIR NAME --copy-ore X0,Y0,X1,Y1 [--to X,Y] | --ore R --area X0,Y0,X1,Y1
         [--amount N] | --clear-ore       ore on NAME's slot: the world's, copied, or made
    fresh GAME_DIR --area X0,Y0,X1,Y1     belts there that carry items their net doesn't are
                                          taken up by robots; the next build lays them fresh
    adopt GAME_DIR X,Y[;X,Y...] --into FACTORY/NET
                                          carriers placed by hand become part of a net's route
    chat GAME_DIR [--all]                 the chat lines since the last read (--all: the journal)
    say TEXT...                           one line in the game chat, as [agent]
    poll GAME_DIR                         what every agent command ends with: the game's alerts
                                          (new, reminders, cleared; grouped), new chat lines and
                                          the research state (loud when nothing researches)
    dnp FILE.fk NAME[,NAME] [--off]       do not populate: laid out and routed, not built
    decon GAME_DIR --area X0,Y0,X1,Y1 [--name ENTITY[,...]] [--cancel]
                                          the deconstruction planner: robots take it up
    request GAME_DIR X,Y|KEY ITEM N [--name ENTITY] [--inventory NAME]
                                          an item request: robots bring N of ITEM from the
                                          network to the entity that stands there (a proxy)
    capture GAME_DIR lab NAME | world X0,Y0,X1,Y1 | design [FACTORY/]INST [--to FILE]
                                          something built (a lab slot, an area of the game or
                                          the planner, a laid-out instance) as a Literal file:
                                          Literal<file=...> anywhere, a module included
    lua GAME_DIR CODE | --file F          run a Lua chunk in the game (RCON); its rcon.print is the
                                          reply. For reading and investigating; the link refuses
                                          Lua that registers handlers or commands (unjoinable saves)
    research GAME_DIR [TECH...] [--first] queue research, each after its unfinished prerequisites;
                                          --first: at the front of the queue; without a name, what
                                          can be researched now and its cost (a free read)
    tech GAME_DIR NAME...                 a technology, or what unlocks a recipe or item: cost or
                                          trigger, prerequisites (a free read)
    recipes GAME_DIR [NAME...] [--enabled]
                                          the locked recipes (or the enabled ones) and what
                                          unlocks each (a free read)
    read-terrain GAME_DIR --surface S --area X0,Y0,X1,Y1 [--name map] [--replace]
                                          a free read of the bare world (ore, water, cliffs;
                                          trees and rocks counted) into GAME_DIR/<name>.terrain.json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

from . import compose, expand, fkp, flow, plan, rates
from . import size as sizemod
from . import surface as surfacemod
from .catalog import Catalog
from .elaborate import ElabError, elaborate_file
from .lang import FkSyntaxError


def _fkp_path(path):
    return os.path.splitext(path)[0] + ".fkp"


def _flat(path, layout=None):
    """The file's netlist; its n=auto rows sized as the build's size step sizes them (with the
    surface file beside it, when that places it), the Sizing on ``flat._sizing``."""
    from . import avail as availmod
    availmod.use(os.path.dirname(os.path.abspath(path)))     # inserter=any chooses from out/
    if layout is None and os.path.exists(_fkp_path(path)):
        layout = fkp.load(_fkp_path(path))
    cat = Catalog.load()
    try:
        flat = elaborate_file(path, cat, layout=layout)
        if sizemod.has_auto(flat):
            flat, sz, note = sizemod.size_file(path, cat, layout)
            flat._sizing = (sz, note)
        return flat
    except (FkSyntaxError, ElabError, OSError, surfacemod.SurfaceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _area(text):
    if not text:
        return None
    parts = [int(v) for v in text.split(",")]
    if len(parts) != 4 or parts[2] <= parts[0] or parts[3] <= parts[1]:
        raise SystemExit(f"error: an area is X0,Y0,X1,Y1 with X1 > X0 and Y1 > Y0, got {text}")
    return tuple(parts)


def _game_link(what):
    """The game link, or None after saying why (it is used only with FK_LINK=on)."""
    from . import link
    r, why = link.open_from_env()
    if r is None:
        print(f"{what}: skipped, no game link ({why})")
    return r


def _world(a, cat, what, body):
    """Run ``body(r)`` -> lines with the game link open; errors are one line, never a trace."""
    from . import link, look
    r = _game_link(what)
    if r is None:
        return 2
    try:
        with r:
            lines = body(r)
    except (look.LookError, surfacemod.SurfaceError, link.LinkError, OSError) as exc:
        print(f"error: {what}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


def _view(a):
    """The look's vision.View from --zoom, --layers and --image; exits with the reason."""
    from . import vision
    try:
        layers = vision.parse_layers(a.layers)
    except ValueError as exc:
        raise SystemExit(f"error: look --layers: {exc}")
    return vision.View(a.zoom, layers, a.image)


def _look_world(a, cat):
    from . import look
    area = _area(a.area)
    view = _view(a)
    return _world(a, cat, "look --source world", lambda r: look.run(
        r, a.game, cat, area, a.notify, a.watch, a.write_marks, view=view).lines())


def _alerts(a, cat):
    from . import alerts
    return _world(a, cat, "alerts", lambda r: alerts.check(r, a.game, a.force).report())


def _free_read(a, cat):
    from . import freereads
    from .apply import force_for
    try:
        sf = surfacemod.load_game(a.game)
    except (surfacemod.SurfaceError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    force = a.force or force_for(sf.mode)
    if a.cmd == "stats" and (a.sample or a.history or a.page):
        return _run_stats(a, cat, force, sf.name)
    if a.cmd == "chart":
        return _world(a, cat, "chart", lambda r: freereads.chart_lines(
            freereads.chart(r, force, sf.name)))
    if a.cmd == "stock":
        return _world(a, cat, "stock", lambda r: freereads.stock_lines(
            freereads.stock(r, force, sf.name)))
    if a.window not in freereads.WINDOWS:
        print(f"error: --window is one of {', '.join(freereads.WINDOWS)}", file=sys.stderr)
        return 2
    items = [i for i in (a.items or "").split(",") if i] or None
    if a.series and not items:
        print("error: --series needs --items (the graph is per item)", file=sys.stderr)
        return 2
    return _world(a, cat, "stats", lambda r: freereads.stats_lines(
        freereads.stats(r, force, sf.name, items, a.window, a.fluids, a.series)))


def _run_stats(a, cat, force, surface):
    """``stats --sample | --history | --page``: the run's history (fkcore.runstats, 2.1). Only
    the sample reads the game; the history and the page read out/stats."""
    from . import runstats
    code = 0
    if a.sample:
        code = _world(a, cat, "stats --sample", lambda r: runstats.sample(
            r, a.game, cat, force, surface, now=a.now))
    if a.history:
        print("\n".join(runstats.history_lines(a.game)))
    if a.page:
        print(f"stats page: {os.path.abspath(runstats.write_page(a.game))}")
    return code


def _capital(a, cat):
    """``capital``: the working capital read (fkcore.capital), or a change to what is parked."""
    from . import capital
    from .apply import force_for
    store = capital.Store(a.game, _area(a.area) if a.area else None)
    cfg = store.config
    if a.park or a.unpark or a.weight:
        if a.park:
            if not a.why:
                print("error: capital --park: say why with --why", file=sys.stderr)
                return 2
            cfg["parked"].append({"area": list(_area(a.park)), "why": a.why})
            print(f"parked {len(cfg['parked'])}: {a.park} ({a.why}): left out of the working "
                  "capital, and reported as parked")
        if a.unpark:
            if not 1 <= a.unpark <= len(cfg["parked"]):
                print(f"error: capital --unpark: {len(cfg['parked'])} areas are parked",
                      file=sys.stderr)
                return 2
            p = cfg["parked"].pop(a.unpark - 1)
            print(f"unparked {a.unpark}: {p.get('area')} ({p.get('why')}): counted again")
        if a.weight:
            tech, _, w = a.weight.partition("=")
            try:
                cfg["weights"][tech] = float(w)
            except ValueError:
                print("error: capital --weight TECH=W, W a number", file=sys.stderr)
                return 2
            print(f"weight {tech} = {float(w):g}")
        store.save_config(cfg)
        return 0
    try:
        sf = surfacemod.load_game(a.game)
    except (surfacemod.SurfaceError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    force = a.force or force_for(sf.mode)
    return _world(a, cat, "capital", lambda r: capital.run(
        r, a.game, cat, force, sf.name, _area(a.area) if a.area else None, a.brief))


def _look(a, cat):
    if a.source == "world":
        return _look_world(a, cat)
    from . import look
    try:
        sf, _lay, _path, s = look.composed(a.game, cat)
    except surfacemod.SurfaceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    box = _area(a.area)
    view = _view(a)
    if not view.plain:
        from . import scene, vision
        box = box or look.default_area(s, cat)
        if box is None:
            print("error: nothing is designed on this surface", file=sys.stderr)
            return 2
        if view.image == "":
            view = vision.View(view.zoom, view.layers, look.image_path(a.game, box, view.zoom))
        tags = vision.design_tags(a.game, sf, s)
        lines, _img = vision.compose_view(view, vision.Frame(box, view.zoom), cat,
                                          scene.design_scene(s, cat, list(box)), plots=tags[0],
                                          tags=tags, title=f"look {sf.name} --source design")
        print(f"look {sf.name} --source design: {len(s.plots)} plots, {len(s.entities)} "
              f"entities, {len(s.problems)} invariant problems; zoom {view.zoom}")
        print("\n".join(lines))
        return 0
    print(f"look {sf.name} --source design: {len(s.plots)} plots, {len(s.entities)} entities, "
          f"{len(s.problems)} invariant problems (. plot, : claim, ! mark, blank sanctioned)")
    print(compose.render(s, cat, box))
    return 0


def _wait(a, cat):
    """``wait built``: a bounded wait for the ghosts in an area (fkcore.wait), through the link.
    The area is the design's, as a look without one, unless given; one line at the end. Then
    (2.1) the wire pass (``fkcore.wires``): a pole robots built joined itself to its neighbours,
    so the wires are set as the design has them, with a count line when any changed."""
    from . import link, look, wait as waitmod, wires as wiresmod
    try:
        max_s = waitmod.parse_duration(a.max)
        poll_s = waitmod.parse_duration(a.poll)
    except ValueError as exc:
        print(f"error: wait built: {exc}", file=sys.stderr)
        return 2
    if max_s < 0 or poll_s <= 0:
        print("error: wait built: --max is 0 or more and --poll is more than 0 (90s, 5m, 1h)",
              file=sys.stderr)
        return 2
    try:
        sf, _surface_lay, _lay_path, s = look.composed(a.game, cat)
    except (surfacemod.SurfaceError, OSError) as exc:
        print(f"error: wait built: {exc}", file=sys.stderr)
        return 2
    try:
        area = _area(a.area) if a.area else look.default_area(s, cat)
    except SystemExit as exc:
        print(exc.code, file=sys.stderr)          # _area says why; a bad area is no wait
        return 2
    if area is None:
        print("error: wait built: nothing is designed on this surface", file=sys.stderr)
        return 2
    names = [n.strip() for n in (a.name or "").split(",") if n.strip()]
    r = _game_link("wait built")
    if r is None:
        return 2
    try:
        with r:
            code, lines = waitmod.run(r, sf.name, area, names, max_s, poll_s)
            got = wiresmod.run(r, s, cat, area, tag="wait-wires")
            if got.line():
                lines.append(got.line())
            lines += [f"  by-hand: {h}" for h in got.hand]
    except KeyboardInterrupt:
        print("wait built: interrupted; the link is closed and no game call is left running",
              file=sys.stderr)
        return 130
    except (link.LinkError, OSError) as exc:
        print(f"error: wait built: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return code


def _composed(game, cat):
    from . import look
    try:
        return look.composed(game, cat)
    except surfacemod.SurfaceError as exc:
        raise SystemExit(f"error: {exc}") from None


def _sense(a, cat):
    import json
    from . import own, scene, sense
    sf, surface_lay, lay_path, s = _composed(a.game, cat)
    with open(a.capture, encoding="utf-8") as fh:
        live = scene.from_capture(json.load(fh))
    area = [int(v) for v in a.area.split(",")]
    rep = sense.sense(s, live, cat, area, sf, own=own.load(a.game, sf.name))
    print("\n".join(rep.lines()))
    if a.write_marks and rep.marks:
        for m in rep.marks:
            surface_lay.marks[m.name] = m
        fkp.save(surface_lay, lay_path, cat)
        print(f"wrote {len(rep.marks)} mark(s) into {os.path.basename(lay_path)}")
    return 0


def _talk(a):
    from . import chat
    r = _game_link(a.cmd)
    if r is None:
        return 2
    with r:
        if a.cmd == "say":
            chat.say(r, " ".join(a.text))
            return 0
        if a.cmd == "ack":
            tick = chat.ack(r, a.game)
            print(f"ack: chat dismissed up to tick {tick}: nothing unanswered")
            return 0
        if a.cmd == "chat" and a.all:
            got = chat.read(r, 0)
            lines = [f"chat {m.get('name') or '?'}: {m.get('message', '')}" for m in got["messages"]]
        else:
            lines = chat.pending(r, a.game)
        if a.cmd == "poll":
            from . import alerts
            # 2.0.25: the unanswered chat goes LAST, so `| Select-Object -Last N` keeps it
            lines = alerts.poll(r, a.game) + [_research_footer(r, a.game)] + lines
        print("\n".join(lines) if lines else ("chat: nothing unanswered" if a.cmd == "chat" else ""),
              end="\n" if lines or a.cmd == "chat" else "")
    return 0


def _migrate_surface(a):
    """``fk migrate-surface GAME`` (2.0.43, PLAN-GLOBAL phase B): write the world design
    ``<surface>.fk`` from the surface's ``place`` lines, and put ``design`` in the surface file.
    The place lines stay as commented-out record; nothing else changes."""
    from . import root as rootmod
    game = a.game
    try:
        sf = surfacemod.load_game(game, derive=False)
    except (surfacemod.SurfaceError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if sf.design:
        print(f"error: {sf.name} already has `design {sf.design}`: nothing to migrate",
              file=sys.stderr)
        return 2
    if not sf.places:
        print(f"error: {os.path.basename(sf.path)} has no place lines to migrate", file=sys.stderr)
        return 2
    rootname = f"{sf.name}.fk"
    rootpath = os.path.join(game, rootname)
    if os.path.exists(rootpath):
        print(f"error: {rootname} already exists: move it aside first", file=sys.stderr)
        return 2
    text = rootmod.migrate(sf.places, sf.name)
    with open(rootpath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    with open(sf.path, encoding="utf-8") as fh:
        was = fh.read()
    with open(sf.path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(surfacemod.migrate_text(was, rootname))
    # 2.0.44: the design must give back exactly the plots the place lines gave, or nothing changes
    try:
        after = surfacemod.load(sf.path)
        lost = _migrate_mismatch(sf.places, after.places)
    except surfacemod.SurfaceError as exc:
        after, lost = None, [str(exc)]
    if lost:
        with open(sf.path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(was)
        os.remove(rootpath)
        print(f"error: the design would not give back the place lines' plots, so nothing was "
              f"changed:", file=sys.stderr)
        for line in lost + (after.design_notes if after is not None else []):
            print(f"  {line}", file=sys.stderr)
        return 2
    print(f"migrate-surface {game}: wrote {rootname} "
          f"({len(sf.places)} plots: {', '.join(sorted(sf.places))})")
    for p in sf.places.values():
        print(f"  {p.factory} = {p.module} @{p.x},{p.y} size {p.w}x{p.h}"
              + (f" rot {p.rot}" if p.rot else "") + (f" gap {p.gap}" if p.gap else "")
              + (f" patch {p.patch}" if p.patch else "") + f" ({p.lifecycle})")
    print(f"  {os.path.basename(sf.path)}: `design {rootname}` added, "
          f"{len(sf.places)} place lines kept as comments")
    if after.design_notes:
        for note in after.design_notes:
            print(f"  note {note}")
    print(f"  the design derives {len(after.places)} plots; "
          f"`fk build` reads it from now on (`place` lines no longer place anything)")
    return 0


def _migrate_mismatch(was, now):
    """What a migrated design's plot list loses or changes against the place lines' (2.0.44)."""
    out = []
    for name in sorted(set(was) | set(now)):
        p, q = was.get(name), now.get(name)
        if q is None:
            out.append(f"{name}: placed by a place line, not by the design")
        elif p is None:
            out.append(f"{name}: placed by the design, not by a place line")
        elif (p.module, p.x, p.y, p.w, p.h, p.rot, p.gap, p.patch, p.lifecycle) != \
                (q.module, q.x, q.y, q.w, q.h, q.rot, q.gap, q.patch, q.lifecycle):
            out.append(f"{name}: the design places it otherwise")
    return out


def _force_surface(game):
    from .apply import force_for
    sf = surfacemod.load_game(game)
    return getattr(sf, "force", None) or force_for(sf.mode), sf.name


def dnp_edit(text, names, off=False):
    """``text`` (a .fk file's top level) with ``names`` (dotted refs) put on, or with ``off`` taken
    off, its ``dnp`` lines (2.0.63). The names go on the file's last top-level ``dnp`` line, or a new
    one at the end; ``off`` drops each name wherever it stands, and a line left empty. -> (new
    text, what changed)."""
    lines = text.split("\n")
    pat = re.compile(r"^dnp\s+(.*?)(\s*#.*)?$")
    have = {}
    for i, line in enumerate(lines):
        m = pat.match(line)
        if m:
            have[i] = [n.strip() for n in m.group(1).split(",") if n.strip()]
    on = {n for ns in have.values() for n in ns}
    if off:
        gone = [n for n in names if n in on]
        for i, ns in sorted(have.items(), reverse=True):
            keep = [n for n in ns if n not in names]
            if keep == ns:
                continue
            m = pat.match(lines[i])
            if keep:
                lines[i] = "dnp " + ", ".join(keep) + (m.group(2) or "")
            else:
                del lines[i]
        return "\n".join(lines), gone
    new = [n for n in names if n not in on]
    if not new:
        return text, []
    if have:
        i = max(have)
        m = pat.match(lines[i])
        lines[i] = "dnp " + ", ".join(have[i] + new) + (m.group(2) or "")
    else:
        while lines and lines[-1] == "":
            lines.pop()
        lines += ["dnp " + ", ".join(new) + "   # do not populate: laid out, not built", ""]
    return "\n".join(lines), new


def _dnp(a, cat):
    """fk dnp FILE.fk NAME[,NAME] [--off] (2.0.63): the file's dnp line, edited; then elaborated, so
    a name that matches nothing is said before the next build."""
    names = [n.strip().replace("/", ".") for n in a.names.split(",") if n.strip()]
    with open(a.file, encoding="utf-8") as fh:
        text = fh.read()
    new, changed = dnp_edit(text, names, a.off)
    with open(a.file, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(new)
    try:
        elaborate_file(a.file, cat)
    except (ElabError, FkSyntaxError) as exc:
        with open(a.file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print(f"error: dnp: {exc} (the file is as it was)", file=sys.stderr)
        return 2
    verb = "built again (dnp taken off)" if a.off else "DNP: laid out, not built"
    print(f"dnp {os.path.basename(a.file)}: {', '.join(changed) or 'nothing changed'}"
          + (f" {verb}" if changed else "") + "; the next fk build does it"
          + (" (fk build --confirm also takes up what stands built there)" if not a.off
             and changed else ""))
    return 0


def _request_at(a, cat):
    """``fk request``'s ENTITY: ``X,Y`` (map position), or a design key (``world/ab``, as the
    .fkp and ``fk look --design`` name it) found in the composed design. -> (x, y, name or None)."""
    m = re.fullmatch(r"\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*", a.entity)
    if m:
        return float(m.group(1)), float(m.group(2)), a.name or None
    from . import look
    _sf, _lay, _path, s = look.composed(a.game, cat)
    hits = [e for k, e in s.entities.items() if k == a.entity or k.split(":", 1)[-1] == a.entity]
    if len(hits) != 1:
        raise SystemExit(f"error: request: {a.entity} is no X,Y and names "
                         f"{'no' if not hits else len(hits)} entities of the design")
    e = hits[0]
    return float(e.x), float(e.y), a.name or e.name


def _request(a, cat):
    """fk request ENTITY ITEM N (2.1.3): an item-request proxy, the game's own way to have robots
    bring items to an entity that stands (robots into a roboport, ammo into a turret, barrels into
    a machine). It only asks, and says what the network doesn't hold."""
    from . import link
    if a.count < 1:
        print("error: request: N is at least 1", file=sys.stderr)
        return 2
    if not cat.is_item(a.item):
        print(f"error: request: no item {a.item}", file=sys.stderr)
        return 2
    x, y, name = _request_at(a, cat)
    force, surface = _force_surface(a.game)

    def body(r):
        got = r.lua(link.lua_request(surface, force, (x, y), a.item, a.count, name,
                                     a.inventory)).strip()
        try:
            d = json.loads(got)
        except ValueError:
            raise link.LinkError(got[:300]) from None
        if not d.get("ok"):
            raise link.LinkError(d.get("why") or got[:300])
        where = f"{d['name']} at {_fmt_xy(d['x'], d['y'])}"
        asked, stock = int(d["asked"]), int(d["stock"])
        if not asked:
            return [f"request: nothing asked: {where} has no room for {a.item} "
                    f"(inventory {d['inventory']})"]
        out = [f"request: robots bring {asked} {a.item} to {where} (inventory {d['inventory']})"
               + (f", with the {d['had']} request(s) it had" if d.get("had") else "")]
        if asked < a.count:
            out.append(f"  only {asked} of {a.count} fit (room for {d['room']})")
        if stock < asked:
            out.append(f"  SHORT: the network holds {stock} {a.item}; the request waits for the "
                       f"other {asked - stock} (make them, or bring them into the network)")
        else:
            out.append(f"  the network holds {stock} {a.item}")
        if not d.get("reach"):
            out.append(f"  UNREACHED: no powered roboport's construction area reaches {where}: "
                       "no robot comes until one does")
        return out
    return _world(a, cat, "request", body)


def _fmt_xy(x, y):
    return ",".join(str(int(v)) if float(v).is_integer() else f"{float(v):g}" for v in (x, y))


def _decon(a, cat):
    """fk decon (2.0.62): the deconstruction planner over an area, the robots take it up."""
    from . import link
    area = _area(a.area)
    names = [n for n in (a.name or "").split(",") if n]
    unknown = [n for n in names if not cat.has_entity(n)]
    if unknown:
        print(f"error: decon: no entity {', '.join(unknown)}", file=sys.stderr)
        return 2
    force, surface = _force_surface(a.game)

    def body(r):
        got = r.lua(link.lua_decon_area(surface, force, area, names, a.cancel)).strip()
        try:
            data = json.loads(got)
        except ValueError:
            raise link.LinkError(got[:300]) from None
        by = data.get("by") or {}
        if isinstance(by, list):                  # an empty Lua table comes back as []
            by = {}
        n = sum(by.values())
        what = ", ".join(f"{c} {k}" for k, c in sorted(by.items(), key=lambda kv: (-kv[1], kv[0])))
        if a.cancel:
            return [f"decon: {n} orders taken back in {a.area}" + (f": {what}" if what else "")]
        out = [f"decon: {n} entities in {a.area} ordered out, for the robots"
               + (f": {what}" if what else "")]
        if n:
            out.append("  what the design still has there, the next build of its factory lays "
                       "again: take it out of the .fk too, or `fk decon AREA --cancel`")
        if data.get("robots"):
            out.append(f"  the roboports ordered out hold {data['robots']} robots: they go to "
                       "storage as items and do nothing there until an inserter puts them into a "
                       "roboport (fk stock shows them)")
        return out
    return _world(a, cat, "decon", body)


def _capture(a, cat):
    """fk capture (2.0.62): something built, as a Literal file that can go anywhere
    (fkcore.capture)."""
    from . import capture, lab, link
    to = a.to or os.path.join("captured", f"{re.sub(r'[^A-Za-z0-9_-]+', '-', a.what)}.json")
    dest = to if os.path.isabs(to) else os.path.join(a.game, to)
    try:
        if a.source == "design":
            fkp_path = a.fkp
            if not fkp_path:
                factory, _, rest = a.what.partition("/")
                fkp_path = os.path.join(a.game, f"{factory}.fkp")
                path = rest
                if not rest or not os.path.exists(fkp_path):
                    # one plot: every instance is in the world's layout
                    sf = surfacemod.load_game(a.game)
                    only = sorted(sf.places)
                    fkp_path = os.path.join(a.game, f"{only[0]}.fkp") if len(only) == 1 \
                        else fkp_path
                    path = a.what
            else:
                path = a.what
            if not os.path.exists(fkp_path):
                raise capture.CaptureError(f"no layout {fkp_path} (run a build to write_layout "
                                           "first, or name one with --fkp)")
            rows = capture.from_design(fkp.load(fkp_path), cat, path)
            data = {"entities": rows, "from": f"design {path} of {fkp_path}"}
            named = capture.names([row["name"] for row in rows], cat)
            if named:
                data["names"] = named
            capture.write(dest, data)
            names = {}
            for row in rows:
                names[row["name"]] = names.get(row["name"], 0) + 1
            n = len(rows)
        else:
            r = _game_link("capture")
            if r is None:
                return 2
            with r:
                if a.source == "lab":
                    held = lab.slots(a.game)
                    if a.what not in held:
                        raise capture.CaptureError(
                            f"no lab slot {a.what} (slots: {', '.join(sorted(held)) or 'none'})")
                    x, y, w, h = held[a.what]
                    area = (x, y, x + w, y + h)
                    surface, force = lab.SURFACE, lab.FORCE
                else:
                    area = _area(a.what)
                    force, surface = _force_surface(a.game)
                got = capture.from_game(r, surface, force, area)
            n, names = got["n"], got.get("names") or {}
            data = {"bp": got["bp"], "from": f"{a.source} {a.what} on {surface}"}
            named = capture.names_of_bp(got["bp"], cat)
            if named:
                data["names"] = named
            capture.write(dest, data)
    except (capture.CaptureError, link.LinkError, surfacemod.SurfaceError, OSError) as exc:
        print(f"error: capture: {exc}", file=sys.stderr)
        return 2
    rel = os.path.relpath(dest, a.game).replace(os.sep, "/")
    print(f"capture: {n} entities into {rel}: {capture.summary(names)}")
    if named:
        print(f"  names: {capture.name_span(named)}: a net ends on one as X.asm1 when the Literal "
              "is X")
    print(f'  use it anywhere, a module included:  X = Literal<file="{rel}"> @(x, y)'
          f"   (or fk sim place game\\{rel.replace('/', chr(92))} @X,Y)")
    return 0


def _avail(a):
    """What a design's ``inserter=any`` / ``route --hand any`` / ``cover power --pole any`` can
    build now: one read of the families' prototypes, and ``--save`` writes ``out/avail.json`` for
    the next build's design half, which runs where there is no game link (2.0.14)."""
    from . import avail as availmod
    from . import link, supply
    r = _game_link("avail")
    if r is None:
        return 2
    force, surface = _force_surface(a.game)
    try:
        with r:
            sup = supply.read(r, force, surface, availmod.CANDIDATES, tag="avail")
    except (link.LinkError, OSError) as exc:
        print(f"error: avail: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    availmod.install(sup, "fk avail")
    lines = [f"avail: {len(sup.entities)} items read on {surface} (tick {sup.tick})"]
    for fam in sorted(availmod.FAMILIES):
        lines.append(f"  {availmod.choose(fam, 'any', need=1)[1]}")
    if a.save:
        path = availmod.save(a.game, sup, "fk avail --save")
        lines.append(f"  saved {path}: what the next build's any chooses from")
    print("\n".join(lines))
    return 0


def _research_footer(r, game):
    """The research bar a player always sees (a free read): one line, loud when nothing moves."""
    from . import supply
    try:
        force, surface = _force_surface(game)
        return supply.research_line(supply.read(r, force, surface).research)
    except Exception as exc:          # a footnote must never break the command that asked
        return f"research: not read ({type(exc).__name__}: {exc})"


def _lua(a):
    """A Lua chunk through the link: the player's own probe (Rory, 2026-10-20: the player may write
    code and read the game with Lua to investigate). The link still refuses Lua that registers a
    handler, a command or an interface: that made every save unjoinable on 2026-10-18.
    ``--file -`` reads the chunk from stdin (agent/fk.py sends it that way, 1.2). The chunk goes
    with its lines as written (1.2): ``Rcon.lua`` joins lines with spaces, so a comment at the end
    of a line took the rest of the chunk with it, and the game takes newlines in a command (a
    40 kB chunk on a laptop test server, 2.0.77)."""
    from . import link
    code = a.code
    if a.file == "-":
        code = sys.stdin.buffer.read().decode("utf-8")
    elif a.file:
        with open(a.file, encoding="utf-8") as fh:
            code = fh.read()
    if not code or not code.strip():
        print("error: lua needs CODE or --file F", file=sys.stderr)
        return 2
    r = _game_link("lua")
    if r is None:
        return 2
    with r:
        try:
            print(r.call("/sc " + code))
        except link.LinkError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    return 0


def _research(a):
    """research / tech / recipes: the research screen and the tech tree (v4's research, tech and
    recipes, through fkcore.supply)."""
    from . import supply
    r = _game_link(a.cmd)
    if r is None:
        return 2
    force, surface = _force_surface(a.game)
    with r:
        if a.cmd == "research" and a.techs:
            got = supply.add_research(r, force, surface, a.techs, a.first)
            added, failed, front = (supply._list(got.get(k)) for k in ("added", "failed", "front"))
            pre = [t for t in added if t not in a.techs]
            lines = [f"research: queued {', '.join(added) or 'nothing new'}"
                     + (f" ({', '.join(pre)} first: prerequisites)" if pre else "")]
            if got.get("moved"):
                lines.append(f"  at the front of the queue now: {', '.join(front)}")
            lines += [f"  not queued: {f}" for f in failed]
            if added or got.get("moved"):
                lines.append(supply.research_line(got.get("research")))
        elif a.cmd == "research":
            lines = supply.available_lines(supply.available(r, force, surface))
        elif a.cmd == "tech":
            lines = supply.tech_lines(supply.tech(r, force, a.names))
        else:
            lines = supply.recipe_lines(supply.recipes(r, force, a.names), a.enabled)
    print("\n".join(lines))
    return 0


def _rewire(a):
    from . import merge
    try:
        if a.cmd == "merge":
            ch = merge.merge(a.game, a.names.split(","), a.into)
        elif a.cmd == "insert":
            ch = merge.insert(a.game, a.component_dir, a.name, a.into,
                              tuple(int(v) for v in a.at.split(",")))
        elif a.cmd == "chests-to-net":
            ch = merge.chests_to_net(a.game, a.factory, a.provider, a.requester, a.net)
        else:
            ch = merge.net_to_chests(a.game, a.factory, a.net, a.provider, a.requester)
    except merge.MergeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(ch)
    return 0


def _flats_and_models(game, sf, cat):
    flats, models = {}, {}
    for p in sf.places.values():
        path = os.path.join(game, f"{p.module}.fk")
        lay_path = os.path.join(game, f"{p.module}.fkp")
        if not os.path.exists(path):
            continue
        flat = elaborate_file(path, cat, name=p.module,
                              layout=fkp.load(lay_path) if os.path.exists(lay_path) else None)
        flats[p.factory] = flat
        models[p.factory] = rates.solve(flat)
    return flats, models


def _measure(a, cat):
    from . import measure
    r = _game_link("measure")
    if r is None:
        return 2
    sf, _surface_lay, _lay_path, s = _composed(a.game, cat)
    flats, models = _flats_and_models(a.game, sf, cat)
    area = tuple(int(v) for v in a.area.split(","))
    with r:
        got = measure.measure(r, s, cat, area, flats, models, a.seconds)
    print("\n".join(got.lines()))
    return 0


def _lab(a, cat):
    """fk lab (2.0.3): a design built and measured on the lab surface (fkcore.lab)."""
    from . import lab
    if a.list or not a.name:
        held = lab.slots(a.game)
        print("lab slots: " + (", ".join(f"{n} at {x},{y} ({w}x{h})"
                                         for n, (x, y, w, h) in sorted(held.items()))
                               or "none in use"))
        return 0
    r = _game_link("lab")
    if r is None:
        return 2
    size = tuple(int(v) for v in a.size.lower().split("x")) if a.size else None
    try:
        with r:
            if a.clear:
                lines = lab.clear(r, a.game, a.name)
            elif a.ore or a.copy_ore or a.clear_ore:
                to = tuple(int(float(v)) for v in a.to.split(","))
                lines = lab.ore(r, a.game, a.name, a.ore, a.area, a.amount, a.copy_ore, to,
                                a.clear_ore)
            elif a.measure is not None:
                lines = lab.measure_lab(r, a.game, a.name, a.measure, cat)
            else:
                lines = lab.build(r, a.game, a.name, a.src, size, cat)
    except lab.LabError as exc:
        print(f"lab: {exc}", file=sys.stderr)
        return 2
    print("\n".join(lines))
    return 0


def _sim(a, cat):
    """fk sim (2.0.61): the speedrun planner (fkcore.sim), on this machine."""
    from . import link
    from . import sim as simmod
    s = simmod.Sim(os.path.dirname(os.path.abspath(a.game)))
    v, rest = a.verb, list(a.rest)

    def one(what):
        if len(rest) != 1:
            raise simmod.SimError(f"fk sim {v} {what}")
        return rest[0]
    try:
        if v == "start":
            spectate = "--spectate" in rest
            names = [w for w in rest if w != "--spectate"]
            print(s.start(names[0] if names else "start", spectate=spectate))
        elif v == "stop":
            print(s.stop())
        elif v == "status":
            st = s.state()
            print("sim: not running" if st is None else
                  f"sim: running from {st['save']} since {st['started']}, at tick {s.tick()}, "
                  f"speed {s.lua('rcon.print(game.speed)').strip()}"
                  + (f"; join to watch at {s.address()}" if st.get("spectate") else ""))
            print("save states: " + (", ".join(s.names()) or "none"))
        elif v == "saves":
            print("save states: " + (", ".join(s.names()) or "none"))
        elif v == "save":
            print(s.save(one("NAME")))
        elif v == "load":
            print(s.load(one("NAME")))
        elif v == "speed":
            print(s.speed(one("X")))
        elif v == "run":
            print(s.run(ticks=int(one("N")))[1])
        elif v == "until":
            print(s.run(until=int(one("T")))[1])
        elif v in ("place", "paste"):
            if len(rest) != 2:
                raise simmod.SimError(f"fk sim {v} FILE @X,Y")
            print((s.place if v == "place" else s.paste)(rest[0], simmod._xy(rest[1]), cat))
        elif v in ("remove", "decon"):
            area = simmod._area(one("X0,Y0,X1,Y1"))
            print(s.remove(area) if v == "remove" else s.decon(area))
        elif v == "play":
            path = one("SCRIPT")
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            print("\n".join(simmod.play(s, text, cat, os.path.dirname(os.path.abspath(path)))))
        elif v == "explore":
            print("\n".join(_explore(s, rest)))
        elif v == "explore-game":
            # fk sim game CMD ... in explore mode: the command into the episode's trajectory
            s.explore_game(json.loads(one("JSON")))
        elif v == "env":
            print(json.dumps(s.env()))
        elif v == "add-save":
            if len(rest) != 2:
                raise simmod.SimError("fk sim add-save FILE NAME")
            print(s.add_save(rest[0], rest[1]))
        else:
            raise simmod.SimError(f"fk sim {v}: start, stop, status, save, load, saves, speed, "
                                  "run, until, place, paste, remove, decon, play, explore")
    except (simmod.SimError, OSError, link.LinkError, ValueError) as exc:
        print(f"sim: {exc}", file=sys.stderr)
        return 2
    return 0


def _explore(s, rest):
    """fk sim explore on|next|status|off|archive|best|reset (2.0.64, fkcore.explore)."""
    from . import explore
    from . import sim as simmod
    verb = rest[0] if rest else "status"
    opts, args, i = {}, [], 1
    while i < len(rest):
        if rest[i].startswith("--") and i + 1 < len(rest):
            opts[rest[i][2:]] = rest[i + 1]
            i += 2
        else:
            args.append(rest[i])
            i += 1
    num = lambda k, f: f(opts[k]) if k in opts else None     # noqa: E731
    if verb == "on":
        auto = opts.get("auto")
        return s.explore_on(base=num("base", float), dist=num("dist", int),
                            budget=num("budget", int), chunk=num("chunk", int),
                            prior=num("prior", int), seed=num("seed", int),
                            minutes=num("minutes", float),
                            auto=None if auto is None else int(auto not in ("off", "0", "no")),
                            pick=opts.get("pick"), rise=num("rise", float),
                            steps=num("steps", int))
    if verb == "next":
        return s.explore_next(num("seed", int))
    if verb == "off":
        return [s.explore_off()]
    arch = s.archive()
    base = float(arch.settings["base"])
    if verb == "status":
        # the player's view: the round and its manifest, never the cells (2.0.65)
        ep, st = arch.episode, arch.settings
        out = [f"explore: {'on' if arch.on else 'off'}; settings "
               + ", ".join(f"{k} {v}" for k, v in sorted(st.items()))]
        if ep is not None:
            tick = s.tick() if s.state() else ep["start"]
            used = tick - int(ep["start"])
            mins = (time.time() - float(ep.get("wall", time.time()))) / 60
            out.append(f"  round {ep['n']}: {used:,} ticks in"
                       + (f" of {int(st['budget']):,}" if int(st["budget"] or 0) else "")
                       + f", {mins:.0f} min"
                       + (f" of {float(st['minutes']):g}" if float(st.get('minutes') or 0) else "")
                       + (f"; over: {ep['ended']}" if ep.get("ended") else ""))
            out.append(f"  manifest: {arch.goal_text()}")
        return out
    if verb == "archive":
        n = int(args[0]) if args else 15
        pos, path = arch.positions()
        if pos:
            # 2.0.67: the map, highest V first; * marks the best path
            ranked = sorted(arch.cells.items(), key=lambda kv: (-pos[kv[0]][0],
                                                                int(kv[1]["tick"])))[:n]
            out = [f"explore archive: {len(arch.cells)} cells at base {base:g}, highest V first "
                   f"(V: log2 of worth in raw units, research folded in; H: off the best path, "
                   f"{len(path)} cells long, marked *)"]
        else:
            ranked = arch.best(n)
            out = [f"explore archive: {len(arch.cells)} cells at base {base:g}, the furthest first "
                   "(levels in all, then the earliest tick)"]
        on = set(path)
        for k, row in ranked:
            top = sorted(row["levels"].items(), key=lambda kv: -kv[1])[:5]
            vh = (f"V {pos[k][0]:6.2f}  H {pos[k][1]:>3}  {'*' if k in on else ' '} "
                  if pos else "")
            out.append(f"  {row['save']:<6} tick {row['tick']:>8}  {vh}"
                       f"{len(row['traj']):>3} actions  visits {row.get('visits', 0)}  "
                       + ", ".join(f"{i} {explore.count_text(l, base)}" for i, l in top))
        return out
    if verb == "best":
        if args:
            k = next((k for k, row in arch.cells.items() if row["save"] == args[0]), None)
            if k is None:
                raise simmod.SimError(f"no cell {args[0]} (fk sim explore archive)")
        else:
            k = arch.best(1)[0][0]
        text = arch.plan(k)
        to = opts.get("to")
        if to:
            with open(to, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            return [f"explore: the way to cell {arch.cells[k]['save']} written to {to} "
                    "(fk sim play plays it)"]
        return [text.rstrip("\n")]
    if verb == "reset":
        if arch.on:
            raise simmod.SimError("fk sim explore off first")
        import shutil
        shutil.rmtree(arch.root, ignore_errors=True)
        return ["explore: archive cleared (its save states x<k> stay in sim\\write\\saves)"]
    raise simmod.SimError("fk sim explore on [--base 2] [--dist 4] [--budget 36000] "
                          "[--minutes M] [--auto on|off] [--pick grid|uniform|track] [--rise 0.1] "
                          "[--steps 12] [--chunk 600] "
                          "[--prior K] | next | "
                          "status | off | archive [N] | "
                          "best [CELL] [--to plan.sim] | reset")


def _adopt(a, cat):
    import math
    from . import adopt, link, scene
    factory, _, net = a.into.partition("/")
    if not net:
        print("error: --into FACTORY/NET", file=sys.stderr)
        return 2
    # 2.2 (FRICTION ~4480k): a tile is the floor of a position, so -128.5 is tile -129 (int()
    # truncated it to -128, the tile beside it)
    tiles = [tuple(math.floor(float(v)) for v in t.split(",")) for t in a.tiles.replace(";", " ").split()]
    sf, surface_lay, lay_path, s = _composed(a.game, cat)
    place = next((p for p in sf.places.values() if p.factory == factory), None)
    if place is None or factory not in surface_lay.plots:
        print(f"error: no factory {factory} on {sf.name}", file=sys.stderr)
        return 2
    r = _game_link("adopt")
    if r is None:
        return 2
    xs, ys = [t[0] for t in tiles], [t[1] for t in tiles]
    with r:
        live = scene.from_capture(link.snapshot(r, sf.name, (min(xs) - 1, min(ys) - 1,
                                                            max(xs) + 2, max(ys) + 2), "adopt"))
    things = [t for t in live["things"] if (math.floor(t["x"]), math.floor(t["y"])) in set(tiles)
              and not t["ghost"]]
    if len(things) != len(tiles):
        print(f"error: found {len(things)} built entities on {len(tiles)} tiles", file=sys.stderr)
        return 2
    fpath = os.path.join(a.game, f"{place.module}.fkp")
    lay = fkp.load(fpath)
    try:
        keys, covered = adopt.adopt(lay, surface_lay.plots[factory], things, net, cat,
                                    surface_lay.marks)
    except adopt.AdoptError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    fkp.save(lay, fpath, cat)
    for name in covered:
        surface_lay.marks.pop(name, None)
    fkp.save(surface_lay, lay_path, cat)
    print(f"adopted into {factory}/{net}: {', '.join(keys)}"
          + (f"; marks cleared: {', '.join(covered)}" if covered else ""))
    return 0


def _read_terrain(a):
    """Read the bare world through the game link (FK_LINK=on) into <name>.terrain.json."""
    from . import link, terrain
    if os.environ.get("FK_LINK", "off").lower() != "on":
        print("read-terrain: skipped, FK_LINK is not on (FK_LINK=on uses the game link)",
              file=sys.stderr)
        return 2
    area = tuple(int(v) for v in a.area.split(","))
    if len(area) != 4 or area[2] <= area[0] or area[3] <= area[1]:
        print(f"error: --area X0,Y0,X1,Y1 with X1 > X0 and Y1 > Y0, got {a.area}", file=sys.stderr)
        return 2
    try:
        with link.Rcon() as r:
            data = terrain.read(r, a.surface, area)
    except (OSError, link.LinkError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    os.makedirs(a.game, exist_ok=True)
    path = os.path.join(a.game, f"{a.name}.terrain.json")
    got, note = terrain.from_dict(data), None
    # 2.1.3: an area is read into the file, never over it (the 10-29 run's north read dropped the
    # whole base's terrain); --replace writes the one area alone, as before
    if os.path.exists(path) and not a.replace:
        try:
            had = terrain.load(path)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            had, note = None, f"the old {a.name}.terrain.json did not load ({exc}): replaced"
        if had is not None and had.surface != got.surface:
            had, note = None, f"the old file is of surface {had.surface}: replaced"
        if had is not None:
            got = terrain.combine(had, got)
            note = f"merged into the old {','.join(str(v) for v in had.area)}"
    terrain.save(got, path)
    print("\n".join(terrain.summary(got, a.name)))
    if note:
        print(note)
    print(f"wrote {path}")
    return 0


def _plan(a, cat):
    """``plan ITEM RATE`` or ``plan ITEM --machines N`` (fkcore.plan): Rory, 2026-10-21, asked
    whether an agent can easily work out how many miners 15 blue science assemblers need."""
    try:
        rate = None if a.rate is None else plan.parse_rate(a.rate)
        p = plan.plan(cat, a.item, rate, a.machines, a.asm, a.furnace, a.mining_productivity,
                      plan.parse_recipes(a.recipe), [x for x in a.raw.split(",") if x])
    except plan.PlanError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(plan.report(p))
    return 0


def _render(lay, cat):
    w, h = lay.size
    surface = fkp.loads(f"fkp 1 surface view\nplot {lay.name} at 0,0 rot 0 size {w}x{h}\n")
    return compose.render(compose.compose(surface, {lay.name: lay}, cat), cat, (0, 0, w, h))


def _glue(argv):
    """argparse reads ``--area -320,-288,256,288`` as two options: glue a coordinate list that
    starts with a minus to its flag (``--area=-320,...``)."""
    if argv and argv[0] == "request":
        # ``request GAME -30,74 ITEM N``: a position that starts with a minus is no option
        opts, pos, i = [], [], 1
        while i < len(argv):
            if argv[i] in ("--name", "--inventory") and i + 1 < len(argv):
                opts += argv[i:i + 2]
                i += 2
            elif argv[i].startswith(("--name=", "--inventory=")) or argv[i] in ("-h", "--help"):
                opts.append(argv[i])
                i += 1
            else:
                pos.append(argv[i])
                i += 1
        return ["request"] + opts + (["--"] + pos if pos else [])
    out, i = [], 0
    while i < len(argv):
        if argv[i] in ("--area", "--at") and i + 1 < len(argv) \
                and re.fullmatch(r"-\d+(,-?\d+)+", argv[i + 1]):
            out.append(f"{argv[i]}={argv[i + 1]}")
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out


def _design(a):
    """3.0 ``fk status|hold|unhold|unroute|seed``: the design database's net files
    (``fkcore.design``), read even while a build runs; a hold written now is taken when the router
    reaches that net."""
    from . import design
    out = os.path.join(a.game, "out")
    factories = design.factories(out)
    if not factories:
        print(f"error: no net files under {os.path.join(out, 'design')}: no route step has run "
              "since 3.0", file=sys.stderr)
        return 2
    if a.cmd == "status":
        for f in factories:
            if a.net is None or a.net in design.read_all(out, f):
                print("\n".join(design.status_lines(out, f, a.net)))
        return 0
    where = [f for f in factories if a.net in design.read_all(out, f)]
    if a.cmd == "seed" and a.pieces and not where and len(factories) == 1:
        where = factories
    if not where:
        print(f"error: no net file for {a.net} (fk status {a.game} lists them)", file=sys.stderr)
        return 2
    if a.cmd == "seed":
        ents = None
        if a.pieces:
            try:
                lay = fkp.loads("fkp 1 factory x\n" + "\n".join(
                    f"e ~seed/{k} {p}" for k, p in enumerate(a.pieces)) + "\n")
            except ValueError as exc:
                print(f"error: a piece is 'NAME X,Y DIR [ug=in|out]', as a net file writes "
                      f"its carriers: {exc}", file=sys.stderr)
                return 2
            ents = [lay.entities[f"~seed/{k}"] for k in range(len(a.pieces))]
        for f in where:
            if design.seed(out, f, a.net, ents) is None:
                print(f"error: {f} net {a.net} has no carriers to keep: give the pieces "
                      "(fk seed NET 'splitter 11,30 S')", file=sys.stderr)
                return 2
            n = len(ents) if ents is not None else len(design.read_all(out, f)[a.net]["ents"])
            print(f"{f} net {a.net}: seeded with {n} carriers; the next build lays the rest of "
                  "it through them (fk route NET does it now)")
        return 0
    status, why = {"hold": ("held", ""), "unhold": ("routed", ""),
                   "unroute": ("unrouted", design.UNROUTED_BY_YOU + " (fk unroute)")}[a.cmd]
    for f in where:
        design.set_status(out, f, a.net, status, why)
        print(f"{f} net {a.net}: {status}" + {
            "hold": "; its route stands as it is in every build until fk unhold",
            "unhold": "; the next build judges its route as any other",
            "unroute": "; the next build routes it again"}[a.cmd])
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m fkcore", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check")
    c.add_argument("file")
    ms = sub.add_parser("migrate-surface", description="write the world design (the root .fk) from "
                                                       "a game codebase's place lines, and add "
                                                       "`design` to its surface file (2.0.43, "
                                                       "PLAN-GLOBAL phase B)")
    ms.add_argument("game")
    ct = sub.add_parser("contract", description="every sealed module's contract: each port's "
                        "direction, item, rate at full speed, where it crosses and what it got "
                        "(fkcore.contract)")
    ct.add_argument("file")
    r = sub.add_parser("rates")
    r.add_argument("file")
    r.add_argument("--unbounded", default="")
    r.add_argument("--share", default="appetite", choices=("appetite", "level"))
    r.add_argument("--chain", default="")
    r.add_argument("--hand", type=int, default=None)
    r.add_argument("--bulk-hand", type=int, default=None)
    pl = sub.add_parser("plan", description="the machines, furnaces and drills a rate takes, "
                        "per item of its recipe tree (fkcore.plan)")
    pl.add_argument("item")
    pl.add_argument("rate", nargs="?", help="1/s, 60/min or 0.5/h (a bare number is /s)")
    pl.add_argument("--machines", type=float, metavar="N",
                    help="what N machines of ITEM's recipe make")
    pl.add_argument("--asm", default="assembling-machine-2", choices=plan.ASSEMBLERS)
    pl.add_argument("--furnace", default="stone-furnace", choices=plan.FURNACES)
    pl.add_argument("--mining-productivity", type=int, default=0, metavar="LEVEL",
                    help="the research level: +10%% ore a level")
    pl.add_argument("--recipe", default="", metavar="ITEM=RECIPE,...",
                    help="make ITEM with RECIPE (petroleum-gas=advanced-oil-processing)")
    pl.add_argument("--raw", default="", metavar="ITEM,...",
                    help="items that come in from elsewhere: not planned further")
    lo = sub.add_parser("layout")
    lo.add_argument("file")
    lo.add_argument("--render", action="store_true")
    for name in ("explain", "unfold"):
        p = sub.add_parser(name)
        p.add_argument("file")
        p.add_argument("inst")
    rd = sub.add_parser("render")
    rd.add_argument("file")
    b = sub.add_parser("build")
    b.add_argument("script")
    b.add_argument("--from", dest="frm")
    b.add_argument("--to")
    b.add_argument("--with", dest="extra", action="append", default=[], metavar="'STEP OPTS'",
                   help="options added to STEP for this build only (repeatable)")
    b.add_argument("--reroute", default="", metavar="NET[,NET]",
                   help="route these nets (or every net inside an instance) again: "
                        "--with 'route --again NET'")
    b.add_argument("--confirm", action="store_true",
                   help="take up what stands built on DNP pieces (diff and apply --confirm)")
    sp = sub.add_parser("step", help="3.0: one step of the build script alone, on what the steps "
                                       "before it left (fk route NET, fk place)")
    sp.add_argument("script")
    sp.add_argument("step", help="a step of the script: route, place, report_checks, ...")
    sp.add_argument("nets", nargs="?", default="",
                    help="route only: NET[,NET] (or an instance, every net in it) to lay again")
    sp.add_argument("--with", dest="extra", action="append", default=[], metavar="'STEP OPTS'")
    sp.add_argument("--try", dest="try_", nargs=2, metavar="X,Y",
                    help="how a belt would go from tile A to tile B here; nothing laid")
    st = sub.add_parser("status", help="3.0: the nets as the route step left them (out/design)")
    st.add_argument("game")
    st.add_argument("net", nargs="?")
    for name, what in (("hold", "keep a net's route as it stands, whatever the router would do"),
                       ("unhold", "let the router judge a held net again"),
                       ("unroute", "drop a net's route: the next build routes it again")):
        p = sub.add_parser(name, help=f"3.0: {what}")
        p.add_argument("game")
        p.add_argument("net")
    sd = sub.add_parser("seed", help="3.0: pin carriers of a net (a splitter, a belt) for the "
                                     "router to lay the rest of it through")
    sd.add_argument("game")
    sd.add_argument("net")
    sd.add_argument("pieces", nargs="*", metavar="'NAME X,Y DIR'",
                    help="the carriers to pin (default: the net file's carriers as they stand)")
    lk = sub.add_parser("look")
    lk.add_argument("game")
    lk.add_argument("--source", default="design", choices=("design", "world"))
    lk.add_argument("--area")
    lk.add_argument("--notify", choices=("nearby", "all", "off"))
    lk.add_argument("--watch", help="strict | keep | revert | mark DUR, or plots=A,sanctioned=B")
    lk.add_argument("--write-marks", action="store_true")
    lk.add_argument("--zoom", type=int, default=1, choices=(1, 2, 4, 8, 16, 32),
                    help="tiles a cell (2.1): 1 draws every tile; from 8 up, a map with plots")
    lk.add_argument("--layers", default="", metavar="NAME[,NAME]|all",
                    help="robots, power, defense, pollution, pipes, recipes, tags, or all")
    lk.add_argument("--image", nargs="?", const="", default=None, metavar="PATH",
                    help="write a PNG instead of the text renders (default "
                         "<game>/out/look-X0_Y0_X1_Y1-zN.png) and print its path")
    wt = sub.add_parser("wait", description="a bounded wait for the ghosts in an area (fkcore.wait)")
    wt.add_argument("game")
    wt.add_argument("what", choices=("built",))
    wt.add_argument("--area")
    wt.add_argument("--name", default="", metavar="ENTITY[,...]",
                    help="wait only for these entities' ghosts")
    wt.add_argument("--max", default="5m", metavar="DUR",
                    help="the bound: 90s, 5m or 1h (default 5m)")
    wt.add_argument("--poll", default="10s", metavar="DUR",
                    help="how often to read the world (default 10s)")
    fr = sub.add_parser("fresh")
    fr.add_argument("game")
    fr.add_argument("--area", required=True)
    al = sub.add_parser("alerts")
    al.add_argument("game")
    al.add_argument("--force")
    st = sub.add_parser("stats")
    st.add_argument("game")
    st.add_argument("--items")
    st.add_argument("--window", default="1min")
    st.add_argument("--fluids", action="store_true")
    st.add_argument("--series", type=int, default=0, choices=range(0, 301), metavar="N")
    st.add_argument("--force")
    st.add_argument("--sample", action="store_true",
                    help="take the run's stats sample when one is due (fkcore.runstats)")
    st.add_argument("--now", action="store_true", help="with --sample: take it now")
    st.add_argument("--history", action="store_true",
                    help="the run's history: research, the latest window and the trend")
    st.add_argument("--page", action="store_true",
                    help="write the run's stats page, out/stats/index.html")
    ch = sub.add_parser("chart")
    ch.add_argument("game")
    ch.add_argument("--force")
    sk = sub.add_parser("stock", description="what the base holds, item by item: in chests and "
                                             "in roboports (a free read, never an alert)")
    sk.add_argument("game")
    sk.add_argument("--force")
    ca = sub.add_parser("capital", description="working capital: what the base owns in raw "
                                               "units, working against idle (a free read, "
                                               "never an alert)")
    ca.add_argument("game")
    ca.add_argument("--area")
    ca.add_argument("--brief", action="store_true")
    ca.add_argument("--force")
    ca.add_argument("--park")
    ca.add_argument("--why")
    ca.add_argument("--unpark", type=int)
    ca.add_argument("--weight")
    se = sub.add_parser("sense")
    se.add_argument("game")
    se.add_argument("capture")
    se.add_argument("--area", required=True)
    se.add_argument("--write-marks", action="store_true")
    sp = sub.add_parser("split-to-chest")
    sp.add_argument("game")
    sp.add_argument("factory")
    sp.add_argument("net")
    sp.add_argument("name")
    sp.add_argument("--at", required=True)
    sp.add_argument("--requester", action="store_true")
    sp.add_argument("--item")
    sp.add_argument("--rate")
    mg = sub.add_parser("merge")
    mg.add_argument("game")
    mg.add_argument("names")
    mg.add_argument("--into", required=True)
    ins = sub.add_parser("insert")
    ins.add_argument("game")
    ins.add_argument("component_dir")
    ins.add_argument("name")
    ins.add_argument("--into", required=True)
    ins.add_argument("--at", required=True)
    c2n = sub.add_parser("chests-to-net")
    c2n.add_argument("game")
    c2n.add_argument("factory")
    c2n.add_argument("provider")
    c2n.add_argument("requester")
    c2n.add_argument("--net", default="link")
    n2c = sub.add_parser("net-to-chests")
    n2c.add_argument("game")
    n2c.add_argument("factory")
    n2c.add_argument("net")
    n2c.add_argument("--provider", required=True)
    n2c.add_argument("--requester", required=True)
    me = sub.add_parser("measure")
    me.add_argument("game")
    me.add_argument("--area", required=True)
    me.add_argument("--seconds", type=float, default=30.0)
    sm = sub.add_parser("sim")
    sm.add_argument("game")
    sm.add_argument("verb")
    sm.add_argument("rest", nargs=argparse.REMAINDER)
    lb = sub.add_parser("lab")
    lb.add_argument("game")
    lb.add_argument("name", nargs="?")
    lb.add_argument("--from", dest="src")
    lb.add_argument("--size")
    lb.add_argument("--measure", type=float)
    lb.add_argument("--clear", action="store_true")
    lb.add_argument("--list", action="store_true")
    lb.add_argument("--ore")                      # 2.0.56: RESOURCE over --area
    lb.add_argument("--area")
    lb.add_argument("--amount", type=int, default=100000)
    lb.add_argument("--copy-ore", dest="copy_ore")    # X0,Y0,X1,Y1 of the world
    lb.add_argument("--to", default="0,0")
    lb.add_argument("--clear-ore", dest="clear_ore", action="store_true")
    ad = sub.add_parser("adopt")
    ad.add_argument("game")
    ad.add_argument("tiles")
    ad.add_argument("--into", required=True)
    ch = sub.add_parser("chat")
    ch.add_argument("game")
    ch.add_argument("--all", action="store_true")
    sy = sub.add_parser("say")
    sy.add_argument("text", nargs="+")
    ak = sub.add_parser("ack")
    ak.add_argument("game")
    po = sub.add_parser("poll")
    po.add_argument("game")
    dn = sub.add_parser("dnp", description="do not populate: a piece of the design laid out and "
                        "routed but not built (or --off: built again)")
    dn.add_argument("file")
    dn.add_argument("names", metavar="NAME[,NAME]")
    dn.add_argument("--off", action="store_true")
    dc = sub.add_parser("decon", description="the deconstruction planner over an area: the "
                        "robots take it up (or --cancel the orders)")
    dc.add_argument("game")
    dc.add_argument("--area", required=True)
    dc.add_argument("--name", default="", metavar="ENTITY[,...]", help="only these")
    dc.add_argument("--cancel", action="store_true")
    rq = sub.add_parser("request", description="an item request: robots bring N of ITEM from "
                        "the network to the entity that stands at X,Y (or the design's KEY)")
    rq.add_argument("game")
    rq.add_argument("entity", metavar="X,Y|KEY")
    rq.add_argument("item")
    rq.add_argument("count", type=int, metavar="N")
    rq.add_argument("--name", default="", metavar="ENTITY", help="the entity's prototype there")
    rq.add_argument("--inventory", default="", metavar="NAME",
                    help="a defines.inventory name (default: the one its kind takes ITEM in)")
    cp = sub.add_parser("capture", description="something built, as a Literal file that can go "
                        "anywhere (fkcore.capture)")
    cp.add_argument("game")
    cp.add_argument("source", choices=("lab", "world", "design"))
    cp.add_argument("what", help="lab: a slot's NAME; world: X0,Y0,X1,Y1; design: "
                    "[FACTORY/]INST")
    cp.add_argument("--to", help="the file (default captured/<what>.json, in GAME_DIR)")
    cp.add_argument("--fkp", help="design: the layout to read (default the committed one)")
    lu = sub.add_parser("lua")
    lu.add_argument("game")
    lu.add_argument("code", nargs="?")
    lu.add_argument("--file")
    rs = sub.add_parser("research")
    rs.add_argument("game")
    rs.add_argument("techs", nargs="*")
    rs.add_argument("--first", action="store_true",
                    help="put them (and their prerequisites) at the front of the queue")
    th = sub.add_parser("tech")
    th.add_argument("game")
    th.add_argument("names", nargs="+")
    av = sub.add_parser("avail", description="what inserter=any, --hand any and --pole any can "
                        "build now (fkcore.avail); --save writes out/avail.json for the next build")
    av.add_argument("game")
    av.add_argument("--save", action="store_true")
    rc = sub.add_parser("recipes")
    rc.add_argument("game")
    rc.add_argument("names", nargs="*")
    rc.add_argument("--enabled", action="store_true")
    rt = sub.add_parser("read-terrain")
    rt.add_argument("game")
    rt.add_argument("--surface", required=True)
    rt.add_argument("--area", required=True)
    rt.add_argument("--name", default="map")
    rt.add_argument("--replace", action="store_true")
    sg = sub.add_parser("stage", add_help=False)
    sg.add_argument("game")
    sg.add_argument("rest", nargs=argparse.REMAINDER)
    a = ap.parse_args(_glue(sys.argv[1:] if argv is None else list(argv)))
    if a.cmd == "stage":
        from . import stage
        return stage.main(a.game, a.rest)
    if a.cmd in ("chat", "say", "poll", "ack"):
        return _talk(a)
    if a.cmd in ("research", "tech", "recipes"):
        return _research(a)
    if a.cmd == "avail":
        return _avail(a)
    if a.cmd == "lua":
        return _lua(a)
    if a.cmd == "read-terrain":
        return _read_terrain(a)
    cat = Catalog.load()
    if a.cmd == "migrate-surface":
        return _migrate_surface(a)
    if a.cmd in ("merge", "insert", "chests-to-net", "net-to-chests"):
        return _rewire(a)
    if a.cmd == "measure":
        return _measure(a, cat)
    if a.cmd == "sim":
        return _sim(a, cat)
    if a.cmd == "lab":
        return _lab(a, cat)
    if a.cmd == "capture":
        return _capture(a, cat)
    if a.cmd == "decon":
        return _decon(a, cat)
    if a.cmd == "request":
        return _request(a, cat)
    if a.cmd == "dnp":
        return _dnp(a, cat)
    if a.cmd == "adopt":
        return _adopt(a, cat)

    if a.cmd == "plan":
        return _plan(a, cat)
    if a.cmd == "render":
        print(_render(fkp.load(a.file), cat))
        return 0
    if a.cmd == "build":
        try:
            extra = list(a.extra) + ([f"route --again {a.reroute}"] if a.reroute else [])
            if a.confirm:
                with open(a.script, encoding="utf-8") as fh:
                    have = {ln.cmd for ln in flow.parse(fh.read(), a.script)}
                extra += [f"{s} --confirm" for s in ("diff", "apply") if s in have]
            code, _ctx = flow.run(a.script, a.frm, a.to, cat, extra=extra)
        except (flow.FlowError, surfacemod.SurfaceError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return code
    if a.cmd in ("status", "hold", "unhold", "unroute", "seed"):
        return _design(a)
    if a.cmd == "step":
        # 3.0 (PLAN-3.0 §2, every step a command): the script's own line for that step, alone;
        # the nets named are laid again, every other route kept from the net files
        try:
            extra = list(a.extra) + ([f"route --again {a.nets}"] if a.nets else [])
            if a.nets and a.step != "route":
                raise flow.FlowError("nets go with the route step only")
            if a.try_:
                if a.step != "route" or a.nets:
                    raise flow.FlowError("--try goes alone: fk route --try X,Y X,Y")
                extra.append(f"route --try {a.try_[0]}:{a.try_[1]}")
            code, _ctx = flow.run(a.script, a.step, a.step, cat, extra=extra)
        except (flow.FlowError, surfacemod.SurfaceError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return code
    if a.cmd == "look":
        return _look(a, cat)
    if a.cmd == "wait":
        return _wait(a, cat)
    if a.cmd == "alerts":
        return _alerts(a, cat)
    if a.cmd == "fresh":
        from . import fresh
        return _world(a, cat, "fresh", lambda r: fresh.run(r, a.game, cat, _area(a.area)))
    if a.cmd in ("stats", "chart", "stock"):
        return _free_read(a, cat)
    if a.cmd == "capital":
        return _capital(a, cat)
    if a.cmd == "sense":
        return _sense(a, cat)
    if a.cmd == "split-to-chest":
        from . import merge
        at = tuple(int(v) for v in a.at.split(","))
        try:
            ch = merge.split_to_chest(a.game, a.factory, a.net, a.name, at,
                                      "requester" if a.requester else "provider", a.item, a.rate)
        except merge.MergeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(ch)
        return 0
    flat = _flat(a.file)
    if a.cmd in ("layout", "explain", "unfold"):
        previous = fkp.load(_fkp_path(a.file)) if os.path.exists(_fkp_path(a.file)) else None
        lay, notes = expand.expand_factory(flat, cat, previous)
        if a.cmd == "layout":
            try:
                fkp.save(lay, _fkp_path(a.file), cat)
            except fkp.InvariantError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
            print(f"layout {flat.name}: {len(lay.insts)} instances, {len(lay.entities)} "
                  f"entities, {lay.size[0]}x{lay.size[1]}, 0 invariant failures -> "
                  f"{_fkp_path(a.file)}")
            for note in notes:
                print(f"  note {note}")
            if a.render:
                print(_render(lay, cat))
            return 0
        net = a.inst.replace(".", "/")
        if a.cmd == "explain" and a.inst not in flat.insts and net in flat.nets:
            print(expand.explain_net(flat, net))      # 2.1: a net, and what limits it
            return 0
        if a.inst not in flat.insts:
            print(f"error: no instance {a.inst}", file=sys.stderr)
            return 2
        if a.cmd == "explain":
            print(expand.explain(flat, lay, a.inst, cat))
            return 0
        with open(a.file, encoding="utf-8") as fh:
            src = fh.read()
        new_src, new_lay = expand.unfold(src, flat, lay, a.inst)
        fkp.save(new_lay, _fkp_path(a.file), cat)
        with open(a.file, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(new_src)
        print(f"unfold {a.inst}: now a Literal; {sum(1 for e in new_lay.entities.values() if e.owner == a.inst)} "
              "entities pinned, every key kept")
        return 0
    if a.cmd == "check":
        print(f"check {flat.name}: {len(flat.machines)} machines, {len(flat.nets)} nets, "
              f"{len(flat.checks)} check lines")
        for line in flat.checks:
            print(f"  {line}")
        from .elaborate import advice
        for line in advice(flat):
            print(f"  advice {line}")
        return 0
    if a.cmd == "contract":
        from . import contract as contractmod
        try:
            model = rates.solve(flat)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        lines = contractmod.contract(flat, model)
        print(f"contract {flat.name}: {len(lines)} sealed instances")
        for line in lines:
            print(f"  {line}")
        return 0
    unbounded = [n for n in a.unbounded.split(",") if n]
    try:
        model = rates.solve(flat, share=a.share, unbounded=unbounded)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    title = flat.name + (f" (unbounded: {', '.join(unbounded)})" if unbounded else "")
    sizing = getattr(flat, "_sizing", None)
    if sizing is not None:
        sz, note = sizing
        lines, checks = sizemod.report(sz, cat)
        print(f"{lines[0]}\n  sized {note}; fk build --to size writes out/size.txt, what "
              "each count serves")
        for c in checks:
            print(f"  {c}")
        print()
    print(rates.report(model, title))
    for path in [p for p in a.chain.split(",") if p]:
        if path not in flat.insts:
            print(f"error: no instance {path}", file=sys.stderr)
            return 2
        print()
        print("\n".join("  " + line for line in rates.chain(model, path)))
    if flat.checks:
        print("\n  checks")
        for line in flat.checks:
            print(f"  {line}")
    _routed_rates(a, flat, model, cat)
    return 0


def _routed_rates(a, flat, model, cat):
    """``fk rates``' routed section (2.0, CORE-V2 "Open"): the netlist on FILE.fkp as the last
    build laid it out, through the flow solver, as the build's second ``report_rates`` shows it.
    The hands: ``--hand``/``--bulk-hand``, else the force's as the last diff saved them
    (``out/force.json`` beside the file), else 1."""
    from . import routed
    path = _fkp_path(a.file)
    lay = fkp.load(path) if os.path.exists(path) else None
    if lay is None or not any(e.owner.startswith("~") for e in lay.entities.values()):
        print(f"\n  no routed rates: {os.path.basename(path)} has no routes yet (fk build lays "
              "them; then this shows the lanes and inserters as laid)")
        return
    hand, bulk, note = a.hand, a.bulk_hand, "inserter hands as given"
    if hand is None or bulk is None:
        saved = {}
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(a.file)), "out",
                                   "force.json"), encoding="utf-8") as fh:
                saved = json.load(fh)
            note = (f"inserter hands {saved['hand']}, bulk {saved['bulk_hand']} (the force's at "
                    "the last diff, out/force.json)")
        except (OSError, ValueError, KeyError, TypeError):
            saved = {}
            note = "inserter hands 1 (no diff has saved the force's yet; --hand N sets them)"
        hand = hand or int(saved.get("hand", 1))
        bulk = bulk or int(saved.get("bulk_hand", 1))
    r_ = routed.solve(flat, lay, cat, model, hand=hand, bulk_hand=bulk)
    lines, checks = routed.report(r_)
    print(f"\n{lines[0]}; on {os.path.basename(path)} as last laid out")
    print("\n".join(lines[1:]))
    if checks:
        print("\n  routed checks")
        for c in checks:
            print(f"  {c}")
    print(f"  {note}")


if __name__ == "__main__":
    raise SystemExit(main())
