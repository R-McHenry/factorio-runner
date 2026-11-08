"""fkcore.templates: the built-in templates, as the machines they contribute to the netlist.

A template is a pure function ``(params, catalog) -> [MSpec]``: the machines one instance adds,
with the endpoints the rate model and the checks need. Geometry (the Fragment of CORE-V1.md §5:
carriers, roles, attach points) is added per template in ``fkcore.fragments``; the machine locals
here (``m0``, ``m1``...) are the keys the fragment uses for the same machines.

Endpoints come from the recipe (ingredients in, products out), plus the fuel of a burner machine,
the resource of a drill and every science pack for a lab. The steam chain has its own kinds: a
``Boiler`` (water and fuel in, steam out) and a ``SteamEngine`` (steam in; electricity is not an
item, so it makes nothing the netlist carries). ``InfinityPipe`` is a lab-only fluid source (the
lab's free materials; no recipe makes one). A ``Bus`` has no machines: its loads and taps are
ports, endpoints that pass on what reaches them (``extra["port"]``). Pure, stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import facts as _facts
from .catalog import FUEL_USE_W, FUEL_VALUE_J, HEAT_W, MINING_TIME, REACTOR_FUEL_S, STEAM_PER_S
from .fragments import FragmentError, bus_check, bus_ports

__all__ = ["MSpec", "TemplateError", "TEMPLATES", "COMMON_PARAMS", "CIRCUIT_PARAMS", "CONTACT",
           "SINGLE", "CRAFTERS", "machines", "science_packs", "literal_machines", "fluid_misfit"]

INF = float("inf")

#: 2.0, circuits in the source (``fkcore.circuits``): a maker's limits and a crafter's guard.
#: ``keep=N``: the logistic network keeps N of its product; ``fill=N``: each chest it fills
#: straight holds at most N; ``guard``: its input inserters carry only what its recipe takes.
CIRCUIT_PARAMS = ("keep", "fill", "guard")
#: Parameters every instance may carry; the elaborator reads them, the templates ignore them.
COMMON_PARAMS = ("duty", "dnp", "inserter", "modules", "beacons", "beacon") + CIRCUIT_PARAMS
#: 2.0.53, ``modules=``: the module slots of the machines that take them, and each module's
#: effect on speed and productivity (normal quality; efficiency changes no rate). 2.1.1: read
#: from facts.json (``fkcore.facts``): every entity with module slots, every module.
MODULE_SLOTS = _facts.module_slots()
MODULE_EFFECTS = _facts.module_effects()
#: 3.0: the recipes that take productivity (a machine's built-in bonus, too)
PRODUCTIVE = _facts.productive()
#: Items that pass by contact, with no carrier (2.0): a reactor heats the exchanger it touches.
#: A net of only these has no route; it is connected when its machines touch.
CONTACT = frozenset({"heat"})


class TemplateError(ValueError):
    """Malformed template parameters: no machines can be produced (an invariant, not a check)."""


@dataclass
class MSpec:
    local: str                     # key below the instance: m0, m1, c0
    prototype: str
    recipe: str | None
    needs: dict                    # item -> items/s at full speed (ingredients and fuel)
    makes: dict                    # item -> items/s at full speed (INF for an unbounded source)
    takes: frozenset | None        # items it accepts as a consumer; None = anything (a chest)
    sink: bool = False             # takes whatever reaches it beyond its needs (chest, lab)
    drain: float | None = None     # a sink's cap in items/s (None = unlimited); extra["each"]:
                                   # the cap of each item it is fed (a lab's rate=), not of all
    source: bool = False           # an unbounded source (a requester chest with no rate)
    extra: dict = field(default_factory=dict)


def science_packs(cat):
    return frozenset(r["name"] for r in cat.recipes() if r["name"].endswith("-science-pack"))


# -- helpers --------------------------------------------------------------------------------------
def _int(params, key, default=None):
    v = params.get(key, default)
    if v is None:
        raise TemplateError(f"needs {key}=")
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if not isinstance(v, int) or v < 1:
        raise TemplateError(f"{key} must be a positive whole number, got {v!r}")
    return v


def _word(params, key, default=None):
    v = params.get(key, default)
    if v is None:
        raise TemplateError(f"needs {key}=")
    if not isinstance(v, str):
        raise TemplateError(f"{key} must be one name, got {v!r}")
    return v


def _rate(params, key, default=None):
    v = params.get(key, default)
    if v is None:
        return None
    if not isinstance(v, (int, float)) or v < 0:
        raise TemplateError(f"{key} must be a rate (e.g. 0.5/s), got {v!r}")
    return float(v)


def _only(params, allowed):
    # 2.1: a raw setting (cb.FIELD=, bp.FIELD=) goes on any template's machines (fkcore.circuits)
    extra = sorted(k for k in set(params) - set(allowed) - set(COMMON_PARAMS)
                   if not (isinstance(k, str) and k.partition(".")[0] in ("cb", "bp") and "." in k))
    if extra:
        raise TemplateError("unknown parameter%s %s (allowed: %s)" % (
            "s" if len(extra) > 1 else "", ", ".join(extra), ", ".join(allowed) or "none"))


def _crafter(cat, local, machine, recipe, fuel=None):
    if not cat.has_recipe(recipe):
        raise TemplateError(f"unknown recipe {recipe}")
    if not cat.has_entity(machine):
        raise TemplateError(f"unknown machine {machine}")
    speed = cat.entity(machine).get("crafting_speed")
    if speed is None:
        raise TemplateError(f"{machine} does not craft")
    needs, makes = cat.recipe_rates(recipe, speed)
    f = _facts.entity(machine)
    # 3.0: a machine's built-in productivity (a foundry's or an electromagnetic plant's +50%,
    # the game's effect_receiver.base_effect), on the recipes that take productivity
    bonus = float((f.get("base_effect") or {}).get("productivity") or 0.0)
    if bonus and recipe in PRODUCTIVE:
        makes = {k: v * (1.0 + bonus) for k, v in makes.items()}
    if fuel is not None:
        needs[fuel] = needs.get(fuel, 0.0) + _fuel_rate(machine, fuel)
    extra = {}
    cats = f.get("crafting_categories")
    category = cat.recipe(recipe).get("category")
    if cats and category and category not in cats:
        # 3.0: a check, never a gate (a catalog may lag the game, a mod may add a category)
        extra["checks"] = [("wrong-category", f"{machine} doesn't craft {recipe}: it is a "
                                              f"{category} recipe, and {machine} crafts "
                                              f"{', '.join(cats)}")]
    return MSpec(local, machine, recipe, needs, makes, frozenset(needs), extra=extra)


def _burner(machine):
    """3.0: the fuel categories a machine's burner takes, from the facts (a learned machine too:
    the biochamber burns nutrients), or () for a machine with no burner."""
    f = _facts.entity(machine)
    if machine in FUEL_USE_W:
        return tuple(f.get("fuel_categories") or ("chemical",))
    return tuple(f.get("fuel_categories") or ()) if f.get("burner") else ()


def _fuel(params, machine):
    """``fuel=`` of a burner machine, None for any other. The default is coal for a chemical
    burner, else the first fuel of its category by name (nutrients for a biochamber)."""
    cats = _burner(machine)
    if not cats:
        return None
    if "chemical" in cats:
        return _word(params, "fuel", "coal")
    known = sorted(n for c in cats for n in _facts.fuels(c))
    if "fuel" not in params and not known:
        raise TemplateError(f"{machine} burns {'/'.join(cats)} fuel, and the catalog knows none "
                            "(fk catalog learn reads it from the game): give fuel=")
    return _word(params, "fuel", known[0] if known else None)


def _fuel_rate(machine, fuel):
    cats = _burner(machine)
    if not cats:
        raise TemplateError(f"{machine} burns no fuel")
    if machine in FUEL_USE_W and fuel in FUEL_VALUE_J:
        return FUEL_USE_W[machine] / FUEL_VALUE_J[fuel]
    value = _facts.fuel_value(fuel)
    if value is None:
        raise TemplateError(f"{fuel} is not a fuel")
    cat = (_facts.load()["fuels"].get(fuel) or {}).get("category")
    if cat not in cats:
        raise TemplateError(f"{machine} burns {'/'.join(cats)} fuel, and {fuel} is {cat} fuel")
    return float(_facts.energy_w(machine)) / value


# -- templates ------------------------------------------------------------------------------------
def asm_row(params, cat):
    """``AsmRow<n, recipe, machine=assembling-machine-1>``: n assemblers on one recipe."""
    _only(params, ("n", "recipe", "machine"))
    n = _int(params, "n")
    recipe = _word(params, "recipe")
    machine = _word(params, "machine", "assembling-machine-1")
    return [_crafter(cat, f"m{k}", machine, recipe) for k in range(n)]


#: 2.1, the entity types ``Machine`` takes: everything that crafts a recipe
CRAFTERS = ("assembling-machine", "furnace")


def machine(params, cat):
    """``Machine<entity, recipe, ore=, fuel=coal>`` (2.1, PLAN-2.1 "A. Machine"): one machine of any
    crafting entity in the catalog (assemblers, furnaces, the chemical plant, the refinery, the
    centrifuge). ``machine=`` is ``entity=`` by another name. ``recipe`` is required, except that a
    furnace may take ``ore=`` (the recipe that smelts it). A burner takes ``fuel=`` (default
    coal); an electric machine ignores it. A recipe with a fluid the entity has no fluid box for
    is refused (``no-fluidbox``): the machine would never run. ``Assembler``, ``Furnace``,
    ``ChemPlant`` and ``Refinery`` are kinds of it, in the prelude."""
    _only(params, ("entity", "machine", "recipe", "ore", "fuel"))
    if "entity" in params and "machine" in params:
        raise TemplateError("entity= and machine= are one parameter: give one")
    entity = _word(params, "entity" if "entity" in params else "machine") \
        if ("entity" in params or "machine" in params) else None
    if entity is None:
        raise TemplateError("needs entity= (the machine: assembling-machine-2, centrifuge, ...)")
    if not cat.has_entity(entity):
        raise TemplateError(f"unknown entity {entity}: the catalog has no such building")
    if cat.kind(entity) not in CRAFTERS:
        raise TemplateError(f"{entity} is a {cat.kind(entity)}, not a crafting machine: a Machine "
                            f"is an assembling-machine or a furnace (Entity<name={entity}> places "
                            "any other building)")
    if "recipe" in params:
        recipe = _word(params, "recipe")
    elif "ore" in params:
        if cat.kind(entity) != "furnace":
            raise TemplateError(f"ore= picks a smelting recipe, and {entity} is not a furnace: "
                                "give recipe=")
        recipe = _smelt_recipe(params, cat)
    else:
        raise TemplateError("needs recipe=" + (" (or ore=)" if cat.kind(entity) == "furnace"
                                               else ""))
    fuel = _fuel(params, entity)
    spec = _crafter(cat, "m0", entity, recipe, fuel)
    no_fluidbox(cat, entity, recipe)
    return [spec]


def fluid_box_counts(cat, entity):
    """(input boxes, output boxes) of ``entity`` as the game reads them (``fluidboxes.json``)."""
    boxes = cat.fluid_boxes(entity)
    ins = sum(1 for b in boxes if b.get("production") == "input")
    outs = sum(1 for b in boxes if b.get("production") == "output")
    return ins, outs


def fluid_misfit(cat, entity, recipe):
    """Why ``entity`` can't run ``recipe`` for want of fluid boxes, or None (2.1)."""
    if not cat.has_recipe(recipe) or not (getattr(cat, "_fluid", None) or {}).get("entities"):
        return None                     # a catalog without the game's fluid boxes can't tell
    r = cat.recipe(recipe)
    fin = sorted({i["name"] for i in r.get("ingredients") or () if i.get("type") == "fluid"})
    fout = sorted({p["name"] for p in r.get("products") or () if p.get("type") == "fluid"})
    if not fin and not fout:
        return None
    ins, outs = fluid_box_counts(cat, entity)
    if len(fin) > ins or len(fout) > outs:
        what = ", ".join(fin + fout)
        has = ("no fluid box" if not ins + outs else
               f"{ins} input and {outs} output fluid box{'es' if ins + outs > 1 else ''}")
        return (f"no-fluidbox: {recipe} takes or makes {what}, and {entity} has {has} for it, so "
                "it would never run. Pick a machine with the boxes (assembling-machine-2, "
                "chemical-plant, ...)")
    return None


def no_fluidbox(cat, entity, recipe):
    msg = fluid_misfit(cat, entity, recipe)
    if msg:
        raise TemplateError(msg)


#: 2.1: the types ``Entity`` leaves to their own kinds: what crafts, and what carries items or
#: fluids (the router lays those)
_NOT_ENTITY = {"assembling-machine": "Machine<entity={0}, recipe=...>",
               "furnace": "Machine<entity={0}, recipe=...>"}


def entity_(params, cat):
    """``Entity<name=...>`` (2.1, PLAN-2.1 "Circuits that reach any building"): one building that
    isn't a crafter (a lamp, a speaker, a display panel, a pump, a train stop, a beacon): placed,
    powered and wireable, and in no item flow. Its size, circuit connectors and power use come
    from the catalog."""
    _only(params, ("name", "read"))
    name = _word(params, "name")
    if not cat.has_entity(name):
        raise TemplateError(f"unknown entity {name}: the catalog has no such building")
    use = _NOT_ENTITY.get(cat.kind(name))
    if use:
        raise TemplateError(f"{name} crafts: write {use.format(name)}")
    if "read" in params and cat.kind(name) != "accumulator":
        # 2.1 (power domains): read=SIGNAL puts an accumulator's charge (0-100) on its wire
        raise TemplateError(f"read={params['read']}: an Entity reads only an accumulator's "
                            "charge; set another building's reading with cb. fields")
    return [MSpec("m0", name, None, {}, {}, frozenset(), extra={"entity": True})]


def decider(params, cat):
    """``Decider<if="...", out="...">`` (2.1, power domains): one decider combinator.
    ``if`` is its condition in 2.0's form, ``or`` between groups of ``and``
    (``"green.A < 20 or (red.S > 0 and green.A < 80)"``, ``red.S`` / ``green.S`` reading one
    wire colour, a bare signal both); ``out`` what it sends while the condition holds
    (``"S=1"`` a constant, ``"S"`` the input's count of S). ``fkcore.circuits`` reads both. Its
    two sides are wired as ``NAME.in`` and ``NAME.out``. It moves no items."""
    _only(params, ("if", "out"))
    for k in ("if", "out"):
        if not isinstance(params.get(k), str) or not params[k].strip():
            raise TemplateError(f'Decider needs {k}="..." (a quoted string)')
    return [MSpec("m0", "decider-combinator", None, {}, {}, frozenset(),
                  extra={"circuit": True, "combinator": True})]


def arithmetic(params, cat):
    """``Arithmetic<calc="A * 2", out="B">`` (2.1, power domains): one arithmetic combinator,
    ``calc`` one operation on two operands (a signal, ``red.S`` / ``green.S``, ``each`` or a whole
    number), ``out`` the signal it sends. Its sides are ``NAME.in`` and ``NAME.out``."""
    _only(params, ("calc", "out"))
    for k in ("calc", "out"):
        if not isinstance(params.get(k), str) or not params[k].strip():
            raise TemplateError(f'Arithmetic needs {k}="..." (a quoted string)')
    return [MSpec("m0", "arithmetic-combinator", None, {}, {}, frozenset(),
                  extra={"circuit": True, "combinator": True})]


def _smelt_recipe(params, cat):
    if "recipe" in params:
        return _word(params, "recipe")
    ore = _word(params, "ore")
    try:
        return cat.smelting_recipe(ore)
    except KeyError:
        raise TemplateError(f"no smelting recipe takes {ore}") from None


def smelter_row(params, cat):
    """``SmelterRow<n, recipe | ore, fuel=coal, machine=stone-furnace>``: n furnaces, ore + fuel in."""
    _only(params, ("n", "recipe", "ore", "fuel", "machine"))
    n = _int(params, "n")
    recipe = _smelt_recipe(params, cat)
    machine = _word(params, "machine", "stone-furnace")
    fuel = _fuel(params, machine)
    return [_crafter(cat, f"m{k}", machine, recipe, fuel) for k in range(n)]


def smelter_array(params, cat):
    """``SmelterArray<n, recipe | ore, fuel=coal, machine=stone-furnace, units=>`` (2.0): Rory's
    smelter stack, n burner furnaces fed by side-loading, ore and fuel on the two lanes of each
    column's belt (``fkcore.fragments.smelter_array``). ``units``: the 11-wide units it tiles
    to; by default the fewest that keep each column within one yellow lane of ore."""
    _only(params, ("n", "recipe", "ore", "fuel", "machine", "units"))
    n = _int(params, "n")
    if "units" in params and _int(params, "units") > n:
        raise TemplateError(f"units={params['units']} is more units than n={n} furnaces")
    recipe = _smelt_recipe(params, cat)
    machine = _word(params, "machine", "stone-furnace")
    if machine not in FUEL_USE_W or not cat.has_entity(machine) or cat.size(machine) != (2, 2):
        raise TemplateError(f"machine={machine}: a SmelterArray's furnaces are 2x2 burners "
                            "(stone-furnace, steel-furnace), fed ore and fuel from one belt")
    fuel = _word(params, "fuel", "coal")
    return [_crafter(cat, f"m{k}", machine, recipe, fuel) for k in range(n)]


def circuit_stack(params, cat):
    """``CircuitStack<n, machine=assembling-machine-1>`` (2.0.47): n green circuit assemblers on
    both sides of one output belt, each fed cable directly by two cable assemblers beside it, and
    iron and copper on the two lanes of the stack's own feed belts (``fragments.circuit_stack``).
    Each circuit assembler is one machine taking iron and the copper its cable assemblers turn
    into cable (1.5 copper a circuit); the cable assemblers are the stack's entities."""
    _only(params, ("n", "machine"))
    n = _int(params, "n")
    if n > 14:
        raise TemplateError(f"n={n}: a stack's columns hold 7 circuit assemblers each (a lane of "
                            "iron on each feed belt); use two stacks")
    machine = _word(params, "machine", "assembling-machine-1")
    out = []
    for k in range(n):
        spec = _crafter(cat, f"m{k}", machine, "electronic-circuit")
        needs = {"iron-plate": spec.needs.get("iron-plate", 0.0),
                 "copper-plate": spec.needs.get("copper-cable", 0.0) / 2}
        out.append(MSpec(spec.local, machine, "electronic-circuit", needs, spec.makes,
                         frozenset(needs)))
    return out


def miner_row(params, cat):
    """``MinerRow<n, resource, drill=electric-mining-drill, fuel=coal>``: n drills on a patch."""
    _only(params, ("n", "resource", "drill", "fuel"))
    n = _int(params, "n")
    resource = _word(params, "resource")
    drill = _word(params, "drill", "electric-mining-drill")
    if resource not in MINING_TIME:
        raise TemplateError(f"unknown resource {resource}")
    if _facts.entity(drill) and not _facts.mines(drill, resource):
        raise TemplateError(f"{drill} does not mine {resource}")
    speed = cat.entity(drill).get("mining_speed")
    if speed is None:
        raise TemplateError(f"{drill} does not mine")
    # 2.1 (fluid ports): an ore that needs a fluid (uranium: sulfuric acid, 1 an ore) is mined
    # only by a drill with a fluid box, and the fluid is one of its needs, piped in
    wet = _facts.required_fluid(resource)
    if wet is not None and not cat.has_fluid_boxes(drill):
        raise TemplateError(f"{resource} needs {wet[0]}, and a {drill} has no fluid box: mine "
                            "it with electric-mining-drill")
    out = []
    for k in range(n):
        needs = {}
        fuel = _fuel(params, drill)
        if fuel is not None:
            needs[fuel] = _fuel_rate(drill, fuel)
        if wet is not None:
            needs[wet[0]] = speed / MINING_TIME[resource] * wet[1]
        out.append(MSpec(f"m{k}", drill, None, needs, {resource: speed / MINING_TIME[resource]},
                         frozenset(needs), extra={"resource": resource}))
    return out


def pumpjack(params, cat):
    """``Pumpjack<n=1, rate=30/s>``: crude oil at the well's rate (a property of the well)."""
    _only(params, ("n", "rate"))
    n = _int(params, "n", 1)
    rate = _rate(params, "rate", 30.0)
    return [MSpec(f"m{k}", "pumpjack", None, {}, {"crude-oil": rate}, frozenset(),
                  extra={"resource": "crude-oil"}) for k in range(n)]


def offshore_pump(params, cat):
    """``OffshorePump<rate=1200/s>``: water."""
    _only(params, ("rate",))
    return [MSpec("m0", "offshore-pump", None, {}, {"water": _rate(params, "rate", 1200.0)},
                  frozenset())]


def boiler(params, cat):
    """``Boiler<fuel=coal, machine=boiler>``: water and fuel in, steam out (60/s each)."""
    _only(params, ("fuel", "machine"))
    machine = _word(params, "machine", "boiler")
    if cat.has_entity(machine) and cat.kind(machine) != "boiler":
        raise TemplateError(f"{machine} is not a boiler")
    fuel = _word(params, "fuel", "coal")
    rate = STEAM_PER_S["boiler"]
    needs = {"water": rate, fuel: _fuel_rate("boiler", fuel)}
    return [MSpec("m0", machine, None, needs, {"steam": rate}, frozenset(needs),
                  extra={"fuel": fuel})]


def steam_engine(params, cat):
    """``SteamEngine<machine=steam-engine>``: steam in at full power (30/s, 900 kW)."""
    _only(params, ("machine",))
    machine = _word(params, "machine", "steam-engine")
    if cat.has_entity(machine) and cat.kind(machine) != "generator":
        raise TemplateError(f"{machine} is not a generator")
    # role=engine on its .fkp line tells a netlist engine from an imported plant's turbine
    return [MSpec("m0", machine, None, {"steam": STEAM_PER_S["steam-engine"]}, {},
                  frozenset({"steam"}), extra={"role": "engine"})]


def steam_turbine(params, cat):
    """``SteamTurbine``: 500 C steam in at full power (60/s, 5.82 MW; 2.0, the starter reactor)."""
    _only(params, ())
    return [MSpec("m0", "steam-turbine", None, {"steam": STEAM_PER_S["steam-turbine"]}, {},
                  frozenset({"steam"}), extra={"role": "turbine"})]


def reactor(params, cat):
    """``Reactor<fuel=uranium-fuel-cell, read=temperature, signal=>`` (2.0, the starter reactor,
    drafts\\reactor-1.1): one nuclear reactor. A loaded cell burns 200 s at the full 40 MW whatever
    is drawn, so it needs a cell per 200 s while it burns and makes as many used cells, and heat
    (MW) for the exchangers it touches. ``read=temperature`` puts its temperature on a wire as
    ``signal`` (a declared signal or a game one), a circuit setting (``fkcore.circuits``)."""
    _only(params, ("fuel", "read", "signal"))
    fuel = _word(params, "fuel", "uranium-fuel-cell")
    if fuel not in REACTOR_FUEL_S:
        raise TemplateError(f"{fuel} is no reactor fuel")
    if params.get("read") not in (None, "temperature"):
        raise TemplateError(f"read={params['read']}: a reactor reads its temperature")
    per = 1.0 / REACTOR_FUEL_S[fuel]
    spent = "used-up-" + fuel
    return [MSpec("m0", "nuclear-reactor", None, {fuel: per},
                  {spent: per, "heat": HEAT_W["nuclear-reactor"] / 1e6}, frozenset({fuel}),
                  extra={"fuel": fuel})]


def heat_exchanger(params, cat):
    """``HeatExchanger``: heat (10 MW) and water in, 500 C steam out (103/s; 2.0). It needs 500 C
    to make steam."""
    _only(params, ())
    rate = STEAM_PER_S["heat-exchanger"]
    needs = {"heat": HEAT_W["heat-exchanger"] / 1e6, "water": rate}
    return [MSpec("m0", "heat-exchanger", None, needs, {"steam": rate}, frozenset(needs))]


def constant(params, cat):
    """``Constant<signals="signal-A=1, iron-plate=100">`` (2.0): a constant combinator, the
    signals it puts on its wires (``fkcore.circuits`` reads them). It moves no items."""
    _only(params, ("signals",))
    return [MSpec("m0", "constant-combinator", None, {}, {}, frozenset(), extra={"circuit": True})]


def pole(params, cat):
    """``Pole<pole=medium-electric-pole>`` (2.0.49): one electric pole, pinned where the design puts
    it. Power is the build's own step, and it keeps a pole that stands in the layout; this is for a
    cell packed so tight that the pole's tile is part of its design (Rory, 2026-10-26: \"a power pole
    and two undergrounds in every row\"): the routes go round it, or under it, and the power step
    adds only what it still lacks. It moves no items."""
    _only(params, ("pole",))
    name = _word(params, "pole", "medium-electric-pole")
    if not cat.has_entity(name) or cat.kind(name) != "electric-pole":
        raise TemplateError(f"pole={name}: not an electric pole (small-electric-pole, "
                            "medium-electric-pole, big-electric-pole, substation)")
    return [MSpec("m0", name, None, {}, {}, frozenset(), extra={"pole": True})]


def infinity_pipe(params, cat):
    """``InfinityPipe<fluid, rate=>``: a lab-only source of ``fluid`` (the lab's free materials).
    Without a rate it is unbounded (an no-rate check line)."""
    _only(params, ("fluid", "rate"))
    fluid = _word(params, "fluid")
    if not cat.is_fluid(fluid):
        raise TemplateError(f"{fluid} is not a fluid")
    rate = _rate(params, "rate")
    return [MSpec("m0", "infinity-pipe", None, {}, {fluid: INF if rate is None else rate},
                  frozenset(), source=rate is None, extra={"fluid": fluid})]


def pump(params, cat):
    """``Pump<fluid=lubricant>`` (3.0): one pump in a fluid net, ``net a: x -> p`` and ``net b: p ->
    y``. It takes in at its back and pushes out at its front (``rot`` turns it: rot 0 pushes north),
    so the router meets each net at its own end. A conduit, as a port is: its two nets are one
    flow to the rate model. A long pipe loses throughput; a pump starts a new run (a check names
    the runs too long for their flow)."""
    _only(params, ("fluid",))
    fluid = _word(params, "fluid")
    if not cat.is_fluid(fluid):
        raise TemplateError(f"Pump<fluid={fluid}>: {fluid} is not a fluid")
    return [MSpec("m0", "pump", None, {}, {fluid: 0.0}, frozenset([fluid]),
                  extra={"port": "pump", "item": fluid, "fluid": fluid})]


def roboport(params, cat):
    """``Roboport<>``: a roboport of the logistic network, taking construction and logistic robots
    into its robot inventory from the net (a sink). A ``Chest<requester, item="logistic-robot",
    request=N>`` beside it and a net from the chest to the roboport is the loader: the router lays
    the inserter (``robots``, 2.0.16: every run before this one hand-wrote a blueprint for it, and
    the 0317 and 0928 players each spent a long detour on it: the kit's robots come back out of a
    storage chest only into a roboport). ``cover robots`` counts it as coverage, ``cover power``
    powers it."""
    _only(params, ())
    if not cat.has_entity("roboport"):
        raise TemplateError("this catalog has no roboport")
    return [MSpec("m0", "roboport", None, {}, {},
                  frozenset({"logistic-robot", "construction-robot"}), sink=True)]


def lab_row(params, cat):
    """``LabRow<n, rate=>``: n labs; each takes every science pack that reaches it (a sink), or,
    with ``rate`` (1.6), up to ``rate`` of each pack it is fed: a research unit of T seconds
    takes one of each pack in T seconds, so ``rate=2/min`` for 30 s. Labs with a rate hold the
    science rows back when they can't keep up, and ``n=auto`` sizes them to take what comes."""
    _only(params, ("n", "rate"))
    packs = science_packs(cat)
    rate = _rate(params, "rate")
    return [MSpec(f"m{k}", "lab", None, {}, {}, packs, sink=True, drain=rate,
                  extra={} if rate is None else {"each": True})
            for k in range(_int(params, "n"))]


def lab(params, cat):
    """``Lab<rate=>``: one lab, its inserters laid by the router like an Assembler's (1.7, Rory
    2026-10-21: a lab fed from a multi-item chest by one inserter is valid: the inserter only takes
    what the lab accepts). Several Labs around one requester chest each get their own inserter
    from it; the 2005 player laid that by hand (purple.json) for want of it."""
    _only(params, ("rate",))
    return lab_row(dict(params, n=1), cat)


_CHEST = {"provider": "passive-provider-chest", "storage": "storage-chest",
          "requester": "requester-chest", "buffer": "buffer-chest", "plain": "iron-chest"}


def chest(params, cat):
    """``Chest<provider | storage | requester | buffer | plain, item=, rate=, request=, max=,
    limit=, chest=>``: a boundary.

    provider / storage (the default is provider): the end of a net into the logistic network, a
    sink for whatever reaches it, up to ``rate``. requester: the logistic network feeding a net with
    ``item``, at ``rate`` or unbounded (an no-rate check line); ``request`` is how many the
    chest asks the robots to keep in it (default: a minute of ``rate``, at least 10; else 100).
    ``item="a,b"`` (1.1, from the 0534 player's own patch): a requester asks for several items,
    each at ``rate``, and feeds them all onto its net (a one-off crafter's single chest).
    ``limit`` (1.1): the chest's slots, its inventory bar in the game. A limited chest stops taking
    when full, so what feeds it stops too: limited production (no no-limit or SHARED_SINK line).
    ``trash`` (1.4.2, a requester): the game's "trash unrequested", so items it no longer asks
    for go back to the network (the 2005 player's stone requester kept 101 copper plates).
    2.0: ``max`` (a requester) is the request's upper bound, as the starter plant's requester asks
    for 20 cells and takes at most 50; ``plain`` is a chest off the logistic network (an iron
    chest unless ``chest=`` says otherwise): robots neither fill nor empty it.

    2.1, ``hold`` (the buffer role, PLAN-2.1 G): a chest one net fills and another drains. The
    model takes it as an unlimited supply of what it holds downstream and an unlimited sink
    upstream (``source`` and ``sink`` both, ``extra["buffer"]``), and reports its derivative.
    What it holds is ``item=`` (``a|b`` for several), else what the nets into it carry
    (``elaborate`` fills ``makes`` in). Off the logistic network: ``plain`` is its only role.
    ``bar=`` is the game's word for ``limit=``. ``Buffer<chest=steel-chest, bar=N>`` is this
    chest (the prelude's ``kind Buffer = Chest<plain, hold, chest=steel-chest>``).
    """
    roles = [r for r in _CHEST if params.get(r) is True]
    if len(roles) > 1:
        raise TemplateError("one role only: " + ", ".join(roles))
    role = roles[0] if roles else "provider"
    _only(params, tuple(_CHEST) + ("item", "rate", "chest", "request", "max", "limit", "trash",
                                   "hold", "bar"))
    if params.get("bar") is not None:
        if params.get("limit") is not None:
            raise TemplateError("bar= and limit= are one setting (the chest's inventory bar): "
                                "write one")
        params = dict(params, limit=params["bar"])
    if params.get("hold") not in (None, False):
        return [_hold_chest(params, roles)]
    proto = _word(params, "chest", _CHEST[role])
    rate = _rate(params, "rate")
    limit = _int(params, "limit") if params.get("limit") is not None else None
    if role == "requester":
        items = [i.strip() for i in str(params.get("item", "")).split(",") if i.strip()]
        if not items:
            items = [_word(params, "item")]
        extra = {"role": role}
        if limit is not None:
            extra["limit"] = limit
        if params.get("request") is not None:
            extra["request"] = int(float(params["request"]))
        if params.get("max") is not None:
            extra["max"] = _int(params, "max")
        if params.get("trash") not in (None, False, "no", "false", "0"):
            extra["trash"] = True
        return [MSpec("c0", proto, None, {}, {it: INF if rate is None else rate for it in items},
                      frozenset(), source=rate is None, extra=extra)]
    takes = None if "item" not in params else frozenset([_word(params, "item")])
    extra = {"role": role}
    if limit is not None:
        extra["limit"] = limit
    return [MSpec("c0", proto, None, {}, {}, takes, sink=True, drain=rate, extra=extra)]


def _hold_chest(params, roles):
    """``Chest<plain, hold, ...>`` (2.1): a buffer, off the logistic network."""
    if params.get("hold") is not True:
        raise TemplateError(f"hold is a flag (<hold>), got hold={params['hold']!r}")
    if roles not in ([], ["plain"]):
        raise TemplateError(f"hold: a buffer is a plain chest, off the logistic network, not "
                            f"{roles[0]} (robots would fill or empty it)")
    for bad in ("rate", "request", "max", "trash"):
        if params.get(bad) is not None:
            raise TemplateError(f"hold: a buffer takes chest=, item= and bar=, not {bad}=")
    proto = _word(params, "chest", _CHEST["plain"])
    items = params.get("item")
    if items is None:
        items = []
    elif isinstance(items, list):
        items = [str(i) for i in items]
    else:
        items = [i.strip() for i in str(items).split(",") if i.strip()]
    extra = {"role": "plain", "buffer": True}
    if params.get("limit") is not None:
        extra["limit"] = _int(params, "limit")
    return MSpec("c0", proto, None, {}, {i: INF for i in items},
                 frozenset(items) if items else None, sink=True, source=True, extra=extra)


def turret_row(params, cat):
    """``TurretRow<n, ammo=firearm-magazine>``: n gun turrets taking ammo (a sink)."""
    _only(params, ("n", "ammo"))
    ammo = _word(params, "ammo", "firearm-magazine")
    return [MSpec(f"m{k}", "gun-turret", None, {}, {}, frozenset([ammo]), sink=True)
            for k in range(_int(params, "n"))]


def bus(params, cat):
    """``Bus<belts=4, length, item, load=s11|..., tap=n26|..., mouth=in1|in2>``: Rory's bus
    (2026-10-21, "a river of iron, that you keep adding more iron inputs to, and pull iron out when
    you need it"). Its ports are its endpoints, ``load1``.. then ``tap1``.., in the order written: a
    net into ``bus.load1`` puts ``item`` on the bus, a net from ``bus.tap1`` takes it off. A port
    passes on what reaches it (no rate of its own; a tap's 0 makes it a driver of the item), so the
    rate model joins every net through the bus into one pool (``fkcore.rates``). ``mouth=in1..inN``
    (2.0.33) are the belt ends at the head: a net into ``bus.in1`` feeds belt 1 in line, no staircase.
    The geometry is ``fragments.bus``; a port that doesn't fit is a bad parameter here."""
    _only(params, ("belts", "length", "item", "load", "tap", "mouth", "tapkind"))
    try:
        belts, length, item, loads, taps = bus_ports(params)
        bus_check(belts, length, loads, taps)
    except FragmentError as exc:
        raise TemplateError(str(exc)) from None
    if cat.is_fluid(item):
        raise TemplateError(f"{item} is a fluid: a bus is belts")
    mouths = params.get("mouth") or []
    if isinstance(mouths, str):
        mouths = [mouths]
    seen = set()
    for name in mouths:
        if not isinstance(name, str) or not name.startswith("in") or not name[2:].isdigit():
            raise TemplateError(f"mouth={name}: a mouth is named in1..in{belts}")
        if not 1 <= int(name[2:]) <= belts:
            raise TemplateError(f"mouth={name}: the bus has belts 1..{belts}")
        if name in seen:
            raise TemplateError(f"mouth={name} given twice")
        seen.add(name)
    out = [MSpec(f"load{k}", "transport-belt", None, {}, {}, frozenset([item]),
                 extra={"port": "load", "item": item, "at": f"{s}{x}"})
           for k, (s, x) in enumerate(loads, 1)]
    tkinds = params.get("tapkind") or []
    if isinstance(tkinds, str):
        tkinds = [tkinds]
    if len(tkinds) > len(taps):
        raise TemplateError(f"tapkind={tkinds}: the bus has {len(taps)} taps")
    tkinds = list(tkinds) + ["dual"] * (len(taps) - len(tkinds))
    for kind in tkinds:
        if kind not in ("dual", "left", "right"):
            raise TemplateError(f"tapkind={kind}: dual, left or right")
    out += [MSpec(f"tap{k}", "transport-belt", None, {}, {item: 0.0}, frozenset(),
                  extra={"port": "tap", "item": item, "at": f"{s}{x}",
                         **({} if kind == "dual" else {"kind": kind})})
            for k, ((s, x), kind) in enumerate(zip(taps, tkinds), 1)]
    out += [MSpec(name, "transport-belt", None, {}, {}, frozenset([item]),
                  extra={"port": "load", "item": item, "at": name, "mouth": True})
            for name in mouths]
    return out


#: the core kinds (2.1). ``Assembler``, ``Furnace``, ``ChemPlant`` and ``Refinery`` are kinds of
#: ``Machine`` in the prelude (``fkcore/prelude.fk``)
TEMPLATES = {
    "Machine": machine,
    "Entity": entity_,
    "AsmRow": asm_row,
    "SmelterRow": smelter_row,
    "SmelterArray": smelter_array,
    "MinerRow": miner_row,
    "Pumpjack": pumpjack,
    "OffshorePump": offshore_pump,
    "Boiler": boiler,
    "SteamEngine": steam_engine,
    "SteamTurbine": steam_turbine,
    "Reactor": reactor,
    "HeatExchanger": heat_exchanger,
    "Constant": constant,
    "Decider": decider,
    "Arithmetic": arithmetic,
    "Pole": pole,
    "InfinityPipe": infinity_pipe,
    "Pump": pump,
    "LabRow": lab_row,
    "Lab": lab,
    "Roboport": roboport,
    "Chest": chest,
    "TurretRow": turret_row,
    "Bus": bus,
    "CircuitStack": circuit_stack,
}
#: the core kinds that are one machine each, placed where written: the router lays their belts,
#: pipes and inserters, and every net of the machine gets a role of its own (2.1: one list for
#: ``expand`` and ``elaborate``; a kind resolves to one of these or not)
SINGLE = ("Machine", "Entity", "Chest", "Pumpjack", "OffshorePump", "Pole", "Boiler",
          "SteamEngine", "InfinityPipe", "Lab", "Reactor", "HeatExchanger", "SteamTurbine",
          "Constant", "Roboport", "Decider", "Arithmetic", "Pump")


def literal_machines(entities, cat):
    """The machines of a ``Literal``: rebuilt from its pinned .fkp entities (CORE-V1.md §5).

    A machine entity carries what it needs: ``recipe`` (and ``fuel``) for a crafter, ``resource``
    for a drill, ``role``/``item``/``rate`` for a chest, ``rate`` for a pump. Carriers and poles
    are not machines.
    """
    out = []
    for e in entities:
        local = e.key.rsplit("/", 1)[1]
        kind = cat.kind(e.name)
        a = e.attrs
        rate = float(a["rate"]) if "rate" in a else None
        if kind in ("assembling-machine", "furnace") and a.get("recipe"):
            out.append(_crafter(cat, local, e.name, a["recipe"], a.get("fuel")))
        elif e.name == "pumpjack" and ("resource" in a or rate is not None):
            out.append(MSpec(local, e.name, None, {}, {"crude-oil": rate or 30.0}, frozenset(),
                             extra={"resource": "crude-oil"}))
        elif kind == "mining-drill" and a.get("resource"):
            spec = miner_row({"n": 1, "resource": a["resource"], "drill": e.name,
                              **({"fuel": a["fuel"]} if "fuel" in a else {})}, cat)[0]
            spec.local = local
            out.append(spec)
        elif kind == "offshore-pump" and rate is not None:
            # without a rate it feeds a power plant (an imported Literal), not the netlist
            out.append(MSpec(local, e.name, None, {}, {"water": rate}, frozenset()))
        elif kind == "boiler" and "fuel" in a:
            # the same rule: a boiler without fuel= is part of an imported plant
            spec = boiler({"fuel": a["fuel"], "machine": e.name}, cat)[0]
            spec.local = local
            out.append(spec)
        elif kind == "generator" and a.get("role") == "engine":
            spec = steam_engine({"machine": e.name}, cat)[0]
            spec.local = local
            out.append(spec)
        elif kind == "infinity-pipe" and "fluid" in a:
            spec = infinity_pipe({"fluid": a["fluid"], **({"rate": rate} if rate is not None
                                                         else {})}, cat)[0]
            spec.local = local
            out.append(spec)
        elif kind == "lab":
            out.append(MSpec(local, e.name, None, {}, {}, science_packs(cat), sink=True,
                             drain=rate, extra={} if rate is None else {"each": True}))
        elif kind in ("container", "logistic-container") and "role" in a:
            params = {a["role"]: True, "chest": e.name}
            for k in ("item", "rate"):
                if k in a:
                    params[k] = rate if k == "rate" else a[k]
            spec = chest(params, cat)[0]
            spec.local = local
            out.append(spec)
        elif kind == "ammo-turret":
            out.append(MSpec(local, e.name, None, {}, {}, frozenset([a.get("ammo",
                             "firearm-magazine")]), sink=True))
    return out


def machines(kind, params, cat):
    """The machines an instance of ``kind`` contributes (raises TemplateError)."""
    try:
        fn = TEMPLATES[kind]
    except KeyError:
        raise TemplateError(f"unknown template {kind}") from None
    out = fn(params, cat)
    if params.get("modules") is not None:
        out = with_modules(out, params["modules"])
    return out


def with_modules(specs, modules):
    """``modules=speed-module`` fills every slot of each machine; ``modules=a|b`` names them slot by
    slot. A machine's needs and makes scale with the modules' speed, its makes with their
    productivity; ``extra["modules"]`` is the list the paste requests (``expand``). The machines
    with no slots (an assembling-machine-1, a burner furnace, a chest) are left as they are."""
    words = [str(w) for w in (modules if isinstance(modules, list) else [modules])]
    for w in words:
        if w not in MODULE_EFFECTS:
            raise TemplateError(f"modules={w}: not a module (speed-module, productivity-module, "
                                "efficiency-module, or their -2 / -3)")
    slotted = [s for s in specs if MODULE_SLOTS.get(s.prototype)]
    if not slotted:
        raise TemplateError(f"modules={'|'.join(words)}: {specs[0].prototype if specs else 'it'} has "
                            "no module slots (" + ", ".join(sorted(MODULE_SLOTS)) + " have)")
    for s in slotted:
        slots = MODULE_SLOTS[s.prototype]
        mods = words * slots if len(words) == 1 else words
        if len(mods) > slots:
            raise TemplateError(f"modules={'|'.join(words)}: {s.prototype} has {slots} slots")
        speed = max(0.2, 1 + sum(MODULE_EFFECTS[m][0] for m in mods))
        prod = 1 + sum(MODULE_EFFECTS[m][1] for m in mods)
        s.needs = {i: r * speed for i, r in s.needs.items()}
        s.makes = {i: r * speed * prod for i, r in s.makes.items()}
        s.extra = dict(s.extra, modules=list(mods))
    return specs
