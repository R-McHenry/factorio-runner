"""Build the toolkit's `sa-early` pack: the base game's data (Space Age, fkcore/data) plus the
three fk-early-bots prototypes, at fk-early-bots 0.5.0's values with the runner's 10x cost.

A pack is normally dumped from its game (tools/dump_catalog.lua, tools/read_facts.py). Space Age
plus one small mod is the base data with three rows added, so this script adds them: the
Sea Block pack's rows for the same mod (0.2.0), corrected to 0.5.0 (radii 44/20, 5 kW drain,
low-energy robots) and to this game's recipes (electronic circuits, 10x).
    python3 toolkit/make_pack.py
"""
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "fkcore", "data")
SB = os.path.join(DATA, "packs", "seablock")
OUT = os.path.join(DATA, "packs", "sa-early")
COST = 10
BOT = [("iron-plate", 8), ("iron-gear-wheel", 4), ("electronic-circuit", 6)]
PORT = [("iron-plate", 30), ("iron-gear-wheel", 15), ("electronic-circuit", 20), ("stone-brick", 20)]


def load(p):
    with open(p) as fh:
        return json.load(fh)


def main():
    os.makedirs(OUT, exist_ok=True)
    cat = load(os.path.join(DATA, "catalog.json"))
    sb = load(os.path.join(SB, "catalog.json"))
    early = {e["name"]: e for e in sb["entities"] if e["name"].startswith("fk-early")}
    port = early["fk-early-roboport"]
    port["construction_radius"] = 44
    port["logistic_radius"] = 20
    port["energy"] = {"drain_w": 5000, "priority": "secondary-input", "source": "electric", "usage_w": 405000}
    names = {e["name"] for e in cat["entities"]}
    cat["entities"] += [e for n, e in sorted(early.items()) if n not in names]
    recipes = []
    for name, ing in (("fk-early-construction-robot", BOT), ("fk-early-logistic-robot", BOT), ("fk-early-roboport", PORT)):
        recipes.append({"category": "crafting", "enabled": True, "energy": 2, "name": name,
                        "ingredients": [{"type": "item", "name": i, "amount": a * COST} for i, a in ing],
                        "products": [{"type": "item", "name": name, "amount": 1}]})
    have = {r["name"] for r in cat["recipes"]}
    cat["recipes"] += [r for r in recipes if r["name"] not in have]
    with open(os.path.join(OUT, "catalog.json"), "w") as fh:
        json.dump(cat, fh, indent=1, sort_keys=True)
    facts = load(os.path.join(DATA, "facts.json"))
    sbf = load(os.path.join(SB, "facts.json"))
    for n, row in sbf["entities"].items():
        if n.startswith("fk-early"):
            row = dict(row)
            if n == "fk-early-roboport":
                row.update(drain_w=5000, energy_w=5000)
            else:
                row.update(energy_w=300)      # energy_per_tick 0.005 kJ
            facts["entities"][n] = row
    facts.setdefault("mods", {})["fk-early-bots"] = "0.5.0"
    facts["source"] = (str(facts.get("source", "")) + " | sa-early: plus fk-early-bots 0.5.0 rows "
                       "(toolkit/make_pack.py in factorio-runner)")
    with open(os.path.join(OUT, "facts.json"), "w") as fh:
        json.dump(facts, fh, indent=1, sort_keys=True)
    shutil.copyfile(os.path.join(DATA, "fluidboxes.json"), os.path.join(OUT, "fluidboxes.json"))
    with open(os.path.join(OUT, "pack.json"), "w") as fh:
        json.dump({}, fh)
    print("sa-early:", len(cat["entities"]), "entities,", len(cat["recipes"]), "recipes")


if __name__ == "__main__":
    main()
