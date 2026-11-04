"""fkcore.sense: what the agent learns when it looks at an area (CORE-V1 §4.6, §6.4, §6.5), pure.

``sense(surface, live, cat, area, policy)`` compares the composed layout (``compose.Surface``)
with a live scene (``scene.from_capture`` of the game link's ``snapshot(area)``) inside ``area``
only. That is fair play's attention: nothing outside the area is read or reported.

Every layout entity in the area gets one verdict, matched to the world by position + name:

- ``ok``: there, as designed;
- ``ghost``: its ghost is there (pending);
- ``settings-changed``: there, but turned, the other underground end, or another recipe; a belt, an
  underground, a splitter, an inserter, a pipe-to-ground, a pump or a drill that stands turned
  or as the other end is also ``turned`` (2.0.5): the next apply lays it fresh as designed (a
  ghost of an older design facing another way too);
- ``other-entity``: another entity stands on its tiles;
- ``not-placed``: nothing there (never built, or destroyed: the next apply restores it).

A carrier that holds an item (or a fluid) its net doesn't expect is also ``other-item``, lane by
lane for a belt whose ``net=`` gives each lane its nets (2.0, ``iron|copper``: copper on the left
lane is wrong there; no leniency for the right belt in the wrong lane, CORE-V2); when the
toolkit's record (``own``) shows it was pasted under another net, the verdict says so
(``renet``): the next apply takes it up and lays it fresh (1.2). Where a ghost stands on an
entity ordered for deconstruction, the ghost is the design's (the entity is being replaced).
Every world entity in the area that no layout entity explains is one of:

- ``claimed``: inside a claim;
- ``marked``: under a mark;
- a foreign change, handled by the surface's watch policy for its region (plots: ``keep``,
  sanctioned space: ``mark <duration>``, or ``revert`` / ``strict``). It is attributed to its
  ``last_user``. ``keep`` and ``mark`` write a mark (``<actor>@<tick>``, its tiles, no expiry for
  keep), so the next look says ``marked``, not ``unowned``. One actor's ``keep`` and ``mark`` in
  one look are two marks, so a name already taken (in this look or on the surface) gets a
  suffix, ``<actor>@<tick>.2``. ``revert`` leaves it ``unowned`` for the next apply to
  steamroll, and ``strict`` leaves it ``unowned``, loud.

The toolkit's own entities (``own``, the record of what the applies pasted, ``fkcore.own``) are
no foreign change: one the design has dropped is ``unowned`` (the next apply removes it) whatever
the policy says, unless a claim or a mark covers it or a player touched it (a ``last_user``).

Enemies and nature (neutral force) are separate and never judged. Each foreign change gets a
"changes here" line with who, what and a hint: built by a player; an unkeyed ghost; a carrier next
to net N, which looks like a fix to it (offer ``adopt``). Expired marks are listed for clearing.
With notify ``nearby`` (play) the lines are only this look's report. With ``all`` (lab) they are
also returned as ``push``, for chat and the owner's inbox. Stdlib only; nothing here talks to the
game.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from . import fkp as fkpmod

__all__ = ["Verdict", "SenseReport", "sense", "duration_ticks", "hand_edits", "turn",
           "TURN_KINDS"]

TICKS_PER_S = 60
_CARRIERS = fkpmod.CARRIER_TYPES
_DIRECTIONAL = {"transport-belt", "underground-belt", "splitter", "inserter", "pipe-to-ground",
                "pump", "mining-drill", "offshore-pump", "boiler", "generator"}
#: the kinds an apply lays fresh when one stands on its design entity's position turned, or as
#: the other underground end (2.0.5): such a one breaks its line, and a robot replaces it for one
#: item. Generators, boilers and offshore pumps stay a DRIFT to read: a power plant is not taken
#: up for a turn
TURN_KINDS = ("transport-belt", "underground-belt", "splitter", "inserter", "pipe-to-ground",
              "pump", "mining-drill")


@dataclass
class Verdict:
    verdict: str
    name: str
    x: float
    y: float
    key: str | None = None            # the layout key, or the claim/mark that explains it
    detail: str = ""
    actor: str | None = None
    action: str | None = None         # mark | keep | revert | strict | own, for a foreign change
    wrong_items: list = field(default_factory=list)
    renet: str | None = None          # the net the toolkit pasted this carrier under, if another
    ghost: bool = False               # NOBODY: an unbuilt ghost, not a built entity (1.7)
    #: WRONG: what stands on the design entity's tiles, [(name, x, y, last_user, ghost, own)];
    #: ``own``: the toolkit pasted it (the record says so), so it is no hand edit (2.0)
    under: list = field(default_factory=list)
    #: DRIFT or GHOST: one of ``TURN_KINDS`` turned or the other underground end, which the next
    #: apply lays fresh (2.0.5)
    turned: bool = False

    def __str__(self):
        where = f"{_fmt(self.x)},{_fmt(self.y)}"
        who = f" by {self.actor}" if self.actor else ""
        text = f"{self.verdict:<8} {self.name} {where}" + (f" {self.key}" if self.key else "")
        if self.detail:
            text += f": {self.detail}"
        if self.turned:
            text += "; re-laid: the next build lays it fresh as designed"
        if self.wrong_items:
            text += f"; other-item {', '.join(self.wrong_items)}"
            if self.renet:
                text += (f" (laid under net {self.renet} before: the next apply takes it up and "
                         "lays it fresh)")
        return text + (who if self.verdict in ("marked", "unowned") and self.actor else "")


@dataclass
class SenseReport:
    area: list
    tick: int | None
    verdicts: list                    # [Verdict]
    marks: list                       # new fkp.Mark rows to write into the surface .fkp
    changes: list                     # "changes here" lines
    expired: list                     # names of marks past their expiry, inside the area
    push: list                        # lines to push (notify all), else []
    owners: dict                      # live thing index -> owner word (for the render)
    #: 2.0.12: standing entities whose settings differ from the design's (``fkcore.settings``
    #: Drift rows, read by ``apply.diff``), and why they could not be read
    settings: list = field(default_factory=list)
    settings_error: str | None = None

    def counts(self):
        out = {}
        for v in self.verdicts:
            out[v.verdict] = out.get(v.verdict, 0) + 1
            if v.wrong_items:
                out["other-item"] = out.get("other-item", 0) + 1
        return out

    def lines(self):
        c = self.counts()
        head = (f"sense {_area_text(self.area)} tick {self.tick}: "
                + (", ".join(f"{k} {c[k]}" for k in sorted(c)) or "nothing here"))
        # a carrier with stale items is listed too, so a look says where they are (1.1)
        # 2.0.63: a DNP entity that isn't built is as designed: counted, not listed
        out = [head] + [f"  {v}" for v in self.verdicts
                        if v.verdict not in ("ok", "dnp") or v.wrong_items]
        if self.changes:
            out += ["changes here:"] + [f"  {l}" for l in self.changes]
        if self.marks:
            out += [f"new mark {m.name}: {len(m.tiles)} tiles"
                    + (f", until {m.until}" if m.until is not None else ", kept until decided")
                    for m in self.marks]
        out += [f"expired mark {name}: list it for clearing" for name in self.expired]
        if self.settings:
            out += [f"settings: {len(self.settings)} standing entities set otherwise than the "
                    "design (hand edits)"]
        if self.settings_error:
            out.append(f"settings: not read ({self.settings_error})")
        return out


#: a ``mark`` with no duration (the surface file's default ``watch sanctioned: mark``) lasts 2h,
#: CORE-V1 §6.4's default; without it the mark expired at its own tick (worker sense, 2026-10-20)
DEFAULT_MARK = "2h"


def duration_ticks(text):
    """'2h', '30min', '90s' -> ticks (60 per second); None for no duration."""
    if not text:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(h|min|m|s)", text.strip())
    if not m:
        raise ValueError(f"a duration is like 2h, 30min or 90s, got {text!r}")
    per = {"h": 3600, "min": 60, "m": 60, "s": 1}[m.group(2)]
    return int(round(float(m.group(1)) * per * TICKS_PER_S))


def _fmt(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else repr(v)


def _area_text(area):
    return "area " + ",".join(str(int(a)) for a in area) if area else "everywhere"


def _in_area(tiles, area):
    if area is None:
        return True
    return any(area[0] <= t[0] < area[2] and area[1] <= t[1] < area[3] for t in tiles)


def _policy(text):
    """'mark 2h' -> ('mark', '2h'); 'keep' -> ('keep', None)."""
    parts = (text or "").split()
    return (parts[0] if parts else "strict"), (parts[1] if len(parts) > 1 else None)


def sense(surface, live, cat, area, policy, tick=None, net_items=None, own=None,
          confirm=False) -> SenseReport:
    """``policy``: a ``surface.SurfaceFile`` (its watch and notify settings) or a dict
    {"plots": "keep", "sanctioned": "mark 2h", "notify": "nearby"}. ``net_items``: {net: items}
    for other-item, with surface net names (``<factory>/<net>``). ``own``: the toolkit's own
    entities ({``own.at``: ``own.Row``}, ``fkcore.own.load``), or None.

    2.0.63, do not populate: a design entity with ``dnp`` is not built. Not there, it is ``dnp``
    (as designed; counted, not listed). A ghost of it is ``unowned`` (the apply cancels it). Built,
    it is ``dnp-standing``, kept as it stands; with ``confirm`` (``fk build --confirm``) it is
    ``unowned`` instead, and the apply takes it up."""
    if hasattr(policy, "watch"):
        watch, notify = dict(policy.watch), policy.notify_mode
    else:
        watch = {"plots": policy.get("plots", "keep"),
                 "sanctioned": policy.get("sanctioned", "mark 2h")}
        notify = policy.get("notify", "nearby")
    tick = live.get("tick") if tick is None else tick
    things = [t for t in live["things"] if _in_area(t["tiles"], area)]
    owners = {}
    for i, t in enumerate(things):
        if t["force"] == "enemy":
            owners[i] = "enemy"
        elif t["force"] == "neutral":
            owners[i] = "nature"
    judged = [i for i in range(len(things)) if i not in owners]
    at_pos, at_tile = {}, {}
    for i in judged:
        t = things[i]
        at_pos.setdefault((t["name"], round(t["x"], 3), round(t["y"], 3)), []).append(i)
        for tile in t["tiles"]:
            at_tile.setdefault(tuple(tile), []).append(i)

    used, verdicts = set(), []
    layout = [e for k, e in sorted(surface.entities.items()) if _in_area(e.tiles, area)]
    for e in layout:
        cands = [i for i in at_pos.get((e.name, round(float(e.x), 3), round(float(e.y), 3)), ())
                 if i not in used]
        if e.attrs.get("dnp"):
            built = [i for i in cands if not things[i]["ghost"]]
            for i in cands:
                used.add(i)
                owners[i] = e.key.rsplit("/", 1)[0]
                t = things[i]
                if t["ghost"]:
                    verdicts.append(Verdict("unowned", e.name, e.x, e.y, e.key,
                                            "a ghost of a DNP entity: the apply cancels it",
                                            None, "own", ghost=True))
                elif confirm:
                    verdicts.append(Verdict("unowned", e.name, e.x, e.y, e.key,
                                            "DNP, and --confirm: the apply takes it up",
                                            t.get("last_user"), "own"))
                else:
                    verdicts.append(Verdict("dnp-standing", e.name, e.x, e.y, e.key,
                                            "DNP in the design, kept as it stands: fk build "
                                            "--confirm takes it up"))
            if not built:
                verdicts.append(Verdict("dnp", e.name, e.x, e.y, e.key, "not built, as designed"))
            continue
        if cands:
            # an entity first, then a ghost; one ordered for deconstruction last: a ghost on it
            # is its replacement (a carrier laid fresh under another net, 1.2)
            i = min(cands, key=lambda c: (bool(things[c]["decon"]), bool(things[c]["ghost"])))
            used.add(i)
            t = things[i]
            owners[i] = e.key.rsplit("/", 1)[0]
            if t["ghost"]:
                v = Verdict("ghost", e.name, e.x, e.y, e.key, "pending")
                v.turned = cat.kind(e.name) in TURN_KINDS and bool(
                    turn(cat, e.name, e.dir, e.attrs.get("ug"), t["direction"], t["ug"]))
                verdicts.append(v)
                continue
            turned = turn(cat, e.name, e.dir, e.attrs.get("ug"), t["direction"], t["ug"])
            drift = "; ".join(turned + _recipe_drift(e, t, cat))
            v = Verdict("settings-changed" if drift else "ok", e.name, e.x, e.y, e.key, drift)
            v.turned = bool(turned) and cat.kind(e.name) in TURN_KINDS
            if v.turned and t.get("last_user"):
                v.actor = t["last_user"]          # a player turned it (or placed it so)
            net = _net(e)
            names = fkpmod.net_names(net)
            if names and net_items and all(n in net_items for n in names) \
                    and cat.kind(e.name) in _CARRIERS:
                v.wrong_items = _wrong(net, t, net_items)
                row = own.get((e.name, round(float(e.x), 3), round(float(e.y), 3))) if own else None
                if row is not None and row.net and row.net != net:
                    v.renet = row.net
            verdicts.append(v)
            continue
        others = sorted({i for tile in e.tiles for i in at_tile.get(tuple(tile), ()) if i not in used})
        if others:
            names = ", ".join(sorted({things[i]["name"] for i in others}))
            v = Verdict("other-entity", e.name, e.x, e.y, e.key, f"{names} stands there")
            for i in others:
                t = things[i]
                mine = own.get((t["name"], round(t["x"], 3), round(t["y"], 3))) if own else None
                v.under.append((t["name"], t["x"], t["y"], t.get("last_user"), bool(t["ghost"]),
                                mine is not None))
            verdicts.append(v)
            continue
        verdicts.append(Verdict("not-placed", e.name, e.x, e.y, e.key,
                                "not built or destroyed; the next apply restores it"))

    pending, changes = {}, []         # (actor, action) -> the mark it writes, named at the end
    carrier_tiles = {}
    for e in surface.entities.values():
        if e.attrs.get("net") and cat.kind(e.name) in _CARRIERS:
            for tile in e.tiles:
                carrier_tiles[tuple(tile)] = _net(e)
    for i in judged:
        if i in used:
            continue
        t = things[i]
        kind, detail = surface.whose(t["x"], t["y"])
        if kind == "claim":
            owners[i] = detail["key"]
            verdicts.append(Verdict("claimed", t["name"], t["x"], t["y"], detail["key"]))
            continue
        if kind == "mark":
            owners[i] = detail["key"]
            verdicts.append(Verdict("marked", t["name"], t["x"], t["y"], detail["key"],
                                    actor=detail["key"].rsplit("/", 1)[1].split("@")[0]))
            continue
        cx, cy = math.floor(t["x"]), math.floor(t["y"])
        in_plot = any(p.x <= cx < p.x + p.w and p.y <= cy < p.y + p.h
                      for p in surface.plots.values())
        region = "plots" if in_plot else "sanctioned"      # shared infra stands in sanctioned space
        action, dur = _policy(watch.get(region))
        actor = t.get("last_user") or "unknown"
        hint = _hint(t, cat, carrier_tiles)
        what = f"{'a ghost of ' if t['ghost'] else ''}{t['name']} at {_fmt(t['x'])},{_fmt(t['y'])}"
        mine = own.get((t["name"], round(t["x"], 3), round(t["y"], 3))) if own else None
        if mine is not None and not t.get("last_user"):
            owners[i] = "unowned"
            why = ("ordered out: robots take it up" if t.get("decon") else
                   "dropped from the design: the next apply removes it")
            verdicts.append(Verdict("unowned", t["name"], t["x"], t["y"], mine.key,
                                    f"the toolkit's own, {why}", None, "own",
                                    ghost=bool(t["ghost"])))
            changes.append(f"{what}: the toolkit's own ({mine.key}), {why.split(':')[0]} "
                           f"({region}); {why.split(': ')[1]}")
            continue
        if action in ("mark", "keep"):
            p = pending.setdefault((actor, action), {
                "mark": fkpmod.Mark(None, [], None if action == "keep" else
                                    (None if tick is None else
                                     tick + duration_ticks(dur or DEFAULT_MARK))),
                "verdicts": [], "things": []})
            for tile in t["tiles"]:
                if (tile[0], tile[1]) not in p["mark"].tiles:
                    p["mark"].tiles.append((tile[0], tile[1]))
            v = Verdict("marked", t["name"], t["x"], t["y"], None,
                        "kept in the plot until its owner decides" if action == "keep"
                        else f"TEMP {actor}, marked for {dur or DEFAULT_MARK}", actor, action)
            p["verdicts"].append(v)
            p["things"].append(i)
            verdicts.append(v)
        else:
            owners[i] = "unowned"
            verdicts.append(Verdict("unowned", t["name"], t["x"], t["y"], None,
                                    "the next apply removes it" if action == "revert" else
                                    "unattributed (strict)", actor, action,
                                    ghost=bool(t["ghost"])))
        changes.append(f"{what}: {'built by ' + actor if actor != 'unknown' else 'no last_user'}"
                       f" ({region}, {action}){'; ' + hint if hint else ''}")
    marks, taken = [], set(surface.marks)
    for actor_action in sorted(pending):
        p = pending[actor_action]
        name, n = f"{actor_action[0]}@{tick}", 2
        while name in taken:
            name, n = f"{actor_action[0]}@{tick}.{n}", n + 1
        taken.add(name)
        p["mark"].name = name
        for v in p["verdicts"]:
            v.key = f"{surface.name}:mark/{name}"
        for i in p["things"]:
            owners[i] = f"{surface.name}:mark/{name}"
        marks.append(p["mark"])
    expired = sorted(m.name for m in surface.marks.values()
                     if m.until is not None and tick is not None and m.until < tick
                     and _in_area([(math.floor(x), math.floor(y)) for x, y in m.tiles], area))
    push = list(changes) if notify == "all" else []
    return SenseReport(list(area) if area else None, tick, verdicts, marks, changes, expired,
                       push, owners)


def hand_edits(rep, held=()):
    """What a build replaces or removes that someone placed by hand: report lines (2.0, Rory
    2026-10-21: the source overrules the world by design, so a build may replace a hand change,
    but the agent is told, and decides: adopt it into the design, leave it out, or come back to
    it). From a diff (``rep``) taken before the paste:

    - a thing on a design entity's tiles that the toolkit did not paste (the ``own`` record does
      not have it): the paste goes over it, kept by the watch policy or not;
    - a foreign ``unowned`` (``revert`` / ``strict``) the apply orders out, unless ``held``
      (scaffolding, {(name, x, y)});
    - a ``turned`` one a player touched last (2.0.5): the build lays it fresh as designed;
    - a standing entity whose settings differ from the design's (2.0.12, ``rep.settings``): a
      condition, a wire, a filter, a request, a limit or a recipe, and what the paste does.

    Who: the entity's ``last_user``. Entities placed in the map editor carry none (the first lead
    run's map, 2026-10-21: none of 555 had one, Rory's edit among them), and neither do the
    toolkit's own, so "the game names no one" is said, not guessed."""
    out = []
    for v in rep.verdicts:
        if v.verdict == "other-entity":
            for name, x, y, user, ghost, mine in v.under:
                if mine:
                    continue
                out.append(f"{_who(user)} {'a ghost of ' if ghost else ''}{name} at {_fmt(x)},"
                           f"{_fmt(y)} on {_local(v.key)}'s tile; this build replaces it")
        elif v.verdict == "unowned" and v.action in ("revert", "strict") \
                and (v.name, v.x, v.y) not in held:
            out.append(f"{_who(v.actor)} {'a ghost of ' if v.ghost else ''}{v.name} at "
                       f"{_fmt(v.x)},{_fmt(v.y)}, which the design doesn't have; this build "
                       "removes it")
        elif v.turned and v.actor:
            out.append(f"{v.actor} turned {v.name} at {_fmt(v.x)},{_fmt(v.y)} ({_local(v.key)}: "
                       f"{v.detail}); this build lays it fresh as designed")
    out += [d.line() for d in getattr(rep, "settings", None) or ()]
    return out


def _who(user):
    if user and user != "unknown":
        return f"{user} placed"
    return "Someone placed (the game names no one: the map editor, or a script)"


def _local(key):
    """A composed key without its surface: ``nauvis:coal/~cfuel/s0`` -> ``coal/~cfuel/s0``."""
    return (key or "").split(":", 1)[-1]


def _net(e):
    """A carrier's net as the surface names it: ``<factory>/<net>`` (nets never leave a factory;
    a belt of two, ``<factory>/<a>|<factory>/<b>``)."""
    net = e.attrs.get("net")
    if not net:
        return None
    plot = getattr(e, "plot", None)
    return fkpmod.map_nets(net, lambda n: f"{plot}/{n}") if plot else net


def _wrong(net, t, net_items):
    """The items on a carrier that its nets don't carry: per lane where its ``net=`` gives each
    lane its own and the scene read the lanes, else what it holds against all its nets."""
    lanes, live = fkpmod.net_lanes(net), t.get("lanes")
    if lanes is not None and live:
        out = set()
        for side, got in zip(("L", "R"), live):
            ok = set().union(*(set(net_items[n]) for n in lanes[side]))
            out |= set(got or {}) - ok
        return sorted(out)
    ok = set().union(*(set(net_items[n]) for n in fkpmod.net_names(net)))
    return sorted(set(t["items"]) - ok)


def turn(cat, name, want_dir, want_ug, got_dir, got_ug):
    """How an entity standing on a design entity's position, of its name, is turned against it:
    [``faces 12, designed 4``] and [``underground input, designed output``], or [] when it faces
    as designed. ``want_dir``: the design's direction (None: not given); ``want_ug``: its ``ug=``
    (in, out); ``got_dir`` / ``got_ug``: what the game reports (None: unread, not judged)."""
    out = []
    want = want_dir or 0
    if (cat.kind(name) in _DIRECTIONAL or want_dir is not None) and got_dir is not None \
            and int(got_dir) != int(want):
        out.append(f"faces {int(got_dir)}, designed {int(want)}")
    ug = {"in": "input", "out": "output"}.get(want_ug)
    if ug and got_ug and got_ug != ug:
        out.append(f"underground {got_ug}, designed {ug}")
    return out


def _recipe_drift(e, t, cat):
    want_recipe = e.attrs.get("recipe")
    # a furnace picks its recipe from what it is fed: the game reports none while it is idle, and
    # there is no setting to compare (found by the first in-game diff, 2026-10-20: 22 false DRIFTs)
    if want_recipe and cat.kind(e.name) != "furnace" and t["recipe"] != want_recipe:
        return [f"recipe {t['recipe']}, designed {want_recipe}"]
    return []


def _hint(t, cat, carrier_tiles):
    if t["ghost"]:
        return "an unkeyed ghost: robots will build it"
    if cat.has_entity(t["name"]) and cat.kind(t["name"]) in _CARRIERS:
        near = set()
        for x, y in t["tiles"]:
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in carrier_tiles:
                    near.add(carrier_tiles[(nx, ny)])
        if near:
            return (f"next to net {', '.join(sorted(near))}: looks like a fix to it (adopt "
                    "it into the source, or revert it)")
    return ""
