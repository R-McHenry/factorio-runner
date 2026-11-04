data:extend({
  { type = "bool-setting", name = "fk-early-bots", setting_type = "startup", default_value = true, order = "a" },
  { type = "bool-setting", name = "fk-loot-chests", setting_type = "runtime-global", default_value = true, order = "b" },
  -- one chest per SPACING x SPACING chunks, never inside the first ring around spawn
  { type = "int-setting", name = "fk-loot-spacing", setting_type = "runtime-global", default_value = 5, minimum_value = 2, order = "c" },
  -- early construction robots in the nearest ring; each ring further out adds this many again
  { type = "int-setting", name = "fk-loot-bots", setting_type = "runtime-global", default_value = 10, minimum_value = 1, order = "d" },
  { type = "bool-setting", name = "fk-loot-tags", setting_type = "runtime-global", default_value = true, order = "e" },
  -- worms are Sea Block's only enemies; on, none generate and any standing are removed
  { type = "bool-setting", name = "fk-no-worms", setting_type = "runtime-global", default_value = true, order = "f" },
})
