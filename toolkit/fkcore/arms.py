"""fkcore.arms: where an inserter picks and drops, and the vectors the force may set (3.0, Bob's
adjustable inserters, Sea Block).

Rory, 2026-11-02: "bobs unlocks inserters picking up and placing down in any of the 4 adjacent
positions. When the long inserter upgrade unlocks, it doesnt actually give us any item called long
inserter, it just changes the valid drop and pickup positions for an inserter."

An inserter faces the side it picks from and reaches ``REACH`` tiles (one; a long-handed one two):
its pickup tile is that far ahead, its drop tile that far behind. With custom vectors (a pack whose
``pack.json`` says ``"inserter_vectors": true``: every inserter prototype has
``allow_custom_vectors``) an inserter may carry its own: the .fkp entity's ``pick=dx,dy`` and
``drop=dx,dy``, whole tiles from the inserter's own tile in the world's frame (x east, y south), the
direction left as it stands. Both or neither (:func:`canon`); vectors equal to the default are
dropped, so a plain inserter has none.

What the game does with them (a Sea Block laptop server, 2.0.77, bobinserters 2.0.4, 2026-11-02):

- setting ``pickup_position`` / ``drop_position`` leaves the direction as it was;
- a swing takes ``2 x floor(turn / rotation speed)`` ticks, ``turn`` the share of a full turn
  from pickup to drop: a plain inserter 1.198/s straight, 2.5/s round a corner; fast 3.75 and
  7.5; ``bob-red-inserter`` 2.5 and 5.0; range 2 as fast as range 1 (``lanes.inserter_rate``);
- a blueprint writes them as offsets from the inserter's centre in the world's frame, only where
  they differ from the prototype's, the drop 0.2 past the tile's centre (the far lane, as
  vanilla's 1.2; the game keeps 1.19921875);
- a ghost pasted from a blueprint keeps them, a single ghost takes them as world positions, and
  the entity built from either keeps them.

What the force may set (the bobinserters GUI's limits, by research; the stock read,
``supply.read``, reads ``TECHS``): from the start each of pickup and drop may be any of the 4 tiles
beside the inserter (range 1, 4 ways), chosen apart, so an inserter can turn a corner;
``bob-long-inserters-1`` range 2, ``-2`` range 3; ``bob-more-inserters-1`` 8 ways (the diagonals),
``-2`` more; ``bob-near-inserters`` the drop's near lane. The router lays cardinal vectors only, at
up to the force's range. Nothing read (a lab, a paper game): the base set. Pure, stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["REACH", "TECHS", "Arms", "of", "parse", "fmt", "default", "tiles", "canon", "turn",
           "heading", "offsets", "pairs", "needs", "turned", "locked", "blueprint_fields",
           "from_blueprint"]

#: Tiles from an inserter to its pickup and drop tile; every other inserter reaches 1.
REACH = {"long-handed-inserter": 2}
#: the bobinserters technologies that widen what an inserter may be set to
LONG_1, LONG_2 = "bob-long-inserters-1", "bob-long-inserters-2"
MORE_1, MORE_2 = "bob-more-inserters-1", "bob-more-inserters-2"
NEAR = "bob-near-inserters"
TECHS = (LONG_1, LONG_2, MORE_1, MORE_2, NEAR)

_STEP = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}
_HEAD = {v: k for k, v in _STEP.items()}
#: past the drop tile's centre, along the drop vector: the far lane (vanilla's drop is 1.2 out)
_FAR = 0.2


@dataclass(frozen=True)
class Arms:
    """What the force may set an inserter's vectors to."""
    range: int = 1             # tiles out a pickup or a drop may be
    ways: int = 4              # directions at each range: 4, 8 (the diagonals), 16 or more
    near: bool = False         # the drop may be on a belt's near lane

    def allows(self, v):
        """True when the force may set vector ``v`` (dx, dy)."""
        dx, dy = v
        r = max(abs(dx), abs(dy))
        if not 1 <= r <= self.range:
            return False
        if dx == 0 or dy == 0:
            return True
        return self.ways >= 8 and abs(dx) == abs(dy) if self.ways < 16 else True


def of(sup=None, packed=None):
    """The force's :class:`Arms`, or None where inserters take no custom vectors (the base game:
    ``pack.json`` has no ``inserter_vectors``). ``sup``: a ``supply.Supply`` (default: the build's
    snapshot, ``fkcore.avail``); its ``research["techs"]`` is {tech: researched}. Nothing read:
    the base set. ``packed``: True/False in place of the pack's word (tests)."""
    if packed is None:
        from . import pack
        packed = bool(pack.facts().get("inserter_vectors"))
    if not packed:
        return None
    if sup is None:
        from . import avail
        sup = avail.snapshot().sup
    done = ((getattr(sup, "research", None) or {}).get("techs") or {}) if sup is not None else {}
    if not isinstance(done, dict):
        done = {}
    return Arms(3 if done.get(LONG_2) else 2 if done.get(LONG_1) else 1,
                16 if done.get(MORE_2) else 8 if done.get(MORE_1) else 4, bool(done.get(NEAR)))


def parse(text):
    """``"dx,dy"`` -> (dx, dy) whole tiles, or None (absent or not two integers)."""
    if text is None:
        return None
    try:
        a, b = str(text).split(",")
        return int(a), int(b)
    except ValueError:
        return None


def fmt(v):
    return f"{int(v[0])},{int(v[1])}"


def default(name, direction):
    """(pick, drop) offsets of an inserter of ``name`` facing ``direction`` without vectors of
    its own, or None for a direction that is no cardinal."""
    step = _STEP.get(int(direction or 0) % 16)
    if step is None:
        return None
    r = REACH.get(name, 1)
    return (step[0] * r, step[1] * r), (-step[0] * r, -step[1] * r)


def vectors(name, direction, attrs):
    """(pick, drop) offsets of an entity: its own ``pick=``/``drop=`` where it has them, the
    default for the rest; None for no cardinal direction and no vectors."""
    d = default(name, direction)
    pick, drop = parse((attrs or {}).get("pick")), parse((attrs or {}).get("drop"))
    if d is None and (pick is None or drop is None):
        return None
    return (pick if pick is not None else d[0]), (drop if drop is not None else d[1])


def tiles(name, direction, attrs, tile):
    """(pickup tile, drop tile) of an inserter standing on ``tile``, or None."""
    v = vectors(name, direction, attrs)
    if v is None:
        return None
    (px, py), (dx, dy) = v
    return (tile[0] + px, tile[1] + py), (tile[0] + dx, tile[1] + dy)


def canon(name, direction, pick, drop):
    """The attrs an inserter's vectors make: {} when both are its default, else both."""
    d = default(name, direction)
    if d is not None and tuple(pick) == d[0] and tuple(drop) == d[1]:
        return {}
    return {"pick": fmt(pick), "drop": fmt(drop)}


def heading(v):
    """The direction (0, 4, 8, 12) a cardinal vector points, None for a diagonal or none."""
    dx, dy = v
    if (dx == 0) == (dy == 0):
        return None
    return _HEAD[((dx > 0) - (dx < 0), (dy > 0) - (dy < 0))]


def turn(pick, drop):
    """The share of a full turn the hand swings from pickup to drop: 0.5 straight through, 0.25
    round a corner."""
    import math
    a = math.atan2(pick[1], pick[0]) - math.atan2(drop[1], drop[0])
    a = abs((a + math.pi) % (2 * math.pi) - math.pi)
    return round(a / (2 * math.pi), 6)


def offsets(arms):
    """The cardinal vectors ``arms`` allows, nearest first: [(dx, dy)]."""
    out = []
    for r in range(1, arms.range + 1):
        out += [(0, -r), (r, 0), (0, r), (-r, 0)]
    return out


def pairs(arms, straight=True):
    """Every (pick, drop) of cardinal vectors ``arms`` allows, the cheapest-looking first: the
    plain inserter's straight swing at range 1, the corners, then the longer ones. ``straight``
    False leaves out the range-1 straight pairs (the caller tries those as plain inserters)."""
    vs = offsets(arms)
    got = [(p, d) for p in vs for d in vs if p != d]

    def order(pd):
        p, d = pd
        r = max(abs(p[0]) + abs(p[1]), abs(d[0]) + abs(d[1]))
        return (r, turn(p, d) != 0.5, abs(p[0]) + abs(p[1]) + abs(d[0]) + abs(d[1]))
    got.sort(key=order)
    if not straight:
        got = [(p, d) for p, d in got
               if not (max(abs(p[0]) + abs(p[1]), abs(d[0]) + abs(d[1])) == 1 and turn(p, d) == 0.5)]
    return got


def needs(arms, pick, drop):
    """What research a vector pair asks for that ``arms`` lacks: a tech name, or None."""
    for v in (pick, drop):
        r = max(abs(v[0]), abs(v[1]))
        if r > arms.range:
            return LONG_1 if r == 2 else LONG_2
        if v[0] and v[1] and not arms.allows(v):
            return MORE_1 if abs(v[0]) == abs(v[1]) else MORE_2
    return None


def turned(attrs, rot):
    """``attrs`` with its ``pick``/``drop`` turned ``rot`` quarter turns clockwise (a plot's or an
    instance's ``rot``, as ``compose.turn`` turns its direction); the same dict when it has none."""
    if not rot % 4 or ("pick" not in attrs and "drop" not in attrs):
        return attrs
    out = dict(attrs)
    for k in ("pick", "drop"):
        v = parse(out.get(k))
        if v is None:
            continue
        for _ in range(rot % 4):
            v = (-v[1], v[0])
        out[k] = fmt(v)
    return out


def locked(ents, arms):
    """{tech: [entity key]} of the inserters among ``ents`` whose own vectors ``arms`` (the force's)
    does not allow: the research each one waits on (fair play: the GUI's own limits)."""
    out = {}
    if arms is None:
        return out
    for e in ents:
        v = vectors(e.name, e.dir, e.attrs) if ("pick" in e.attrs or "drop" in e.attrs) else None
        tech = needs(arms, *v) if v else None
        if tech:
            out.setdefault(tech, []).append(e.key)
    return out


def blueprint_fields(attrs):
    """The blueprint fields of an entity's ``pick``/``drop`` attrs (popped): pickup_position at the
    tile's centre, drop_position 0.2 past it (the far lane), [x, y] as the game exports them. {}
    for an entity without them."""
    out = {}
    pick, drop = parse(attrs.pop("pick", None)), parse(attrs.pop("drop", None))
    if pick is not None:
        out["pickup_position"] = [pick[0], pick[1]]
    if drop is not None:
        out["drop_position"] = [_far(drop[0], drop[1], 0), _far(drop[0], drop[1], 1)]
    return out


def _far(dx, dy, axis):
    v = (dx, dy)[axis]
    n = max(abs(dx), abs(dy)) or 1
    return round(v + _FAR * v / n, 6) if v else 0


def _xy(v):
    if isinstance(v, dict):
        return float(v.get("x", 0)), float(v.get("y", 0))
    return float(v[0]), float(v[1])


def from_blueprint(name, direction, fields):
    """An inserter's blueprint ``pickup_position`` / ``drop_position`` (popped from ``fields``)
    back as attrs ({} or both, :func:`canon`): each rounded to its tile."""
    pick, drop = fields.pop("pickup_position", None), fields.pop("drop_position", None)
    if pick is None and drop is None:
        return {}
    d = default(name, direction) or ((0, -1), (0, 1))
    p = tuple(round(c) for c in _xy(pick)) if pick is not None else d[0]
    q = tuple(round(c) for c in _xy(drop)) if drop is not None else d[1]
    return canon(name, direction, p, q)
