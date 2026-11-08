"""fkcore.kickoff_setup: the starter world of a V1 run, on a map just made from the fk-play scenario.

3.0 (11-02): moved here from ``kickoff/setup_v1.py`` (still its command) so a run's own toolkit
has it: ``fk sim kickoff`` sets the planner's bare map up the same way (``run`` takes anything
with a ``lua(text)``: an RCON link or a ``fkcore.sim.Sim``).

Copied from the kickoff (v4-hand/kickoff/setup.py and starter_rules.py, 2026-10-18: Rory's kit and
rules), changed for V1:

- **no chat handler** through RCON. v4 installed one with ``script.on_event`` over RCON, the
  pattern that made saves unjoinable on 2026-10-18; the fk-play scenario keeps the chat journal
  itself (``fk_chat.lua``).
- **no plant paste here.** The plant is a factory of the run's game codebase (a Literal, the same
  world positions), so the run's first ``fk build`` pastes it with everything else and robots
  build it from the kit, like any factory.
- **the kit is in the design** (2.0.51). The bootstrap and the six chests below are exactly the
  entities ``kickoff/template/game/kit.fk`` declares (``kit.json``), and the plant and its pole line
  are ``plant.fk``'s: the run's one plot owns them, so nothing inside it belongs to the surface. The
  first build finds them standing and keeps them; what it adds is the rest of the design.

Steps, each one RCON call (``fkcore.link``: 5 s, nothing registered):

1. **bootstrap**, built directly, because there are no robots before the roboport exists:
   roboport (8,8) with 50 construction + 50 logistic robots, 6 solar panels, 2 accumulators, the
   7 panel poles, and three crude-oil wells north of spawn (Rory 2026-10-18).
2. **kit**: six storage chests holding the settled kit (93 medium poles: 100 with the bootstrap).
   Small on purpose (Rory, 2026-10-21: the player owns its supplies and manages the scarce
   logistic chests), with 10 burner mining drills added that day: a kit of plates and no drill
   deadlocked the lead world when its starter iron went to belts first (every electric drill
   takes plates; a burner drill mines on the kit's coal). One active provider chest since 2.0.72
   (Rory, 2026-10-30): the output chest of the circuit auto mall.
3. **rules**: robot speed 1 and cargo 1 only, steam power and electronics granted (there is no
   character to unlock them by hand), every player a spectator, no crash site.

Guarded by ``storage.kickoff_setup``: a second run on the same map refuses.
    python3 kickoff/setup_v1.py [--dry] [--pack seablock]   (FK_RCON_PORT / FK_RCON_PW_FILE as for
                                                             fkcore.link; FK_PACK as --pack)

**Sea Block** (``--pack seablock``, 2026-11-01): the world is water, so the bootstrap's rectangle is
landfilled first. The roboport is fk-early-roboport with 30 + 20 early robots (fk-early-bots: the
player can make more from the start), no oil and no plant. The chests hold Sea Block's own
starting items (read from its remote interface, then cleared so no joining player gets a copy;
minus the bootstrap's panels and accumulators), 2000 wood (foraging is hand-only and nothing else
makes wood before the arboretum: each basic circuit and red pack is one wood, and burner crushers
and stone furnaces burn it) and assemblers (locked until red science). ``angels-bio-wood-processing`` is granted:
its trigger is crafting wood by hand, which no character will do.
"""
from __future__ import annotations

import json
import os
import sys

from . import link

BOOTSTRAP = (
    [("roboport", 8, 8)]
    + [("solar-panel", x + 0.5, y + 0.5) for x, y in ((8, 15), (12, 15), (16, 15), (8, 19), (12, 19),
                                                       (16, 19))]
    + [("accumulator", 21, 15), ("accumulator", 21, 19)]
    + [("medium-electric-pole", x, y) for x, y in ((8.5, 11.5), (10.5, 14.5), (14.5, 14.5),
                                                   (10.5, 18.5), (14.5, 18.5), (21.5, 13.5),
                                                   (21.5, 17.5))]
)
ROBOTS = {"construction-robot": 50, "logistic-robot": 50}
OIL = [(6.5, -9.5, 300000), (11.5, -9.5, 300000), (16.5, -9.5, 300000)]   # 300000 = 100% yield
CHESTS = [(x + 0.5, y + 0.5) for x, y in ((2, 8), (3, 8), (4, 8), (2, 9), (3, 9), (4, 9))]
KIT = [("roboport", 5), ("assembling-machine-1", 3), ("medium-electric-pole", 93),
       ("storage-chest", 10), ("requester-chest", 10), ("passive-provider-chest", 10),
       ("active-provider-chest", 1),
       ("nuclear-reactor", 1), ("uranium-fuel-cell", 100), ("heat-exchanger", 8),
       ("steam-turbine", 10), ("heat-pipe", 30), ("offshore-pump", 4), ("iron-plate", 200),
       ("copper-plate", 200), ("iron-ore", 400), ("copper-ore", 400), ("coal", 200),
       ("stone", 200), ("transport-belt", 200), ("inserter", 50), ("long-handed-inserter", 20),
       ("fast-inserter", 20), ("pipe", 200), ("iron-chest", 1), ("underground-belt", 20),
       ("splitter", 10), ("burner-mining-drill", 10)]
RESEARCH = {"worker-robots-speed-1": True, "worker-robots-storage-1": True, "steam-power": True,
            "electronics": True}
RESEARCH.update({f"worker-robots-speed-{n}": False for n in range(2, 8)})
RESEARCH.update({f"worker-robots-storage-{n}": False for n in range(2, 4)})

SB_BOOTSTRAP = [("fk-early-roboport" if n == "roboport" else n, x, y) for n, x, y in BOOTSTRAP]
SB_RESEARCH = {k: v for k, v in RESEARCH.items() if k not in ("steam-power", "electronics")}
SB_RESEARCH["angels-bio-wood-processing"] = True

#: per pack: the bootstrap, its roboport, robots, oil, kit, research, the landfilled rectangle
#: (x0, y0, x1, y1) and whether Sea Block's starting items join the kit
PACKS = {
    None: {"bootstrap": BOOTSTRAP, "port": "roboport", "robots": ROBOTS, "oil": OIL, "kit": KIT,
           "research": RESEARCH, "land": [], "sb_items": False},
    "seablock": {
        "bootstrap": SB_BOOTSTRAP, "port": "fk-early-roboport",
        "robots": {"fk-early-construction-robot": 30, "fk-early-logistic-robot": 20}, "oil": [],
        "kit": [("fk-early-roboport", 4), ("assembling-machine-1", 8), ("medium-electric-pole", 93),
                 ("storage-chest", 10), ("requester-chest", 10), ("passive-provider-chest", 10),
                 ("active-provider-chest", 1), ("wood", 2000), ("offshore-pump", 4),
                 ("transport-belt", 200), ("inserter", 50), ("underground-belt", 20),
                 ("splitter", 10), ("iron-chest", 1)],
        "research": SB_RESEARCH, "land": [0, 5, 25, 23], "tile": "sand-3", "sb_items": True},
}


def q(value):
    return json.dumps(json.dumps(value))


LUA_BOOTSTRAP = """
if storage.kickoff_setup then rcon.print('REFUSED: this map is already set up') return end
local s = game.surfaces[1] local f = game.forces.player
local B = helpers.json_to_table(%s) local R = helpers.json_to_table(%s) local O = helpers.json_to_table(%s)
local PORT = %s local L = helpers.json_to_table(%s) local TILE = %s
pcall(function() remote.call('freeplay', 'set_disable_crashsite', true) end)
pcall(function() remote.call('freeplay', 'set_skip_intro', true) end)
local wrecks = 0
for _, e in pairs(s.find_entities_filtered{force = 'neutral'}) do
  if e.valid and e.name:find('^crash%%-site') then e.destroy() wrecks = wrecks + 1 end
end
local cleared = 0
for _, b in ipairs(B) do
  local r = (b[1] == PORT) and 2.5 or 1.6
  for _, e in pairs(s.find_entities_filtered{area = {{b[2] - r, b[3] - r}, {b[2] + r, b[3] + r}}, type = {'tree', 'simple-entity'}}) do
    e.destroy() cleared = cleared + 1
  end
end
local wells = 0
for _, o in ipairs(O) do
  for _, e in pairs(s.find_entities_filtered{area = {{o[1] - 1.5, o[2] - 1.5}, {o[1] + 1.5, o[2] + 1.5}}, type = {'tree', 'simple-entity'}}) do
    e.destroy() cleared = cleared + 1
  end
  if s.create_entity{name = 'crude-oil', position = {o[1], o[2]}, amount = o[3]} then wells = wells + 1 end
end
local out = {'wrecks removed ' .. wrecks, 'nature cleared ' .. cleared, 'oil wells ' .. wells .. '/' .. #O}
if L[1] then
  for _, e in pairs(s.find_entities_filtered{area = {{L[1], L[2]}, {L[3], L[4]}}, type = 'fish'}) do e.destroy() end
  local t = {}
  for x = L[1], L[3] - 1 do for y = L[2], L[4] - 1 do
    local m = s.get_tile(x, y).prototype.collision_mask.layers
    if m and m.water_tile then t[#t + 1] = {name = TILE, position = {x, y}} end
  end end
  s.set_tiles(t, true)
  out[#out + 1] = 'landfilled ' .. #t
end
local built = 0
for _, b in ipairs(B) do
  local e = s.create_entity{name = b[1], position = {b[2], b[3]}, force = f, raise_built = true}
  if e then built = built + 1 else out[#out + 1] = b[1] .. ' FAILED at ' .. b[2] .. ',' .. b[3] end
end
out[#out + 1] = 'built ' .. built .. '/' .. #B
local port = s.find_entity(PORT, {8, 8})
if port then
  local inv = port.get_inventory(defines.inventory.roboport_robot)
  for n, c in pairs(R) do inv.insert{name = n, count = c} end
end
storage.kickoff_setup = 'bootstrap'
rcon.print(table.concat(out, ' | '))
"""

LUA_KIT = """
if storage.kickoff_setup ~= 'bootstrap' then rcon.print('REFUSED: kit step out of order') return end
local s = game.surfaces[1] local f = game.forces.player
local C = helpers.json_to_table(%s) local K = helpers.json_to_table(%s) local SB = %s
local sb = ''
if SB then
  local used = {}
  for _, b in ipairs(helpers.json_to_table(%s)) do used[b[1]] = (used[b[1]] or 0) + 1 end
  local ok, items = pcall(remote.call, 'SeaBlock', 'get_starting_items')
  if ok and items then
    local n = 0
    for name, c in pairs(items) do
      if prototypes.item[name] and c - (used[name] or 0) > 0 then K[#K + 1] = {name, c - (used[name] or 0)} n = n + 1 end
    end
    remote.call('SeaBlock', 'set_starting_items', {})
    sb = ' seablock items ' .. n
  else sb = ' seablock items MISSING' end
end
local chests = {}
for _, c in ipairs(C) do chests[#chests + 1] = s.create_entity{name = 'storage-chest', position = c, force = f} end
local short = {} local i = 1
for _, k in ipairs(K) do
  local left = k[2]
  while left > 0 and i <= #chests do
    local n = chests[i].get_inventory(defines.inventory.chest).insert{name = k[1], count = left}
    if n == 0 then i = i + 1 else left = left - n end
  end
  if left > 0 then short[#short + 1] = k[1] .. ':' .. left end
end
storage.kickoff_setup = 'kit'
rcon.print('chests ' .. #chests .. ' short {' .. table.concat(short, ',') .. '}' .. sb)
"""

LUA_RULES = """
local f = game.forces.player local want = helpers.json_to_table(%s) local dry = %s local o = {changes = {}}
for name, on in pairs(want) do
  local t = f.technologies[name]
  if t then
    if t.researched ~= on then o.changes[#o.changes + 1] = name .. '=' .. tostring(on) if not dry then t.researched = on end end
  else o.changes[#o.changes + 1] = name .. ' missing' end
end
if not dry then f.reset_technology_effects() end
o.players = {}
for _, p in pairs(game.players) do
  local c = p.character
  o.players[#o.players + 1] = p.name .. (c and ' had a character' or ' no character')
  if not dry and c then p.set_controller{type = defines.controllers.spectator} if c.valid then c.destroy() end end
end
o.characters = game.surfaces[1].count_entities_filtered{type = 'character'}
o.speed_bonus = f.worker_robots_speed_modifier o.cargo_bonus = f.worker_robots_storage_bonus
local c, l = 0, 0
for _, n in pairs(f.logistic_networks[game.surfaces[1].name] or {}) do
  c = c + n.all_construction_robots l = l + n.all_logistic_robots
end
o.construction_robots = c o.logistic_robots = l o.tick = game.tick o.dry = dry
if not dry then storage.kickoff_setup = 'rules' end
rcon.print(helpers.table_to_json(o))
"""


def run(r, dry=False, pack=None):
    p = PACKS[pack]
    out = {}
    if not dry:
        out["bootstrap"] = r.lua(LUA_BOOTSTRAP % (q(p["bootstrap"]), q(p["robots"]), q(p["oil"]),
                                                  json.dumps(p["port"]), q(p["land"]),
                                                  json.dumps(p.get("tile", "landfill")))).strip()
        out["kit"] = r.lua(LUA_KIT % (q(CHESTS), q(p["kit"]), "true" if p["sb_items"] else "false",
                                      q(p["bootstrap"]))).strip()
    out["rules"] = json.loads(r.lua(LUA_RULES % (q(p["research"]), "true" if dry else "false")))
    return out


def main(argv):
    pack = argv[argv.index("--pack") + 1] if "--pack" in argv else (os.environ.get("FK_PACK") or None)
    with link.Rcon() as r:
        got = run(r, dry="--dry" in argv, pack=pack)
    for k, v in got.items():
        print(f"{k}: {v}")
    rules = got["rules"]
    ok = abs(float(rules["speed_bonus"]) - 0.35) < 1e-6 and int(rules["cargo_bonus"]) == 1
    print("starter rules:", "ok" if ok or "--dry" in argv else "NOT as expected")
    return 0 if ok or "--dry" in argv else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
