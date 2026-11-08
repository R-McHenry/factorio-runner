# The toolkit (3.1): reference

`bin\` is on your PATH: `fk` is the one command, and it reaches the game for you. Your world is
`game\`: the factories (`.fk`, your base's plot `world.fk` among them), the world design
`nauvis.fk` that places the plots, the surface file `nauvis.surface`, the build script
`nauvis.build.fkb`. Edit those with normal file tools. The `.fkp` files beside them are the layouts
the toolkit writes: read them, never edit them. Every command takes `--help`. `START.md` walks the
first hour; each command below names the chapter of `EXAMPLES.md` that shows it.

## The commands

Design, on paper (no game):

| command | example | chapter |
|---|---|---|
| `fk check FILE.fk` | `fk check gears.fk`: the netlist checks (`no-feed`, `no-taker`, `cannot-carry`, ...) and the advice lines (`row-cell`, `gate-auto`, `limited-why`) | 1 |
| `fk rates FILE.fk [--hand N]` | `fk rates gears.fk`: every machine's utilization, every net's supply and demand, `BUFFER` lines; after a build the routed rates as laid | 1, 4 |
| `fk plan ITEM RATE \| --machines N` | `fk plan automation-science-pack 1/s`: the machines, furnaces and drills a rate takes, down to the ore; `--asm`, `--furnace`, `--mining-productivity N`, `--recipe ITEM=RECIPE`, `--raw ITEM,...` | 5 |
| `fk explain FILE.fk INST \| NET` | `fk explain gears.fk gears`: one instance as its entities and where its belts attach; one net and what limits it | 7 |
| `fk contract FILE.fk` | `fk contract busbase.fk`: every sealed module's ports with the rates they need and get | 8, 12 |
| `fk unfold FILE.fk INST` | `fk unfold mall.fk pipe`: an instance as a Literal, its entities pinned as they are | 13 |
| `fk dnp FILE.fk NAME[,NAME] [--off]` | `fk dnp world.fk sci3`: do not populate, laid out and routed, not built | 12 |
| `fk migrate-origins FILE [--write]`, `fk migrate-surface [--mode-only]` | a design written before 3.0.1 laid as it was laid; a surface file written before 3.1 as properties | |

The build and its steps:

| command | example | chapter |
|---|---|---|
| `fk build [--to STEP] [--from STEP] [--with 'STEP OPTS'] [--reroute NET] [--confirm] [--yes] [--wait]` | `fk build --to report_checks`: the build script in the background (`game\out\build.log`), design only up to that step; `fk build` pastes | 1 |
| `fk status [NET]` | `fk status`: the nets as the router left them, the unrouted with reasons, the compiled store, the log's last lines | 11 |
| `fk route NET[,NET] [--with ...]` | `fk route coal`: lay those nets again now, every other route kept; nothing pasted. `fk route --try X,Y X,Y`: how a belt would go from tile A to B | 11 |
| `fk place`, `fk report KIND` | `fk report checks`: one step of the script alone | |
| `fk hold NET`, `fk unhold NET`, `fk unroute NET` | `fk hold fe3p`: keep a net's route as it stands in every build; let it go; drop it | 11 |
| `fk seed NET ['splitter X,Y S' ...]` | `fk seed iron1 'splitter 9,30.5 S'`: pin a piece of a net; the router lays the rest through it | 11 |
| `fk compile MODULE` | `fk compile RedMod`: route a sealed module's inside once and store it | 11 |
| `fk wait 90s \| built AREA \| build \| 2m -- CMD` | `fk wait build`: the one way to wait, from a fixed budget (below) | 1 |

Looking at the world:

| command | example | chapter |
|---|---|---|
| `fk look X0,Y0,X1,Y1 [--design] [--zoom N] [--layers all] [--image]`, `fk look ID` | `fk look 6,6,22,22`: that area against the design, one verdict per designed entity; `--design` the design alone, no game | 1, 8 |
| `fk walk X,Y [--up 3] [--down 2]`, `fk walk X,Y --frame AREA` | `fk walk 112,40`: what the machine there waits on, up and down its belts; `--frame` a module frame drawn on the base, ready `port` lines | |
| `fk measure X0,Y0,X1,Y1 [--seconds 30]` | what the machines there really make, against the model | 13 |
| `fk robots [X0,Y0,X1,Y1] [--surface S]` | the roboports that stand against the design's chests: covered, redundant, how few would do | 14 |
| `fk placements` | the registry of what was pasted where | 14 |
| `fk paste FILE --at X,Y [--rot R] [--as NAME] [--surface S]` | `fk paste captured/std.json --at 2500,-300 --surface lab`: a captured file or blueprint string as ghosts, recorded | 14 |
| `fk register X0,Y0,X1,Y1 MODULE[/INSTANCE] \| --as NAME` | what stands there is that artifact | 14 |
| `fk capture --from ID\|AREA --into FILE [--names]` | `fk capture --from 10,10,16,14 --into captured/std.json`: what stands, as a Literal file anchored where it stands | 14 |
| `fk diff ID\|X0,Y0,X1,Y1` | the diff of a placement or an area | 14 |
| `fk adopt ID\|AREA [--write]`, `fk adopt X,Y --into FACTORY/NET` | `set` lines proposed for settings changed in the world; a belt placed by hand into a net's route | 14 |
| `fk read-terrain --surface S --area A [--entities] [--replace]` | `fk read-terrain --surface nauvis --area 0,0,200,200 --entities`: bare ground, and what stands, as constraints | 14 |
| `fk stats [--items A,B] [--history] [--page]`, `fk stock`, `fk capital`, `fk chart`, `fk alerts` | production and the run's history (free reads); what the base holds; working capital; the map explored; the alert panel | |
| `fk research [TECH...] [--first]`, `fk tech NAME`, `fk recipes [--enabled]` | what can be researched and what runs; a technology, or what opens an item | |
| `fk chat`, `fk say "text"`, `fk ack` | the game chat; ack dismisses what you have read | |
| `fk lua "CODE" \| --file F` | a Lua chunk in the game; its `rcon.print` comes back | |

Changing the world besides the build:

| command | example | chapter |
|---|---|---|
| `fk alone NAME [--surface S] [--size WxH] [--keep] \| --measure S \| --clear \| --list` | `fk alone plastic-cell --surface lab`: a module built alone on a slot of a surface (default lab) | 13 |
| `fk standin NAME supply \| feed \| ore` | `fk standin plastic-cell feed`: god-mode stand-ins on it, where things are free | 13 |
| `fk lab NAME [--from DIR] [--size WxH] [--keep] [--measure S] [--clear] [--list]` | `fk lab plastic-cell`: alone on the lab, then supply and feed | 6, 13 |
| `fk capture slot\|lab NAME \| world AREA \| design [FACTORY/]INST [--to FILE]` | `fk capture slot try`: something built, as a file a Literal reads | 13 |
| `fk fresh X0,Y0,X1,Y1` | belts, inserters and crafters holding stale items taken up | |
| `fk decon X0,Y0,X1,Y1 [--name E] [--cancel]` | the deconstruction planner, as you'd drag it | 14 |
| `fk request X,Y\|KEY ITEM N` | an item request on a standing entity: robots bring N | |
| `fk merge . A,B --into C`, `fk chests-to-net . C PROV REQ`, `fk net-to-chests . C NET --provider P --requester R`, `fk split-to-chest . FACTORY NET NAME --at X,Y` | two factories become one where they stand; chests and nets swapped | |
| `fk catalog learn NAME... \| --mod M \| --all-mods \| show [NAME] \| forget NAME` | a building the catalog lacks, read from the game (BUILDING.md, "Adding a machine") | |
| `fk stage list\|insert\|edit\|check\|next\|done\|rebase\|drop\|reorder` | the staged plan (below) | |
| `fk map save\|saves\|load\|restart\|pause\|resume` | the live map (below) | |
| `fk sim ...` | the speedrun planner, your own copy of the map (below) | |

Every command ends with the game's new alerts (grouped per kind), Rory's new chat lines and the
research line (`research military-3 34%, 41 packs/min, ~22 min left`; loud when nothing researches,
`RESEARCH: NONE`, or the labs stall, `RESEARCH STALLED`). A chat message comes back at the end of
every command until you `fk ack`: answer first (`fk say`), do the work, then ack.

### Packs

The game's pack is a run-level setting: `FK_PACK` in the environment, or the `pack` file in the
game folder, as the Sea Block template writes it. Two packs exist: the base game with Space Age
(the default), and Sea Block (`SEABLOCK.md` in that pack: Bob's inserters pick up and drop on any
side and reach further after research, and the router lays only what the force has researched as
the last stock read saw it, `out\avail.json`). The pack is not per surface and not swapped in a
run; the catalog, recipes and the plan all come from it.

### Waiting

Only `fk wait` waits, from a fixed budget. `fk wait 90s` waits that long, `fk wait built
X0,Y0,X1,Y1` until the robots have built the area, `fk wait build` until the background build is
done (its exit code), and `fk wait 2m -- COMMAND` runs a command and stops it at the cap. It asks
first: run it again, or add `--sure`. The run has 30 minutes of waiting in all
(`wait-budget.json`). Anything long goes in the background while you keep working.

## The build

`fk build` runs the steps of `game\nauvis.build.fkb` in order, in the background: it returns at
once and writes one line per step to `game\out\build.log` (its last line `fk build: done, exit
N`). One build at a time in a run: a second waits for the first. Each step writes
`game\out\<step>.txt`; the ones to read are `report_checks.txt`, `route.txt`, `size.txt`,
`cover_power.txt`, `diff.txt`, `report_bom.txt` and `apply.txt`. `fk build --to report_checks` is
design only (START.md walks it); `fk build --to diff` is a dry run against the world, nothing
written; `fk build --from route --to report_checks` runs part of it (`report_rates:2` names the
second `report_rates`), from the layout the last build left unless a source changed since.
`out\timings.txt` gives each step's time.

**A build changes only what changed in the source.** Unchanged rows and routes stay, a net is
patched when a machine or port is added to it, taken off it or moved (`out\route.txt` says
`patched` with how many carriers it kept), and the poles and roboports a build laid stay wherever
the new design leaves their spot free. When no branch fits, a net with belts is never laid anew by
itself: its belts stand, the new end alone fails (`no-route`), and `fk build --reroute NET` lays
the net anew when you want that (`--reroute mall1`: every net inside mall1). `--with 'STEP OPTS'`
adds options to a step for one build without editing the script (`--with 'cover power --fresh'`,
`--with 'route --effort high'`, repeatable).

**A build never quietly undoes what was changed in the game.** The diff step lists every hand
edit the paste would replace, remove or set back, and the template's `stop_on by-hand` holds the
paste there: put the ones to keep into the design (`fk adopt`, a Literal, a `set` line) and build
again, or `fk build --yes` pastes over them once. A world entity on a design tile that the toolkit
did not paste is a `mismatch` and is refused, never pasted over (chapter 14).

**Room for routes.** A belt fed into a machine by an inserter takes 2 tiles beside it; two machines
sharing a belt between them need 3 tiles apart; a 1-tile gap holds a direct inserter only. Keep a
module's ports and new machines off the path of a main belt that stands. A patch that won't fit
fails alone and leaves the rest standing: move the new machine a few tiles, or `--reroute` the
net it joins. A net that finds no way through another's belts may cross by promotion (a straight
run of the other route becomes an underground pair; `promoted` in `out\route.txt`).

### How route works, and what you set

Each net is routed **once**, in order, and keeps what it laid. A net that fails is tried again with
the nets laid near its ends (up to `--group-max 8`, for `--group-budget 3` seconds), and if they
don't all fit the old routes stay and the net gets one last search of its own. On the `route` line
of the build script, or in `--with 'route ...'`:

- `--first coal,fe3p,mall/*`: those nets first, in that order.
- `--skip st3o,oil/*`: left to you (`manual` in route.txt): a Literal, `fk adopt`.
- `--retry-failed changed` (the template's): a failed net is not searched again while its ends
  and the ground around it stand as they did (`always`, `never`; `--again NET` or `fk route NET`).
- `--failed keep` (the template's): a net that fails keeps the route that stood where its tiles
  are still free, so the paste goes on (`drop` loses it).
- `--area X0,Y0,X1,Y1`: only nets with a belt or an end in that rectangle are routed again.
- `--ug red|blue|turbo` (default `any`, the fastest tier the network can build) for tunnels;
  `--pipe copper`; `--m2m` (a many-to-many net as runs above one lane); `--hand any` (the lowest
  hand the network can build; `hands minimize` raises only the connectors whose flow needs more).
- `--compiled no`, `--store DIR`, `--reuse-lab`: the compile store (chapter 11).

The route step writes one file per net, `out\design\<factory>\nets\<net>.net` (its status, seed and
carriers), and the next build starts from these, even when the last one stopped mid-route. `fk
status`, `fk hold`, `fk seed` and `fk route NET` work on them (chapter 11). A net the router can't
find a way for can be sent through a piece from `game\routing.fk` (LANGUAGE.md). `out\route.txt`
has a `group` line for each net that got a group try, a `ribbon` line per ribbon (chapter 8), and
`compiled` lines for the store. `FK_NEGOTIATE=1` turns on an opt-in negotiation pass; it is
measured, not the default.

### Sealed modules compile once

Each sealed module is routed alone in its frame, the innermost first, then the nets outside them.
Every module routed whole is stored in `game\out\compiled\<module>\<key>.json` (`current` beside it
points at the last key laid); a build that places the module lays the stored inside wherever it
stands now and routes only the nets that cross frames. The key hashes everything the inside is
laid to (the frame, what stands in it, each net's ends, items, lanes and taps, the children's keys,
the catalog and terrain facts, the router's settings, seeds and held routes, the router version),
so a change inside routes it again by itself; nothing to clear. A module built alone on the lab
from your game folder writes the same store. Entries written by an older router are not read, and
the route step and `fk status` say so once. Chapter 11 shows the lines.

### After the route: circuits, the paste, robots

Steps after a `later:` line in the build script run once the rest is done, the paste included:
reports only. Circuits come from the source and are laid just before the floorplan
(`out\floorplan.txt`, the `circuits` lines, chapter 9); a changed limit is written onto the
inserters that stand. A build changes what stands in one of three ways, and `out\apply.txt` names
each: **written in place** (settings), **upgraded** (another entity of the same fast-replace group
on the spot: inserter tiers, assembler 1 to 3, belt tiers, chests, furnaces, poles) or **laid
fresh**. Wires follow the design: after the paste, and again when `fk wait built` finds the robots
done, the build adds the missing red, green and copper wires and cuts the ones between your
entities the design lacks (`wires: 3 added, 1 cut`). A build also takes back what it built before
and the design no longer has (`out\own\nauvis.json` remembers what it pasted). After the paste the
robots build the ghosts from stock: `out\report_bom.txt` says what the network can't cover
(`short-stock`, `needs-research`), and a ghost that stays is a blocked build (`fk look`: `ghost
SHORT`, `ghost needs-research`).

## The build script

`game\nauvis.build.fkb` is the list of steps `fk build` runs, one per line, each reading what the
steps before it made; the template is in your game folder with a comment per line. Keep it or
tweak it; leave `apply` out while you design.

- `stop_on no-destination cannot-carry by-hand` holds the paste on those checks (`malformed`
  always does). Add `stop_on no-route` to hold it until every net routes, `stop_on reach` for
  ghosts no robot reaches, `stop_on no-power`, `stop_on short-stock`.
- `size` counts the `n=auto` lines (`fk build --to size`; `size --payback 2h` also counts what
  the design draws over two hours).
- `route --only stale --retry-failed changed --failed keep --effort normal --hand any` is the
  template's line (above).
- `hands minimize` raises only the hands whose flow needs more (`out\hands.txt`); `hands
  maximize` takes the top rung everywhere; `--downgrade` may take a standing fast hand back.
  Where even the best hand is too slow the router lays 2 or 3 inserters between a machine and its
  belt.
- `tier minimize --allow yellow,red` lays red belts where a yellow lane is too thin (`--red N`
  caps how many; `tier maximize` the fastest allowed everywhere; never down a tier without
  `--downgrade`).
- `cover robots` lays roboports where the design's logistic chests need them (none on a free
  surface; it places no storage chest, so put one in your hub); `cover power --pole any` the
  poles, medium where the network can build them, else small (`floorplan --bridge-power any` the
  same for the bridge to the plant's grid). The choice comes from `out\avail.json`, written by
  `report_bom --stock live`.
- `diff --scope stale` and `apply --scope stale` paste only what changed; `apply --area A` for
  one region. `write_layout` writes the `.fkp` files the next build keeps to (the one before is
  kept in `out\fkp-history\`, the last 8 of each).

## Surfaces

A surface is a game surface with a surface file, `game\<surface>.surface`: what is true of the
surface itself. The world design (`design nauvis.fk`) says where the plots go (chapter 12).

```
surface  nauvis
terrain  map                                  # map.terrain.json: the bare ground, read at kickoff
catalog  base
design   nauvis.fk                            # the plots, from the world design
watch plots:      keep                        # something placed by hand in a plot: kept, listed
watch sanctioned: mark 2h                     # ... outside every plot: marked for two hours
notify   nearby                               # alerts for what is built: nearby, all or off
target automation-science-pack 10/min         # a rate the whole surface should make
```

Properties: `free` (things are free there: a paste's ghosts are revived at once, nothing is built
by robots, a slot is emptied by a direct destroy, a look may take the whole design, `notify`
defaults to `all`, `cover robots` places nothing; a file without `free` is a base), `force NAME`
(whose the paste is, default `player`; the lab's is `force lab`), `terrain NAME|none`, `catalog`,
`design`, `notify`, `remind`, `watch`, `target`, `robots`, `claim`, and the older `place` lines
(`place iron = iron plot 40,-20 30x30`; `fk migrate-surface` writes a world design from them). A
file written before 3.1 had `mode play|lab`: still read with a note, `fk migrate-surface
--mode-only` rewrites it. The lab's file is `surface lab`, `free`, `force lab`; it needs none.

Every top-level sealed instance of the world design is a **plot**: its name the factory's, the
file it is imported from the plot's `.fk`, `@(x, y)` its corner in map coordinates, `size WxH` its
ground; `gap N`, `retired`, `temporary until ...` and `@patch(resource)` go on the instance line.
A plot owns every tile inside it, plots never overlap, and a plot may not hold the surface's own
buildings, which is why a template gives a new run one plot with the kit and the plant declared
inside it. Move a plot by changing its `@(x, y)`: the build takes up what stood on the old ground.

### Building on the lab surface

Chapter 13. The lab is a game surface like any other (`free`, `force lab`), where none of the
fair-play rules apply and Rory watches with `/lab`. `fk alone NAME --surface lab` (or `fk lab
NAME`, which adds the stand-ins) gives NAME.fk a workspace `lab\NAME` with a surface file that
places it on a slot of its own, runs the build script there, and the apply pastes and revives it.
A blueprint goes on the lab as a Literal in a file of its own, then `fk lab try`. `fk alone`
builds a module alone on any surface (`--surface nauvis`: robots build it, nothing is destroyed).
Power, stock and infinity feeds are god-mode stand-ins you ask for by name, only where things are
free: `fk standin NAME supply | feed | ore` (`ore --copy-ore AREA --to X,Y`, `ore --ore iron-ore
--area A [--amount N]`, `--clear-ore`). `fk lab NAME --from stages\4` builds from a stage's folder.

## Bounds and obstacles

A design is laid out from its source and the terrain file, never from the live world. To keep a
factory in bounds, give it a plot. To make the design go round what stands in the world, read it
in on purpose: `fk read-terrain --surface nauvis --area X0,Y0,X1,Y1 --entities` puts every built
entity there that the design does not own into `map.terrain.json` as blocked tiles, and the next
build routes, poles and roboports round them. Ghosts, trees, rocks, cliffs and units are never
obstacles; without `--entities` only the bare ground (water, cliffs, ore) is merged; `--replace`
drops everything first. `fk robots [AREA]` reads the standing roboports against the design's
logistic chests and changes nothing (chapter 14).

## Placements (3.1)

Chapter 14. A placement is one paste: which module (or file) stands where, in
`out\placements\<surface>.json`, one record per paste (id, module and instance, artifact
`store:<key>` or `rows:<sha>`, origin, rotation, area, tick, source `build | alone | paste |
register`); a rebuild of the same id replaces its record. A placement id brings its surface: `fk
look ID`, `fk diff ID`, `fk adopt ID` and `fk capture --from ID` read the surface the placement
stands on; for an area on another surface give `--surface S`. A value that starts with a minus
sign goes with `=`: `--from=-300,-300,-100,-100`, `--at=-300,-300`.

**The mismatch rule.** A build pastes only onto tiles it can explain. A world entity on a design
entity's tiles that the toolkit did not paste there is a conflict: the diff names it and its
owner (`placement <id>`, `hand edit by <player>`, `unknown`), the apply refuses those tiles with
the command that resolves each (`fk register`, `fk capture --from`, `fk adopt X,Y --into`, `fk
decon`), and pastes the rest. No `--yes` overrides it. Mating is no mismatch (same name, position
and direction: `ok`); inside another placement's area what the design does not match is `placed`.
`fk adopt ID|AREA` proposes `set` lines for settings changed in the world and never writes without
`--write`.

## Saving, loading and starting over: `fk map`

`fk map save NAME` saves the live map (the game goes on); `fk map saves` lists the run's start,
the map the server runs and your saves; `fk map load NAME` saves the map as it stands first
(`before-load-STAMP`), then restarts the server on NAME; `fk map restart` is load start, set up as
the kickoff set it up; `fk map pause | resume`. A load restarts the server (about half a minute).
Your designs in `game\` stay as they are.

## The speedrun planner: `fk sim`

A second game of your own map on this machine: same seed, same first tick, yours to try a plan in,
about 20 times real time, never the live game. `fk sim fetch [--live now]` (your map as it stood
after the kickoff, or now), `start [NAME] | stop | status`, `kickoff`, `speed 100`, `run 3600 |
until T`, `save NAME | load NAME | saves`, `place FILE @X,Y` (built at once and free; a `.fkp`, a
blueprint string or `{"bp": ...}`), `paste FILE @X,Y` (ghosts for the robots), `remove AREA | decon
AREA`, `play plan.sim` (a file of actions), `game stats | look | lua | research ...` (any game
command on the planner). A `.fkp` is in its design's own tiles, so `world.fkp` goes in at the world
plot's corner. The planner's files are in `sim\` of your run folder. **Explore mode** (`fk sim
explore on [--budget 36000] [--minutes M]`) hands the planning to the planner in short rounds, each
with a manifest of items to make; `status`, `next`, `off`, `archive`, `best --to plan.sim`,
`reset`.

## Planning ahead: `fk stage`

`fk stage` keeps a plan of stages in git beside `game\` (`.stage\`, folders `stages\N`): `main` is
what is built, the plan one commit per stage on top of it, each commit's message the stage's spec.
`fk stage list` (the stages, each one's plates per minute and state, and the horizon: the planned
base against the built one); `insert "oil plant" --goal "..." --needs "..." --rates "plastic-bar
30/min" --test "..."` (`--after K`, `--first`); `edit N`; `check N [--render [AREA]]` (stage N
built in `stages\N` through report_checks); `next [--apply]`; `done [N]`; `rebase [-m "why"]`;
`drop N`; `reorder N --after K`. A stage folder is a scratch copy of the whole game folder. When
main moves the plan replays stage by stage, and a conflict comes back with markers. A plan 1.5 to
2 times the built base stays about the same span of play ahead.

## What `fk look` says

Every designed entity in the area gets a verdict: `ok`; `ghost` (robots on their way); `ghost
SHORT` (the network holds none of the item); `ghost needs-research` (`fk tech ITEM` says which);
`not-placed`; `dnp`, `dnp-standing`; `other-entity`; `settings-changed` (`re-laid` for a belt,
underground, splitter, inserter, pipe-to-ground, pump or drill standing turned: the next build
lays it again); `UNPAIRED` (an underground with no partner); `other-item` (a belt or pipe holds an
item its net doesn't; `fk fresh AREA` takes those up). Anything the design doesn't explain is
`claimed`, `marked` (placed by hand, listed under "changes here" with who placed it), `placed`
(inside another placement) or `unowned` (the next build removes it).

`--zoom N` draws N tiles in one cell (1, 2, 4, 8, 16 or 32; chapter 8 shows zoom 4); from zoom 8 up
each plot is its outline with its name and machine count, counts per cell, no verdicts.
`--layers NAME,...` (or `all`) adds a panel per layer, design | live: `robots` (logistic areas,
`!` a ghost outside every construction area), `power` (`X` a machine with no power, `~` low),
`defense` (turret ranges, `T` a turret, `0` one with no ammo, `@` a nest), `pollution`, `pipes`
(each pipeline in its own letter), `recipes` (each machine's recipe tag and status: `*` working,
`n` no ingredients, `f` output full, `P` no power), `tags` (your plots and instances). `--image`
writes a PNG instead (`out/look-X0_Y0_X1_Y1-zN.png`).

`fk walk X,Y` walks a machine's belts: each net in and out, how full each lane is, the machines
that feed it and take from it and their state, as deep as `--up` and `--down` say, marking where
the flow stops; the first starved machine upstream whose own inputs are fine is the place to
look. `fk walk X,Y --frame X0,Y0,X1,Y1` checks a module frame before you draw it: every belt that
crosses the edge as a ready `port` line, and "sealable as drawn" when only belts cross.

## The checks

Checks are report lines, never errors: the build goes on and tells you, and `stop_on` in the build
script names the ones that hold the paste. The capitals are the point: a loud one means something
will stop working. The chapters quote each where it first appears; this is the list.

**Loud:** `CLOG` (a mixed belt or chest an item can fill and stop; `limited "why"` or `split`,
chapter 9), `SHARED_SINK` (a chest on a net with machines: whoever stands first gets its fill),
`SUSHI_SINK` (a sushi net reaches a machine that doesn't take every item), `SUSHI_LIMIT` (a
`limit=` on a mixed belt's chest), `SPILL` (a net's route touches another net), `WRONG-LANE`
(chapter 4), `NO_POWER` (chapter 1), `no-source` / `power-short` / `switch-never` /
`domain-overlap` / `copper` (chapter 10), `TILE_SHARED` (two pieces of one net on one tile),
`UNREACHED` (diff: ghosts no powered roboport's construction area reaches), `REACH` (report_bom:
the same per item; `stop_on reach`).

**What is where:** `no-feed` / `no-taker` (an input with no net; an output going nowhere),
`short-feed` (chapter 1), `cannot-carry` (a net names a machine with no endpoint for its items;
the template stops on it), `feed-rides-on` (chapter 9), `no-limit` (chapter 6), `no-rate` (chapter
2), `could-pair` (chapter 3), `no-match` (a `set` that matches nothing), `two-nets`, `port`,
`crosses-frame`, `overlap` (chapter 11), `CIRCUIT`, `cannot-filter`, `no-signal`, `no-wire`,
`wire-too-far`, `wires-share`, `gate-open`, `require-not-met`, `gate-auto`, `limited` (LANGUAGE.md,
"Circuits"), `taken-up` (the build takes up a lot and the robots need minutes; `fk merge` when a
factory moved), `rate-not-met` / `target-not-met`, `size` / `does-not-fit` (chapter 5),
`pipe-too-long` (a pipe segment past 320x320 tiles; a `Pump`), `too-thin` (a lane or inserter
runs full: `tier minimize --allow yellow,red`, a second belt), `no-route` / `partly-connected`
/ `unused`, `no-destination` (a maker whose product reaches none of its sinks; the template stops
on it), `bootstrap` (chapter 9), `no-robots` (a logistic chest outside every logistic area, chapter
2), `on-water-or-cliff` / `no-water` / `no-ore`, `by-hand` (diff: who changed what, where, and what
the paste does; `out\hand.txt` keeps every one), `mismatch` (chapter 14), `re-laid`,
`short-stock` / `needs-research`, `malformed` (always stops), `no-terrain`.

`fk check FILE` prints the check lines, then `advice` lines about how the source is written
(`limited-why`, `row-cell`, `gate-auto`); only `fk check` prints advice. A refusal is not a check:
a design the language can't take stops `fk check`, `fk rates` and the build with one error line
naming the file, the line and the fix (`no-fluidbox`, a missing module parameter, an index past
the end of a list, `unknown template`).

## Reading the base

- `fk stock`: what the base holds, per item: chests, roboports, your pack and the robots in the
  air (a robot in a chest is an item and does nothing).
- `fk capital [--area A]`: working capital, what the base owns in raw units, working against idle;
  `--park AREA --why TEXT` leaves an area out on purpose.
- `fk stats`: the whole base in one report: research and its rate, capital, made and used a
  minute, power by domain, machines by state, the trend. `--items iron-plate,copper-plate
  [--window 10min] [--series 30] [--fluids]`: the production panel's numbers. `--history`: every 5
  game minutes a sample keeps what changed (items, power, research, machines working, short, full
  or unpowered); `--page` writes `game\out\stats\index.html`.
- `fk chart`, `fk alerts`, `fk research`, `fk tech NAME`, `fk recipes [--enabled]`, `fk request
  X,Y|KEY ITEM N` (robots bring N to a standing entity), `fk catalog learn NAME` (BUILDING.md).

## Programming the game yourself

Each `fk` command is Python sending Lua over RCON, readable in `.toolkit\fkcore\` (`link.py` holds
most Lua chunks; `alone.py`, `standin.py`, `sim.py`, `capture.py` their own). `fk lua "CODE"` runs
anything the game API can do that no command does yet, and your own scripts (Python or PowerShell
in `helpers\`) chain `fk` commands. The rules are the same either way (`FAIR-PLAY.md`): you build
by ghosts, and you may press any button a player could press. What you place outside the design
reads `marked` in `fk look`; `fk capture world AREA` keeps it for good as a Literal. The chunk
goes to the game as you wrote it (a 40 kB file works); a big answer is better written to a file on
the game host (`helpers.write_file`). The link refuses Lua that registers an event handler, a
command or an interface: that made every save unjoinable once. Try risky Lua on the planner first
(`fk sim game lua ...`).
