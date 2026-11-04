# Factorio: the speedrun lab

You play Factorio 2.0 (with Space Age content) through tools, in a lab copy of a real game. There is
no player character: robots from the logistic network build everything you place as ghosts.

## The rules

- **Reading the game is free.** Inspect anything with Lua: entities anywhere, inventories,
  production statistics, recipes, technologies, prototypes, the map.
- **Changing the game goes through the game.** Place ghosts (robots build them from items in the
  logistic network), order deconstruction, set recipes, filters, requests and circuit conditions,
  queue research. Never create, move or delete items or entities directly; the referee refuses
  that.
- **Time only moves when you call `wait`.** Your thinking costs no game time. The score is game
  ticks: reach the goal in as few as you can.
- You start where the previous player stopped. NOTES.md in your work dir is what they left for
  you; keep it current for whoever continues from where you stop.

## Lua (Factorio 2.0 API, run as /sc; output only through rcon.print)

- `local s = game.surfaces[1]`, `local f = game.forces.player`.
- Find things: `s.find_entities_filtered{area = {{x0, y0}, {x1, y1}}, name = ..., type = ...}`,
  `s.count_entities_filtered{...}`, `s.find_entity(name, {x, y})`.
- Place a ghost: `s.create_entity{name = 'entity-ghost', inner_name = 'assembling-machine-1',
  position = {x, y}, direction = defines.direction.north, force = 'player'}`. Check
  `s.can_place_entity{name = ..., position = ..., direction = ...}` first. Tile ghosts:
  `name = 'tile-ghost'`.
- A crafting machine's recipe: `e.set_recipe('iron-gear-wheel')` (on the built machine, or its ghost).
- Research: `f.add_research('automation')`, `f.current_research`, `f.research_queue`,
  `f.technologies['automation'].enabled / .researched / .prerequisites / .research_unit_ingredients`.
- Recipes: `f.recipes['iron-gear-wheel'].enabled`, `prototypes.recipe['iron-gear-wheel'].ingredients
  / .products / .energy`.
- Production so far: `f.get_item_production_statistics('nauvis').input_counts` (made) and
  `.output_counts` (used).
- Logistic network: `f.find_logistic_network_by_position({x, y}, 'nauvis')` ->
  `.get_contents()`, `.available_construction_robots`, `.all_construction_robots`.
- Resources: `s.find_entities_filtered{type = 'resource', area = ...}` (`.name`, `.amount`).
- Encode output as JSON with `helpers.table_to_json(t)`.

## Working well here

- Look before you build: what's in the chests, where the ore and water are, what is researched.
- After placing ghosts, `wait` a little and check they got built (robots need the items in the
  network, and power).
- If something doesn't work, find out why from the game before trying again.
- Write what you learn in NOTES.md as you go, not only at the end.
