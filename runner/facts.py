"""The fact bank: the strategy guide as atomic facts, each one's value measured.

- `facts/bank.json`: {"facts": [{id, text, when, kind, status, source}]}. Status: candidate,
  testing, accepted, retired. The guide is compiled from it, not edited by hand.
- Every episode includes each `testing` fact independently with probability p, and records which
  ones it got (randomized inclusion). Regressing episode outcomes on those indicators, with the
  start node as a fixed effect, estimates every fact's effect from one pool of episodes.
- Every episode ends with a knowledge step; its candidates go to `facts/candidates/<arm>/<episode>.json`
  for merging into the bank.
"""
from __future__ import annotations

import json
import random
import re

KNOWLEDGE_PROMPT = """The episode is over. Write down what you learned that would help a player in a
future game, as a JSON list of facts. Each fact:
{"text": "one or two sentences: the claim", "when": "the situation it applies to",
 "evidence": "what happened in this episode that shows it (ticks, counts, what broke)",
 "kind": "method" (how to work things out in any factory game) | "process" (how to design and grow a
 base so it stays easy to expand) | "game" (a specific fact about this game)}
Only facts you have evidence for from this episode. 0 to 5 facts. Reply with the JSON list only."""


def bank(store) -> dict:
    return store.get_json("facts/bank.json") or {"facts": []}


def scopes_for(spec: dict) -> set:
    """The fact scopes an arm's episodes can use: any, kit, its toolkit profile, biters."""
    s = {"any", "kit", spec["toolkit"]["profile"]}
    if spec["game"].get("biters", True):
        s.add("biters")
    return s


def sample(store, rng: random.Random, p: float, scopes: set | None = None) -> tuple[list[dict], list[dict]]:
    """-> (always-on accepted facts, the testing facts drawn for this episode)."""
    facts = [f for f in bank(store)["facts"] if scopes is None or f.get("scope", "any") in scopes]
    accepted = [f for f in facts if f.get("status") == "accepted"]
    drawn = [f for f in facts if f.get("status") == "testing" and rng.random() < p]
    return accepted, drawn


def import_seed(store, seed: dict) -> dict:
    """Merge a seed file's facts into the bank by id; a fact already there keeps its status."""
    out = {}

    def fn(b):
        b = b or {"facts": []}
        have = {f["id"]: f for f in b["facts"]}
        added = 0
        for f in seed.get("facts", []):
            if f["id"] in have:
                keep = have[f["id"]].get("status")
                have[f["id"]].update(f)
                have[f["id"]]["status"] = keep
            else:
                b["facts"].append(dict(f))
                added += 1
        out["added"], out["total"] = added, len(b["facts"])
        return b

    store.update_json("facts/bank.json", fn)
    return out


def prompt_block(accepted: list[dict], drawn: list[dict]) -> str:
    fs = accepted + drawn
    if not fs:
        return ""
    rng = random.Random(len(fs))
    fs = fs[:]
    rng.shuffle(fs)   # order carries no signal
    lines = ["## Notes from earlier play (they may be wrong; check what matters)"]
    lines += [f"- {f['text']}" + (f" (when: {f['when']})" if f.get("when") else "") for f in fs]
    return "\n".join(lines)


def parse_candidates(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text or "", re.S)
    if not m:
        return []
    try:
        got = json.loads(m.group(0))
    except ValueError:
        return []
    out = []
    for f in got if isinstance(got, list) else []:
        if isinstance(f, dict) and isinstance(f.get("text"), str) and f["text"].strip():
            out.append({k: str(f.get(k, ""))[:1000] for k in ("text", "when", "evidence", "kind")})
    return out[:5]
