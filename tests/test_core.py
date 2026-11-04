import json
import os
import random
import tempfile
import threading
import unittest

from runner import archive, cell, facts, ledger
from runner.agent.tools import referee
from runner.node import Node, pack_dir, unpack_dir
from runner.store import LocalStore


class CellTest(unittest.TestCase):
    def test_cell_and_goal(self):
        lv = cell.cell({"iron-plate": 1024, "copper-plate": 3, "never": 0})
        self.assertEqual(lv, {"iron-plate": 10, "copper-plate": 1})
        self.assertEqual(cell.levels_of(cell.key(lv)), lv)
        goal = cell.make_goal(lv, ["iron-gear-wheel"], 3, random.Random(1))
        self.assertTrue(goal)
        self.assertFalse(cell.reached(lv, goal))
        self.assertEqual(cell.count_for(10), 1024)


class StoreTest(unittest.TestCase):
    def test_blobs_and_cas(self):
        with tempfile.TemporaryDirectory() as d:
            s = LocalStore(d)
            sha = s.put_blob(b"abc")
            self.assertEqual(s.get_blob(sha), b"abc")
            n = 40

            def bump():
                s.update_json("c.json", lambda o: {"n": (o or {"n": 0})["n"] + 1})

            ts = [threading.Thread(target=bump) for _ in range(n)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            self.assertEqual(s.get_json("c.json")["n"], n)

    def test_key_escape(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                LocalStore(d).put("../x", b"")


class ArchiveTest(unittest.TestCase):
    def node(self, nid, levels, tick, usd=0.0, flags=()):
        return Node(id=nid, arm="a", parent=None, episode=None, tick=tick, levels=levels,
                    save="s", work="w", ledger={"usd": usd}, flags=list(flags))

    def test_offer_and_select(self):
        with tempfile.TemporaryDirectory() as d:
            s = LocalStore(d)
            self.assertEqual(archive.offer(s, self.node("n1", {"x": 1}, 100)), "new")
            self.assertEqual(archive.offer(s, self.node("n2", {"x": 1}, 200)), "known")
            self.assertEqual(archive.offer(s, self.node("n3", {"x": 1}, 50)), "faster")
            self.assertEqual(archive.offer(s, self.node("n4", {"x": 2}, 60, flags=["cheat"])), "flagged")
            arch = archive.get(s, "a")
            self.assertEqual(arch["cells"]["x=1"]["node"], "n3")
            self.assertEqual(archive.select(arch, random.Random(0)), "x=1")


class RefereeTest(unittest.TestCase):
    def test_rules(self):
        ok = ["rcon.print(game.tick)",
              "game.surfaces[1].create_entity{name = 'entity-ghost', inner_name = 'inserter', position = {1, 1}, force = 'player'}",
              "game.forces.player.add_research('automation')",
              "local t = {} table.insert(t, 1) rcon.print(#t)",
              "for n, t in pairs(game.forces.player.technologies) do if t.researched == false then end end",
              "rcon.print(game.speed)", "if e.amount == 0 then end",
              # DeepSeek's real read-only queries that an earlier referee flagged
              "local out = {} out.researched = {} for name, t in pairs(game.forces.player.technologies) do "
              "if t.researched then table.insert(out.researched, name) end end",
              "local out = {} local e = game.surfaces[1].find_entity('roboport', {8, 8}) out.energy = e.energy "
              "out.health = e.health rcon.print(helpers.table_to_json(out))"]
        bad = ["chest.insert{name = 'iron-plate', count = 5}",
               "game.surfaces[1].create_entity{name = 'inserter', position = {1, 1}, force = 'player'}",
               "for _, g in pairs(gs) do g.revive() end",
               "game.forces.player.technologies['automation'].researched = true",
               "game.speed = 10", "e.destroy()",
               "game.forces.player.recipes['assembling-machine-2'].enabled = true",
               "game.surfaces[1].find_entities_filtered{type = 'resource'}[1].amount = 1000000",
               "e.energy = 5e6", "game.tick_paused = false"]
        for c in ok:
            self.assertIsNone(referee(c), c)
        for c in bad:
            self.assertIsNotNone(referee(c), c)


class MiscTest(unittest.TestCase):
    def test_pack_roundtrip(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            os.makedirs(os.path.join(a, "game"))
            with open(os.path.join(a, "game", "x.fk"), "w") as fh:
                fh.write("hi")
            data = pack_dir(a)
            self.assertEqual(data, pack_dir(a))   # deterministic
            unpack_dir(data, b)
            with open(os.path.join(b, "game", "x.fk")) as fh:
                self.assertEqual(fh.read(), "hi")

    def test_facts_parse(self):
        got = facts.parse_candidates('Sure:\n[{"text": "Belts beat chests", "kind": "process"}, {"x": 1}]')
        self.assertEqual([f["text"] for f in got], ["Belts beat chests"])
        self.assertEqual(facts.parse_candidates("no json"), [])

    def test_facts_import_and_scopes(self):
        with tempfile.TemporaryDirectory() as d:
            s = LocalStore(d)
            seed = {"facts": [{"id": "a", "text": "x", "scope": "any", "status": "testing"},
                              {"id": "b", "text": "y", "scope": "fk", "status": "testing"}]}
            self.assertEqual(facts.import_seed(s, seed), {"added": 2, "total": 2})
            self.assertEqual(facts.import_seed(s, seed)["added"], 0)
            spec = {"toolkit": {"profile": "bare"}, "game": {"biters": False}}
            _, drawn = facts.sample(s, random.Random(0), 1.0, facts.scopes_for(spec))
            self.assertEqual([f["id"] for f in drawn], ["a"])

    def test_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            s = LocalStore(d)
            ledger.add(s, "deepseek", 4.0)
            ledger.check(s, "deepseek", 10.0)
            with self.assertRaises(ledger.OverBudget):
                ledger.check(s, "deepseek", 10.0, reserve=7.0)


if __name__ == "__main__":
    unittest.main()


class StatusTest(unittest.TestCase):
    def test_collect(self):
        from runner import status
        with tempfile.TemporaryDirectory() as d:
            s = LocalStore(d)
            s.put_json("archives/a.json", {"arm": "a", "episodes": 2, "cells": {
                "x=1": {"node": "n1", "tick": 10, "usd": 0.1, "height": 2},
                "x=2": {"node": "n2", "tick": 20, "usd": 0.05, "height": 3}}})
            s.put_json("status/a/s1.json", {"session": "s1", "arm": "a", "state": "running", "updated": 0})
            s.put_json("stats/a/s1.json", {"samples": [{"tick": 1, "valid": True, "made": {"iron-plate": 3}}]})
            d2 = status.collect(s)
            self.assertEqual(list(d2["arms"]), ["a"])
            self.assertEqual(d2["arms"]["a"]["frontier"][-1]["height"], 3)
            self.assertEqual(d2["sessions"][0]["state"], "stalled")   # old update -> stalled
            self.assertEqual(d2["stats"]["s1"][0]["made"], {"iron-plate": 3})
            site = os.path.join(d, "site")
            status.write_site(s, site)
            self.assertTrue(os.path.exists(os.path.join(site, "index.html")))


class ReadOnlyTest(unittest.TestCase):
    def test_show_does_not_write(self):
        """show must work with a read-only store (computah's role), so it writes nothing."""
        import io
        import contextlib
        from runner import __main__ as cli
        from runner.store import LocalStore

        class ReadOnly(LocalStore):
            def put(self, key, data):
                raise PermissionError(f"read-only: {key}")

            def put_if(self, key, data, version):
                raise PermissionError(f"read-only: {key}")

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with tempfile.TemporaryDirectory() as d:
            ro = ReadOnly(d)
            orig = cli.open_store
            cli.open_store = lambda url: ro
            try:
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    rc = cli.main(["show", os.path.join(root, "arms", "test-scripted.toml"), "--store", d])
            finally:
                cli.open_store = orig
            self.assertEqual(rc, 0)
            self.assertIn("test-scripted: 0 cells", out.getvalue())


class MapsTest(unittest.TestCase):
    def test_parse_pack_window(self):
        from runner import maps
        rows = maps._parse("roboport|roboport|8.0|8.0|3.9|3.9|0|player\n"
                           "entity-ghost|bad\n"
                           "assembling-machine|assembling-machine-1|14.5|4.5|2.8|2.8|1|player\n"
                           "unit-spawner|biter-spawner|300.0|-200.0|4.8|4.8|0|enemy")
        self.assertEqual(len(rows), 3)
        packed = maps._pack(rows)
        self.assertEqual(set(packed["types"]), {"roboport", "ghost:assembling-machine", "enemy:unit-spawner"})
        self.assertEqual(packed["types"]["roboport"][:4], [16, 16, 8, 8])     # half tiles
        cx, cy, size = maps._window([r for r in rows if r[7] != "enemy"])
        self.assertEqual((cx, cy, size), (0, 0, 256))
        cx, cy, size = maps._window(rows)                                      # spans 300 m -> 512
        self.assertEqual(size, 512)
        self.assertEqual(cx % 64, 0)


class LengthRetryTest(unittest.TestCase):
    def test_cut_off_turn_is_retried_and_paid_for(self):
        """A reasoning model that spends max_tokens thinking returns nothing (finish 'length');
        the client asks again with 4x the limit and adds both calls' usage."""
        from runner.agent.llm import OpenAICompat
        calls = []

        class Fake(OpenAICompat):
            def _post(self, url, body):
                calls.append(body["max_tokens"])
                if body["max_tokens"] < 64:
                    return {"choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": ""}}],
                            "usage": {"prompt_tokens": 100, "completion_tokens": body["max_tokens"],
                                      "completion_tokens_details": {"reasoning_tokens": body["max_tokens"]}}}
                return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": "",
                        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "evaluate", "arguments": "{}"}}]}}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 40}}

        c = Fake("http://x", "m", max_tokens=16, max_tokens_ceiling=256, price={"in_miss": 1e6, "out": 1e6})
        r = c.complete([{"role": "user", "content": "go"}])
        self.assertEqual(calls, [16, 64])
        self.assertEqual(r.retried, 1)
        self.assertEqual(r.finish, "tool_calls")
        self.assertEqual(r.usage["out"], 16 + 40)
        self.assertEqual(r.usage["reasoning"], 16)
        self.assertEqual(r.usd, 200 + 56)        # both calls paid for

    def test_gives_up_at_the_ceiling(self):
        from runner.agent.llm import OpenAICompat
        calls = []

        class Fake(OpenAICompat):
            def _post(self, url, body):
                calls.append(body["max_tokens"])
                return {"choices": [{"finish_reason": "length", "message": {"role": "assistant", "content": None}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": body["max_tokens"]}}

        r = Fake("http://x", "m", max_tokens=16, max_tokens_ceiling=100).complete([])
        self.assertEqual(calls, [16, 64, 100])
        self.assertEqual(r.finish, "length")


class MapsV2Test(unittest.TestCase):
    def test_status_recipe_cover(self):
        from runner import maps
        rows = maps._parse("roboport|roboport|8.0|8.0|3.9|3.9|0|player||low_power|25.0|55.0\n"
                           "electric-pole|medium-electric-pole|10.5|14.5|0.3|0.3|0|player|||3.5|\n"
                           "assembling-machine|assembling-machine-1|14.5|4.5|2.8|2.8|0|player|iron-gear-wheel|no_ingredients||\n"
                           "construction-robot|construction-robot|20.2|3.1|0.5|0.5|0|player||||")
        p = maps._pack(rows)
        self.assertEqual(p["stride"], 8)
        n = p["names"]
        am = p["types"]["assembling-machine"]
        self.assertEqual((n[am[4]], n[am[5]], n[am[6]]), ("assembling-machine-1", "iron-gear-wheel", "no_ingredients"))
        self.assertEqual(p["types"]["construction-robot"][5:8], [-1, -1, 0])
        d = maps._pack(maps._parse("wooden-chest|wooden-chest|1.5|1.5|0.7|0.7|0|player|||||1"))
        self.assertEqual(d["types"]["wooden-chest"][7], 1)   # marked for deconstruction
        self.assertEqual(p["cover"], {"roboport": [[8.0, 8.0, 25.0, 55.0]], "pole": [[10.5, 14.5, 3.5]]})
        # robots don't stretch the terrain window
        self.assertEqual(maps._window([r for r in rows if not r[0].endswith("-robot")])[2], 256)

    def test_palette_from_dump(self):
        from runner import maps
        raw = {"utility-constants": {"default": {"chart": {
                   "default_friendly_color": [0, 0.38, 0.57], "default_enemy_color": {"r": 1, "g": 0.1, "b": 0.1},
                   "default_friendly_color_by_type": {"transport-belt": [0.8, 0.63, 0.28], "ammo-turret": [202, 167, 24]}}}},
               "roboport": {"roboport": {"friendly_map_color": [0.9, 0.9, 0.5]}},
               "tile": {"water": {"map_color": [0, 0, 1]}}}
        pal = maps.palette_from_dump(raw)
        self.assertEqual(pal["default_friendly_color"], [0, 97, 145])
        self.assertEqual(pal["default_enemy_color"], [255, 26, 26])
        self.assertEqual(pal["by_type"]["ammo-turret"], [202, 167, 24])      # already 0-255
        self.assertEqual(pal["by_name"], {"roboport": [230, 230, 128]})       # tiles left out


class TrimTest(unittest.TestCase):
    def test_old_tool_results_shortened_in_one_pass(self):
        from runner.episode import Session
        ses = object.__new__(Session)
        ses.ses = {"trim_at": 1000, "trim_keep": 2}
        ses.steps = []
        ses.ep_state = {}
        ses.messages = [{"role": "system", "content": "s"}]
        for i in range(5):
            ses.messages += [{"role": "assistant", "content": ""}, {"role": "tool", "tool_call_id": str(i), "content": "x" * 2000}]
        ses.prompt_tokens = 500
        self.assertEqual(ses._trim(), 0)                     # under the threshold: nothing
        ses.prompt_tokens = 5000
        self.assertEqual(ses._trim(), 3)                     # all but the last 2 tool results
        tools = [m["content"] for m in ses.messages if m["role"] == "tool"]
        self.assertTrue(all(t.startswith("[shortened") for t in tools[:3]))
        self.assertEqual(tools[3:], ["x" * 2000] * 2)
        self.assertEqual(ses.prompt_tokens, 0)
        ses.prompt_tokens = 5000
        self.assertEqual(ses._trim(), 0)                     # already shortened: no second change

    def test_long_call_arguments_and_episode_start(self):
        import json as _json
        from runner.episode import Session
        ses = object.__new__(Session)
        ses.ses = {"trim_at": 1000, "trim_keep": 1}
        ses.steps, ses.ep_state = [], {}
        ses.messages = [{"role": "system", "content": "s"}]
        for i in range(3):
            ses.messages += [{"role": "assistant", "content": "", "tool_calls": [{"id": str(i), "type": "function",
                              "function": {"name": "lua", "arguments": _json.dumps({"code": "x" * 900})}}]},
                             {"role": "tool", "tool_call_id": str(i), "content": "y" * 900}]
        ses.prompt_tokens = 10
        self.assertGreater(ses._trim(before=len(ses.messages)), 0)   # under trim_at, but a new episode
        args = [m["tool_calls"][0]["function"]["arguments"] for m in ses.messages if m.get("tool_calls")]
        self.assertTrue(all(_json.loads(a).get("shortened") for a in args))
        self.assertTrue(all(len(a) < 300 for a in args))


class ModSettingsTest(unittest.TestCase):
    def test_early_bots_settings_file_matches_its_source(self):
        import json
        from runner import modsettings
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        d = os.path.join(root, "game", "space-age-early", "mods")
        src = json.load(open(os.path.join(d, "mod-settings.json")))
        src = {k: v for k, v in src.items() if k in ("startup", "runtime-global", "runtime-per-user")}
        with open(os.path.join(d, "mod-settings.dat"), "rb") as fh:
            data = fh.read()
        self.assertEqual(data, modsettings.write(src))
        self.assertFalse(modsettings.read(data)[1]["runtime-global"]["fk-loot-chests"]["value"])

    def test_kits(self):
        from runner import kit
        self.assertEqual(kit.KITS["kickoff-early-bots"][0][0][0], "fk-early-roboport")   # roboport first
        self.assertNotIn("nuclear-reactor", dict(kit.KITS["kickoff-early-bots"][2]))
        warp = kit.KITS["warp-green-early-bots"]
        self.assertEqual(warp[0][0][0], "fk-early-roboport")
        self.assertTrue(warp[3]["logistic-science-pack"] and warp[3]["steam-power"])
        self.assertFalse(kit.RESEARCH.get("automation"))          # the baseline kit is untouched
        stacks = {"fk-early-roboport": 10, "electric-mining-drill": 50, "transport-belt": 100}
        self.assertLess(sum(-(-c // stacks.get(n, 50)) for n, c in warp[2]), 6 * 48)   # fits the chests
        self.assertEqual(kit.kit_json("warp-green-early-bots")["entities"][0]["name"], "fk-early-roboport")
