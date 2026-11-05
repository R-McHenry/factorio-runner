# The win run: rungs, not districts

This arm's only score is a legit rocket launch inside the episode's turn budget. A beautiful
base that never reaches blue science scores nothing. Every turn should move the next ladder
rung or defend what already stands.

- **Read the ladder before you act.** Red and green are usually already done; blue science is
  the next gate, then the real climb: production and utility science, rocket-silo research, the
  silo, then 100 rocket parts and the launch. Work the lowest unfinished gate and nothing else.
- **Blue's chain is the critical path:** pumpjack on the crude patch, a refinery, plastic for
  advanced circuits, sulfur for chemical packs, then `blue6.fk`. If blue is still 0/32, the
  oil chain is the next build — not a new district, not a mall, not a roboport grid.
- **Blue is not the last science.** `rocket-silo` costs 1000 each of red, green, blue, production
  and utility packs and needs concrete, rocket fuel, accumulators, solar, radar and module 3s.
  Production packs are electric furnaces + productivity modules + rails; utility packs are
  low-density structures + processing units + flying robot frames. Build those two chains and
  their research before the silo. The silo is 1000 steel + 1000 concrete + 100 pipes + 200
  processing units + 200 electric engines; a launch eats 100 rocket parts, each 10 processing
  units + 10 LDS + 10 rocket fuel.
- **Power before plumbing.** Check steam-engine saturation and accumulator charge before adding
  machines: a refinery or chemical plant sitting at `full_output` is usually a brownout, not a bad
  recipe, and every assembler marked `no_power` is a rung you are not climbing. Fix the grid first.
- **Research is a machine:** 3 red and 3 green assemblers feeding 4 or more labs. Keep that
  block standing and fed before you queue the oil techs; one lab cannot research the ladder in
  the turns you have.
- **Defend, don't conquer.** Keep the existing turrets loaded, repair what biters break, and
  belt ammo to them if that is cheap. Do not turret-creep, clear nests, or expand territory:
  no nest is a ladder rung.
- **Do not refactor, re-plan areas, pave over the base, or add room for a later doubling.**
  The run ends at the rocket; leave the base ugly and working.
- **Measure progress by rung, not by activity.** If two dozen turns pass without the current
  milestone's `have` rising, you are off the critical path: stop, read the ladder, and build
  the missing link in the chain.
