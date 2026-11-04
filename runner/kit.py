"""The kickoff kit: what a map gets before tick-counted play, since there is no player character.

Adapted from factorio-playable's design/v5/kickoff/setup_v1.py (BOOTSTRAP, ROBOTS, OIL, CHESTS,
KIT, RESEARCH and its three Lua steps). Only the bootstrap is placed directly (nothing can build
before a roboport and power exist); everything else is items in storage chests for the robots.
"""
from __future__ import annotations

import json

BOOTSTRAP = (
    [("roboport", 8, 8)]
    + [("solar-panel", x + 0.5, y + 0.5) for x, y in ((8, 15), (12, 15), (16, 15), (8, 19),
                                                       (12, 19), (16, 19))]
    + [("accumulator", 21, 15), ("accumulator", 21, 19)]
    + [("medium-electric-pole", x, y) for x, y in ((8.5, 11.5), (10.5, 14.5), (14.5, 14.5),
                                                   (10.5, 18.5), (14.5, 18.5), (21.5, 13.5),
                                                   (21.5, 17.5))]
)
ROBOTS = {"construction-robot": 50, "logistic-robot": 50}
OIL = [(6.5, -9.5, 300000), (11.5, -9.5, 300000), (16.5, -9.5, 300000)]
CHESTS = [(x + 0.5, y + 0.5) for x, y in ((2, 8), (3, 8), (4, 8), (2, 9), (3, 9), (4, 9))]
KIT = [("roboport", 5), ("assembling-machine-1", 3), ("medium-electric-pole", 93),
       ("storage-chest", 10), ("requester-chest", 10), ("passive-provider-chest", 10),
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


def _q(value) -> str:
    return json.dumps(json.dumps(value))


LUA_BOOTSTRAP = """
if storage.kickoff_setup then rcon.print('REFUSED: this map is already set up') return end
local s = game.surfaces[1] local f = game.forces.player
local B = helpers.json_to_table(%s) local R = helpers.json_to_table(%s) local O = helpers.json_to_table(%s)
pcall(function() remote.call('freeplay', 'set_disable_crashsite', true) end)
pcall(function() remote.call('freeplay', 'set_skip_intro', true) end)
for _, e in pairs(s.find_entities_filtered{force = 'neutral'}) do
  if e.valid and e.name:find('^crash%%-site') then e.destroy() end
end
for _, b in ipairs(B) do
  local r = (b[1] == 'roboport') and 2.5 or 1.6
  for _, e in pairs(s.find_entities_filtered{area = {{b[2] - r, b[3] - r}, {b[2] + r, b[3] + r}}, type = {'tree', 'simple-entity'}}) do e.destroy() end
end
local wells = 0
for _, o in ipairs(O) do
  for _, e in pairs(s.find_entities_filtered{area = {{o[1] - 1.5, o[2] - 1.5}, {o[1] + 1.5, o[2] + 1.5}}, type = {'tree', 'simple-entity'}}) do e.destroy() end
  if s.create_entity{name = 'crude-oil', position = {o[1], o[2]}, amount = o[3]} then wells = wells + 1 end
end
local built, failed = 0, {}
for _, b in ipairs(B) do
  local e = s.create_entity{name = b[1], position = {b[2], b[3]}, force = f, raise_built = true}
  if e then built = built + 1 else failed[#failed + 1] = b[1] end
end
local port = s.find_entity(B[1][1], {B[1][2], B[1][3]})
if port then
  local inv = port.get_inventory(defines.inventory.roboport_robot)
  for n, c in pairs(R) do inv.insert{name = n, count = c} end
end
storage.kickoff_setup = 'bootstrap'
rcon.print('built ' .. built .. '/' .. #B .. ' oil ' .. wells .. ' failed {' .. table.concat(failed, ',') .. '}')
"""

LUA_KIT = """
if storage.kickoff_setup ~= 'bootstrap' then rcon.print('REFUSED: kit step out of order') return end
local s = game.surfaces[1] local f = game.forces.player
local C = helpers.json_to_table(%s) local K = helpers.json_to_table(%s)
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
rcon.print('chests ' .. #chests .. ' short {' .. table.concat(short, ',') .. '}')
"""

LUA_RULES = """
local f = game.forces.player local want = helpers.json_to_table(%s)
local missing = {}
for name, on in pairs(want) do
  local t = f.technologies[name]
  if t then t.researched = on else missing[#missing + 1] = name end
end
f.reset_technology_effects()
for _, p in pairs(game.players) do
  local c = p.character
  if c then p.set_controller{type = defines.controllers.spectator} if c.valid then c.destroy() end end
end
storage.kickoff_setup = 'rules'
rcon.print('rules speed ' .. f.worker_robots_speed_modifier .. ' cargo ' .. f.worker_robots_storage_bonus .. ' missing {' .. table.concat(missing, ',') .. '}')
"""


#: The early-bots start (Rory, 2026-11-04: the kickoff kit's start is too hard, its power is a
#: nuclear reactor to build first). Rory's own fk-early-bots mod (in the arm's mod directory):
#: robots at a tenth of a normal robot's energy and a roboport that idles at 5 kW, so the solar
#: block carries them, and a steam plant in the chests (steam power is researched) instead of
#: the reactor. The first bootstrap entry must be the roboport: the robots go into it.
BOOTSTRAP_EARLY = [("fk-early-roboport", 8, 8)] + BOOTSTRAP[1:]
ROBOTS_EARLY = {"fk-early-construction-robot": 50, "fk-early-logistic-robot": 20}
KIT_EARLY = [("fk-early-roboport", 5), ("assembling-machine-1", 3), ("medium-electric-pole", 93),
             ("small-electric-pole", 50), ("storage-chest", 10), ("requester-chest", 10),
             ("passive-provider-chest", 10), ("offshore-pump", 2), ("boiler", 4), ("steam-engine", 8),
             ("stone-furnace", 10), ("iron-plate", 200), ("copper-plate", 200), ("iron-ore", 400),
             ("copper-ore", 400), ("coal", 400), ("stone", 200), ("transport-belt", 200),
             ("inserter", 50), ("burner-inserter", 20), ("long-handed-inserter", 20),
             ("fast-inserter", 20), ("pipe", 200), ("pipe-to-ground", 10), ("iron-chest", 1),
             ("underground-belt", 20), ("splitter", 10), ("burner-mining-drill", 10)]

#: kit name (an arm's game.kit) -> (bootstrap, robots, chest contents)
KITS = {
    "kickoff-v1": (BOOTSTRAP, ROBOTS, KIT),
    "kickoff-early-bots": (BOOTSTRAP_EARLY, ROBOTS_EARLY, KIT_EARLY),
}


def apply(server, name: str = "kickoff-v1") -> dict:
    """Put kit `name` down on a fresh map (paused server). -> the three steps' reports."""
    if name not in KITS:
        raise ValueError(f"no kit {name!r}; kits: {', '.join(KITS)}")
    bootstrap, robots, contents = KITS[name]
    out = {"kit_name": name,
           "bootstrap": server.lua(LUA_BOOTSTRAP % (_q(bootstrap), _q(robots), _q(OIL))),
           "kit": server.lua(LUA_KIT % (_q(CHESTS), _q(contents))),
           "rules": server.lua(LUA_RULES % _q(RESEARCH))}
    for step, line in out.items():
        if step != "kit_name" and line.startswith("REFUSED"):
            raise RuntimeError(f"kit {step}: {line}")
    return out
