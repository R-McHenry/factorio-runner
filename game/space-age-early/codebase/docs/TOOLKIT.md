# The toolkit

`bin\` is on your PATH: `fk` is the one command, and it reaches the game for you. Your world is
`game\`: the factories (`.fk`, your base's plot among them), the world design `nauvis.fk` that
places the plots, the surface file `nauvis.surface`, the build script. Edit those with normal file
tools. The `.fkp` files beside them are the layouts the toolkit writes: read them, never edit them.

## The loop

```
fk rates main.fk                 the factory's numbers on paper: every machine's utilization, every
                                 net's supply and demand, what limits it (the binding chain)
fk check main.fk                 the netlist checks (no-feed, no-taker, cannot-carry, ...)
fk build                         the whole build script: lay out, route, power, check, paste
fk build --to report_checks      design only: everything up to the checks, nothing pasted
fk build --to diff               a dry run: the design against the world and what the build would
                                 take up; nothing written, nothing pasted
fk look X0,Y0,X1,Y1              that area now, against the design
fk look X0,Y0,X1,Y1 --design     the design alone, as it will be built
```

Only `fk wait` waits, from a fixed budget. `fk wait 90s` waits that long, and `fk wait built
X0,Y0,X1,Y1` waits until the robots have built the area. It asks first: run it again, or add
`--sure`. The run has 30 minutes of waiting in all; `wait-budget.json` in your folder says how
much is spent.

One thing `fk rates` gets wrong: it shares a short net in proportion to what each machine wants,
but the game doesn't. The machine that reaches the items first (the first inserters along a belt,
one side of a splitter) takes its fill, and the ones further on get what is left, or nothing. So a
net that is short on paper starves its far machines rather than slowing them all a little: give it
more supply than demand, and give a chest on it a `limit=` (`SHARED_SINK` says where). A bus
(`Bus<...>`, see LANGUAGE.md) is a pool on paper: what its loads bring, its taps share; along the
real bus the first tap takes its fill first.

After `fk build`, `out\report_rates.txt` also has the **routed** rates: what the belts and
inserters as laid let through, with the first machines along a belt served first, as in the game.
A belt lane carries 7.5 items/s (yellow; red 15, blue 22.5, turbo 30), a fast inserter 2.5/s, a
long-handed one 1.2/s, a plain one about 0.85/s, more with the inserter capacity research (the
build uses your force's, as the last build saw it; the report's last line says which). Each
routed section's first line says whether its machines settled; where they did not, its numbers
are approximate. `fk rates main.fk` shows the same routed section after its nominal one, on
`main.fkp` as the last build laid it out (`--hand N` for other inserter hands).

A net is one lane. A row's inserters drop its products on one lane of its belt, so a net from one
row carries at most 7.5/s on yellow, and the routed rates count it so: each net's critical
connector is its own lane. Two nets can share a belt, one on each lane (`lanes ore_ | fuel`,
LANGUAGE.md): one belt to lay instead of two, and the two never stop each other, since a full
lane holds only its own items. A net on both lanes (a two-sided MinerRow's ore) gets one lane
when it is side-loaded onto a shared belt: 7.5/s there, and `too-thin` says so where it brings
more.

`fk build` runs the steps of `game\nauvis.build.fkb` in order and prints one line per step (one
build at a time in a run: a second `fk build` waits for the first). Each step writes its report
to `game\out\<step>.txt`: read the one that matters
(`out\report_checks.txt`, `out\route.txt`, `out\apply.txt`). A build changes only what changed
in the source: unchanged rows and routes stay where they are, a net is patched when a machine is
added to it or taken off it, and it is routed again when one of its ends moves (a chest one tile
over, too).

Rebuilds keep what stands. The poles and roboports a build laid stay where they are wherever the
new design leaves their spot free, and new ones go only where something new needs them, so power
and the robot network keep running through a rebuild (`out\cover_power.txt` says how many were
kept; `cover power --fresh` or `cover robots --fresh` in the build script lays them all anew).
Switching the pole kind (`cover power --pole small-electric-pole` while medium poles are short)
keeps the poles of the old kind the last build found standing and lays the new kind only where
the old one never stood. `--pole any` switches by itself, the way `--hand any` does: medium poles
while the network can build them (they are in the kit at the start), small ones when it cannot.
Small and medium poles are both 1x1, so a switch keeps what stands; `big-electric-pole` and
`substation` are 2x2 and are named by hand only.
Old poles and roboports the design no longer has stay up until the design's own stand there, and
the first build after that takes them up.

Routes stand too. A net with a new machine keeps its belts and gets only a branch to the new
machine, laid into what stands: a splitter in place of a straight belt, the belt's end run on, a
belt side-loaded onto it (a new driver), or an inserter straight off the belt. A machine taken
off a net takes its branch with it. A belt two nets share is patched the same way: a new
consumer of both gets a branch of both lanes, and a new driver of one net side-loads onto that
net's own lane. `out\route.txt` says `patched` with how many of the net's carriers it kept, or
`routed again:` and why, when no branch fits and the net is laid anew. The route follows the
net's ends, not its name: a net whose sink changed under the same name (`X -> lab` to `X -> px`)
is never kept as it was. So a patch is cheap: a row beside the others, a chest one tile over, one
more machine on a net, one more net into what stands. The design the next build keeps to is the
one the last full build wrote (`write_layout`, just before the paste); `fk build --to diff` and
`--to report_checks` write nothing, so try changes with them freely.

Circuits come from the source (`LANGUAGE.md`, "Circuits"): the properties on objects (`set
NAME.out -> CHEST: logistic=ITEM<N`), a crafter's `guard`, wires and conditions. The build lays them on the layout just
before the floorplan (`out\floorplan.txt`, the `circuits` lines, says what each one set), and the
.fkp shows them on the entity lines (`if=`, `logistic=`, `read=`, `filter=`, `signals=`) and as
`wire` / `w` lines. Change a limit and the next build lays the inserters it touches fresh, so the
game has the new one (`out\apply.txt`: "with new circuit settings"); no script sets them, and a
rebuild never loses them.

A build also takes back what it built before and your design no longer has: a row you replaced,
the old ground of a plot you moved or shrank, a factory you emptied, its unbuilt ghosts too. The
toolkit remembers what it pasted (`out\own\nauvis.json`), so this holds whatever `watch` says.
A belt or pipe it lays again under another net, while it still holds the old net's items or
fluid, is taken up and laid fresh in the same build.

On ground a paste can't use (uncharted ground, with no radar or character to chart it), each
entity is laid as a single ghost instead, carrying its recipe, underground end, requests, circuit
settings, filters, fuel and wires, so robots build it the same way. A setting the game won't put
on a ghost (a requester chest's trash-not-requested, a chest's bar) is named in `out\apply.txt`
under SETTINGS: set it in the game, or build again once a radar charts the ground.

After `fk build`, the ghosts are in the game and the robots build them from the network's stock.
Before the paste, `out\report_bom.txt` says what the network can't cover: its `STOCK:` line, and
short-stock / needs-research per item. A ghost that stays is a blocked build, not a slow one: `fk look`
marks it ghost SHORT or ghost needs-research (below), and waiting won't change that.

Every command ends with the game's new alerts (grouped per kind), Rory's new chat lines and the
research line (`RESEARCH: NONE` when nothing researches).

## How many machines and drills for a rate

In a factory you write, `n=auto` on a row lets the build count it: the fewest machines that meet
the surface's `target` lines, your asserts and what the row's consumers take (LANGUAGE.md, "Counts
the build chooses"). `fk build --to size` shows the counts and what each serves in
`out\size.txt`; `fk rates FILE.fk` shows the rates at them.

`fk rates` checks a factory you have written; `fk plan` answers before you write one, from an
item and a rate or from the machines you want on that item:

```
fk plan chemical-science-pack --machines 15
                                 what 15 blue science assemblers take, every step down to the ore
fk plan chemical-science-pack 1/s
                                 the same for a rate (60/min, 0.5/h; a bare number is /s)
fk plan electronic-circuit 10/s --raw iron-plate,copper-plate
                                 the plates come from elsewhere: plan only the rest
```

One line per item of the recipe tree: the rate it takes (/s and /min), its recipe and machine, and
how many machines that is (`machines` as a fraction, `build` rounded up). Under an item that feeds
several recipes, `for ...` says how much each one takes, which is what its net carries to each row.
Then the raw inputs: ore and coal (furnace fuel included) with their electric mining drills, crude
oil with its pumpjacks, water with its offshore pumps. For 15 blue science assemblers that is 11.25
iron and 7.03 copper plates a second: 36 + 23 stone furnaces and 15 more for steel, then 23 iron,
15 copper and 7 coal drills.

The plan runs every machine at full speed, without modules or beacons: assembling-machine-2 for
crafting (a row's default is assembling-machine-1, two thirds as fast: `--asm
assembling-machine-1` gives a row's numbers), stone furnaces burning coal for smelting, chemical
plants, oil refineries, and electric drills at 0.5 ore/s. These change it:

- `--asm assembling-machine-1` or `--asm assembling-machine-3`
- `--furnace steel-furnace` or `--furnace electric-furnace` (no coal)
- `--mining-productivity N`: your research level, +10% ore a level
- `--recipe ITEM=RECIPE`: the recipe for an item that has several (the notes name them)

Petroleum gas comes from basic oil processing, the oil recipe the research opens first. With
`--recipe petroleum-gas=advanced-oil-processing` it comes from advanced oil processing, without
cracking, and the heavy and light oil nothing takes show as `surplus` (a refinery stops when one
of its outputs is full). Pumpjacks are counted at 100% yield, 10 crude oil a second each: a well's
yield is in its tooltip.

## Planning ahead: `fk stage`

`fk stage` keeps a plan of stages in git beside `game\`. `main` is what is built; the plan is one
commit per stage on top of it, and each commit's message is the stage's spec: its goal, what it
needs first, the rates it should reach, and the test that says it's done. A stage holds source only
(`.fk` files, the world design, the surface file, the build script, and the file each Literal reads,
such as relay.json), and its layout is computed when it is checked: `fk stage edit` keeps that file
in the stage it edits, the stages replayed after it get it, and a file outside `game\` or in `out\`
is not taken. A stage keeps its number however the plan changes.

```
fk stage list                    the stages in order, each one's plates per minute and state, and
                                 the horizon: the planned base against the built one
fk stage insert "oil plant" --goal "..." --needs "..." --rates "plastic-bar 30/min" --test "..."
                                 a new stage at the end (--after K, --first), with its folder
                                 stages\N: game\ as the stages before it leave it
fk stage edit N [--goal ...]     stages\N's .fk, surface and build script (and the spec given) into
                                 the plan; the stages after it replay onto it
fk stage check N [--render [X0,Y0,X1,Y1]]
                                 stage N built in stages\N through report_checks: its check lines,
                                 its rates against the stage before it and against its spec, and
                                 what it builds, with what the base before it makes of each item
                                 and the stock game\'s last build saw; nothing touches the world.
                                 --render draws the design it laid out (the plots it changes, or
                                 the area); stages\N\out\render.txt keeps it
fk stage next [--apply]          the next stage's spec and its diff; --apply puts it into game\
fk stage done [N]                game\ as it stands becomes main, with stage N done; the rest of
                                 the plan replays onto it
fk stage rebase [-m "why"]       game\ as it stands becomes main (a hand fix adopted); the plan
                                 replays onto it
fk stage drop N                  stage N leaves the plan
fk stage reorder N --after K     stage N moves (--first: to the front)
```

A stage folder is a scratch copy of the whole game folder, built layouts and terrain included:
design in it with normal file tools, `fk stage check N` it as often as you like, and `fk stage edit
N` takes its source into the plan. Until then its edits are its own ("unsaved edits" in the list),
so a worker can try a change there while another builds in `game\`.

Replaying is the key operation. When main moves (a stage done, a hand fix adopted), the plan is
replayed onto it, stage by stage. A stage whose files reality didn't touch replays cleanly; one
whose lines reality changed comes back with conflict markers in its files (`<<<<<<< what is built`
... `=======` ... `>>>>>>> stage 5`) and a CONFLICT in the list: settle them in its folder, then
`fk stage edit N`. Every rewrite keeps the plan before it as a tag (`plan/rev-N`), so you can see
how the plan changed. A stage finished out of order is fine: `fk stage done N` says which stages
before it stay planned.

The horizon is in plates per minute, iron plus copper, as the rate model's furnaces make them:
plates grow from the first drill on, and every part of the base is made of them. Science per
minute is shown beside it. A plan that reaches 1.5 to 2 times the built base stays about the same
span of play ahead at any size of base: nearer 2 while builds take long, nearer 1.5 while the base
is young and changes fast.

The plan's repository is `.stage\` and its folders are `stages\`, both in the run folder, outside
`game\`. `fk stage` changes `game\` only with `next --apply`.

## The lab: `fk lab`

The game has a second surface, the lab, where you try a design for real before it goes into the
base: its own force with the research done, building at once, free materials, and none of the
fair-play rules (`docs\FAIR-PLAY.md`, "The lab"). Rory watches it with `/lab`. While your
builders work and there seems to be nothing to do, the lab and planning (`fk stage`) are the two
things worth doing, and workers can do most of the lab work.

```
fk lab NAME                      NAME (a factory the world design places, or NAME.fk) built on
                                 its own lab slot: powered, its requester chests filled, burners
                                 given a first load of coal; says where Rory sees it
fk lab NAME --from stages\4      the same from a stage's folder (or a scratch copy of game\)
fk lab NAME --size 60x40         its plot size, when no plot places it (default 100x100)
fk lab NAME --measure 60         what its machines really make over 60 s, against the rate model
fk lab NAME --clear              its slot emptied and freed
fk lab --list                    the slots in use
```

- The lab has no edge, and a factory of any size fits. Each factory gets its own slot, sized to
  it in 200-tile cells, and keeps it while it fits; building it again empties the slot first,
  and `fk lab` generates the ground under it. Its workspace is `lab\NAME` in the run
  folder, never `game\`.
- Drill rows get their ore seeded under them, so a smelting block runs as designed. A requester
  chest is filled with what it asks for, so the machines run as fast as they can: the measure
  shows what the machines do when their inputs keep up, where the rate model counts the
  `rate=` you wrote (the gears example: 120 gears a minute in the lab, 37.5 on paper at
  `rate=1.25/s`).
- Let it run a minute or two before `--measure`: a backlog that piled up while it started reads as
  speed. `--measure 60` takes a minute; it tops the requesters up first.
- A good worker task: "Try stage 4's mall in the lab (`fk lab mall --from stages\4`), let it run
  two minutes, measure it, and report what each maker makes and what it waits for."

## What `fk look` says

Every designed entity in the area gets a verdict:

- `ok`: there, as designed
- `ghost`: its ghost is there and the network holds the item: robots are on their way
- `ghost SHORT`: its ghost is there and the network holds none (or too few) of the item: robots
  can't build it until you make the item (an assembler row into a provider chest) or bring it
- `ghost needs-research`: its ghost is there and the item's recipe isn't researched: `fk tech ITEM` says
  which technology opens it and what it costs
- `not-placed`: nothing there (destroyed, or never built; the next build restores it)
- `other-entity`: something else stands on its tiles
- `settings-changed`: there but turned, or with another recipe. A belt, underground, splitter, inserter,
  pipe-to-ground, pump or drill that stands turned (or as the other underground end) says
  `re-laid`: the next build orders it out and robots lay it again as designed
- `UNPAIRED`: a built underground belt with no partner in reach, or with another end of the same
  kind (two outputs) so no tunnel forms; the line gives its position. A ghost pair is not built
  and is not reported
- `other-item`: a belt or pipe holds an item or fluid its net doesn't (old items from a
  rebuild); on a belt two nets share, lane by lane (copper on the iron lane is wrong there). One
  the build laid under another net says so, and the next build renews it by itself; for the
  others, `fk fresh X0,Y0,X1,Y1` takes those belts up, and the next build lays them fresh

Anything in the area that the design doesn't explain is:

- `claimed`: inside a claim (the starter kit)
- `marked`: something placed by hand, kept for now (it is listed under "changes here", with who
  placed it, and a hint when it looks like a fix to a net)
- `unowned`: nobody owns it; the next build removes it. This is also what the toolkit built and
  your design no longer has ("the toolkit's own, dropped from the design")

## The checks

Checks are report lines, never errors: the build goes on and tells you. Read them; most are
worth fixing in the source. They come in two voices, and the capitals are the point.

### The loud ones: something will stop working

A belt that fills, machines that starve, a build that never happens. Read these now.

- `CLOG`: a net of two or more items (sushi), or an unlimited chest fed two or more items by
  separate nets: an item that comes faster than it is used can fill it and stop the others. No
  tool can tell whether what feeds a mixed net is limited, so its line goes in two ways only. One:
  the route is one inserter from the source straight into each sink, which takes only what that
  sink accepts (a requester beside a one-off crafter, `Lab`s around a requester of packs); the route
  step says so. A belt in between, or a second inserter, and it can clog again. Two: your design
  says it limits what goes on the net (`net NAME limited:`), which limits nothing itself
- `SHARED_SINK`: such a chest on a net with machines: whoever stands first on the belt gets its
  fill, so the machines can starve
- `SUSHI_SINK`: a sushi net reaches a machine that doesn't take every item on it
- `SUSHI_LIMIT`: a `limit=` on a chest a mixed belt of makers feeds: when the chest holds that
  much the belt backs up with every item on it, so the limit only brings the stop sooner. A chest
  of many items works best unlimited, with the limit at each maker
- `SPILL`: a net's route touches another net, so its items end up on a belt that isn't its own
- `WRONG-LANE`: items on a lane the layout gives other nets (a belt of two nets with its lanes the
  wrong way round), nets on one lane that go to different sinks, or items on the closed side of a
  belt side-loaded onto an underground: only the lane on its open side gets through, and the other
  stops there
- `NO_POWER`: a factory's poles don't reach a grid with generation (`floorplan --bridge-power`
  in the build script joins it to the plant's grid)
- `UNREACHED` (diff, before the paste): entities the paste lays where no powered roboport's
  construction area reaches, counting the roboports the paste lays where robots reach: robots
  never build them. It names power-bridge poles among them, since a roboport powered only through
  such a bridge never gets its power



### The rest: what is where

These describe a state - what is missing, what stands elsewhere, what happened - and the usual
answer is the next build. Nothing here is broken by itself.

- `no-feed` / `no-taker`: a machine's input has no net feeding it / its output goes nowhere
- `short-feed` (report_rates): a net's drivers make less than its consumers would take, so they
  run below speed; it names the net where the shortage starts (a short ore patch, not every step
  after it) and the `fk plan` line that sizes its drivers
- `cannot-carry`: a net names a machine that has no endpoint for the net's items
- `feed-rides-on` (report_rates): a sink at the end of a belt that brings it a product and also a
  feed, an item machines upstream on the same belt consume (sulfur riding the blue packs' belt
  into the packs' chest): what they don't take of the feed fills the sink and stops the belt for
  the product too. Give the product a belt of its own, or filter the sink's hands
  (`set packs.in: filter=chemical-science-pack`)
- `no-limit`: provider or storage chests without `limit=`: what reaches them goes into the
  network without end. `Chest<provider, limit=4>` keeps 4 stacks, and what feeds it stops when it
  is full. A chest a mixed belt of makers feeds gets a line of its own: it fills unless each maker
  is limited (`net NAME limited:` says your design limits them)
- `could-pair`: two single-item nets feed the same machines on two belts, a lane each: one belt carries
  both, one lane each (`lanes A | B`). Half of both belts is empty until you pair them. Whoever
  drives the nets, so a lab row fed red packs on one belt and green packs on another is named too
  (2.0.50); what is left alone is a pair into chests only, a pair a `SmelterArray` or a
  `CircuitStack` feeds (it side-loads its own two nets), and any flow that doesn't fit a lane (a
  requester without `rate=` is unbounded)
- `no-match`: a `set` that matches nothing (no such instance, no hand whose sink is the named one,
  or an `if=` with no chest to read), or an unknown property
- `cannot-filter`: a `guard` whose input inserters can't filter (plain inserters; fast ones can)
- `no-signal` / `no-wire`: a condition reads a signal nothing on its wire makes, or a name that is no
  signal / a wire nothing drives or reads, or a belt reading on no wire (its reading reaches
  nothing until the belt is wired)
- `wire-too-far` / `wires-share`: two entities of a wire further apart than a wire reaches (9 tiles)
  / two wires of one colour on one entity, so their signals add
- `gate-open` / `require-not-met`: a gated net the rate model takes as always open (no `behave` cycle on its
  wire) / a `behave` block's `require` that doesn't hold
- `taken-up`: the build takes up a lot, and the robots need minutes for it (the structures, the items
  in them, and how long: `out\diff.txt`). It goes faster if you stop what feeds it first and let
  it run a few minutes to empty its belts and chests. When the same things come back elsewhere, a
  factory moved: `fk merge` joins factories where they stand
- `two-nets`: one machine input or output on two nets
- `rate-not-met` / `target-not-met`: a rate you asserted, or the surface's target, is not met on paper (or,
  after a build, as routed)
- `size`: an `n=auto` row can't meet a target, an assert or what its consumers ask (the line says
  what stops it: the row is at the top of its bounds, a requester chest gives its rate, a row with
  a written count runs at full speed); after the routing, a net of a sized row that asks more
  than its belts or inserters carry
- `does-not-fit`: a sized row runs past its plot or into another instance's frame at the count it got;
  the build stops at `place` when their entities would overlap
- `too-thin`: a machine (or a chest with a rate, or the labs) runs short of a net's item as laid,
  and some of the net's belt lanes or inserters run full: the line says what is required there,
  what they carry, and who runs short. Lanes count one at a time (7.5/s yellow); on a belt two
  nets share, each net has its own. A chest without a rate beside machines on a net takes what
  they leave, so it is never short and asks nothing of the belts. The ways past one: `tier
  minimize --allow yellow,red` in the build script (red lanes carry 15/s), a second row, or a
  second belt
- `no-route`: no belt route was found for a net (the reason follows: often a row is boxed in;
  leave a few tiles free around a row's head, where its belts attach). The default build script
  stops on it, so nothing is pasted
- `no-destination` (report_checks): a maker whose product reaches none of the consumers of its net, so
  what it makes has nowhere to go. The default build script stops on it too; report_checks'
  `sinks` line counts the makers that reach a sink
- `unused`: a declared net that no machine and no belt uses
- `partly-connected`: a net's route doesn't reach every machine (the line says how many of its driver->consumer pairs connect)
- `bootstrap` (report_checks): a net's own route needs a building item (a belt, an underground, a
  splitter, an inserter) whose only maker in the factory takes an item from that same net, so
  neither can be built first. The line names the item, how many the route lays, the maker and what
  it takes from the net: stock that item first, or build that maker first. No line when the item
  comes from outside or another maker is fed elsewhere
- `no-robots`: a logistic chest outside every logistic area of the main robot network (robots
  won't serve it), or entities outside its construction area (robots won't build them). The kit's
  roboport at 8,8 serves chests 25 tiles each way and builds 55 tiles each way; `cover robots`
  gives a factory a roboport only for the chests no roboport of that network reaches yet (the
  kit's, and every one joined to it), which joins the network when it stands within 50 tiles of
  another one on both axes. It puts a storage chest beside each roboport it places, so what the
  robots take up there is stored there and not at spawn (`cover robots --storage none` leaves
  the chest out). Grow outward: the next factory within reach of the last.
- `on-water-or-cliff`: something designed stands on water or a cliff
- `no-water`: an offshore pump whose water side is dry ground (it gives no water)
- `by-hand`: the build replaces or removes something someone placed by hand: who (when the game
  names them), what, where, and on which design entity's tile. The design wins, so the build goes
  on; adopt it into your source, leave it out, or come back to it. A Claude lead hears it like
  chat (`out\hand.txt` keeps every one). It also names settings changed by hand on what the
  toolkit placed, with what the paste does: `rory_618 changed inserter at 14.5,0.5 (mall/i0):
  logistic=copper-plate>7 (the paste sets logistic=iron-plate<50)`. A paste writes the design's
  condition, request, chest limit and recipe over the one set by hand; a wire or condition the
  design lacks stays. To keep it, write it into the source (`logistic=copper-plate>7`);
  `stop_on by-hand` holds a paste until you have
- `re-laid` (diff, before the paste): belts, undergrounds, splitters or inserters stand on their
  design tiles under the right name but facing another way (or as the other underground end), so
  their line stops there. A factory built over another keeps the old one's belts wherever the
  names match, facing the old ways. The build orders each out and lays it fresh; `out\apply.txt`
  lists them under re-laid. One a player turned by hand is also a `by-hand` line
- `not-where-designed` / `no-position`: the paste did not land where the design says
- `short-stock`: the network holds fewer of an item than the build still needs
- `needs-research`: an item to build, or a machine's recipe, isn't researched (the technology and its
  cost follow)

These describe a state - what is missing, what stands elsewhere, what happened - and the usual
## When you need something

```
fk stock                         what you have, per item (free to read): robots in chests count,
                                 and a robot in a chest does nothing until it is in a roboport
fk plan ITEM RATE                the machines, furnaces and drills a rate takes, every step down to
                                 the ore (fk plan ITEM --machines N: what N machines take)
fk build --to size               the counts of your n=auto rows and what each one serves
                                 (out\size.txt), nothing laid out
fk stage list                    the plan: the stages after what is built, and how far it reaches
                                 ("Planning ahead")
fk stage check N                 a planned stage built on paper in its own folder, stages\N
fk lab NAME --from stages\N      that stage built for real on its own lab slot; then
                                 fk lab NAME --measure 60 for what it really makes ("The lab")
fk explain main.fk gears         one row as its template and as the entities and nets it becomes
fk build --to diff               what the next build would take up, and how long the robots need
                                 (a dry run: nothing written, nothing pasted)
fk fresh X0,Y0,X1,Y1             belts and pipes there with old items or fluid in them,
                                 inserters stuck holding an item their machine doesn't use, a
                                 crafter whose output holds what its recipe no longer makes, and
                                 a chest its crafters fill holding what none of them makes now,
                                 are taken up; the next build lays them fresh
fk merge . A,B --into C          two factories become one, both kept where they stand
fk chests-to-net . C PROV REQ    a robot link (provider chest to requester chest) becomes a belt
fk net-to-chests . C NET --provider P --requester R
                                 a belt becomes a robot link
fk unfold main.fk gears          turn a row into a Literal (its entities pinned as they are)
fk split-to-chest . main NET box --at X,Y [--requester --item I]
                                 a chest joins a net as one more endpoint (a buffer, a tap)
fk adopt X,Y --into main/NET     a belt you placed by hand becomes part of that net's route
fk measure X0,Y0,X1,Y1 [--seconds 30]
                                 what the machines in an area really make, against the model
fk stats [--items iron-plate,copper-plate] [--window 10min]
                                 production statistics (free to read)
fk stock                         what the base holds, per item: chests, roboports, your pack and
                                 the robots in the air (free to read). A robot in a chest is an
                                 item and does nothing; the line says how many are flying, how
                                 many in roboports and how many in chests
fk chart                         how much of the map is explored (free to read)
fk alerts                        the alert panel now (new alerts also come by themselves)
fk lua "CODE"                    run a Lua chunk in the game: its rcon.print comes back (read,
                                 investigate; see "Reading the game yourself")
fk lua --file probe.lua          the same with a file of yours
fk research                      what can be researched now, cheapest first, and what is running
fk research TECH [TECH...]       queue research, each after its unfinished prerequisites (labs
                                 and science packs do the rest); --first puts them at the front
fk tech NAME                     a technology, or what opens an item or recipe: cost (or what to
                                 craft or build for it), what to research first
fk recipes [NAME...] [--enabled] the locked recipes (or the enabled ones) and what opens each
fk read-terrain --surface nauvis --area X0,Y0,X1,Y1
                                 read more bare ground into map.terrain.json
fk chat                          the chat messages you have not dismissed yet
fk say "text"                    one line in the game chat
fk ack                           dismiss them (the only way; until then they keep coming back)
```

A chat message is not shown once and gone: it appears at the end of every `fk` command until you
`fk ack`, so a pipe (`| Select-Object -First 30`) that drops it costs nothing. Answer first, then
ack: `fk say "on it: belting the ore now"`, do the work, `fk ack`.

Every command takes `--help`.

## Reading the game yourself

`fk lua` sends a Lua chunk to the game over RCON and prints what it `rcon.print`s:

```
fk lua "rcon.print(game.forces.player.current_research and game.forces.player.current_research.name or 'none')"
fk lua "local n = 0 for _, g in pairs(game.surfaces.nauvis.find_entities_filtered{type='entity-ghost'}) do n = n + 1 end rcon.print(n)"
```

The chunk goes to the game as you wrote it, lines, comments, quotes and all, and a long one is
fine (a file of 40 kB works). A big answer is better written to a file
(`helpers.write_file('mine.json', helpers.table_to_json(t))`) on the game host and read there. For a script of your own, the game host is `computah-ct`
(`ssh computah-ct`); `run.json` names the toolkit there (`host_toolkit`: `fkcore.link.Rcon` is
the RCON client) and your game codebase there (`host_game`).

Use it to look and to understand. Changes go through the game: `fk build` (ghosts and robots).
What a script of yours may and may not do is in `FAIR-PLAY.md`. (`fk fresh` is the toolkit's own
example of a multi-stage build action: it takes up the belts, inserters and crafter output that
hold stale items, so the next build lays them again.) The link refuses Lua that registers an event
handler, a command or an interface: that made every save unjoinable once. Reusable helpers go in
`helpers\` (its README says how).

## The surface file and the world design

The world is two files. `game\nauvis.surface` says what is true of the world itself, and
`game\nauvis.fk`, the **world design**, says where the plots go:

```
# nauvis.surface
surface  nauvis
mode     play                                 # fair play: you see where you look; alerts come to you
terrain  map                                  # map.terrain.json: the bare ground, read at kickoff
catalog  base
design   nauvis.fk                            # the plots, from the world design

watch plots:      keep                        # something placed by hand in a plot: kept, listed
watch sanctioned: mark 2h                     # ... outside every plot: marked for two hours
notify   nearby                               # alerts for what is built: nearby, all or off
target automation-science-pack 10/min         # a rate the whole surface should make
```

```
# nauvis.fk: the world design
import World from "world.fk"

world = World @(-1000, -1000) size 2000x2000  # your base, and the only plot there is
```

Every top-level sealed instance of `nauvis.fk` is a **plot**: the instance's name is the factory's
name, the file it is imported from is the plot's `.fk` (its layout is that file's `.fkp`), `@(x, y)`
is the plot's corner in **map coordinates**, and `size WxH` (or the file's own `size` line) is its
ground. `gap N`, `retired`, `temporary until ...` and `@patch(resource)` go on the instance line as
they went on a `place` line. `design` wins over `place` lines: while it is there, a plot is an
instance and a `place` line names nothing.

A plot owns every tile inside it, and a plot may not overlap another or hold the surface's own
buildings: that is why a template gives a new run **one** plot and declares what stands inside it.
The starter kit (`kit.fk`, a `Literal` of the kickoff's own entities) and the reactor with its pole
line (`plant.fk`) are part of the world design, so they are the plot's, and the ground is free for
the base's own factories.

A factory's own `@(x, y)` positions are inside its plot: (0, 0) is the plot's top-left corner.
Plots never overlap. A plot can be moved by changing its `@(x, y)` alone: the build takes up what
stood on the old ground.

Without a `design` line the older form still works, one plot per line:

```
place iron = iron plot 40,-20 30x30          # factory iron, from iron.fk, on its own plot
```

`fk migrate-surface` writes a world design from a surface's place lines, and `fk look --source
design` draws the surface as designed rather than as built.

## The build script

`game\nauvis.build.fkb` is the list of steps `fk build` runs: one step per line, each reading
what the steps before it made. Keep it, or tweak it: leave `apply` out while you design. Its
`stop_on no-route no-destination` holds a paste with a net unrouted or a maker whose product reaches no
sink; add `stop_on no-feed` before a big paste so that check stops it too (`stop_on short-stock`
holds the paste until the network has what it lays: report_bom says when a paste would take up
running machines while their replacements are short). `size`, after `elaborate`, counts
the `n=auto` rows (`fk build --to size` stops there). `fk build --from route --to
report_checks` runs part of it (`report_rates:2` names the second `report_rates`). It starts
from the layout the last build left, or lays it out again when a source file changed since (and
says so); `fk look` says when the source has moved on from the `.fkp` files it judges.
`tier minimize --allow yellow,red` lays red belts where a yellow lane is too thin for what the
design asks (`--red 20` caps how many; `tier maximize` uses the fastest allowed everywhere; the
default `--allow yellow` keeps every belt yellow). A belt never goes down a tier unless you add
`--downgrade`.
`route --hand inserter` links machines with plain inserters
instead of fast ones (rows take `inserter=inserter` for the same). `--hand any` (the template's
line) is usually better: the router lays the **lowest** hand the network can build (a plain
inserter, 0.86/s), and the `hands` step raises only the connectors whose flow needs more
(`out\hands.txt`: `hand minimize ... 41 inserter, 8 fast-inserter; 8 changed`). The flow it reads is
the design's demand through every connector with no capacities (`routed.required`), so it does not
depend on the hand laid: a stone furnace (0.33/s all in) keeps plain hands, a gear assembler (2
plates/s in) gets fast ones. `hands maximize` takes the top rung everywhere; `hands minimize
--downgrade` may also take a standing fast hand back to plain; a rung the network cannot build now
is skipped and the line says so. A route that already
stands keeps its carriers, so `--hand any` renames the standing hands robots cannot build to the one
they can (`out\route.txt`: `rehanded 2 standing inserters robots cannot build, to inserter`), and
leaves a hand that stands built alone. `cover power --pole
any` and `floorplan --bridge-power any` do the same for poles: medium poles where the network can
build them, small ones otherwise. The choice is written to `out\avail.json` by `report_bom --stock
live` (and `fk avail --save`), the design half of a build runs on the laptop where there is no game
link, and `out\route.txt` / `out\cover_power.txt` say what was taken and why.
