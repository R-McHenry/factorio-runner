# Building a base that scales

A factory is a system that has to keep growing. The problems that stop it growing are the same
ones that stop software growing, and so are the cures.

- **The design is the source.** The world is what the design builds. Change the design and build
  again, instead of patching the world by hand; a hand fix you need to keep goes back into the
  design.
- **Modules with interfaces.** Build each factory as a sealed module: a fixed footprint, its own
  belts inside its frame, and named ports on its edge. Factories connect only through those ports.
  A change inside one module then never breaks another, and a module can be copied, moved or
  replaced whole.
- **Scaffolding first.** Before production grows, lay the infrastructure everything else stands
  on, on a regular grid: a roboport grid whose logistic areas tile the ground, a power backbone
  along it, and corridors kept free for the belts between modules. Establish it early, while it's
  cheap; retrofitting it under a grown base costs far more. Put new modules into the grid's cells.
- **Standards.** Keep one grid size, one way of laying a module, one direction for flows. A
  consistent base is cheap to extend: the next module is a copy of the last with new parameters.
- **Grow in tested steps.** Build one module, run the game, check it does what the design says
  (`fk look`, `fk stats`), and only then build on it. A save_state before a risky change is your
  test harness: try it, measure, load_state if it didn't work.
- **Leave room for the next doubling.** Size corridors, power and grid for twice what you build
  now.
- **Name your debt and pay it down.** A hand-fed chest, a belt that wanders around something, a
  machine outside the grid, a design that no longer says what stands: each is debt, and debt
  makes every later change slower. Write it in NOTES.md when you take it on. When something
  doesn't follow the organization the base needs, be willing to deconstruct it and pave over it
  with the right structure, instead of building around it.
- **Refactor when the shape stops fitting.** When the base outgrows its layout, re-plan the area
  and rebuild it cleanly; the toolkit makes a rebuild cheap, and a base that stays easy to change
  is worth more than one that grew fastest this hour.
