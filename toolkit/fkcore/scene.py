"""fkcore.scene: the shared tile model of `fk look`, and the V1 adapters into it.

The model (``thing``, ``scene``, ``box_tiles``, ``footprint``...) is copied from
C:\\fk-lead\\repo\\fk\\sight\\model.py (d6018d3), the vision base kept as it is, with one field
added: ``last_user`` (the player who last built or changed it, from the game; attribution for
foreign changes, CORE-V1 §6.4). The V1 adapters at the end turn a capture dump into a scene
(``from_capture``, the row mapping of fk/sight/live.py) and a composed surface into a scene
(``design_scene``: the design adapter re-pointed from fkc build dirs at fkcore.compose.Surface;
``src`` is the V1 key, ``design`` the factory whose plot holds it). v4's text follows.

Every source (the live game, a compiled design) is turned into a SCENE by an adapter, and every
later step (owner, flow, grid render, where) reads and annotates the same scene. Plain dicts, so
scenes are JSON-able and fixtures are easy to write by hand.

Scene::

    {"source": "live" | "design:<build dir>",
     "tick":   int | None,
     "area":   [x0, y0, x1, y1] | None,      # world coordinates the source covered
     "things": [Thing, ...],
     "nests":  [{"name", "x", "y"}, ...]}    # enemy spawners/worms, possibly outside the area

Thing (made with :func:`thing`; every key is always present)::

    name    entity name (a ghost carries the ghosted name; item entities are "item-on-ground")
    type    prototype type (a ghost carries the ghosted type)
    x, y    centre (float);  direction  0..15 (0 N, 4 E, 8 S, 12 W)
    tiles   sorted list of [i, j] integer tiles the thing occupies, never empty
    force   "player" | "enemy" | "neutral"
    ghost, decon   bool (decon = marked for deconstruction)
    hp      health ratio 0..1, or None (no health / unknown)
    status  defines.entity_status name ("working", "no_power", ...), or None
    recipe, filter   str | None
    items   {item: count}   (belt lanes, chest inventory, inserter hand, item on ground)
    lanes   per-lane belt contents (live only), in transport line order: lanes[0] is line 1 = the
            LEFT lane looking along the flow, lanes[1] line 2 = the right lane; an empty lane is {},
            and None means "not captured" (designs, older dumps)
    ug      "input" | "output" | None (underground belts; None also = unknown, as in designs)
    pair    [x, y] of the game's underground partner, or None (live only; designs never have it)
    net     electric network id, or None
    unit    unit_number, or None
    ammo    rounds of ammo in a built ammo turret (0 = EMPTY), or None (not a turret / unknown)
    slots   usable chest slots (the bar; an unlimited steel/logistic chest is 48), or None
    used    how many of those slots hold something, or None (not captured / not a chest)
    storage_filter   the item a storage chest's filter keeps it to, or None
    fluid   per fluidbox machine, in box order: [{"fluid": name|None (empty), "amount": int,
            "pipes": pipe connections, "open": connections with nothing attached}], or None
    fluids_in, fluids_out   the fluid names the machine's recipe takes / makes, or None
    have    a ghost's item stock in the logistics networks (0 = SHORT), or None
    uncovered   True when a ghost sits outside every roboport's construction range, or None
    lgnet   a roboport's or a logistic chest's logistic network id, or None
    offnet  True when a logistic chest (built or ghost) is outside every roboport's logistic range,
            False when it is inside one, None for anything else / not captured
    docked  [logistic robots, construction robots] docked in a roboport, or None
    design, src   design name and design entity key ("block/array/name0"), or None

Annotations added later (absent until then)::

    owner     "<design>/<src without its last segment>" | "keep/<name>" | "temp/<id>"
              | "unowned" | "enemy" | "nature" | "-"
    zone      "<design>/<block>" (designated area containing the centre tile) | "sanctioned"
    trespass  bool
    paired    bool (underground belts only; geometric or game truth), pair_at [x, y] | None
    allowed   the items the design's net(s) carry on this belt (fk.sight.owner), or None
    lane_plan [lane 1 items|None, lane 2 items|None] the design fixes (fk.sight.owner), or None
    ug_expected, dir_expected   the underground kind / the direction the design's manifest row
              asks for (fk.sight.owner), or None
    net_split   a roboport whose network is not the main one (fk.sight.robots), or None
    drop_lane   "left"/"right": the lane an inserter's drop fills on the straight belt under it
              (fk.lanes; the far-lane rule), or None when the drop is not on a straight belt
    drop_belt   [i, j] tile of that belt, or None
"""
from __future__ import annotations

import math

__all__ = ["MODES", "MARKERS", "MODE_MARKERS", "DIRS", "UG_MAX", "thing", "scene",
           "box_tiles", "footprint", "centre_tile", "num", "from_capture", "design_scene"]

#: Modes in canonical order; `full` means all of them in this order.
MODES = ("entity", "state", "owner", "zone", "threat", "items", "flow", "power", "build", "fluid",
         "robots")

#: Loud marker words, in the fixed order they are printed.
MARKERS = ("ATTACK", "TRESPASS", "unowned", "EMPTY", "other-item", "WRONG_TYPE", "WRONG_LANE",
           "SHORT", "UNCOVERED", "NO_NETWORK", "FLUID_DRY", "FLUID_OPEN", "FULL", "DAMAGED",
           "ghost", "DECON", "UNPAIRED", "NET_SPLIT", "THREAT:hot", "THREAT:close")

#: Which markers a single mode shows (full shows all).
MODE_MARKERS = {
    "entity": (),
    "state": ("EMPTY", "other-item", "DAMAGED", "ghost", "DECON"),
    "owner": ("TRESPASS", "unowned"),
    "zone": ("TRESPASS",),
    "threat": ("ATTACK", "THREAT:hot", "THREAT:close"),
    "items": ("other-item", "FULL"),
    "flow": ("UNPAIRED", "WRONG_TYPE", "WRONG_LANE"),
    "power": (),
    "build": ("SHORT", "UNCOVERED", "NO_NETWORK", "ghost"),
    "fluid": ("FLUID_OPEN", "FLUID_DRY"),
    "robots": ("NO_NETWORK", "NET_SPLIT"),
}

#: Direction -> (letter, unit step in tiles).  y grows to the south.
DIRS = {0: ("N", (0, -1)), 4: ("E", (1, 0)), 8: ("S", (0, 1)), 12: ("W", (-1, 0))}

#: Underground belt reach: the partner may be at most this many tiles away (centre to centre).
UG_MAX = {"underground-belt": 5, "fast-underground-belt": 7, "express-underground-belt": 9,
          "turbo-underground-belt": 11}

_EPS = 1e-6
_FIELDS = {
    "name": None, "type": None, "x": 0.0, "y": 0.0, "direction": 0, "tiles": None,
    "force": "player", "ghost": False, "decon": False, "hp": None, "status": None,
    "recipe": None, "filter": None, "items": None, "ug": None, "pair": None, "net": None,
    "unit": None, "slots": None, "ammo": None, "design": None, "src": None,
    # the 2026-10-17 sensing fields: what the build probes answered
    "lanes": None, "used": None, "storage_filter": None, "fluid": None,
    "fluids_in": None, "fluids_out": None, "have": None, "uncovered": None,
    "lgnet": None, "docked": None, "offnet": None,
    # V1: the game's last_user (attribution of foreign changes)
    "last_user": None,
}

#: Keys an annotation pass adds later (owner, flow, robots); the adapters never set them, so they
#: stay absent until that pass has run.
_ANNOTATIONS = ("owner", "zone", "trespass", "paired", "pair_at", "allowed", "lane_plan",
                "ug_expected", "dir_expected", "net_split", "drop_lane", "drop_belt")


def centre_tile(x, y):
    """The tile containing a point: (floor(x), floor(y))."""
    return (int(math.floor(float(x))), int(math.floor(float(y))))


def box_tiles(box):
    """Tiles covered by a box [l, t, r, b]: i with floor(l+eps) <= i < ceil(r-eps) (fk.world rule)."""
    left, top, right, bottom = (float(v) for v in box)
    i0, i1 = int(math.floor(left + _EPS)), int(math.ceil(right - _EPS))
    j0, j1 = int(math.floor(top + _EPS)), int(math.ceil(bottom - _EPS))
    return [[i, j] for i in range(i0, i1) for j in range(j0, j1)]


def footprint(name, x, y, direction=0, catalog=None):
    """Tiles of an entity of `name` centred at (x, y); east/west rotations swap width and height.

    Uses ``catalog.size(name)`` (fk.catalog.Catalog); an unknown name, or no catalog, is 1x1.
    """
    w, h = 1, 1
    if catalog is not None:
        try:
            w, h = catalog.size(name)
        except (KeyError, TypeError):
            w, h = 1, 1
    if int(direction) % 8 == 4:
        w, h = h, w
    i0 = int(math.floor(float(x) - w / 2.0 + _EPS))
    j0 = int(math.floor(float(y) - h / 2.0 + _EPS))
    return [[i0 + dx, j0 + dy] for dx in range(w) for dy in range(h)]


def thing(**fields):
    """A Thing with every key present; unknown keys are an error.  tiles default to the centre tile."""
    unknown = set(fields) - set(_FIELDS) - set(_ANNOTATIONS)
    if unknown:
        raise KeyError("unknown thing fields: %s" % ", ".join(sorted(unknown)))
    out = {k: fields.get(k, v) for k, v in _FIELDS.items()}
    for k in _ANNOTATIONS:
        if k in fields:
            out[k] = fields[k]
    if not out["name"]:
        raise ValueError("a thing needs a name")
    if out["type"] is None:
        out["type"] = out["name"]
    out["x"], out["y"] = float(out["x"]), float(out["y"])
    out["direction"] = int(out["direction"] or 0)
    out["items"] = dict(out["items"] or {})
    tiles = out["tiles"] or [list(centre_tile(out["x"], out["y"]))]
    out["tiles"] = sorted([int(t[0]), int(t[1])] for t in tiles)
    return out


def scene(source, things, tick=None, area=None, nests=None):
    """A Scene dict."""
    return {"source": source, "tick": tick, "area": list(area) if area is not None else None,
            "things": list(things), "nests": list(nests or [])}


def num(value):
    """Numbers the way the render prints them: integers bare, else repr(float); None -> '?'."""
    if value is None:
        return "?"
    f = float(value)
    return str(int(f)) if f.is_integer() else repr(f)


# -- V1 adapters ----------------------------------------------------------------------------------
def from_capture(dump):
    """A scene from a capture dump (the game link's ``snapshot(area)``), row keys as v4 captures
    them (fk/sight/live.py ``_thing``: n, t, x, y, d, g, dc, force, hp, st, rc, it, ug, u, box ...)
    plus ``lu``, the entity's last_user."""
    things = []
    for row in dump.get("things") or ():
        tiles = box_tiles(row["box"]) if row.get("box") else []
        things.append(thing(
            name=row["n"], type=row.get("t"), x=row["x"], y=row["y"], direction=row.get("d"),
            tiles=tiles or None, force=row.get("force"), ghost=bool(row.get("g")),
            decon=bool(row.get("dc")), hp=row.get("hp"), status=row.get("st"),
            recipe=row.get("rc"), filter=row.get("fl"), items=row.get("it"), ug=row.get("ug"),
            pair=row.get("pair"), net=row.get("net"), unit=row.get("u"),
            last_user=row.get("lu")))
    nests = [{"name": n["n"], "x": n["x"], "y": n["y"]} for n in dump.get("nests") or ()]
    return scene("live", things, tick=dump.get("tick"), area=dump.get("area"), nests=nests)


_UG_NAMES = {"in": "input", "out": "output"}


def design_scene(surface, cat, area=None):
    """A scene of the composed layout (``fkcore.compose.Surface``), optionally cut to ``area``
    [x0, y0, x1, y1] (tiles x0 <= i < x1): what the design says the world holds."""
    things = []
    for key in sorted(surface.entities):
        e = surface.entities[key]
        tiles = [list(t) for t in e.tiles]
        if area is not None and not any(area[0] <= t[0] < area[2] and area[1] <= t[1] < area[3]
                                        for t in tiles):
            continue
        things.append(thing(
            name=e.name, type=cat.kind(e.name) or e.name, x=e.x, y=e.y,
            direction=e.dir or 0, tiles=tiles, recipe=e.attrs.get("recipe"),
            ug=_UG_NAMES.get(e.attrs.get("ug")), design=e.plot, src=key))
    return scene(f"design:{surface.name}", things, area=area)
