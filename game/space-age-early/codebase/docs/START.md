# Start here: the first hour

One file, from a copy to a look at it in the game. Every line the toolkit prints is explained once
here; after that, `EXAMPLES.md` adds one idea per chapter, each with a file that builds, the
command, and the lines it prints. The reference (`LANGUAGE.md`, `TOOLKIT.md`) hangs off those
chapters: read it when a chapter points at it, not before.

Your folder: `bin\` is on your PATH, so `fk` is the one command. `game\` holds your factories
(`.fk`), your base's plot (`world.fk`), the world design (`nauvis.fk`), the surface file and the
build script. `docs\examples\` holds the files this guide quotes.

## 1. Copy the file

```
copy docs\examples\gears.fk game\gears.fk
```

`game\gears.fk` (the header comment left out):

```
inbox = Chest<requester, item=iron-plate, rate=1.25/s> @(2, 2)
gears = AsmRow<n=2, recipe=iron-gear-wheel>           @(6, 2)
box   = Chest<provider, limit=4>                      @(2, 12)

net plates_in: inbox -> gears
net gear:      gears -> box
```

Three instances and two nets. An instance is `name = Kind<parameters> @(x, y)`: a requester chest
the robots fill with plates, a row of two gear assemblers, a provider chest the robots empty. A net
is `net NAME: drivers -> consumers`: what puts items on a belt, and what takes them off. Where the
belts, inserters and poles go is the toolkit's job.

## 2. Check it on paper

```
fk check gears.fk
```

```
check gears: 4 machines, 2 nets, 0 check lines
  advice row-cell gears: a new AsmRow could be Stack<cell=AsmCell1, n=2, recipe=iron-gear-wheel, ins=iron-plate, out=iron-gear-wheel>, a Stack of the library's AsmCell1 (import it from the kickoff template's game\cells.fk). Rows still build, and new lines are Stacks of cells
```

- `4 machines`: the two chests and the two assemblers. `2 nets`. `0 check lines`: nothing is
  unfed, nothing goes nowhere.
- `advice row-cell`: advice, not a fault. `AsmRow` is an older kind the toolkit lays for you; the
  current way to write a line is a Stack of cells from your `game\cells.fk` (EXAMPLES.md, chapter
  3). The row still builds.

## 3. The rates

```
fk rates gears.fk
```

```
rates gears (nominal V1 model: logical nets, no capacity)
  solved in 40 passes, last change 6.3e-13

  instance           kind           n   util  makes /min                             binding
  inbox              Chest          1  1.000  iron-plate 75.00                       full speed
  gears              AsmRow         2  0.313  iron-gear-wheel 37.50                  iron-plate short on plates_in
  box                Chest          1  1.000                                         sink

  net              item                        supply/s would take  taken/s  sinks/s surplus/s
  plates_in        iron-plate                     1.250      4.000    4.000    0.000     0.000
  gear             iron-gear-wheel                0.625      0.000    0.000    0.625     0.000

  no routed rates: gears.fkp has no routes yet (fk build lays them; then this shows the lanes and inserters as laid)
```

- The first table is one line per instance: `util` is how much of full speed it runs at, `makes`
  what it makes, `binding` what holds it there. The two assemblers run at 31%: the chest brings
  1.25 plates/s (`rate=`), and two assemblers could eat 4/s.
- The second table is one line per net: what its drivers supply, what its consumers would take at
  full speed, what they get. `sinks/s` is what ends in a chest.
- `no routed rates`: nothing is laid out yet. After a build the same command adds a second
  section, the rates as the belts and inserters as laid let through.

Want more gears? Raise `rate=` on the chest (the robots bring what you ask), or `fk plan
iron-gear-wheel 1/s` says what a rate takes down to the ore (EXAMPLES.md, chapter 5).

## 4. Give it ground

A factory is placed by the world design. Your base is one plot, `world.fk`, and the simplest way
to add a factory is to import it there:

```
# in game\world.fk
import Gears from "gears.fk"
gears = Gears @(6, 6) size 16x16
```

`@(6, 6)` is where its (0, 0) goes inside your plot, `size 16x16` the ground it may use. Every
`@(x, y)` in `gears.fk` is inside that frame.

## 5. Build it on paper

```
fk build --to report_checks
```

The build runs the steps of `game\nauvis.build.fkb` in the background and writes one line per
step to `game\out\build.log`; `--to report_checks` stops before anything touches the game. The
log, as the steps land (the right-hand part of each line is cut to the width here):

```
[ 1/14] read_surface    nauvis                       surface nauvis: force player, terrain none, catalog base, notify nearb
[ 3/14] elaborate                                    gears: 3 instances, 4 machines, 2 nets (0 sushi), 0 check lines
[ 4/14] size                                         no n=auto rows: every count is as written
[ 5/14] report_rates                                 rates gears (nominal V1 model: logical nets, no capacity)  (1 check lines)
[ 6/14] place           --keep pinned                gears: 3 instances placed by their hints, 20 entities, 12x13; 3 new
[ 7/14] route           --only stale --retry-failed changed --failed keep --effort normal --hand any gears: 2 routed, 0 kept (0 pinned from the committed .fkp), 0 failed,
[10/14] cover robots                                 gears: cover robots robots: 1 roboports; 2/2 logistic chests inside a
[11/14] cover power     --pole any                   gears: cover power power pole=medium-electric-pole: 3 poles (0 bridgin
[13/14] report_checks                                gears: 2 nets: 2 connected, 0 open, 0 unrouted; 0 SPILL; 34 entities,
```

Each step writes `game\out\<step>.txt` with its whole report. The ones to read:

`out\route.txt`, how each net was laid:

```
gears: 2 routed, 0 kept (0 pinned from the committed .fkp), 0 failed, 1 pass
  routed    gear: 1 -> 1, 6 belts, 0 underground ends, 0 splitters, 1 inserters
  routed    plates_in: 1 -> 1, 2 belts, 0 underground ends, 0 merges, 1 inserters
```

`1 -> 1` is one driver to one consumer. A net that found no way says `not routed` with the
reason, and the summary counts it under `failed`.

`out\report_checks.txt`, the verdict:

```
gears: 2 nets: 2 connected, 0 open, 0 unrouted; 0 SPILL; 34 entities, 30 edges, 0 underground pairs
  sinks     2 of 2 makers on a net reach a sink with what they make
check lines this run: NO_POWER 1, short-feed 1
  report_rates  short-feed gears net plates_in: iron-plate: its drivers make 75.0/min at most, its consumers would take 240.0/min, so they run at about 31% for it; `fk plan iron-plate 240/min` sizes the drivers
  floorplan     NO_POWER nauvis gears: its grid reaches no generator (bridge it: floorplan --bridge-power)
```

- `2 connected, 0 open, 0 unrouted`: every net reaches every machine it names. `0 SPILL`: no
  net's items can end on another net's belt. `34 entities`: the four machines, the row's own 16
  inserters and belts, the two routes' 8 belts and 2 inserters, 3 poles and a roboport.
- `short-feed` is the rates table again as a check: the row is plate-short, and the line names
  the `fk plan` call that sizes a supply for it.
- `NO_POWER` is loud because nothing in the factory runs without it. On paper the file has no
  generator; in your base the `floorplan --bridge-power` step of the build script joins the
  factory's poles to the plant's grid, and the line goes away. A check line never stops the build
  unless the build script's `stop_on` names it.

## 6. Build it in the game

```
fk build
```

The same steps, and then the game ones: `diff` (what stands against the design), `report_bom`
(what the network has in stock for it) and `apply` (the paste). It runs in the background;
`fk status` shows the nets as the router left them and the log's last lines, and `fk wait build`
waits for the end. In the game the apply line has this shape:

```
apply nauvis area X0,Y0,X1,Y1 (robots, force player): 34 entities designed, N ghosts placed, K where designed
  robots: 0 unowned entities ordered for deconstruction; robots build the ghosts
```

Ghosts are what the robots build from the network's stock. `out\report_bom.txt` says what the
network lacks for it (`short-stock` per item): a ghost that stays is a blocked build, not a slow
one.

## 7. Look at it

```
fk look 6,6,22,22
```

The area in map coordinates (your plot's corner plus the factory's `@(6, 6)` and its `size`).
In the game the head line has this shape, then one verdict per designed entity:

```
look nauvis --source world, area 6,6,22,22, tick T: ok N/34 designed entities (notify nearby; watch plots keep, sanctioned mark 2h)
  ok       assembling-machine-1 8.5,3.5 nauvis:gears/gears/m0
  ghost    transport-belt 10.5,2.5 nauvis:gears/~plates_in/b0
```

`ok` is there as designed; `ghost` is on its way; `ghost SHORT` means the network holds none of
the item; `not-placed` means nothing is there yet. `fk look --design 0,0,16,16` draws the design
alone, no game needed:

```
look nauvis --source design: 1 plots, 34 entities, 0 invariant problems (. plot, : claim, ! mark, blank sanctioned)
................
......+.........
..Ci>>>>>>>v....
......i..i......
RRRR..AAAAAA....
RRRR..AAAAAA....
RRRR..AAAAAA....
RRRR...i+.i.....
+.v<<<<<<<<<....
..v.............
..v.............
..i.............
..C.............
```

`C` a chest, `i` an inserter, `A` the assemblers, `R` the roboport, `+` a pole, `>` `v` `<` belts
by direction. The plates run east along the top into the row's head, the gears come back west
along its foot and down to the chest.

## 8. Change it

Edit `game\gears.fk` (raise `rate=`, add an assembler, move the chest) and `fk build` again. A
build changes only what changed: unchanged rows and routes stay, and the diff names anything a
rebuild would take up.

## The path from here

`EXAMPLES.md`, in order, one idea each. Each chapter is a file in `docs\examples\`, the command,
the lines it prints, and the check lines you meet at that stage:

1. `gears.fk`: this file, the short form.
2. `beltless.fk`: robots and inserters, no belts at all.
3. `smelting.fk`: a row becomes a Stack of library cells.
4. `shared-belt.fk`: two items on one belt, `lanes A | B`.
5. `red.fk`, `sized.fk`: science into labs; `n=auto`; `fk plan`.
6. `plastic-cell.fk`: a cell of your own; a fluid port; `fk lab` to try it alone.
7. `library-lines.fk`: the library's cells, every one with its ports.
8. `ribbon-bus.fk`, `bus4.fk`: belts side by side, a bus, taps.
9. `mall-loop.fk`, `mall.fk`, `belt-switch.fk`: malls; circuits; a switch on a belt.
10. `power-shed.fk`, `steam.fk`: power, a switch, `cover power`.
11. `red-chain.fk`: a sealed module with ports; compiled once; `fk status`, `fk route NET`.
12. `busbase.fk`: a world design of plots.
13. The lab surface: `fk lab`, `fk standin`, `fk measure`, `fk capture slot`.
14. `mate.fk`: placements, `fk paste`, `fk capture --from`, the mismatch rule, `fk adopt`.

`FAIR-PLAY.md` is the game's rules, one page. `BUILDING.md` is how a base here is laid out, and
`STRATEGY.md` how fast to grow and why.
