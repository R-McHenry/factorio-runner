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


if __name__ == "__main__":
    unittest.main()
