# Every goal is reachable: plan the whole path, then walk it

Every goal you are given can be reached from where you stand. When one looks out of reach, you
are missing a step, not hitting a wall. A goal far from your current cell can take a long chain
of unglamorous steps first: finding things, reaching them, then making them. Those steps are the
work, not a sign the goal is wrong.

- **Work backwards to a full plan.** For each goal item, ask what it is made from, where every
  ingredient comes from, and what stands between you and that source: research, a machine,
  ground your robots can't reach, power. Write the chain in NOTES.md as numbered steps before
  you build.
- **Reach comes first.** Robots only work inside a roboport's area: building inside its
  construction radius, carrying inside its logistic radius. Ore, stone, water and trees are
  usually tens of tiles outside the starting area. If a source lies outside, your first build
  is the reach: a line of roboports (with poles) out to it, each one inside the last one's
  logistic area, so the network joins up. Check the radii, then measure the distance.
- **Search wider before deciding something isn't there.** Scan a large area (hundreds of tiles)
  for resources before concluding a resource is missing. A starting area always has iron,
  copper, coal and stone.
- **Don't drop a plan because a step is slow or dull.** Surveying, laying roboports and running
  the clock with `evaluate` while robots fly are real progress. If a step fails, fix that step
  and keep the plan; switch plans only for a reason you can name.
- **Progress is the cell, not the episode's goal.** Everything you make counts. If a goal is
  long, every step toward it (a mine, a smelter, an extended network) moves your cell up and is
  kept for the next episode. Leave NOTES.md saying which step you're on and what comes next.
- **Time only passes in `evaluate`.** A plan that never runs the clock never produces. After a
  build, evaluate, look at what changed, and continue.
