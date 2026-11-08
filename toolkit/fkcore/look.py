"""fkcore.look: the live look (CORE-V1 §4.6, §6.4, §6.5), an area of the composed layout vs the game.

``python -m fkcore look GAME --source world --area X0,Y0,X1,Y1`` composes the game codebase's
layout (``composed``), snapshots the area through the link (``link.snapshot``), judges it with
``sense.sense`` under the surface file's watch and notify settings, and prints the verdict
summary, every verdict that is not ok, the "changes here" list, and the live render next to the
design render (``compose.render_scene`` of ``scene.from_capture`` and of ``scene.design_scene``;
after a finished apply both render the same text: look parity). A built underground belt with no
partner (or with an end of the same kind, so no tunnel forms) also gets a warning line,
``UNPAIRED``, with its position: a report line, never a gate. ``--write-marks`` writes the new
marks into the surface ``.fkp``, as the offline ``sense`` command does, so the next look says
``marked`` for them.

Notify (the surface file's ``notify``, or ``--notify``):

- ``nearby`` (play default): the lines are only this look's report;
- ``all`` (the default on a free surface): they are also pushed, one game chat line per change, and appended to
  ``<game>/out/inbox.txt`` (``fkcore.notify``);
- ``off``: report only, nothing pushed.

Vision (2.1, ``fkcore.vision``): ``view`` (``vision.View``) zooms out (N tiles a cell), adds
layers (robots, power, defense, pollution, pipes, recipes, tags; the design render gets those it
knows) and writes a PNG instead of the text. At zoom 1 with no layer and no image the look is
2.0.78's, byte for byte. A map look (zoom 8 and up, or a big area) reads counts per cell
(``link.vision_read``), not every entity, and gives no verdicts; on a surface that is not free a
zoomed-out look reads only charted chunks, what the game's map view shows.

Fair play: a look reads only its area. On a surface that is not free the area must be given; on
a free surface (the lab) a look without one covers everything designed (a global diff). ``--watch`` overrides the watch policy for
one look (``strict``, or ``plots=keep,sanctioned=strict``). ``report`` is pure (the tests feed it
fixture captures); ``run`` uses the game link, which is on only with ``FK_LINK=on``.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from . import compose, fkp, link, scene, sense, vision
from . import notify as notifymod
from . import own as ownmod
from . import surface as surfacemod

__all__ = ["LookError", "Look", "MapLook", "composed", "net_items", "policy", "default_area", "report",
           "side_by_side", "unpaired", "run"]

_LAYOUT_VERDICTS = ("ok", "ghost", "settings-changed", "other-entity", "not-placed")
_ACTIONS = ("keep", "revert", "strict", "mark")
#: Two renders side by side up to this many columns, else one above the other.
MAX_WIDTH = 160


class LookError(ValueError):
    pass


@dataclass
class Look:
    surface: str
    area: tuple
    policy: dict
    rep: sense.SenseReport
    design: str                      # the design render of the area
    live: str                        # the live render of the area
    pushed: list = field(default_factory=list)
    wrote: int = 0                   # marks written into the surface .fkp
    notes: list = field(default_factory=list)
    unpaired: list = field(default_factory=list)   # built underground ends with no partner
    view_lines: list | None = None   # the vision renders (zoom, layers, image) in place of the plain one
    image: str | None = None         # the PNG written, with --image

    @property
    def designed(self):
        """Layout entities in the area (each has one layout verdict; ghost SHORT is a ghost)."""
        return sum(1 for v in self.rep.verdicts if v.verdict.split()[0] in _LAYOUT_VERDICTS)

    @property
    def ok(self):
        return sum(1 for v in self.rep.verdicts if v.verdict == "ok")

    def lines(self):
        p = self.policy
        a = ",".join(str(int(v)) for v in self.area)
        out = [f"look {self.surface} --source world, area {a}, tick {self.rep.tick}: ok "
               f"{self.ok}/{self.designed} designed entities (notify {p['notify']}; watch plots "
               f"{p['plots']}, sanctioned {p['sanctioned']})"]
        out += self.rep.lines()
        out += [f"  UNPAIRED {u['name']} {scene.num(u['x'])},{scene.num(u['y'])} "
                f"({u['ug']}; {u['why']})" for u in self.unpaired]
        stale = sum(1 for v in self.rep.verdicts if v.wrong_items)
        relay = sum(1 for v in self.rep.verdicts if v.wrong_items and v.renet)
        if stale > relay:
            out.append(f"{stale - relay} belts or pipes here carry items their net doesn't "
                       f"(other-item): fk fresh {a} takes them up, and the next fk build lays "
                       "them fresh")
        if relay:
            out.append(f"{relay} belts or pipes here hold what the net they were laid under before "
                       "carried (other-item): the next fk build takes them up and lays them fresh "
                       "by itself")
        if self.view_lines is not None:
            out += self.view_lines
        else:
            out += side_by_side(self.design, self.live, self.area)[0]
        if p["notify"] == "all":
            out.append(f"notify all: pushed {len(self.pushed)} line(s) to game chat and "
                       "out/inbox.txt")
        else:
            out.append(f"notify {p['notify']}: nothing pushed (the lines are this look's "
                       "report only)")
        if self.wrote:
            out.append(f"wrote {self.wrote} mark(s) into the surface .fkp")
        out += [f"note: {n}" for n in self.notes]
        return out


def composed(game, cat):
    """(surface file, surface .fkp, its path, the composed Surface) of a game codebase."""
    sf = surfacemod.load_game(game)
    lay_path = os.path.join(game, f"{sf.name}.fkp")
    surface_lay = fkp.load(lay_path) if os.path.exists(lay_path) else surfacemod.floorplan(sf)
    factories = {}
    for p in sf.places.values():
        path = os.path.join(game, f"{p.module}.fkp")
        if os.path.exists(path):
            lay = fkp.load(path)
            lay.name = p.factory
            factories[p.factory] = lay
    return sf, surface_lay, lay_path, compose.compose(surface_lay, factories, cat)


def net_items(game, sf, cat):
    """({<factory>/<net>: items}, notes) from each placed factory's source, for other-item."""
    from .elaborate import ElabError, elaborate_file
    from .lang import FkSyntaxError
    items, notes = {}, []
    for p in sf.places.values():
        src = os.path.join(game, f"{p.module}.fk")
        if p.lifecycle == "retired" or not os.path.exists(src):
            continue
        lay_path = os.path.join(game, f"{p.module}.fkp")
        try:
            lay = fkp.load(lay_path) if os.path.exists(lay_path) else None
            flat = elaborate_file(src, cat, name=p.module, layout=lay)
        except (FkSyntaxError, ElabError, OSError, fkp.FkpError) as exc:
            notes.append(f"{p.factory}: no netlist ({exc}); other-item is not checked there")
            continue
        for n, net in flat.nets.items():
            items[f"{p.factory}/{n}"] = list(net.items)
    return items, notes


def policy(sf, notify=None, watch=None):
    """The sense policy of one look: the surface file's, with ``notify`` / ``watch`` overrides."""
    w = dict(sf.watch)
    for part in (watch or "").split(","):
        if not part.strip():
            continue
        region, _, action = part.rpartition("=")
        action = action.strip()
        if not action or action.split()[0] not in _ACTIONS:
            raise LookError(f"a watch action is keep, revert, strict or mark [<duration>], "
                            f"got {action!r}")
        regions = [region.strip()] if region.strip() else ["plots", "sanctioned"]
        for r in regions:
            if r not in ("plots", "sanctioned"):
                raise LookError(f"a watch region is plots or sanctioned, got {r!r}")
            w[r] = action
    mode = notify or sf.notify_mode
    if mode not in ("nearby", "all", "off"):
        raise LookError(f"notify is nearby, all or off, got {mode!r}")
    return {"plots": w["plots"], "sanctioned": w["sanctioned"], "notify": mode}


def default_area(s, cat):
    """The tile box of everything designed on the surface, plus a 2-tile margin."""
    from .apply import scope_area
    return scope_area(s.rows(), cat)


def _ug_step(direction):
    """The unit step of a cardinal direction, or None (scene.DIRS; 0 N, 4 E, 8 S, 12 W)."""
    d = scene.DIRS.get(int(direction))
    return d[1] if d else None


def _ug_walk(t, index):
    """The first underground end ahead of ``t`` on its line (behind it for an output), same name
    and direction, within the tier's reach; the game's rule (CORE-V1 flow, and Factorio 2.0:
    both ends of a pair face the flow, so an input looks ahead and an output behind)."""
    step = _ug_step(t["direction"])
    if step is None:
        return None
    dx, dy = step
    if t["ug"] == "output":
        dx, dy = -dx, -dy
    x, y = float(t["x"]), float(t["y"])
    for k in range(1, int(scene.UG_MAX.get(t["name"], 5)) + 1):
        for other in index.get((round(x + k * dx, 2), round(y + k * dy, 2)), ()):
            if other is not t and other["name"] == t["name"] \
                    and int(other["direction"]) == int(t["direction"]):
                return other
    return None


def unpaired(live):
    """The built underground belts of a live scene with no partner: one dict each, with name,
    position, its end kind and why. The game's ``pair`` is trusted when the capture has it; on a
    capture without it the walk pairs an input ahead with an output (and an output behind with an
    input). A ghost is never judged: a ghost pair is not built, and a ghost is no partner. This is
    a warning in the look's report, never a gate."""
    index = {}
    for t in live["things"]:
        if t["type"] == "underground-belt" and not t["ghost"] \
                and t.get("ug") in ("input", "output"):
            key = (round(float(t["x"]), 2), round(float(t["y"]), 2))
            index.setdefault(key, []).append(t)
    out = []
    for t in live["things"]:
        if t["type"] != "underground-belt" or t["ghost"] \
                or t.get("ug") not in ("input", "output") or t.get("pair") is not None:
            continue
        other = _ug_walk(t, index)
        if other is None:
            why = f"no partner within {int(scene.UG_MAX.get(t['name'], 5))} tiles"
        elif other["ug"] == t["ug"]:
            why = (f"partner at {scene.num(other['x'])},{scene.num(other['y'])} is another "
                   f"{other['ug']}, so it points the same way")
        else:
            continue                      # a capture without `pair`: the walk found the partner
        out.append({"name": t["name"], "x": t["x"], "y": t["y"], "ug": t["ug"], "why": why})
    return out


def report(s, live, cat, area, pol, net_items=None, own=None, view=None, facts=None,
           tags=None, placements=None) -> Look:
    """The look at ``area`` of the composed ``s`` against the ``live`` scene. Pure. ``own``: the
    toolkit's own entities (``fkcore.own``): those the design dropped read unowned. ``view``
    (``vision.View``): zoom, layers and image; ``facts``: the game's vision read
    (``vision.facts_from_read``); ``tags``: ``vision.design_tags``; ``placements`` (3.1): the
    registry's records that are not this design's (``placements.explaining``)."""
    area = tuple(int(v) for v in area)
    rep = sense.sense(s, live, cat, list(area), pol, net_items=net_items, own=own,
                      placements=placements)
    dsc = scene.design_scene(s, cat, list(area))
    design = compose.render_scene(dsc, cat, area)
    lk = Look(s.name, area, dict(pol), rep, design, compose.render_scene(live, cat, area),
              unpaired=unpaired(live))
    if view is not None and not view.plain:
        plots, _insts = tags or (vision.plots_of(s), [])
        frame = vision.Frame(area, view.zoom)
        base = side_by_side(design, lk.live, area)[0] if view.zoom == 1 else None
        lk.view_lines, lk.image = vision.compose_view(
            view, frame, cat, dsc, live, facts, plots, tags or (plots, []),
            title=f"look {s.name}", plain_base=base)
    return lk


@dataclass
class MapLook:
    """A map look (``vision.map_mode``): counts per cell, no verdicts."""
    surface: str
    area: tuple
    zoom: int
    tick: int | None
    view_lines: list
    image: str | None = None
    calls: list = field(default_factory=list)       # seconds of each game call
    notes: list = field(default_factory=list)

    def lines(self):
        a = ",".join(str(int(v)) for v in self.area)
        out = [f"look {self.surface} --source world, area {a}, tick {self.tick}: map view at "
               f"zoom {self.zoom}, counts per cell (verdicts come from a look at zoom 1 to 4 "
               f"of {vision.MAP_TILES} tiles or fewer)"]
        out += self.view_lines
        if self.calls:
            out.append(f"read in {len(self.calls)} game call(s), {sum(self.calls):.2f} s "
                       f"(longest {max(self.calls):.2f} s)")
        out += [f"note: {n}" for n in self.notes]
        return out


def image_path(game, area, zoom):
    """Where ``--image`` writes when no path is given: ``<game>/out/look-X0_Y0_X1_Y1-zN.png``."""
    a = "_".join(str(int(v)) for v in area)
    return os.path.join(game, "out", f"look-{a}-z{zoom}.png")


def _vision_facts(r, sf, s, area, view, plots, cells, timings):
    play = not sf.free
    want = [n for n in view.layers if n != "tags"]
    if not want and not cells and not (play and view.zoom > 1):
        return None
    force = sf.force_name
    cfg = vision.read_config(sf.name, force, vision.Frame(area, view.zoom), want, play,
                             plots if cells else (), cells, link.SKIP_TYPES)
    return link.vision_read(r, cfg, timings=timings)


def _rows(text, w, h):
    rows = text.split("\n") if text else []
    rows += [""] * (h - len(rows))
    return [r.ljust(w, ".") for r in rows[:h]]


def side_by_side(design, live, area, max_width=MAX_WIDTH):
    """-> (lines, tiles that differ): the two renders next to each other, each row labelled with
    its y; one above the other when two would not fit in ``max_width`` columns."""
    x0, y0, x1, y1 = (int(v) for v in area)
    w, h = x1 - x0, y1 - y0
    d, l = _rows(design, w, h), _rows(live, w, h)
    differ = sum(1 for dr, lr in zip(d, l) for a, b in zip(dr, lr) if a != b)
    out = [f"render: design | live, x {x0}..{x1 - 1}, y {y0}..{y1 - 1} (. a free tile): "
           f"{differ} tiles differ"]
    if 2 * w + 9 <= max_width:
        out += [f"{y0 + i:>5} {dr} | {lr}" for i, (dr, lr) in enumerate(zip(d, l))]
    else:
        out += ["design:"] + [f"{y0 + i:>5} {r}" for i, r in enumerate(d)]
        out += ["live:"] + [f"{y0 + i:>5} {r}" for i, r in enumerate(l)]
    return out, differ


def run(r, game, cat, area=None, notify=None, watch=None, write_marks=False, tag="look",
        view=None, surface=None, force=None):
    """Look at ``area`` of the game codebase ``game`` in the game: snapshot, sense, render; write
    the marks (``write_marks``) and push (notify all). ``view`` (``vision.View``): zoom, layers,
    image; a map look returns a :class:`MapLook`. ``surface`` (3.1.1): another surface than the
    game's own (a placement's, the lab's): read there, against an empty design, with ``force``."""
    sf, surface_lay, lay_path, s = composed(game, cat)
    if surface and surface != sf.name:
        import dataclasses
        sf = dataclasses.replace(sf, name=surface, force=force or sf.force)
        s = compose.Surface(name=surface)
    if area is None:
        if not sf.free:
            raise LookError("fair play: a look needs --area X0,Y0,X1,Y1 on a surface that is not "
                            "free (the agent looks at an area)")
        area = default_area(s, cat)
        if area is None:
            raise LookError("nothing is designed on this surface")
    if view is not None and view.image == "":
        view = vision.View(view.zoom, view.layers, image_path(game, area, view.zoom))
    if view is not None and not view.plain:
        tags = vision.design_tags(game, sf, s)
        area = tuple(int(v) for v in area)
        timings = []
        if vision.map_mode(area, view.zoom):
            raw = _vision_facts(r, sf, s, area, view, tags[0], True, timings)
            facts = vision.facts_from_read(raw)
            dsc = scene.design_scene(s, cat, list(area))
            lines, image = vision.compose_view(view, vision.Frame(area, view.zoom), cat, dsc,
                                               None, facts, tags[0], tags,
                                               title=f"look {sf.name}")
            return MapLook(sf.name, area, view.zoom, facts.tick, lines, image, timings,
                           [f"composed surface: {p}" for p in s.problems])
    items, notes = net_items(game, sf, cat)
    pol = policy(sf, notify, watch)
    live = scene.from_capture(link.snapshot(r, sf.name, area, tag, keep=False))
    facts = tags = None
    if view is not None and not view.plain:
        raw = _vision_facts(r, sf, s, area, view, (), False, timings)
        tags = vision.design_tags(game, sf, s)
        facts = vision.facts_from_read(raw, live, tags[0]) if raw is not None else None
        if facts is None and view.layers:
            facts = vision.facts_from_read({}, live, tags[0])
    from . import placements as placementsmod
    recs = placementsmod.explaining(placementsmod.load(game, sf.name), s)
    lk = report(s, live, cat, area, pol, items, ownmod.load(game, sf.name), view, facts, tags,
                placements=recs)
    ghosts = sorted({v.name for v in lk.rep.verdicts if v.verdict == "ghost"})
    if ghosts:
        # a ghost that stays is a blocked build: say why (SHORT: the network holds none of the
        # item; LOCKED: its recipe is not researched), from the free supply read
        from . import supply
        try:
            sup = supply.read(r, sf.force_name, sf.name, ghosts)
            supply.mark_ghosts(lk.rep.verdicts, sup)
        except (OSError, link.LinkError) as exc:
            lk.notes.append(f"ghosts not judged against the network ({type(exc).__name__}: {exc})")
    lk.notes += notes + [f"composed surface: {p}" for p in s.problems]
    # 2.0.4: the verdicts judge against the .fkp files; say when the source moved on from them
    from .flow import layout_source_changes
    lk.notes += layout_source_changes(game)
    if write_marks and lk.rep.marks:
        for m in lk.rep.marks:
            surface_lay.marks[m.name] = m
        fkp.save(surface_lay, lay_path, cat)
        lk.wrote = len(lk.rep.marks)
    if lk.rep.push:
        lk.pushed = notifymod.push(r, game, sf.name, lk.rep.push, lk.rep.tick, "look")
    return lk
