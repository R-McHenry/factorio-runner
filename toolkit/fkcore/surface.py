"""fkcore.surface: the surface file (`<surface>.surface`), CORE-V1.md §6.2 and §11.

    surface  <name>
    mode     play | lab
    force    <name>                           # whose the paste is (default: player in play, lab in lab)
    terrain  <name> | none                    # the bare world the layout is made against
    catalog  <name>
    design   <file>                           # the root .fk: every plot is a sealed instance there
    place <factory> = <module> plot <x>,<y> <W>x<H> [rot <r>] [patch <resource>[#<n>]]
          [gap <tiles>] [temporary until <condition or tick>] [retired]
    robots <name>: cover <factory>, ...
    claim <name> box <x0>,<y0> <x1>,<y1> [until <tick>] ["reason"]
    watch plots|sanctioned: keep | revert | strict | mark [<duration>]
    notify   nearby | all | off
    remind   <duration>                       # 2h, 10min, 20s: the alert reminder cadence
    target   <item> <rate>/<unit>, ...

A ``#`` starts a comment at the start of a line or after a space. ``<module>`` is a factory file of the game codebase (``<module>.fk`` beside the surface file); its
layout record is ``<module>.fkp``. :func:`floorplan` turns the place and claim lines into the
surface's own .fkp (plots and claims); composing it with the factories' layouts is
``fkcore.compose``. The defaults follow CORE-V1.md §6.4/6.5: ``play`` mode, watch ``plots: keep``
and ``sanctioned: mark``, notify ``nearby`` in play and ``all`` in lab.

**``design`` (2.0.43, PLAN-GLOBAL phase B).** With a ``design`` line the plot list no longer comes
from the ``place`` lines: it is derived from the root ``.fk``'s top-level sealed instances (name,
file, x, y, rot, w, h, ``@patch``), by ``fkcore.root``. ``design`` wins when both are present, and
``place`` lines stay readable, so an old game codebase builds exactly as before. ``fk
migrate-surface`` writes the root design from the place lines and comments them out.

``remind`` is how often a persisting alert of the game's alert panel is pushed again
(``fkcore.alerts``; CORE-V1 §6.5, decision 11). Its default, **10min, is a placeholder: the
default is still open with Rory** (CORE-V1 §13). Pure, stdlib only.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass, field

from . import fkp as fkpmod
from . import snapshot
from .sense import duration_ticks

__all__ = ["SurfaceError", "Place", "SurfaceFile", "loads", "load", "load_game", "floorplan",
           "derive_places", "migrate_text", "DESIGN_NOTE"]

_UNITS = {"s": 1.0, "min": 60.0, "h": 3600.0}
#: The alert reminder cadence when the surface file has no ``remind`` line. Still open with Rory.
DEFAULT_REMIND = "10min"


class SurfaceError(ValueError):
    pass


@dataclass
class Place:
    factory: str
    module: str
    x: int
    y: int
    w: int
    h: int
    rot: int = 0
    patch: str | None = None
    lifecycle: str = "placed"          # placed | temporary until ... | retired
    line: int = 0
    gap: int = 0                       # tiles kept free around the plot for the root's routes


@dataclass
class SurfaceFile:
    path: str
    name: str = ""
    mode: str = "play"
    terrain: str = "none"
    catalog: str = "base"
    places: dict = field(default_factory=dict)        # factory -> Place
    robots: dict = field(default_factory=dict)        # name -> [factory]
    claims: dict = field(default_factory=dict)        # name -> fkp.Claim
    watch: dict = field(default_factory=lambda: {"plots": "keep", "sanctioned": "mark"})
    notify: str | None = None
    force: str | None = None                          # the paste's force; None = by mode
    targets: dict = field(default_factory=dict)       # item -> items/s
    remind: str = DEFAULT_REMIND                      # alert reminder cadence (open with Rory)
    design: str | None = None                         # the root .fk (2.0.43): the plot list
    design_notes: list = field(default_factory=list)  # what the derivation could not make a plot
    lines_places: dict = field(default_factory=dict)  # the place lines, when a design won

    @property
    def notify_mode(self):
        return self.notify or ("all" if self.mode == "lab" else "nearby")

    @property
    def remind_ticks(self):
        """The reminder cadence in game ticks (60 per second)."""
        return duration_ticks(self.remind)


def _pair(s, where):
    try:
        x, y = s.split(",")
        return int(x), int(y)
    except ValueError:
        raise SurfaceError(f"{where}: expected x,y, got {s!r}") from None


def _strip(raw):
    """The line without its comment: a # at the start or after a space, outside quotes (so a
    patch name like iron-ore#2 survives)."""
    quoted = False
    for i, ch in enumerate(raw):
        if ch == '"':
            quoted = not quoted
        elif ch == "#" and not quoted and (i == 0 or raw[i - 1].isspace()):
            return raw[:i].strip()
    return raw.strip()


def loads(text, path="<surface>") -> SurfaceFile:
    sf = SurfaceFile(path)
    for n, raw in enumerate(text.splitlines(), 1):
        line = _strip(raw)
        if not line:
            continue
        where = f"{path}:{n}"
        t = shlex.split(line)
        head = t[0]
        try:
            if head in ("surface", "mode", "terrain", "catalog", "notify", "force", "design") \
                    and len(t) == 2:
                if head == "mode" and t[1] not in ("play", "lab"):
                    raise SurfaceError(f"{where}: mode is play or lab")
                if head == "notify" and t[1] not in ("nearby", "all", "off"):
                    raise SurfaceError(f"{where}: notify is nearby, all or off")
                setattr(sf, "name" if head == "surface" else head, t[1])
            elif head == "place":
                if t[2] != "=" or t[4] != "plot":
                    raise SurfaceError(f"{where}: place <factory> = <module> plot x,y WxH ...")
                x, y = _pair(t[5], where)
                m = re.fullmatch(r"(\d+)x(\d+)", t[6])
                if not m:
                    raise SurfaceError(f"{where}: expected WxH, got {t[6]!r}")
                p = Place(t[1], t[3], x, y, int(m.group(1)), int(m.group(2)), line=n)
                rest = t[7:]
                while rest:
                    if rest[0] == "rot":
                        p.rot, rest = int(rest[1]) % 4, rest[2:]
                    elif rest[0] == "patch":
                        p.patch, rest = rest[1], rest[2:]
                    elif rest[0] == "gap":
                        p.gap, rest = int(rest[1]), rest[2:]
                        if p.gap < 0:
                            raise SurfaceError(f"{where}: gap is 0 tiles or more")
                    elif rest[0] == "temporary":
                        p.lifecycle, rest = " ".join(rest), []
                    elif rest[0] == "retired":
                        p.lifecycle, rest = "retired", rest[1:]
                    else:
                        raise SurfaceError(f"{where}: unknown place option {rest[0]!r}")
                if p.factory in sf.places:
                    raise SurfaceError(f"{where}: {p.factory} is placed twice")
                sf.places[p.factory] = p
            elif head == "robots":
                name = t[1].rstrip(":")
                if t[2] != "cover":
                    raise SurfaceError(f"{where}: robots <name>: cover <factory>, ...")
                sf.robots[name] = [w.strip(",") for w in t[3:] if w.strip(",")]
            elif head == "claim":
                lay = fkpmod.loads("fkp 1 surface s\n" + line, where)
                (c,) = lay.claims.values()
                sf.claims[c.name] = c
            elif head == "watch":
                region = t[1].rstrip(":")
                if region not in ("plots", "sanctioned") or not t[2:]:
                    raise SurfaceError(f"{where}: watch plots|sanctioned: <action>")
                if t[2] not in ("keep", "revert", "strict", "mark"):
                    raise SurfaceError(f"{where}: a watch action is keep, revert, strict or mark")
                sf.watch[region] = " ".join(t[2:])
            elif head == "remind":
                if len(t) != 2 or not duration_ticks(t[1]):
                    raise SurfaceError(f"{where}: remind <duration>, like 10min or 20s")
                sf.remind = t[1]
            elif head == "target":
                for part in " ".join(t[1:]).split(","):
                    item, rate = part.split()
                    num, unit = rate.split("/")
                    sf.targets[item] = float(num) / _UNITS[unit]
            else:
                raise SurfaceError(f"{where}: unknown line {head!r}")
        except (IndexError, KeyError, ValueError) as exc:
            if isinstance(exc, SurfaceError):
                raise
            raise SurfaceError(f"{where}: malformed {head} line ({exc})") from None
    if not sf.name:
        raise SurfaceError(f"{path}: no surface line")
    return sf


def load(path, derive=True) -> SurfaceFile:
    """A surface file, with its plot list derived from its ``design`` (2.0.43)."""
    sf = loads(snapshot.read(path), path)
    return derive_places(sf, os.path.dirname(os.path.abspath(path))) if derive else sf


def load_game(game_dir, derive=True) -> SurfaceFile:
    """The surface file of a game codebase: its play-mode ``*.surface``, else its first (one
    surface per codebase; 2.0.3: a lab.surface copied in beside nauvis.surface came first
    alphabetically and every command read the lab's). With ``derive``, its ``design`` names the
    plots (2.0.43)."""
    names = sorted(f for f in os.listdir(game_dir) if f.endswith(".surface"))
    if not names:
        raise SurfaceError(f"no .surface file in {game_dir}")
    files = []
    for n in names:
        path = os.path.join(game_dir, n)
        files.append(loads(snapshot.read(path), path))
    sf = next((sf for sf in files if sf.mode == "play"), files[0])
    return derive_places(sf, game_dir) if derive else sf


def derive_places(sf: SurfaceFile, folder) -> SurfaceFile:
    """``sf`` with the plot list its ``design`` names (2.0.43, PLAN-GLOBAL phase B): the root
    .fk's top-level sealed instances replace the ``place`` lines, which stay readable (what the
    design does not name is not a plot). ``design_notes`` says what could not be a plot."""
    if not sf.design:
        return sf
    from . import root                          # local: fkcore.root reads Place from here
    path = os.path.join(folder, sf.design)
    if not os.path.exists(path):
        raise SurfaceError(f"design {sf.design}: no such file in {folder}")
    sf.lines_places = dict(sf.places)
    sf.places, notes = root.places(path)
    sf.design_notes = list(notes)
    return sf


def floorplan(sf: SurfaceFile, infra=None) -> fkpmod.Layout:
    """The surface's own .fkp: a plot per placed factory (retired ones free their plot), its
    claims, and ``infra`` (a surface Layout of shared infrastructure, kept as it is). A plot's
    ``gap`` (2.0.43: tiles kept free around it for the root's routes) rides on its plot line."""
    lay = fkpmod.Layout("surface", sf.name)
    for name, p in sf.places.items():
        if p.lifecycle == "retired":
            continue
        params = {"module": p.module}
        if p.patch:
            params["patch"] = p.patch
        if p.gap:
            params["gap"] = str(p.gap)
        lay.plots[name] = fkpmod.Plot(name, p.x, p.y, p.rot, p.w, p.h, params)
    lay.claims.update(sf.claims)
    if infra is not None:
        lay.covers.update(infra.covers)
        lay.marks.update(infra.marks)
        for e in infra.entities.values():
            lay.add(e)
    return lay


#: The line a migration adds to a surface file (2.0.43): the root design names the plots.
DESIGN_NOTE = "# (2.0.43) the plots are the sealed instances of {design}: these place lines are "\
              "their record"


def migrate_text(text, design):
    """``text`` (a surface file) with ``design`` added and every ``place`` line commented out
    (2.0.43, PLAN-GLOBAL phase B: ``fk migrate-surface``): the root .fk is now what places the
    plots, and the old place lines stay as the record they were migrated from. ``design`` is the
    root file's name, relative to the surface file."""
    out, done = [], False
    for raw in text.splitlines():
        head = raw.split("#", 1)[0].strip()
        word = head.split(" ", 1)[0] if head else ""
        if not done and word not in ("", "surface", "mode", "force", "terrain", "catalog"):
            out.append(f"design   {design}")
            out.append(DESIGN_NOTE.format(design=design))
            done = True
        out.append("# " + raw if word == "place" else raw)
    if not done:
        out.append(f"design   {design}")
    return "\n".join(out) + "\n"
