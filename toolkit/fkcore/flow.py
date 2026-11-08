"""fkcore.flow: the build script (``<surface>.build.fkb``), run as a Vivado-style flow (CORE-V1 §7).

One contained tool per line, straight-line, no control flow. Every step reads the state the steps
before it left and writes a report, ``out/<step>.txt``. A step that changes a layout also writes a
checkpoint, ``out/<step>/<factory>.fkp`` (and ``<surface>.fkp`` once there is a floorplan).
``stop_on <check ...>`` lines (anywhere in the script) name the check codes that stop the run;
``invariant`` is always among them, because malformed data can't be written. Everything else is a
report line, never a gate.

``run(script, frm=None, to=None)`` runs the whole script or the lines from ``frm`` to ``to``.
Steps before ``frm`` that only read source (``read_surface``, ``read_terrain``, ``run_models``,
``elaborate``, ``size``) are re-run quietly: source is cheap and pure. Layouts come from the
checkpoint of the last layout step before ``frm``, and a ``floorplan`` before ``frm`` is replayed
on them (it is pure too), so ``--from diff`` on the game host applies what a laptop build laid out.
A checkpoint says what source it was laid from (``out/<step>/source.json``: the source files and
the script's lines up to its step). When that is not the source as it is now (2.0.4: a recipe
changed, and the paste laid the old one), the layout steps before ``frm`` run again instead.

Steps: read_surface, read_terrain, run_models, elaborate, size, report_rates, place, route, tier,
cover power, cover robots, floorplan, report_checks, report_bom, write_layout, diff, apply,
stop_on.

``route --only stale`` hands the router the committed routes of the nets none of whose ends moved;
``route --module X`` (PLAN-MODULES phase 4, 2.0.39) re-routes a sealed module's own nets and the
parent nets ending at its ports, and keeps every other committed route as it stands -- a rib change
never moves the rest of the base.

``size`` (1.6, ``fkcore.size``) gives every ``n=auto`` row its count: the fewest machines that
meet the surface's targets, the factories' asserts and what each row's consumers ask, all the
factories together, before anything is placed. Its report says what each count serves; ``does-not-fit``
says when a row grown that way runs past its plot or into another instance (rows grow, the
player arranges them), and report_rates after route says (``size``) when a net of a sized row
asks more of its belts than they carry.

The terrain (``read_terrain <name>``: ``<name>.terrain.json``, ``fkcore.terrain``) is the bare
world every layout step designs against: ``place`` puts each ``@patch`` MinerRow on its ore inside
its plot, ``route`` and ``cover power`` keep their carriers and poles off its blocked tiles
(water, cliffs), each factory's in its own frame, and ``floorplan`` reports ``on-water-or-cliff`` for a
composed entity on blocked ground and ``no-ore`` for a drill with no ore of its resource in
reach. They are checks, never gates. With a terrain, a lab ``apply`` seeds no ore: the ore is
the terrain's.

``diff`` and ``apply`` use the game link (``fkcore.link``: RCON, 5 s per call; the
surface file's name is the game surface, its ``force`` and ``free`` properties pick whose the
paste is and whether Lua revives it, see ``fkcore.apply``). The link is used only when ``FK_LINK=on`` is set (so a paper game run on the
game host never pastes by accident); without it, or with no RCON password file or nothing
listening, they report that they were skipped. Apart from those two steps it is pure: it reads
the game codebase and writes ``out/`` and, in write_layout, the ``.fkp`` files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import time
from dataclasses import dataclass, field

from . import arms as armsmod
from . import avail as availmod
from . import compiled as compiledmod
from . import compose, connect, design, expand, fkp, placer, rates, route, snapshot
from . import own as ownmod
from . import placements as placementsmod
from . import pack as packmod
from . import surface as surfacemod
from . import terrain as terrainmod
from .catalog import Catalog
from .elaborate import Check, ElabError, elaborate_file
from .lang import FkSyntaxError

__all__ = ["FlowError", "Ctx", "Line", "parse", "run", "STEPS", "layout_source_changes"]


class FlowError(ValueError):
    pass


@dataclass
class Line:
    n: int
    cmd: str                 # the step name: "route", "cover_power", ...
    args: list
    text: str
    later: bool = False      # 3.0: after `later:`, off the critical path (never stops a paste)


@dataclass
class Ctx:
    root: str
    script: str
    cat: Catalog
    out: str
    surface: surfacemod.SurfaceFile | None = None
    flats: dict = field(default_factory=dict)        # factory -> Flat
    layouts: dict = field(default_factory=dict)      # factory -> factory Layout (current)
    previous: dict = field(default_factory=dict)     # factory -> the committed .fkp, if any
    surface_lay: fkp.Layout | None = None
    composed: compose.Surface | None = None
    terrain: set = field(default_factory=set)        # blocked ground tiles (none on paper)
    ground: object = None                            # the terrain.Terrain, when one is read
    stop_on: set = field(default_factory=lambda: {"malformed"})
    log: list = field(default_factory=list)          # one summary line per step run
    checks: list = field(default_factory=list)       # [(step, Check)] of every step run so far
    link: object = None                              # the game link (fkcore.link.Rcon), on use
    link_error: str | None = None
    link_lent: bool = False                          # the caller's link: not closed here
    last_diff: object = None                         # the last diff's SenseReport this run
    roboports: list | None = None                    # what the last diff read stands (REACH)
    hands: tuple | None = None                       # the force's (hand, bulk hand, note), read once
    hand_requested: str | None = None                # route's --hand, as written (2.0.15)
    rates_runs: int = 0                              # report_rates steps run so far
    sized: object = None                             # the size step's fkcore.size.Sizing
    steps: list = field(default_factory=list)        # the script's step names, in order
    source: dict = field(default_factory=dict)       # {source file: sha1} as the run began
    echo: object = None                              # the run's echo, for a long step's progress
    models: dict = field(default_factory=dict)       # factory -> (flat, its rates.solve), reused
    compiled: str | None = None                      # the compiled-module store (PLAN-3.0 §4)

    def model(self, factory):
        """The nominal rate model of ``flats[factory]``, solved once per flat (2.1.27: hands, tier
        and both report_rates each solved it again, ~3 s apiece on the live world)."""
        flat = self.flats[factory]
        got = self.models.get(factory)
        if got is None or got[0] is not flat:
            got = self.models[factory] = (flat, rates.solve(flat))
        return got[1]


def parse(text, path="<fkb>") -> list:
    """The script's lines. 3.0 (Rory, 2026-11-01: "a section for non critical path stuff that
    wont block a paste, like the more expensive rate report"): the steps after a `later:` line run
    once the rest has run, the paste too; what they find is reported and never stops the build,
    and one that fails says so in its report. They only read the layout, so a layout step there
    is a script error."""
    out, later = [], False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line == "later:":
            later = True
            continue
        t = shlex.split(line)
        cmd, args = t[0], t[1:]
        if cmd == "cover":
            if not args or args[0] not in ("power", "robots"):
                raise FlowError(f"{path}:{n}: cover power|robots")
            cmd, args = f"cover_{args[0]}", args[1:]
        if cmd not in STEPS:
            raise FlowError(f"{path}:{n}: unknown step {t[0]!r} (steps: "
                            + ", ".join(sorted(s.replace('_power', ' power')
                                               .replace('_robots', ' robots') for s in STEPS)) + ")")
        if later and cmd in _NOT_LATER:
            raise FlowError(f"{path}:{n}: {t[0]} changes the layout or the game; it goes before "
                            "`later:`, whose steps only report")
        out.append(Line(n, cmd, args, line, later))
    return out


def _opts(args, **defaults):
    """--key value / --flag options -> dict (values stay strings)."""
    out, i = dict(defaults), 0
    while i < len(args):
        a = args[i]
        if a.startswith("--"):
            key = a[2:].replace("-", "_")
            if i + 1 < len(args) and not args[i + 1].startswith("--"):
                out[key] = args[i + 1]
                i += 2
            else:
                out[key] = True
                i += 1
        else:
            out.setdefault("_", []).append(a)
            i += 1
    return out


def _count(o, key, lo=0):
    """An option that must be a whole number (``--red 20``) -> int, None when not given."""
    v = o.get(key)
    if v is None:
        return None
    flag = "--" + key.replace("_", "-")
    if v is True:
        raise FlowError(f"{flag} needs a number")
    try:
        n = int(v)
    except ValueError:
        raise FlowError(f"{flag} needs a whole number, got {v!r}") from None
    if n < lo:
        raise FlowError(f"{flag} is at least {lo}, got {n}")
    return n


def _hands(ctx, o):
    """(hand, bulk hand, note) for ``--hand N|auto`` and ``--bulk-hand N|auto``: a number as given;
    ``auto`` (the default) the force's, 1 + its inserter capacity bonuses: read over the game link
    once a build, else as the last ``diff`` saved them (``out/force.json``: the agent kit designs
    on the laptop, where there is no link, and diffs on the game host), else 1 (the note says
    which)."""
    want = {}
    for key in ("hand", "bulk_hand"):
        if o.get(key, "auto") != "auto":
            want[key] = _count(o, key, lo=1)
    if len(want) < 2 and ctx.hands is None:
        ctx.hands = _force_hands(ctx)
    hand, bulk, note = ctx.hands if len(want) < 2 else (1, 1, "")
    if want:
        note = "inserter hands as given" if len(want) == 2 else note
    return want.get("hand", hand), want.get("bulk_hand", bulk), note


_FORCE_FILE = "force.json"                           # in out/: the force's hands, by diff
_STAMP_FILE = "source.json"             # in out/<step>/: the source its checkpoint was laid from
_LAID_FILE = "layout-source.json"       # in out/: the source write_layout's .fkp files came from
#: the source files of a game codebase (beside the build script): what a layout is laid from.
#: The build script itself counts by its lines up to each step; the .fkp files are its output.
_SOURCE_EXT = (".fk", ".surface", ".json", ".model.py")


def _source_files(root):
    """{file: sha1} of the game codebase's source files in ``root``."""
    out = {}
    for name in sorted(os.listdir(root)):
        full = os.path.join(root, name)
        if name.endswith(_SOURCE_EXT) and os.path.isfile(full):
            # inside a build, the bytes it holds (fkcore.snapshot): what it is laid from
            out[name] = hashlib.sha1(snapshot.read_bytes(full)).hexdigest()
    return out


def _changed(was, now):
    """The source files that differ between two ``_source_files`` reads, sorted, each with how."""
    out = []
    for name in sorted(set(was) | set(now)):
        if was.get(name) != now.get(name):
            how = "changed" if name in was and name in now else "added" if name in now \
                else "removed"
            out.append(f"{name} {how}")
    return out


def _script_sha(lines):
    return hashlib.sha1("\n".join(ln.text for ln in lines).encode()).hexdigest()


def layout_source_changes(game):
    """What changed in the source of the placed factories (their modules' ``.fk``, the surface
    file) since write_layout last wrote the ``.fkp`` files, one line each; [] when nothing did or
    no build has said what it wrote from. 2.0.4: ``fk look`` judges against the ``.fkp`` files,
    and after a paste from stale checkpoints it said ok while the machine ran the old recipe (the
    second lead run, 2026-10-22); a look now says when the .fkp is not the source's."""
    try:
        with open(os.path.join(game, "out", _LAID_FILE), encoding="utf-8") as fh:
            was = json.load(fh)["files"]
        sf = surfacemod.load_game(game)
    except (OSError, ValueError, KeyError, TypeError, surfacemod.SurfaceError):
        return []
    now = _source_files(game)
    lines = []
    for name in _changed(was, now):
        file, how = name.rsplit(" ", 1)
        mods = sorted({p.module for p in sf.places.values()
                       if p.lifecycle != "retired" and f"{p.module}.fk" == file})
        if mods:
            lines.append(f"{file} {how} since {mods[0]}.fkp was written: this look judges the "
                         f"design as last written, and the next fk build lays out the change")
        elif file.endswith(".surface"):
            lines.append(f"{file} {how} since the .fkp files were written: this look judges the "
                         f"design as last written, and the next fk build lays out the change")
    return lines


def _force_hands(ctx):
    r = _link(ctx) if ctx.surface is not None else None
    if r is not None:
        try:
            hand, bulk = _save_hands(ctx, r)
            return hand, bulk, f"inserter hands {hand}, bulk {bulk} (force {_force(ctx)}'s research)"
        except Exception as exc:                    # noqa: BLE001 - a report line, not a stop
            return 1, 1, f"inserter hands 1 (could not read the force's bonuses: {exc})"
    try:
        with open(os.path.join(ctx.out, _FORCE_FILE), encoding="utf-8") as fh:
            saved = json.load(fh)
        hand, bulk = int(saved["hand"]), int(saved["bulk_hand"])
    except (OSError, ValueError, KeyError, TypeError):
        why = ctx.link_error or "no surface read"
        return 1, 1, (f"inserter hands 1 (no game link: {why}; no diff has saved the force's "
                      f"yet; --hand N sets them)")
    return hand, bulk, (f"inserter hands {hand}, bulk {bulk} (force {saved.get('force', '?')}'s "
                        f"research at the last diff, out/{_FORCE_FILE})")


def _save_hands(ctx, r):
    """Read the force's inserter capacity bonuses over the link and save the hands in
    ``out/force.json``. -> (hand, bulk hand)."""
    force = _force(ctx)
    reply = r.lua(f"local f = game.forces['{force}'] rcon.print(f.inserter_stack_size_bonus "
                  ".. ' ' .. f.bulk_inserter_capacity_bonus)")
    a, b = (int(float(x)) for x in reply.split())
    os.makedirs(ctx.out, exist_ok=True)
    with open(os.path.join(ctx.out, _FORCE_FILE), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"force": force, "hand": 1 + a, "bulk_hand": 1 + b}, fh)
        fh.write("\n")
    return 1 + a, 1 + b


def _read_roboports(ctx, r):
    """The diff's live read of the roboports that stand with power, for ``REACH`` and
    ``UNREACHED`` (the diff's and report_bom's business, this build only; 3.1: no cover step
    reads it, see ``fk robots``): [(x, y, logistic radius, construction radius)], or None when the
    read fails."""
    from . import link, robots
    tag = f"roboports-{os.getpid()}"
    try:
        got = link.read_output(r, robots.lua_roboports(ctx.surface.name, _force(ctx), tag), tag,
                               "roboports", keep=False)
    except (link.LinkError, OSError, ValueError):
        return None
    rows = got if isinstance(got, list) else list(got.values()) if isinstance(got, dict) else []
    return [tuple(float(v) for v in p[:4]) for p in rows if len(p) >= 4]


def _factories(ctx):
    if ctx.surface is None:
        raise FlowError("no surface read yet: the script needs read_surface first")
    return [p for p in ctx.surface.places.values() if p.lifecycle != "retired"]


def _invariant_checks(where, problems):
    return [Check("malformed", where, p) for p in problems]


# -- steps: each returns (report lines, checks) ---------------------------------------------------
def s_read_surface(ctx, args):
    name = args[0] if args else None
    if not name:
        raise FlowError("read_surface <name>")
    path = os.path.join(ctx.root, f"{name}.surface")
    ctx.surface = surfacemod.load(path)
    sf = ctx.surface
    lines = [f"surface {sf.name}: {'free, ' if sf.free else ''}force {sf.force_name}, terrain "
             f"{sf.terrain}, catalog {sf.catalog}, notify {sf.notify_mode}, watch plots "
             f"{sf.watch['plots']} / sanctioned {sf.watch['sanctioned']}"]
    lines += [f"  note: {n}" for n in sf.notes]
    if sf.design:
        lines.append(f"design {sf.design}: {len(sf.places)} plots from its top-level sealed "
                     f"instances" + (f" (the {len(sf.lines_places)} place lines are not read "
                                     "while a design is present)" if sf.lines_places else ""))
    for p in sf.places.values():
        lines.append(f"  place {p.factory} = {p.module} plot {p.x},{p.y} {p.w}x{p.h} rot {p.rot}"
                     + (f" gap {p.gap}" if p.gap else "")
                     + (f" patch {p.patch}" if p.patch else "")
                     + f" ({p.lifecycle})")
    for item, r in sf.targets.items():
        lines.append(f"  target {item} {r * 60:g}/min")
    checks = [Check("design", f"{sf.name} plot {note.split(':', 1)[0]}",
                    note.split(": ", 1)[-1]) for note in sf.design_notes]
    for p in sf.places.values():
        prev = os.path.join(ctx.root, f"{p.module}.fkp")
        if os.path.exists(prev):
            ctx.previous[p.factory] = fkp.load(prev)
    return lines, checks


def s_read_terrain(ctx, args):
    name = args[0] if args else "none"
    if name == "none":
        return ["terrain none: no ore, water or cliffs constrain the layout (a paper surface)"], []
    path = os.path.join(ctx.root, f"{name}.terrain.json")
    if not os.path.exists(path):
        return [f"terrain {name}: no {name}.terrain.json in the game codebase"], [
            Check("no-terrain", name, "no terrain file; the layout is not checked against the ground")]
    t = terrainmod.load(path)
    ctx.ground = t
    ctx.terrain = set(t.blocked)
    lines, checks = terrainmod.summary(t, name), []
    if ctx.surface is None:
        return lines, checks
    if t.surface != ctx.surface.name:
        checks.append(Check("no-terrain", name, f"read from surface {t.surface}; this surface is "
                                             f"{ctx.surface.name}"))
    x0, y0, x1, y1 = t.area
    for p in _factories(ctx):
        outside = p.w * p.h - (max(0, min(x1, p.x + p.w) - max(x0, p.x))
                               * max(0, min(y1, p.y + p.h) - max(y0, p.y)))
        lines.append("  " + _plot_ground(t, p))
        if outside:
            checks.append(Check("no-terrain", f"{name} plot {p.factory}",
                                f"{outside} of its {p.w * p.h} tiles are outside the terrain's "
                                "area: unknown ground"))
    return lines, checks


def _plot_ground(t, p):
    """One line: what the terrain holds inside a plot (blocked ground, ore by patch)."""
    def inside(tile):
        return p.x <= tile[0] < p.x + p.w and p.y <= tile[1] < p.y + p.h

    parts = [f"{sum(1 for b in t.water if inside(b))} water",
             f"{sum(1 for b in t.cliff_tiles if inside(b))} cliff",
             f"{sum(1 for b in t.other if inside(b))} other blocked tiles"]
    if t.entities_read is not None:
        parts.append(f"{sum(1 for b in t.entities if inside(b))} standing-entity tiles "
                     "(read-terrain --entities)")
    for res, ps in sorted(t.patches.items()):
        held = [(pt.n, sum(1 for tile in pt.tiles if inside(tile))) for pt in ps]
        held = [(n, k) for n, k in held if k]
        if held:
            parts.append(f"{res} " + ", ".join(f"#{n} {k} tiles" for n, k in held))
    return f"plot {p.factory} at {p.x},{p.y} {p.w}x{p.h}: " + "; ".join(parts)


def s_run_models(ctx, args):
    found = [p.module for p in _factories(ctx)
             if os.path.exists(os.path.join(ctx.root, f"{p.module}.model.py"))]
    if not found:
        return ["no model scripts (<module>.model.py); parameters are as written in the .fk"], []
    return [f"model scripts found but not run in V1 yet: {', '.join(found)}"], [
        Check("model", ", ".join(found), "model scripts are not run yet")]


def s_elaborate(ctx, args):
    lines, checks = [], []
    for p in _factories(ctx):
        path = os.path.join(ctx.root, f"{p.module}.fk")
        try:
            flat = elaborate_file(path, ctx.cat, name=p.module, layout=ctx.previous.get(p.factory))
        except (FkSyntaxError, ElabError, OSError) as exc:
            return lines, [Check("malformed", p.factory, f"no netlist: {exc}")]
        ctx.flats[p.factory] = flat
        sushi = sum(n.sushi for n in flat.nets.values())
        autos = [path for path, inst in flat.insts.items() if inst.auto]
        lines.append(f"{p.factory}: {len(flat.insts)} instances, {len(flat.machines)} machines, "
                     f"{len(flat.nets)} nets ({sushi} sushi), {len(flat.checks)} check lines"
                     + (f"; {len(autos)} n=auto rows (at the count their .fkp laid out, or their "
                        "low bound, until the size step counts them)" if autos else ""))
        checks += [Check(c.code, f"{p.factory} {c.where}", c.msg, c.line) for c in flat.checks]
        if autos and "size" not in ctx.steps:
            checks.append(Check("size", p.factory, f"n=auto rows ({', '.join(autos)}) and no size "
                                "step in the build script: each keeps the count its .fkp laid "
                                "out last, else its low bound"))
    return lines, checks


def s_size(ctx, args):
    """The counts of the ``n=auto`` rows (``fkcore.size``): the fewest machines that meet the
    surface's targets, the factories' asserts and what each row's consumers ask, every factory
    together. The report says what each count serves; a constraint no count can meet is a
    ``size`` line, a sized row that runs past its plot or into another instance a ``does-not-fit`` line.
    Rows grow; where they stand is the player's (and ``place`` stops on two entities on a
    tile)."""
    from . import size as sizemod
    makers, previous = {}, {}
    for p in _factories(ctx):
        if p.factory not in ctx.flats:
            continue
        path = os.path.join(ctx.root, f"{p.module}.fk")
        makers[p.factory] = sizemod.maker(path, ctx.cat, p.module, ctx.previous.get(p.factory))
        if p.factory in ctx.previous:
            previous[p.factory] = ctx.previous[p.factory]
    o = _opts(args)
    payback = 0.0
    if o.get("payback") not in (None, True):
        from .wait import parse_duration
        try:
            payback = parse_duration(o["payback"])
        except ValueError as exc:
            raise FlowError(f"size --payback {o['payback']}: {exc}") from None
    sz = sizemod.size(makers, ctx.surface.targets if ctx.surface is not None else None, previous,
                      cat=ctx.cat, payback=payback)
    lines, checks = sizemod.report(sz, ctx.cat)
    if not sz.rows and not sz.picks:
        return lines, checks
    ctx.flats.update(sz.flats)
    ctx.sized = sz
    patch = [r for r in sz.rows if "patch" in sz.flats[r.factory].insts[r.path].hints
             and "at" not in sz.flats[r.factory].insts[r.path].hints]
    if patch:
        lines += ["", "  on their ore by place (@patch): " + ", ".join(
            f"{r.path} n={r.n}" for r in patch) + "; place says when that many drills don't fit"]
    return lines, checks + sizemod.fit(sz, ctx.surface.places if ctx.surface else {}, ctx.cat)


def s_report_rates(ctx, args):
    """The nominal rate model per factory (V1); once a factory is laid out (after ``place`` and
    ``route``), also its routed rates (V2, ``fkcore.routed``: the lanes and inserters as laid,
    ``too-thin`` where a carrier binds). ``--hand N`` / ``--bulk-hand N``: the inserter hand
    sizes (1 + the research bonus); ``auto`` (the default) reads the force's over the game link.
    Run again later in the script (after ``route``), it reports in full but adds as checks only
    what is new: ``too-thin``, and a ``target-not-met`` the nominal rates met and the routed ones don't
    (the ``rate-not-met`` and ``target-not-met`` lines of the nominal model are the first run's)."""
    o = _opts(args, hand="auto", bulk_hand="auto")
    for key in ("hand", "bulk_hand"):
        if o[key] != "auto":
            _count(o, key, lo=1)
    ctx.rates_runs += 1
    again = ctx.rates_runs > 1
    lines, checks, made, routed_made = [], [], {}, None
    hands = None
    for factory, flat in ctx.flats.items():
        model = ctx.model(factory)
        lines += rates.report(model, factory).splitlines() + [""]
        lines += _dnp_lines(flat, factory)
        for text, ok, value, target, op in model.asserts:
            if not ok and not again:
                checks.append(Check("rate-not-met", factory, f"{text}: {value * 60:.3f}/min"))
        for net, item, can, want in ([] if again else rates.underfed(model)):
            checks.append(Check(
                "short-feed", f"{factory} net {net}",
                f"{item}: its drivers make {can * 60:.1f}/min at most, its consumers would take "
                f"{want * 60:.1f}/min, so they run at about {can / want:.0%} for it; "
                f"`fk plan {item} {want * 60:.0f}/min` sizes the drivers"))
        for net, feed, product, sink, takers in ([] if again else rates.carryover(model)):
            who = takers[0] + (f" and {len(takers) - 1} more" if len(takers) > 1 else "")
            checks.append(Check(
                "feed-rides-on", f"{factory} net {net}",
                f"{feed} rides the belt into {sink} with {product}: what {who} upstream don't take "
                f"of it fills {sink}, and the belt stops for {product} too. Give {product} a belt "
                f"of its own, or end the {feed} lane before the sink"))
        for key, m in flat.machines.items():
            for item, r in m.makes.items():
                made[item] = made.get(item, 0.0) + r * model.util[key]
        lay = ctx.layouts.get(factory)
        if lay is not None:
            from . import routed
            if hands is None:
                hands = _hands(ctx, o)
            r_ = routed.solve(flat, lay, ctx.cat, model, hand=hands[0], bulk_hand=hands[1])
            rlines, rchecks = routed.report(r_, factory)
            lines += rlines + [""]
            checks += rchecks
            if ctx.sized is not None:
                from . import size as sizemod
                checks += sizemod.belts(ctx.sized, factory, r_)
            routed_made = routed_made if routed_made is not None else {}
            for key, m in flat.machines.items():
                u = r_.util(key)
                for item, rate in m.makes.items():
                    if u is not None and rate != float("inf"):
                        routed_made[item] = routed_made.get(item, 0.0) + rate * u
    if ctx.surface is not None:
        for item, want in ctx.surface.targets.items():
            got = made.get(item, 0.0)
            nominal_ok = ok = got >= want - 1e-9
            text = f"{got * 60:.3f}/min nominal"
            if routed_made is not None:
                rgot = routed_made.get(item, 0.0)
                ok = ok and rgot >= want - 1e-9
                text += f", {rgot * 60:.3f}/min routed"
            lines.append(f"target {item} {want * 60:g}/min: {'met' if ok else 'NOT met'}, {text}")
            if not ok and not (again and not nominal_ok):
                checks.append(Check("target-not-met", item, f"{text}, target {want * 60:g}/min"))
    if hands is not None:
        lines.append(hands[2])
    return lines, checks


def _dnp_lines(flat, factory):
    """2.0.63: the DNP instances (do not populate: laid out and routed, never built, nothing in
    the model), the topmost of each, and what their machines would make at full speed."""
    tops = sorted(p for p in getattr(flat, "dnp", ()) if not any(
        p.startswith(q + "/") for q in flat.dnp))
    if not tops:
        return []
    would, n = {}, 0
    for key, m in flat.machines.items():
        if not m.extra.get("dnp") or m.source or m.sink:
            continue
        n += 1
        for item, rate in m.makes.items():
            would[item] = would.get(item, 0.0) + rate
    made = ", ".join(f"{item} {rate * 60:.1f}/min" for item, rate in
                     sorted(would.items(), key=lambda kv: -kv[1])[:6])
    return [f"dnp {factory}: {', '.join(tops)}: laid out and routed, not built ({n} machine"
            f"{'s' if n != 1 else ''}" + (f"; at full speed they'd make {made}" if made else "")
            + ")", ""]


def _classify(new, prev):
    """unchanged | trivial | stale | new per instance, plus orphans, against the committed .fkp."""
    out = {}
    for path, r in new.insts.items():
        p = prev.insts.get(path) if prev is not None else None
        if p is None:
            out[path] = "new"
        elif (p.kind, p.sig, p.x, p.y, p.rot) != (r.kind, r.sig, r.x, r.y, r.rot):
            out[path] = "stale"
        elif p.params != r.params:
            out[path] = "trivial"
        else:
            out[path] = "unchanged"
    for path in (prev.insts if prev is not None else {}):
        if path not in new.insts:
            out[path] = "orphan"
    return out


_OWN_POLE = re.compile(r"[prs]\d+")   # a cover's own: poles p<k>, roboports r<k>, storage s<k>


def _pinned_covers(new, prev, cat):
    """Carry over a cover's pinned entities (a merged factory's poles, hand-placed ones: every
    entity under a cover that is not the cover's own ``p<k>``) from the committed .fkp."""
    if prev is None:
        return 0
    taken = set()
    for e in new.entities.values():
        taken |= fkp.tiles_of(cat, e)
    kept = 0
    for name, c in prev.covers.items():
        new.covers.setdefault(name, fkp.Cover(c.name, c.kind, dict(c.params)))
        for e in prev.entities.values():
            local = e.key[len(name) + 1:]
            if e.owner != name or _OWN_POLE.fullmatch(local):
                continue
            tiles = fkp.tiles_of(cat, e)
            if tiles & taken or e.key in new.entities:
                continue
            taken |= tiles
            new.add(fkp.Entity(e.key, e.name, e.x, e.y, e.dir, dict(e.attrs)))
            kept += 1
    return kept


def s_place(ctx, args):
    lines, checks = [], []
    for p in _factories(ctx):
        flat = ctx.flats[p.factory]
        ground, placed = None, []
        if ctx.ground is not None:
            # a quarter-turned plot's frame depends on the factory's size, not known before it
            # is laid out: the placer takes the frame to be the plot's own extent
            ground = terrainmod.frame_ground(ctx.ground, p, terrainmod.frame_size(p),
                                             patch=p.patch)
        lay, notes = expand.expand_factory(flat, ctx.cat, ctx.previous.get(p.factory), ground,
                                           placed)
        lay.name = p.factory
        pinned = _pinned_covers(lay, ctx.previous.get(p.factory), ctx.cat)
        if pinned:
            notes = list(notes)
            lines.append(f"{p.factory}: {pinned} pinned cover entities kept from the committed .fkp")
        ctx.layouts[p.factory] = lay
        cls = _classify(lay, ctx.previous.get(p.factory))
        count = {}
        for c in cls.values():
            count[c] = count.get(c, 0) + 1
        lines.append(f"{p.factory}: {len(lay.insts)} instances placed by their hints, "
                     f"{len(lay.entities)} entities, {lay.size[0]}x{lay.size[1]}; "
                     + ", ".join(f"{n} {c}" for c, n in sorted(count.items())))
        lines += [f"  {path}: {c}" for path, c in sorted(cls.items()) if c != "unchanged"]
        lines += [f"  placed {line}" for line in placed]
        checks += [Check("no-position", p.factory, n) for n in notes]
        checks += _overlap_checks(p.factory, flat, lay, ctx.cat)
        checks += _invariant_checks(p.factory, fkp.invariants(lay, ctx.cat))
    return lines, checks


def _overlap_checks(factory, flat, lay, cat):
    """``overlap`` (2.0.75, warn): two sealed modules whose frames share tiles. Both route there,
    the first laid in the way of the next; only two entities on one tile stop the build."""
    out = []
    pairs = expand.frame_overlaps(flat, lay, cat)
    if not pairs:
        return out
    try:
        edges = expand.port_edges(flat, lay, cat)
    except Exception:                           # the router reports a port it can't place
        edges = {}
    for a, b, (x0, y0, x1, y1) in pairs:
        out.append(Check("overlap", f"{factory} {a}",
                         f"its frame and {b}'s share {x1 - x0}x{y1 - y0} tiles at "
                         f"{x0},{y0}..{x1 - 1},{y1 - 1}: both modules' routes may use them, and "
                         "each is in the way of the other's"))
        for mine, other in ((a, b), (b, a)):
            ob = expand.frame_of(flat, lay, other, cat)
            for key, pe in sorted(edges.items()):
                tx, ty = pe.outside
                if key.rsplit("/", 1)[0] == mine and ob[0] <= tx < ob[2] and ob[1] <= ty < ob[3]:
                    out.append(Check("overlap", f"{factory} {mine}",
                                     f"port {key.rsplit('/', 1)[-1]} opens at {tx},{ty}, inside "
                                     f"{other}'s frame: a net from outside both can't reach it"))
    return out


def _pinned_routes(flat, new, prev, cat):
    """The committed routes the router may keep (route --only stale): -> ({net: its committed
    carriers}, {net: why it is routed again}). A fluid net is routed again when the new layout
    takes one of its tiles, an end moved or its ends changed at all. Every other committed route
    goes to the router, less its carriers on tiles the new layout takes, and the router keeps it
    as it stands, patches it for the ends added, dropped and moved and the gaps, or routes the net
    again (``route._standing``).

    2.0 (Rory, 2026-10-21): "Pin what stands and make patch changes rather than regenerating
    the base": a consumer added to a net no longer re-lays the whole net. 2.0.74: nor does an
    end moved (``route.NetJob.moved_ends``, a chest moved a tile): the patch takes up its old
    branch and lays a new one to where it stands now; nor a machine set on its belt (the 10-31
    replay: a chest moved a tile onto fe_pl4's trunk re-laid its 137 belts)."""
    if prev is None:
        return {}, {}
    old = route.jobs(flat, prev, cat) if prev.insts else {}
    now = route.jobs(flat, new, cat)
    taken = set()
    for e in new.entities.values():
        taken |= fkp.tiles_of(cat, e)
    pinned, again = {}, {}
    by_net = {}
    for e in prev.entities.values():
        if e.owner.startswith("~"):
            by_net.setdefault(e.owner[1:], []).append(e)
    for net, ents in sorted(by_net.items()):
        if net not in now:
            continue
        moved = now[net].moved_ends(old[net]) if net in old and now[net].fluid else []
        if moved:
            again[net] = f"its ends moved since the committed .fkp ({', '.join(moved)})"
            continue
        if net not in old or (now[net].fluid and old[net].signature() != now[net].signature()):
            again[net] = "its ends changed since the committed .fkp"
            continue
        tiles = set()
        for e in ents:
            tiles |= fkp.tiles_of(cat, e)
        if tiles & taken and now[net].fluid:
            again[net] = "the new layout takes some of its tiles"
            continue
        ents = [e for e in ents if not fkp.tiles_of(cat, e) & taken]
        if not ents:
            again[net] = "the new layout takes all of its tiles"
            continue
        for e in ents:
            taken |= fkp.tiles_of(cat, e)
        pinned[net] = [fkp.Entity(e.key, e.name, e.x, e.y, e.dir, dict(e.attrs)) for e in ents]
    return pinned, again


def _with_last_routes(ctx, factory, flat, lay, pinned, again):
    """3.0 (the 20261030-2352 run, 11-01: eight hours of builds that stopped before the paste, each
    laying every new net again): the routes the last route step laid (its net files,
    ``fkcore.design``, written as each net landed, so a build that stopped mid-route counts too),
    when it ran after the committed ``.fkp`` was written, are kept in place of the committed ones: that step
    laid on what stood of them, so its routes are the newer design (the 11-01 bench: c4_suo's
    committed route was broken, its last route stood, and it was laid again in every build). A
    committed route of a net the last step has none for keeps its carriers on tiles no last route
    holds. -> (pinned, again), in net order."""
    last = os.path.join(design.folder(ctx.out, factory), "base.fkp")
    committed = os.path.join(ctx.root, f"{factory}.fkp")
    if not os.path.exists(last) or (os.path.exists(committed)
                                    and os.path.getmtime(last) <= os.path.getmtime(committed)):
        return pinned, again
    prev = design.layout(ctx.out, factory)
    if prev is None:
        return pinned, again
    more, why = _pinned_routes(flat, lay, prev, ctx.cat)
    if not more:
        return pinned, again
    taken = set()
    for ents in more.values():
        for e in ents:
            taken |= fkp.tiles_of(ctx.cat, e)
    out = dict(more)
    for net, ents in pinned.items():
        if net in out:
            continue
        ents = [e for e in ents if not fkp.tiles_of(ctx.cat, e) & taken]
        if ents:
            out[net] = ents
    again = {n: w for n, w in {**again, **why}.items() if n not in out}
    return dict(sorted(out.items())), again


def _held_nets(ctx, flat, lay, recs):
    """3.0: the nets the player holds (``fk hold NET``, or ``status: held`` in a net file) stand as
    they are: their carriers go on the layout before the router, which keeps them. A carrier on a
    tile something else now holds is left out, and said. -> (layout, {net: carriers}, lines)."""
    held = {n: r["ents"] for n, r in recs.items() if r["status"] == "held"
            and all(m in flat.nets for m in fkp.net_names(n) or [n])}
    if not held:
        return lay, {}, []
    lay = lay.copy()
    taken = {t for e in lay.entities.values() for t in fkp.tiles_of(ctx.cat, e)}
    out, lines = {}, []
    for net, ents in sorted(held.items()):
        mine = [e for e in ents if e.key not in lay.entities
                and not fkp.tiles_of(ctx.cat, e) & taken]
        for e in mine:
            lay.entities[e.key] = e
            taken |= fkp.tiles_of(ctx.cat, e)
        out[net] = mine
        lines.append(f"held {net}: {len(mine)} carriers stand as you hold them"
                     + (f" ({len(ents) - len(mine)} left out: their tiles are taken now)"
                        if len(mine) < len(ents) else ""))
    return lay, out, lines


def _write_nets(ctx, factory, r, held, seeds=None, stood=()):
    """Every net's file (``fkcore.design``) as the route step left it; a net held while it ran
    stays held, a seed stays (``fk seed``), and the file of a net the design no longer has goes."""
    seeds = seeds or {}
    by_owner = {}
    for e in r.layout.entities.values():
        if e.owner.startswith("~"):
            by_owner.setdefault(e.owner[1:], []).append(e)
    kept, routed = set(r.kept) | set(stood), set(r.routed)
    now = design.read_all(ctx.out, factory)
    held = set(held) | {n for n, rec in now.items() if rec["status"] == "held"}
    nets = set(by_owner) | set(r.failed) | held
    for net in set(now) - nets:
        try:
            os.remove(design.net_path(ctx.out, factory, net))
        except OSError:
            pass
    for net in sorted(nets):
        ents = by_owner.get(net, [])
        if net in held:
            status, why = "held", ""
        elif net in r.failed:
            status, why = "unrouted", r.failed[net]
        elif net in r.patched:
            status, why = "patched", r.patched[net]
        elif net in kept:
            status, why = "kept", ""
        else:
            status, why = ("routed" if ents or net in routed else "skipped"), ""
        sd = seeds.get(net) or ()
        if sd and status == "unrouted":
            ents = sd
        design.write_net(ctx.out, factory, net, status, ents, why,
                         key=(r.memo.get(net) or {}).get("key") if status == "unrouted" else None,
                         seed=[e.key for e in sd])


def _module_pinned(flat, prev, cat, path):
    """A sealed module's patch scope (``route --module X``, PLAN-MODULES phase 4): X's own nets and
    the parent nets ending at X's ports are routed again; every other committed route is kept as it
    stands, so changing a rib re-lays its frame and its port segments and touches nothing else.
    -> ({net: its committed carriers}, {net: why it is routed again})."""
    if prev is None:
        return {}, {}
    scope = {name for name in flat.nets if name == path or name.startswith(path + "/")}
    for key, port in flat.ports.items():
        if not key.startswith(path + "/"):
            continue
        item = port.items[0] if port.items else None
        for hold in (flat._in_hold, flat._out_hold):
            for net in hold.get((key, item), []):
                scope.add(net)
    pinned, again = {}, {}
    by_net = {}
    for e in prev.entities.values():
        if e.owner.startswith("~"):
            by_net.setdefault(e.owner[1:], []).append(e)
    for net, ents in sorted(by_net.items()):
        if net in scope:
            continue
        pinned[net] = [fkp.Entity(e.key, e.name, e.x, e.y, e.dir, dict(e.attrs)) for e in ents]
    for net in sorted(scope):
        again[net] = f"in {path}'s scope (route --module)"
    return pinned, again


def _one_inserter(ctx, factory, flat, lay):
    """2.0 (Rory, 2026-10-21): a sushi net laid as one inserter from its source straight into
    each sink can't clog (the inserter takes only what the sink accepts), so its ``CLOG`` line
    from elaborate goes; on any other route it stays. -> the report lines that say so."""
    sushi = {n: e for n, e in connect.endpoints(flat).items()
             if flat.nets[n].sushi and not flat.nets[n].limited}
    if not sushi:
        return []
    clear = connect.one_inserter(lay, ctx.cat, sushi)
    where = {f"{factory} net {n}" for n in clear}
    ctx.checks = [(s, c) for s, c in ctx.checks if not (c.code == "CLOG" and c.where in where)]
    return [f"  one hand  {n}: one inserter from its source straight into each sink, which takes "
            "only what the sink accepts: no CLOG" for n in sorted(clear)]


def _power_units(ctx, factory, lay):
    """2.1.35: the sealed modules of ``factory`` as cover units, innermost first:
    [(path, frame, (kind, rot))]; [] for a layout without them."""
    flat = ctx.flats.get(factory)
    if flat is None or not getattr(flat, "sealed", None):
        return []
    from .expand import frame_of
    out = []
    for path in flat.sealed:
        rec = lay.insts.get(path)
        if rec is None or path not in flat.insts:
            continue
        try:
            box = tuple(int(v) for v in frame_of(flat, lay, path, ctx.cat))
        except Exception:            # a frame the fragment can't size: the last pass covers it
            continue
        out.append((path, box, (rec.kind, int(rec.rot or 0) % 4)))
    out.sort(key=lambda u: (-u[0].count("/"), u[0]))
    return out


def _empty_slots(ctx, factory, lay):
    """2.1.34: the tiles of every sealed module that holds nothing (a slot a paste will fill, as
    the megabase grid's bay slots are): cover power keeps its poles out of them, so the paste
    finds its frame empty. -> frozenset of tiles."""
    flat = ctx.flats.get(factory)
    if flat is None or not getattr(flat, "sealed", None):
        return frozenset()
    from .expand import frame_of
    full = {e.key.split("/")[:-1] and "/".join(e.key.split("/")[:-1]) for e in lay.entities.values()}
    tiles = set()
    for path in flat.sealed:
        if path not in lay.insts or path not in flat.insts:
            continue
        if any(o == path or o.startswith(path + "/") for o in full if o):
            continue
        try:
            x0, y0, x1, y1 = frame_of(flat, lay, path, ctx.cat)
        except Exception:            # a frame the fragment can't size: leave it to the poles
            continue
        tiles |= {(x, y) for x in range(x0, x1) for y in range(y0, y1)}
    return frozenset(tiles)


def _frame_blocked(ctx, factory, size):
    """The terrain's blocked tiles in ``factory``'s frame (empty on a paper surface)."""
    if ctx.ground is None or ctx.surface is None or factory not in ctx.surface.places:
        return frozenset()
    p = ctx.surface.places[factory]
    return terrainmod.frame_blocked(ctx.ground, p, size or terrainmod.frame_size(p))


def _need(ctx, kind):
    """How many entities of ``kind`` the design holds (its committed layout when this build has
    none yet): what a family choice must be able to build, at least. A tier the network holds one
    or two of is no better than none for forty, and this keeps ``any`` from choosing it."""
    lays = list(ctx.layouts.values()) or list(ctx.previous.values())
    n = sum(1 for lay in lays for e in lay.entities.values()
            if ctx.cat.has_entity(e.name) and ctx.cat.kind(e.name) == kind)
    return max(n, 1)


_RIBBON_UG_NEED = 16      # a ribbon of eight belts under one crossing: sixteen underground ends


def _ribbon_ug(word):
    """``route --ug``: the fastest tier a ribbon's tunnels may have (2.1 ribbon tunnels). A tier
    (yellow, red, blue, turbo) as given; ``any``: the fastest whose underground robots can build
    now, sixteen of them (out/avail.json), else yellow. -> (tier, a report line or None)."""
    from . import tiers as tiersmod
    word = word.strip().lower()
    if not availmod.is_any(word):
        if word not in tiersmod.TIERS:
            raise FlowError(f"route --ug is yellow, red, blue, turbo or any, got {word!r}")
        return word, None
    for tier in reversed(tiersmod.TIERS[1:]):
        if availmod.buildable(tiersmod.FAMILY["underground-belt"][tiersmod.TIERS.index(tier)],
                              _RIBBON_UG_NEED):
            return tier, (f"ribbon tunnels: up to {tier} (--ug any: the network can build "
                          f"{_RIBBON_UG_NEED} of its undergrounds); a tunnel gets the slowest tier "
                          "that reaches it")
    return "yellow", None


def _pipe(ctx, word, lines):
    """(pipe, pipe-to-ground) for ``route --pipe`` (a material or a pipe entity; empty: the
    pack's default, else iron); an unresearched one falls back to iron, with a line."""
    from . import fluid_route as fluidmod
    try:
        pipe = fluidmod.material(word, ctx.cat)
    except ValueError as exc:
        raise FlowError(str(exc)) from None
    if pipe[0] != fluidmod.PIPE and (availmod.locked(pipe[0]) or availmod.locked(pipe[1])):
        lines.append(f"pipe: {pipe[0]} is not researched here: iron pipes instead")
        return fluidmod.PIPE, fluidmod.PTG
    if pipe[0] != fluidmod.PIPE:
        lines.append(f"pipe: {pipe[0]} and {pipe[1]}")
    return pipe


def s_route(ctx, args):
    # 3.0: the routing policy is the build script's: --group-budget S (a failed net's group try,
    # 0 = every net once and alone), --group-max N, --first NET,... (patterns too: mall/*), and
    # --skip NET,... (left to the player, reported manual); --retry-failed changed|always|never (a
    # net that failed last build), --failed keep|drop (a net that fails now keeps the route that
    # stands, or loses it), --area X0,Y0,X1,Y1 (only the nets there are routed; the rest stand)
    o = _opts(args, only="all", effort="normal", hand="any", module="", again="", ug="any",
              m2m=False, group_budget=route.GROUP_BUDGET, group_max=route.GROUP_MAX, first="",
              skip="", retry_failed="changed", failed="keep", area="", compiled="yes",
              reuse_lab=False, compile="", pipe="", store="", **{"try": ""})
    if o["try"]:
        return _route_try(ctx, str(o["try"]))
    if o["compile"]:
        return _route_compile(ctx, str(o["compile"]), str(o["hand"]))
    if o["retry_failed"] not in ("changed", "always", "never"):
        raise FlowError(f"route --retry-failed changed|always|never, got {o['retry_failed']!r}")
    if o["failed"] not in ("keep", "drop"):
        raise FlowError(f"route --failed keep|drop, got {o['failed']!r}")
    area = _area(o["area"]) if o["area"] else None
    # 3.1, the one compile store (fkcore.compiled): the game folder's out/compiled (fk lab shares
    # its game's); --store DIR elsewhere, --store none (or --compiled no) to lay every module
    # afresh; --reuse-lab reads the lab builds' own stores too
    store = str(o["store"]) or ctx.compiled or os.path.join(ctx.out, "compiled")
    if store.lower() in ("none", "no", "off") or str(o["compiled"]) == "no":
        store = None
    else:
        if not os.path.isabs(store):
            store = os.path.join(ctx.root, store)
        store = compiledmod.Store(store, more=_compiled_dirs(ctx, o["reuse_lab"]),
                                  locked=availmod.locked)
    ctx.hand_requested = str(o["hand"])
    hand, _note = availmod.choose("hand", o["hand"], need=_need(ctx, "inserter"))
    limit, retries = (400000, None) if o["effort"] == "high" else (60000, None)
    lines, checks = [], []
    ug, ug_note = _ribbon_ug(str(o["ug"]))
    pipe = _pipe(ctx, o["pipe"], lines)
    for factory, lay in list(ctx.layouts.items()):
        flat = ctx.flats[factory]
        pinned, again, comp = {}, {}, {}
        if o["module"]:
            if o["module"] not in flat.insts or o["module"] not in flat.sealed:
                lines.append(f"{factory}: --module {o['module']} is not a sealed instance here")
            else:
                pinned, again = _module_pinned(flat, ctx.previous.get(factory), ctx.cat,
                                               o["module"])
        elif o["only"] == "stale":
            pinned, again = _pinned_routes(flat, lay, ctx.previous.get(factory), ctx.cat)
            pinned, again = _with_last_routes(ctx, factory, flat, lay, pinned, again)
        # 3.0, the design database: the net files the last route step left (fkcore.design)
        recs = design.read_all(ctx.out, factory)
        for net, rec in sorted(recs.items()):
            if rec["status"] == "unrouted" and rec["why"].startswith(design.UNROUTED_BY_YOU):
                pinned.pop(net, None)
                again[net] = "unrouted by you (fk unroute)"
        lay, held, held_lines = _held_nets(ctx, flat, lay, recs)
        for net in held:
            pinned.pop(net, None)
            again.pop(net, None)
        lines += [f"{factory}: {line}" for line in held_lines]
        old = _old_routes(ctx, factory, recs)
        stood, inside = {}, set()
        if area is not None:
            # route --area: every net with a carrier or an end there is routed again, and every
            # other route stands exactly as it is (held for this build), whatever the router thinks
            inside = _nets_in_area(ctx, flat, lay, old, area)
            out_ = {n: {"status": "held", "ents": ents} for n, ents in old.items()
                    if n not in inside and n not in held and n in flat.nets}
            lay, stood, stood_lines = _held_nets(ctx, flat, lay, out_)
            lines += [f"{factory}: {ln.replace('held ', 'stands ', 1)}" for ln in stood_lines
                      if "left out" in ln]
            for net in stood:
                pinned.pop(net, None)
                again.pop(net, None)
            for net in sorted(inside):
                if net in flat.nets and net not in held:
                    pinned.pop(net, None)
                    again[net] = "in the area (route --area)"
            lines.append(f"{factory}: route --area {o['area']}: {len(inside)} nets there routed "
                         f"again, {len(stood)} outside stand as they are")
        # fk seed: carriers the player pinned; the router lays the rest of the net through them
        seeds = {n: design.seed_of(r) for n, r in recs.items()
                 if r["seed"] and n in flat.nets and n not in held}
        for net, rec in sorted(recs.items()):
            if net in seeds and rec["status"] == "seeded":
                pinned.pop(net, None)
                again[net] = "seeded by you (fk seed)"
        if o["again"]:
            asked, unknown = _asked(flat, str(o["again"]))
            for net in sorted(asked):
                if pinned.pop(net, None) is not None:
                    again[net] = "asked (--again / fk build --reroute)"
            if unknown:
                lines.append(f"{factory}: --again names no net here: {', '.join(unknown)}")
        blocked = _frame_blocked(ctx, factory, lay.size)
        # 2.1.19: a long route says how far it is, every route.PROGRESS_EVERY seconds
        saved = route.PROGRESS
        if ctx.echo is not None:
            import time
            route._progress_at[:] = [time.monotonic()] * 2
            route._progress_net[:] = [None, 0.0]
            route.PROGRESS = lambda msg, f=factory: ctx.echo(f"      {f}: {msg}")
        memo = {n: {"key": r["key"], "why": r["why"]} for n, r in recs.items()
                if r["status"] == "unrouted" and r["key"]}
        design.write_base(ctx.out, factory, lay)
        began = time.time()

        def on_net(net, status, ents, why, took, f=factory, seeds=seeds):
            sd = seeds.get(net) or ()
            if sd and status == "unrouted":
                ents = sd                          # a seed stays, routed or not
            design.write_net(ctx.out, f, net, status, ents, why, took, seed=[e.key for e in sd])

        def on_reach(net, which, k, n, f=factory):
            design.write_now(ctx.out, f, f"route, {which}: {k + 1} of {n}, {net}")
            path = design.net_path(ctx.out, f, net)
            try:
                if os.path.getmtime(path) < began:
                    return None
            except OSError:
                return None
            rec = design.read_net(path)        # the player held it while this build runs
            return rec["ents"] if rec and rec["status"] == "held" else None
        try:
            r = route.route_layout(flat, lay, ctx.cat, limit=limit, retries=retries,
                                   blocked=blocked, hand=hand, patch=pinned, ug=ug, pipe=pipe,
                                   m2m="left" if o["m2m"] == "left"
                                   else o["m2m"] not in (False, "no"),
                                   group_budget=float(o["group_budget"]),
                                   group_max=int(o["group_max"]),
                                   first=[p for p in str(o["first"]).split(",") if p],
                                   skip=[p for p in str(o["skip"]).split(",") if p],
                                   memo=memo,
                                   again=sorted((_asked(flat, str(o["again"]))[0]
                                                 if o["again"] else set()) | inside),
                                   on_net=on_net, on_reach=on_reach,
                                   seeds={n: s for n, s in seeds.items() if n not in pinned},
                                   retry=str(o["retry_failed"]), store=store)
        finally:
            route.PROGRESS = saved
            design.clear_now(ctx.out, factory)
        # every net as this step left it, the nets that failed with the ground they failed on: the
        # next build does not search them again while it stands (route --again NET does)
        if o["failed"] == "keep":
            r.layout, standing = _keep_failed(ctx, r, old)
            lines += [f"  standing  {n}: unrouted; its last route ({k} carriers) stands until it "
                      f"routes (route --failed keep)" for n, k in sorted(standing.items())]
        _write_nets(ctx, factory, r, held, seeds, stood)
        # 3.1: what the compile store did for each sealed unit (route.Routed.compiled)
        comp = {n for c in r.compiled.values() if c["from"] == "store" for n in c["nets"]}
        lines += _compiled_lines(factory, r)
        r.layout, rehanded = _rehand(r.layout, ctx.cat, hand)
        dnp = route.tag_dnp(flat, r.layout, ctx.cat)
        again.update(r.again)
        ctx.layouts[factory] = r.layout
        lines.append(f"{factory}: {len(r.routed)} routed, {len(r.kept)} kept"
                     + (f", {len(r.patched)} patched" if r.patched else "")
                     + f" ({len(pinned)} pinned from the committed .fkp"
                     + (f", {len(comp)} laid from the compiled store" if comp else "")
                     + f"), {len(r.failed)} failed, "
                     f"{r.passes} pass{'es' if r.passes != 1 else ''}"
                     + (f"; {len(blocked)} terrain tiles kept clear" if ctx.ground is not None
                        else ""))
        lines += [f"  stale     {n}: routed again: {why}" for n, why in sorted(again.items())]
        if dnp:
            lines.append(f"  dnp       {dnp} entities laid but not built (DNP: "
                         f"{', '.join(sorted(p for p in flat.dnp if not any(p.startswith(q + '/') for q in flat.dnp)))}"
                         ")")
        if rehanded:
            lines.append(f"  rehanded  {rehanded} standing inserter"
                         f"{'s' if rehanded != 1 else ''} robots cannot build, to {hand}")
        lines += [f"  {l}" for l in r.lines]
        lines += _chain_lines(flat, r)
        lines += _one_inserter(ctx, factory, flat, r.layout)
        checks += [Check("no-route", f"{factory} net {n}", why) for n, why in r.failed.items()]
        checks += [Check("maxflow", f"{factory} net {n}", why) for n, why in r.maxflow.items()]
        checks += [Check("no-route", f"{factory} net {l.split(':')[0].split()[-1]}",
                         l.split(": ", 1)[1]) for l in r.lines if l.startswith("skipped")]
        checks += _invariant_checks(factory, fkp.invariants(r.layout, ctx.cat, nets=flat.nets))
    if ug_note and any(ln.lstrip().startswith("ribbon ") for ln in lines):
        lines.append(f"  {ug_note}")
    return lines, checks


def _route_compile(ctx, module, hand_word):
    """``route --compile MODULE`` (``fk compile MODULE``): every placed sealed instance of MODULE
    routed alone (its own nets and its children's, the router's compile units) and stored in
    the one compile store (fkcore.compiled); a twin routes once. Nothing else is laid, written
    or pasted."""
    hand, _note = availmod.choose("hand", hand_word, need=_need(ctx, "inserter"))
    store = compiledmod.Store(ctx.compiled or os.path.join(ctx.out, "compiled"),
                              locked=availmod.locked)
    lines = []
    for factory, lay in ctx.layouts.items():
        flat = ctx.flats[factory]
        mine = sorted(p for p in lay.insts if p in flat.sealed and p in flat.insts
                      and (flat.insts[p].module or flat.insts[p].kind) == module)
        if not mine:
            lines.append(f"{factory}: compile: no sealed instance of {module} is placed here")
            continue
        inside = {n for n in flat.nets if any(n.startswith(p + "/") for p in mine)}
        began = time.time()
        r = route.route_layout(flat, lay, ctx.cat, hand=hand, pipe=_pipe(ctx, "", []),
                               blocked=_frame_blocked(ctx, factory, lay.size),
                               skip=[n for n in flat.nets if n not in inside], store=store)
        took = time.time() - began
        for path in mine:
            c = r.compiled.get(path)
            if c is None:
                lines.append(f"{factory}: compile {path}: no net of its own to route")
            elif c["from"] == "store":
                lines.append(f"{factory}: compile {path}: already compiled ({module} {c['key']})")
            elif c["from"] and c["from"].startswith("twin "):
                lines.append(f"{factory}: compile {path}: the same inside as {c['from'][5:]}")
            elif c["stored"]:
                lines.append(f"{factory}: compile {path}: {len(c['nets'])} nets routed and stored "
                             f"({module} {c['key']}, {took:.1f} s); the next build lays them "
                             "from there")
            else:
                failed = sorted(n for n in r.failed if n in c["nets"])
                lines.append(f"{factory}: compile {path}: {len(failed)} of {len(c['nets'])} nets "
                             f"did not route ({', '.join(failed)}), not stored ({took:.1f} s)")
        lines += [f"{factory}: {ln}" for ln in r.lines if ln.startswith("compiled  old")]
    return lines, []


def _compiled_lines(factory, r):
    """The route step's lines on the compile store: what each sealed unit was laid from, the
    entries written, the entries refused (an entity the force can't build)."""
    lines = []
    for path, c in sorted(r.compiled.items()):
        if c["from"] == "store":
            lines.append(f"{factory}: compiled  {path}: {len(c['nets'])} nets laid from the store "
                         f"({c['module']} {c['key']})")
        elif c["refused"]:
            lines.append(f"{factory}: compiled  {path}: its stored entry ({c['module']}) holds "
                         f"{', '.join(c['refused'])}, not researched here: routed again")
    stored = sorted(p for p, c in r.compiled.items() if c["stored"])
    if stored:
        lines.append(f"{factory}: compiled  stored {', '.join(stored)} (out/compiled): laid from "
                     "there wherever each stands next")
    return lines


def _compiled_dirs(ctx, lab):
    """The stores the route step reads besides its own: with ``route --reuse-lab`` every lab
    build's (``<run>/lab/<name>/out/compiled``, fkcore.lab; a lab build of the game folder
    writes the game's own store, so this is for a lab built elsewhere)."""
    dirs = []
    if lab:
        labs = os.path.join(os.path.dirname(os.path.abspath(ctx.root)), "lab")
        if os.path.isdir(labs):
            dirs += [os.path.join(labs, d, "out", "compiled") for d in sorted(os.listdir(labs))
                     if os.path.isdir(os.path.join(labs, d, "out", "compiled"))]
    return dirs


def _area(spec):
    """``route --area X0,Y0,X1,Y1`` -> (x0, y0, x1, y1), the tiles x0 <= x < x1, y0 <= y < y1."""
    try:
        a = tuple(int(v) for v in str(spec).split(","))
        if len(a) != 4 or a[0] >= a[2] or a[1] >= a[3]:
            raise ValueError
    except ValueError:
        raise FlowError(f"route --area X0,Y0,X1,Y1 (x0 < x1, y0 < y1), got {spec!r}") from None
    return a


def _old_routes(ctx, factory, recs):
    """{net: carriers} of the routes that stand before this route step: the committed .fkp's,
    and over them the net files' (routed, kept, patched, held: newer when the last build stopped
    before its paste)."""
    out = {}
    prev = ctx.previous.get(factory)
    if prev is not None:
        for e in prev.entities.values():
            if e.owner.startswith("~"):
                out.setdefault(e.owner[1:], []).append(e)
    for net, rec in recs.items():
        if rec["status"] in ("routed", "kept", "patched", "held") and rec["ents"]:
            out[net] = list(rec["ents"])
    return {n: [fkp.Entity(e.key, e.name, e.x, e.y, e.dir, dict(e.attrs)) for e in ents]
            for n, ents in out.items()}


def _nets_in_area(ctx, flat, lay, old, area):
    """The nets with a carrier of their standing route, or a machine at one of their ends, on a
    tile of ``area``."""
    x0, y0, x1, y1 = area

    def hit(e):
        return any(x0 <= x < x1 and y0 <= y < y1 for x, y in fkp.tiles_of(ctx.cat, e))
    out = {n for n, ents in old.items() if any(hit(e) for e in ents)}
    for name, net in flat.nets.items():
        for key, _item in list(net.drivers) + list(net.consumers):
            e = lay.entities.get(key)
            if e is not None and hit(e):
                out.add(name)
                break
    return out


def _chain_lines(flat, r):
    """3.0 chained nets: one line per chain, its segments and how they stand, so the chain reads
    as the one net it is written as."""
    chains = {}
    for name, net in flat.nets.items():
        if net.chain:
            chains.setdefault(net.chain, []).append(name)
    out = []
    for chain, segs in sorted(chains.items()):
        failed = [s for s in segs if s in r.failed]
        out.append(f"  chain     {chain}: {len(segs)} segments ({', '.join(segs)}), "
                   + (f"{len(failed)} unrouted: {', '.join(failed)}" if failed else "all laid"))
    return out


def _keep_failed(ctx, r, old):
    """``route --failed keep`` (the default; Rory, 2026-11-01: "in a live game its better to
    paste and fix after"): a net that fails keeps the route that stood before, where every tile of
    it is still free, so the paste doesn't take up a belt that works. -> (layout, {net: carriers
    put back})."""
    lay = r.layout
    have = {e.owner[1:] for e in lay.entities.values() if e.owner.startswith("~")}
    back = {}
    for net in sorted(r.failed):
        ents = old.get(net)
        if not ents or net in have:
            continue
        taken = {t for e in lay.entities.values() for t in fkp.tiles_of(ctx.cat, e)}
        if any(fkp.tiles_of(ctx.cat, e) & taken or e.key in lay.entities for e in ents):
            continue
        if not back:
            lay = lay.copy()
        for e in ents:
            lay.entities[e.key] = e
        back[net] = len(ents)
    return lay, back


def _route_try(ctx, spec):
    """``route --try X,Y:X,Y`` (3.0, the question mode): how a belt would go from A to B on the
    design as it stands (the net files on its base); nothing is laid or written."""
    try:
        a, b = (tuple(int(v) for v in end.split(",")) for end in spec.split(":"))
        if len(a) != 2 or len(b) != 2:
            raise ValueError
    except ValueError:
        raise FlowError(f"route --try X,Y:X,Y (a belt from tile A to tile B), got {spec!r}")
    import time
    lines = []
    for factory, lay in list(ctx.layouts.items()):
        now = design.layout(ctx.out, factory) or lay
        ctx.layouts[factory] = now
        t0 = time.monotonic()
        got, how = route.try_route(ctx.flats[factory], now, ctx.cat, a, b,
                                   blocked=_frame_blocked(ctx, factory, now.size))
        took = f"{time.monotonic() - t0:.1f} s"
        where = f"{a[0]},{a[1]} -> {b[0]},{b[1]}"
        if got is None:
            lines.append(f"{factory}: try {where}: no belt: {how} ({took})")
            continue
        lines.append(f"{factory}: try {where}: {len(got)} carriers, {how} ({took}); nothing laid")
        lines += [f"  {e.name} {e.x:g},{e.y:g} {fkp.DIRS[e.dir]}" for e in got]
    return lines, []


def _asked(flat, spec):
    """The nets ``route --again`` names: NET[,NET...], each a net's name, or an instance's path
    for every net inside it (``mall1`` is mall1/fe, mall1/g, ...), or ``all``. -> (nets, the
    names that matched none)."""
    nets, unknown = set(), []
    for name in (s.strip() for s in spec.split(",")):
        if not name:
            continue
        hit = {n for n in flat.nets if name == "all" or n == name or n.startswith(name + "/")}
        nets |= hit
        if not hit:
            unknown.append(name)
    return nets, unknown


def _rehand(lay, cat, hand):
    """The standing route's inserters that robots cannot build now take ``hand`` instead: -> (new
    layout, how many changed). A route that stands keeps its carriers (2.0), so ``--hand any`` alone
    would leave the fast inserter a run pasted while none was researched exactly where it was: the
    2026-10-25 run's four needs-research ghosts, on a net that never changes. Only a short hand becomes
    another short hand (the footprint is one tile either way), only one the network cannot build
    now is touched, and only when it can build ``hand``: an inserter that stands built is left
    alone, since it works and its speed is not worth losing."""
    out = dict(lay.entities)
    n = 0
    for key, e in lay.entities.items():
        if not e.owner.startswith("~") or not cat.has_entity(e.name) \
                or cat.kind(e.name) != "inserter" or e.name == hand:
            continue
        if connect.REACH.get(e.name, 1) != 1 or connect.REACH.get(hand, 1) != 1:
            continue
        if availmod.buildable(e.name) or not availmod.buildable(hand):
            continue
        out[key] = fkp.Entity(e.key, hand, e.x, e.y, e.dir, dict(e.attrs))
        n += 1
    if not n:
        return lay, 0
    return lay.copy(entities=out), n


def _fixed_hands(ctx):
    """The keys of the inserters the design names itself: a row whose ``inserter=`` is a prototype
    (not a family word), every tap when the route step was given a concrete hand, and every
    ``Literal``'s entities (a Literal is pinned as it is: ``fkcore.tiers`` pins them too). The
    ``hands`` step leaves them alone."""
    fixed = set()
    for factory, flat in ctx.flats.items():
        lay = ctx.layouts.get(factory)
        if lay is None:
            continue
        for path, inst in flat.insts.items():
            if inst.kind == "Literal":
                fixed |= {k for k, e in lay.entities.items() if e.owner == path}
                continue
            want = (getattr(inst, "kparams", None) or inst.params).get("inserter")
            if want is None or availmod.is_any(want):
                continue
            fixed |= {k for k, e in lay.entities.items() if e.owner == path}
    if ctx.hand_requested and not availmod.is_any(ctx.hand_requested):
        for lay in ctx.layouts.values():
            fixed |= {k for k, e in lay.entities.items() if e.owner.startswith("~")}
    return fixed


def s_hands(ctx, args):
    """Every short hand by what its connector carries (``fkcore.hands``): the lowest rung the
    network can build, raised only where the routed model says the connector cannot pass the flow
    (2.0.15, Rory 2026-10-25). ``--allow inserter,fast-inserter,bulk-inserter`` names the rungs (a
    short hand: reach 1), ``maximize`` takes the top one everywhere, ``--downgrade`` may put a hand
    below the one that stands in the committed .fkp, ``--hand N`` / ``--bulk-hand N`` the force's
    hand sizes the rates are computed with."""
    from . import hands as handsmod
    o = _opts(args, allow=",".join(handsmod.HANDS), downgrade=False, hand="auto", bulk_hand="auto")
    mode = (o.get("_") or ["minimize"])[0]
    allow = [t.strip() for t in str(o["allow"]).split(",") if t.strip()] \
        if o["allow"] is not True else []
    for name in allow:
        if not ctx.cat.has_entity(name) or ctx.cat.kind(name) != "inserter":
            raise FlowError(f"hands --allow {name}: not an inserter prototype")
        if connect.REACH.get(name, 1) != 1:
            raise FlowError(f"hands --allow {name}: not a short hand (a long-handed inserter "
                            "reaches 2 tiles, which is the router's geometry, not a hand's speed)")
    if mode not in ("minimize", "maximize") or not allow:
        raise FlowError("hands minimize|maximize [--allow inserter,fast-inserter] [--downgrade] "
                        "[--hand N] [--bulk-hand N]")
    hand, bulk, note = _hands(ctx, o)
    fixed = _fixed_hands(ctx)
    # a rung is only skipped when something was read: an empty snapshot is not "the network holds
    # nothing", it is "nobody looked yet" (a lab, a paper game, `fk build --to report_checks`)
    gate = availmod.buildable if availmod.snapshot().sup is not None else None
    lines, checks = [], []
    for factory, lay in list(ctx.layouts.items()):
        new, hlines, _changed = handsmod.assign(lay, ctx.flats[factory], ctx.cat,
                                                ctx.previous.get(factory), mode, allow, hand, bulk,
                                                o.get("downgrade") is True, fixed, gate,
                                                model=ctx.model(factory))
        ctx.layouts[factory] = new
        lines += [f"{factory}: {hlines[0]}"] + hlines[1:]
        checks += _invariant_checks(factory, fkp.invariants(new, ctx.cat))
    return lines + [note], checks


def s_tier(ctx, args):
    """Belt tiers per connector (CORE-V2, ``fkcore.tiers``): ``tier minimize|maximize|yellow``
    (default minimize) ``--allow yellow,red,blue,turbo`` (the tiers it may use; default yellow,
    which yellow always is) ``--red N --blue N --turbo N`` (at most N entities gain a tier),
    ``--downgrade`` (minimize and maximize may go below the tier a belt has; without it they
    never do), ``--hand N`` / ``--bulk-hand N`` (the inserter hand sizes, as for report_rates)."""
    from . import tiers
    o = _opts(args, allow="yellow", hand="auto", bulk_hand="auto", all=False)
    mode = (o.get("_") or ["minimize"])[0]
    allow = [t.strip() for t in str(o["allow"]).split(",") if t.strip()] \
        if o["allow"] is not True else []
    unknown = sorted(set(allow) - set(tiers.TIERS))
    if unknown or not allow or mode not in ("minimize", "maximize", "yellow"):
        raise FlowError("tier minimize|maximize|yellow [--allow yellow,red,blue,turbo] "
                        "[--red N] [--blue N] [--turbo N] [--downgrade]"
                        + (f": unknown tier {', '.join(unknown)}" if unknown else ""))
    budgets = {t: _count(o, t) for t in tiers.TIERS if o.get(t) is not None}
    hand, bulk, note = _hands(ctx, o)
    lines, checks = [], []
    for factory, lay in list(ctx.layouts.items()):
        new, tlines, _changed = tiers.assign(ctx.flats[factory], lay, ctx.cat, mode, allow,
                                             budgets, hand=hand, bulk_hand=bulk,
                                             downgrade=o.get("downgrade") is True,
                                             model=ctx.model(factory),
                                             every=o.get("all") is True)
        ctx.layouts[factory] = new
        lines += [f"{factory}: {tlines[0]}"] + tlines[1:]
        checks += _invariant_checks(factory, fkp.invariants(new, ctx.cat))
    return lines + [note], checks


def _own(prev, owner, prefix):
    """The committed .fkp's own entities of cover ``owner`` keyed ``<owner>/<prefix><k>``."""
    if prev is None:
        return []
    pat = re.compile(re.escape(prefix) + r"\d+")
    return [e for e in prev.entities.values()
            if e.owner == owner and pat.fullmatch(e.key[len(owner) + 1:])]


def s_cover_power(ctx, args):
    """Poles for every electric entity (``fkcore.cover``). 1.7 (Rory, 2026-10-21: pin and patch):
    the committed poles stay where they still fit and new ones go only where coverage is missing;
    ``--fresh`` lays every pole anew, as before. 3.1 (Rory 11-08: the world never feeds a step):
    what the diff found built does not reach here; a pinned pole of another kind than ``--pole``
    is laid again in the new kind, and what stands is the diff's difference."""
    o = _opts(args, pole="medium-electric-pole")
    pole, _note = availmod.choose("pole", o["pole"], need=_need(ctx, "electric-pole"))
    if not ctx.cat.has_entity(pole) or ctx.cat.kind(pole) != "electric-pole":
        raise FlowError(f"--pole {pole}: not an electric pole (small-electric-pole, "
                        "medium-electric-pole, big-electric-pole, substation), or any")
    try:
        from . import cover
    except ImportError:
        return ["cover power: fkcore.cover is not built yet; nothing placed"], [
            Check("NO_POWER", "all factories", "no power coverage step yet")]
    from . import domains
    lines, checks = [], []
    for factory, lay in list(ctx.layouts.items()):
        keep_out = _frame_blocked(ctx, factory, lay.size) | _empty_slots(ctx, factory, lay)
        doms = domains.active(ctx.flats.get(factory))
        if doms is not None:
            # 2.1: two power lines or more are domains, each its own network (fkcore.domains)
            prev = ctx.previous.get(factory)
            new, dlines, dchecks = domains.cover_domains(
                ctx.flats[factory], lay, ctx.cat, doms, pole, keep_out=keep_out,
                pinned_of=lambda owner: [] if o.get("fresh") is True else _own(prev, owner, "p"))
            ctx.layouts[factory] = new
            lines += [f"{factory}: {ln}" for ln in dlines]
            checks += [Check(c.code, f"{factory} {c.where}", c.msg) for c in dchecks]
            checks += _invariant_checks(factory, fkp.invariants(new, ctx.cat))
            continue
        pinned = [] if o.get("fresh") is True else _own(ctx.previous.get(factory), "power", "p")
        units = [] if o.get("flat") is True else _power_units(ctx, factory, lay)
        if units:
            # 2.1.35 (Rory 11-07): poles in the hierarchy, as the routes are: each sealed module
            # its own, alike modules alike, then one pass joins them. Laid fresh each build (a
            # unit's poles depend only on what stands in it); ``--flat`` for the one-pass cover
            pinned = []
            poles, rep = cover.cover_power_units(lay, ctx.cat, units, pole=pole,
                                                 keep_out=keep_out)
        else:
            poles, rep = cover.cover_power(lay, ctx.cat, pole=pole, keep_out=keep_out,
                                           pinned=pinned)
        # 2.1.26: the poles just laid, not a second cover_power (the live world's took ~30 s each)
        new = cover.apply_cover(lay, ctx.cat, pole=pole, keep_out=keep_out, pinned=pinned,
                                poles=poles)
        ctx.layouts[factory] = new
        if units:
            lines.append(f"{factory}: by unit: {rep.units[0]} sealed modules laid, "
                         f"{rep.units[1]} took a twin's poles")
        lines.append(f"{factory}: {rep.summary()}"
                     + (f"; {len(keep_out)} terrain tiles kept out" if ctx.ground is not None
                        else ""))
        checks += [Check(c.code, f"{factory} {c.where}", c.msg) for c in rep.checks]
        checks += _invariant_checks(factory, fkp.invariants(new, ctx.cat))
    return lines, checks


def _infra(ctx):
    """The surface .fkp's own entities (shared infrastructure: the kit's roboport, the bridge),
    as last written; [] before the first floorplan."""
    path = os.path.join(ctx.root, f"{ctx.surface.name}.fkp") if ctx.surface is not None else None
    if path is None or not os.path.exists(path):
        return []
    return list(fkp.load(path).entities.values())


def s_cover_robots(ctx, args):
    """Roboports so every logistic chest of a factory is in a logistic area (``fkcore.robots``);
    before ``cover power`` in the script, so the poles power them. A chest the surface's network
    already reaches (the kit's roboport and every roboport joined to it) needs none (1.2). 1.7 (pin
    and patch): the committed roboports stay where they still fit and reach a chest; ``--fresh``
    places them all anew, as before. 3.0 (U5, Rory): no storage chest beside a roboport any more;
    ``--storage`` is ignored with one line. 3.1 (Rory 11-08): the roboports that stand in the
    world never decide where new ones go (2.1.3's chain from the last diff's read is gone); the
    design says, and ``fk robots`` reports what stands against it."""
    from . import robots
    o = _opts(args, roboport="roboport", storage=None)
    if ctx.surface is not None and ctx.surface.free:
        return ["cover robots: a free surface revives its ghosts, no robots build here; nothing "
                "placed"], []
    storage = None
    infra = _infra(ctx)
    places = ctx.surface.places if ctx.surface is not None else {}
    lines, checks = [], []
    if o["storage"] not in (None, "none", "off"):
        lines.append("cover robots: --storage is gone in 3.0 (no chest beside a roboport); ignored")
    for factory, lay in list(ctx.layouts.items()):
        near = robots.served(places, infra, ctx.layouts, factory, ctx.cat)
        keep_out = _frame_blocked(ctx, factory, lay.size)        # no roboport or chest on water
        prev = ctx.previous.get(factory)
        pinned = [] if o.get("fresh") is True else \
            _own(prev, "robots", "r")
        new, rep = robots.apply_robots(lay, ctx.cat, keep_out=keep_out, roboport=o["roboport"],
                                       served=near, storage=storage, pinned=pinned)
        ctx.layouts[factory] = new
        lines.append(f"{factory}: {rep.summary()}")
        checks += [Check(c.code, f"{factory} {c.where}", c.msg) for c in rep.checks]
        checks += _invariant_checks(factory, fkp.invariants(new, ctx.cat))
    return lines, checks


_BRIDGE_POLE = re.compile(r"bridge/p\d+")      # the bridge's own poles (cover bridge power)


def _bridge_in_plots(ents, sf, cat):
    """The bridge's own poles that stand in a plot of the surface file: -> (the entities without
    them, their keys). A plot laid over an old bridge takes its poles, and ``--bridge-power``
    lays the bridge again around the plots (1.2: the 0534 run's new plot stopped the build with
    malformed shared infrastructure)."""
    boxes = [(p.x, p.y, p.x + p.w, p.y + p.h) for p in sf.places.values()
             if p.lifecycle != "retired"]
    keep, gone = {}, []
    for key, e in ents.items():
        if _BRIDGE_POLE.fullmatch(key) and cat.has_entity(e.name) \
                and cat.kind(e.name) == "electric-pole" \
                and any(b[0] <= x < b[2] and b[1] <= y < b[3] for b in boxes
                        for x, y in fkp.tiles_of(cat, e)):
            gone.append(key)
            continue
        keep[key] = e
    return keep, sorted(gone, key=fkp.natural_key)


def _circuits(ctx):
    """2.0 (circuits in the source, ``fkcore.circuits``): each factory's circuit settings and
    wires, laid again from its source on the layout as it stands, before the floorplan composes
    it. -> (report lines, checks)."""
    from . import circuits
    lines, checks = [], []
    for factory, lay in list(ctx.layouts.items()):
        flat = ctx.flats.get(factory)
        if flat is None:
            continue
        new, clines, cchecks = circuits.lay(flat, lay, ctx.cat,
                                            keep_out=_frame_blocked(ctx, factory, lay.size))
        ctx.layouts[factory] = new
        lines += [f"circuits {factory}: {line}" for line in clines]
        checks += [Check(c.code, f"{factory} {c.where}", c.msg) for c in cchecks]
        checks += _invariant_checks(factory, fkp.invariants(new, ctx.cat))
    return lines, checks


def s_floorplan(ctx, args):
    sf = ctx.surface
    o = _opts(args)
    infra_path = os.path.join(ctx.root, f"{sf.name}.fkp")
    infra = None
    lines, checks = _circuits(ctx)
    if os.path.exists(infra_path):
        prev = fkp.load(infra_path)
        ents = prev.entities
        if o.get("bridge_power"):
            ents, gone = _bridge_in_plots(prev.entities, sf, ctx.cat)
            if gone:
                lines.append(f"bridge power: {len(gone)} of its poles stand in a plot now "
                             f"({', '.join(gone)}): taken out, the bridge is laid again around "
                             "the plots")
        infra = fkp.Layout("surface", sf.name, covers=prev.covers, marks=prev.marks,
                           entities=ents)
    ctx.surface_lay = surfacemod.floorplan(sf, infra)
    ctx.composed = compose.compose(ctx.surface_lay, ctx.layouts, ctx.cat)
    if o.get("bridge_power"):
        from . import cover
        pole = o["bridge_power"] if isinstance(o["bridge_power"], str) else "medium-electric-pole"
        pole, _note = availmod.choose("pole", pole, need=_need(ctx, "electric-pole"))
        if not ctx.cat.has_entity(pole) or ctx.cat.kind(pole) != "electric-pole":
            raise FlowError(f"--bridge-power {pole}: not an electric pole (small-electric-pole, "
                            "medium-electric-pole, big-electric-pole, substation), or any")
        s = ctx.composed
        keep = set()
        for p in s.plots.values():
            keep |= {(x, y) for x in range(p.x, p.x + p.w) for y in range(p.y, p.y + p.h)}
        for c in s.claims.values():
            keep |= {(x, y) for x in range(c.x0, c.x1) for y in range(c.y0, c.y1)}
        for m in s.marks.values():
            keep |= {(int(x), int(y)) for x, y in m.tiles}
        keep |= ctx.terrain                          # no bridge pole on water or a cliff
        poles, blines = cover.bridge(list(s.entities.values()), ctx.cat, keep, pole)
        if poles and "bridge" not in ctx.surface_lay.covers:
            ctx.surface_lay.covers["bridge"] = fkp.Cover("bridge", "power", {"pole": pole})
        for e in poles:
            ctx.surface_lay.add(e)
        lines += [f"bridge power: {l}" for l in blines] or ["bridge power: every network is "
                                                             "already joined"]
        ctx.composed = compose.compose(ctx.surface_lay, ctx.layouts, ctx.cat)
    s = ctx.composed
    lines.insert(0, f"surface {sf.name}: {len(s.plots)} plots, {len(s.claims)} claims, "
                    f"{len(s.marks)} marks, {len(s.entities)} entities composed, {len(s.tiles)} tiles")
    if ctx.ground is not None:
        tlines, tchecks = _on_terrain(ctx.ground, s, ctx.cat)
        lines += tlines
        checks += tchecks
    for name, p in sorted(s.plots.items()):
        lay = ctx.layouts.get(name)
        size = f"{lay.size[0]}x{lay.size[1]}" if lay is not None and lay.size else "?"
        lines.append(f"  plot {name} at {p.x},{p.y} rot {p.rot} {p.w}x{p.h} (factory {size})")
    try:
        from . import cover
    except ImportError:
        cover = None
    if cover is not None:
        nets = cover.networks(list(s.entities.values()), ctx.cat)
        lines.append(f"power: {len(nets)} wire network{'s' if len(nets) != 1 else ''}, "
                     f"{sum(1 for n in nets if n['sources'])} with generation")
        for name in sorted(s.plots):
            mine = {k for k in s.entities if k.startswith(f"{sf.name}:{name}/")
                    and ctx.cat.kind(s.entities[k].name) == "electric-pole"}
            powered = any(n["sources"] for n in nets if mine & set(n["poles"]))
            lines.append(f"  {name}: {len(mine)} poles, "
                         + ("on a grid with generation" if powered else "NOT on a grid with "
                                                                         "generation"))
            if mine and not powered:
                checks.append(Check("NO_POWER", f"{sf.name} {name}",
                                    "its grid reaches no generator (bridge it: floorplan "
                                    "--bridge-power)"))
    _write_domains(ctx, s)
    from . import robots
    rlines, rchecks = robots.check_surface(s, ctx.cat)
    return lines + rlines, checks + rchecks + _invariant_checks(sf.name, s.problems)


def _write_domains(ctx, s):
    """2.1: ``out/power-domains.json``, each power domain's poles where they stand on the surface
    ({"domains": {name: [[x, y], ...]}}), for run stats to split power by domain
    (``fkcore.runstats``); taken away when no factory has domains."""
    import json
    from . import domains as dommod
    path = os.path.join(ctx.root, "out", "power-domains.json")
    got = {}
    for factory in sorted(ctx.layouts):
        doms = dommod.active(ctx.flats.get(factory))
        if not doms:
            continue
        owners = {d.owner: d.name for d in doms}
        for k in sorted(s.entities, key=fkp.natural_key):
            head = f"{s.name}:{factory}/"
            if not k.startswith(head):
                continue
            e = s.entities[k]
            owner = k[len(head):].rsplit("/", 1)[0]
            name = owners.get(owner) or e.attrs.get("domain")
            if name and ctx.cat.has_entity(e.name) and ctx.cat.kind(e.name) == "electric-pole":
                got.setdefault(name, []).append([float(e.x), float(e.y)])
    if not got:
        if os.path.exists(path):
            os.remove(path)
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"domains": got}, fh, sort_keys=True)
        fh.write("\n")


def _on_terrain(t, s, cat):
    """``on-water-or-cliff``: a composed entity on blocked ground (water, cliff); ``no-ore``: a drill
    with no ore of its resource in its mining area (the game would not place it); ``no-water``
    (1.4.2): an offshore pump whose water tiles are dry ground (the lead world's kickoff plant
    stood where world 3 had a lake: no water, no power, and every look said ok). -> (report
    lines, checks); checks, never gates."""
    lines, checks, hits = [], [], 0
    # a pack with landfill (Sea Block): water is ground that costs landfill, so it is no check
    wet = frozenset() if packmod.landfill() else t.water
    for key in sorted(s.entities, key=fkp.natural_key):
        e = s.entities[key]
        tiles = set(e.tiles)
        parts = [(len(tiles & wet), "water"), (len(tiles & t.cliff_tiles), "cliff"),
                 (len(tiles & t.other), "other blocked")]
        if not any(n for n, _k in parts):
            continue
        hits += 1
        first = min(tiles & t.blocked, key=lambda q: (q[1], q[0]))
        what = ", ".join(f"{n} {k}" for n, k in parts if n)
        checks.append(Check("on-water-or-cliff", key,
                            f"{e.name} at {fkp.fmt(e.x)},{fkp.fmt(e.y)} stands on {what} "
                            f"tile{'s' if sum(n for n, _k in parts) != 1 else ''} (first "
                            f"{first[0]},{first[1]})"))
    if packmod.landfill():
        n = len(landfill_tiles(s.entities.values(), t, cat))
        if n:
            lines.append(f"  landfill: {n} entity tiles stand on water; the paste lays "
                         f"{packmod.landfill()} under them")
    x0, y0, x1, y1 = t.area
    unknown = {tuple(c) for c in t.ungenerated}
    # 2.1.19 (the live run, ~4245k): the terrain stopped at x480 and cu2p was routed on to x498,
    # over cliffs it had never read, and nothing said so. An entity on ground the terrain does not
    # hold is a no-terrain line, with the area to read
    def unread(q):
        return not (x0 <= q[0] < x1 and y0 <= q[1] < y1) or (q[0] // 32, q[1] // 32) in unknown
    blind = sorted((k for k, e in s.entities.items() if any(unread(q) for q in e.tiles)),
                   key=fkp.natural_key)
    if blind:
        qs = [q for k in blind for q in s.entities[k].tiles if unread(q)]
        bx0, by0 = min(q[0] for q in qs), min(q[1] for q in qs)
        bx1, by1 = max(q[0] for q in qs) + 1, max(q[1] for q in qs) + 1
        checks.append(Check("no-terrain", f"{len(blind)} entities",
                            f"stand on ground the terrain has not read ({bx0},{by0} to {bx1},{by1}; "
                            f"first {', '.join(blind[:4])}): cliffs and water there are not "
                            f"checked. fk read-terrain --area {bx0 - 8},{by0 - 8},{bx1 + 8},"
                            f"{by1 + 8}, then build again"))
    for key in sorted(s.entities, key=fkp.natural_key):
        e = s.entities[key]
        wet = cat.water_tiles(e.name, e.x, e.y, e.dir) if cat.has_entity(e.name) else set()
        seen = {q for q in wet if x0 <= q[0] < x1 and y0 <= q[1] < y1
                and (q[0] // 32, q[1] // 32) not in unknown}
        dry = sorted(seen - t.water, key=lambda q: (q[1], q[0]))
        if dry:
            checks.append(Check("no-water", key,
                                f"{e.name} at {fkp.fmt(e.x)},{fkp.fmt(e.y)}: {len(dry)} of the "
                                f"{len(wet)} tiles it pumps from are not water (first "
                                f"{dry[0][0]},{dry[0][1]}): it gives no water"))
    drills = sorted((e for e in s.entities.values() if e.attrs.get("resource")
                     and cat.has_entity(e.name) and cat.kind(e.name) == "mining-drill"),
                    key=lambda e: fkp.natural_key(e.key))
    full = under = amount = 0
    for e in drills:
        ore = t.resources.get(e.attrs["resource"], {})
        body = [ore[q] for q in e.tiles if q in ore]
        full += len(body) == len(e.tiles)
        under += len(body)
        amount += sum(body)
        # 3.0 (U11): a pumpjack stands on its well, so its own tiles are where the crude is (its
        # mining area is not; every pumpjack on a field read no-ore)
        area = e.tiles if e.name == "pumpjack" else \
            placer.mining_area(e.name, float(e.x), float(e.y))
        if not any(q in ore for q in area):
            checks.append(Check("no-ore", e.key,
                                f"{e.name} at {fkp.fmt(e.x)},{fkp.fmt(e.y)}: no "
                                f"{e.attrs['resource']} in its mining area"))
    lines.append(f"terrain: {hits} of {len(s.entities)} entities on blocked ground; "
                 + (f"{full}/{len(drills)} drills with ore under every tile, {under} ore tiles "
                    f"under them ({terrainmod.fmt_amount(amount)})" if drills else "no drills"))
    return lines, checks


def _bootstrap_checks(ctx, factory, flat, lay):
    """bootstrap (2.0.4, the live run's mall): a net whose own route needs a building item (a
    belt, an underground, a splitter, an inserter ...: a carrier whose name is also an item) that
    only makers fed from that same net make. Then the route cannot be built before its makers,
    and its makers cannot run before the route is built.

    For each net: the items of the carriers on it (``e.attrs["net"]``, lanes included), the
    machines that make each item, and whether every one of them takes an item from this net. One
    line per net, and none when there is no maker in the factory (the item comes from outside, a
    raw resource or stock) or a maker independent of the net (a second row, a requester chest)."""
    on_net = {}
    for e in lay.entities.values():
        for name in fkp.net_names(e.attrs.get("net")):
            on_net.setdefault(name, []).append(e)
    out = []
    for net in flat.nets.values():
        ents = on_net.get(net.name)
        if not ents:
            continue
        counts = {}
        for e in ents:
            counts[e.name] = counts.get(e.name, 0) + 1
        fed_by = {key for key, _item in net.consumers}
        rows = []
        for item in sorted(counts, key=lambda i: (-counts[i], i)):
            if not ctx.cat.is_item(item):
                continue                    # not a building item (a carrier that is no item)
            makers = [m for m in flat.machines.values() if item in m.makes]
            if not makers:                  # it comes from outside: stock, a raw resource
                continue
            if not all(m.key in fed_by for m in makers):
                continue                    # a maker that does not depend on this net
            keys = {m.key for m in makers}
            takes = sorted({i for key, i in net.consumers if key in keys})
            rows.append((item, counts[item], makers, takes))
        if not rows:
            continue
        clauses = []
        for item, _count, makers, takes in rows:
            insts = sorted({f"{factory}/{m.inst}" for m in makers})
            only = f"the only maker ({insts[0]})" if len(insts) == 1 \
                else f"the only makers ({', '.join(insts)})"
            clauses.append(f"{only} {'takes' if len(insts) == 1 else 'take'} "
                           f"{', '.join(takes)} from this same net")
        if len(rows) == 1:
            msg = (f"its route needs {rows[0][1]} {rows[0][0]}, and {clauses[0]}: stock the "
                   f"{rows[0][0]} first, or build that maker first")
        else:
            needs = " and ".join(f"{count} {item}" for item, count, _m, _t in rows)
            detail = "; ".join(f"{item}: {clause}" for (item, _n, _m, _t), clause
                               in zip(rows, clauses))
            msg = (f"its route needs {needs}: {detail}: stock what the route needs first, or "
                   "build those makers first")
        if getattr(net, "chain", None):
            msg += f" ({net.name} is a stretch of chain {net.chain})"
        out.append(Check("bootstrap", f"net {factory}/{net.name}", msg))
    return out


def s_report_checks(ctx, args):
    """Connectivity of every net and the invariants, now; then every check line of the run."""
    lines, checks = [], []
    for factory, lay in ctx.layouts.items():
        flat = ctx.flats[factory]
        ports = expand.port_edges(flat, lay, ctx.cat) if flat.ports else None   # 2.0.48
        rep = connect.connect(lay, ctx.cat, connect.endpoints(flat), ports)
        lines.append(f"{factory}: {rep.summary()}")
        checks += [Check(c.code, f"{factory} {c.where}", c.msg) for c in rep.checks]
        checks += [Check(c.code, f"{factory} {c.where}", c.msg)
                   for c in connect.long_pipes(rep.graph)]          # 3.0
        sink_lines, sink_checks = _sinks(ctx, factory, flat, rep)
        lines += sink_lines
        checks += sink_checks
        checks += _invariant_checks(factory, fkp.invariants(lay, ctx.cat, nets=flat.nets))
        seen = {c.where for _step, c in ctx.checks if c.code == "bootstrap"}
        checks += [c for c in _bootstrap_checks(ctx, factory, flat, lay) if c.where not in seen]
    if ctx.composed is not None:
        checks += _invariant_checks(ctx.surface.name, ctx.composed.problems)
    run = [(step, c) for step, c in ctx.checks] + [("report_checks", c) for c in checks]
    codes = {}
    for _step, c in run:
        codes[c.code] = codes.get(c.code, 0) + 1
    lines.append("check lines this run: " + (", ".join(f"{k} {v}" for k, v in sorted(codes.items()))
                                             or "none"))
    lines += [f"  {step:<13} {c}" for step, c in run]
    return lines, checks


#: the machines that make something for a net (2.0.12, NO_SINK)
_MAKERS = ("assembling-machine", "furnace", "mining-drill")


def _sinks(ctx, factory, flat, rep):
    """2.0.12: does each maker's product reach a sink? One report line, and ``no-destination`` for a
    maker whose product reaches none of the consumers of a net it drives while the net's other
    pairs connect (a net none of whose pairs connect has its no-route line). The solo run's Mall4
    went out with its products belt unrouted and its makers had no output (2026-10-23 23:23)."""
    def maker(m):
        try:
            return bool(m.makes) and not m.source and ctx.cat.kind(m.prototype) in _MAKERS
        except KeyError:
            return False
    makers = {k for k, m in flat.machines.items() if maker(m)}
    feeding = {d for r in rep.nets.values() if r.consumers for d in r.drivers if d in makers}
    if not feeding:
        return [], []
    cut = connect.cut_off(rep, makers)
    keys = sorted({k for k, _n, _c in cut}, key=fkp.natural_key)
    line = (f"  sinks     {len(feeding) - len(keys)} of {len(feeding)} makers on a net reach a sink "
            "with what they make")
    if cut:
        line += ("; none reached: " + ", ".join(f"{k} (net {n})" for k, n, _c in cut[:4])
                 + (f" ... {len(cut) - 4} more" if len(cut) > 4 else ""))
    checks = [Check("no-destination", f"{factory} {k}",
                    f"what it makes reaches none of the {c} consumer{'s' if c != 1 else ''} of net "
                    f"{n}: it has nowhere to go")
              for k, n, c in cut if rep.nets[n].reached]
    return [line], checks


def _to_build(ctx):
    """{entity: count} still to build: after a diff its not-placed, ghost and other-entity entities, else
    the whole composed design."""
    need = {}
    if ctx.last_diff is not None:
        for v in ctx.last_diff.verdicts:
            if v.verdict.split()[0] in ("not-placed", "ghost", "other-entity"):
                need[v.name] = need.get(v.name, 0) + 1
        return need, "what the last diff found missing, ghosted or wrong"
    for e in (ctx.composed.entities.values() if ctx.composed is not None else ()):
        if not e.attrs.get("dnp"):                   # 2.0.63: never built
            need[e.name] = need.get(e.name, 0) + 1
    return need, "the whole design (no diff before this step)"


def _to_build_entities(ctx):
    """The composed entities :func:`_to_build` counts: after a diff those it found missing,
    ghosted or wrong (by name and position), else every one the design builds."""
    es = [e for e in ctx.composed.entities.values() if not e.attrs.get("dnp")]
    if ctx.last_diff is None:
        return es
    todo = {(v.name, float(v.x), float(v.y)) for v in ctx.last_diff.verdicts
            if v.verdict.split()[0] in ("not-placed", "ghost", "other-entity")}
    return [e for e in es if (e.name, float(e.x), float(e.y)) in todo]


def landfill_tiles(entities, t, cat):
    """The water tiles under ``entities`` that take landfill: each entity's own tiles, except an
    offshore pump's water side (the tiles it pumps from stay water)."""
    out = set()
    for e in entities:
        tiles = set(e.tiles) & t.water
        if tiles and cat.has_entity(e.name):
            tiles -= cat.water_tiles(e.name, e.x, e.y, e.dir)
        out |= tiles
    return out


def _bom_live(ctx):
    """report_bom --stock live: what still has to be built against the network's stock, and every
    item and machine recipe against what is researched (short-stock, needs-research). The first V1 run
    (2026-10-20) waited on 295 ghosts the network held nothing for."""
    from . import supply
    r = _link(ctx)
    if r is None:
        return [f"report_bom --stock live: skipped, no game link ({ctx.link_error})"], []
    need, what = _to_build(ctx)
    # a pack with landfill: one landfill under each entity tile on water (the paste lays it)
    if packmod.landfill() and ctx.ground is not None and ctx.composed is not None:
        todo = _to_build_entities(ctx)
        n = len(landfill_tiles(todo, ctx.ground, ctx.cat))
        if n:
            need[packmod.landfill_item()] = need.get(packmod.landfill_item(), 0) + n
    used = sorted({e.attrs["recipe"] for e in (ctx.composed.entities.values() if ctx.composed
                                                else ()) if e.attrs.get("recipe")
                   and ctx.cat.kind(e.name) == "assembling-machine"})
    # the availability families too: this is the read the next (linkless) build's inserter=any,
    # --hand any and --pole any choose from (out/avail.json, beside out/force.json)
    # 3.0: and every entity laid, and every one read before (it stays read): a compiled inside is
    # only reused when none of its entities needs research (compiled.reuse, avail.locked)
    laid = sorted({e.name for e in ctx.composed.entities.values()}) if ctx.composed else []
    before = list(availmod.snapshot().sup.entities) if availmod.snapshot().sup else []
    sup = supply.read(r, _force(ctx), ctx.surface.name,
                      list(dict.fromkeys(list(need) + list(availmod.CANDIDATES) + laid + before)),
                      used)
    availmod.save(ctx.root, sup, "report_bom --stock live")
    lines, checks = supply.bom(need, sup, used)
    # 3.0 (fair play): an inserter set to vectors the force has not researched (Bob's adjustable
    # inserters: the GUI's own limits) waits on that research like an unresearched entity
    for tech, keys in sorted(armsmod.locked(ctx.composed.entities.values() if ctx.composed
                                            else (), armsmod.of(sup)).items()):
        checks.append(Check("needs-research", f"inserter vectors ({tech})",
                            f"{len(keys)} inserter{'s' if len(keys) != 1 else ''} "
                            f"({', '.join(keys[:4])}{' ...' if len(keys) > 4 else ''}) reach "
                            f"further or wider than the force may set: research {tech}"))
    lines = ([lines[0], f"  to build: {what}; {sum(need.values())} entities",
              f"  {supply.research_line(sup.research)}"] + lines[1:])
    # the first lead run (2026-10-21): a paste took up the burner row before the 137 belts its
    # replacement needed were made (23 in stock), and iron stood still until they were
    short = [c.where for c in checks if c.code == "short-stock"]
    running = _machines_taken_up(ctx) if short else []
    if running:
        warn = (f"the paste takes up {len(running)} machine{'s' if len(running) != 1 else ''} "
                f"that stand now while {len(short)} kind{'s' if len(short) != 1 else ''} it lays "
                f"are SHORT ({', '.join(short[:6])}): what they make stops until those are made; "
                "`stop_on short-stock` in the build script holds the paste until the network has them")
        lines.append(f"  {warn}")
        checks.append(Check("taken-up", ctx.surface.name, warn))
    # 2.0.73 (live 10-30): a net laid again pasted 549 belts SHORT, and the robots took the old
    # mains up before the new ones could be built, which stopped the base
    carrying = _carriers_taken_up(ctx) if short else 0
    if carrying and any(s in _CARRIER_KINDS_SHORT for s in short):
        kinds = [s for s in short if s in _CARRIER_KINDS_SHORT]
        warn = (f"the paste takes up {carrying} belts and pipes that carry now while "
                f"{', '.join(kinds)} for their new route are SHORT: those nets stop until the "
                "new route is built; `stop_on short-stock` in the build script holds the paste, "
                "and out/fkp-history/ keeps the layout before this one")
        lines.append(f"  {warn}")
        checks.append(Check("taken-up", ctx.surface.name, warn))
    far = _reach(ctx)
    if far is not None:
        lines += far[0]
        checks.append(far[1])
    return lines, checks


#: how many ghosts out of reach report_bom names one by one
_REACH_SHOWN = 40


def _reach(ctx):
    """``REACH`` (2.1.3): the ghosts the paste lays outside every construction area that stands
    with power, or that its own roboports give it (``robots.reach``): robots never build them.
    -> (report lines, Check), or None when every ghost is in reach or the diff read nothing. The
    runs: a cover-robots roboport out of every area stood a ghost with the 47 entities it was to
    cover, and 26 ghosts of a big paste waited out of reach until the player found them."""
    from . import robots
    if ctx.last_diff is None or ctx.roboports is None or ctx.surface.free:
        return None
    rows = [(v.key, v.name, v.x, v.y) for v in ctx.last_diff.verdicts
            if v.verdict.split()[0] in _TO_LAY]
    far = robots.reach(rows, ctx.roboports, ctx.cat)
    if not far:
        return None

    def name(row):
        return f"{(row[0] or row[1]).split(':', 1)[-1]} ({row[1]} {fkp.fmt(row[2])},{fkp.fmt(row[3])})"
    ports = [row for row in far if ctx.cat.kind(row[1]) == "roboport"]
    msg = (f"{len(far)} of the {len(rows)} ghosts the paste lays are outside every construction "
           f"area that stands with power, or that the paste's own roboports give it: robots never "
           f"build them ({', '.join(name(row) for row in far[:4])}{' ...' if len(far) > 4 else ''})")
    if ports:
        msg += (f"; {len(ports)} of them are roboports, which bring no robots where none reach "
                "them: lay one inside a standing network's area first")
    msg += "; `stop_on reach` holds such a paste"
    lines = [f"  REACH: {len(far)} ghosts out of every construction area:"]
    lines += [f"    {name(row)}" for row in far[:_REACH_SHOWN]]
    if len(far) > _REACH_SHOWN:
        lines.append(f"    ... {len(far) - _REACH_SHOWN} more")
    return lines, Check("REACH", ctx.surface.name, msg)


#: the carriers whose shortage leaves a re-laid net cut (inserters stay with their machine)
_CARRIER_KINDS_SHORT = ("transport-belt", "fast-transport-belt", "express-transport-belt",
                        "underground-belt", "fast-underground-belt", "express-underground-belt",
                        "splitter", "fast-splitter", "express-splitter", "pipe",
                        "pipe-to-ground")


def _carriers_taken_up(ctx):
    """How many built belts and pipes the last diff says the paste takes up (the toolkit's own
    dropped from the design, or under a other-entity verdict)."""
    if ctx.last_diff is None:
        return 0
    n = 0
    for v in ctx.last_diff.verdicts:
        head = v.verdict.split()[0]
        if head == "unowned" and not v.ghost and v.name in _CARRIER_KINDS_SHORT:
            n += 1
        elif head == "other-entity":
            n += sum(1 for u in v.under if len(u) > 4 and not u[4]
                     and u[0] in _CARRIER_KINDS_SHORT)
    return n


#: what a paste that takes it up stops (2.0, report_bom's warning)
_MACHINE_KINDS = ("assembling-machine", "furnace", "mining-drill", "lab", "boiler", "generator",
                  "rocket-silo")


def _machines_taken_up(ctx):
    """The built machines the last diff says the paste takes up: a unowned one that is no ghost,
    and what stands built under a other-entity verdict. [name]."""
    if ctx.last_diff is None:
        return []

    def machine(name):
        try:
            return ctx.cat.kind(name) in _MACHINE_KINDS
        except (KeyError, ValueError):
            return False
    out = []
    for v in ctx.last_diff.verdicts:
        head = v.verdict.split()[0]
        if head == "unowned" and not v.ghost and machine(v.name):
            out.append(v.name)
        elif head == "other-entity":
            out += [u[0] for u in v.under if len(u) > 4 and not u[4] and machine(u[0])]
    return out


def s_report_bom(ctx, args):
    o = _opts(args, stock="none")
    if o["stock"] == "live":
        return _bom_live(ctx)
    need = {}
    ents = (ctx.composed.entities.values() if ctx.composed is not None
            else [e for lay in ctx.layouts.values() for e in lay.entities.values()])
    for e in ents:
        if not e.attrs.get("dnp"):                   # 2.0.63: never built
            need[e.name] = need.get(e.name, 0) + 1
    stock = None
    if o["stock"] == "live":
        lines = ["stock live: needs the game link, which this offline build has not; needs only"]
    elif o["stock"] != "none":
        with open(os.path.join(ctx.root, o["stock"]), encoding="utf-8") as fh:
            stock = json.load(fh)
        lines = [f"stock from {o['stock']}"]
    else:
        lines = ["stock none: needs only"]
    checks = []
    for name in sorted(need, key=lambda n: (-need[n], n)):
        have = None if stock is None else int(stock.get(name, 0))
        lines.append(f"  {name:<28} {need[name]:>5}" + ("" if have is None else f"  have {have}"))
        if have is not None and have < need[name]:
            checks.append(Check("short-stock", name, f"need {need[name]}, have {have}"))
    lines.append(f"  {'total':<28} {sum(need.values()):>5}")
    return lines, checks


#: how many earlier copies of each .fkp out/fkp-history keeps
KEEP_PRIOR = 8


def _keep_prior(ctx, path, text):
    """Before ``path`` is written with ``text``: its current content, when different, copied to
    out/fkp-history/<name>.<stamp>.fkp, the newest KEEP_PRIOR kept (2.0.73, live 10-30: a paste
    that tore the mains up had overwritten world.fkp, with no way back)."""
    try:
        with open(path, encoding="utf-8") as fh:
            old = fh.read()
    except OSError:
        return
    if old == text:
        return
    folder = os.path.join(ctx.out, "fkp-history")
    os.makedirs(folder, exist_ok=True)
    base = os.path.basename(path)[:-len(".fkp")]
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(path)))
    with open(os.path.join(folder, f"{base}.{stamp}.fkp"), "w", encoding="utf-8",
              newline="\n") as fh:
        fh.write(old)
    mine = sorted(f for f in os.listdir(folder)
                  if f.startswith(base + ".") and f.endswith(".fkp")
                  and re.fullmatch(r"\d{8}-\d{6}", f[len(base) + 1:-4]))
    for f in mine[:-KEEP_PRIOR]:
        os.remove(os.path.join(folder, f))


def s_write_layout(ctx, args):
    lines, checks = [], []
    if ctx.surface is not None and ownmod.load(ctx.root, ctx.surface.name) is None:
        # 1.2: the toolkit's own entities start as the .fkp files last written (what was built
        # from them), before this step writes the new ones; each play apply keeps it after that
        record = ownmod.seed(ctx.root, ctx.surface.name, ctx.cat)
        ownmod.save(ctx.root, ctx.surface.name, record)
        lines.append(f"own: {len(record)} entities of the last written .fkp files are the "
                     f"toolkit's own (out/own/{ctx.surface.name}.json)")
    for p in _factories(ctx):
        lay = ctx.layouts.get(p.factory)
        if lay is None:
            continue
        path = os.path.join(ctx.root, f"{p.module}.fkp")
        try:
            _keep_prior(ctx, path, fkp.dumps(lay, ctx.cat))
            fkp.save(lay, path, ctx.cat)
            lines.append(f"wrote {os.path.basename(path)}: {len(lay.entities)} entities")
        except fkp.InvariantError as exc:
            checks += _invariant_checks(p.factory, exc.problems)
    if ctx.surface_lay is not None:
        path = os.path.join(ctx.root, f"{ctx.surface.name}.fkp")
        try:
            _keep_prior(ctx, path, fkp.dumps(ctx.surface_lay, ctx.cat))
            fkp.save(ctx.surface_lay, path, ctx.cat)
            lines.append(f"wrote {os.path.basename(path)}: {len(ctx.surface_lay.plots)} plots")
        except fkp.InvariantError as exc:
            checks += _invariant_checks(ctx.surface.name, exc.problems)
    # 2.0.4: the source these .fkp files were laid from, for the look (layout_source_changes)
    os.makedirs(ctx.out, exist_ok=True)
    with open(os.path.join(ctx.out, _LAID_FILE), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"files": ctx.source}, fh, indent=0, sort_keys=True)
        fh.write("\n")
    return lines, checks


def _link(ctx):
    """The game link, opened on first use; None (``ctx.link_error`` says why) without one."""
    if ctx.link is None and ctx.link_error is None:
        from . import link
        ctx.link, ctx.link_error = link.open_from_env()
    return ctx.link


def _scope(ctx, o):
    """(area, key prefixes) from --area X0,Y0,X1,Y1 and --plot NAME[,NAME]; None, None = all."""
    area = tuple(int(v) for v in o["area"].split(",")) if isinstance(o.get("area"), str) else None
    keys = None
    if isinstance(o.get("plot"), str):
        keys = [f"{ctx.surface.name}:{p}/" for p in o["plot"].split(",")]
        if area is None:
            boxes = [ctx.composed.plots[p].box() for p in o["plot"].split(",")
                     if p in ctx.composed.plots]
            if boxes:
                area = (min(b[0] for b in boxes) - 2, min(b[1] for b in boxes) - 2,
                        max(b[2] for b in boxes) + 2, max(b[3] for b in boxes) + 2)
    return area, keys


def _net_items(ctx):
    return {f"{factory}/{n}": list(net.items) for factory, flat in ctx.flats.items()
            for n, net in flat.nets.items()}


def s_diff(ctx, args):
    o = _opts(args, scope="all", confirm=False, yes=False)
    if ctx.composed is None:
        return ["diff: no composed surface yet (floorplan first)"], []
    r = _link(ctx)
    if r is None:
        return [f"diff: skipped, no game link ({ctx.link_error})"], []
    from . import apply as applymod
    area, keys = _scope(ctx, o)
    own = ownmod.load(ctx.root, ctx.surface.name)
    if area is None:
        area = applymod.default_area(ctx.composed, ctx.cat, keys, own)
    if area is None:
        return ["diff: nothing is designed on this surface"], []
    rep = applymod.diff(r, ctx.composed, ctx.cat, area, ctx.surface, _net_items(ctx), own=own,
                        force=_force(ctx), confirm=bool(o["confirm"]),
                        placements=_placements(ctx))
    try:
        _save_hands(ctx, r)            # for the next build's rates and tiers, on the laptop
    except Exception:                  # noqa: BLE001 - they fall back to hand 1, and say so
        pass
    if not ctx.surface.free:
        ctx.roboports = _read_roboports(ctx, r)      # REACH and UNREACHED, this build only
    ghosts = sorted({v.name for v in rep.verdicts if v.verdict == "ghost"})
    if ghosts:
        from . import supply
        supply.mark_ghosts(rep.verdicts, supply.read(r, _force(ctx), ctx.surface.name, ghosts))
    ctx.last_diff = rep
    checks = [Check("diff", ctx.surface.name, f"{k} {n}") for k, n in sorted(rep.counts().items())
              if k != "ok"]
    lines = rep.lines()
    from . import sense
    # 3.1 (Rory 11-08): a design tile something else stands on is a MISMATCH the apply refuses
    bad = sense.conflicts(rep)
    if bad:
        lines.append(f"mismatch: {len(bad)} design tiles have something standing on them the "
                     "toolkit did not paste; the apply refuses them and pastes the rest:")
        lines += [f"  {c.line()}" for c in bad]
        checks += [Check("mismatch", ctx.surface.name, c.line()) for c in bad]
    # 2.0 (Rory, 2026-10-21): a hand change the build would replace is said before the paste
    hand = sense.hand_edits(rep)
    if hand:
        lines.append(f"hand: {len(hand)} hand edits that the apply would replace, remove or set "
                     "back:")
        lines += [f"  {line}" for line in hand]
        # 3.0 (the retro: builds undid pumps, hand upgrades and filters set in the game): with
        # `stop_on by-hand` the paste waits; `fk build --yes` (diff --yes) is the player's OK for
        # this one build, and adopting the change into the design keeps it
        code = "by-hand-ok" if o["yes"] is True else "by-hand"
        if code == "by-hand":
            lines.append("  keep one: put it in the design (fk adopt, a Literal, a set line); "
                         "paste over them: fk build --yes")
        checks += [Check(code, ctx.surface.name, line) for line in hand]
    turned = _turned(ctx, rep)
    if turned:
        lines.append(f"  {turned.msg}")
        checks.append(turned)
    if not ctx.surface.free:
        far = _unreached(ctx, r, rep)
        if far:
            lines.append(f"  {far.msg}")
            checks.append(far)
    pts = applymod.rip_points(rep)
    if pts and not ctx.surface.free:
        # 1.1: what the apply will take up, and how long the robots need for it (before it starts)
        from . import link
        try:
            load = applymod.rip_load(r, ctx.surface.name, _force(ctx), pts)
        except (ValueError, OSError, link.LinkError) as exc:
            lines.append(f"rip: not estimated ({type(exc).__name__}: {exc})")
        else:
            line, warn = applymod.rip_report(load, applymod.moved(rep))
            lines.append(line)
            if warn:
                checks.append(Check("taken-up", ctx.surface.name, warn))
    return lines, checks


#: the diff's verdicts the paste lays an entity for
_TO_LAY = ("not-placed", "ghost", "other-entity")


def _turned(ctx, rep):
    """A ``re-laid`` check for the belts, undergrounds, splitters, inserters (and pipes-to-ground,
    pumps, drills) that stand, or wait as ghosts, on their design entity's position under its name
    but facing another way or as the other underground end, or None. Their lines stop there. 2.0.5
    (the 2.0 run's south iron array, 2026-10-22): a factory built over another kept the old one's
    belts wherever the names matched, facing the old ways; on uncharted ground the paste turns
    nothing, so every build kept them, and the diff's settings-changed count said so only as a number. The
    apply now orders them out and lays them fresh as designed."""
    bad = [v for v in rep.verdicts if v.turned]
    if not bad:
        return None
    shown = ", ".join(f"{(v.key or v.name).split(':', 1)[-1]} "
                      f"({'a ghost' if v.verdict.startswith('ghost') else v.detail})"
                      for v in bad[:4])
    names = sorted({v.name for v in bad})
    msg = (f"{len(bad)} {', '.join(names)} stand on their design tiles facing another way or as "
           f"the other underground end, so their lines stop there ({shown}"
           f"{' ...' if len(bad) > 4 else ''}): the apply lays them fresh as designed (ordered "
           "out, robots take each up and build it again). A factory built over another keeps the "
           "old one's belts where the names match, facing the old ways")
    return Check("re-laid", ctx.surface.name, msg)


def _unreached(ctx, r, rep, batch=80):
    """An ``UNREACHED`` check for what the paste would lay where no powered roboport's
    construction area reaches, or None: robots never build it. 2.0.1 (the first lead run's
    plates worker): the coal site's power bridge ran through ground no powered roboport reached,
    and the relay roboport that would have reached it was powered only through that bridge: its
    poles stood as ghosts for ever. The world line says the same after the paste; this says it
    before."""
    from . import link
    lay = [v for v in rep.verdicts if v.verdict.split()[0] in _TO_LAY]
    if not lay:
        return None
    far = []
    try:
        for i in range(0, len(lay), batch):
            part = lay[i:i + batch]
            got = json.loads(r.lua(link.lua_unreached(ctx.surface.name, _force(ctx),
                                                      [(v.x, v.y) for v in part])).strip() or "[]")
            far += [part[int(k) - 1] for k in (got if isinstance(got, list) else [])]
    except (ValueError, OSError, link.LinkError):
        return None
    # a roboport the paste lays where robots reach covers its own area once built (its poles
    # come with it), and so on outward; one that stands without power covers nothing (the
    # deadlock), and the read above already left it out
    reach = []
    grew = True
    while grew and far:
        grew = False
        for v in [v for v in lay if v not in far and ctx.cat.kind(v.name) == "roboport"
                  and (v.x, v.y) not in {(x, y) for x, y, _r in reach}]:
            row = ctx.cat.entity(v.name) if ctx.cat.has_entity(v.name) else {}
            reach.append((v.x, v.y, float(row.get("construction_radius") or 0)))
            grew = True
        far = [v for v in far if not any(abs(v.x - x) <= d and abs(v.y - y) <= d
                                         for x, y, d in reach)]
    if not far:
        return None
    bridge = [v for v in far if (v.key or "").split(":", 1)[-1].startswith("bridge/")]
    shown = ", ".join(f"{v.name} {fkp.fmt(v.x)},{fkp.fmt(v.y)}" for v in far[:4])
    msg = (f"{len(far)} of the {len(lay)} entities the paste lays stand where no powered "
           f"roboport's construction area reaches ({shown}{' ...' if len(far) > 4 else ''}): "
           "robots never build them until a powered roboport reaches them")
    if bridge:
        msg += (f"; {len(bridge)} of them are power-bridge poles, and a roboport powered only "
                "through such a bridge never gets its power")
    return Check("UNREACHED", ctx.surface.name, msg)


def _force(ctx):
    return ctx.surface.force_name


def _placements(ctx):
    """3.1: the registry's records that explain what stands and is not this design's own."""
    return placementsmod.explaining(placementsmod.load(ctx.root, ctx.surface.name), ctx.composed)


def s_apply(ctx, args):
    o = _opts(args, scope="all", confirm=False)
    if ctx.composed is None:
        return ["apply: no composed surface yet (floorplan first)"], []
    if ctx.composed.problems:
        return [f"apply: refused, the composed surface has {len(ctx.composed.problems)} invariant "
                "problems"], _invariant_checks(ctx.surface.name, ctx.composed.problems)
    r = _link(ctx)
    if r is None:
        return [f"apply: skipped, no game link ({ctx.link_error})"], []
    from . import apply as applymod
    area, keys = _scope(ctx, o)
    rep = applymod.apply(r, ctx.composed, ctx.cat, area, free=ctx.surface.free,
                         force=ctx.surface.force_name, policy=ctx.surface,
                         keys=keys, net_items=_net_items(ctx),
                         label=f"fk build {os.path.basename(ctx.script)}",
                         seed=ctx.ground is None, own=ownmod.load(ctx.root, ctx.surface.name),
                         confirm=bool(o["confirm"]), placements=_placements(ctx))
    if rep.own is not None:
        ownmod.save(ctx.root, ctx.surface.name, rep.own)
    if rep.rows:
        # 3.1: one placement per factory in the paste (fkcore.placements), replaced on a rebuild
        modules = {p.factory: p.module for p in ctx.surface.places.values()}
        placementsmod.put_all(ctx.root, ctx.surface.name, placementsmod.from_apply(
            ctx.composed, rep.rows, ctx.cat, modules, ctx.out, rep.tick,
            f"fk build {os.path.basename(ctx.script)}"))
    checks = [Check("mismatch", ctx.surface.name, c.line()) for c in rep.refused]
    if rep.hand:
        # 2.0: what this build replaced or removed that was placed by hand, for the player (the
        # Claude loop hands out/hand.txt's new lines to the lead the way it hands it chat)
        checks += [Check("by-hand", ctx.surface.name, line) for line in rep.hand]
        _hand_file(ctx, rep.hand)
    if rep.misplaced:
        checks.append(Check("not-where-designed", ctx.surface.name, f"{rep.misplaced} ghosts are not where "
                                                           "the design says"))
    if rep.present < rep.design:
        checks.append(Check("no-position", ctx.surface.name, f"{rep.design - rep.present} of "
                                                          f"{rep.design} designed entities are not "
                                                          "there as ghost or entity"))
    return rep.lines(), checks


def _hand_file(ctx, lines):
    """Append a build's hand edits to ``out/hand.txt`` (never rewritten: the loop reads what is
    new since its last look)."""
    import time
    with open(os.path.join(ctx.out, "hand.txt"), "a", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# {time.strftime('%Y-%m-%d %H:%M')} fk build ({os.path.basename(ctx.script)}): "
                 f"{len(lines)} hand edits this build replaced, removed or set back\n")
        fh.writelines(f"  {line}\n" for line in lines)


#: what a script may still say for a check renamed in 2.0.52: the old word keeps its meaning, so an
#: upgraded run's script (or a pinned one) stops on the same things it did before
_OLD_STOPS = {"invariant": "malformed", "unrouted": "no-route", "no_sink": "no-destination",
              "unfed": "no-feed", "underfed": "short-feed", "unconsumed": "no-taker",
              "unlimited": "no-limit", "low_stock": "short-stock", "locked": "needs-research",
              "no_robots": "no-robots", "off_ore": "no-ore", "off_water": "no-water",
              "on_terrain": "on-water-or-cliff"}


def _stop_code(word):
    """A ``stop_on`` word as the check code it names (case folded, old names mapped)."""
    w = str(word).strip().lower()
    return _OLD_STOPS.get(w, w)


def _stopping(ctx, checks):
    """The checks a ``stop_on`` word names. 2.1.3: a dash and an underscore are one, so
    ``stop_on no-power`` holds the paste on cover power's ``NO_POWER`` as ``stop_on NO_POWER``
    does (2.0.52 named the new checks with dashes; the older ones kept their capitals)."""
    want = {s.replace("_", "-") for s in ctx.stop_on}
    return [c for c in checks if c.code.lower().replace("_", "-") in want]


def s_stop_on(ctx, args):
    # the first lead run (2026-10-21): "stop_on: INVARIANT" as a full build's last line read as
    # the failure behind a nonzero exit, which came from the worker's own shell chain
    codes = ", ".join(sorted(ctx.stop_on))
    return [f"stop_on {codes}: a check of these stops the run; none has stopped it so far"], []


STEPS = {
    "read_surface": s_read_surface, "read_terrain": s_read_terrain, "run_models": s_run_models,
    "elaborate": s_elaborate, "size": s_size, "report_rates": s_report_rates, "place": s_place,
    "route": s_route, "hands": s_hands, "tier": s_tier,
    "cover_power": s_cover_power, "cover_robots": s_cover_robots, "floorplan": s_floorplan,
    "report_checks": s_report_checks, "report_bom": s_report_bom, "write_layout": s_write_layout,
    "diff": s_diff, "apply": s_apply, "stop_on": s_stop_on,
}
_SOURCE = ("read_surface", "read_terrain", "run_models", "elaborate", "size", "stop_on")
_LAYOUT = ("place", "route", "hands", "tier", "cover_power", "cover_robots")
#: the steps a `later:` section may not hold: they lay out, write or paste
_NOT_LATER = tuple(s for s in _SOURCE if s != "stop_on") + _LAYOUT + (
    "floorplan", "write_layout", "diff", "apply")


def _write_checkpoint(ctx, step, upto):
    """``out/<step>/``: each factory's layout as the step left it, and (2.0.4) ``source.json``:
    the source it was laid from (the files as the run began, and the script's lines ``upto``
    and including the step), so a later run that starts after it can tell it is stale."""
    folder = os.path.join(ctx.out, step)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, _STAMP_FILE), "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"line": upto[-1].n, "script": _script_sha(upto), "files": ctx.source}, fh,
                  indent=0, sort_keys=True)
        fh.write("\n")
    for factory, lay in ctx.layouts.items():
        with open(os.path.join(folder, f"{factory}.fkp"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(fkp.dumps(lay))
    if step == "floorplan" and ctx.surface_lay is not None:
        with open(os.path.join(folder, f"{ctx.surface.name}.fkp"), "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(fkp.dumps(ctx.surface_lay))


def _stale_checkpoint(ctx, step, upto):
    """Why ``out/<step>/`` was not laid from the source as it is now (the script's lines ``upto``
    and including the step, the source files as this run began): a list of reasons, [] when it
    was. A checkpoint with no ``source.json`` (an older toolkit's) is stale: nothing says what it
    was laid from."""
    try:
        with open(os.path.join(ctx.out, step, _STAMP_FILE), encoding="utf-8") as fh:
            stamp = json.load(fh)
        files, script, line = dict(stamp["files"]), stamp["script"], stamp["line"]
    except (OSError, ValueError, KeyError, TypeError):
        return ["it does not say what source it was laid from"]
    why = _changed(files, ctx.source)
    if line != upto[-1].n or script != _script_sha(upto):
        why.append(f"{os.path.basename(ctx.script)} changed")
    return why


def _load_checkpoint(ctx, step):
    folder = os.path.join(ctx.out, step)
    for p in _factories(ctx):
        path = os.path.join(folder, f"{p.factory}.fkp")
        if not os.path.exists(path):
            raise FlowError(f"no checkpoint {path}: run the steps before it first")
        ctx.layouts[p.factory] = fkp.load(path)


def with_options(lines, extra):
    """``lines`` with ``extra`` (["STEP OPTS", ...]: ``fk build --with``) added to every line of
    each STEP, for this build only; options given later win (``_opts``). 2.0.62 (the opus run:
    "one build with --fresh, then restore the line"): one build's options without editing the
    script. -> the new lines."""
    add = {}
    for spec in extra or ():
        t = shlex.split(spec)
        if not t:
            continue
        cmd, args = t[0], t[1:]
        if cmd == "cover" and args:
            cmd, args = f"cover_{args[0]}", args[1:]
        if cmd not in STEPS:
            raise FlowError(f"--with {spec!r}: unknown step {t[0]!r}")
        add.setdefault(cmd, []).extend(args)
    missing = sorted(set(add) - {ln.cmd for ln in lines})
    if missing:
        raise FlowError(f"--with: the script has no {', '.join(missing)} step")
    return [Line(ln.n, ln.cmd, ln.args + add[ln.cmd], ln.text + "  " + " ".join(add[ln.cmd]),
                 ln.later) if ln.cmd in add else ln for ln in lines]


def run(script, frm=None, to=None, cat=None, echo=print, link=None, extra=None,
        compiled=None) -> tuple:
    """Run ``script`` (a .fkb path). -> (exit code, Ctx). 0 = ran to the end (or to ``to``), 1 =
    stopped by a stop_on check, 2 = malformed script or source. ``link``: an open game link the
    steps use instead of opening their own (``fkcore.lab``, 2.0.3), and the caller closes.
    ``extra``: options for this build only (``with_options``). ``compiled``: the compiled-module
    store the route step uses when its line names none (``fkcore.lab``: the game folder's).

    2.1.19: the whole build reads the source as it began (``fkcore.snapshot``). A file edited
    while it runs is left for the next build, and the build says which ones were."""
    with snapshot.held() as snap:
        try:
            return _run(script, frm, to, cat, echo, link, extra, compiled)
        finally:
            moved = snapshot.changed(snap)
            if moved:
                names = ", ".join(os.path.relpath(p, os.path.dirname(os.path.abspath(script)))
                                  for p in moved)
                echo(f"changed while this build ran: {names}. It was laid from them as the "
                     "build began; build again to lay the new source")


def _run(script, frm, to, cat, echo, link, extra, compiled=None):
    root = os.path.dirname(os.path.abspath(script))
    with open(script, encoding="utf-8") as fh:
        lines = with_options(parse(fh.read(), script), extra)
    ctx = Ctx(root, script, cat or Catalog.load(), os.path.join(root, "out"))
    ctx.compiled = compiled
    ctx.echo = echo
    availmod.use(root)          # what the game can build, as the host last read it (out/avail.json)
    if link is not None:
        ctx.link, ctx.link_lent = link, True
    ctx.steps = [ln.cmd for ln in lines]
    for ln in lines:
        if ln.cmd == "stop_on":
            ctx.stop_on |= {_stop_code(a) for a in ln.args}
    names = [ln.cmd for ln in lines]
    first = _at(names, frm) if frm else 0
    last = _at(names, to) if to else len(lines) - 1
    os.makedirs(ctx.out, exist_ok=True)
    # the source as the run begins, before any step reads it: a file edited while the run goes
    # on makes its checkpoints stale, never the other way round
    ctx.source = _source_files(root)
    for ln in lines[:first]:
        if ln.cmd in _SOURCE:
            STEPS[ln.cmd](ctx, ln.args)
    before = [k for k, ln in enumerate(lines[:first]) if ln.cmd in _LAYOUT]
    notes = []
    if before:
        k = before[-1]
        why = _stale_checkpoint(ctx, lines[k].cmd, lines[:k + 1])
        if not why:
            _load_checkpoint(ctx, lines[k].cmd)
        else:
            # 2.0.4 (the second lead run, 2026-10-22): a recipe changed in red.fk, the build
            # resumed on checkpoints laid from the old source, and the paste laid the old
            # recipe. A stale checkpoint is laid out again from the source, never taken
            notes.append(f"out/{lines[k].cmd} was laid from other source ({'; '.join(why)}): the "
                         f"layout steps before {lines[first].cmd} ran again on the source as it is")
            echo(notes[-1])
            for j, ln in enumerate(lines[:first]):
                if ln.cmd not in _LAYOUT and ln.cmd != "floorplan":
                    continue
                _report, checks = STEPS[ln.cmd](ctx, ln.args)
                ctx.checks += [(ln.cmd, c) for c in checks]
                _write_checkpoint(ctx, ln.cmd, lines[:j + 1])
                stops = _stopping(ctx, checks)
                if stops:
                    echo(f"stopped at line {ln.n} ({ln.cmd}), laid out again: {len(stops)} "
                         f"{stops[0].code} line(s), first: {stops[0]}")
                    _close(ctx)
                    return 1, ctx
    plans = [k for k, ln in enumerate(lines[:first]) if ln.cmd == "floorplan"]
    if plans and ctx.layouts and not notes:
        STEPS["floorplan"](ctx, lines[plans[-1]].args)
    width = len(lines)
    written = set()
    # 2.1.26: each step's time, in out/timings.txt and on its summary line when it is slow (the
    # 20261030-2352 run's dry builds ran 20+ min, and nothing said where the time went)
    timings = open(os.path.join(ctx.out, "timings.txt"), "w", encoding="utf-8", newline="\n")
    try:
        return _steps(ctx, script, lines, first, last, width, written, notes, timings)
    finally:
        timings.close()


#: a step this slow (s) says its time on its summary line
SLOW_STEP = 10.0


def _steps(ctx, script, lines, first, last, width, written, notes, timings):
    echo = ctx.echo
    began = time.monotonic()
    for i in range(first, last + 1):
        ln = lines[i]
        t0 = time.monotonic()
        if ln.later:
            try:
                report, checks = STEPS[ln.cmd](ctx, ln.args)
            except FlowError:
                raise                         # a malformed line is still the script's error
            except Exception as exc:      # noqa: BLE001 - off the critical path: it reports
                report, checks = [f"failed (a later step; the build goes on): "
                                  f"{type(exc).__name__}: {exc}"], []
        else:
            report, checks = STEPS[ln.cmd](ctx, ln.args)
        took = time.monotonic() - t0
        timings.write(f"{ln.cmd:<14} line {ln.n:>3}  {took:8.1f} s  (at {t0 - began:7.1f} s)\n")
        timings.flush()
        report = report + availmod.drain()      # what inserter=any / --hand any / --pole any chose
        if i == first:
            report = report + notes
        ctx.checks += [(ln.cmd, c) for c in checks]
        text = [f"# {ln.text}   (line {ln.n} of {os.path.basename(script)})"] + report
        if checks:
            text += ["", "checks:"] + [f"  {c}" for c in checks]
        # a step the script runs again adds its report to the same file, after the first's
        with open(os.path.join(ctx.out, f"{ln.cmd}.txt"), "a" if ln.cmd in written else "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(("\n" if ln.cmd in written else "") + "\n".join(text) + "\n")
        written.add(ln.cmd)
        if ln.cmd in _LAYOUT or ln.cmd == "floorplan":
            _write_checkpoint(ctx, ln.cmd, lines[:i + 1])
        stops = [] if ln.later else _stopping(ctx, checks)
        head = report[0] if report else ""
        summary = (f"[{i + 1:>2}/{width}] {ln.text:<44} {head[:70]}"
                   + (f"  ({len(checks)} check lines)" if checks else "")
                   + (f"  [{took:.0f} s]" if took >= SLOW_STEP else ""))
        ctx.log.append(summary)
        echo(summary)
        if stops:
            why = (f"stopped at line {ln.n} ({ln.cmd}): {len(stops)} {stops[0].code} "
                   f"line(s), first: {stops[0]}")
            echo(why)
            # 3.0: a held paste over hand edits names every one (the player says yes to a list)
            hand = [c for c in stops if c.code == "by-hand"]
            for c in hand[:30]:
                echo(f"  by-hand: {c.msg}")
            if hand:
                echo(("  ... and more in out/diff.txt; " if len(hand) > 30 else "  ")
                     + "keep one: put it in the design; paste over them: fk build --yes")
            # the steps this run would have reached say so, instead of keeping an earlier run's
            # report (the first lead run's mall2 designer read stale report_checks and route
            # lines after a build stopped at elaborate, 2026-10-21)
            for later in lines[i + 1:last + 1]:
                if later.cmd in written:
                    continue
                with open(os.path.join(ctx.out, f"{later.cmd}.txt"), "w", encoding="utf-8",
                          newline="\n") as fh:
                    fh.write(f"# {later.text}   (line {later.n} of {os.path.basename(script)})\n"
                             f"not run: the build {why}\n")
                written.add(later.cmd)
            _close(ctx)
            return 1, ctx
    _close(ctx)
    return 0, ctx


def _at(names, spec):
    """The index of the step ``spec`` names: ``route``, or ``report_rates:2`` for the second
    report_rates of the script."""
    name, _, k = spec.partition(":")
    where = [i for i, n in enumerate(names) if n == name]
    try:
        k = int(k or 1)
        if k < 1:
            raise ValueError(k)
        return where[k - 1]
    except (ValueError, IndexError):
        raise FlowError(f"--from/--to must name a step of the script, NAME or NAME:K for its K-th "
                        f"time ({spec!r}): {', '.join(names)}") from None


def _close(ctx):
    if ctx.link is not None and not ctx.link_lent:
        ctx.link.close()
        ctx.link = None
