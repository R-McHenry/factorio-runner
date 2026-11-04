"""The win: a milestone ladder from the first science pack to the first rocket.

The cell goals the runner started with are open-ended: produce more of some item. A game is
won, not maximised, so an arm can set `[objective] mode = "ladder"` and this module supplies the
ordered target instead. The last milestone is the victory check: `rockets_launched >= 1`.

Checks read one state dict from `node.read_state`: `made` (item production totals), `silos`
(player rocket silos standing) and `rockets` (rockets launched). Stdlib only.
"""
from __future__ import annotations

from . import cell as cells

#: The ladder. Production milestones carry a cell level; `count_for(level)` is the target count.
MILESTONES = (
    {"id": "red-science", "label": "red science automated",
     "item": "automation-science-pack", "level": 4},        # 16
    {"id": "green-science", "label": "green science automated",
     "item": "logistic-science-pack", "level": 5},          # 32
    {"id": "blue-science", "label": "blue science automated",
     "item": "chemical-science-pack", "level": 5},          # 32
    {"id": "silo", "label": "rocket silo built", "silos": 1},
    {"id": "rocket", "label": "rocket launched", "rockets": 1},
)


def _done(m: dict, st: dict) -> bool:
    made = st.get("made") or {}
    if "item" in m:
        return int(made.get(m["item"], 0)) >= cells.count_for(m["level"])
    if "silos" in m:
        return int(st.get("silos", 0)) >= int(m["silos"])
    if "rockets" in m:
        return int(st.get("rockets", 0)) >= int(m["rockets"])
    return False


def status(st: dict) -> list[dict]:
    """Every milestone, in order, with its target and what the state has."""
    made = st.get("made") or {}
    out = []
    for m in MILESTONES:
        row = {"id": m["id"], "label": m["label"], "done": _done(m, st)}
        if "item" in m:
            row.update(item=m["item"], have=int(made.get(m["item"], 0)),
                       need=cells.count_for(m["level"]))
        elif "silos" in m:
            row.update(have=int(st.get("silos", 0)), need=int(m["silos"]))
        else:
            row.update(have=int(st.get("rockets", 0)), need=int(m["rockets"]))
        out.append(row)
    return out


def current(st: dict) -> dict | None:
    """The first milestone not yet met, or None when the game is won."""
    for m in status(st):
        if not m["done"]:
            return m
    return None


def next_goal(st: dict) -> dict:
    """The production goal of the next production milestone, else {} (silo/rocket stages)."""
    cur = current(st)
    if cur and "item" in cur:
        meta = next(m for m in MILESTONES if m["id"] == cur["id"])
        return {meta["item"]: meta["level"]}
    return {}


def victory(st: dict) -> bool:
    return bool(st.get("rockets", 0)) and int(st["rockets"]) >= 1


def ladder_text(st: dict) -> str:
    """One markdown-ish line per milestone, the current one marked."""
    rows, cur = [], (current(st) or {}).get("id")
    for m in status(st):
        mark = "x" if m["done"] else " "
        line = f"[{mark}] {m['label']} ({m['have']}/{m['need']})"
        if m["id"] == cur:
            line += "  <- now"
        rows.append(line)
    return "\n".join(rows)


def brief(st: dict) -> str:
    """A short line for tool output: the next target, or the win."""
    if victory(st):
        return "VICTORY: rocket launched"
    cur = current(st)
    if cur is None:
        return "milestones done"
    return f"next: {cur['label']} ({cur['have']}/{cur['need']})"
