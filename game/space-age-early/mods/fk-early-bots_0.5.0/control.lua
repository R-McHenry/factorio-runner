-- Loot chests: a small landfilled islet with an iron chest of bots, one per SPACING x SPACING chunks.
-- The chest is the player force's (0.2.1: robots may not deconstruct a neutral chest): a player
-- reaches it with roboport coverage and marks it for deconstruction, and the robots bring its
-- contents home.

local function hash(x, y)
  return bit32.bxor(x * 73856093 % 4294967296, y * 19349663 % 4294967296, 0x5bd1e995) % 1000003
end

local function contents(ring)
  local n = settings.global["fk-loot-bots"].value * ring
  local items = {
    { name = "fk-early-construction-robot", count = n },
    { name = "fk-early-logistic-robot", count = math.floor(n / 2) },
    { name = "fk-early-roboport", count = 1 },
  }
  -- the far rings also hold some real robots
  if ring >= 3 then table.insert(items, { name = "construction-robot", count = 5 * (ring - 2) }) end
  return items
end

local function clear_worms(surface, area)
  for _, w in pairs(surface.find_entities_filtered{ area = area, force = "enemy", type = "turret" }) do
    w.destroy()
  end
end

local function sweep_worms()
  if not settings.global["fk-no-worms"].value then return end
  for _, s in pairs(game.surfaces) do clear_worms(s, nil) end
end
script.on_init(sweep_worms)
script.on_configuration_changed(sweep_worms)
script.on_event(defines.events.on_runtime_mod_setting_changed, function(e)
  if e.setting == "fk-no-worms" then sweep_worms() end
end)

script.on_event(defines.events.on_chunk_generated, function(e)
  if settings.global["fk-no-worms"].value then clear_worms(e.surface, e.area) end
  if not settings.global["fk-loot-chests"].value then return end
  if e.surface.name ~= "nauvis" then return end
  local s = settings.global["fk-loot-spacing"].value
  local cx, cy = e.position.x, e.position.y
  if cx % s ~= 0 or cy % s ~= 0 then return end
  local ring = math.floor(math.max(math.abs(cx), math.abs(cy)) / s)
  if ring < 1 then return end

  local h = hash(cx, cy)
  local x = cx * 32 + 6 + h % 20
  local y = cy * 32 + 6 + math.floor(h / 20) % 20
  local tile = prototypes.tile["landfill"] and "landfill" or "grass-1"
  local tiles = {}
  for dx = -2, 2 do
    for dy = -2, 2 do table.insert(tiles, { name = tile, position = { x + dx, y + dy } }) end
  end
  e.surface.set_tiles(tiles, true)
  for _, ent in pairs(e.surface.find_entities_filtered{ area = { { x - 2, y - 2 }, { x + 3, y + 3 } } }) do
    if ent.valid and ent.type ~= "character" then ent.destroy() end
  end
  local chest = e.surface.create_entity{ name = "iron-chest", position = { x + 0.5, y + 0.5 }, force = "player" }
  if not chest then return end
  local inv = chest.get_inventory(defines.inventory.chest)
  for _, it in pairs(contents(ring)) do
    if prototypes.item[it.name] and it.count > 0 then inv.insert(it) end
  end
  storage.loot = storage.loot or {}
  table.insert(storage.loot, { position = chest.position, ring = ring })
end)

local function tag_charted(e)
  if not settings.global["fk-loot-tags"].value or not storage.loot then return end
  local surface = game.surfaces[e.surface_index]
  if not surface or surface.name ~= "nauvis" then return end
  local area = e.area
  for _, l in pairs(storage.loot) do
    local p = l.position
    if not l.tagged and p.x >= area.left_top.x and p.x < area.right_bottom.x
      and p.y >= area.left_top.y and p.y < area.right_bottom.y then
      e.force.add_chart_tag(surface, { position = p, text = "loot " .. l.ring, icon = { type = "item", name = "iron-chest" } })
      l.tagged = true
    end
  end
end
script.on_event(defines.events.on_chunk_charted, tag_charted)

-- loot.list: where every chest is, for the toolkit
remote.add_interface("fk-loot", {
  list = function() return storage.loot or {} end,
})
