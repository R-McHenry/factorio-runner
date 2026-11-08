"""fkcore.apply: the only world write (CORE: the writer rule), and the diff that judges it.

``apply(r, s, cat, area, free=False)`` pastes the composed layout ``s`` (a ``compose.Surface``)
inside ``area`` as one named blueprint. Every entity is keyed (``tags.src``), pole wires are
derived, and it goes through ``link.paste`` (superforced: ghosts go over what stands in the way,
and the game marks that for deconstruction). Then, by the surface's ``free`` property (3.1:
surface files have properties, not a play or lab mode; ``fkcore.surface``):

- not free (the default, a base): ghosts only; robots build them. A world entity in the area that the design does not
  explain and the watch policy leaves to the apply (``unowned``) is ordered for deconstruction, an
  unbuilt ghost too (the game removes a ghost ordered out at once); ``marked`` and ``claimed``
  ones stay. The paste must land where designed: a shifted paste is reported (``not-where-designed``),
  never cleaned up. Scaffolding (1.7, :func:`scaffold`): a ``unowned`` pole or roboport on no
  tile of the design stays up while the design's own poles (or roboports) in the area are not
  all built, so a rebuild doesn't cut power or the robot network; the first apply after they
  stand takes it up.
- ``own`` (1.2, ``fkcore.own``): the record of what the applies pasted. The toolkit's own
  entities that the design dropped are ``unowned`` whatever the watch policy says, so a moved,
  shrunk or emptied plot, or a replaced row, leaves nothing behind; with no ``area`` the scope
  reaches them wherever they stand. A carrier the record shows under another net that holds
  what its new net doesn't carry (``renet`` with ``other-item``: the old net's items on a belt,
  its fluid in a pipe) is ordered out before the paste, and the paste lays a fresh ghost on it:
  robots take the old one up with what it holds and build the new one (``relaid``; the game
  keeps both, checked on a laptop test server, 2.0.77). ``ApplyReport.own`` is the record after
  the paste, for the caller to save.
- ``free`` (the lab, or any surface where things are free): the chunks under the area (and no
  others) are generated and charted first (a new surface has none), each drill row's designed
  resource is seeded under it (bare ground has no ore; ``seed=False`` when the surface has a real
  terrain, whose ore is already there), a shifted paste is measured and pasted again, and the
  ghosts are revived at once (free materials).

What the paste refuses (the game won't place an offshore pump from a blueprint: the kickoff
found it on 2026-10-18, the V1 rehearsal again; nor anything on uncharted ground: a map with no
radar and no character, the 2.0 run, 2026-10-22) is placed as single ghosts, still keyed and
still built by robots (``placed_alone``). Each carries what the paste would have given it
(2.0.4): its recipe, underground end, requests, circuit settings, filters, fuel and other item
requests, and its wires (to the other single ghosts and to what the paste placed or stands).
A setting the game would not take on a ghost is named in the report (``alone_missed``). A design
entity that stands but is ordered out (ordered up for new settings) is not a placed one: it
goes, so it gets its ghost too.

Turned (2.0.5, :func:`turned_rows`): a belt, an underground, a splitter, an inserter, a
pipe-to-ground, a pump or a drill of the design's name on its position that faces another way, or
is the other underground end, is no placed one either. Where the paste can land, it turns or
replaces such a one itself (a laptop test server, 2.0.77: a belt, an inserter or a splitter is
turned in place, a belt a quarter turn off and an underground of the other end get a ghost and
are ordered out); where it is refused (uncharted ground) the one found stood as it was, counted as
placed, and every later build kept it so. So after the paste each one still turned is ordered out
and gets its single ghost (``turned``): robots take it up and lay it as designed. The 2.0 run's
south iron array (2026-10-22) stood on the burner iron block it replaced: 25 of that block's belts
and one inserter were on the array's tiles under the same names, and every build left them
facing the old block's ways, so ore and coal stopped at them.

Settings changed in place (2.1, Rory 2026-10-31): a change to a standing entity costs no robot
trip where the game allows it. Each change is one of:

- written in place: the codec writes every setting a paste would otherwise leave (``codec``:
  measured on a laptop server), so new settings, or a hand edit set back, are just the paste;
- upgraded: an entity of another name on a design entity's position, in the same fast-replace
  group and of the same size (inserter tiers but not the long-handed one, assembler 1-2-3, belt,
  underground and splitter tiers, every chest to every chest, stone to steel furnace, small to
  medium pole), is ordered up to the design's (``_upgrade``, before the paste) and robots swap
  it: its settings (the paste writes the design's on it), wires and contents stay;
- laid fresh: what the game can't change in place (another size or group, a turned one, a
  carrier under another net, a ghost, a drill's or an offshore pump's settings) is ordered out
  and the paste lays it new.

The report names each change and why (``changes``). Then the wire pass (``fkcore.wires``) adds the
design's missing wires and cuts the ones between design entities that the design doesn't have:
one count line. It runs after every paste (and ``fk wait built`` runs it once robots are done).

MISMATCH (3.1, placements; Rory 11-08: "a conflicting design must produce a MISMATCH, never a
silently adjusted plan or a build over or around"): a design tile that something stands on which
the toolkit did not paste there (not in ``own``: another placement's, a hand edit, or unknown) is
refused. Its row is left out of the paste, one line names what stands there, whose it is and the
command that would resolve it (``fk register``, ``fk capture --from``, ``fk adopt``, or taking it
out), and the rest of the paste goes (``refused``, ``sense.conflicts``). Mating is no mismatch: an
entity standing as the design has it is ``ok``. ``placements`` are the registry's records that
are not this design's own (``placements.explaining``); the pasted rows (``rows``) and the paste's
tick are on the report, for the caller to record the placements (``placements.from_apply``).

``diff(r, s, cat, area, policy)`` is a snapshot of the area judged by ``sense``: after robots finish
(or a lab revive) it is all ``ok``, which is 1:1. Nothing here registers anything; every game
call is one RCON command with the link's 5 s timeout.

The rip estimate (1.1, Rory 2026-10-20): what the next apply takes up (``rip_points`` of a diff:
unowned is ordered out, other-entity is pasted over), read with ``link.lua_rip_load``, and
``rip_report``: how many structures, the items they hold, and about how long the network's
construction robots need to carry it all to storage. A big one is a ``taken-up`` check, with the way to
make it faster (stop the input and let the factory drain first) and, when the taken-up things come
back as new ghosts of the same names, the hint that a factory moved (``fk merge`` joins
factories where they stand).
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field

from . import codec, link, scene, sense
from . import own as ownmod
from . import settings as settingsmod
from . import wires as wiresmod
from .fkp import natural_key
from .surface import DEFAULT_FORCE

__all__ = ["ApplyReport", "apply", "diff", "scope_rows", "scope_area", "default_area",
           "paste_blueprint", "turned_rows",
           "scaffold", "rip_points", "rip_load", "rip_minutes", "moved",
           "rip_report"]

#: a rip worth a RIP check: this many minutes of robot time, or this many structures
RIP_WARN_MIN, RIP_WARN_N = 3.0, 100
#: what the next apply takes up: NOBODY is ordered for deconstruction, WRONG is pasted over
RIP_VERDICTS = ("unowned", "other-entity")
#: taken-up structures coming back as new ghosts of the same names: a factory moved
MOVED_N = 30
#: kinds an apply leaves standing until the design's own of that kind stand (1.7, scaffolding)
SCAFFOLD_KINDS = ("electric-pole", "roboport")


@dataclass
class ApplyReport:
    surface: str
    force: str
    free: bool                         # the surface's property: revived by Lua, not robots
    area: tuple
    design: int = 0                    # entities in the scope
    pasted: int = 0                    # what build_blueprint placed (new ghosts)
    present: int = 0                   # design entities now there, as ghost or built
    corrected: tuple | None = None     # the shift a lab paste was corrected by
    misplaced: int = 0                 # ghosts that are not where the design says (play)
    placed_alone: int = 0              # refused by the paste, placed as single ghosts
    alone_missed: dict = field(default_factory=dict)  # settings those ghosts would not take
    landfill: int = 0                  # landfill tile ghosts laid under them on water (a pack)
    decon: int = 0                     # NOBODY entities ordered for deconstruction (play)
    dropped: int = 0                   # of them, the toolkit's own that the design dropped
    held: int = 0                      # NOBODY poles and roboports left up as scaffolding (1.7)
    released: dict = field(default_factory=dict)  # roboports not held, and why (2.1.3)
    hand: list = field(default_factory=list)   # placed by hand, replaced or removed (2.0)
    turned: list = field(default_factory=list)  # keys found turned, ordered out, laid fresh (2.0.5)
    relaid: list = field(default_factory=list)   # carriers laid fresh under another net
    resettled: list = field(default_factory=list)  # laid fresh for new settings a paste can't write
    written: list = field(default_factory=list)    # (key, why): settings the paste writes in place
    upgraded: list = field(default_factory=list)   # (key, old name, new name): robots swap it
    fresh: list = field(default_factory=list)      # (key, why): laid fresh, and why (2.1)
    wires: object = None               # the wire pass (``fkcore.wires.Pass``)
    own: dict | None = None            # the record of what the applies pasted, after this one
    refused: list = field(default_factory=list)   # 3.1: MISMATCH tiles left out (sense.Conflict)
    rows: list = field(default_factory=list)      # 3.1: the codec rows pasted (for placements)
    tick: int | None = None                       # the game tick after the paste
    dnp_kept: int = 0                  # built on DNP entities, kept standing (2.0.63)
    dnp_taken: int = 0                 # on DNP entities, ordered out: ghosts, and --confirm
    chunks: int = 0
    seeded: int = 0
    revived: int = 0
    revive_failed: int = 0
    seconds: float = 0.0
    notes: list = field(default_factory=list)

    def lines(self):
        a = ",".join(str(int(v)) for v in self.area)
        head = (f"apply {self.surface} area {a} ({'free' if self.free else 'robots'}, force "
                f"{self.force}): {self.design} entities designed, {self.pasted} ghosts placed, "
                f"{self.present} where designed")
        out = [head]
        if self.free:
            out.append(f"  free: {self.chunks} chunks generated, {self.seeded} ore tiles seeded, "
                       f"{self.revived} revived, {self.revive_failed} not revivable"
                       + (f", paste corrected by {self.corrected}" if self.corrected else ""))
        else:
            out.append(f"  robots: {self.decon} unowned entities ordered for deconstruction"
                       + (f" ({self.dropped} of them the toolkit's own, dropped from the design)"
                          if self.dropped else "") + "; robots build the ghosts")
            if self.dnp_taken:
                out.append(f"  dnp: {self.dnp_taken} on DNP entities ordered out (their ghosts, "
                           "and with --confirm what stands)")
            if self.dnp_kept:
                out.append(f"  dnp: {self.dnp_kept} built on DNP entities kept standing; "
                           "fk build --confirm takes them up")
            if self.held:
                out.append(f"  scaffold: {self.held} old poles and roboports stay up until the "
                           "design's own stand here, so power and robots keep running; the "
                           "first apply after that takes them up")
            for why, n in sorted(self.released.items()):
                out.append(f"  scaffold: {n} old roboport{'s' if n != 1 else ''} ordered out "
                           f"now, {why}")
            if self.hand:
                out.append(f"  by-hand: {len(self.hand)} placed by hand, replaced or removed by "
                           "this build (adopt into the design, leave out, or come back to it):")
                out += [f"    {line}" for line in self.hand]
        if self.relaid:
            items = sorted({i for v in self.relaid for i in v.wrong_items})
            out.append(f"  {len(self.relaid)} carriers laid under another net held "
                       f"{', '.join(items)}: ordered up, and the paste lays them fresh")
        if self.resettled:
            out.append(f"  {len(self.resettled)} with new settings a paste can't write (a ghost, "
                       "a drill, an offshore pump): ordered out, and the paste lays them fresh "
                       "with the new ones")
        if self.refused:
            out.append(f"  mismatch: {len(self.refused)} design tiles have something standing on "
                       "them the toolkit did not paste; refused (not pasted), the rest went:")
            out += [f"    {c.line()}" for c in self.refused]
        out += self.change_lines()
        if self.turned:
            shown = ", ".join(k.split(":", 1)[-1] for k in self.turned[:6])
            out.append(f"  re-laid {len(self.turned)} stood on their design tiles facing another way "
                       f"or as the other underground end ({shown}"
                       f"{' ...' if len(self.turned) > 6 else ''}): ordered out, and laid fresh "
                       "as designed")
        if self.placed_alone:
            out.append(f"  {self.placed_alone} the paste refused, placed one by one as ghosts "
                       "with their settings and wires (uncharted ground refuses a paste)"
                       + (f"; {self.landfill} landfill under them on water" if self.landfill
                          else ""))
        if self.alone_missed:
            out.append("  SETTINGS the single ghosts would not take (set them in the game, or "
                       "build again once a radar charts the ground): "
                       + ", ".join(f"{k} x{n}" for k, n in sorted(self.alone_missed.items())))
        if self.misplaced:
            out.append(f"  not-where-designed {self.misplaced} ghosts are not where the design says")
        line = self.wires.line() if self.wires is not None else None
        if line:
            out.append(f"  {line}")
        out += [f"  {n}" for n in self.notes]
        out.append(f"  {self.seconds:.1f} s")
        return out

    def change_lines(self, most=8):
        """How each change to a standing entity goes (2.1): written in place, upgraded, or laid
        fresh, and why; at most ``most`` named a kind."""
        out = []
        for word, rows, say in (
                ("written in place", self.written, lambda c: f"{_local(c[0])} ({c[1]})"),
                ("upgraded", self.upgraded, lambda c: f"{_local(c[0])} {c[1]} -> {c[2]}"),
                ("laid fresh", self.fresh, lambda c: f"{_local(c[0])} ({c[1]})")):
            if not rows:
                continue
            shown = "; ".join(say(c) for c in rows[:most])
            more = f" and {len(rows) - most} more" if len(rows) > most else ""
            tail = {"written in place": "the paste sets them on what stands",
                    "upgraded": "robots swap them; settings, wires and contents stay",
                    "laid fresh": "ordered out, and robots lay them new"}[word]
            out.append(f"  {word} {len(rows)} ({tail}): {shown}{more}")
        return out


def _local(key):
    return (key or "").split(":", 1)[-1]


def scope_rows(s, area=None, keys=None, dnp=False):
    """The codec rows of ``s`` inside ``area`` (any tile in it) and under a key prefix in ``keys``.
    2.0.63: the DNP entities (do not populate) only with ``dnp``: they are never pasted."""
    out = []
    for e in sorted(s.entities.values(), key=lambda e: natural_key(e.key)):
        if e.attrs.get("dnp") and not dnp:
            continue
        if area is not None and not _touches(e.tiles, area):
            continue
        if keys and not any(e.key.startswith(k) for k in keys):
            continue
        out.append({"src": e.key, "name": e.name, "x": e.x, "y": e.y, "direction": e.dir,
                    "attrs": dict(e.attrs)})
    return out


def _touches(tiles, area):
    return any(area[0] <= t[0] < area[2] and area[1] <= t[1] < area[3] for t in tiles)


def default_area(s, cat, keys=None, own=None):
    """An apply's area when none is given: the design in scope plus the toolkit's own entities
    it dropped (``own``), wherever they stand (a moved plot's old ground). None: nothing."""
    rows = scope_rows(s, None, keys, dnp=True)       # a DNP entity that stands is judged there
    for row in ownmod.dropped(own, s):
        if ownmod.in_scope(row, cat, None, keys):
            rows.append({"src": row.key, "name": row.name, "x": row.x, "y": row.y,
                         "direction": row.dir, "attrs": {}})
    return scope_area(rows, cat)


def scope_area(rows, cat, margin=2):
    """The bounding box of ``rows``' tiles, plus ``margin``."""
    xs, ys = [], []
    for r in rows:
        for x, y in cat.tiles(r["name"], float(r["x"]), float(r["y"]), r["direction"] or 0):
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return (min(xs) - margin, min(ys) - margin, max(xs) + 1 + margin, max(ys) + 1 + margin)


def paste_blueprint(rows, s, cat, description=None, label=None, poles=None):
    """The blueprint an apply pastes: ``rows`` with their poles' copper wires (derived) and the
    surface's circuit wires (2.0, its ``w`` records) whose ends are both among them. ``poles``:
    the whole design's copper wires (:func:`design_poles`); without them, the rows' own."""
    return codec.blueprint(rows, description=description, label=label,
                           wires=pole_wires(rows, s, cat, poles),
                           links=codec.links_of(rows, getattr(s, "wires", ())))


def design_poles(s, cat):
    """(the whole design's codec rows, the copper wires between their poles as row index pairs):
    one spanning forest for the surface, so an apply of one plot lays the wires an apply of the
    whole lays, and the wire pass never cuts one of them (2.1)."""
    rows = scope_rows(s)
    return rows, codec.pole_wires(rows, cat)


def pole_wires(rows, s, cat, poles=None):
    """[(i, j)] indices of ``rows``: the whole design's copper wires (``poles``, from
    :func:`design_poles`) with both ends among them; with no ``poles``, ``codec.pole_wires`` over
    the rows alone."""
    if poles is None:
        return codec.pole_wires(rows, cat)
    full, edges = poles
    index = {(r["name"], float(r["x"]), float(r["y"])): i for i, r in enumerate(rows)}
    out = []
    for i, j in edges:
        a, b = full[i], full[j]
        ka = index.get((a["name"], float(a["x"]), float(a["y"])))
        kb = index.get((b["name"], float(b["x"]), float(b["y"])))
        if ka is not None and kb is not None:
            out.append((ka, kb))
    return out


def _anchor(rows, cat):
    """Where build_blueprint is called so every entity lands on its own position: the centre of
    the rows' tile box (the game centres a blueprint on the cursor; found exact on the lab bench)."""
    x0, y0, x1, y1 = scope_area(rows, cat, margin=0)
    return (x0 + x1) / 2, (y0 + y1) / 2


def _things(cap):
    want = {}
    for t in cap["things"]:
        want.setdefault((t["n"], float(t["x"]), float(t["y"])), []).append(bool(t["g"]))
    return want


def _placed(cap):
    """What stands for the design after a paste: a ghost, or a built entity not ordered out. One
    ordered out goes, so its row still needs a ghost (2026-10-22: an inserter ordered up for a new
    condition on uncharted ground went, and nothing took its place)."""
    return {(t["n"], float(t["x"]), float(t["y"])) for t in cap["things"]
            if t["g"] or not t.get("dc")}


def turned_rows(rows, cap, cat):
    """The indices of ``rows`` (one of ``sense.TURN_KINDS``) whose ghost, or with no ghost their
    entity built and not ordered out, stands on the row's position under its name facing another
    way or as the other underground end, in a snapshot ``cap`` taken after the paste: the paste
    was refused there, so nothing will turn it. A ghost an earlier build left of an older design
    is one too (ordered out, the game removes it at once)."""
    built, ghosts = {}, {}
    for t in cap["things"]:
        k = (t["n"], float(t["x"]), float(t["y"]))
        if t.get("g"):
            ghosts[k] = t
        elif not t.get("dc"):
            built[k] = t
    out = []
    for i, row in enumerate(rows):
        k = (row["name"], float(row["x"]), float(row["y"]))
        t = ghosts.get(k) or built.get(k)
        if t is None or not cat.has_entity(row["name"]) \
                or cat.kind(row["name"]) not in sense.TURN_KINDS:
            continue
        if sense.turn(cat, row["name"], row.get("direction"), (row.get("attrs") or {}).get("ug"),
                      t.get("d"), t.get("ug")):
            out.append(i)
    return out


def single_ghosts(rows, s, cat, refused, poles=None):
    """What :func:`link.ghosts` places for the rows at indices ``refused``: their blueprint
    entities, and the paste's wires with an end among them, each end as [name, x, y]."""
    alone = set(refused)
    entities = [codec.entity_json(rows[i], k + 1) for k, i in enumerate(refused)]
    wires = codec.wire_list(rows, pole_wires(rows, s, cat, poles),
                            codec.links_of(rows, getattr(s, "wires", ())))

    def end(n):
        row = rows[n - 1]
        return [row["name"], float(row["x"]), float(row["y"])]
    return entities, [[end(a), ca, end(b), cb] for a, ca, b, cb in wires
                      if a - 1 in alone or b - 1 in alone]


def diff(r, s, cat, area, policy, net_items=None, tag="diff", own=None, force=None,
         confirm=False, placements=None):
    """Snapshot ``area`` and judge it against the design: a ``sense.SenseReport``. With
    ``force`` (2.0.12), the standing entities' settings too (``fkcore.settings``): those that
    differ from the design's are in the report's ``settings``, and so in its hand edits.
    ``confirm`` (2.0.63): what stands on a DNP entity is taken up (``sense``). ``placements``
    (3.1): the registry's records that explain what stands (``placements.explaining``)."""
    live = scene.from_capture(link.snapshot(r, s.name, area, tag))
    rep = sense.sense(s, live, cat, list(area), policy, net_items=net_items, own=own,
                      confirm=confirm, placements=placements)
    if force is not None:
        try:
            have = settingsmod.read(r, s.name, force, area, f"{tag}-settings")
            users = {ownmod.at(t["name"], t["x"], t["y"]): t.get("last_user")
                     for t in live["things"] if not t["ghost"]}
            rep.settings = settingsmod.drifts(settingsmod.design(scope_rows(s, area), s), have,
                                              rep.verdicts, users)
        except (link.LinkError, OSError, ValueError, KeyError, TypeError) as exc:
            rep.settings_error = f"{type(exc).__name__}: {exc}"
    return rep


def apply(r, s, cat, area=None, free=False, force=None, label=None, policy=None, keys=None,
          net_items=None, tag="apply", seed=True, own=None, confirm=False,
          placements=None) -> ApplyReport:
    """Paste the design inside ``area`` (default: all of ``s``, and the toolkit's own entities it
    dropped) as one named blueprint. ``free``: the surface's property (ghosts revived by Lua,
    nothing built by robots). ``own``: the record of what the applies pasted. ``placements``
    (3.1): the registry's records that are not this design's (``placements.explaining``); a
    design tile something else stands on is a MISMATCH, refused (``refused``)."""
    t0 = time.time()
    given = area
    rows = scope_rows(s, area, keys)
    if area is None:
        area = default_area(s, cat, keys, own)
    force = force or DEFAULT_FORCE
    rep = ApplyReport(s.name, force, bool(free), tuple(area or (0, 0, 0, 0)), design=len(rows))
    if area is None:
        rep.notes.append("nothing designed in this scope")
        return rep
    standing = []
    poles = design_poles(s, cat)
    upped = {}
    if not free and policy is not None:
        before = diff(r, s, cat, area, policy, net_items, tag=f"{tag}-before", own=own,
                      force=force, confirm=confirm, placements=placements)
        # 3.1: a design tile something stands on that the toolkit did not paste is refused: its
        # row leaves the paste, and one line says what stands there and whose it is
        rep.refused = sense.conflicts(before)
        bad = {c.key for c in rep.refused}
        if bad:
            rows = [row for row in rows if row["src"] not in bad]
            rep.design = len(rows)
        rep.dnp_kept = sum(1 for v in before.verdicts if v.verdict == "dnp-standing")
        rep.dnp_taken = sum(1 for v in before.verdicts if v.verdict == "unowned"
                            and v.key and v.detail.startswith(("DNP", "a ghost of a DNP")))
        stock = _scaffold_stock(r, s.name, force, before, cat)
        held = scaffold(before, s, cat, area, stock)
        rep.released = _released(before, s, cat, area, held)
        rep.hand = sense.hand_edits(before, held)
        # 2.1: another entity on a design entity's position goes up to it where the game allows
        cands = upgrade_candidates(before, s)
        why = _upgrade(r, s.name, force, cands)
        for c in cands:
            got = why.get((c["old"], c["x"], c["y"]))
            if got == "up":
                upped[(c["old"], c["x"], c["y"])] = c
                rep.upgraded.append((c["key"], c["old"], c["new"]))
            elif got in UPGRADE_WHY:
                rep.fresh.append((c["key"], f"{c['old']} stood there, {UPGRADE_WHY[got]}"))
        asked = {c["key"] for c in cands}
        rep.fresh += [(v.key, f"{v.detail.replace(' stands there', '')} stood on its tiles")
                      for v in before.verdicts if v.verdict == "other-entity"
                      and v.key not in asked and v.key not in bad
                      and any(not u[4] for u in v.under)]
        gone = [v for v in before.verdicts if v.verdict == "unowned"
                and (v.name, v.x, v.y) not in held and (v.name, v.x, v.y) not in upped]
        # the toolkit's own that still stand stay in the record: ordered out, held up or upgrading
        standing = [ownmod.at(v.name, v.x, v.y) for v in before.verdicts
                    if v.verdict == "unowned" and v.action == "own"]
        rep.dropped = sum(1 for v in gone if v.action == "own")
        rep.held = len(held)
        rep.decon = _decon(r, s.name, force, [(v.name, v.x, v.y) for v in gone])
        # ordered out before the paste, so the paste lays a fresh one on each (a paste over the
        # same entity not ordered out places nothing, and the old items stay)
        rep.relaid = [v for v in before.verdicts if v.renet and v.wrong_items
                      and v.verdict in ("ok", "settings-changed")]
        _decon(r, s.name, force, [(v.name, v.x, v.y) for v in rep.relaid])
        rep.fresh += [(v.key, f"held {', '.join(v.wrong_items)} under net {v.renet} before")
                      for v in rep.relaid]
        # 2.0: settings the design changed (the record shows what was pasted); 2.1: the paste
        # writes them in place, but on a ghost (replaced at once) and where the game takes none
        changed = [v for v in ownmod.resettled(own, s, before.verdicts) if v not in rep.relaid]
        rep.resettled = [v for v in changed if v.verdict == "ghost" or _skips(v.name)]
        _decon(r, s.name, force, [(v.name, v.x, v.y) for v in rep.resettled])
        rep.fresh += [(v.key, "a ghost with old settings" if v.verdict == "ghost" else
                       "new settings, and a paste writes none on a " + str(codec.entity_type(v.name)))
                      for v in rep.resettled]
        rep.written = [(v.key, "the design changed its settings") for v in changed
                       if v not in rep.resettled]
        done = {k for k, _w in rep.written}
        for d in getattr(before, "settings", None) or ():
            if d.key in done or d.wires:
                continue
            done.add(d.key)
            if _skips(d.name):
                rep.fresh.append((d.key, "set otherwise by hand, and a paste writes none on a "
                                  + str(codec.entity_type(d.name))))
                rep.resettled.append(d)
                _decon(r, s.name, force, [(d.name, d.x, d.y)])
            else:
                rep.written.append((d.key, "set back from a hand edit"))
        rep.own = ownmod.update(own, s, cat, None if given is None else area, keys, standing)
    if not rows:
        rep.notes.append("nothing designed in this scope" if not rep.refused else
                         "nothing pasted: every designed tile in this scope is a mismatch")
        rep.seconds = time.time() - t0
        return rep
    if free:
        rep.chunks = int(r.lua(link.lua_generate_area(s.name, force, area)).strip() or 0)
        ore = {}
        for e in s.entities.values():
            if e.attrs.get("resource") and cat.kind(e.name) == "mining-drill" \
                    and _touches(e.tiles, area):
                ore.setdefault(e.attrs["resource"], []).extend(e.tiles)
        if ore and seed:
            rep.seeded = int(r.lua(link.lua_seed(s.name, ore)).strip() or 0)
        elif ore:
            rep.notes.append("no ore seeded: the drills stand on the terrain's ore")
    desc = f"fk apply {s.name} area {','.join(str(int(v)) for v in area)} ({len(rows)} entities)"
    bp = paste_blueprint(rows, s, cat, desc, label or f"apply {s.name}", poles=poles)
    at = _anchor(rows, cat)
    rep.pasted = _int(link.paste(r, s.name, force, bp, at))
    want = {(row["name"], float(row["x"]), float(row["y"])) for row in rows}
    rep.rows = rows
    cap = link.snapshot(r, s.name, area, f"{tag}-ghosts")
    rep.tick = cap.get("tick") if isinstance(cap, dict) else None
    # an entity ordered up stands for its design entity until robots swap it
    up_now = {(c["new"], float(c["x"]), float(c["y"])) for (o, x, y), c in upped.items()
              if any(t["n"] == o and float(t["x"]) == float(x) and float(t["y"]) == float(y)
                     and not t.get("g") and not t.get("dc") for t in cap["things"])}
    seen = _things(cap)
    rep.present = len(want & (set(seen) | up_now))
    ghosts = {k for k, gs in seen.items() if any(gs)}
    stray = ghosts - want
    if stray and free and rep.present < len(want):
        shift = _shift(stray, want)
        if shift:
            r.lua(link.lua_destroy(s.name, area, names=["entity-ghost"]))
            link.paste(r, s.name, force, bp, (at[0] - shift[0], at[1] - shift[1]))
            rep.corrected = shift
            cap = link.snapshot(r, s.name, area, f"{tag}-ghosts")
            seen = _things(cap)
            rep.present = len(want & set(seen))
            stray = {k for k, gs in seen.items() if any(gs)} - want
    if not free:
        rep.misplaced = len(stray)
    placed = _placed(cap) | up_now
    if not free and not rep.misplaced:
        # what the paste left turned goes, and its single ghost takes its place (2.0.5)
        bad = turned_rows(rows, cap, cat)
        if bad:
            out = [(rows[i]["name"], float(rows[i]["x"]), float(rows[i]["y"])) for i in bad]
            _decon(r, s.name, force, out)
            rep.turned = [rows[i]["src"] for i in bad]
            rep.fresh += [(k, "stood turned, or as the other underground end")
                          for k in rep.turned]
            placed -= set(out)
    refused = [i for i, row in enumerate(rows)
               if (row["name"], float(row["x"]), float(row["y"])) not in placed]
    if refused and not rep.misplaced:
        entities, wires = single_ghosts(rows, s, cat, refused, poles)
        try:
            got = link.ghosts(r, s.name, force, entities, wires)
            rep.placed_alone, rep.alone_missed = got["n"], got["missed"]
            rep.landfill = got.get("landfill", 0)
        except link.LinkError as exc:
            rep.notes.append(str(exc))
        if rep.placed_alone:
            seen = _things(link.snapshot(r, s.name, area, f"{tag}-ghosts"))
            rep.present = len(want & (set(seen) | up_now))
    if free:
        said = r.lua(link.lua_revive(s.name, area))
        got = said.split()
        # 2.1.31: a game error ("Cannot execute command ...") stopped the build after its route
        if got and not all(g.isdigit() for g in got[:2]):
            rep.notes.append(f"revive: {said.strip()}")
            got = []
        rep.revived = int(got[0]) if got else 0
        rep.revive_failed = int(got[1]) if len(got) > 1 else 0
    # 2.1: the wires as the design has them, quietly (the hand edits above named any made by hand)
    rep.wires = wiresmod.run(r, s, cat, area, poles, tag=f"{tag}-wires")
    rep.seconds = time.time() - t0
    return rep


#: why the game won't swap an entity in place (``_upgrade``)
UPGRADE_WHY = {"group": "not in the design entity's fast-replace group",
               "size": "another size", "turned": "facing another way",
               "refused": "and the game refused the upgrade"}


def _skips(name):
    """True for an entity a paste writes no settings on (``codec.PASTE_SKIPS``)."""
    return codec.entity_type(name) in codec.PASTE_SKIPS


def upgrade_candidates(before, s):
    """What could go up to its design entity (2.1): a built entity of another name standing on a
    design entity's position (``other-entity`` in the diff ``before``), not ordered out. 3.1: the
    toolkit's own only (the ``own`` record has it); anything else there is a MISMATCH, refused.
    -> [{old, x, y, new, key, dir, ug}]."""
    out = []
    for v in before.verdicts:
        if v.verdict != "other-entity" or v.owner:
            continue
        e = s.entities.get(v.key)
        for name, x, y, _user, ghost, mine in v.under:
            if ghost or not mine or name == v.name \
                    or (float(x), float(y)) != (float(v.x), float(v.y)):
                continue
            out.append({"old": name, "x": float(x), "y": float(y), "new": v.name, "key": v.key,
                        "dir": e.dir if e is not None else None,
                        "ug": (e.attrs.get("ug") if e is not None else None)})
    return out


def lua_upgrade(surface, force, cands):
    """Lua that orders each candidate up to its design entity where the game allows a swap in
    place: the same fast-replace group and the same collision box (measured on a laptop server,
    2.1: then a paste orders the same upgrade itself; otherwise it lays a ghost and orders the old
    one out), facing the design's way. Replies JSON, per candidate: up, group, size, turned,
    refused or gone."""
    data = json.dumps([[c["old"], c["x"], c["y"], c["new"], c["dir"],
                        {"in": "input", "out": "output"}.get(c["ug"])] for c in cands],
                      separators=(",", ":"))
    return f"""
local s = game.get_surface({link._q(surface)})
local F = {link._q(force)}
local function box(p) local c = p.collision_box
  return string.format('%.3f,%.3f,%.3f,%.3f', c.left_top.x, c.left_top.y, c.right_bottom.x, c.right_bottom.y) end
local out = {{}}
for i, t in ipairs(helpers.json_to_table({link._q(data)})) do
  local e = s.find_entity(t[1], {{t[2], t[3]}})
  local pa, pb = prototypes.entity[t[1]], prototypes.entity[t[4]]
  local why
  if not e or not pa or not pb or e.to_be_deconstructed() then why = 'gone'
  elseif not pa.fast_replaceable_group or pa.fast_replaceable_group ~= pb.fast_replaceable_group then why = 'group'
  elseif box(pa) ~= box(pb) then why = 'size'
  elseif (t[5] and t[5] ~= e.direction) or (t[6] and t[6] ~= e.belt_to_ground_type) then why = 'turned'
  elseif e.to_be_upgraded() and e.get_upgrade_target() and e.get_upgrade_target().name == t[4] then why = 'up'
  elseif e.order_upgrade{{target = t[4], force = F}} then why = 'up'
  else why = 'refused' end
  out[i] = why
end
rcon.print(helpers.table_to_json(out))
"""


#: why an old roboport is not held up (2.1.3)
STOCK_OUT = "since the network holds too few roboports to build the design's: theirs build them"


def _kind(cat, name):
    try:
        return cat.kind(name)
    except KeyError:
        return None


def _waiting(before, cat):
    """[(name, x, y)] of the design's roboports the paste lays (not built yet)."""
    return [(v.name, v.x, v.y) for v in before.verdicts
            if v.verdict in ("ghost", "not-placed", "other-entity") and _kind(cat, v.name) == "roboport"]


def _waiting_roboports(before, cat):
    """{name: count} of the design's roboports the paste lays (not built yet)."""
    out = {}
    for name, _x, _y in _waiting(before, cat):
        out[name] = out.get(name, 0) + 1
    return out


def _scaffold_stock(r, surface, force, before, cat):
    """{roboport name: items of it in the networks whose construction areas reach the design's
    roboports waiting to be built}, or None when none waits or the read fails (2.1.3)."""
    waiting = _waiting(before, cat)
    if not waiting:
        return None
    try:
        got = json.loads(r.lua(_lua_build_stock(surface, force, waiting)).strip())
        return {n: int(got.get(n, 0)) for n, _x, _y in waiting} if isinstance(got, dict) else \
            {n: 0 for n, _x, _y in waiting}
    except (link.LinkError, OSError, ValueError, KeyError, TypeError, IndexError):
        return None


def _lua_build_stock(surface, force, things):
    """{name: items of it} in the logistic networks whose construction areas reach ``things``
    [(name, x, y)], each network counted once (a free read)."""
    data = json.dumps([[n, float(x), float(y)] for n, x, y in things])
    q = link._q
    return f"""
local s, f = game.get_surface({q(surface)}), game.forces[{q(force)}]
local out, seen = {{}}, {{}}
for _, t in pairs(helpers.json_to_table({q(data)})) do
  out[t[1]] = out[t[1]] or 0
  for _, net in pairs(s.find_logistic_networks_by_construction_area({{t[2], t[3]}}, f)) do
    local k = t[1] .. '/' .. net.network_id
    if not seen[k] then
      seen[k] = true
      out[t[1]] = out[t[1]] + net.get_item_count(t[1])
    end
  end
end
rcon.print(helpers.table_to_json(out))
"""


def _upgrade(r, surface, force, cands, batch=40):
    """``lua_upgrade`` in batches: -> {(old, x, y): why}; {} when the game can't be asked."""
    out = {}
    for i in range(0, len(cands), batch):
        part = cands[i:i + batch]
        try:
            got = json.loads(r.lua(lua_upgrade(surface, force, part)).strip() or "[]")
        except (ValueError, link.LinkError):
            return out
        if not isinstance(got, list):
            return out
        for c, why in zip(part, got):
            out[(c["old"], c["x"], c["y"])] = why
    return out


def _released(before, s, cat, area, held):
    """{why: how many} of the built unowned roboports :func:`scaffold` would have held but for
    the stock rule (2.1.3) and orders out now."""
    old = scaffold(before, s, cat, area)
    out = {}
    for v in before.verdicts:
        if (v.name, v.x, v.y) in old and (v.name, v.x, v.y) not in held:
            out[STOCK_OUT] = out.get(STOCK_OUT, 0) + 1
    return out


def scaffold(before, s, cat, area, stock=None):
    """What an apply leaves standing for now (1.7, Rory 2026-10-21: old poles and roboports stay
    up until the new grid stands, so a rebuild doesn't cut power or the robot network; both
    overnight players lost power to a re-laid grid): the built ``unowned`` poles and roboports on
    no tile of the design, while a pole (or roboport) the design has in ``area`` is not built
    yet (a ghost, missing, or under something else). One under a design tile goes as before (the
    paste needs its ground). ``before``: the diff before the paste. -> {(name, x, y)}.

    3.1 (Rory 11-08): a roboport the design moved or dropped is held like any other old one
    (2.1.3 had ordered the toolkit's own moved roboports out at once, a rule read off the own
    record: the design says where roboports go, what stands is a difference, and the 1.7 rule
    holds it until the design's own stand). ``stock``: {roboport name: items in the network} for
    the design's roboports waiting; no old roboport is held while the network holds fewer than
    they need: the old ones are taken up first and their items build the new (10-29: the
    replacements waited as ghosts for the scaffold's own items, a deadlock)."""
    def kind(name):
        return _kind(cat, name)

    waiting = {kind(v.name) for v in before.verdicts
               if v.verdict in ("ghost", "not-placed", "other-entity") and kind(v.name) in SCAFFOLD_KINDS}
    if not waiting:
        return set()
    if stock is not None:
        need = _waiting_roboports(before, cat)
        if any(stock.get(n, 0) < k for n, k in need.items()):
            waiting.discard("roboport")
    taken = set()
    for e in s.entities.values():
        if _touches(e.tiles, area):
            taken |= {tuple(t) for t in e.tiles}
    out = set()
    for v in before.verdicts:
        if v.verdict != "unowned" or v.ghost or kind(v.name) not in waiting:
            continue
        tiles = {tuple(t) for t in cat.tiles(v.name, float(v.x), float(v.y), 0)}
        if not tiles & taken:
            out.add((v.name, v.x, v.y))
    return out


#: entities per decon order (one RCON command each; pastes go in 3000-character chunks)
DECON_BATCH = 60


def _decon(r, surface, force, things):
    """Order ``things`` [(name, x, y)] out in batches; -> how many the game took."""
    n = 0
    for i in range(0, len(things), DECON_BATCH):
        n += _int(r.lua(link.lua_decon_list(surface, force, things[i:i + DECON_BATCH])))
    return n


def rip_points(rep):
    """The positions of what the next apply takes up, from a diff (a ``sense.SenseReport``):
    unowned and other-entity, and the carriers it lays fresh under another net (1.2)."""
    return [(v.x, v.y) for v in rep.verdicts if v.verdict.split()[0] in RIP_VERDICTS
            or (v.renet and v.wrong_items)]


def rip_load(r, surface, force, points, batch=80):
    """``link.lua_rip_load`` over ``points`` in batches that fit one RCON command (pastes go in
    3000-character chunks for the same reason): the counts summed, the network's numbers from
    the first batch that has them."""
    total = {"n": 0, "items": 0}
    for i in range(0, len(points), batch):
        got = json.loads(r.lua(link.lua_rip_load(surface, force, points[i:i + batch])).strip()
                         or "{}")
        total["n"] += int(got.get("n") or 0)
        total["items"] += int(got.get("items") or 0)
        for k in ("robots", "idle", "dist", "speed", "cargo"):
            if got.get(k) is not None and k not in total:
                total[k] = got[k]
    return total


def rip_minutes(load):
    """Robot time to take up a load read by ``link.lua_rip_load``: each structure, and each cargo
    of the items it holds, is a trip to the nearest storage and back, shared by the network's
    construction robots. An estimate: robots also build, charge and queue."""
    n, items = int(load.get("n") or 0), int(load.get("items") or 0)
    if not n:
        return 0.0
    trips = n + math.ceil(items / max(1, int(load.get("cargo") or 1)))
    trip = 2 * max(5.0, float(load.get("dist") or 50.0)) / max(0.5, float(load.get("speed") or 3.6))
    return trips * (trip + 2) / max(1, int(load.get("robots") or 1)) / 60


def moved(rep):
    """How many taken-up structures come back as new ones of the same name (not-placed or ghost)
    in the same diff: the sign of a factory that moved."""
    gone, new = {}, {}
    for v in rep.verdicts:
        head = v.verdict.split()[0]
        if head in RIP_VERDICTS:
            gone[v.name] = gone.get(v.name, 0) + 1
        elif head in ("not-placed", "ghost"):
            new[v.name] = new.get(v.name, 0) + 1
    return sum(min(c, new.get(name, 0)) for name, c in gone.items())


def rip_report(load, moved_n=0):
    """-> (the report line, the taken-up check text or None) for a load from ``link.lua_rip_load``."""
    n, items = int(load.get("n") or 0), int(load.get("items") or 0)
    minutes = rip_minutes(load)
    dist = load.get("dist")
    where = f"storage {dist:.0f} tiles away" if dist is not None else "no storage chest in reach"
    line = (f"rip: the apply takes up {n} structures holding {items} items: about {minutes:.0f} "
            f"min for {int(load.get('robots') or 0)} construction robots ({where}, "
            f"{int(load.get('cargo') or 1)} items a trip)")
    if minutes < RIP_WARN_MIN and n < RIP_WARN_N and moved_n < MOVED_N:
        return line, None
    text = (f"{n} structures holding {items} items come down: about {minutes:.0f} min of robot "
            "time, and builds wait for robots meanwhile. To make it faster, stop what feeds it "
            "first and let it run a few minutes to drain its belts and chests, then build")
    if moved_n >= MOVED_N:
        text += (f". {moved_n} of them come back as new ones of the same name: it looks like a "
                 "factory moved. To join factories, fk merge A,B --into C keeps both where they "
                 "stand")
    return line, text


def _int(text):
    try:
        return int(str(text).strip())
    except ValueError:
        return 0


def _shift(stray, want):
    """The most common offset between stray ghosts and design entities of the same name."""
    shifts = {}
    for n, x, y in stray:
        for wn, wx, wy in want:
            if wn == n and abs(wx - x) <= 3 and abs(wy - y) <= 3 and (wx, wy) != (x, y):
                shifts[(x - wx, y - wy)] = shifts.get((x - wx, y - wy), 0) + 1
    return max(shifts, key=shifts.get) if shifts else None
