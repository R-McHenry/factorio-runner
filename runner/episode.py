"""One episode: pick a node, restore it, set a goal, let the agent play on the lockstep clock,
offer the nodes it reaches, and end with the knowledge step.

Writes to the store:
- `nodes/<arm>/<id>.json` and blobs for every snapshot, offered to `archives/<arm>.json`;
- `episodes/<arm>/<id>.json`, the result;
- `steps/<arm>/<id>.jsonl`, every turn (tool calls, results, usage);
- `facts/candidates/<arm>/<id>.json`, the knowledge step's facts;
- the month's spend in `ledger/`.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import time
import uuid

from . import archive, cell as cells, facts, kit, ledger, node as nodes
from .agent import llm as llms
from .agent.tools import PROFILES, Toolbox, goal_text
from .server import Server, ensure_factorio

PROMPTS = os.path.join(os.path.dirname(__file__), "prompts")


def load_prompt(layers: list[str]) -> str:
    parts = []
    for layer in layers:
        with open(os.path.join(PROMPTS, f"{layer}.md"), encoding="utf-8") as fh:
            parts.append(fh.read().strip())
    return "\n\n".join(parts)


def new_server(spec: dict, root: str) -> Server:
    g = spec["game"]
    exe = ensure_factorio(g["factorio"], os.environ.get("FACTORIO_CACHE", os.path.expanduser("~/.cache/factorio")))
    return Server(exe, root, mods=spec["_paths"].get("mods"))


# -- fresh: the root node ------------------------------------------------------------------------
def fresh(store, spec: dict, work: str) -> nodes.Node:
    """A fresh map with the kickoff kit, frozen as the arm's root node and offered to its archive."""
    arm = spec["arm"]
    g = spec["game"]
    os.makedirs(work, exist_ok=True)
    srv = new_server(spec, os.path.join(work, "server"))
    with srv:
        save = srv.create("fresh", spec["_paths"].get("map_gen"), spec["_paths"].get("map_settings"),
                          g.get("seed"))
        srv.start(save)
        report = kit.apply(srv)
        wd = os.path.join(work, "work")
        os.makedirs(wd, exist_ok=True)
        with open(os.path.join(wd, "NOTES.md"), "w") as fh:
            fh.write("# Notes\n\nNothing yet: this is a fresh map with the kickoff kit.\n")
        root = nodes.snapshot(store, srv, wd, arm, None, None, {"usd": 0.0, "turns": 0})
    store.put_json(f"roots/{arm}.json", {"node": root.id, "kit": report, "created": time.time()})
    archive.offer(store, root)
    return root


# -- the episode -----------------------------------------------------------------------------------
class Episode:
    def __init__(self, store, spec: dict, work: str, client=None, seed: int | None = None,
                 start_node: str | None = None, log=print):
        self.store = store
        self.spec = spec
        self.arm = spec["arm"]
        self.ep = spec["episode"]
        self.id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        self.work = os.path.join(work, self.id)
        self.rng = random.Random(seed if seed is not None else uuid.uuid4().int)
        self.client = client or llms.from_spec(spec["agent"])
        self.provider = spec["agent"].get("provider_name", spec["agent"].get("model", "model"))
        self.start_node_id = start_node
        self.log = log
        self.usd = 0.0
        self.tokens = {"in_hit": 0, "in_miss": 0, "out": 0}
        self.turns = 0
        self.offered: list[dict] = []
        self.last_snapshot_tick = 0
        self.reached_tick: int | None = None
        self.steps_buf: list[str] = []

    # -- the clock, as the agent's wait tool sees it -------------------------------------------------
    def wait(self, ticks: int) -> str:
        cap = int(self.ep.get("wait_cap", 36000))
        ticks = max(1, min(ticks, cap))
        left = self.tick_budget_end - self.server.tick()
        if left <= 0:
            return "no game time left in this episode: finish"
        ticks = min(ticks, left)
        chunk = int(self.ep.get("check_every", 600))
        done = 0
        while done < ticks:
            step = min(chunk, ticks - done)
            self.server.run(step)
            done += step
            st = nodes.read_state(self.server)
            levels = cells.cell(st["made"])
            if self.reached_tick is None and cells.reached(levels, self.goal):
                self.reached_tick = st["tick"]
            if (levels != self.last_levels
                    and st["tick"] - self.last_snapshot_tick >= int(self.ep.get("snapshot_every", 7200))):
                self._snapshot()
            if self.reached_tick is not None:
                break
        st = nodes.read_state(self.server)
        cut = "" if done == ticks else f" (stopped after {done} of {ticks}: goal met)"
        line = f"tick {st['tick']}{cut}; goal: {goal_text(self.goal, st['made'])}"
        if self.reached_tick is not None:
            line += "\nGOAL MET. Bring NOTES.md up to date for whoever continues from here, then finish."
        return line

    def _snapshot(self):
        node = nodes.snapshot(self.store, self.server, self.workdir, self.arm, self.start.id, self.id,
                              self._ledger())
        outcome = archive.offer(self.store, node)
        self.offered.append({"node": node.id, "tick": node.tick, "height": cells.progress(node.levels),
                             "outcome": outcome})
        self.last_snapshot_tick = node.tick
        self.last_levels = node.levels
        self.log(f"  node {node.id} at tick {node.tick}: {outcome}")

    def _ledger(self) -> dict:
        base = self.start.ledger
        return {"usd": round(base.get("usd", 0.0) + self.usd, 6),
                "turns": base.get("turns", 0) + self.turns,
                "tokens_in": base.get("tokens_in", 0) + self.tokens["in_hit"] + self.tokens["in_miss"],
                "tokens_out": base.get("tokens_out", 0) + self.tokens["out"]}

    def _record(self, **kw):
        self.steps_buf.append(json.dumps(dict(kw, t=round(time.time(), 2)), ensure_ascii=False))

    def _ask(self, messages, tools=None):
        r = self.client.complete(messages, tools)
        self.usd += r.usd
        for k in self.tokens:
            self.tokens[k] += r.usage.get(k, 0)
        return r

    # -- run ----------------------------------------------------------------------------------------
    def run(self) -> dict:
        cap = float(self.spec.get("budget", {}).get("monthly_usd", 0) or 0)
        if cap:
            ledger.check(self.store, self.provider, cap, reserve=float(self.ep.get("max_usd", 0)))
        arch = archive.get(self.store, self.arm)
        if self.start_node_id:
            start_key = None
            nid = self.start_node_id
        else:
            start_key = archive.select(arch, self.rng)
            if start_key is None:
                raise RuntimeError(f"archive {self.arm} is empty: run `fresh` first")
            nid = arch["cells"][start_key]["node"]
            archive.mark_chosen(self.store, self.arm, start_key)
        self.start = nodes.load(self.store, self.arm, nid)
        self.workdir = os.path.join(self.work, "work")
        os.makedirs(self.work, exist_ok=True)
        self.server = new_server(self.spec, os.path.join(self.work, "server"))
        started = time.time()
        stop = "turns"
        try:
            tick0 = nodes.restore(self.store, self.start, self.server, self.workdir)
            st = nodes.read_state(self.server)
            self.last_levels = cells.cell(st["made"])
            self.last_snapshot_tick = tick0
            self.tick_budget_end = tick0 + int(self.ep.get("max_ticks", 216000))
            self.goal = cells.make_goal(self.last_levels, st["can"], int(self.ep.get("goal_dist", 3)),
                                        self.rng)
            accepted, drawn = facts.sample(self.store, self.rng, float(self.spec["prompt"].get("facts_p", 0.5)))
            system = load_prompt(self.spec["prompt"]["layers"])
            block = facts.prompt_block(accepted, drawn)
            if block:
                system += "\n\n" + block
            tools = PROFILES[self.spec["toolkit"]["profile"]]
            box = Toolbox(self.server, self.workdir, self)
            notes = ""
            if os.path.exists(os.path.join(self.workdir, "NOTES.md")):
                with open(os.path.join(self.workdir, "NOTES.md"), encoding="utf-8") as fh:
                    notes = fh.read()
            first = (f"You continue a game at tick {tick0} ({tick0 / 216000:.2f} game hours in). "
                     f"Research done: {st['researched']} technologies.\n\n"
                     f"GOAL for this episode: produce, in total since the game began, at least: "
                     f"{goal_text(self.goal, st['made'])}.\n"
                     f"You have up to {self.tick_budget_end - tick0} game ticks and "
                     f"{self.ep.get('max_turns', 60)} turns. Fewer game ticks is better.\n\n"
                     f"Your work dir holds the notes the previous player left (NOTES.md):\n\n{notes}")
            messages = [{"role": "system", "content": system}, {"role": "user", "content": first}]
            self._record(role="start", node=self.start.id, tick=tick0, goal=self.goal,
                         facts=[f["id"] for f in drawn])
            self.log(f"episode {self.id}: from {self.start.id} (tick {tick0}), goal {self.goal}")
            wrap = False
            max_turns = int(self.ep.get("max_turns", 60))
            while True:
                if box.finished is not None:
                    stop = "finished"
                    break
                if self.turns >= max_turns + (3 if wrap else 0):
                    stop = "turns"
                    break
                if self.usd >= float(self.ep.get("max_usd", 1e9)):
                    stop = "usd"
                    break
                if self.turns == max_turns and not wrap:
                    wrap = True
                    messages.append({"role": "user", "content": "Out of turns: bring NOTES.md up to "
                                     "date for whoever continues from here, then finish."})
                r = self._ask(messages, tools)
                self.turns += 1
                messages.append(r.message)
                calls = r.message.get("tool_calls") or []
                self._record(role="assistant", turn=self.turns, content=r.message.get("content"),
                             calls=[(c["function"]["name"], c["function"]["arguments"]) for c in calls],
                             usage=r.usage, usd=r.usd)
                if not calls:
                    messages.append({"role": "user", "content": "Use the tools to act; call finish to end."})
                    continue
                for c in calls:
                    try:
                        args = json.loads(c["function"]["arguments"] or "{}")
                    except ValueError:
                        args = {}
                    out = box.call(c["function"]["name"], args if isinstance(args, dict) else {})
                    messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})
                    self._record(role="tool", turn=self.turns, name=c["function"]["name"], out=out[:4000])
            # the knowledge step: facts for the bank
            messages.append({"role": "user", "content": facts.KNOWLEDGE_PROMPT})
            r = self._ask(messages)
            cands = facts.parse_candidates(r.message.get("content", ""))
            self._record(role="knowledge", content=r.message.get("content"), usd=r.usd)
            st = nodes.read_state(self.server)
            if cells.cell(st["made"]) != self.last_levels or not self.offered:
                self._snapshot()
            result = {
                "id": self.id, "arm": self.arm, "start_node": self.start.id, "start_key": start_key,
                "start_tick": tick0, "end_tick": st["tick"], "goal": self.goal,
                "reached_tick": self.reached_tick, "stop": stop, "turns": self.turns,
                "usd": round(self.usd, 6), "tokens": self.tokens, "offered": self.offered,
                "refused_lua": box.refused, "facts_included": [f["id"] for f in drawn],
                "facts_accepted": [f["id"] for f in accepted], "candidates": len(cands),
                "model": self.spec["agent"].get("model"), "profile": self.spec["toolkit"]["profile"],
                "layers": self.spec["prompt"]["layers"], "started": started, "ended": time.time(),
                "summary": box.finished}
            if cands:
                self.store.put_json(f"facts/candidates/{self.arm}/{self.id}.json",
                                    {"episode": self.id, "facts": cands})
            return result
        finally:
            self.server.stop()
            try:
                self.store.put(f"steps/{self.arm}/{self.id}.jsonl", ("\n".join(self.steps_buf) + "\n").encode())
                if "result" in locals():
                    self.store.put_json(f"episodes/{self.arm}/{self.id}.json", result)
                if self.usd:
                    ledger.add(self.store, self.provider, self.usd)
            finally:
                if not self.spec.get("_keep_work"):
                    shutil.rmtree(self.work, ignore_errors=True)
