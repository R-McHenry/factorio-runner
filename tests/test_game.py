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
    def test_fresh_and_episode(self):
        spec = specs.load(os.path.join(ROOT, "arms", "test-scripted.toml"))
        spec["agent"]["script"] = os.path.join(ROOT, spec["agent"]["script"])
        with tempfile.TemporaryDirectory() as d:
            store = LocalStore(os.path.join(d, "store"))
            root = episode.fresh(store, spec, os.path.join(d, "fresh"))
            kit = store.get_json(f"roots/{spec['arm']}.json")["kit"]
            self.assertIn("built 16/16", kit["bootstrap"])
            self.assertIn("short {}", kit["kit"])
            res = episode.Episode(store, spec, os.path.join(d, "work"), seed=1).run()
            self.assertEqual(res["stop"], "finished")
            self.assertEqual(res["refused_lua"], 1)
            self.assertGreater(res["end_tick"], root.tick)
            steps = [json.loads(l) for l in store.get(f"steps/{spec['arm']}/{res['id']}.jsonl").decode().splitlines()]
            built = [s["out"] for s in steps if s.get("role") == "tool" and s["name"] == "lua"][-1]
            self.assertEqual(built.strip(), "1")   # robots built the ghost
            arch = archive.get(store, spec["arm"])
            self.assertGreaterEqual(len(arch["cells"]), 1)
            self.assertEqual(arch["episodes"], 1)


if __name__ == "__main__":
    unittest.main()
