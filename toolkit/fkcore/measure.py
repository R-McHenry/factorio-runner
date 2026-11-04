"""fkcore.measure: what the machines in an area really make, against the nominal model (CORE-V1 §8).

Fair play: only the area the agent names. Two reads of every crafting machine there (assemblers and
furnaces: ``LuaEntity.products_finished``, the crafts it finished in its life), ``seconds`` apart.
A machine's rate is its crafts per second times its recipe's products. Each machine is matched to
the design by position and name, so its rate lands on its instance (``<factory>/<instance>``),
and is compared with the rate model's rate for that instance (``rates.solve``: utilization times
full speed). Drills and labs are not measured in V1 (they finish no products). The reads go
through ``helpers.write_file`` like a snapshot; nothing is written to the world.
"""
from __future__ import annotations

import json
import os
import time

from . import link

__all__ = ["lua_read", "read", "measure", "Measure"]


def lua_read(surface, area, tag):
    x0, y0, x1, y1 = area
    return f"""
local s = game.get_surface({json.dumps(surface)})
local out = {{}}
for _, e in pairs(s.find_entities_filtered{{area = {{{{{x0},{y0}}},{{{x1},{y1}}}}}, type = {{'assembling-machine', 'furnace'}}}}) do
  local ok, r = pcall(e.get_recipe)
  out[#out + 1] = {{n = e.name, x = e.position.x, y = e.position.y, pf = e.products_finished,
    rc = ok and r and r.name or nil, st = e.status}}
end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({{tick = game.tick, machines = out}}), false)
rcon.print(#out)
"""


def read(r, surface, area, tag="measure"):
    tag = f"{tag}-{os.getpid()}"
    n = r.lua(lua_read(surface, area, tag)).strip()
    if not n.isdigit():
        raise link.LinkError(f"measure read failed: {n}")
    with open(os.path.join(link.OUT_DIR, "fk5", f"{tag}.json"), encoding="utf-8") as fh:
        got = json.load(fh)
    rows = got.get("machines") or []
    got["machines"] = list(rows.values()) if isinstance(rows, dict) else rows
    return got


class Measure:
    def __init__(self, area, seconds, ticks, rows, unmatched):
        self.area, self.seconds, self.ticks, self.rows, self.unmatched = (area, seconds, ticks,
                                                                          rows, unmatched)

    def lines(self):
        head = (f"measure area {','.join(str(int(v)) for v in self.area)}: {self.ticks} ticks "
                f"({self.ticks / 60:.1f} s), {sum(r['machines'] for r in self.rows)} machines "
                f"matched to the design, {self.unmatched} not")
        out = [head, f"  {'instance':<26} {'item':<28} {'measured/min':>13} {'model/min':>10} "
                     f"{'ratio':>6}"]
        for r in self.rows:
            # a model rate that rounds to nothing is no ratio (2.0.3: a float like 1e-17 printed
            # as a ratio of 264886835391085376)
            ratio = f"{r['measured'] / r['model']:.2f}" if r["model"] > 1e-9 else "-"
            out.append(f"  {r['inst']:<26} {r['item']:<28} {r['measured'] * 60:>13.2f} "
                       f"{r['model'] * 60:>10.2f} {ratio:>6}")
        return out


def measure(r, s, cat, area, flats, models, seconds=30.0):
    """``s`` the composed surface, ``flats``/``models`` per factory (``rates.solve``). -> Measure."""
    a = read(r, s.name, area, "measure-a")
    time.sleep(seconds)
    b = read(r, s.name, area, "measure-b")
    ticks = int(b["tick"]) - int(a["tick"])
    first = {(m["n"], round(float(m["x"]), 3), round(float(m["y"]), 3)): m for m in a["machines"]}
    design = {(e.name, round(float(e.x), 3), round(float(e.y), 3)): e for e in s.entities.values()}
    per, unmatched = {}, 0
    for m in b["machines"]:
        k = (m["n"], round(float(m["x"]), 3), round(float(m["y"]), 3))
        e, m0 = design.get(k), first.get(k)
        if e is None or m0 is None or e.plot is None:
            unmatched += 1
            continue
        local = e.key.split(":", 1)[1].split("/", 1)[1]            # <inst path>/<m>
        inst = local.rsplit("/", 1)[0]
        flat = flats.get(e.plot)
        recipe = m.get("rc") or e.attrs.get("recipe")
        if flat is None or local not in flat.machines or not recipe or not cat.has_recipe(recipe):
            unmatched += 1
            continue
        crafts = (int(m["pf"]) - int(m0["pf"])) / (ticks / 60.0) if ticks else 0.0
        _needs, makes1 = cat.recipe_rates(recipe, 1.0)
        energy = float(cat.recipe(recipe)["energy"])
        for item, per_s_at_speed1 in makes1.items():
            amount = per_s_at_speed1 * energy                       # products per craft
            row = per.setdefault((e.plot, inst, item), {"inst": f"{e.plot}/{inst}", "item": item,
                                                        "measured": 0.0, "model": 0.0,
                                                        "machines": 0})
            row["measured"] += crafts * amount
            row["model"] += flat.machines[local].makes.get(item, 0.0) * models[e.plot].util[local]
            row["machines"] += 1
    rows = [per[k] for k in sorted(per)]
    return Measure(area, seconds, ticks, rows, unmatched)
