"""Sessions and episodes.

A session is one agent conversation. It plays up to `session.episodes` episodes in a row, so the
agent doesn't start from nothing each time (and episode 2 vs 1 of a session can be compared). An
episode is a goal cell from a start state; it ends when a legit state meets the goal, at the
episode's budget, or when the agent calls finish. Then the knowledge step, then the next episode:
from a node the archive picks (`session.next = "select"`) or from where the agent stands
(`"continue"`).

Time is decoupled: the game only moves in `evaluate`, and the agent may save and load states.
The run is legit while every change since the last legit state went through the game; the
referee doesn't block anything, it saves `last-valid-N` before a flagged command and marks the
run as not counting until a legit state is loaded. Only legit states become archive nodes or
count as reaching a goal.

Writes to the store:
- `nodes/<arm>/<id>.json` + blobs for every snapshot, offered to `archives/<arm>.json`;
- `episodes/<arm>/<id>.json` per episode, `steps/<arm>/<session>.jsonl` per session;
- `facts/candidates/<arm>/<episode>.json`; the month's spend in `ledger/`.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import time
import uuid

from . import archive, cell as cells, facts, kit, ledger, maps, node as nodes
from .agent import llm as llms
from .agent.tools import PROFILES, Toolbox, goal_text, referee
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


def _uid() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


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


# -- the session ---------------------------------------------------------------------------------
class Session:
    def __init__(self, store, spec: dict, work: str, client=None, seed: int | None = None,
                 start_node: str | None = None, log=print):
        self.store = store
        self.spec = spec
        self.arm = spec["arm"]
        self.ep = spec["episode"]
        self.ses = spec.get("session", {})
        self.id = _uid()
        self.work = os.path.join(work, self.id)
        self.workdir = os.path.join(self.work, "work")
        self.rng = random.Random(seed if seed is not None else uuid.uuid4().int)
        self.client = client or llms.from_spec(spec["agent"])
        self.provider = spec["agent"].get("provider_name", spec["agent"].get("model", "model"))
        self.first_node = start_node
        self.log = log
        self.usd = 0.0
        self.tokens = {"in_hit": 0, "in_miss": 0, "out": 0}
        self.turns = 0
        self.steps: list[str] = []
        self.results: list[dict] = []
        self.states: dict[str, dict] = {}    # name -> {tick, valid, last_valid, file}
        self.valid = True
        self.last_valid: str | None = None
        self.n_invalid = 0
        self.prompt_tokens = 0
        self.ep_state: dict = {}
        self.series: list[dict] = []        # in-game stats over the session, for the status page
        self.started = time.time()
        self.last_call = ""
        self._pushed = 0.0
        self._mapped = 0.0

    # -- what the tools call ---------------------------------------------------------------------
    def status_tag(self) -> str:
        t = self.server.tick()
        if self.valid:
            return f"[legit run - counting towards the golden run - tick {t}]"
        lv = self.states.get(self.last_valid, {})
        return (f"[testing/planning - does not count - last valid state {self.last_valid} "
                f"at tick {lv.get('tick', '?')}]")

    def lua(self, code: str) -> str:
        why = referee(code)
        if why and self.valid:
            self.n_invalid += 1
            name = f"last-valid-{self.n_invalid}"
            self._save(name, valid=True)
            self.last_valid = name
            self.valid = False
            self._record(role="invalid", why=why, state=name)
            out = self.server.lua(code)
            return (f"{out}\n(note: {why}. The run doesn't count from here; state {name} is "
                    f"the legit game from just before. load_state {name} to count again.)")
        return self.server.lua(code)

    def _save(self, name: str, valid: bool) -> dict:
        path = self.server.save(f"st-{name}")
        st = {"tick": self.server.tick(), "valid": valid,
              "last_valid": None if valid else self.last_valid, "file": path}
        self.states[name] = st
        return st

    def save_state(self, name: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", name) or name.startswith("last-valid-"):
            return "error: a state name is 1-40 letters, digits, _ . - (last-valid-N is the referee's)"
        st = self._save(name, self.valid)
        return f"saved state {name} at tick {st['tick']} ({'legit' if st['valid'] else 'testing'})"

    def load_state(self, name: str) -> str:
        st = self.states.get(name)
        if st is None:
            return f"error: no state {name}; list_states shows them"
        tick = self.server.load(st["file"])
        self.valid = st["valid"]
        self.last_valid = st["last_valid"]
        made = nodes.read_state(self.server)["made"]
        self.ep_state["levels"] = cells.cell(made)
        self._record(role="load", state=name, tick=tick, valid=self.valid)
        return f"loaded state {name}: tick {tick}; goal: {goal_text(self.goal, made)}"

    def list_states(self) -> str:
        rows = [f"{n}: tick {s['tick']}, {'legit' if s['valid'] else 'testing'}"
                for n, s in sorted(self.states.items(), key=lambda kv: kv[1]["tick"])]
        return "\n".join(rows) or "(none)"

    def evaluate(self, ticks: int) -> str:
        cap = int(self.ep.get("eval_cap", 36000))
        ticks = max(1, min(ticks, cap))
        end = self.ep_state["tick_end"]
        now = self.server.tick()
        if self.valid and now >= end:
            return (f"this episode's game time is used up (tick {end}); finish, or load an earlier "
                    "state to try another way")
        if self.valid:
            ticks = min(ticks, end - now)
        chunk = int(self.ep.get("check_every", 600))
        done = 0
        while done < ticks:
            step = min(chunk, ticks - done)
            self.server.run(step)
            done += step
            st = nodes.read_state(self.server)
            self._sample(st)
            levels = cells.cell(st["made"])
            if self.valid:
                if self.ep_state["reached"] is None and cells.reached(levels, self.goal):
                    self.ep_state["reached"] = st["tick"]
                if (levels != self.ep_state["levels"] and st["tick"] - self.ep_state["snap_tick"]
                        >= int(self.ep.get("snapshot_every", 7200))):
                    self._snapshot()
            self.ep_state["levels"] = levels
            if self.valid and self.ep_state["reached"] is not None:
                break
        st = nodes.read_state(self.server)
        cut = "" if done == ticks else f" (stopped after {done} of {ticks}: goal met)"
        line = f"tick {st['tick']}{cut}; goal: {goal_text(self.goal, st['made'])}"
        if self.valid and self.ep_state["reached"] is not None:
            line += "\nGOAL MET. Bring NOTES.md up to date for whoever continues from here, then finish."
        return line

    # -- bookkeeping -----------------------------------------------------------------------------
    def _ledger(self) -> dict:
        base = self.ep_state["start"].ledger
        return {"usd": round(base.get("usd", 0.0) + self.usd - self.ep_state["usd0"], 6),
                "turns": base.get("turns", 0) + self.turns - self.ep_state["turns0"],
                "tokens_in": base.get("tokens_in", 0) + self.tokens["in_hit"] + self.tokens["in_miss"],
                "tokens_out": base.get("tokens_out", 0) + self.tokens["out"]}

    def _snapshot(self):
        node = nodes.snapshot(self.store, self.server, self.workdir, self.arm,
                              self.ep_state["start"].id, self.ep_state["id"], self._ledger())
        outcome = archive.offer(self.store, node)
        self.ep_state["offered"].append({"node": node.id, "tick": node.tick,
                                         "height": cells.progress(node.levels), "outcome": outcome})
        self.ep_state["snap_tick"] = node.tick
        self.log(f"  node {node.id} at tick {node.tick}: {outcome}")

    def _record(self, **kw):
        kw.setdefault("episode", self.ep_state.get("id"))
        self.steps.append(json.dumps(dict(kw, t=round(time.time(), 2)), ensure_ascii=False))

    def _sample(self, st: dict, force: bool = False):
        """One point of the session's in-game stats (at most every 1800 ticks unless forced)."""
        last = self.series[-1] if self.series else None
        if not force and last and last["valid"] == self.valid and 0 <= st["tick"] - last["tick"] < 1800:
            return
        self.series.append({"t": round(time.time(), 1), "tick": st["tick"], "valid": self.valid,
                            "ep": self.ep_state.get("index", 0), "researched": st["researched"],
                            "made": {k: int(v) for k, v in st["made"].items() if v}})

    def _push(self, state: str = "running", force: bool = False):
        """The live status and stats for the status page (throttled to one write per 20 s)."""
        now = time.time()
        if not force and now - self._pushed < 20:
            return
        self._pushed = now
        es = self.ep_state
        try:
            tick = self.server.tick() if state == "running" else (self.series[-1]["tick"] if self.series else 0)
        except Exception:
            tick = self.series[-1]["tick"] if self.series else 0
        status = {
            "session": self.id, "arm": self.arm, "model": self.spec["agent"].get("model"),
            "profile": self.spec["toolkit"]["profile"], "state": state, "updated": now,
            "started": self.started, "tick": tick, "valid": self.valid, "last_valid": self.last_valid,
            "turns": self.turns, "usd": round(self.usd, 5), "tokens": self.tokens,
            "last_call": self.last_call[:300], "facts": len(getattr(self, "facts_drawn", [])),
            "episode": {"index": es.get("index"), "id": es.get("id"), "goal": getattr(self, "goal", {}),
                        "start_tick": es.get("tick0"), "reached_tick": es.get("reached")} if es else None,
            "episodes_done": [{k: r.get(k) for k in ("index", "id", "reached_tick", "stop", "turns", "usd",
                                                     "start_tick", "end_tick", "end_valid", "invalid_marks")}
                              for r in self.results]}
        try:
            self.store.put_json(f"status/{self.arm}/{self.id}.json", status)
            self.store.put_json(f"stats/{self.arm}/{self.id}.json", {"session": self.id, "arm": self.arm,
                                                                      "samples": self.series})
        except Exception as e:  # the page is a nicety; a store hiccup must not end the session
            self.log(f"  status push failed: {e}")
        self._map(force=force and state == "running")

    def _map(self, force: bool = False):
        """Maps of every surface for the status page, at most every `session.map_every_s` (120 s)."""
        every = float(self.ses.get("map_every_s", 120))
        now = time.time()
        if every <= 0 or (not force and now - self._mapped < every):
            return
        self._mapped = now
        try:
            maps.capture(self.store, self.server, self.arm, self.id, self.spec["_paths"].get("map_gen"),
                         os.path.join(self.work, "maps"))
        except Exception as e:  # a map is a nicety too
            self.log(f"  map capture failed: {e}")

    def _ask(self, messages, tools=None):
        r = self.client.complete(messages, tools)
        self.usd += r.usd
        for k in self.tokens:
            self.tokens[k] += r.usage.get(k, 0)
        self.prompt_tokens = r.usage.get("in_hit", 0) + r.usage.get("in_miss", 0)
        return r

    def _pick_start(self, index: int) -> tuple[nodes.Node, str | None]:
        if index == 0 and self.first_node:
            return nodes.load(self.store, self.arm, self.first_node), None
        arch = archive.get(self.store, self.arm)
        k = archive.select(arch, self.rng)
        if k is None:
            raise RuntimeError(f"archive {self.arm} is empty: run `fresh` first")
        archive.mark_chosen(self.store, self.arm, k)
        return nodes.load(self.store, self.arm, arch["cells"][k]["node"]), k

    # -- run -------------------------------------------------------------------------------------
    def run(self) -> list[dict]:
        cap = float(self.spec.get("budget", {}).get("hard_usd", 0) or 0)
        if cap:
            ledger.check(self.store, self.provider, cap, reserve=float(self.ses.get("max_usd", self.ep.get("max_usd", 0))))
        os.makedirs(self.work, exist_ok=True)
        self.server = new_server(self.spec, os.path.join(self.work, "server"))
        accepted, drawn = facts.sample(self.store, self.rng, float(self.spec["prompt"].get("facts_p", 0.5)),
                                       facts.scopes_for(self.spec))
        system = load_prompt(self.spec["prompt"]["layers"])
        block = facts.prompt_block(accepted, drawn)
        if block:
            system += "\n\n" + block
        self.messages = [{"role": "system", "content": system}]
        self.facts_drawn = [f["id"] for f in drawn]
        self.facts_accepted = [f["id"] for f in accepted]
        self.box = Toolbox(self.server, self.workdir, self)
        self._record(role="session", arm=self.arm, facts=self.facts_drawn, model=self.spec["agent"].get("model"))
        try:
            n_eps = int(self.ses.get("episodes", 1))
            mode = self.ses.get("next", "select")
            for i in range(n_eps):
                if i > 0 and mode == "continue" and self.valid:
                    start, start_key, moved = self._continue_node(), None, False
                else:
                    start, start_key = self._pick_start(i)
                    moved = True
                res = self._episode(i, start, start_key, moved)
                self.results.append(res)
                self.store.put_json(f"episodes/{self.arm}/{res['id']}.json", res)
                if res["stop"] in ("context", "session_usd"):
                    break
                if self.usd >= float(self.ses.get("max_usd", 1e9)):
                    break
            final = "finished"
            return self.results
        except BaseException:
            final = "failed"
            raise
        finally:
            try:
                self._sample(nodes.read_state(self.server), force=True)
            except Exception:
                pass
            self._push(final, force=True)
            self._map(force=True)
            self.server.stop()
            try:
                self.store.put(f"steps/{self.arm}/{self.id}.jsonl", ("\n".join(self.steps) + "\n").encode())
                if self.usd:
                    ledger.add(self.store, self.provider, self.usd)
                out = self.spec.get("_out")
                if out:
                    os.makedirs(out, exist_ok=True)
                    with open(os.path.join(out, f"{self.id}.steps.jsonl"), "w") as fh:
                        fh.write("\n".join(self.steps) + "\n")
                    with open(os.path.join(out, f"{self.id}.results.json"), "w") as fh:
                        json.dump(self.results, fh, indent=1)
            finally:
                if not self.spec.get("_keep_work"):
                    shutil.rmtree(self.work, ignore_errors=True)

    def _continue_node(self) -> nodes.Node:
        """Freeze where the agent stands (legit) as the next episode's start."""
        node = nodes.snapshot(self.store, self.server, self.workdir, self.arm,
                              self.ep_state["start"].id, self.ep_state["id"], self._ledger())
        archive.offer(self.store, node)
        return node

    def _episode(self, index: int, start: nodes.Node, start_key: str | None, moved: bool) -> dict:
        eid = _uid()
        if moved:
            if os.path.isdir(self.workdir):
                shutil.rmtree(self.workdir)
            tick0 = nodes.restore(self.store, start, self.server, self.workdir)
            self.valid, self.last_valid = True, None
        else:
            tick0 = self.server.tick()
        st = nodes.read_state(self.server)
        self.goal = cells.make_goal(cells.cell(st["made"]), st["can"], int(self.ep.get("goal_dist", 3)), self.rng)
        self.ep_state = {"id": eid, "start": start, "tick_end": tick0 + int(self.ep.get("max_ticks", 216000)),
                         "levels": cells.cell(st["made"]), "snap_tick": tick0, "reached": None,
                         "offered": [], "usd0": self.usd, "turns0": self.turns, "invalid0": self.n_invalid,
                         "index": index, "tick0": tick0}
        self._sample(st, force=True)
        self.states = {k: v for k, v in self.states.items() if not moved}
        self._save("start", valid=True)
        self.box.finished = None
        notes = ""
        if os.path.exists(os.path.join(self.workdir, "NOTES.md")):
            with open(os.path.join(self.workdir, "NOTES.md"), encoding="utf-8") as fh:
                notes = fh.read()
        head = (f"EPISODE {index + 1}. " + ("" if index == 0 else
                ("You are now in a different saved game. " if moved else "Continue from where you are. ")))
        msg = (f"{head}The game is at tick {tick0} ({tick0 / 216000:.2f} game hours in); "
               f"research done: {st['researched']} technologies.\n\n"
               f"GOAL: produce, in total since the game began, at least: {goal_text(self.goal, st['made'])}.\n"
               f"A legit run has up to {self.ep_state['tick_end'] - tick0} game ticks for it, and you "
               f"have {self.ep.get('max_turns', 60)} turns. Fewer game ticks is better. State 'start' "
               f"is saved.\n")
        if moved:
            msg += f"\nThe notes left in this game's work dir (NOTES.md):\n\n{notes}"
        self.messages.append({"role": "user", "content": msg})
        self._record(role="episode", index=index, node=start.id, tick=tick0, goal=self.goal, moved=moved)
        self.log(f"session {self.id} episode {index + 1}: from {start.id} (tick {tick0}), goal {self.goal}")
        tools = PROFILES[self.spec["toolkit"]["profile"]]
        max_turns = int(self.ep.get("max_turns", 60))
        turns = 0
        wrap = False
        stop = "turns"
        while True:
            if self.box.finished is not None:
                stop = "finished"
                break
            if turns >= max_turns + (3 if wrap else 0):
                break
            if self.usd - self.ep_state["usd0"] >= float(self.ep.get("max_usd", 1e9)):
                stop = "usd"
                break
            if self.usd >= float(self.ses.get("max_usd", 1e9)):
                stop = "session_usd"
                break
            if self.prompt_tokens > int(self.ses.get("context_limit", 120000)):
                stop = "context"
                break
            if turns == max_turns and not wrap:
                wrap = True
                self.messages.append({"role": "user", "content": "Out of turns for this episode: bring "
                                      "NOTES.md up to date for whoever continues from here, then finish."})
            r = self._ask(self.messages, tools)
            turns += 1
            self.turns += 1
            self.messages.append(r.message)
            calls = r.message.get("tool_calls") or []
            self._record(role="assistant", turn=self.turns, content=r.message.get("content"),
                         calls=[(c["function"]["name"], c["function"]["arguments"]) for c in calls],
                         usage=r.usage, usd=r.usd, finish=r.finish, retried=r.retried)
            if not calls:
                if r.finish == "length" and not (r.message.get("content") or "").strip():
                    nudge = ("Your reply ran out of output tokens while thinking, before you called a tool. "
                             "Take a smaller step: decide one thing and call one tool now.")
                else:
                    nudge = "Use the tools to act; call finish to end the episode."
                self.messages.append({"role": "user", "content": nudge})
                continue
            for c in calls:
                try:
                    args = json.loads(c["function"]["arguments"] or "{}")
                except ValueError:
                    args = {}
                out = self.box.call(c["function"]["name"], args if isinstance(args, dict) else {})
                self.messages.append({"role": "tool", "tool_call_id": c["id"], "content": out})
                self._record(role="tool", turn=self.turns, name=c["function"]["name"], out=out[:4000])
                self.last_call = f"{c['function']['name']} {c['function']['arguments'] or ''}"
                self._push()
        self._map(force=True)
        # the knowledge step
        self.messages.append({"role": "user", "content": facts.KNOWLEDGE_PROMPT})
        r = self._ask(self.messages)
        self.messages.append({"role": "assistant", "content": r.message.get("content") or ""})
        cands = facts.parse_candidates(r.message.get("content", ""))
        self._record(role="knowledge", content=r.message.get("content"), usd=r.usd)
        if cands:
            self.store.put_json(f"facts/candidates/{self.arm}/{eid}.json", {"episode": eid, "facts": cands})
        end = nodes.read_state(self.server)
        if self.valid and (cells.cell(end["made"]) != cells.cell(st["made"])) and not self.ep_state["offered"]:
            self._snapshot()
        return {
            "id": eid, "session": self.id, "index": index, "arm": self.arm, "start_node": start.id,
            "start_key": start_key, "moved": moved, "start_tick": tick0, "end_tick": end["tick"],
            "end_valid": self.valid, "goal": self.goal, "reached_tick": self.ep_state["reached"],
            "stop": stop, "turns": turns, "usd": round(self.usd - self.ep_state["usd0"], 6),
            "offered": self.ep_state["offered"], "invalid_marks": self.n_invalid - self.ep_state["invalid0"],
            "states_saved": len(self.states), "facts_included": self.facts_drawn,
            "facts_accepted": self.facts_accepted, "candidates": len(cands),
            "model": self.spec["agent"].get("model"), "profile": self.spec["toolkit"]["profile"],
            "layers": self.spec["prompt"]["layers"], "ended": time.time(), "summary": self.box.finished}


#: kept for callers of the single-episode API: a session of the arm's configured length
Episode = Session
