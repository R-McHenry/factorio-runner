"""factorio-runner command line.

    python -m runner fresh   ARM.toml --store URL         the arm's root node: fresh map + kickoff kit
    python -m runner episode ARM.toml --store URL [-n N]  N sessions (each several episodes), in a row
    python -m runner smoke   ARM.toml --store URL [-n N]  fresh, then N sessions (one job, no shared store)
    python -m runner show    ARM.toml --store URL         the archive's frontier and the month's spend
    python -m runner ping    ARM.toml                     the model endpoint answers (lists models)
    python -m runner facts-import SEED.json --store URL   merge facts into the bank (by id)
    python -m runner status --store URL --site DIR        the status page (index.html + data.json)

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
    ap.add_argument("cmd", choices=["fresh", "episode", "smoke", "show", "ping", "facts-import", "status"])
    ap.add_argument("arm", nargs="?", help="arm spec (facts-import: the seed JSON; status: unused)")
    ap.add_argument("--store", default=os.environ.get("RUNNER_STORE", "./store"))
    ap.add_argument("--work", default="./work")
    ap.add_argument("-n", type=int, default=1)
    ap.add_argument("--seed", type=int)
    ap.add_argument("--node", help="start from this node instead of selecting one")
    ap.add_argument("--keep-work", action="store_true")
    ap.add_argument("--out", default="./out", help="each session's steps and results, also kept here")
    ap.add_argument("--transcript", action="store_true", help="print a compact transcript")
    ap.add_argument("--site", default="./site", help="status: the directory the page is written to")
    a = ap.parse_args(argv)
    if a.cmd == "status":
        from . import status
        d = status.write_site(open_store(a.store), a.site)
        print(f"wrote {a.site}: {len(d['arms'])} arms, {len(d['sessions'])} sessions, {len(d['episodes'])} episodes")
        return 0
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
    if a.cmd != "show":
        # only commands that run sessions record the arm's lock; show must work on a read-only store
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
    spec["_out"] = a.out
    for i in range(a.n):
        seed = None if a.seed is None else a.seed + i
        ses = episode.Session(store, spec, a.work, seed=seed, start_node=a.node)
        try:
            results = ses.run()
        except ledger.OverBudget as e:
            print(f"stopped: {e}")
            return 3
        finally:
            if a.transcript:
                print_transcript(ses.steps)
        for res in results:
            print(json.dumps({k: res.get(k) for k in ("session", "index", "id", "start_tick", "end_tick",
                                                       "reached_tick", "end_valid", "stop", "turns",
                                                       "usd", "invalid_marks", "candidates")}))
    return 0


def print_transcript(steps: list[str], width: int = 300):
    """A compact view of a session for the job log: each call and the start of its result."""
    for line in steps:
        s = json.loads(line)
        r = s.get("role")
        if r == "assistant":
            say = (s.get("content") or "").strip().replace("\n", " ")
            if say:
                print(f"  [{s['turn']}] says: {say[:width]}")
            for name, args in s.get("calls", []):
                print(f"  [{s['turn']}] {name} {args[:width]}")
        elif r == "tool":
            print(f"      -> {s['out'][:width].replace(chr(10), ' | ')}")
        elif r in ("episode", "invalid", "load", "knowledge"):
            body = {k: v for k, v in s.items() if k not in ("t", "role")}
            print(f"  == {r} {json.dumps(body)[:width]}")


if __name__ == "__main__":
    sys.exit(main())
