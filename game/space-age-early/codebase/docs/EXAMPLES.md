# Examples: the toolkit, one idea at a time

Fourteen chapters, each a file in `docs\examples\` that builds, the command, the lines it prints,
and the one new thing. The files are the truth; this page quotes them. `START.md` walks the first
one from copy to look. `LANGUAGE.md` and `TOOLKIT.md` are the reference: each row there names the
chapter that uses it.

Every example builds where it stands (`fk check docs\examples\gears.fk`) and copied into `game\`
(the ones that import cells take them from `cells.fk` beside them; `docs\examples\cells.fk` is the
library as the toolkit ships it, and in `game\` yours is used). The output quoted here is a paper
build: the template's build script through `report_checks`, no terrain, the plot size the chapter
names. Two lines appear in almost every chapter and are explained once:

- `NO_POWER nauvis NAME: its grid reaches no generator (bridge it: floorplan --bridge-power)`: on
  paper the file has no generator. In your base the `floorplan --bridge-power` step joins the
  factory's poles to the plant's grid, and the line goes away.
- `hand=any: fast-inserter (no stock read yet: ...)`: the route step lays the lowest hand the
  network can build, and on paper it has not read the network yet.

Game steps (`fk build`, `fk look`, `fk lab`, `fk paste`) are shown by the shape of their lines,
with `N`, `X,Y` and `T` where the game fills in numbers.

## 1. gears.fk: one machine, one net

`docs\examples\gears.fk`, plot 16x16:

```
inbox = Chest<requester, item=iron-plate, rate=1.25/s> @(2, 2)
gears = AsmRow<n=2, recipe=iron-gear-wheel>           @(6, 2)
box   = Chest<provider, limit=4>                      @(2, 12)

net plates_in: inbox -> gears
net gear:      gears -> box
```

```
fk check gears.fk
fk rates gears.fk
fk build --to report_checks
```

```
check gears: 4 machines, 2 nets, 0 check lines
```

```
  instance           kind           n   util  makes /min                             binding
  inbox              Chest          1  1.000  iron-plate 75.00                       full speed
  gears              AsmRow         2  0.313  iron-gear-wheel 37.50                  iron-plate short on plates_in
  box                Chest          1  1.000                                         sink
```

```
gears: 2 nets: 2 connected, 0 open, 0 unrouted; 0 SPILL; 34 entities, 30 edges, 0 underground pairs
```

**The idea.** A factory file names machines and nets; the toolkit lays everything between them.
`rate=` on a requester is a promise about the robots, and the rate model holds the row to it:
37.5 gears/min from 1.25 plates/s. `limit=4` on the provider fills four slots and then stops its
inserter, so gears never pile up in the network.

**What goes wrong here.** `short-feed gears net plates_in: iron-plate: its drivers make 75.0/min
at most, its consumers would take 240.0/min, so they run at about 31% for it; `fk plan iron-plate
240/min` sizes the drivers`: the row could take more than the chest brings. Raise `rate=`, or
accept 31%. `advice row-cell gears: a new AsmRow could be Stack<cell=AsmCell1, n=2, ...>`: advice,
not a fault; chapter 3.

## 2. beltless.fk: robots and inserters, no belts

`docs\examples\beltless.fk`, plot 13x7:

```
fe   = Chest<requester, item=iron-plate, request=400>   @(0, 0)
gear = Assembler<recipe=iron-gear-wheel>                 @(2, 0)
red  = Assembler<recipe=automation-science-pack>         @(6, 0)
lab1 = Lab                                               @(10, 0)
cu   = Chest<requester, item=copper-plate, request=200>  @(6, 4)

net fe:    fe -> gear
net gears: gear -> red
net cu:    cu -> red
net packs: red -> lab1
```

```
fk check beltless.fk
fk build --to report_checks
```

```
check beltless: 5 machines, 4 nets, 2 check lines
  no-rate fe: iron-plate from the logistic network has no rate=: everything downstream of it is unbounded. The 1 machine on it takes 2/s: `fk stock` says what the network holds, so compare that with this need before you size it
  no-rate cu: copper-plate from the logistic network has no rate=: everything downstream of it is unbounded. The 1 machine on it takes 0.1/s: `fk stock` says what the network holds, so compare that with this need before you size it
```

`out\route.txt`:

```
beltless: 4 routed, 0 kept (0 pinned from the committed .fkp), 0 failed, 1 pass
  routed    cu: 1 -> 1, 1 inserter (direct insertion)
  routed    fe: 1 -> 1, 1 inserter (direct insertion)
  routed    gears: 1 -> 1, 1 inserter (direct insertion)
  routed    packs: 1 -> 1, 1 inserter (direct insertion)
```

```
beltless: 4 nets: 4 connected, 0 open, 0 unrouted; 0 SPILL; 11 entities, 8 edges, 0 underground pairs
```

**The idea.** A machine one to three tiles from the next gets one inserter and no belt: a fast
one across a one-tile gap, a long-handed one across two or three. This is the shape of a first
factory: chests in, machine to machine by hand, 6 packs/min on paper and measured 6.00/min in the
lab. `request=400` says how many the chest asks for; `rate=` says how fast they come.

**What goes wrong here.** `no-rate fe: ... has no rate=`: without a rate the model treats the
chest as endless, so nothing downstream is bounded; `fk stock` says what the network holds.
`no-robots beltless cu/c0: no free 4x4 spot inside the factory's frame reaches it` (the `cover
robots` step): a 13x7 plot has no room for a roboport, so the chests must be inside a roboport's
area already. In the base they are, in the hub.

**Aside: robot-feed.fk.** The kit's robots end up as items in a storage chest whenever a build
takes up the roboport that held them. `docs\examples\robot-feed.fk` puts them back:

```
port  = Roboport @(0, 0)
botin = Chest<requester, item="logistic-robot", request=50, rate=1/s> @(5, 1)
net load: botin -> port
```

`fk check robot-feed.fk`: `check robot-feed: 2 machines, 1 nets, 0 check lines`. The route is
`routed    load: 1 -> 1, 1 inserter (direct insertion)`: a roboport takes robots from an
inserter; `robot-feed: 1 nets: 1 connected, 0 open, 0 unrouted; 0 SPILL; 4 entities, 2 edges, 0
underground pairs`. A second chest with `item="construction-robot"` does the same for those.

## 3. smelting.fk: a row, and the cell that replaces it

`docs\examples\smelting.fk`, plot 26x26 on an iron patch:

```
ore   = MinerRow<n=4, resource=iron-ore>   @(16, 9, rot 2)
coal  = MinerRow<n=2, resource=coal>       @(21, 9, rot 2)
smelt = SmelterRow<n=8, recipe=iron-plate> @(10, 12)
box   = Chest<provider, limit=4>           @(4, 22)

net ore_:   ore -> smelt
net fuel:   coal -> smelt
net plates: smelt -> box
```

```
fk check smelting.fk
fk rates smelting.fk
```

```
check smelting: 15 machines, 3 nets, 1 check lines
  could-pair net fuel and ore_: both feed smelt (8 machines) on belts of their own, a lane each: one belt carries both (`lanes fuel | ore_`), one lane each, and each machine takes from both. Two half-empty belts otherwise
  advice row-cell smelt: a new SmelterRow could be Stack<cell=StoneFurnaceCell, n=8, plate=iron-plate, ore=iron-ore>, a Stack of the library's StoneFurnaceCell (import it from the kickoff template's game\cells.fk). Rows still build, and new lines are Stacks of cells
```

```
  instance           kind           n   util  makes /min                             binding
  ore                MinerRow       4  1.000  iron-ore 120.00                        full speed
  coal               MinerRow       2  0.144  coal 8.64                              coal: fuel takes 0.14/s
  smelt              SmelterRow     8  0.800  iron-plate 120.00                      iron-ore short on ore_
  box                Chest          1  1.000                                         sink
```

```
smelting: 3 nets: 3 connected, 0 open, 0 unrouted; 0 SPILL; 138 entities, 143 edges, 0 underground pairs
```

**The idea.** A line of machines is a **Stack of cells**. `SmelterRow` is a row the toolkit lays
for you; the `row-cell` advice writes the Stack that replaces it, cell by cell:

```
import StoneFurnaceCell from "cells.fk"
smelt = Stack<cell=StoneFurnaceCell, n=8, plate=iron-plate, ore=iron-ore> @(10, 12)
net ore_: ore -> smelt.f_in
net fuel: coal -> smelt.f_in
lanes ore_ | fuel
net plates: smelt.pl_out -> box
```

A cell is a sealed module whose belts run straight across it, edge to edge, so `n` cells in a
line make one long line; its ports (`f_in`, `pl_out`) are where the belts cross. `game\cells.fk`
is yours to read and change, and chapter 7 lists every cell in it. `rot 2` turns the miner rows
180 degrees about their `@(x, y)`, so their ore leaves west, where the router has room. Drill
rows need ore under them: put the plot on the patch.

**What goes wrong here.** `could-pair net fuel and ore_: ... one belt carries both (`lanes fuel |
ore_`)`: two single-item nets into the same machines on two belts, a lane each; chapter 4 pairs
them. `short-feed smelting net ore_: iron-ore: its drivers make 120.0/min at most, its consumers
would take 150.0/min, so they run at about 80% for it`: four drills for eight furnaces.

**Aside: smelter-array.fk.** `SmelterArray<n=24, recipe=iron-plate>` is the older stack row,
furnaces on both sides of each plate belt; the library's `SmelterPair` replaces it. On paper
(`fk rates smelter-array.fk`) 24 furnaces make `iron-plate 450.00` a minute at full speed into
three chests, since one chest's inserter takes 2.5/s (`smelter-array: 3 nets: 3 connected, 0
open, 0 unrouted; 0 SPILL; 345 entities, 351 edges, 2 underground pairs`). At plot 33x50 the
power cover has no spot for five of the sixteen drills: `NO_POWER smelter-array ore/m10: all 81 spots a
medium-electric-pole could power it from are taken: 54 on entities (ore); 27 outside the 33x50
frame`. A wider plot, or fewer drills in the row, is the answer.

## 4. shared-belt.fk: two items on one belt

`docs\examples\shared-belt.fk`, plot 26x23: the same smelting, one line added:

```
net ore_:   ore -> smelt
net fuel:   coal -> smelt
net plates: smelt -> box
lanes ore_ | fuel
```

```
fk check shared-belt.fk
fk build --to report_checks
```

```
check shared-belt: 15 machines, 3 nets, 0 check lines
```

`out\route.txt`:

```
shared-belt: 2 routed, 0 kept (0 pinned from the committed .fkp), 0 failed, 1 pass
  routed    ore_|fuel: 2 -> 1, trunk 8,8 E (ore_ on the left lane, fuel on the right), ore_ side-loads stem belt 0 at 8,7; fuel side-loads stem belt 0 at 8,9; ore_: 4 belts, 0 underground ends, 0 splitters; fuel: 12 belts, 2 underground ends, 0 splitters; both: 6 belts, 0 underground ends, 0 splitters
  routed    plates: 1 -> 1, 9 belts, 0 underground ends, 0 splitters, 1 inserters
```

```
shared-belt: 3 nets: 3 connected, 0 open, 0 unrouted; 0 SPILL; 110 entities, 110 edges, 1 underground pairs
```

After the build, `fk rates shared-belt.fk` adds the routed section, the belts and inserters as
laid, served in belt order:

```
routed shared-belt (V2: the lanes and inserters as laid): 177 connectors and machines; the machines settled in 3 rounds; on shared-belt.fkp as last laid out
  instance           nominal  routed  lowest  limited by
  ore                  1.000   1.000   1.000  as nominal
  coal                 0.144   0.144   0.000  2 of 2 blocked (output backs up)
  smelt                0.800   0.800   0.000  2 of 8 starved of iron-ore
```

**The idea.** A belt has two lanes and a net is one lane. `lanes A | B` puts two nets on one belt,
A on the left lane and B on the right (looking downstream): the router brings them together once,
side-loading each onto its lane, and every furnace's one inserter takes both. They stay two nets:
a lane that backs up holds only its own item, so a shared belt never gets `CLOG`. The `could-pair`
line of chapter 3 is gone.

**What goes wrong here.** `WRONG-LANE` is the loud line of lanes: items sit on a lane the layout
gives another net, two nets on one lane go to different sinks, or items are on the closed side of
a side-loaded underground. It comes from the routed rates after a build; the layout moved an
inserter or a side-load onto the wrong lane, so give the join room or move the driver. On a
short line the far machines starve first: `2 of 8 starved of iron-ore` is the belt order, and
more drills is the answer.

## 5. red.fk and sized.fk: science into labs, counted by the build

`docs\examples\red.fk`, plot 30x30:

```
iron   = Chest<requester, item=iron-plate, rate=1/s>   @(2, 2)
copper = Chest<requester, item=copper-plate, rate=1/s> @(2, 12)
gears  = AsmRow<n=1, recipe=iron-gear-wheel>          @(8, 2)
red    = AsmRow<n=3, recipe=automation-science-pack>  @(8, 12)
labs   = LabRow<n=3>                                  @(8, 23)

net plates: iron -> gears
net copp:   copper -> red
net gear:   gears -> red
net packs:  red -> labs

assert red.rate >= 9/min
```

```
fk rates red.fk
```

```
  instance           kind           n   util  makes /min                             binding
  iron               Chest          1  0.600  iron-plate 36.00                       iron-plate: plates takes 0.60/s
  copper             Chest          1  0.300  copper-plate 18.00                     copper-plate: copp takes 0.30/s
  gears              AsmRow         1  0.300  iron-gear-wheel 18.00                  iron-gear-wheel: gear takes 0.30/s
  red                AsmRow         3  1.000  automation-science-pack 18.00          full speed
  labs               LabRow         3  1.000                                         sink

  asserts
  PASS red.rate >= 9/min: 18.000/min
```

```
red: 4 nets: 4 connected, 0 open, 0 unrouted; 0 SPILL; 92 entities, 88 edges, 0 underground pairs
```

`docs\examples\sized.fk`, plot 42x32, is the same file with `n=auto` on every row,
`LabRow<n=auto, rate=6/min>` and `assert red.rate >= 60/min`. `fk build --to size` writes
`out\size.txt`:

```
sized: gears n=1 (auto), red n=10 (auto), labs n=10 (auto)
  3 n=auto rows, 21 machines; every target and assert holds, and every net of an n=auto row carries what its consumers ask; 4 model solves, 0 growth steps after the seed

  row                  kind           n seed   bounds  serves
  gears                AsmRow         1    1   1..100  its low bound, all of it used: net gear: iron-gear-wheel for red
  red                  AsmRow        10   10   1..100  assert red.rate >= 60/min
  labs                 LabRow        10   10   1..100  labs takes the automation-science-pack of net packs (for assert red.rate >= 60/min)
```

```
sized: 4 nets: 4 connected, 0 open, 0 unrouted; 0 SPILL; 228 entities, 228 edges, 0 underground pairs
```

**The idea.** `assert INST.rate >= R` is a check on paper: `fk rates` prints `PASS` or a
`rate-not-met` line. `n=auto` hands the count to the build's `size` step: the fewest machines
that meet the asserts, the surface's `target` lines and what the line's consumers take. Ten red
assemblers make the asserted 1 pack/s, one gear assembler the gear/s they take, ten labs at 6
packs a minute each take what the rows make. Before you write a factory, `fk plan` says what a
rate takes down to the ore:

```
fk plan automation-science-pack 1/s
```

```
  item                            /s       /min  recipe                        machine               machines build
  automation-science-pack      1.000      60.00  automation-science-pack       assembling-machine-2      6.67     7
  copper-plate                 1.000      60.00  copper-plate                  stone-furnace             3.20     4
  iron-gear-wheel              1.000      60.00  iron-gear-wheel               assembling-machine-2      0.67     1
  iron-plate                   2.000     120.00  iron-plate                    stone-furnace             6.40     7

  raw                             /s       /min  source                        machine               machines build
  iron-ore                     2.000     120.00  mined                         electric-mining-drill     4.00     4
  copper-ore                   1.000      60.00  mined                         electric-mining-drill     2.00     2
  coal                         0.216      12.96  mined                         electric-mining-drill     0.43     1
```

**What goes wrong here.** `could-pair net copp and gear: both feed red (3 machines) on belts of
their own, a lane each: one belt carries both (`lanes copp | gear`)`: the pairing of chapter 4,
left as it is here so the two rows keep their own belts. `size` is the line a sized row prints
when it cannot meet what is asked (a requester at its `rate=`, a row at the top of its bounds),
and `does-not-fit` when a sized row runs past its plot: the rows grow east from their head, so
give them room. `advice row-cell red: a new AsmRow could be Stack<cell=AsmCell, n=3,
recipe=automation-science-pack, ins=copper-plate|iron-gear-wheel, out=automation-science-pack>`:
the cell takes two ingredients on one belt, a lane each, which also answers `could-pair`.

## 6. plastic-cell.fk: a cell of your own, and the lab

`docs\examples\plastic-cell.fk`, plot 200x200:

```
kind Plastics = Machine<entity=chemical-plant, recipe=plastic-bar>

module PlasticCell<fuel=coal> size 12x5 {
  port c_in    : in  $fuel         @s0
  port c_out   : out $fuel         @n0
  port gas_in  : in  petroleum-gas @s5
  port gas_out : out petroleum-gas @n5
  port p_in    : in  plastic-bar   @n11
  port p_out   : out plastic-bar   @s11
  stack c_in -> c_out, gas_in -> gas_out, p_out -> p_in
  plant = Plastics @(5, 1)
  net c: c_in -> plant, c_out
  net g: gas_in -> plant, gas_out
  net p: p_in, plant -> p_out
}

gas   = InfinityPipe<fluid=petroleum-gas, rate=80/s> @(7, 26)
coal  = Chest<requester, item=coal, rate=4/s> @(2, 26)
line  = Stack<cell=PlasticCell, n=4> @(2, 4)
box   = Chest<provider, limit=8> @(18, 26)
net fuel:    coal -> line.c_in
net gas_:    gas -> line.gas_in
net plastic: line.p_out -> box
```

```
fk check plastic-cell.fk
fk rates plastic-cell.fk
fk build --to report_checks
```

```
check plastic-cell: 31 machines, 24 nets, 0 check lines
```

```
  instance           kind           n   util  makes /min                             binding
  gas                InfinityPipe   1  1.000  petroleum-gas 4800.00                  full speed
  coal               Chest          1  1.000  coal 240.00                            full speed
  line/0/plant       Plastics       1  1.000  plastic-bar 120.00                     full speed
  line/1/plant       Plastics       1  1.000  plastic-bar 120.00                     full speed
  line/2/plant       Plastics       1  1.000  plastic-bar 120.00                     full speed
  line/3/plant       Plastics       1  1.000  plastic-bar 120.00                     full speed
  box                Chest          1  1.000                                         sink
```

`out\route.txt` begins:

```
plastic-cell: compiled  stored line/0 (out/compiled): laid from there wherever each stands next
plastic-cell: 24 routed, 0 kept (0 pinned from the committed .fkp), 0 failed, 1 pass
  routed    fuel: 1 -> 1, 1 belts, 0 underground ends, 0 merges, 1 inserters
  routed    gas_: 1 -> 1, 2 pipes, 0 pipe-to-ground pairs
```

```
plastic-cell: 24 nets: 24 connected, 0 open, 0 unrouted; 0 SPILL; 164 entities, 93 edges, 0 underground pairs; 76 fluid boxes, 71 fluid joins, 3 pipe-to-ground pairs
```

**The idea.** When the library has no cell for a machine, write one. `Machine<entity, recipe>` is
any crafting building of the game, and `kind` gives it a name. A module with `size` and `port`
lines is sealed: its nets stay inside its frame and meet the outside only at ports. `port NAME :
in|out ITEM @s5` is a place on the frame's south edge, tile 5 along it; `stack NEAR -> FAR` pairs
the two ends of one belt, so the next cell's `c_in` meets this one's `c_out` and the net between
them lays nothing. A port of a fluid is one pipe across the edge: the gas runs up through all four
cells on one pipe. `$fuel` is a module parameter, set per instance or by the Stack. The reports
name each cell's machine `line/0/plant`, `line/1/plant`.

**On the lab.** `InfinityPipe` is a stand-in: free gas where things are free. The lab is such a
surface, and every example builds alone there:

```
fk lab plastic-cell
```

In the game the lines have this shape: `alone plastic-cell on lab (free): slot X,Y (WxH tiles
for 19x27), workspace lab\plastic-cell (from game)`, then `emptied first: N entities; C chunks
generated under the slot`, the route and report_checks lines of the build, `placement
plastic-cell: recorded in out/placements/lab.json (fk look plastic-cell, fk diff plastic-cell)`,
`stand-in supply: power ...; requesters stocked: 1 coal; burners given a first load: 0`,
`stand-in feed: N open ports (...)` and `next: let it run a minute or two, then `fk lab
plastic-cell --measure 60`; Rory sees it with /lab at X,Y`. Chapter 13 has the rest of the lab.

**What goes wrong here.** A machine standing on the tile just inside or outside a port leaves
its route nowhere to meet it: a `port` line from `fk check` names it. A net that names a port
from outside the module that declares it is refused with the `port` line to add. `no-fluidbox`
refuses a fluid recipe on a building with no box for it (an assembling-machine-1 never runs
lubricant).

**Aside: furnace-column.fk.** `docs\examples\furnace-column.fk` is the same shape for electric
furnaces: a 7x3 `FurnaceCell` with `modules=productivity-module`, `Stack<cell=FurnaceCell, n=8>`,
six drills. `fk rates furnace-column.fk`: the first five furnaces full (`iron-plate 36.45`), the
sixth at a third, the last two `iron-ore short on col/0/o`: 3 ore/s is ore for 5.3 furnaces, and
the belt serves them in order; `furnace-column: 32 nets: 32 connected, 0 open, 0 unrouted; 0
SPILL; 189 entities, 178 edges, 0 underground pairs`. `no-limit furnace-column box: no limit=:
what reaches it goes into the logistic network without end`: the provider has no `limit=`.

**Aside: beacon-cells.fk.** `docs\examples\beacon-cells.fk` (plot 44x40) is the lab's beaconed
line, from the processing-unit bay: `kind Beacon = Entity<name=beacon, modules=speed-module-2>`,
`kind EFurn = Machine<entity=electric-furnace, modules=productivity-module-2>`, and a `FurnB`
cell 10x3 with one beacon two tiles west of its furnace. `fk check beacon-cells.fk`: `check
beacon-cells: 61 machines, 39 nets, 0 check lines`. `fk rates beacon-cells.fk` counts the modules
and the beacons: a beacon reaches every machine whose box comes within 3 tiles of its own, so the
middle furnace of three has two and the end ones one:

```
  fe/0/f             EFurn          1  1.000  iron-plate 87.06                       full speed
  fe/1/f             EFurn          1  1.000  iron-plate 99.08                       full speed
  fe/2/f             EFurn          1  1.000  iron-plate 87.06                       full speed
```

A plain electric furnace makes 37.5 plates/min. Summary: `beacon-cells: 39 nets: 39 connected, 0
open, 0 unrouted; 0 SPILL; 234 entities, 210 edges, 2 underground pairs`. Where the layout can't
place the beacons (a row), `beacons=8` on the instance counts them anyway (LANGUAGE.md, "Other
kinds").

## 7. library-lines.fk: the library's cells

`docs\examples\library-lines.fk`, plot 200x200:

```
import SmelterPair, AsmCell1 from "cells.fk"

kind CableCell = AsmCell1<recipe=copper-cable, ins=copper-plate, out=copper-cable>

ore   = Chest<requester, item=copper-ore, rate=3/s> @(4, 2)
coal  = Chest<requester, item=coal, rate=0.3/s>     @(14, 2)
smelt = Stack<cell=SmelterPair, n=5, plate=copper-plate, ore=copper-ore> @(10, 8)
cable = Stack<cell=CableCell, n=3>                  @(30, 8)
box   = Chest<provider, limit=8>                    @(36, 22)

net ore_:  ore -> smelt.f_in
net fuel:  coal -> smelt.f_in
lanes ore_ | fuel
net plates: smelt.pw_out, smelt.pe_out -> cable.in_in
net wire:   cable.out_out -> box
```

```
fk check library-lines.fk
fk rates library-lines.fk
```

```
check library-lines: 58 machines, 46 nets, 0 check lines
```

```
  smelt/3/w          Furnace        1  0.977  copper-plate 18.33                     copper-plate: cable/0/i takes 3.00/s
  smelt/3/e          Furnace        1  0.977  copper-plate 18.33                     copper-plate: cable/0/i takes 3.00/s
  smelt/4/w          Furnace        1  0.890  copper-plate 16.69                     copper-ore short on ore_
  smelt/4/e          Furnace        1  0.890  copper-plate 16.69                     copper-ore short on ore_
  cable/0/a          Assembler      1  1.000  copper-cable 120.00                    full speed
```

```
library-lines: 46 nets: 46 connected, 0 open, 0 unrouted; 0 SPILL; 203 entities, 200 edges, 2 underground pairs
```

**The idea.** The library's cells make iron unless you say. A Stack passes every parameter but
its own (`cell`, `n`, `head`, `built`, `duty`, `dnp`) to each cell, so `plate=copper-plate,
ore=copper-ore` turns five SmelterPairs to copper; `kind CableCell = AsmCell1<...>` names a cell
with its three parameters set, and `Stack<cell=CableCell>` takes the kind. The two plate belts of
the pairs (`pw_out`, `pe_out`) join into the cable line's one feed. 3 ore/s is a little short of
ten furnaces, and the rates show it where the belt does: the first four pairs at 98%, the last
at 89%.

The cells of `game\cells.fk` (this table is checked against the file by a test):

| cell | what | ports |
|---|---|---|
| `FurnaceCell<plate, ore>` | an electric furnace, ore up one belt, plates down the other: one lane of plates, about 12 to a yellow lane | `ore_in`/`ore_out`, `pl_in`/`pl_out` |
| `FurnacePair<plate, ore>` | two electric furnaces facing each other across the plate belt: both lanes, 24 furnaces to a yellow belt | `oa_*`, `ob_*`, `pl_*` |
| `StoneFurnaceCell<plate, ore, fuel=coal>` | a stone furnace, ore and fuel on the two lanes of one belt | `f_in`/`f_out`, `pl_in`/`pl_out` |
| `SmelterPair<plate, ore, fuel=coal>` | two stone furnaces back to back on one ore and fuel belt, plates out both sides: 24 furnaces to a yellow lane of ore | `f_*`, `pw_*`, `pe_*` |
| `SmeltColumn<plate, ore>` | three FurnaceCells in a column, the ore along its foot; stacked east, a bus of plate belts (chapter 8) | `ore_in`/`ore_out`, `pl` |
| `AsmCell<recipe, ins, out>` | an assembler, two ingredients on the two lanes of one belt (`ins=A\|B`), its product down the other | `in_*`, `out_*` |
| `AsmCell1<recipe, ins, out>`, `GearCell` | the same with one ingredient; `GearCell` is a kind of it with gears set | `in_*`, `out_*` |
| `CircuitCell` | green circuits: three cable assemblers to two circuit assemblers, handing straight across. Two cells: 480 circuits/min | `wi_*`, `wo_*`, `cu_*`, `eo_*`, `ei_*` |
| `LabCell<packs>` | a lab, two packs on the two lanes of one belt (red and green unless you say) | `p_in`/`p_out` |
| `LabColumn<packs>` | six LabCells and a header of one belt a pack; stacked east, a lab field | `r_*`, `g_*` |
| `DrillCell<resource>` | an electric drill dropping its ore onto the belt through the cell: one lane, 15 drills to a yellow lane | `ore_in`/`ore_out` |
| `DrillPair<resource>` | two drills facing each other across the ore belt: both lanes | `ore_in`/`ore_out` |
| `WrappedDrillCell<resource>` | a DrillCell built from a wrapped machine (`drill.json`, one drill named `d`): the way to build cells of any machine you captured | `ore_in`/`ore_out` |
| `AcidDrillCell` | an electric drill on uranium, its acid through the cell on a pipe | `acid_*`, `ore_*` |
| `GateCell<item, items, amount=auto>`, `GateCellR<item, items, amount=auto>` | a sushi loop's source: loads its item while the loop holds less than `amount` (`GateCellR` on the right lane) | `s_in`/`s_out` |
| `MallCell<recipe, items>` | a maker on a sushi loop, and its provider chest | `s_in`/`s_out` |
| `BeltEndBuffer<items>`, `ChestEndBuffer<items>` | a sushi loop's end buffer: a belt through, or a chest | `i`, `o` |
| `Hysteresis<in, out, lo, hi>` | a latch: `out` turns on below `lo` and off again above `hi` | none: name its parts (chapter 10) |

`fk explain FILE INST` shows any instance as its exact entities, with its size and where its
belts attach:

```
fk explain gears.fk gears
```

```
gears = AsmRow<n=2, recipe=iron-gear-wheel> at 6,2 rot 0 sig 9bdeea0726
  machines  gears/m0..m1  (2 x assembling-machine-1, recipe iron-gear-wheel)
  in   in1  net plates_in      items iron-plate                     attach 6.5,2.5 E   carriers gears/f0..f1, gears/in1b0..in1b5
  out  out  net gear           items iron-gear-wheel                attach 6.5,8.5 W   carriers gears/o0..o1, gears/outb0..outb5
  size      6x7 (its own frame)
```

**What goes wrong here.** A parameter a module declares without a default and an instance leaves
out is refused, naming it; a parameter a module does not declare is refused too. `unknown
template` is the refusal for a kind no file defines: import it (`import SmelterPair from
"cells.fk"`), or check the name against the table.

## 8. ribbon-bus.fk and bus4.fk: belts side by side, and a bus

`docs\examples\ribbon-bus.fk`, plot 200x200:

```
import SmeltColumn from "cells.fk"

ore  = Chest<requester, item=iron-ore, rate=7.5/s> @(0, 4)
cols = Stack<cell=SmeltColumn, n=4, plate=iron-plate, ore=iron-ore> @(4, 0)
box  = Boxes @(60, 30)

net feed: ore -> cols.ore_in
net each plates: cols[*].pl -> box
```

(`Boxes` is a sealed module of four provider chests with an `in` port each; the file has it.)

```
fk build --to report_checks
fk look --design --zoom 4
```

`out\route.txt` ends with the ribbon:

```
  ribbon    plates: 4 belts side by side (plates_0, plates_1, plates_2, plates_3), belts 3 2 1 0 from left to right looking along it; head 13,15 S, tail 60,29 S; 2 bus turns, 0 crossings, 179 belts, 0 underground ends; 1 belt starts where its driver's belt does, 3 from a belt laid to it; 4 belts end where its consumer's belt starts, 0 on in a belt of its own
```

```
ribbon-bus: 60 nets: 60 connected, 0 open, 0 unrouted; 0 SPILL; 499 entities, 479 edges, 4 underground pairs
```

```
look nauvis --source design: 1 plots, 435 entities, 0 invariant problems; zoom 4
render: design, zoom 4 (a cell is 4x4 tiles), x -2..65, y -2..36
   -2 ..FF.FF^Fv^Fv....
    2 C+FF+FF^Fv^Fv....
    6 v.FF+FF^Fv^Fv....
   10 >>>>>>>>>>>>v....
   14 ...v.+v..>>>>>>>v
   18 ...>>>>>>>>>>>>vv
   22 ......+....+...vv
   26 ......RR....RR+vv
   30 ...............vv
   34 ...............+.
cells: F furnace, R roboport, C chest, ^ belt north, > belt east, v belt south, + pole, . free
```

**The idea.** Cells within cells: a `SmeltColumn` is a Stack of three FurnaceCells with the ore
along its foot, and four columns stand side by side sharing the ore. `cols[*].pl` is every
column's plate port, and `net each` makes one net per cell, `plates_0` to `plates_3`, column k to
the box's port k. The router lays them as one **ribbon**: side by side, in order, turning
together. On paper the 12 furnaces make 450 plates/min, 112.5 to each belt.

**A bus.** `docs\examples\bus4.fk` (plot 54x25) is Rory's "river of iron, that you keep adding
more iron inputs to, and pull iron out when you need it":

```
bus   = Bus<belts=4, length=54, item=iron-plate, load=s11, tap=n26|s33> @(0, 10)
net iron1: smelt -> bus.load1             # the smelters' plates onto the bus
net fe:    bus.tap1 -> gears              # the gears take what they need first
net spare: bus.tap2 -> box                # the rest into the logistic network
```

Four belts run east; a load from the south at tile 11 merges into all four by a staircase of
splitters, two taps pull out by staircases of their own. `fk rates bus4.fk` treats it as a pool:

```
  bus                Bus            3      -  iron-plate 150.00                      pool: loads bring 2.500/s, taps would take 2.000/s
```

```
bus4: 7 nets: 7 connected, 0 open, 0 unrouted; 0 SPILL; 363 entities, 372 edges, 3 underground pairs
```

In the game the first tap along the bus takes its fill first. `bus4.json` beside it is Rory's
demo exactly as he placed it (`Literal<file="bus4.json">` pastes it as it is).

**What goes wrong here.** A ribbon with no path fails every net of it and lays none: `no-route`
on each, and the `ribbon` line says where it stopped. `tap-too-early`: a tap written before any
load, so nothing feeds the bus there. `partly-connected` on the bus's own net: a load after a tap
never reaches it.

**Aside: bus4ribs.fk.** A bus may bind its ports with a list instead of pins, each entry a named
net in port order, and a sealed module hangs on it as a rib (plot 100x40):

```
bus  = Bus<belts=4, item=iron-plate>(in1: feed, r0: rib0.iron, r1: rib1.iron,
                                     r2: rib2.iron, r3: rib3.iron) @(10, 20)
```

`fk contract bus4ribs.fk` is every sealed module's contract from the rate model, what each port
needs and what it gets:

```
contract bus4ribs: 4 sealed instances
  rib0: out iron-gear-wheel 1.0/s via box0/c0 (got 1.0) | in iron-plate 2.0/s via bus tap n7 (got 2.0)
  rib1: out iron-gear-wheel 1.0/s via box1/c0 (got 1.0) | in iron-plate 2.0/s via bus tap n13 (got 2.0)
  rib2: out iron-gear-wheel 1.0/s via box2/c0 (got 1.0) | in iron-plate 2.0/s via bus tap n32 (got 2.0)
  rib3: out iron-gear-wheel 1.0/s via box3/c0 (got 1.0) | in iron-plate 2.0/s via bus tap n52 (got 2.0)
```

`bus4ribs: 18 nets: 18 connected, 0 open, 0 unrouted; 0 SPILL; 460 entities, 451 edges, 0
underground pairs`.

## 9. mall-loop.fk, mall.fk, belt-switch.fk: malls and circuits

`docs\examples\mall-loop.fk`, plot 200x200: one belt of iron, copper and gears runs in a loop past
four makers:

```
import GateCell, GateCellR, MallCell, BeltEndBuffer from "cells.fk"

g1 = GateCellR<item=iron-plate,     items=iron-plate|copper-plate|iron-gear-wheel> @(4, 23)
g2 = GateCell<item=copper-plate,    items=iron-plate|copper-plate|iron-gear-wheel> @(6, 20)
g3 = GateCell<item=iron-gear-wheel, items=iron-plate|copper-plate|iron-gear-wheel> @(6, 17)
m1 = MallCell<recipe=iron-gear-wheel, items=iron-plate|copper-plate|iron-gear-wheel> @(6, 14)
m2 = MallCell<recipe=copper-cable,    items=iron-plate|copper-plate|iron-gear-wheel> @(6, 11)
m3 = MallCell<recipe=transport-belt,  items=iron-plate|copper-plate|iron-gear-wheel> @(6, 8)
m4 = MallCell<recipe=pipe,            items=iron-plate|copper-plate|iron-gear-wheel> @(6, 5)
e  = BeltEndBuffer<items=iron-plate|copper-plate|iron-gear-wheel> @(6, 2)

net l1 limited "the gates hold the loop": g1.s_out -> g2.s_in
...                                                  # l2 to l7, cell to cell
net back limited "the end buffer feeds the head": e.o -> g1.s_in
```

```
fk check mall-loop.fk
fk rates mall-loop.fk
```

`fk check` ends with the advice that says what each gate's `amount=auto` became:

```
  advice gate-auto net g1/s: auto holds iron-plate below 160: 40 s of the 4/s its loop's machines take (m1/a, m3/a, m4/a), and at least 2 crafts each
  advice gate-auto net g2/s: auto holds copper-plate below 40: 40 s of the 1/s its loop's machines take (m2/a), and at least 2 crafts each
  advice gate-auto net g3/s: auto holds iron-gear-wheel below 40: 40 s of the 1/s its loop's machines take (m3/a), and at least 2 crafts each
```

`fk rates` has a `BUFFER` line per item of the end buffer:

```
  BUFFER e: +0.0/min copper-plate (steady, its gated sources top up what goes out: in 30.0, out 30.0)
  BUFFER e: +0.0/min iron-gear-wheel (steady, its gated sources top up what goes out: in 30.0, out 30.0)
  BUFFER e: +0.0/min iron-plate (steady, its gated sources top up what goes out: in 120.0, out 120.0)
```

```
mall-loop: 20 nets: 20 connected, 0 open, 0 unrouted; 0 SPILL; 96 entities, 88 edges, 1 underground pairs
```

**The idea.** A mixed belt (a **sushi** net) runs while no item piles up. Each `GateCell` loads
its item only while the whole loop, read on a wire (`wire lw red: s` and `set s: read=hold-all`
inside the cell), holds less than its amount, and `net ... when lw.$item < $amount` is the gate:
every inserter of the net moves items only while the condition holds. The `BeltEndBuffer` feeds
what passed every maker back onto the head, so nothing has nowhere to go. `limited "why"` on a net
says your design limits what goes on it, and quiets `CLOG` for that net. In the lab this file ran
30 game minutes with the belt never stopped.

**mall.fk** (plot 34x44) is a mall of single assemblers with no loop: `Assembler<recipe>` stands
anywhere, rows and single machines mix, and the router lays the belts between them. `fk check
mall.fk`: `check mall: 12 machines, 8 nets, 0 check lines`; `mall: 8 nets: 8 connected, 0 open,
0 unrouted; 0 SPILL; 178 entities, 168 edges, 3 underground pairs`. A chest one tile from its
machine (`bbox`, `ibox`) gets a single inserter and no belt. The limit on a maker is a `set` line
on the hand that has the job (`set ins.out -> ibox: logistic=inserter<50`: wait while the network
holds 50), LANGUAGE.md "Properties on objects".

**belt-switch.fk** (plot 64x70) is a production switch on a belt, the lab's steel bay: a sealed
`SteelMod` of beaconed furnaces (chapter 6's `FurnB`), and around it a bay whose radar reads the
hub's flags on a universe channel, green for what the hub holds and red for what it wants:

```
  sd     = Entity<name=radar, cb.mode=2, cb.universe_channel={type: virtual, name: signal-1}> @(14, 61)
  want_p = Decider<if="green.steel-plate < red.steel-plate", out="steel-plate=1"> @(17, 61)
  want_t = Decider<if="green.iron-plate < red.iron-plate", out="iron-plate=1">    @(18, 61)
  wire sdr red:   sd -> want_p.in, want_t.in
  wire sdg green: sd -> want_p.in, want_t.in
  wire sw red:    want_p.out, want_t.out
  net out_ items steel-plate: c1.st_out -> out when sw.steel-plate > 0
  net tap_ items iron-plate:  c1.fe_tap -> out when sw.iron-plate > 0
  lanes out_ | tap_
```

```
fk check belt-switch.fk
fk build --to report_checks
```

```
check belt-switch: 178 machines, 116 nets, 3 check lines
  limited net take: the bay's switch stops each lane while the hub has enough
  gate-open net bay/out_: gated (sw.steel-plate > 0): the rate model takes it as always open; a behave block on bay/sw with a cycle says how often it runs
  gate-open net bay/tap_: gated (sw.iron-plate > 0): the rate model takes it as always open; a behave block on bay/sw with a cycle says how often it runs
```

`out\floorplan.txt`, where the build lays the circuits:

```
circuits belt-switch: gated net bay/out_: 1 carrier if=steel-plate>0 on bay/sw
circuits belt-switch: gated net bay/tap_: 1 carrier if=iron-plate>0 on bay/sw
```

```
belt-switch: 116 nets: 116 connected, 0 open, 0 unrouted; 0 SPILL; 672 entities, 618 edges, 0 underground pairs
```

A gated net whose route has no inserter or pump is switched at its trunk belt, the first belt
every driver's items pass (`1 carrier`): a wire to a belt, enable or disable. The two output nets
stop in the bay when the hub has enough, which is Rory's rule: stop a bay's output in the bay,
never by the hub's filters. `cb.FIELD=` writes any setting of any building as the game's blueprint
names it (the radar's channel here); `Decider<if, out>` is a decider combinator, `x.in` and
`x.out` its two sides.

**What goes wrong here.** `CLOG net NAME: a, b share one belt: one that comes faster than it is
used can fill it and stop the others ...` is the loud line of any sushi net; `limited "why"`
quiets it once your design has a limit, `net NAME split:` gives each item a lane of its own.
`no-limit m1/box, m2/box, m3/box, m4/box: no limit=: what reaches them goes into the logistic
network without end`: the MallCell's chests have no limit, by design in a loop (the gates limit
the loop). `gate-open`: the rate model takes a gated net as always open, so the numbers are the
machines' full speed. `feed-rides-on belt-switch net take: iron-plate rides the belt into box/c0
with steel-plate: ...`: here one chest takes both lanes; in the base the hub takes each lane with
a filtered inserter of its own. `bootstrap net mall/fe: its route needs 68 transport-belt and 5
inserter: transport-belt: the only maker (mall/belt) takes iron-plate from this same net; ...`: the
mall's own route needs items only the mall makes; stock them first, or build those makers first.

**Aside: early-mall.fk and compact-mall.fk.** Two malls laid for few belts. `early-mall.fk`
(plot 49x24): every machine within 3 tiles of what it feeds, so 24 of its 30 nets are inserters
only (`early-mall: 30 nets: 30 connected, 0 open, 0 unrouted; 0 SPILL; 234 entities, 234 edges, 9
underground pairs`). `compact-mall.fk` (plot 50x16): Rory's layout, rows with no gap between
chains and a 1-tile gap inside a chain, two rows facing one corridor, one products net into one
unlimited chest with the limit at each maker (`set yu.out: logistic=underground-belt<50`);
`compact-mall: 20 nets: 20 connected, 0 open, 0 unrouted; 0 SPILL; 271 entities, 275 edges, 9
underground pairs`. Its `fk check` asks `advice limited-why net products: limited says something
limits what goes on it: say what, so a reader sees it where CLOG would be`.

## 10. power-shed.fk and steam.fk: power

`docs\examples\power-shed.fk`, plot 200x200:

```
import LabColumn, Hysteresis from "cells.fk"

power main: cover all                 # everything the labs domain doesn't name
power labs: cover field               # the lab field, its own network

s1 = Entity<name=solar-panel> @(0, 0)          # and s2, s3, s4
acc  = Accumulator<read=A> @(0, 5)
acc2 = Accumulator         @(3, 5)

signal A = signal-A
signal S = signal-S
shed = Hysteresis<in=A, out=S, lo=20, hi=80> @(8, 6)
wire level green: acc -> shed.latch.in
sw = PowerSwitch @(12, 5) when shed.hold.S = 0

copper feed: main -> sw.left
copper load: sw.right -> labs

red   = Chest<requester, item=automation-science-pack, rate=1/s> @(18, 26)
green = Chest<requester, item=logistic-science-pack, rate=1/s>   @(18, 28)
field = Stack<cell=LabColumn, n=2> @(24, 6)
net r: red -> field.r_in
net g: green -> field.g_in
```

```
fk check power-shed.fk
fk build --to report_checks
```

```
check power-shed: 54 machines, 42 nets, 0 check lines
```

`out\cover_power.txt`:

```
power-shed: domain main (cover all): cover power power pole=medium-electric-pole: 6 poles (2 bridging, 0 existing); 10/10 electric entities covered; 1 wire component
power-shed: domain labs: cover power-labs power pole=medium-electric-pole: 4 poles (0 bridging, 0 existing); 24/24 electric entities covered; 1 wire component
power-shed: copper feed: power/p3 to sw/m0.left
power-shed: copper load: power-labs/p4 to sw/m0.right, 1 bridge pole added (power-labs/p4)
```

```
power-shed: 42 nets: 42 connected, 0 open, 0 unrouted; 0 SPILL; 170 entities, 150 edges, 0 underground pairs
```

**The idea.** Power is its own step: `cover power` lays the poles, so put none in the source. A
design with more than one `power` line has one electric network per line, a **domain**; `copper
NAME: A -> B` joins two domains, or a domain and a switch's side. The library's `Hysteresis` is a
decider whose output feeds back to its input: `S` turns on when the charge `A` falls below 20 and
stays on until it is back above 80, so the switch does not flicker round one threshold; `when
shed.hold.S = 0` closes the switch while nothing is shed. In the lab the switch opened at 19.2%
charge and closed at 79.6%, once each way.

**steam.fk** (plot 16x26) is the power plant: `OffshorePump`, two `Boiler<fuel=coal>`, four
`SteamEngine`, coal from a requester; `net water: pump -> b1, b2` and `net steam: b1, b2 -> e1,
e2, e3, e4` are pipes. `fk check steam.fk`: `check steam: 8 machines, 3 nets, 0 check lines`;
the boilers run `steam 3600.00` a minute each at full speed and the pump at 10%. `out\route.txt`:

```
  routed    steam: 2 -> 4, 6 pipes, 1 pipe-to-ground pair, 2 direct joins
  routed    water: 1 -> 2, 6 pipes, 1 pipe-to-ground pair
```

`steam: 3 nets: 3 connected, 0 open, 0 unrouted; 0 SPILL; 43 entities, 17 edges, 0 underground
pairs; 25 fluid boxes, 23 fluid joins, 2 pipe-to-ground pairs`, and it is the one example with no
check line at all: its engines are a generator, so `out\floorplan.txt` says `steam: 3 poles, on a
grid with generation`. Place the plot so the pump stands on the shore with water west of it (`rot
3` faces west).

**What goes wrong here.** `power-short power-shed power main: its machines want 220 kW at full
load and its 4 generators make 168 kW, with its power switches open (solar panels at their day's
average, 42 kW)`: the template's `cover robots` step gave the plot a roboport, and four panels
do not carry it with the rest of `main`. More panels, or fewer things in the domain; on the lab
(a free surface) the cover lays no roboport and the line does not appear. `no-source`: a domain
with no generator and no copper line to one. `switch-never`: a switch whose `when` can never
change. `domain-overlap`: a machine no pole of its domain can power without reaching another
domain's. `NO_POWER` (every chapter): the factory's poles reach no grid with generation.

## 11. red-chain.fk: a sealed module, compiled once

`docs\examples\red-chain.fk`, plot 92x76, is the lab's red-circuit chain: copper and iron smelted
on beaconed furnaces, green circuits, plastic from gas and coal, red circuits, all inside one
sealed module with its ports on the edges. The head of the file:

```
import FurnB, GreenB from "beacon-cells.fk"

kind Asm3   = Machine<entity=assembling-machine-3, modules=productivity-module-2>
kind Chem   = Machine<entity=chemical-plant, modules=productivity-module-2>
kind Beacon = Entity<name=beacon, modules=speed-module-2>
```

Then four more sealed modules (`Plas2`, `RedB`, `PlasPair`, `RedPair`) and the chain itself:

```
module RedMod size 74x56 {
  port cu_in   : in  copper-ore | copper-ore @s3
  port fe_in   : in  iron-ore | iron-ore     @s13
  port gas_in  : in  petroleum-gas           @s29
  port coal_in : in  coal                    @s35
  port r_a     : out advanced-circuit        @e40
  port r_b     : out advanced-circuit        @e36

  cua = Stack<cell=FurnB, n=5, plate=copper-plate, ore=copper-ore> @(0, 19)
  fea = Stack<cell=FurnB, n=4, plate=iron-plate, ore=iron-ore>     @(10, 22)
  ga  = Stack<cell=GreenB, n=2>                                    @(20, 20)
  cur = Stack<cell=FurnB, n=3, plate=copper-plate, ore=copper-ore> @(40, 25)
  cu2 = Stack<cell=FurnB, n=3, plate=copper-plate, ore=copper-ore> @(30, 25)
  rp  = RedPair                                                    @(50, 1)
  pl  = PlasPair                                                   @(26, 42)

  net cu:      cu_in -> cua.ore_in, cur.ore_in, cu2.ore_in
  net fe:      fe_in -> fea.ore_in
  net cu1:     cua.pl_out -> ga.in_in
  net fe1:     fea.pl_out -> ga.in_in
  net gas:     gas_in -> pl.gas_in
  net coal_:   coal_in -> pl.coal_in
  net green1:  ga.g_out -> rp.gp_in
  net plastic: pl.plastic_a, pl.plastic_b -> rp.gp_in
  net cu5:     cur.pl_out, cu2.pl_out -> rp.cu_in
  net ra:      rp.r_a -> r_a
  net rb:      rp.r_b -> r_b
}

chain  = RedMod @(2, 4)
```

and stand-ins for the trunks that feed it in a base (two ore requesters, an `InfinityPipe`, a
coal requester, a provider chest on each output).

```
fk check red-chain.fk
fk build --to report_checks
fk status
```

```
check red-chain: 200 machines, 127 nets, 1 check lines
```

`out\route.txt` begins with the store and ends every inner net of a repeated cell with the first
one's layout:

```
red-chain: compiled  stored chain, chain/cu2/0, chain/fea/0, chain/ga/0, chain/pl, chain/pl/p1, chain/rp, chain/rp/red/0 (out/compiled): laid from there wherever each stands next
red-chain: 118 routed, 0 kept (0 pinned from the committed .fkp), 0 failed, 1 pass
  routed    chain/cu2/1/o: 1 -> 2, laid as chain/cu2/0 laid it (compiled once, PLAN-3.0 §4)
```

```
red-chain: 127 nets: 127 connected, 0 open, 0 unrouted; 0 SPILL; 1003 entities, 904 edges, 9 underground pairs; 42 fluid boxes, 37 fluid joins, 2 pipe-to-ground pairs
```

`fk rates red-chain.fk`: the two output nets carry `red_a ... 2.832` and `red_b ... 1.844` red
circuits/s to their chests on paper, the red assemblers at 89% (`electronic-circuit short on
chain/ga/0/o`). Measured in the lab (11-07): about 6.9 red circuits/s from the two belts together,
with the lab's own trunks feeding it.

**The idea.** Each sealed module is routed alone in its frame, the innermost first, and every
module routed whole is stored in `game\out\compiled\<module>\<key>.json`. A second copy of the
same cell (`chain/cu2/1`) is laid as the first was; a build that places the module again, moves
it or rebuilds the world lays the stored inside and routes only the nets that cross frames. The
key is a hash of everything the inside is laid to, so a change inside routes it again by itself,
and `fk status` lists the store:

```
compiled: 5 modules, 5 entries (router 3.1) in .\out\compiled
  Circuits: 1 entry, current 35bf8f24dccd1b422051
```

(from `busbase.fk`, chapter 12; the first line of `fk status` is the nets: `busbase: 34 nets: 34
routed`). The route step writes one file per net, `out\design\<factory>\nets\<net>.net`, and the
next build starts from them. `fk route NET` lays one net again now and keeps every other route;
`fk hold NET` keeps a route as it stands in every build (`fk unhold` lets it go); `fk seed NET
'splitter 9,30.5 S'` pins a piece of a net for the router to lay the rest through; `fk compile
MODULE` routes a module's inside now and stores it, which a build does anyway for every sealed
inside that routed whole.

**What goes wrong here.** `CLOG net chain/rp/gp: electronic-circuit, plastic-bar share one belt:
...`: the one check line this file has. `RedPair` carries green circuits and plastic on one net
into its two stacks; the cell inside (`RedB`) writes them as two nets with `lanes g | p`, and
writing `RedPair`'s net the same way would quiet it. `short-feed red-chain net chain/cu:
copper-ore: its drivers make 720.0/min at most, its consumers would take 842.6/min, so they run
at about 85% for it`: eleven beaconed copper furnaces eat more than 12 ore/s. `no-route NAME net
X: ...` with the reason follows a net that found no way; a net inside a sealed module is tried
again with that module's nets, never the whole design's. `crosses-frame`: a net crosses a sealed
frame outside a port. `overlap`: two sealed frames share tiles. A stored entry holding an entity
the force can't build yet is not taken (`not researched here: routed again`).

**Aside: blue6.fk.** `docs\examples\blue6.fk` (plot 60x40) is an older sealed cell written before
cells had parameters: `BlueCell6`, 18x6, two units of an engine assembler and an advanced circuit
assembler handing into a blue science assembler, with a pinned `Pole` where no other tile reaches
the inserters, and `Stack<cell=BlueCell6, n=2>`. It still builds (`blue6: 33 nets: 33 connected,
0 open, 0 unrouted; 0 SPILL; 235 entities, 231 edges, 4 underground pairs`; the route lays
`blue/1` as `blue/0` laid it), and its `fk check` prints 26 lines, which is why new cells are
written as the library's are: `two-nets blue6 blue/0/t_in: sulfur (out) is on 2 nets: blue/0/ts,
blue/0/tp. Name each net's items (`net blue/0/ts items sulfur: ...`) so one carries it`,
`SUSHI_SINK net blue/0/ts: blue/0/blue0 does not take chemical-science-pack`, `CLOG` on each
paired port's nets, and `could-pair net blue/0/ad0 and blue/0/en0`. `set packs.in:
filter=chemical-science-pack` at the end filters the packs chest's hands, so the sulfur beside
them never fills it.

## 12. busbase.fk: a world of plots

`docs\examples\busbase.fk`, plot 172x82: five sealed modules (two smelters, circuits, a mall,
science) north of three buses that run east along the bottom, each spine perpendicular to the
bus. The world lines at its end:

```
fe   = IronSmelt   @(0, 0)
cu   = CopperSmelt @(36, 0)
circ = Circuits    @(74, 30)
mall = Mall<duty=0.1> @(104, 18)
sci  = Science     @(128, 12)

cbus  = Bus<belts=1, item=electronic-circuit>(cl: circ.circ, mc: mall.circ, sc: sci.circ) @(70, 66)
febus = Bus<belts=2, item=iron-plate>(fl: fe.plate, ci: circ.iron, mi: mall.iron, si: sci.iron) @(0, 72)
cubus = Bus<belts=1, item=copper-plate>(kl: cu.plate, cc: circ.copper, sc2: sci.copper) @(0, 79)
```

```
fk check busbase.fk
fk build --to report_checks
fk contract busbase.fk
```

```
check busbase: 116 machines, 38 nets, 0 check lines
```

`out\route.txt`:

```
busbase: compiled  stored circ, cu, fe, mall, sci (out/compiled): laid from there wherever each stands next
busbase: 34 routed, 0 kept (0 pinned from the committed .fkp), 0 failed, 1 pass
```

```
busbase: 38 nets: 38 connected, 0 open, 0 unrouted; 0 SPILL; 1743 entities, 1746 edges, 22 underground pairs
```

```
contract busbase: 5 sealed instances
  circ: out electronic-circuit 4.0/s via cbus load n11 (got 0.6) | in copper-plate 0.8/s via cubus tap n89 (got 0.8) | in iron-plate 0.6/s via febus tap n78 (got 0.6)
  cu: out copper-plate 3.8/s via cubus load n52 (got 1.3)
  fe: out iron-plate 7.5/s via febus load n16 (got 4.3)
  mall: in electronic-circuit 0.1/s via cbus tap n46 (got 0.1) | in iron-plate 0.5/s via febus tap n110 (got 0.5)
  sci: in electronic-circuit 0.5/s via cbus tap n74 (got 0.5) | in copper-plate 0.5/s via cubus tap n138 (got 0.5) | in iron-plate 3.3/s via febus tap n132 (got 3.3)
```

**The idea.** Composition. A base is sealed modules and the trunks between their ports, and the
world design (`game\nauvis.fk`) places them: every top-level sealed instance there is a **plot**,
`@(x, y)` its corner in map coordinates (its origin, which a turned instance turns about), `size
WxH` its ground. A plot owns every tile inside it and plots never overlap. Your run starts with
one plot, `world.fk`, 2000x2000, and this file is what goes inside it, or a plot of its own:
`import BusBase from "busbase.fk"` and `base = BusBase @(70, -40) size 172x82` in `nauvis.fk`. The
route step took 1.4 s for the 38 nets (`out\timings.txt`). In the lab it stood whole, and by game
minute 30 science ran at the model's rates.

**What goes wrong here.** `short-feed busbase net mall/gr: iron-gear-wheel: its drivers make
6.0/min at most, its consumers would take 49.5/min, so they run at about 12% for it`: the mall is
`duty=0.1`, a maker that runs a tenth of the time, so its one gear assembler is held with it.
`malformed`: a tile owned twice, or a plot outside its ground; it is the one check the template's
`stop_on` always holds the build on. `does-not-fit`: a sized line runs into another frame, and the
build stops at `place` when their entities would overlap.

## 13. The lab surface

The lab is a game surface like any other, with a surface file that says `free` (things are free:
a paste is revived at once, nothing is built by robots) and `force lab`. None of the fair-play
rules apply there (`FAIR-PLAY.md`, "The lab"), and Rory watches it with `/lab`.

```
fk lab plastic-cell
fk lab plastic-cell --measure 60
fk capture slot plastic-cell
```

`fk lab NAME` is `fk alone NAME --surface lab` and then the two stand-ins. `fk alone` gives
`NAME.fk` (or a factory the world design places) a workspace `lab\NAME` in your run folder with a
surface file that places it on a slot of its own, runs the build script there, and the apply
pastes it and revives it. In the game the lines have this shape:

```
alone plastic-cell on lab (free): slot X,Y (WxH tiles for 19x27), workspace lab\plastic-cell (from game)
  emptied first: N entities; C chunks generated under the slot
  route ... 24 routed, 0 kept ...
  report_checks ... 24 nets: 24 connected, 0 open, 0 unrouted ...
  apply lab area X0,Y0,X1,Y1 (free, force lab): N entities designed, N ghosts placed, 0 where designed
  placement plastic-cell: recorded in out/placements/lab.json (fk look plastic-cell, fk diff plastic-cell)
  stand-in supply: power ...; requesters stocked: 1 coal; burners given a first load: 0
  stand-in feed: N open ports (...):
  next: let it run a minute or two, then `fk lab plastic-cell --measure 60`; Rory sees it with /lab at X,Y
```

- `fk standin NAME supply`: power (an energy interface), the requesters' items, burners' coal,
  module requests; run again, it tops them up. `fk standin NAME feed`: an infinity chest and
  loader outside each open belt port, an infinity pipe outside each fluid port; out ports empty
  into a sink. `fk standin NAME ore --copy-ore 272,-72,312,-40 --to 4,2` copies a real patch's
  shape under a drill row. Stand-ins work only where things are free.
- A sealed module needs no lab file of its own: when `NAME.fk` only defines it, it is built alone
  with 4 tiles round it, and `feed` gives every port a source or a sink. So `red-chain.fk`'s
  chain goes on the lab without its requesters: a file with the `RedMod` line alone.
- The lab has no edge. Each factory gets a slot sized to it in 200-tile cells and keeps it while
  it fits; building it again empties the slot first. `--keep` lays over what stands and routes
  only the stale nets; `--size 60x40` sets the plot when no world design places it.

`fk lab NAME --measure 60` (or `fk measure X0,Y0,X1,Y1 --seconds 60` on any area) reads what the
machines really make against the rate model. The shape:

```
measure area X0,Y0,X1,Y1: 3600 ticks (60.0 s), N machines matched to the design, K not
  instance                   item                          measured/min  model/min  ratio
  line/0/plant               plastic-bar                          M          120.00   R
```

Let it run a minute or two first: a backlog that piled up while it started reads as speed. A
module that builds clean alone goes into the world with only its trunks new there, and its stored
inside goes with it (chapter 11): `--with 'route --reuse-lab'` reads the lab builds' stores too.

**A blueprint on the lab.** A blueprint you hold (a capture, a blueprint string) goes on the lab
the same way, as a `Literal` in a file of its own:

```
# game\try.fk
x = Literal<file="captured/x.json"> @(0, 0)
```

Then `fk lab try`: the apply pastes it on the slot and revives it. `fk capture slot try` writes
what stands on the slot back as a Literal file (`captured/try.json` by default), inserters,
settings and wires included, and `fk capture world X0,Y0,X1,Y1` does the same for any area of the
base. Chapter 14 captures with an anchor.

**What goes wrong here.** `NAME: not built in the lab yet (fk lab NAME)` from `--measure`: build
it first. `--keep keeps a slot that stands; it has none yet on lab`. A stand-in on a surface that
is not free is refused: `... is not free; a stand-in is a god-mode convenience ...`. `fk lab
--list` shows the slots in use, `fk lab NAME --clear` empties and frees one. Two `fk lab` runs at
once share the link's paste buffer and both die at apply: queue them.

## 14. mate.fk: placements, and new source over what stands

`docs\examples\mate.fk`, plot 40x40, with `docs\examples\captured\mate-block.json`:

```
std = Literal<file="captured/mate-block.json"> @(10, 10)
fe  = Chest<requester, item=iron-plate, rate=1/s> @(10, 16)
box = Chest<provider, limit=4>                    @(22, 11)
net fe:  fe -> std.asm1
net out: std.asm1 -> box
```

The JSON is what `fk capture --from 10,10,16,14 --into captured/mate-block.json` writes for a
gear assembler and an iron chest standing in the world (the shape of its lines in the game):

```
capture: 2 entities into captured/mate-block.json, anchored at 10,10 on nauvis: 1 assembling-machine-1, 1 iron-chest
  in a module whose frame is the world's (the world design, or a plot at 0,0):
    mate_block = Literal<file="captured/mate-block.json"> @(10, 10)
    net out: mate_block.asm1 -> <your new instance>
  on a plot at X,Y: @(10 - X, 10 - Y). Placed there, its entities land on the standing ones (ok, mated) and your new ones are ghosts
```

The file holds the blueprint, each entity's position relative to the anchor (`wx`, `wy`) and
the machine names (`"names": {"asm1": 0}`): `std.asm1` is a net end like any machine's.

```
fk check mate.fk
fk build --to report_checks
```

```
check mate: 3 machines, 2 nets, 0 check lines
```

```
mate: 2 routed, 0 kept (0 pinned from the committed .fkp), 0 failed, 1 pass
  routed    fe: 1 -> 1, 1 inserter (direct insertion)
  routed    out: 1 -> 1, 7 belts, 0 underground ends, 0 merges, 0 splitters, 2 inserters, hub 16,11 E
```

```
mate: 2 nets: 2 connected, 0 open, 0 unrouted; 0 SPILL; 17 entities, 12 edges, 0 underground pairs
```

**The idea.** A **placement** is one paste: which module (or file) stands where. The registry is
`out\placements\<surface>.json`, one record per paste (id, module, artifact, origin, rotation,
area, tick, source), written by every `fk build` apply, every build alone and every `fk paste`; a
rebuild of the same id replaces its record. `fk placements` lists it (`placement ID: MODULE at
X,Y, area X0,Y0,X1,Y1, N entities, store:KEY (build, tick T)`). The **mismatch rule**: a build
pastes only onto tiles it can explain. A world entity on a design entity's tiles that the toolkit
did not paste there is a conflict, named with its owner, and the apply refuses those tiles and
pastes the rest; there is no `--yes` for it. Mating is no mismatch: an entity standing with the
same name, position and direction as the design's is `ok`. So new source over standing objects is
this workflow:

1. Something stands in the world: a hand build, a `fk paste FILE --at X,Y`, a block registered
   with `fk register X0,Y0,X1,Y1 --as NAME`.
2. `fk capture --from NAME --into captured/std.json` (or `--from X0,Y0,X1,Y1`): the file anchored
   where it stands, and the two lines to paste.
3. Write the module: the Literal at its anchor, then what is new (`box`, `net out`), as `mate.fk`.
4. Place it where its frame is the world's: in the world design, or a plot at 0,0; on a plot at
   X,Y write `@(10 - X, 10 - Y)`.
5. `fk build`. In the game the diff says `ok` for every standing entity of the block and `ghost`
   for the new chest and its carriers, and no `mismatch` line; the build's placement covers the
   block. `fk diff mate` afterwards is headed `placement mate: mate at 0,0, area ..., N entities
   (build, tick T)`.

The other 3.1 reads: `fk adopt ID|AREA` proposes `set` lines for settings changed in the world (a
limit, a request, a filter, a circuit condition) as a diff of the module source, and `--write`
appends them; `fk robots [AREA]` reads the standing roboports against the design's logistic
chests (how many stand, how many have power, which chests are covered, which roboports are
redundant, how few would do); `fk read-terrain --surface nauvis --area X0,Y0,X1,Y1 --entities`
puts every built entity there that the design does not own into the terrain file as blocked
tiles, so the next build routes round them. A value that starts with a minus sign goes with `=`:
`--from=-300,-300,-100,-100`.

**What goes wrong here.** `MISMATCH mate/box/c0 (logistic-chest-passive-provider at 22.5,11.5):
wooden-chest stands there, hand edit by rory_618; refused, not pasted. fk adopt 22.5,11.5 --into
FACTORY/NET takes a carrier into its net; fk register 22,11,23,12 MODULE says what stands is that
artifact; or take it out by hand` is the shape of a conflict line in `out\diff.txt` and `fk
diff`, headed by `mismatch: N design tiles have something standing on them the toolkit did not
paste; the apply refuses them and pastes the rest:`. The owner is `placement ID`, `hand edit by
PLAYER` or `unknown`, and each names the commands that resolve it (`fk capture --from`, `fk
register`, `fk adopt`, `fk decon`). `short-feed mate net fe: iron-plate:
its drivers make 60.0/min at most, its consumers would take 120.0/min`: the requester brings half
of what the gear assembler eats. `no placement 'nope' (placements: std)`: the id is not in the
registry; `fk placements` lists them.
