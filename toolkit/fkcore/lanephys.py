"""fkcore.lanephys: the lane physics of belts, one table for every reader (PLAN-LANES phase 1).

What happens to a belt's two lanes when items move from one carrier into another, as measured in
the game (CORE-V2.md "Lane physics", docs/LAB-LANES-20261020.md, 2.0.77). It works on directions
only (0 N, 4 E, 8 S, 12 W) and knows nothing of layouts or graphs, so the lane graph
(``fkcore.lanes``, the judge) and, later, the router's search read the same rules. L and R are the
lanes looking along the flow (the game's transport lines 1 and 2).

``moves(via, src_dir, dst_dir, ug)`` -> the lane edges of one move, ``((src lane, dst lane,
rank), ...)``, and the source lane it blocks (or None):

- ``straight``, ``turn``, ``tunnel``, ``splitter``: every item keeps its lane;
- ``side``: a belt into the side of a belt, a 2-to-1 merge: both its lanes onto the target's near
  lane; the lane that enters further upstream is served first (rank 1, the other rank 2);
- ``ug_side``: a belt into the side of an underground end: only its lane on the open side goes
  through (an entrance is open upstream, an exit downstream), onto the near lane; the lane on the
  closed side is blocked.

Inserters and drills fill one lane: ``drop_lane`` (across a belt the far lane, in line the right
lane), ``turn_drop_lane`` (onto a turning belt) and ``mine_lane`` (a drill's near lane).
``carry`` folds what each lane holds through a move: the search's step.

A tile that other carriers feed (a pair's join: two belts side-loading it) holds what the game
makes of its feeds, by the carrier the router puts there (``fed``): a belt fed from one side with
nothing behind turns, from two sides or with something behind each side-loads; an underground end
side-loaded passes only the open-side lane; a splitter takes nothing from its sides; nothing goes
in head-on. ``tile_mask`` turns that into the router's verdict per carrier and heading (PLAN-LANES
phase 2): the carriers that hold exactly the lanes the tile needs and lose nothing. The search,
Python and native alike, reads only the verdicts, so the rules stay here. Pure, stdlib only.
"""
from __future__ import annotations

__all__ = ["LANES", "RULES", "left", "right", "near", "moves", "carry", "drop_lane",
           "turn_drop_lane", "mine_lane", "KEEP", "SIDE_RANK", "DROP_RANK", "CARRIERS", "fed",
           "tile_mask", "mask_bit"]

LANES = ("L", "R")
KEEP = ("straight", "turn", "tunnel", "splitter")
SIDE_RANK = (1, 2)       # side-load: the lane entering further upstream first, then the other
DROP_RANK = 3            # an inserter's or a drill's drop onto a belt: after every belt feed

#: Every rule, in the words of CORE-V2's "Lane physics" table (the measured rows).
RULES = {
    "straight": "every item keeps its lane",
    "turn": "fed from one side with nothing behind: a turn, every item keeps its lane",
    "tunnel": "an underground entrance to its exit: every item keeps its lane",
    "splitter": "every item keeps its lane",
    "side": "a belt into the side of a belt: both incoming lanes onto the target's near lane",
    "ug_side": "a belt into the side of an underground end: the lane on the open side goes "
               "through onto the near lane, the lane on the closed side is blocked",
    "drop": "an inserter across a belt fills the far lane; in line, always the right lane",
    "turn_drop": "onto a turning belt: from an outer side the inside lane, from an inner side "
                 "the outside lane",
    "mine": "a drill drops onto the near lane (in line: the right lane)",
    "pickup": "an inserter picks from both lanes",
}


def left(d):
    """The direction on the left of heading ``d``."""
    return (d + 12) % 16


def right(d):
    return (d + 4) % 16


def _other(lane):
    return "R" if lane == "L" else "L"


def near(src_dir, dst_dir):
    """The lane of a belt heading ``dst_dir`` nearest to a carrier moving ``src_dir`` into its
    side: from its left side (moving towards its right) the left lane; None in line."""
    if src_dir == right(dst_dir):
        return "L"
    if src_dir == left(dst_dir):
        return "R"
    return None


def moves(via, src_dir=None, dst_dir=None, ug=None):
    """(((src lane, dst lane, rank), ...), the blocked source lane or None) of one move ``via``
    from a carrier heading ``src_dir`` into one heading ``dst_dir`` (``ug``: "in" or "out" for an
    underground target of ``ug_side``). No edges where the move does not reach a lane."""
    if via in KEEP:
        return (("L", "L", 0), ("R", "R", 0)), None
    to = near(src_dir, dst_dir)
    if to is None:
        return (), None
    if via == "side":
        first = "L" if left(src_dir) == (dst_dir + 8) % 16 else "R"    # enters further upstream
        return ((first, to, SIDE_RANK[0]), (_other(first), to, SIDE_RANK[1])), None
    if via == "ug_side":
        open_dir = (dst_dir + 8) % 16 if ug == "in" else dst_dir
        lane = "L" if left(src_dir) == open_dir else "R"
        return ((lane, to, SIDE_RANK[0]),), _other(lane)
    raise ValueError(f"no lane rule for {via!r}")


def carry(via, held, src_dir=None, dst_dir=None, ug=None):
    """What arrives on each lane of the target: ``held`` {lane: frozenset of nets} on the source
    -> {lane: frozenset} on the target (only the lanes the move reaches)."""
    edges, _blocked = moves(via, src_dir, dst_dir, ug)
    out = {}
    for a, b, _rank in edges:
        out[b] = out.get(b, frozenset()) | frozenset(held.get(a, ()))
    return out


def drop_lane(drop_dir, belt_dir):
    """The lane an inserter dropping in direction ``drop_dir`` fills on a belt heading
    ``belt_dir``: the far lane across it, the right lane in line."""
    if drop_dir % 8 == belt_dir % 8:
        return "R"
    return "R" if drop_dir == right(belt_dir) else "L"


def turn_drop_lane(come, go, side):
    """The lane a drop from ``side`` fills on a belt that turns from heading ``come`` to ``go``:
    from an outer side the inside lane (a left turn's left lane), from an inner side, where the
    belt comes in or goes out, the outside lane (2.1.25, measured on 2.0.77)."""
    inside = "L" if go == left(come) else "R"
    if side in ((come + 8) % 16, go):
        return _other(inside)
    return inside


#: The carriers a router may put on a tile, in ``tile_mask``'s bit order.
CARRIERS = ("belt", "ug_in", "ug_out", "splitter")
_EMPTY = frozenset()


def fed(carrier, d, feeds):
    """({lane: nets}, lost nets) of a ``carrier`` heading ``d`` on a tile fed by ``feeds``
    [(the feeder's heading, {lane: nets})]: what each of its lanes holds, and the nets a feed
    loses (a blocked lane, a head-on belt, a splitter's side)."""
    behind = any(src == d for src, _h in feeds)
    sides = sum(near(src, d) is not None for src, _h in feeds)
    out, lost = {}, _EMPTY
    for src, held in feeds:
        if src == d:
            via = None if carrier == "ug_out" else "straight"     # an exit's back is the tunnel
        elif near(src, d) is None:
            via = None                                             # head-on
        elif carrier == "belt":
            via = "turn" if sides == 1 and not behind else "side"
        elif carrier in ("ug_in", "ug_out"):
            via = "ug_side"
        else:
            via = None                                             # a splitter's side
        if via is None:
            for nets in held.values():
                lost |= frozenset(nets)
            continue
        edges, blocked = moves(via, src, d, "in" if carrier == "ug_in" else "out")
        for a, b, _r in edges:
            out[b] = out.get(b, _EMPTY) | frozenset(held.get(a, ()))
        if blocked is not None:
            lost |= frozenset(held.get(blocked, ()))
    return out, lost


def mask_bit(carrier, d4):
    """The bit of ``tile_mask`` for a carrier heading ``d4`` (the router's 0 N, 1 E, 2 S, 3 W)."""
    return CARRIERS.index(carrier) * 4 + d4


def tile_mask(need, feeds):
    """The router's verdict on a fed tile: bit ``mask_bit(carrier, d4)`` set when that carrier
    heading that way holds exactly ``need`` ({lane: nets}) and loses nothing. ``feeds`` as
    ``fed``'s, headings 0 N, 4 E, 8 S, 12 W."""
    want = {lane: frozenset(need.get(lane, ())) for lane in LANES}
    mask = 0
    for carrier in CARRIERS:
        for d4 in range(4):
            out, lost = fed(carrier, d4 * 4, feeds)
            if not lost and all(out.get(lane, _EMPTY) == want[lane] for lane in LANES):
                mask |= 1 << mask_bit(carrier, d4)
    return mask


def mine_lane(drill_dir, belt_dir):
    """The lane a drill facing ``drill_dir`` drops onto on a belt heading ``belt_dir``: the half
    of the tile towards the drill (in line: the right lane)."""
    side = (drill_dir + 8) % 16
    return "L" if side == left(belt_dir) else "R"
