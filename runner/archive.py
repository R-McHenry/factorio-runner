"""An arm's Go-Explore archive: one entry per cell, the best node known for it.

Stored as `archives/<arm>.json` and only changed through `Store.update_json`, so parallel episodes
of one arm can offer nodes at the same time. "Best" is the speedrun order: the earliest tick, then
the least spent. Selection is count-based (Go-Explore): weight 1/sqrt(1 + times chosen), times a
bonus for height, and synthetic (kit) nodes are never part of the from-fresh score.
"""
from __future__ import annotations

import math
import random
import time

from . import cell as cells
from .node import Node


def _empty(arm: str) -> dict:
    return {"arm": arm, "cells": {}, "episodes": 0, "created": time.time()}


def better(a: dict, b: dict) -> bool:
    """Is entry a better than entry b for the same cell?"""
    return (a["tick"], a.get("usd", 0.0)) < (b["tick"], b.get("usd", 0.0))


def offer(store, node: Node) -> str:
    """Offer a node to its arm's archive. -> 'new', 'faster' or 'known'."""
    if node.flags:
        return "flagged"
    k = node.key
    entry = {"node": node.id, "tick": node.tick, "usd": node.ledger.get("usd", 0.0),
             "height": cells.progress(node.levels), "synthetic": node.synthetic,
             "researched": node.researched}
    outcome = {}

    def fn(arch):
        arch = arch or _empty(node.arm)
        old = arch["cells"].get(k)
        if old is None:
            arch["cells"][k] = dict(entry, chosen=0, seen=1, first=time.time())
            outcome["v"] = "new"
        else:
            old["seen"] = old.get("seen", 0) + 1
            if better(entry, old):
                old.update(entry)
                outcome["v"] = "faster"
            else:
                outcome["v"] = "known"
        return arch

    store.update_json(f"archives/{node.arm}.json", fn)
    return outcome["v"]


def get(store, arm: str) -> dict:
    return store.get_json(f"archives/{arm}.json") or _empty(arm)


def select(arch: dict, rng: random.Random, height_bias: float = 1.0) -> str | None:
    """Pick a cell's node to explore from."""
    entries = list(arch["cells"].items())
    if not entries:
        return None
    top = max(e["height"] for _, e in entries) or 1
    weights = [(1.0 / math.sqrt(1 + e.get("chosen", 0))) * (1 + height_bias * e["height"] / top)
               for _, e in entries]
    k, _ = rng.choices(entries, weights=weights, k=1)[0]
    return k


def mark_chosen(store, arm: str, k: str):
    def fn(arch):
        arch = arch or _empty(arm)
        if k in arch["cells"]:
            arch["cells"][k]["chosen"] = arch["cells"][k].get("chosen", 0) + 1
        arch["episodes"] = arch.get("episodes", 0) + 1
        return arch
    store.update_json(f"archives/{arm}.json", fn)


def frontier(arch: dict) -> list[dict]:
    """Cells by height, the from-fresh ones only: [{key, height, tick, usd, node}]."""
    rows = [dict(e, key=k) for k, e in arch["cells"].items() if not e.get("synthetic")]
    return sorted(rows, key=lambda r: (-r["height"], r["tick"]))
