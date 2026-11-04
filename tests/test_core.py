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
              "game.forces.player.add_research('automation')"]
        bad = ["chest.insert{name = 'iron-plate', count = 5}",
               "game.surfaces[1].create_entity{name = 'inserter', position = {1, 1}, force = 'player'}",
               "for _, g in pairs(gs) do g.revive() end",
               "game.forces.player.technologies['automation'].researched = true",
               "game.speed = 10", "e.destroy()"]
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

    def test_ledger(self):
        with tempfile.TemporaryDirectory() as d:
            s = LocalStore(d)
            ledger.add(s, "deepseek", 4.0)
            ledger.check(s, "deepseek", 10.0)
            with self.assertRaises(ledger.OverBudget):
                ledger.check(s, "deepseek", 10.0, reserve=7.0)


if __name__ == "__main__":
    unittest.main()
