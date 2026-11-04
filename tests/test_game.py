"""The whole loop against a real headless server, with the scripted agent.
Runs when FACTORIO_BIN is set (CI downloads the server; locally point it at a binary)."""
import json
import os
import tempfile
import unittest

from runner import archive, episode, spec as specs
from runner.store import LocalStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUIRED = os.environ.get("REQUIRE_GAME") == "1"   # CI: a missing server is a failure, not a skip


@unittest.skipUnless(os.environ.get("FACTORIO_BIN") or REQUIRED, "FACTORIO_BIN not set")
class GameTest(unittest.TestCase):
    def test_stop_does_not_save(self):
        """Stopping must not write the running game into the file it was loaded from (a Factorio
        server does that on /quit, which overwrote restored nodes on 2026-11-04)."""
        from runner.server import Server
        with tempfile.TemporaryDirectory() as d:
            srv = Server(os.environ["FACTORIO_BIN"], os.path.join(d, "srv"))
            save = srv.create("t")
            t0 = srv.start(save)
            path = srv.save("base")
            srv.load(path)
            srv.run(600)
            srv.stop()
            self.assertEqual(srv.start(path), t0)
            srv.stop()

    def test_fresh_and_session(self):
        spec = specs.load(os.path.join(ROOT, "arms", "test-scripted.toml"))
        spec["agent"]["script"] = os.path.join(ROOT, spec["agent"]["script"])
        with tempfile.TemporaryDirectory() as d:
            store = LocalStore(os.path.join(d, "store"))
            root = episode.fresh(store, spec, os.path.join(d, "fresh"))
            kit = store.get_json(f"roots/{spec['arm']}.json")["kit"]
            self.assertIn("built 16/16", kit["bootstrap"])
            self.assertIn("short {}", kit["kit"])
            ses = episode.Session(store, spec, os.path.join(d, "work"), seed=1)
            results = ses.run()
            self.assertEqual(len(results), 2)                  # two episodes, one conversation
            first = results[0]
            self.assertEqual(first["stop"], "finished")
            self.assertEqual(first["invalid_marks"], 1)        # the cheat was marked, not blocked
            self.assertTrue(first["end_valid"])                # loading last-valid-1 made it count again
            self.assertGreater(first["end_tick"], root.tick)
            self.assertEqual([r["index"] for r in results], [0, 1])
            # both episodes restored the root: the first one's play must not leak into its save
            self.assertEqual(results[1]["start_tick"], root.tick)
            self.assertEqual({r["session"] for r in results}, {ses.id})
            steps = [json.loads(l) for l in store.get(f"steps/{spec['arm']}/{ses.id}.jsonl").decode().splitlines()]
            tools = [s for s in steps if s.get("role") == "tool"]
            cheat = next(s for s in tools if "inserted" in s["out"])
            self.assertIn("[testing/planning - does not count - last valid state last-valid-1", cheat["out"])
            loaded = next(s for s in tools if s["name"] == "load_state")
            self.assertIn("[legit run - counting towards the golden run", loaded["out"])
            built = [s["out"] for s in tools if s["name"] == "lua"][-1]
            self.assertTrue(built.startswith("1\n"), built)    # robots built the ghost
            arch = archive.get(store, spec["arm"])
            self.assertEqual(arch["episodes"], 2)
            # maps for the status page: nauvis with the kit on it, on a cached terrain preview
            idx = store.get_json(f"maps/{spec['arm']}/{ses.id}/index.json")
            nauvis = next(s for s in idx["surfaces"] if s["name"] == "nauvis")
            self.assertGreaterEqual(nauvis["count"], 16)
            self.assertTrue(store.get(nauvis["terrain"]).startswith(b"\x89PNG"))
            ents = store.get_json(nauvis["entities"])
            self.assertIn("roboport", ents["types"])
            self.assertEqual(ents["cover"]["roboport"][0][2:], [25.0, 55.0])   # the game's own radii
            pal = store.get_json(idx["palette"])                              # the game map's colours
            self.assertIn("transport-belt", pal["by_type"])


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(os.environ.get("FACTORIO_BIN") or REQUIRED, "FACTORIO_BIN not set")
class EarlyBotsTest(unittest.TestCase):
    def test_early_bots_start_builds_on_solar(self):
        """Rory's fk-early-bots kit: the solar block carries the low-power roboport, and its robots
        build ghosts from the first minute."""
        from runner.server import Server
        from runner import kit
        with tempfile.TemporaryDirectory() as d:
            srv = Server(os.environ["FACTORIO_BIN"], os.path.join(d, "srv"),
                         mods=os.path.join(ROOT, "game", "space-age-early", "mods"))
            with srv:
                save = srv.create("t", os.path.join(ROOT, "game", "space-age", "map-gen-settings.json"),
                                  os.path.join(ROOT, "game", "space-age", "map-settings.json"), 1)
                srv.start(save)
                rep = kit.apply(srv, "kickoff-early-bots")
                self.assertIn("built 16/16", rep["bootstrap"])
                self.assertIn("short {}", rep["kit"])
                self.assertEqual(srv.lua("rcon.print(tostring(settings.global['fk-loot-chests'].value))"), "false")
                srv.lua("game.surfaces[1].create_entity{name = 'entity-ghost', inner_name = 'assembling-machine-1', "
                        "position = {30.5, 8.5}, force = 'player'}")
                srv.run(1800)
                got = srv.lua("local s = game.surfaces[1] local n = s.find_entity('fk-early-roboport', {8, 8}).logistic_network "
                              "rcon.print(s.count_entities_filtered{name = 'assembling-machine-1'} .. ' ' .. n.all_construction_robots)")
                self.assertEqual(got, "1 50")
                # 10x the 0.4 recipes (mod-settings fk-early-bots-cost = 10), each robot scaled once
                costs = srv.lua("local o = {} for _, n in pairs({'fk-early-construction-robot', 'fk-early-logistic-robot', "
                                "'fk-early-roboport'}) do o[#o + 1] = prototypes.recipe[n].ingredients[1].amount end "
                                "rcon.print(table.concat(o, ' '))")
                self.assertEqual(costs, "80 80 300")


@unittest.skipUnless(os.environ.get("FACTORIO_BIN") or REQUIRED, "FACTORIO_BIN not set")
class ToolkitTest(unittest.TestCase):
    def test_fk_profile(self):
        """The toolkit profile on the early-bots start: the codebase seeded at fresh (kit.json,
        terrain), fk commands against the episode's server, fk lua through the referee with its
        quotes intact, save states that roll the codebase back, a build, and the world matching
        the design."""
        spec = specs.load(os.path.join(ROOT, "arms", "test-fk-scripted.toml"))
        spec["agent"]["script"] = os.path.join(ROOT, spec["agent"]["script"])
        with tempfile.TemporaryDirectory() as d:
            store = LocalStore(os.path.join(d, "store"))
            episode.fresh(store, spec, os.path.join(d, "fresh"))
            kit = store.get_json(f"roots/{spec['arm']}.json")["kit"]
            self.assertIn("terrain", kit)
            ses = episode.Session(store, spec, os.path.join(d, "work"), seed=1)
            res = ses.run()[0]
            steps = [json.loads(l) for l in store.get(f"steps/{spec['arm']}/{ses.id}.jsonl").decode().splitlines()]
            out = {}
            for s in steps:
                if s.get("role") == "tool":
                    out.setdefault(s["name"], []).append(s["out"])
            self.assertIn("research available now", out["fk"][1])
            self.assertIn("isn't used in this lab", out["fk"][2])           # fk wait redirected
            self.assertIn("quotes kept", out["fk"][3])                       # the Lua went through as written
            self.assertNotIn("marker.fk", out["list_files"][0])              # load_state rolled the codebase back
            self.assertEqual(res["invalid_marks"], 1)                        # fk lua's insert was marked
            build = out["fk"][5]
            self.assertIn("[23/23]", build)
            self.assertIn("(exit 0)", build)
            self.assertRegex(out["fk"][6], r"ok (\d+)/\1 designed entities")  # world == design
