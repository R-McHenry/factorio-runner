# Building the base

How a base here is laid out, from the kit to a big base: what goes where, what carries what, and
what to build first. `STRATEGY.md` says how fast to grow and why; this guide says how to build it.
Every piece of it comes from a run that did it, or paid for not doing it.

## What this run tests

Two habits, which the last runs reached late and which this run starts with:

- **Modules all the way down.** `world.fk` places sealed modules and the trunks between their
  ports, and nothing else besides the plant and the kit. Each module is labbed alone before it goes
  into the world.
- **Lines are Stacks of cells**, not the old rows (below).

With `STRATEGY.md`'s ramp (paste the next step, not the one after), these are not rules: the
game's rules are all in `FAIR-PLAY.md`. But they are what this run is for. Follow them in spirit,
and when one doesn't fit, say why in `NOTES.md` and to Rory rather than quietly dropping it.

## The shape of a base

A base here has five kinds of piece. Each is a sealed module (or a region of them) with ports on
its edges.

1. **The backbone.** A pinned roboport grid (roboports on L-inf squares about 50 apart, ore patches
   at the corners, not under them), medium poles along it, and room for the minefield. It comes
   first and is improved, never designed around: everything else is placed on it. Its roboports and
   poles are its own module (or Literal), so no factory build moves them.
2. **The logistic hub.** One zone beside the kit's roboport with every requester and provider chest
   of the base, a storage chest, and the robot loader (`docs\examples\robot-feed.fk`). Robots fly
   short hops inside it; belts bring the base's products into it and take what it holds out.
   Requesters and providers are scarce until late research, so they live here and nowhere else.
3. **Material producers.** Mining and smelting on the big patches: a drill line beside a smelting
   Stack, the plates leaving by one port per belt. Each patch is its own module, sized to the ramp
   step and framed for the next doubling.
4. **The bus or corridor.** A module whose only job is to carry plates and intermediates from the
   producers past the consumers: `Bus<...>` when it is a row of belts of one item each with loads
   and taps (`docs\examples\bus4.fk`), or a plain routing corridor, a frame kept free for trunks.
   Consumers stand across it (machines in columns running away from it), so each takes a short
   stretch and the next one fits beside it (`docs\examples\busbase.fk`).
5. **Consumers.** The mall, each science, oil, power: modules that take from the bus by their ports
   and send their products to the hub or the next consumer.

A starter base is a small version of all five, close to the kit: a boot module, the hub, one iron
and one copper producer, a short corridor, the belt mall, red and green. The big base is the same
shape again on the big patches, further out (the last run's real base went east at ~2.15M ticks),
with the starter base still running beside it until the new one carries the load.

## Belts, chests and robots

- **Belts between modules.** A belt carries continuously, costs no robots, and doesn't slow down when
  the fleet is busy. Every trunk from one module's port to the next is a belt (or a pipe).
- **Chests and robots inside the hub, and in the very first cells.** A first cell fed by a
  requester and emptied into a provider is minutes to build and to change while the base's shape
  is still moving. Once a factory is one you keep, it goes onto belts. A kept factory fed by
  requester chests is a robot fleet doing a belt's job.
- **Never both for one factory.** Belts in and logistic chests out, or the reverse, pays for both.
- **Robots build and fetch.** Construction robots are the paste; logistic robots serve the hub and
  the odd item. Once the base is large, robots are the wall the runs hit (445 of 510 flying, new
  roboports at 0.4/min): build the makers of roboports, poles and robots next to their inputs, and
  keep every robot trip short.
- **Pair two inputs on one belt** (`lanes A | B`), and fill both lanes of an output belt.

## Hierarchy

- **A module is a frame with ports.** Its belts stay inside; a change inside it never moves its
  neighbours. Ports go where the trunks come from, on the edge facing them.
- **Regions nest.** A region (the iron smelting, the red-green block) is a sealed module of sealed
  modules: a Stack inside, a module or two beside it, its own trunks. The last run's LabArea, 72
  nets, routed in 12 s on its own; its flat world of 1,000 nets took minutes a build and pasted
  all or nothing.
- **Build every module alone first.** `fk lab NAME` (the lab surface, where things are free),
  let it run, `fk lab NAME --measure 60`. A module that builds clean alone goes into the world,
  and only its trunks are new there. Three of the last
  run's regions routed on the first try this way.
- **Leave room for the next size.** Frame each line for its next doubling (a Stack's `built=K`)
  without moving its ports, so growing it re-routes nothing outside.
- **Paste one region at a time.** Change a module, build, look, then the next one.
- **A hand-built area becomes a module** by drawing its frame: `fk walk X,Y --frame
  X0,Y0,X1,Y1` lists every belt over the edge as a ready `port` line and says when the frame
  seals (only belts cross it).
- **A new consumer gets its own branch.** Adding a far consumer to a main net re-lays the main
  (the coal belt by the boilers, once): give it a tap, a hub requester, or a net of its own.
- A sealed module's inside routes once: the build (or `fk compile MODULE`) stores it in the one
  compile store, and every later build, copy or move lays the stored inside, a lab build's
  included (TOOLKIT.md, "Sealed modules compile once").
  `--with "route --area ..."` reworks one region and leaves the rest standing.

## Lines are Stacks of cells

A line of machines is a `Stack` of cells from `game\cells.fk`: drills (`DrillCell`, one side;
`DrillPair`, both lanes), furnaces (electric, a pair across the plate belt, stone, a smelter pair,
a column), assemblers, green circuits, labs. A cell is yours to read, copy and change; the old
rows (`SmelterRow`, `SmelterArray`, `AsmRow`, `LabRow`, `CircuitStack`) are black boxes, and each
has a cell that replaces it (on the lab, 12 SmelterPair cells made 450 plates/min to
SmelterArray's 445). A `Stack<cell=DrillPair, n=8>` on a patch is a drill line you can read and
change. A cell you write earns a place in your library once it has built and measured clean in
the lab; until then it lives in the one module that needs it.

A machine the library has no cell for goes in one the same way: wrap it (a Literal of the
machine and its own inserters, the machine named; LANGUAGE.md, "Literals"), place it in
a cell whose nets the router lays, and stack the cell. `WrappedDrillCell` is the pattern. Stack
cells, not machines: a cell is the small unit that routes on its own.

## Adding a machine

The toolkit's catalog is the base game's. A Space Age or modded building it lacks, or lacks
something of (a foundry's fluid boxes), takes three steps:

1. `fk catalog learn foundry` reads it from your game into `game\learned.json`: its size, energy,
   fluid boxes, crafting categories, built-in effects (a foundry's +50% productivity), where it
   drops its output, and the recipes it crafts. `--mod NAME` learns every building a mod adds and
   `--all-mods` every modded one. Every design step plans with it from then on;
   `fk catalog show foundry` says what the toolkit knows of it.
2. Wrap it: a Literal file with the machine and a name for it (`{"entities": [{"name": "foundry",
   "x": 2.5, "y": 2.5, "recipe": "casting-iron-gear-wheel"}], "names": {"f": 0}}`), or
   `fk capture` one you built by hand, inserters and all. `Machine<entity=foundry, recipe=...>`
   places one without a file.
3. Use it in a cell, `m = Literal<file="foundry.json"> @(0, 2)` with `net mi: mi_in -> m.f,
   mi_out`, and stack the cell like any other. On the lab, three stacked foundry cells routed and
   connected 12 of 12 nets and cast gears from molten iron.

A burner takes its own kind of fuel: a biochamber burns nutrients by default (`fuel=` picks
another of its category). A recipe with fewer fluids than the machine has boxes on a side gives
the extra boxes to its first fluid (a cryogenic plant's three inputs with two fluids: boxes 1-2 and
3). `InfinityPipe<fluid=...>` pastes with its fluid set, so a test design can feed itself.

## Bootstrap

The kit has no labs and no stone furnaces, 3 assembling-machine-1s, 10 burner drills, 200 belts and
48 inserters; assembling-machine-1 needs automation. The last run's opening, as its template:

| ticks | what stood |
|---|---|
| ~17k | the grid's first roboports, medium poles, the boot (gears, belts, furnaces), burner fields on one coal belt |
| ~45k | all of it built; the kit's plates spent on its belts and gears |
| ~110k | 16 assembling-machine-1s made; automation, logistics, electric mining done |
| ~163k | the belt mall running (belts, inserters, circuits, cable, gears, assemblers) |
| ~510k | the auto mall, tried on the planner first |
| ~1.05M | blue science (4.9 game hours) |
| ~1.32M / 2.76M | construction robots made in the base / nuclear power |

What bites in the first hour:

- **A maker that waits on its own item is a deadlock.** A route that needs belts while the belt
  maker's own feed is that route, a feed re-laid through an item only its consumers make. Before a
  paste, check every SHORT item in `out\report_bom.txt` has a running maker that doesn't need it.
- **Put the shared input in the middle.** In the boot, the gear maker between the makers that take
  gears, so every link is one inserter (direct insertion) and no belt is needed yet.
- **Lay the maker of every short item first** (belts, inserters, undergrounds), then the module
  that needs them.
- **Robots never fuel what they build.** A burner drill line needs its first coal by belt or by an
  inserter from coal it already has.
- **The belt mall is what ends the bootstrap.** One-off crafters are fiddly by design; a belt mall
  fed from its own furnaces makes everything the next steps need, and is the step to reach first.
- **Don't change a full chest's `limit=` casually.** The build replaces the chest and the robots
  carry its contents out a few at a time.
- **Every roboport move strands its robots** in a chest until an inserter feeds them back in.
- **Buffers on intermediates hold a batch** (about 20 of what they feed), not one.

## Defense

On a map with biters, land mines are the defense from early on: military science right after green,
explosives from the first chemical plant, rings around the hotspots before ~2M ticks, and waves of
~50-80 mine ghosts at once against nests. `STRATEGY.md`, "Defense", has the reasons. The last two
runs that did this lost nothing to biters.
