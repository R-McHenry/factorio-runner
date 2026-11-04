"""fkcore.plan: a forward ratio calculator, ``plan ITEM RATE`` or ``plan ITEM --machines N``.

``rates`` evaluates a design that is already written; nothing sized a factory from a target rate.
Rory told the players to grow exponentially (2026-10-21: about ten red and ten green science
assemblers before blue, then 8 to 15 each of blue science, advanced circuit and engine assemblers)
and asked whether an agent can easily work out how many miners 15 blue assemblers need. This
answers it in one call: per item of the recipe tree, the rate it takes (/s and /min), its recipe
and machine and how many machines that is (fractional, and whole to build), then the raw inputs:
ore and coal with the electric drills for them, crude oil, water.

The model is the hand calc's: every machine at full speed, no modules, beacons or quality, and
mining productivity is the only bonus (+10% a level, on drills and pumpjacks). Burner furnaces
burn coal (``FUEL_USE_W`` / ``FUEL_VALUE_J``), counted with the coal. Each item has one recipe:

1. ``recipes`` (``--recipe ITEM=RECIPE``),
2. the recipe named after the item (iron-plate, plastic-bar, engine-unit ...),
3. petroleum gas from basic oil processing (the oil recipe research opens first: advanced oil
   processing needs blue science, and basic is ``Refinery``'s default too), heavy and light oil
   from advanced oil processing, without cracking,
4. else the first recipe in the catalog that a Nauvis machine makes; a note names the others.

Ore, crude oil, water, steam (boilers; the one recipe that makes it is Vulcanus' acid
neutralisation), the ``raw`` items and what none of these makes are raw inputs. A recipe that
makes several needed items runs as many machines as the most-needed of them asks. What else it
makes is surplus, listed under it (a machine stops when an output is full), and never credited to
another recipe. Pure, stdlib only.
"""
from __future__ import annotations

import difflib
import math
from dataclasses import dataclass, field

from . import facts as _facts
from .catalog import FUEL_USE_W, FUEL_VALUE_J, MINING_TIME, STEAM_PER_S

__all__ = ["PlanError", "Plan", "Row", "plan", "parse_rate", "parse_recipes", "report",
           "ASSEMBLERS", "FURNACES"]

ASSEMBLERS = ("assembling-machine-1", "assembling-machine-2", "assembling-machine-3")
FURNACES = ("stone-furnace", "steel-furnace", "electric-furnace")
DRILL = "electric-mining-drill"
FUEL = "coal"
#: Crude oil a pumpjack (mining speed 1) gets from a 100% yield well: the resource gives 10 a 1 s
#: cycle. A well's yield is in its tooltip.
CRUDE_PER_PUMPJACK = 10.0
#: Water per offshore pump, as ``OffshorePump<rate=1200/s>``.
WATER_PER_PUMP = 1200.0
#: Raw inputs a machine without a recipe gets, besides the ores of ``MINING_TIME``.
RESOURCES = ("crude-oil", "water", "steam")
#: The order the raw rows are listed in: Nauvis's ores as before 2.1.1, then the others ``DRILL``
#: mines (calcite, scrap), then ``RESOURCES``.
RAW_FIRST = ("iron-ore", "copper-ore", "coal", "stone", "uranium-ore")

#: Recipe category -> the Nauvis machine that makes it ("asm" and "furnace": the plan's own).
#: Space Age's x-or-y categories hold recipes its own machines make too; a category only those
#: machines make (metallurgy, electromagnetics, organic, ...) is not planned.
CATEGORY_MACHINE = {
    "crafting": "asm", "advanced-crafting": "asm", "crafting-with-fluid": "asm",
    "electronics": "asm", "electronics-with-fluid": "asm", "pressing": "asm",
    "organic-or-assembling": "asm", "metallurgy-or-assembling": "asm",
    "electronics-or-assembling": "asm", "cryogenics-or-assembling": "asm",
    "crafting-with-fluid-or-metallurgy": "asm",
    "smelting": "furnace",
    "chemistry": "chemical-plant", "chemistry-or-cryogenics": "chemical-plant",
    "organic-or-chemistry": "chemical-plant",
    "oil-processing": "oil-refinery",
    "centrifuging": "centrifuge",
}
#: The oil products no recipe is named after (rule 3 above).
OIL = {"petroleum-gas": "basic-oil-processing", "heavy-oil": "advanced-oil-processing",
       "light-oil": "advanced-oil-processing"}

_UNITS = {"s": 1.0, "min": 60.0, "h": 3600.0}
_EPS = 1e-9


class PlanError(ValueError):
    """No plan: an unknown item or recipe, a bad rate, recipes that loop."""


@dataclass
class Step:
    """One recipe on its machine. ``makes`` / ``needs`` are per machine and second, net of what the
    recipe makes for itself (kovarex); ``fuel`` is the coal/s a burner furnace burns; ``items``
    are the needed items the recipe is chosen for."""
    recipe: str
    machine: str
    makes: dict
    needs: dict
    fuel: float = 0.0
    items: list = field(default_factory=list)
    count: float = 0.0
    depth: int = 0


@dataclass
class Row:
    """One item of the plan. ``recipe`` is None for a raw input: its ``machine`` is the drill or
    pump that gets it (None: given, nothing to count) and ``source`` says where it comes from. A
    ``shared`` row is a further item of the row above's machines (one recipe, several products)."""
    item: str
    rate: float                        # /s the plan takes
    recipe: str | None
    machine: str | None
    machines: float | None             # fractional; None on a shared row or a given input
    source: str = ""
    takers: dict = field(default_factory=dict)      # recipe (or "furnace fuel") -> /s it takes
    surplus: dict = field(default_factory=dict)     # item -> /s the machines make beyond the need
    shared: bool = False


@dataclass
class Plan:
    item: str
    rate: float                        # /s of the target
    machines: float | None             # the target's machines, when the plan was asked that way
    asm: str
    furnace: str
    mining_productivity: int
    drill: float                       # ore/s one electric drill mines (a 1 s ore), bonus included
    rows: list                         # [Row]: recipes, consumers before producers; raw inputs last
    notes: list

    def row(self, item) -> Row:
        for r in self.rows:
            if r.item == item:
                return r
        raise KeyError(item)


def parse_rate(text) -> float:
    """Items/s from ``1/s``, ``60/min`` or ``0.5/h``, as the .fk language writes a rate; a bare
    number is /s."""
    num, _, unit = str(text).strip().partition("/")
    try:
        value = float(num) / _UNITS[unit.strip() or "s"]
    except (KeyError, ValueError):
        raise PlanError(f"a rate is N/s, N/min or N/h (a bare number is /s), got {text!r}") \
            from None
    if not 0 < value < math.inf:
        raise PlanError(f"a rate is above 0, got {text!r}")
    return value


def parse_recipes(text) -> dict:
    """``ITEM=RECIPE[,ITEM=RECIPE...]`` -> {item: recipe}."""
    out = {}
    for part in (text or "").split(","):
        if not part.strip():
            continue
        item, eq, recipe = (s.strip() for s in part.partition("="))
        if not (item and eq and recipe):
            raise PlanError(f"--recipe is ITEM=RECIPE[,ITEM=RECIPE...], got {text!r}")
        out[item] = recipe
    return out


def plan(cat, item, rate=None, machines=None, asm="assembling-machine-2",
         furnace="stone-furnace", mining_productivity=0, recipes=None, raw=()) -> Plan:
    """The plan for ``rate`` items/s of ``item``, or for what ``machines`` of its machine make."""
    if (rate is None) == (machines is None):
        raise PlanError("give a rate (1/s, 60/min) or --machines N, one of the two")
    if rate is not None and not 0 < rate < math.inf:
        raise PlanError(f"a rate is above 0, got {rate:g}/s")
    if machines is not None and not 0 < machines < math.inf:
        raise PlanError(f"--machines is above 0, got {machines:g}")
    if asm not in ASSEMBLERS:
        raise PlanError(f"--asm is one of {', '.join(ASSEMBLERS)}, got {asm}")
    if furnace not in FURNACES:
        raise PlanError(f"--furnace is one of {', '.join(FURNACES)}, got {furnace}")
    if mining_productivity < 0:
        raise PlanError(f"--mining-productivity is a research level, 0 or more, got "
                        f"{mining_productivity}")
    recipes, raw = dict(recipes or {}), set(raw)
    known = _items(cat)
    for x in [item, *recipes, *sorted(raw)]:
        if x not in known:
            raise PlanError(_unknown("item", x, known))
    for x, r in recipes.items():
        _check_recipe(cat, x, r)
    makers = _makers(cat)
    notes, choice, seen, steps = [], {}, [], {}

    def visit(x, path):
        if x in path:
            loop = " -> ".join(path[path.index(x):] + [x])
            raise PlanError(f"the recipes loop: {loop} (--raw one of them)")
        if x in choice:
            return
        seen.append(x)
        r = choice[x] = _choose(x, makers, recipes, raw, notes)
        if r is None:
            return
        if r not in steps:
            steps[r] = _step(cat, r, asm, furnace, notes)
        steps[r].items.append(x)
        for y in _inputs(steps[r]):
            visit(y, path + [x])

    visit(item, [])
    top = choice[item]
    if machines is not None:
        if top is not None:
            rate = machines * steps[top].makes[item]
        else:
            _machine, each, _source = _raw_source(cat, item, raw, mining_productivity)
            if each is None:
                raise PlanError(f"{item} is a raw input here, with no machine to count: give "
                                "a rate")
            rate = machines * each

    demand, takers = {item: rate}, {}
    order = _order(steps, choice, seen, top)
    for st in order:
        st.count = max(demand[x] / st.makes[x] for x in st.items)
        for y, each in st.needs.items():
            _take(demand, takers, y, st.recipe, st.count * each)
        if st.fuel:
            _take(demand, takers, FUEL, "furnace fuel", st.count * st.fuel)

    rows = []
    for st in order:
        items = sorted(st.items, key=lambda x: -demand[x] / st.makes[x])   # the binding one first
        surplus = {}
        for p, each in st.makes.items():
            left = st.count * each - (demand[p] if p in st.items else 0.0)
            if left > _EPS:
                surplus[p] = left
        for i, x in enumerate(items):
            rows.append(Row(x, demand[x], st.recipe, st.machine, None if i else st.count,
                            takers=takers.get(x, {}), surplus={} if i else surplus,
                            shared=bool(i)))
    first = list(RAW_FIRST) + [r for r in MINING_TIME if r not in RAW_FIRST
                               and _facts.mines(DRILL, r)] + list(RESOURCES)
    for x in sorted((x for x in seen if choice[x] is None),
                    key=lambda x: (first.index(x) if x in first else len(first), seen.index(x))):
        machine, each, source = _raw_source(cat, x, raw, mining_productivity)
        rows.append(Row(x, demand[x], None, machine, demand[x] / each if each else None, source,
                        takers=takers.get(x, {})))
        if source == "not planned":
            makers_of = sorted({r["category"] for r in cat.recipes()
                                if any(p["name"] == x for p in r.get("products") or ())})
            notes.append(f"{x}: not planned, a raw input here: " + (
                f"only {', '.join(makers_of)} recipes make it" if makers_of
                else "no recipe makes it"))
    notes += _notes(choice, steps, seen)
    drill = cat.entity(DRILL)["mining_speed"] * (1.0 + 0.1 * mining_productivity)
    return Plan(item, rate, machines, asm, furnace, mining_productivity, drill, rows, notes)


# -- the recipe tree ------------------------------------------------------------------------------
def _items(cat):
    names = {*RESOURCES, *MINING_TIME}
    for rec in cat.recipes():
        for row in list(rec.get("ingredients") or ()) + list(rec.get("products") or ()):
            names.add(row["name"])
    return names


def _unknown(what, name, known):
    near = difflib.get_close_matches(name, sorted(known), n=3)
    return f"no {what} {name}" + (f" (did you mean {', '.join(near)}?)" if near else "")


def _net(needs, makes):
    """A recipe's rates without what it makes for itself (kovarex's uranium-235)."""
    for x in set(needs) & set(makes):
        left = makes.pop(x) - needs.pop(x)
        if left > _EPS:
            makes[x] = left
        elif left < -_EPS:
            needs[x] = -left
    return needs, makes


def _check_recipe(cat, item, recipe):
    if not cat.has_recipe(recipe):
        raise PlanError(f"--recipe {item}={recipe}: "
                        + _unknown("recipe", recipe, {r["name"] for r in cat.recipes()}))
    category = cat.recipe(recipe).get("category")
    if category not in CATEGORY_MACHINE:
        raise PlanError(f"--recipe {item}={recipe}: a {category} recipe, which no Nauvis machine "
                        "makes")
    if item not in _net(*cat.recipe_rates(recipe, 1.0))[1]:
        raise PlanError(f"--recipe {item}={recipe}: {recipe} does not make {item}")


def _makers(cat):
    """item -> the recipes a Nauvis machine makes it with, in catalog order. Emptying a barrel is
    left out: it gives back what filling it took."""
    out = {}
    for rec in cat.recipes():
        name = rec["name"]
        if rec.get("category") not in CATEGORY_MACHINE or (
                name.startswith("empty-") and name.endswith("-barrel")):
            continue
        for x in _net(*cat.recipe_rates(name, 1.0))[1]:
            out.setdefault(x, []).append(name)
    return out


def _choose(x, makers, recipes, raw, notes):
    """The recipe for ``x`` by the module's rules 1-4, or None: a raw input."""
    if x in raw:
        return None
    if x in recipes:
        return recipes[x]
    if x in MINING_TIME or x in RESOURCES:
        return None
    found = makers.get(x, [])
    if x in found:
        return x
    if OIL.get(x) in found:
        return OIL[x]
    if len(found) > 1:
        notes.append(f"{x}: planned with {found[0]}; {', '.join(found[1:])} make it too "
                     f"(--recipe {x}=NAME)")
    return found[0] if found else None


def _step(cat, recipe, asm, furnace, notes):
    rec = cat.recipe(recipe)
    kind = CATEGORY_MACHINE[rec["category"]]
    machine = {"asm": asm, "furnace": furnace}.get(kind, kind)
    fluid = any(r.get("type") == "fluid"
                for r in list(rec.get("ingredients") or ()) + list(rec.get("products") or ()))
    if kind == "asm" and fluid and not cat.fluid_boxes(machine):
        notes.append(f"{recipe}: {machine} takes no fluid, so it is planned on "
                     "assembling-machine-2")
        machine = "assembling-machine-2"
    needs, makes = _net(*cat.recipe_rates(recipe, cat.entity(machine)["crafting_speed"]))
    fuel = FUEL_USE_W[machine] / FUEL_VALUE_J[FUEL] if machine in FUEL_USE_W else 0.0
    return Step(recipe, machine, makes, needs, fuel)


def _inputs(st):
    return list(st.needs) + ([FUEL] if st.fuel else [])


def _order(steps, choice, seen, top):
    """The steps, consumers before producers: by the longest path from the target's recipe, then
    in the order the walk found them. Every consumer of an item is then sized before its maker."""
    if top is None:
        return []
    supply = {r: [] for r in steps}
    for r, st in steps.items():
        for y in _inputs(st):
            s = choice.get(y)
            if s is not None and s not in supply[r]:
                supply[r].append(s)
    post, done = [], set()

    def walk(r):
        done.add(r)
        for s in supply[r]:
            if s not in done:
                walk(s)
        post.append(r)

    walk(top)
    for r in reversed(post):                                  # a topological order
        for s in supply[r]:
            steps[s].depth = max(steps[s].depth, steps[r].depth + 1)
    found = {}
    for i, x in enumerate(seen):
        if choice[x] is not None:
            found.setdefault(choice[x], i)
    return sorted(steps.values(), key=lambda st: (st.depth, found[st.recipe]))


def _take(demand, takers, item, who, amount):
    demand[item] = demand.get(item, 0.0) + amount
    mine = takers.setdefault(item, {})
    mine[who] = mine.get(who, 0.0) + amount


def _raw_source(cat, item, raw, productivity):
    """(machine, /s each, where it comes from) for a raw input; machine None when nothing counts."""
    bonus = 1.0 + 0.1 * productivity
    if item in raw:
        return None, None, "given (--raw)"
    if item in MINING_TIME and _facts.mines(DRILL, item):
        return DRILL, cat.entity(DRILL)["mining_speed"] / MINING_TIME[item] * bonus, "mined"
    if item == "crude-oil":
        return "pumpjack", CRUDE_PER_PUMPJACK * bonus, "a 100% yield well"
    if item == "water":
        return "offshore-pump", WATER_PER_PUMP, "offshore"
    if item == "steam":
        return "boiler", STEAM_PER_S["boiler"], "boilers"
    return None, None, "not planned"


def _notes(choice, steps, seen):
    """What the plan assumed about oil, and about the raw inputs it counts but does not feed."""
    out = []
    gas = choice.get("petroleum-gas", "")
    if gas == "basic-oil-processing":
        out.append("oil: petroleum-gas from basic-oil-processing, the first oil recipe research "
                   "opens; --recipe petroleum-gas=advanced-oil-processing plans advanced oil "
                   "processing instead, without cracking")
    elif "advanced-oil-processing" in steps:
        out.append("oil: advanced-oil-processing without cracking: what nothing here takes of its "
                   "heavy oil, light oil and petroleum gas is surplus")
    elif "petroleum-gas" in choice and gas is None:
        out.append("oil: petroleum-gas is a raw input here, its refineries and crude oil not "
                   "planned")
    if "crude-oil" in seen and choice["crude-oil"] is None:
        out.append(f"crude-oil: pumpjacks are counted at 100% yield ({CRUDE_PER_PUMPJACK:g}/s "
                   "each); on a 300% well a third as many")
    if "uranium-ore" in seen and choice["uranium-ore"] is None:
        out.append("uranium-ore: its drills need sulfuric acid too, not planned")
    if "steam" in seen and choice["steam"] is None:
        out.append(f"steam: boilers are counted at {STEAM_PER_S['boiler']:g}/s each; their water "
                   "and fuel are not planned")
    return out


# -- report ---------------------------------------------------------------------------------------
def _build(n):
    """Whole machines to build for ``n``: rounded up, never one more for float dust."""
    return math.ceil(n - _EPS) if n > _EPS else 0


def _per(d):
    return ", ".join(f"{k} {v:.3f}" for k, v in sorted(d.items(), key=lambda kv: (-kv[1], kv[0])))


def report(p) -> str:
    """The plan as text: the target, the assumptions, one line per item, then the notes."""
    head = f"plan {p.item} {p.rate:.3f}/s ({p.rate * 60:.2f}/min)"
    if p.machines is not None:
        head += f", what {p.machines:g} {p.rows[0].machine} make"
    lines = [head,
             f"  crafting on {p.asm} (--asm), smelting on {p.furnace} (--furnace)"
             + (", which burns coal" if p.furnace in FUEL_USE_W else ""),
             f"  an {DRILL} mines {p.drill:.3f} ore/s (--mining-productivity "
             f"{p.mining_productivity})",
             "  every machine at full speed: no modules, beacons or quality; power is not planned",
             ""]
    cols = "{:<24} {:>9} {:>10}  {:<29} {:<21} {:>8} {:>5}"
    steps = [r for r in p.rows if r.recipe is not None]
    raws = [r for r in p.rows if r.recipe is None]
    for title, group in (("recipe", steps), ("source", raws)):
        if not group:
            continue
        lines.append("  " + cols.format("item" if title == "recipe" else "raw", "/s", "/min",
                                        title, "machine", "machines", "build"))
        for r in group:
            if r.shared:
                n, b = "same", ""
            elif r.machines is None:
                n, b = "", ""
            else:
                n, b = f"{r.machines:.2f}", str(_build(r.machines))
            lines.append("  " + cols.format(r.item, f"{r.rate:.3f}", f"{r.rate * 60:.2f}",
                                            r.recipe or r.source, r.machine or "", n, b).rstrip())
            if len(r.takers) > 1 or "furnace fuel" in r.takers:
                lines.append(f"      for {_per(r.takers)}")
            if r.surplus:
                lines.append(f"      surplus {_per(r.surplus)} (/s): a machine stops when an "
                             "output is full, so give it somewhere to go")
        lines.append("")
    if p.notes:
        lines += ["  notes"] + [f"  {n}" for n in p.notes]
    return "\n".join(lines).rstrip()
