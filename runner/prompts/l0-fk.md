# The fk toolkit

You also have the fk toolkit (the `fk` tool). With it your base is a design, not a pile of placed
entities: factories written as short `.fk` files in your game codebase (`game/` in your work dir),
laid out, routed, powered and pasted as ghosts by one command, then built by robots.

- **The codebase is your base's source of truth.** `game/world.fk` is your base's plot (the
  starter kit is declared in it via `kit.fk`); factories go in their own `.fk` files, imported and
  placed there. Edit them with write_file, then `fk build nauvis.build.fkb`.
- **The build** reads the design, sizes rows, places, routes belts and inserters, adds robot
  coverage and power poles, checks everything, and pastes the difference as ghosts. Reports land
  in `game/out/` (`out/report_checks.txt`, `out/report_bom.txt` say what's wrong or short). Then
  `evaluate` so the robots build it, and `fk look . --source world --area X0,Y0,X1,Y1` to see the
  world against the design.
- **Reading:** `fk stats .`, `fk stock .`, `fk research .`, `fk tech . NAME`, `fk recipes .`,
  `fk rates FILE.fk`, `fk plan ITEM RATE`, `fk explain FILE.fk NAME`, `fk check FILE.fk`.
- **Commands take GAME_DIR, which is `.`** (fk runs in `game/`). `fk help` lists every command of
  this toolkit (2.1.30) and is the authority.
- **Docs:** `game/docs/TOOLKIT.md`, `LANGUAGE.md`, `EXAMPLES.md` and `game/docs/examples/` were
  written for toolkit 2.0.43 on Windows. The language and the build are the same; read their `fk`
  as your fk tool, their `\` as `/`, and where they disagree with `fk help`, `fk help` wins.
- **Not used here:** `fk wait` (the game stands still while you think: use evaluate), `fk sim` and
  `fk lab` (use save_state, try it, load_state to undo). `fk lua` goes through the same referee as
  the lua tool.
- **Save states include the codebase.** load_state puts `game/` back as it was when the state was
  saved, so the design always matches the world it describes. NOTES.md is not rolled back.
- **This start's robots are early robots** (slow, very low power) and its roboport is an early
  roboport (logistic area 20, construction area 44); the build's `cover robots` step adds early
  roboports where a factory's chests need coverage. Real roboports and robots come with research.
