# Factorio: the speedrun lab, with the fk toolkit

You play Factorio 2.0 (with Space Age content) in a lab copy of a real game. There is no player
character: robots from the logistic network build everything placed as ghosts. You build with the
**fk toolkit**: your base is a design in a game codebase, and the toolkit turns the design into
ghosts for the robots.

## How you build: the toolkit

Your codebase is `game/` in your work dir. It's the source of truth for your base.

1. **Write factories as `.fk` files** in `game/`: machines in rows, and the nets that say what
   feeds what (`net plates: smelter -> gears, box`). Copy and adapt a working example from
   `game/docs/examples/` (`gears.fk`, `beltless.fk`, `smelting.fk`, `steam.fk`, `smelter-array.fk`,
   `busbase.fk`); `game/docs/LANGUAGE.md` has the language, `game/docs/EXAMPLES.md` what each
   example shows. **Start from a whole example, not hand-written cells:** `red.fk` is a working
   red-science plot and `busbase.fk` a 116-machine bus base; put one in `world.fk`, `fk check` it,
   `build`, `evaluate`, then extend it. Composing from the library's cells costs dozens of turns
   before the first build lands. The kit's requester and provider chests do build and work:
   blueprint ghosts are not tech-locked, and the bots put items in a requester chest. But only 20
   early logistic robots serve them, so keep `request=` modest and give them game time, or belt
   plates in from miners and furnaces for a steady feed. The kit has no labs: red science counts
   when it is produced, so leave the `LabRow` a ghost until an assembler can craft labs.
2. **Place them in `game/world.fk`**, your base's plot: `import Gears from "gears.fk"` and
   `gears = Gears @(1030, 1000) size 16x16`. world.fk's coordinates are the map's plus 1000, so
   `@(1030, 1000)` is map 30,0. The starter kit is already declared there (`kit.fk`).
3. **Check on paper first:** `fk check FILE.fk`, `fk rates FILE.fk`, `fk plan ITEM RATE` (machines
   a rate takes).
4. **Build:** `fk build nauvis.build.fkb`. It sizes, places, routes belts and inserters, adds
   roboports and power poles, checks, and pastes the new parts as ghosts. Read
   `game/out/report_checks.txt` and `out/report_bom.txt` when it complains.
5. **Run the game** with `evaluate` so robots build it. One call takes up to 36,000 ticks (10
   game minutes) and the milestone is checked every 600, so ask for 6,000-36,000 at a time — a
   600-tick call is one turn for ten game seconds. Then compare the world with the design:
   `fk look . --source world --area X0,Y0,X1,Y1`, and `fk stats .` for what it makes.
6. **Change the design and build again** to fix or grow it. Don't patch the world by hand: the next
   build would undo it, and the design would stop describing the base.

`fk` runs in `game/`, so a command's GAME_DIR is `.` (paths may be written with or without `game/`). `fk help` lists every command of this toolkit
(2.1.30) and wins where the docs (written for 2.0.43, on Windows: read `\` as `/`) disagree. Free
reads: `fk stats .`, `fk stock .`, `fk research .`, `fk tech . NAME`, `fk recipes .`. Queue research
with `fk research . TECH`.

Use **Lua** (the lua tool, or `fk lua . CODE`) to read and investigate anything, and for small
one-off actions a player could do. It's not how you build the base.

## The rules

- **Reading the game is free.** Anything, anywhere, any time.
- **Changing the game goes through the game:** ghosts robots build (the toolkit's paste, or a
  ghost by hand), deconstruction orders, recipes, filters, requests, research. Lua that creates,
  moves or deletes items or entities directly, or touches the clock, still runs, but the run stops
  counting from there: the runner saves the legit game from just before as `last-valid-N`, and
  loading it makes the run count again. Every tool result ends with `[legit run - ...]` or
  `[testing/planning - ...]`; only a legit run reaches a goal.
- **Time only moves when you call `evaluate`.** Your thinking costs no game time. The score is the
  game ticks of the legit run: reach the goal in as few as you can.
- **Save states hold the game and your codebase.** `save_state` before a risky change; `load_state`
  puts both the world and `game/` back (NOTES.md is not rolled back). `fk wait`, `fk sim` and
  `fk lab` aren't used here: evaluate, and save states, do their jobs.
- **Sessions have several episodes,** sometimes in a different saved game. When the conversation
  gets long, old tool calls and results are shortened: anything you'll need again belongs in
  NOTES.md, which the next player also starts from. Keep it current.

## This start

Early robots (slow, very low power) and an early roboport (logistic area 20, construction area 44)
at map 8,8, on six solar panels and two accumulators; supplies in six storage chests beside it,
including a steam power plant to build. The build's `cover robots` step adds early roboports where
a factory's chests need coverage. Real roboports and robots come with research.
