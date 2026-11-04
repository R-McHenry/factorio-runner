-- Tier-0.5 robots: slower, shorter-ranged and much cheaper than the real ones, craftable from tick 0.
-- Built in data-final-fixes so pack tweaks (Sea Block's payload 1, Bob's robot tiers) are already in.
if not settings.startup["fk-early-bots"].value then return end

local TINT = { r = 0.85, g = 0.55, b = 0.25, a = 1 }

local function tint(t)
  if type(t) ~= "table" then return end
  if t.filename or t.filenames then t.tint = TINT end
  for _, v in pairs(t) do tint(v) end
end

local function have(item) return data.raw.item[item] ~= nil end
local CIRCUIT = have("bob-basic-circuit-board") and "bob-basic-circuit-board" or "electronic-circuit"
local BRICK = have("stone-brick") and "stone-brick" or "stone"

local function icons_of(proto)
  if proto.icons then
    local icons = table.deepcopy(proto.icons)
    for _, i in pairs(icons) do i.tint = TINT end
    return icons
  end
  return { { icon = proto.icon, icon_size = proto.icon_size or 64, tint = TINT } }
end

local function clone(kind, from, name, entity_tweak, ingredients)
  local e = table.deepcopy(data.raw[kind][from])
  local item = table.deepcopy(data.raw.item[from])
  e.name = name
  e.minable = { mining_time = 0.1, result = name }
  e.placeable_by = nil
  e.next_upgrade = nil
  for _, k in pairs({ "animation", "idle", "in_motion", "shadow_idle", "shadow_in_motion", "working",
    "shadow_working", "base", "base_patch", "base_animation", "door_animation_up", "door_animation_down" }) do
    tint(e[k])
  end
  e.icons = icons_of(e); e.icon = nil
  entity_tweak(e)
  item.name = name
  item.place_result = name
  item.icons = icons_of(item); item.icon = nil
  item.order = (item.order or "z") .. "-0"
  data:extend({ e, item, {
    type = "recipe", name = name, enabled = true, energy_required = 2,
    ingredients = ingredients, results = { { type = "item", name = name, amount = 1 } },
  } })
end

local function weak_robot(r)
  -- 0.4 (Rory, 11-01: "extreme low power use, but slow and cost a lot to make"): a tenth of a
  -- vanilla robot's energy, so the early solar grid carries them and they seldom queue to charge
  r.speed = 0.036               -- vanilla 0.06
  r.max_payload_size = 1
  r.max_energy = "1.5MJ"        -- vanilla's battery, which lasts ten times as long
  r.energy_per_move = "0.5kJ"   -- vanilla 5kJ
  r.energy_per_tick = "0.005kJ" -- vanilla 0.05kJ
  r.max_health = 75
end

local BOT_COST = {
  { type = "item", name = "iron-plate", amount = 8 },
  { type = "item", name = "iron-gear-wheel", amount = 4 },
  { type = "item", name = CIRCUIT, amount = 6 },
}

clone("construction-robot", "construction-robot", "fk-early-construction-robot", weak_robot, BOT_COST)
clone("logistic-robot", "logistic-robot", "fk-early-logistic-robot", weak_robot, BOT_COST)

clone("roboport", "roboport", "fk-early-roboport", function(p)
  p.logistics_radius = 20         -- vanilla 25
  p.construction_radius = 44      -- vanilla 55
  p.robot_slots_count = 5
  p.material_slots_count = 5
  p.charging_energy = "100kW"     -- vanilla 500kW per slot (0.4: a low-power bot needs little)
  p.energy_usage = "5kW"          -- vanilla 50kW
  p.energy_source.buffer_capacity = "10MJ"
  p.energy_source.input_flow_limit = "500kW"  -- 0.3's 4MW could take the kit's whole solar grid
  p.max_health = 200
  -- the upgrade planner swaps it for a real roboport once those exist
  p.fast_replaceable_group = "roboport"
  p.next_upgrade = "roboport"
end, {
  { type = "item", name = "iron-plate", amount = 30 },
  { type = "item", name = "iron-gear-wheel", amount = 15 },
  { type = "item", name = CIRCUIT, amount = 20 },
  { type = "item", name = BRICK, amount = 20 },
})
data.raw.roboport.roboport.fast_replaceable_group = "roboport"
