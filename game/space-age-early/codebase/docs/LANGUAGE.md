# The factory language (`.fk`): reference

A factory file says what the factory is: its machines and its nets. Where everything goes on the
ground, the belts, the inserters and the poles are the toolkit's job; you give each machine or line
a position. This page is the reference: every construct in a row with a two-line snippet and the
chapter of `EXAMPLES.md` that uses it. Learn from the chapters; look things up here.

```
import FurnaceCell from "cells.fk"
ore = MinerRow<n=4, resource=iron-ore> @(16, 9, rot 2)                          # 4 drills, turned 180
col = Stack<cell=FurnaceCell, n=8, plate=iron-plate, ore=iron-ore> @(10, 12)   # 8 furnaces in a line
box = Chest<provider, limit=4> @(30, 40)
net ore_:   ore -> col.ore_in             # the drills' ore goes up the line
net plates: col.pl_out -> box             # the plates into a provider chest
```

## Statements

| statement | snippet | chapter |
|---|---|---|
| instance | `name = Kind<param=value, ...> @(x, y)` or `@(x, y, rot r)`: the instance's origin (its (0, 0)) goes at (x, y), inside the factory's plot; `rot` turns it r quarter turns clockwise about that point, so a row or module that grows never moves what stood. A single machine (a chest, a pump) has no origin to turn about: (x, y) is its top-left tile at every rot. A Literal's origin is its file's (0, 0) | 1 |
| `@patch(iron-ore)` | `ore = MinerRow<n=4, resource=iron-ore> @patch(iron-ore)`: a miner row on that ore instead of a position (`@patch("iron-ore#2")` for a numbered patch) | 3 |
| `net` | `net NAME: drivers -> consumers`: one belt (or pipe; one lane of a shared belt, `lanes`). A ref names an instance (`gears`), a port (`line.c_in`), a cell's port (`cols[2].pl`, `cols[*].pl`) or a Literal's named machine (`std.asm1`). Options below | 1 |
| `net ... items a, b:` | `net s items iron-plate, coal: ...`: the items declared; else what the drivers make | 7 |
| `net ... width 2:` | `net plates width 2: smelt -> users`: both lanes of its belt (15/s on yellow); `width 8` is four belts side by side, a ribbon. 1, 2 or an even number | 8 |
| `net ... lane L:` | `net su lane L: sulf -> blue.t_in`: the lane its items end on where the net ends on a belt (`L` or `R`, looking downstream); into a machine by inserter it does nothing | 11 |
| `net ... any:` | `net pass any: a_in -> m1, m2, a_out`: connected when each driver reaches some consumer and each consumer is reached by some driver (a belt passing through a module; an output split to two ends). It changes the check, not the route | 6 |
| `net ... split:` | `net blue split: req -> engine, adv`: each item of a mixed net gets a lane and a filtered inserter of its own, one net per item (`blue_engine_unit`), paired two to a belt; a full lane never stops the other | 9 |
| `net ... limited "why":` | `net l1 limited "the gates hold the loop": g1.s_out -> g2.s_in`: your design limits what goes on the net, and the string says how. It quiets `CLOG` and `no-limit` for that net and limits nothing itself; a bare `limited` works and `fk check` asks for the string (`limited-why`) | 9 |
| `net ... when COND` | `net s: s_in, src -> s_out when lw.iron-plate < 160`: a gated net; every inserter and pump of its route moves items only while COND holds, and a route with neither is switched at its trunk belt. COND is below | 9 |
| `net each` | `net each plates: cols[*].pl -> box`: one net per cell, cell k of the Stack with port k of the other side (a Stack's cells or a sealed module's ports), named `plates_0`, `plates_1` ..., laid as one ribbon. Both sides need the same count, neither `n=auto` | 8 |
| chained net | `net gear: gears -> wp -> red, belts`: the net runs through the piece `wp` (a module with `inout` ports, `game\routing.fk`); one net laid in segments `gear` and `gear@wp`; `sp.b -> wp2 -> red` on the next line branches one way out | 11 |
| `X.*` | `net out: mall.* -> chest`: X's free endpoints, the ones no other net holds yet | 9 |
| `lanes A \| B` | `lanes ore_ \| fuel`: two nets on one belt, A on the left lane, B on the right (looking downstream); every machine on either gets the shared belt and one inserter takes both. A net shares with one other at most; fluids have no lanes. The `.fkp` writes the belt `net=ore_\|fuel` | 4 |
| `bundle` | `bundle ab: a, b`: a ribbon of nets you declared, belt 0 carrying `a`; they must run the same way | 8 |
| `assert` | `assert red.rate >= 9/min` (`/s`, `/h`): checked by the rate model, `PASS` or a `rate-not-met` line; `n=auto` lines are sized to meet it. `cols[2].rate` is one cell's, `cols[2].pl.rate` one port's | 5 |
| `set` | `set ins.out -> ibox: logistic=inserter<50`: a property on an object (Properties, below) | 9 |
| `dnp` | `dnp mall1, sci3.packs`, or `dnp` on an instance line: laid out and routed, not built (DNP, below) | 12 |
| `signal` | `signal A = signal-A`, or `signal stop` (the build picks one and says which) | 10 |
| `wire` | `wire level green: acc -> shed.latch.in`: one network of one colour, drivers -> consumers (Circuits, below) | 9, 10 |
| `power` | `power labs: cover field`: a power domain (Power, below) | 10 |
| `copper` | `copper load: sw.right -> labs`: a copper wire between domains or to a switch's side | 10 |
| `behave` | `behave fuel_saver on fuel_ctl { ... }`: what a control loop is meant to do (Circuits, below) | 9 |
| `import` | `import SmelterPair, AsmCell1 from "cells.fk"`: modules and kinds from another file, with the modules and kinds they are built of. `import Gears from "gears.fk"` in the world design makes a file with no module a plot | 7, 12 |
| `module` | `module NAME<p, q=default> [buffer] [size WxH] { ... }`: a reusable part written like a factory (Modules, below) | 6 |
| `port` | `port NAME : in\|out\|inout ITEM [\| ITEM2] @side[pos][L\|R]` inside a module (Modules, below) | 6 |
| `stack` | `stack c_in -> c_out, p_out -> p_in` inside a cell: pairs each NEAR port with its FAR port (Stacks, below) | 6 |
| `kind` | `kind Plastics = Machine<entity=chemical-plant, recipe=plastic-bar>`: a name with defaults; `kind CopperCell = FurnaceCell<plate=copper-plate, ore=copper-ore>` a kind of a module. The instance's own parameters win. At the top of a file; not of `Literal` or `Bus`, not inside a module, no loops | 6, 7 |
| `tiers` | `tiers Assembler: assembling-machine-1, assembling-machine-2, assembling-machine-3`: what `machine=auto` may pick for that kind, the first the default; every entity on a line has the same size, energy and fluid boxes | 5 |

A module instance's machines are named with a dot (`left.smelt`); a Stack's cells are `line/0`,
`line/1` in reports, `line[0]` in source. A refusal is not a check: a design the language can't
take stops `fk check`, `fk rates` and the build with one error line naming the file, the line and
the fix.

## Instance parameters

| on | parameter | what |
|---|---|---|
| any instance | `duty=0.5` | caps it: a machine that runs only part of the time (chapter 12's mall) |
| any instance | `dnp` | laid out, not built |
| a machine or row | `modules=speed-module`, `modules=productivity-module\|speed-module` | every slot, or slot by slot; the paste asks for them and `fk rates` counts their speed and productivity. Slots: assembling-machine-2 2, -3 4, electric-furnace 2, chemical-plant 3, oil-refinery 3, electric-mining-drill 3, pumpjack 2, lab 2, beacon 2 (chapter 6) |
| a machine or row | `beacons=8`, `beacon=speed-module-3` | the beacons that reach it when the layout can't say (a row, beacons placed in another module); a placed `Entity<name=beacon, modules=...>` within 3 tiles of a machine's box counts by itself (chapter 6) |
| a machine | `when COND` | the machine runs only while COND holds (chapter 9) |
| a machine | `guard` | its inserters take only its recipe's ingredients, from a red wire the machine drives; a plain `inserter` can't filter (`cannot-filter`) |
| a machine | `keep=200` | = `set NAME.out: logistic=ITEM<200` (the logistic network's count) |
| a machine | `fill=50` | = `set NAME.out -> CHEST: if=ITEM<50` and `set NAME.in: if=ITEM<50` on the chest's red wire: the machine stops at most one craft past N, no robots |
| a row | `inserter=inserter`, `inserter=any` | plain hands, or the lowest hand the network can build, raised by the `hands` step where the flow needs it (the default); `inserter=fast-inserter` means fast and nothing else. A single machine's `inserter=` does nothing: its hands are the router's (`route --hand`) |
| a sealed instance | `size WxH`, `gap N`, `retired`, `temporary until ...` | its frame (a plot's ground), tiles kept free round it, its lifecycle (chapter 12) |
| any kind | `cb.FIELD=`, `bp.FIELD=` | any setting of any building, as the game's blueprint names it (Circuits, below) |

## The library

The language has a small core, and the everyday kinds are source you can read and change: the
**prelude** (`game\prelude.fk`, over the toolkit's) names the kinds every file gets; your
**`game\cells.fk`** holds the cells your lines are made of (chapter 7 lists them, and a test keeps
that list equal to the file); **`game\routing.fk`** holds pieces for steering the router.

### Core kinds

| kind | what | belts |
|---|---|---|
| `Machine<entity, recipe>` | one machine of any crafting building: `Machine<entity=centrifuge, recipe=uranium-processing>`; `machine=` is the same as `entity=`; a furnace may take `ore=` for `recipe=`; a burner takes `fuel=` (coal). Size, power, fluid boxes and rates come from the game's data; a recipe with a fluid the building has no box for is refused (`no-fluidbox`) | the router lays its inserters and pipes (chapter 6) |
| `Entity<name=...>` | any building that moves no items: a lamp, a speaker, a display panel, a beacon, a solar panel, a radar, a roboport on a wire. Placed, powered and wireable; a crafter is refused (write `Machine`) | none (chapter 9, 10) |
| `Chest<provider, limit=4>`, `Chest<requester, item=iron-plate, rate=2/s, request=400, max=>`, `Chest<storage>`, `Chest<plain>` | a logistic chest, the robots' end of a net (`plain`: an iron chest off the network). `limit=` the slots it fills; `rate=` what the robots bring a second (the model's bound); `request=` how many it asks for; a requester takes several items (`item="iron-plate,copper-plate"`) and `trash` sends back what it no longer asks for. A requester can drive a net; a provider or storage chest is only a net's end | an inserter (chapter 1, 2) |
| `Lab`, `Lab<rate=2/min>` | one lab, its inserters laid like an Assembler's | inserters (chapter 2) |
| `Pole`, `Pole<pole=small-electric-pole>` | one pole, pinned, for a cell packed so tight that its pole's tile is part of the design; the power step keeps it and adds the rest | none (chapter 11) |
| `Roboport` | one roboport, 4x4; it takes robots from a net by an inserter (`net load: chest -> port`); `cover robots` counts it | an inserter (chapter 2) |
| `MinerRow<n, resource>` | n electric drills facing a belt between them, 3 tiles a pair, 7 tall; `drill=burner-mining-drill, fuel=coal` burner drills with a fuel belt. On uranium each drill takes acid: the library's `AcidDrillCell`. In a cell whose frame its belt would leave it lays no belt and drops onto the cell's through-belt | the ore leaves at the row's head (chapter 3) |
| `OffshorePump<rate=1200/s>` | water from the lake; it stands on the shore, open to the water (`rot 3` faces west, water west of it) | pipes (chapter 10) |
| `Boiler<fuel=coal>` | 3x2, water in and out at its short ends, steam out of its north side (rot 0), coal by an inserter; 60 steam/s on 0.45 coal/s | pipes (chapter 10) |
| `SteamEngine` | 3x5 standing, steam at both short ends; 900 kW on 30 steam/s, two per boiler | pipes (chapter 10) |
| `Pumpjack<n>` | crude oil. A machine that makes several fluids (`Refinery<recipe=advanced-oil-processing>`) puts each on the net whose consumers take it | pipes, one per fluid |
| `Reactor<read=temperature, signal=S>`, `HeatExchanger`, `SteamTurbine` | nuclear power: a reactor heats the exchangers it touches (`net heat items heat: reactor -> hx`, no route), 10 MW each, 103 steam/s for 1.7 turbines | pipes, an inserter for the cells |
| `InfinityPipe<fluid, rate=>` | a fluid from nowhere: a stand-in where things are free (the lab); in the base the fluid comes from a net | pipes (chapter 6) |
| `Pump<fluid=lubricant>` | one pump, 1x2, ending two nets: `net a: x -> pm` in at its back, `net b: pm -> y` out at its front (rot 0 pushes north). A pipe segment past 320x320 tiles carries nothing (`pipe-too-long`); a pump starts a new one; in a chain, routing.fk's `InlinePump` | pipes |
| `Constant<signals="S=N, T=M">`, `Decider<if, out>`, `Arithmetic<calc, out>` | combinators (Circuits, below) | wires (chapter 9, 10) |
| `Literal<file="x.json">` | entities exactly as given: a blueprint string (`.txt`), JSON `{"bp": "0e..."}`, or an entity list `[{"name": "roboport", "x": 2, "y": 2, "direction": 0}]`, each setting itself (`"request"`, `"recipe"`, `"config"`). Its frame is its entities' box. `fk capture` writes one from anything built (Literals, below) | none (chapter 13, 14) |
| `Bus<belts=4, length, item, load=s11, tap=n26\|s33>` | Rory's bus: `belts` belts of one item running east at rot 0, loads and taps as ports (A bus, below) | one belt per port (chapter 8) |
| `AsmRow<n, recipe>`, `SmelterRow<n, recipe>`, `LabRow<n>`, `SmelterArray<n, recipe>`, `CircuitStack<n>` | rows the toolkit lays for you, the older way. They still build and are not for new designs: each has a library cell that replaces it, and `fk check` names it (`row-cell`). A row is counterflow: its belts attach at its head, the west end at rot 0 | at the head (chapter 1, 3, 5) |
| `TurretRow<n, ammo=firearm-magazine>` | n gun turrets taking ammo from a net (a sink) | an inserter each |

### The prelude

| kind | is |
|---|---|
| `Assembler`, `Furnace`, `ChemPlant`, `Refinery` | `Machine`s: an assembling-machine-1, a stone furnace on coal, a chemical plant, an oil refinery on basic oil processing. `Assembler<recipe=gear, machine=assembling-machine-2>` changes the building |
| `Buffer` | `Chest<plain, hold, chest=steel-chest>`: a chest that is a buffer (Buffers, below) |
| `PowerSwitch`, `Accumulator<read=S>` | for power domains (chapter 10) |
| `RobotCounter` | a roboport that puts its robots on its wire: `signal-X`/`-Y` logistic free/total, `signal-Z`/`-T` construction, `signal-R` roboports |
| `ColourLamp` | a small lamp that takes its colour from `signal-red`, `-green`, `-blue` |
| `Alarm` | a programmable speaker: a siren heard everywhere and an alert on the map; give it `cb.circuit_condition` and its own `alert_message` |
| `Display` | a display panel, always shown, on the map too; give it `bp.text` or `cb.parameters` |

The last four are `Entity` with raw settings. The prelude's `tiers` lines: `Assembler` and
`AsmRow` (the three assemblers), `Furnace`, `SmelterRow` and `SmelterArray` (stone, steel),
`CircuitStack`. A kind or tiers line of the same name in your file wins for that file, in your
`game\prelude.fk` for the run.

### Your `game\routing.fk`

| piece | what | ports |
|---|---|---|
| `Waypoint<item>` | one belt, 1x3: the net passes here | `i`, `o` (inout) |
| `Merge<a, b>` | two belts into one by a splitter; each item stays on its lane | `a`, `b` in, `o` out |
| `Split<item>` | one belt into two by a splitter | `i`, `a`, `b` (inout) |
| `InlinePump<fluid>` | one pump in a pipe, 1x2: `net lub: oil -> pp -> engines` | `i`, `o` (inout) |

They run north to south; `rot 1` turns one to run east to west, `rot 3` west to east. A chained
net's middle stages are such pieces (`net gear: gears -> wp -> red`), one net laid in segments
that the route step's `chain` line and `fk status` show together. Merge is not a chain piece: it
joins two nets.

## Counts the build chooses: `n=auto`, `machine=auto`, `modules=auto`

| | |
|---|---|
| `Stack<cell=FurnaceCell, n=auto>`, `MinerRow<n=auto>`, a row `n=auto` | the `size` step gives it the fewest cells or machines that meet the surface's `target` lines, the factory's `assert` lines and what its consumers take (a net it drives, a chest or labs with a `rate=`); `n=auto 4..30` bounds it; `out\size.txt` says what each count serves (chapter 5) |
| `built=8`, `built=auto` on a Stack | 12 cells laid out, 8 built (DNP, below), or as many as the targets take |
| `machine=auto` | picks from the kind's `tiers` line; one choice for every copy of a cell |
| `modules=auto` | no modules, speed or productivity where the recipe allows; the cheapest pick in raw resources that meets every target (`size --payback 2h` also counts what it draws over two hours) |
| `size`, `does-not-fit` | a line that can't meet what is asked; a sized line past its plot or into another frame (the build stops at `place` when entities would overlap) |

Lines grow from their head, so leave them room. Counts sized to a rate balance a net exactly, so
`fk rates` may name either side of it as what binds.

## Laid out, not built: `dnp`

`gears = Assembler<recipe=iron-gear-wheel, dnp> @(24, 12)`, `dnp mall1, sci3.packs`, or
`Stack<..., built=8>`. A DNP piece keeps its place and its routes, so nothing moves round it; it
is not built and makes, takes and asks for nothing. `out\route.txt`: `dnp N entities laid but not
built`. A built thing on a DNP piece stays until `fk build --confirm`; `fk look` says
`dnp-standing`. `fk dnp FILE INST` writes the line (`--off` takes it off).

## Modules

```
module GearRib size 16x10 {
  port iron : in iron-plate @w        # plates in at the west edge
  port gear : out iron-gear-wheel @e  # gears out at the east edge
  g = Assembler<recipe=iron-gear-wheel> @(4, 3)
  net fe: iron -> g
  net g: g -> gear
}
```

| | |
|---|---|
| sealed | a module with `port` lines or a `size` is sealed: its nets are its own, and the only way in or out is a port; a crossing elsewhere is `crosses-frame`; a machine or belt past the frame is a `FRAME` line. Its inside routes once and is stored (chapter 11) |
| `port NAME : in\|out\|inout ITEM [\| ITEM2] @side[pos][L\|R]` | a lane of one belt end on the frame's edge (it takes no tile): `item \| item2` the two lanes, the same item twice one item on both lanes; `@s5` tile 5 along the south side (from 0), else the side facing what it connects to; the belt crosses from the tile outside to the tile inside. `inout` is a pass-through for a chain piece. A net names a port only in the module that declares it; from outside it is `inst.port` |
| fluid port | `port gas_in : in petroleum-gas @s5`: one pipe across the edge, one fluid, `in` or `out`, no lanes (chapter 6) |
| width | a port is as wide as the belt inside carries its item: both lanes there make it a both-lane port (15/s on yellow) |
| parameters | `module FurnaceCell<plate, ore=iron-ore> size 7x3`: a parameter without a default is required. `$p` stands for its value wherever a word may stand (a port's item, a net's `items`, a parameter value, a signal, a `set` value, inside a quoted string: `if="green.$in < $lo"`), never for an instance, net or port name, a position or the size. Values are words, numbers or lists; `ins=a\|b` is a list, `$ins[0]` one item of it; there is no arithmetic (chapter 6, 7) |
| `duty=`, `dnp` | every module takes them; any other parameter it doesn't declare is refused |
| `buffer` | `module LoopEnd buffer size 1x3 { ... }`: its ports are a buffer's ends (Buffers, below) |
| overlap | two sealed frames may overlap: an `overlap` line, and the shared tiles are both modules' to route on |
| `fk explain FILE INST` | an instance as its entities and nets, with its size; a module's internals are its own and a parent change never re-routes them |

### Stacks: a line of cells

```
module Cell size 9x3 {
  port f_in  : in  iron-plate      @s1       # the feed: in at the south edge,
  port f_out : out iron-plate      @n1       # on to the next cell at the north edge
  port g_in  : in  iron-gear-wheel @n7       # the product: from the cell beyond,
  port g_out : out iron-gear-wheel @s7       # back toward the head
  stack f_in -> f_out, g_out -> g_in
  a = Assembler<recipe=iron-gear-wheel> @(3, 0)
  net f: f_in -> a, f_out
  net g: g_in, a -> g_out
}
line = Stack<cell=Cell, n=12> @(0, 4)
net fe:  feed -> line.f_in
net out: line.g_out -> box
```

| | |
|---|---|
| a cell | a sealed module whose ports come in pairs on opposite edges; `stack NEAR -> FAR` pairs a port on the edge toward the head with the one opposite at the same tile. One cell's FAR meets the next one's NEAR and the net between them lays nothing. Inside, each belt runs straight across, one inserter per machine (chapter 6) |
| `Stack<cell=CELL, n=N, head=HEAD>` | N cells edge to edge, growing toward the FAR edge, an optional head before the first (a head has a FAR port of each pair). Every other parameter goes to the cells; `cell=` may name a kind of a module (chapter 7) |
| `line.P` | the head's port P; with no head, the first cell's NEAR port P or the last cell's FAR port P. The last FAR ports are ends: nothing need come out of them |
| `line[2].P`, `line[-1].P`, `line[*].P` | one cell's port, the last cell's, every cell's (one net with all of them, or `net each`); `[k]` follows a Stack only, `[*]` only in a net (chapter 8) |
| fluid pairs | `stack acid_in -> acid_out` runs one pipe through each cell (`AcidDrillCell`, chapter 6) |
| drills | a drill drops its ore on the tile in front of it, so a cell's through-belt over that tile takes it with no inserter (`DrillCell` 3x4, `DrillPair` 3x7); a drill whose drop tile is a chest or machine of its net feeds it straight |
| a two-item port | `port f_in : in iron-ore \| coal` is a `lanes` pair: two nets may feed its two lanes, one item each, and the module's own `lanes` line inside is enough |
| cells within cells | a cell may hold a Stack (`SmeltColumn`); the outer Stack's `n=auto` counts its cells, the inner takes a number. Where the foot splits into a column, the column gets half of what reaches it (chapter 8) |
| `Pole` | pinned where a cell is so tight no other tile reaches its inserters; a straight belt tunnels under it (`blue6.fk`, chapter 11) |

On a short line the cells nearest the feed run full and the last ones get what is left, and
`fk rates` says so cell by cell (chapter 6's furnace column).

## Nets: the rules

- A machine is on at most one net per item and direction, except that a single machine may
  take one item from several nets, each carrying only that item (`CircuitCell`'s circuit
  assemblers take cable from two cable assemblers). `two-nets` says where this is broken.
- Several drivers merge onto a net; several consumers split off it; the router lays the
  splitters. A net that carries more than one lane (7.5/s on yellow) is laid on both lanes of
  its belt: the router splits its drivers into two groups and side-loads each onto its lane, then
  runs both lanes to every consumer; `width 2` asks for it outright. One driver brings one lane,
  except a two-sided MinerRow.
- Two or more items on one net is a **sushi** net: it runs while each item comes no faster than it
  is used (`CLOG` otherwise); into a machine it must take every item (`SUSHI_SINK`); into a chest
  the chest goes best without `limit=` (`SUSHI_LIMIT`) with the limit at each maker. One inserter
  from the source straight into each sink can't clog. Chapter 9 has the loop that works.
- Two single-item nets feeding the same machines on two belts are the case for `lanes`
  (`could-pair`); `SmelterArray` and `CircuitStack` side-load two nets onto their own feed belts
  and want them apart.
- A **ribbon** (`net each`, `bundle`, `width 8`) is laid as one: belts adjacent and in order, turns
  nested, each belt going under only what blocks its own line with a tunnel of its own (a tunnel
  reaches as far as the fastest tier `route --ug` allows: yellow 5, red 7, blue 9, turbo 11), each
  belt joining and leaving from the side. A ribbon that finds no path is reported unrouted, every
  net of it. Refused: nets running different ways, a net in two ribbons, a fluid net, a `lanes`
  pair, a bus, a bundle of one, an odd width (chapter 8).

## Buffers

```
b = Buffer                          # a steel chest; Buffer<chest=iron-chest, bar=20, item=>
net ore_: drills -> b               # BUFFER b: +60.0/min iron-ore (filling: in 60.0, out 0.0)
net feed: b -> smelt                # draining when smelt takes more than the drills bring
```

A buffer is a role: one net fills it, another drains it. The rate model treats it as an unlimited
supply to the nets it drives and an unlimited sink to the nets that feed it, and gives what it
gains or loses a minute as a `BUFFER` line. `module NAME<p> buffer size WxH { ... }` makes a
module whose ports are a buffer's ends (its inside is yours; it needs ports). A buffer never gets
`no-taker` or `no-rate`; a buffer whose nets are all `limited` gets no `SUSHI_LIMIT`; a sushi net
with a buffer on it gets no `SUSHI_SINK`; `CLOG` stays. A buffer fed by an unbounded source fills
without end on paper unless that net is gated (`when`): then the gate tops up what goes out and the
line says `steady` (chapter 9).

**The amount.** `when W.ITEM < auto` on a sushi gate works the number out from the netlist: every
machine on the loop that takes ITEM adds 40 s of its take, and at least two crafts; `fk check`
says it (`advice gate-auto`). A condition may compare with a module parameter (`< $amount`). A
mall whose recipes change by circuit fixes none in the netlist: write the amount. `auto` on a
machine's own `when` is refused.

## A bus

```
bus = Bus<belts=4, length=54, item=iron-plate, load=s11, tap=n26|s33> @(0, 10)
net iron1: smelt -> bus.load1          # a smelter line loads the bus
net fe:    bus.tap1 -> gears, belts    # a tap feeds machines
```

| | |
|---|---|
| frame | `belts` + 2 tall: the belts are rows 1..4, rows 0 and 5 where the ports' belts meet it. A port is a side and a tile along the bus from its head: `s11` a load from the south at x 11; `n`/`s` for a bus running east or west, `e`/`w` for south or north. Nets name the ports in order: `bus.load1`, `bus.tap1`, `bus.tap2` |
| staircases | a load merges into every belt by a staircase of splitters, one per belt, filling the far belt first; a tap takes from the far belt first. Each staircase takes `belts` tiles along the bus beside its port; no two ports share one |
| the router | lays the belts to a load and from a tap, never the bus itself; a port no net names keeps its staircase |
| the model | the bus is a net of its own, named as the instance, a pool: what the loads bring is what the taps share. In the game the first tap along the bus takes its fill first, and a load after a tap never reaches it (`partly-connected`) |
| connection list | `Bus<belts=4, item=iron-plate>(in1: smelt.plate, fg: gears.iron, fb: belts.iron)`: each entry a named net in port order, direction from the netlist. `in1..in{belts}` is a mouth (the belt's own end at the head); every other entry is a staircase standing in front of what it connects to, on the bus's north side at rot 0. `left NAME:` / `right NAME:` choose a one-lane underground tap (`dual` the belt strap). A tap before any load is `tap-too-early` (chapter 8, `bus4ribs.fk`) |
| `fk contract FILE` | every sealed module's ports with the rate each needs and gets (chapter 8, 12) |

A yellow belt carries 15/s on its two lanes; a line that puts its plates on one lane brings 7.5/s
to a load (`too-thin` says where a lane is full). A second item is a second bus beside it.

## Circuits

Wires, conditions and limits are written like nets; the build lays them and pastes them with the
rest, and the next build writes a changed one onto what stands.

### Properties on objects: `set`

```
set belt.out            : logistic=transport-belt<200   # the hands that take belts out
set ins.out -> ibox     : if=inserter<50                # only the hand whose sink is ibox
set ins                 : logistic=inserter<50          # the machine itself (stops crafting)
set s                   : read=hold-all                 # a net's belt, read whole by its wire
```

| selector | what |
|---|---|
| `NAME` | the instance's buildings |
| `NAME.out`, `NAME.in` | the hands that take its product out (and down a net it drives), the hands that feed it |
| `... -> NAME` | narrowed to the hands whose sink is that instance; use it whenever a maker feeds more than one consumer |
| `NET` | the net: `read=hold-all` reads its whole belt (`hold` one tile, the default; `pulse`) |

Properties: `logistic=ITEM<N` (the logistic network's count, no wire), `if=ITEM<N` (a circuit
condition; name the chest with `-> CHEST` so it reads that chest), `filter=`, `read=`, `signal=`,
`signals=`, and any raw field `cb.FIELD=` or `bp.FIELD=`. Every `set` reports the hands it hit
(`on 2 hands (~out/i0, ~out/i1)`); one that matches nothing is `no-match`. A limit does not quiet
`CLOG`: `limited` does (chapter 9).

### Any setting of any building: `cb.` and `bp.`

`cb.FIELD=VALUE` writes one field of the building's control behaviour (`cb.read_robot_stats=true`,
`cb.circuit_condition="iron-plate<50"`, `cb.mode=2`), `bp.FIELD=VALUE` any other blueprint field
(a speaker's `parameters`, a display's `text`, a train stop's `station`). In a `set` line or as a
parameter of an instance or a kind. A value is written like JSON: a number, `true`/`false`, a
quoted string or a bare word, `{key: value}`, `[value, ...]`, over several lines if needed. A
string in a field that holds a signal (`_signal`, `signal_id`, `signal`, `icon`) becomes the game's
signal (`icon: iron-plate`); one in a field that holds a condition (`..condition`) is `S<N` as `if=`
takes it. `$p` works in a raw value. Refused (`CIRCUIT`): `bp.` for a field the build writes itself
(`name`, `position`, `direction`, `recipe`, `tags`, `wires`, `type`, `control_behavior`), a field set
twice, a condition that isn't one. The rate model takes a raw condition as always open. Chapter 9's
`belt-switch.fk` sets a radar's channel this way.

### Wires, signals and conditions

```
signal temp = signal-T                            # a signal, pinned to a game signal
reactor = Reactor<read=temperature, signal=temp>  @(2, 5)
k       = Constant<signals="stop=1, iron-plate=50"> @(0, 0)
wire fuel_ctl red: reactor -> fuel                # drivers -> consumers; red or green
net fuel: cells -> reactor  when fuel_ctl.temp < 550       # a gated net
gears = Assembler<recipe=iron-gear-wheel> @(9, 0) when network.iron-gear-wheel < 400
```

| | |
|---|---|
| `wire NAME red\|green: drivers -> consumers` | one network of one colour. Drivers: a chest (its contents), `Reactor<read=temperature, signal=S>`, `Accumulator<read=S>` (its charge 0..100), `Constant`, a combinator's `out`, a net (one of its belts reads what is on it), any building whose `cb.` settings make it send (a radar on a channel). Any building with a circuit connector can be an end; one without is `no-wire` |
| `when A < N` | on a net: every inserter and pump of its route moves only while it holds (none: its trunk belt). On an instance: the machine itself. `A` is `WIRE.SIGNAL`, `network.ITEM` (the logistic network, no wire) or `INSTANCE.SIGNAL` (that chest or machine, on a red wire of its own); `<`, `>`, `<=`, `>=`, `=`, `!=`; against a whole number, another signal on the wire, `$p`, or `auto` on a sushi gate |
| signals | every signal the game has, by name (virtual, items, fluids, recipes, entities, qualities, ...), or the names you declare with `signal`. `signal-each`, `-anything`, `-everything` read in a condition but never go out on a wire (`no-signal` if sent) |
| reach | a wire reaches 9 tiles (a big pole 32, a power switch 10); a longer one is carried on the poles of the area, the build adding a pole on a free tile where there is a gap (`circuit-poles/p1`) and saying so (`wire stock red: 3 entities, 5 wires; on poles: ... over 3 poles (3 added)`); only when no chain joins two ends is it `wire-too-far` |
| checks | `no-signal`: a condition reads a signal nothing on its wire makes; `no-wire`: a wire nothing drives or reads; `wires-share`: two wires of one colour on one entity (their signals add); `gate-open`: a gated net the rate model takes as always open (chapter 9) |

The build matches the world's wires to these after the paste: it adds the missing ones and cuts
the ones between your entities that the source lacks (`out\apply.txt`: `wires: 3 added, 1 cut`).

### Deciders and arithmetic combinators

```
signal A = signal-A
signal S = signal-S
latch  = Decider<if="green.A < 20 or (red.S > 0 and green.A < 80)", out="S=1"> @(6, 2)
double = Arithmetic<calc="green.A * 2", out="S"> @(9, 2)
wire level green: acc -> latch.in
wire hold  red:   latch.out -> latch.in       # its own output back in: a latch
```

| | |
|---|---|
| `Decider<if="...", out="...">` | `if` as the game's decider takes it: `or` between groups of `and` (`and` binds first; a group may stand in parentheses after an `or`; `(A or B) and C` is refused: write `A and C or B and C`); each comparison `SIGNAL OP N` or `SIGNAL OP SIGNAL`; `red.S`/`green.S` read one colour, bare `S` both. `out`: `S=1` sends 1, `S` the input's count, several with commas |
| `Arithmetic<calc="A OP B", out="S">` | `A` a signal (or `each`), `B` a signal or a whole number, `OP` one of `+ - * / % ^ << >> AND OR XOR`, with spaces |
| `x.in`, `x.out` | a combinator's two sides: a wire's consumer end is `x.in`, its driver end `x.out`; a bare `x` takes the side its end needs; `out` back to `in` is a latch's memory |
| from outside | a wire inside an unsealed module is named by its path: `shed.hold.S` in a `when`, `shed.latch.in` as a wire's end; a sealed module keeps its inside to itself (the library's `Hysteresis`, chapter 10) |

### What a loop is meant to do: `behave`

```
behave fuel_saver on fuel_ctl {
    state coasting: T >= 550 and not reactor.burning
    require 550 > hx.min_temperature
    expect reactor.status = no_fuel in coasting
    cycle full_load: burning 200 s, coasting 450 s, cells 1 per 650 s
    fails dark: T < 500 and not reactor.burning
}
```

A control loop makes states a rate model can't show (a hot reactor with no fuel). `require` lines
with numbers the catalog knows are checked (`require-not-met`); a gated net whose wire has no
`cycle` is a `gate-open` line.

### What the language doesn't write: a Literal

Selector combinators, a recipe set by a signal and requests set by a signal are not in the
language. A `Literal` whose file is a blueprint string keeps every one of them, with its settings
and wires, as written (a blueprint Literal keeps `if=`, `read=` and its wires; a wire longer than
9 tiles is dropped by the game). Three things measured 2026-10-30: an assembler keeps no
`input_networks`/`output_networks`; a requester takes its requests from the circuit with
`"control_behavior": {"circuit_mode_of_operation": 1}`; a decider outputs `each` only when a
condition reads `each`.

## Literals

| | |
|---|---|
| names | a JSON Literal may name its machines: `"names": {"asm10": 37}` (a name to an entity's index, from 0). `automall.asm10` is then a net end; `fk capture` writes them (`asm1`, `furnace1`, `drill1` ...). A named **belt** is a belt end: a net into it is routed into the belt from behind (name the first belt), a net out of it starts in front (name the last) and carries what the net declares (`net out items iron-plate: blk.last -> box`); a splitter or underground likewise (an entry takes a net in, an exit gives one out) (chapter 14) |
| wrapped machines | a Literal of one machine with its own inserters, named: a cell places it and names it in its nets (`net i: in_in -> w.a, in_out`), and the router lays the cell's belts past its hands or a drill's drop tile. A blueprint holds no ore: `Literal<file="drill.json", resource=$resource>` (`WrappedDrillCell`) |
| ports | `"ports": {"heavy": {"carrier": 12, "side": "e"}}`: a pipe or belt of the file and the frame edge it meets (`n`, `e`, `s`, `w`, turned with the Literal). `plant.heavy` is a net end on that edge; the carrier's whole group of touching pipes or belts carries the net. `fk capture` writes a port for each belt and pipe that crosses the edge, named by place (`n1` the westmost on the north edge). A port carries what the Literal's machines put on it or take off it; a net of another item there is `cannot-carry` |
| inserter vectors | (Bob's inserters, Sea Block) a listed entity writes its own pickup and drop as tiles from its tile: `"pick": "0,-1", "drop": "1,0"`; one the force can't set is `needs-research`. The router lays its own hands this way where it helps |
| snap | an entity whose centre doesn't fit its size is moved to the nearest centre that does (`literal-snap`) |
| anchored | `fk capture --from ID\|AREA` writes a file anchored where it stands (`"anchor": [10, 10]`, each entity's `wx`, `wy` relative to it), so `Literal<file="..."> @(10, 10)` puts every entity back exactly where it is and new source mates with it (chapter 14) |

## Power

The build's `cover power` step lays the poles every factory needs and `floorplan --bridge-power`
joins factories to the plant's grid: put no poles in the source (`Pole` pins one where a cell
needs it).

```
power main: cover all                 # everything no other domain names
power labs: cover field               # the lab field alone
acc  = Accumulator<read=A> @(30, 4)
shed = Hysteresis<in=A, out=S, lo=20, hi=80> @(32, 4)
wire level green: acc -> shed.latch.in
sw = PowerSwitch @(36, 4) when shed.hold.S = 0
copper feed: main -> sw.left
copper load: sw.right -> labs
```

| | |
|---|---|
| `power NAME: cover A, B` | a domain: those instances with their whole insides (a route's inserters go with the machine they serve); `cover all` everything no other domain names. One `power` line (or none) is one network |
| poles | each domain gets its own (`power-labs/p0`), never wired across domains and never where a pole's supply area reaches another domain's machines (`domain-overlap` when none is left) |
| `copper NAME: A -> B` | a copper wire between two domains, or a domain and a switch's side, from the nearest pole in reach or a bridge pole the build adds; two domains joined with no switch are one network. One that can't be laid is a `copper` line |
| `PowerSwitch` | sides `sw.left`, `sw.right`; closed while its `when` holds |
| `Accumulator<read=S>` | its charge, 0 to 100, as S; the game charges a network's accumulators together, so one reads them all |
| checks | `no-source` (no generator and no copper line to one), `power-short` (its machines want more than its generators make), `switch-never`, `domain-overlap`, `copper`; `NO_POWER` (the factory's poles reach no grid with generation) (chapter 10) |

Copper lines and domains are written at the top level, not inside a module. The run stats split
power by domain (`power by domain: labs made 0 W, used 1.8 MW; main made 3.1 MW, used 1.2 MW`).
