"""factorio-runner command line.

    python -m runner fresh   ARM.toml --store URL         the arm's root node: fresh map + kickoff kit
    python -m runner episode ARM.toml --store URL [-n N]  N episodes, one after another
    python -m runner smoke   ARM.toml --store URL [-n N]  fresh, then N episodes (one job, no shared store)
    python -m runner show    ARM.toml --store URL         the archive's frontier and the month's spend
    python -m runner ping    ARM.toml                     the model endpoint answers (lists models)
    python -m runner facts-import SEED.json --store URL   merge facts into the bank (by id)

--store: a directory, file:///dir, or s3://bucket/prefix. --work: scratch space (default ./work).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import archive, episode, ledger, spec as specs
from .store import open_store


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="runner")
    ap.add_argument("cmd", choices=["fresh", "episode", "smoke", "show", "ping", "facts-import"])
    ap.add_argument("arm", help="arm spec (for facts-import: the seed JSON)")
    ap.add_argument("--store", default=os.environ.get("RUNNER_STORE", "./store"))
    ap.add_argument("--work", default="./work")
    ap.add_argument("-n", type=int, default=1)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--node", help="start from this node instead of selecting one")
    ap.add_argument("--keep-work", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "facts-import":
        from . import facts
        with open(a.arm) as fh:
            print(json.dumps(facts.import_seed(open_store(a.store), json.load(fh))))
        return 0
    spec = specs.load(a.arm)
    spec["_keep_work"] = a.keep_work

    if a.cmd == "ping":
        from .agent.llm import from_spec
        c = from_spec(spec["agent"])
        print(json.dumps(c.models() if hasattr(c, "models") else ["scripted"]))
        return 0

    store = open_store(a.store)
    store.put_json(f"arms/{spec['arm']}.lock.json", specs.lock(spec))
    if a.cmd in ("fresh", "smoke"):
        root = episode.fresh(store, spec, os.path.join(a.work, "fresh-" + spec["arm"]))
        print(json.dumps({"root": root.id, "tick": root.tick, "cell": root.key}))
        if a.cmd == "fresh":
            return 0
    if a.cmd == "show":
        arch = archive.get(store, spec["arm"])
        rows = archive.frontier(arch)
        print(f"{spec['arm']}: {len(arch['cells'])} cells, {arch.get('episodes', 0)} episodes")
        for r in rows[:15]:
            print(f"  height {r['height']:4d}  tick {r['tick']:9d}  ${r.get('usd', 0):.3f}  "
                  f"chosen {r.get('chosen', 0)}  {r['node']}")
        prov = spec["agent"].get("provider_name", spec["agent"].get("model"))
        print(f"spent this month on {prov}: ${ledger.spent(store, prov):.3f}")
        return 0
    rc = 0
    for i in range(a.n):
        seed = None if a.seed is None else a.seed + i
        try:
            res = episode.Episode(store, spec, a.work, seed=seed, start_node=a.node).run()
        except ledger.OverBudget as e:
            print(f"stopped: {e}")
            return 3
        print(json.dumps({k: res[k] for k in ("id", "start_tick", "end_tick", "reached_tick", "stop",
                                                "turns", "usd", "candidates")}))
        rc = 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
