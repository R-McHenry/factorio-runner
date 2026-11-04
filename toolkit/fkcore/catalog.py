"""Prototype facts, read from fkcore/data/catalog.json, fkcore/data/fluidboxes.json and
fkcore/data/facts.json.

Copied from C:\\fk-lead\\repo\\fk\\catalog.py (d6018d3) with its measured fixture fk/catalog-v5.json
(Factorio 2.0 + Space Age prototypes). Added for V1: a default path, ``load()`` with no argument,
``kind`` (the entity type), ``recipe_rates`` (per-second ingredients and products, fluids included)
and the few base-game facts the fixture does not carry (``FUEL_USE_W``, ``FUEL_VALUE_J``,
``MINING_TIME``, ``STEAM_PER_S``, ``MINING_RADIUS``). Every other module takes a Catalog
instance instead of opening the fixture itself. 2.0 added to the six inserters' rows the values
read from the game (2.0.77, docs/LAB-LANES-20261020.md "Prototype values"): ``rotation_speed``,
``extension_speed``, ``bulk``, ``stack_size_bonus`` (the stack inserter's own +4 hand) and
``filter_count``, which the lane graph rates them by. 2.0.51 added the two prototypes the starter
kit needs now that the kit is part of a run's design (``solar-panel`` 3x3, ``accumulator`` 2x2,
sizes read from the live game's own entities: their bounding boxes on the run's map).

Fluid boxes (``fluidboxes.json``, read from the game over RCON, 2.0.77, see its ``source``):
every fluid prototype's boxes and pipe connections per direction, and per crafting machine and
recipe the boxes the recipe makes live. :meth:`Catalog.fluid_boxes_at` turns them into world
tiles: for an entity at (x, y) facing ``direction`` with ``recipe``, each live box with its
fluid (filter or recipe) and its connections (the tile inside the entity, the side it faces, the
tile a pipe must stand on).

2.1.1 (facts into data): ``tools/read_facts.py`` reads the game over RCON. ``catalog.json`` has a
row for every placeable entity (145: combinators, power switch, speaker, display panel, lamps,
train stops ...), each with ``placed_by``, ``energy`` and ``circuit`` (its wire connector ids and
reach); the measured rows keep their values. The facts above are reads of ``facts.json``
(``fkcore.facts``), and this module's own fallback rows (the combinators, the fluid file's
``infinity-pipe``) are gone. Where the game said more than the old dicts did, its answers are
taken (docs/RELEASE-2.1.1.md lists each). Pure, stdlib only.
"""
from __future__ import annotations

import json
import math
import os
from collections import namedtuple

from . import facts as _facts
from . import pack as _pack

__all__ = ["Catalog", "DEFAULT_PATH", "FLUID_PATH", "FUEL_USE_W", "FUEL_VALUE_J", "MINING_TIME",
           "MINING_RADIUS", "STEAM_PER_S", "HEAT_W", "REACTOR_FUEL_S", "CIRCUIT_WIRE_REACH",
           "FluidBox", "PipeConn", "expected"]

#: The run's mod pack (env FK_PACK, :mod:`fkcore.pack`): its catalog is fkcore/data/packs/<pack>/,
#: dumped from that game by tools/dump_catalog.lua + .py. Unset: the base game's measured fixture.
DEFAULT_PATH = _pack.path("catalog.json")
#: The fluid boxes beside the fixture (read from the game; see the file's ``source``).
FLUID_PATH = os.path.join(os.path.dirname(DEFAULT_PATH), "fluidboxes.json")

# Base-game facts the measured fixture does not record: reads of facts.json (fkcore.facts).
#: Burner energy use in watts, for machines whose endpoints include a fuel input (every entity
#: with a burner that takes chemical fuel).
FUEL_USE_W = _facts.burns("chemical")
#: Fuel value in joules (chemical fuels).
FUEL_VALUE_J = _facts.fuels("chemical")
#: Mining time per resource a drill mines into items (an electric drill at mining speed 0.5 makes
#: 0.5/s of a time-1 ore), in the game's order.
MINING_TIME = _facts.solid_resources()
#: Steam per second: a boiler makes it from as much water (1.8 MW, 15 C to 165 C at the steam's
#: 0.2 kJ a unit per degree: 60/s), a steam engine uses it at full power (fluid_usage_per_tick
#: 0.5: 30/s, 900 kW). 2.0 (the starter reactor): an exchanger turns 10 MW into 500 C steam,
#: 103.09/s; a turbine takes 60/s (5.82 MW).
STEAM_PER_S = _facts.steam_per_s()
#: Heat in watts (2.0, the starter reactor): a reactor makes 40 MW while a cell burns (200 s, 8 GJ)
#: whatever is drawn; an exchanger takes 10 MW (a heating tower 40 MW). The netlist carries heat as
#: the item ``heat`` in MW.
HEAT_W = _facts.heat_w()
#: Seconds one fuel item burns in a reactor (its fuel value at the reactor's 40 MW).
REACTOR_FUEL_S = {n: j / _facts.energy_w("nuclear-reactor")
                  for n, j in _facts.fuels("nuclear").items()}

#: How far a red or green wire reaches from an entity whose row has no reach of its own (the
#: game's usual get_max_circuit_wire_distance; fnet's WIRE_REACH).
CIRCUIT_WIRE_REACH = _facts.wire_reach()

_STEP = {0: (0, -1), 4: (1, 0), 8: (0, 1), 12: (-1, 0)}

#: One pipe connection of a placed entity, in world tiles: ``tile`` (inside the entity), ``dir``
#: (the side it faces: 0 N, 4 E, 8 S, 12 W), ``target`` (the tile next to it on that side, where a
#: connecting entity's own connection faces back; None for an underground one), ``flow`` (input,
#: output, input-output), ``type`` (normal, underground), ``reach`` (an underground's max
#: distance, end to end) and ``categories`` (they must share one to connect).
PipeConn = namedtuple("PipeConn", "tile dir target flow type reach categories",
                      defaults=(None, ("default",)))
#: One live fluid box of a placed entity: ``box`` (1-based, the game's order), ``fluid`` (the
#: filter or the recipe's fluid; None = whatever flows in), ``production`` (input, output,
#: input-output, none) and ``conns`` (its PipeConns).
FluidBox = namedtuple("FluidBox", "box fluid production conns")

#: A drill mines the tiles whose centres lie within this distance of its centre on both axes
#: (``mining_drill_radius``, read from the game): an electric drill mines 5x5.
MINING_RADIUS = {n: float(r["mining_drill_radius"])
                 for n, r in _facts.load()["entities"].items() if "mining_drill_radius" in r}



class Catalog:
    """Immutable view over the measured prototype catalog."""

    def __init__(self, version, entities, recipes, fluid=None):
        self.version = version
        self._entities = entities
        self._recipes = recipes
        self._fluids = None
        self._fluid = fluid or {}
        self._items = None

    # -- construction ---------------------------------------------------------------------
    @classmethod
    def load(cls, path=DEFAULT_PATH) -> "Catalog":
        """The fixture at ``path``, with the fluid boxes beside it (``fluidboxes.json``) if any."""
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        fluid = os.path.join(os.path.dirname(os.path.abspath(path)), "fluidboxes.json")
        if os.path.exists(fluid):
            with open(fluid, encoding="utf-8") as fh:
                data = dict(data, fluidboxes=json.load(fh))
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "Catalog":
        entities = {}
        for row in data.get("entities", []):
            entities[row["name"]] = row
        recipes = {}
        for row in data.get("recipes", []):
            recipes[row["name"]] = row
        return cls(data.get("version"), entities, recipes, data.get("fluidboxes"))

    # -- prototypes -----------------------------------------------------------------------
    def entity(self, name) -> dict:
        try:
            return self._entities[name]
        except KeyError:
            raise KeyError(name) from None

    def recipe(self, name) -> dict:
        try:
            return self._recipes[name]
        except KeyError:
            raise KeyError(name) from None

    def recipes(self) -> list[dict]:
        """Every recipe row the fixture holds, in fixture order (a read-only copy)."""
        return list(self._recipes.values())

    def electric_poles(self) -> list[dict]:
        """Every electric-pole prototype, strongest first (supply area, then wire reach)."""
        rows = [row for row in self._entities.values()
                if row.get("type") == "electric-pole"]
        rows.sort(key=lambda row: (-float(row.get("supply_area_distance") or 0.0),
                                   -float(row.get("max_wire_distance") or 0.0),
                                   str(row.get("name"))))
        return rows

    def has_entity(self, name) -> bool:
        return name in self._entities

    def has_recipe(self, name) -> bool:
        return name in self._recipes

    def kind(self, name) -> str:
        """The prototype's entity type (``transport-belt``, ``inserter``, ``assembling-machine``...)."""
        return self.entity(name).get("type", "")

    # -- fluid boxes ----------------------------------------------------------------------
    def has_fluid_boxes(self, name) -> bool:
        """The prototype has fluid boxes (pipes, pumps, boilers, engines, chemical plants ...)."""
        return bool(self.fluid_boxes(name))

    def fluid_boxes(self, name) -> list:
        """The prototype's fluid boxes as read from the game (``fluidboxes.json``), [] if none."""
        row = (self._fluid.get("entities") or {}).get(name)
        return list(row["boxes"]) if row else []

    def fluid_boxes_at(self, name, x, y, direction=None, recipe=None, resource=None) -> list:
        """[FluidBox] of an entity of ``name`` centred at (x, y) facing ``direction`` (None = N)
        with ``recipe``, in world tiles: the boxes the game makes live, in its order.

        A drill whose boxes need a fluid resource (2.1: the electric mining drill) has its box
        live only on a ``resource`` that needs a fluid (uranium ore: sulfuric acid), and that
        box holds the fluid; on any other ore it has none, as in the game.

        A crafting machine with a recipe the file measured takes that recipe's live boxes (one
        fluid on a side with several boxes holds them all: one box, every connection). A recipe
        it did not measure gets the same rule from the recipe itself (fluids in order, one per
        box, an explicit ``fluidbox_index`` first). No recipe: every prototype box, except where
        the boxes need a fluid recipe (assembling machines 2 and 3)."""
        row = (self._fluid.get("entities") or {}).get(name)
        if not row:
            return []
        d = int(direction or 0) % 16
        k = d // 4 if d % 4 == 0 else 0
        protos = {b["index"]: b for b in row["boxes"]}
        live = self._recipe_boxes(name, row, recipe)
        if row.get("boxes_need_fluid_resource"):
            need = _facts.required_fluid(resource)
            if need is None:
                return []
            live = [(need[0], [[b["index"], j + 1] for j in range(len(b["connections"]))])
                    for b in row["boxes"]]
        if live is None:
            if row.get("boxes_need_fluid_recipe"):
                return []
            live = [(b.get("filter"), [[b["index"], j + 1] for j in range(len(b["connections"]))])
                    for b in row["boxes"]]
        out = []
        for i, (fluid, members) in enumerate(live, 1):
            conns, prods = [], set()
            for bi, ci in members:
                b = protos[bi]
                prods.add(b["production"])
                c = b["connections"][ci - 1]
                ax, ay = c["at"][k]
                tile = (math.floor(float(x) + ax), math.floor(float(y) + ay))
                facing = (int(c["dir"]) + d) % 16 if d % 4 == 0 else int(c["dir"])
                step = _STEP[facing]
                target = None if c["type"] != "normal" else (tile[0] + step[0], tile[1] + step[1])
                conns.append(PipeConn(tile, facing, target, c["flow"], c["type"],
                                      c.get("max_distance"),
                                      tuple(c.get("categories") or ("default",))))
            prod = prods.pop() if len(prods) == 1 else "input-output"
            out.append(FluidBox(i, fluid or protos[members[0][0]].get("filter"), prod,
                                tuple(conns)))
        if self.kind(name) == "pump":
            # 3.0: a pump moves its fluid one way, so its box is two ends: the input it takes from
            # and the output it pushes to, each a box of its own (a Pump ends two nets)
            split = []
            for b in out:
                ways = [(f, tuple(c for c in b.conns if c.flow == f)) for f in ("input", "output")]
                if all(cs for _f, cs in ways):
                    for f, cs in ways:
                        split.append(FluidBox(len(split) + 1, b.fluid, f, cs))
                else:
                    split.append(b._replace(box=len(split) + 1))
            out = split
        return out

    def water_tiles(self, name, x, y, direction=None) -> set:
        """The tiles that must be water for an offshore pump at (x, y) facing ``direction`` (its
        ``tile_buildability_rules``, read from the game); empty for anything else."""
        row = (self._fluid.get("entities") or {}).get(name) or {}
        if not row.get("water_area"):
            return set()
        x0, y0, x1, y1 = row["water_area"]
        corners = [(x0, y0), (x1, y1)]
        for _ in range((int(direction or 0) % 16) // 4):
            corners = [(-cy, cx) for cx, cy in corners]          # a quarter turn clockwise
        (ax, ay), (bx, by) = corners
        lo_x, hi_x = sorted((ax, bx))
        lo_y, hi_y = sorted((ay, by))
        eps = 1e-6
        return {(tx, ty)
                for tx in range(math.floor(float(x) + lo_x + eps), math.ceil(float(x) + hi_x - eps))
                for ty in range(math.floor(float(y) + lo_y + eps), math.ceil(float(y) + hi_y - eps))}

    def _recipe_boxes(self, name, row, recipe):
        """[(fluid, [[box, connection], ...])] a recipe makes live, or None (no recipe rule)."""
        if not recipe:
            return None
        measured = ((self._fluid.get("recipes") or {}).get(name) or {}).get(recipe)
        if measured is not None:
            return [(b["fluid"], [list(m) for m in b["members"]]) for b in measured]
        if row["type"] not in ("assembling-machine", "furnace") or not self.has_recipe(recipe):
            return None
        rec = self.recipe(recipe)
        out = []
        for side, rows in (("input", rec.get("ingredients") or ()), ("output", rec.get("products")
                                                                      or ())):
            fluids = [r for r in rows if r.get("type") == "fluid"]
            boxes = [b for b in row["boxes"] if b["production"] == side]
            if not fluids:
                continue
            if len(fluids) == 1 and not fluids[0].get("fluidbox_index"):
                out.append((fluids[0]["name"], [[b["index"], j + 1] for b in boxes
                                                 for j in range(len(b["connections"]))]))
                continue
            free = list(boxes)
            for r in fluids:
                at = r.get("fluidbox_index")
                b = boxes[at - 1] if at and at <= len(boxes) else (free[0] if free else None)
                if b is None:
                    break
                if b in free:
                    free.remove(b)
                out.append((r["name"], [[b["index"], j + 1] for j in range(len(b["connections"]))]))
        return out

    def is_fluid(self, name) -> bool:
        """A fluid: a recipe names it with ``type: fluid``; water, steam and crude oil also come
        from machines without a recipe (offshore pump, boiler, pumpjack)."""
        if self._fluids is None:
            names = {"water", "steam", "crude-oil"}
            for rec in self._recipes.values():
                for row in list(rec.get("ingredients") or ()) + list(rec.get("products") or ()):
                    if row.get("type") == "fluid":
                        names.add(row["name"])
            self._fluids = frozenset(names)
        return name in self._fluids

    def recipe_rates(self, recipe, speed) -> tuple[dict, dict]:
        """``(needs, makes)`` per second for one machine crafting ``recipe`` at ``speed``.

        Items and fluids alike; a product with a probability or an amount range counts its mean
        (``expected``).
        """
        rec = self.recipe(recipe)
        crafts = float(speed) / float(rec["energy"])
        needs, makes = {}, {}
        for ing in rec.get("ingredients", ()):
            needs[ing["name"]] = needs.get(ing["name"], 0.0) + ing["amount"] * crafts
        for prod in rec.get("products", ()):
            makes[prod["name"]] = makes.get(prod["name"], 0.0) + expected(prod) * crafts
        return needs, makes

    def smelting_recipe(self, ingredient) -> str:
        """The smelting recipe whose only ingredient is ``ingredient`` (iron-ore -> iron-plate)."""
        for rec in self._recipes.values():
            ings = rec.get("ingredients") or ()
            if rec.get("category") == "smelting" and len(ings) == 1 and ings[0]["name"] == ingredient:
                return rec["name"]
        raise KeyError(ingredient)

    def size(self, name) -> tuple[int, int]:
        row = self.entity(name)
        return (row["tile_width"], row["tile_height"])

    def tiles(self, name, x, y, direction=None) -> set[tuple[int, int]]:
        """Tiles occupied by an entity of `name` centred at (x, y).

        The footprint runs from floor(c - size/2) to floor(c + size/2) - 1 inclusive, so the centre
        must line up with the size: odd sizes sit on half-tile centres, even sizes on integers.
        East/west (direction 4/12) swap width and height. Without a direction, a centre that only
        lines up with the swapped size is taken as rotated (a 2x1 splitter facing east sits on
        (x.5, y), facing north on (x, y.5)): most callers do not pass the direction.
        2.1.28: remembered per (name, x, y, direction), a fresh set each call (route's patches
        asked ~675k times for the 20261030-2352 world's few thousand entities).
        """
        memo = self.__dict__.setdefault("_tiles_memo", {})
        key = (name, x, y, direction)
        got = memo.get(key)
        if got is None:
            got = memo[key] = tuple(self._tiles(name, x, y, direction))
        return set(got)

    def _tiles(self, name, x, y, direction):
        w, h = self.size(name)
        if direction is not None and int(direction) % 8 == 4:
            w, h = h, w
        elif direction is None and w != h:
            fits = _is_integer(x - w / 2) and _is_integer(y - h / 2)
            if not fits and _is_integer(x - h / 2) and _is_integer(y - w / 2):
                w, h = h, w
        lo_x = x - w / 2
        lo_y = y - h / 2
        if not _is_integer(lo_x) or not _is_integer(lo_y):
            raise ValueError(
                "centre (%r, %r) is inconsistent with size (%d, %d) of %s" % (x, y, w, h, name)
            )
        x0 = int(math.floor(lo_x))
        y0 = int(math.floor(lo_y))
        return {(x0 + dx, y0 + dy) for dx in range(w) for dy in range(h)}

    # -- gameplay facts -------------------------------------------------------------------
    def is_electric(self, name) -> bool:
        """The entity draws power from the grid (``fkcore.facts``: an electric energy source that
        takes input)."""
        self.entity(name)
        return _facts.electric_consumer(name)

    def craft_rate(self, recipe, machine) -> dict[str, float]:
        """Products per second for one machine: amount * crafting_speed / energy."""
        rec = self.recipe(recipe)
        row = self.entity(machine)
        speed = row.get("crafting_speed")
        if speed is None:
            speed = row.get("mining_speed")
        if speed is None:
            raise KeyError(machine)
        energy = rec["energy"]
        rates = {}
        for product in rec.get("products", []):
            rates[product["name"]] = rates.get(product["name"], 0.0) \
                + expected(product) * speed / energy
        return rates

    def belt_throughput(self, name="transport-belt") -> float:
        """Items/s on a fully compressed belt, both lanes."""
        return self.entity(name)["belt_speed"] * 60 * 8

    def inserter_throughput(self, name="inserter"):
        """Items/s one inserter moves with hand size 1: ``60 * rotation_speed``.

        The rule ``fk.netlist`` already uses for an inserter link (contracts/netlist.md); ``None``
        when the prototype carries no rotation speed (a modded or unmeasured entity).  A basic
        inserter is 0.84/s and a fast inserter 2.40/s in the measured catalog.
        """
        row = self.entity(name)
        speed = row.get("rotation_speed")
        return None if speed is None else 60.0 * float(speed)

    def pole_supply(self, name) -> float:
        return self.entity(name)["supply_area_distance"]

    def pole_reach(self, name) -> float:
        return self.entity(name)["max_wire_distance"]

    def circuit_reach(self, name) -> float:
        """How far a red or green wire from ``name`` reaches: its row's (the game's
        get_max_circuit_wire_distance: 32 for a big pole, 10 for a power switch), else the
        usual 9."""
        row = self.entity(name)
        return float((row.get("circuit") or {}).get("wire_reach") or CIRCUIT_WIRE_REACH)

    def is_item(self, name) -> bool:
        """An item: one that places an entity, or a recipe's item (2.0, for item signals in
        circuit conditions). 2.1.1: the item that places it, not the entity's name (a rail's
        is ``rail``, not ``straight-rail``)."""
        if self._items is None:
            names = set()
            for row in self._entities.values():
                names.update(row.get("placed_by") or (row["name"],))
            for rec in self._recipes.values():
                for row in list(rec.get("ingredients") or ()) + list(rec.get("products") or ()):
                    if row.get("type", "item") == "item":
                        names.add(row["name"])
            self._items = frozenset(names)
        return name in self._items and not self.is_fluid(name)


def expected(prod) -> float:
    """What one craft gives of a recipe product on average: its amount (the middle of a range),
    plus a recycler's ``extra_count_fraction``, times its ``probability`` (2.1, read from the
    game: ``uranium-processing`` gives U-235 at 0.007, so 0.007 a craft, not 1)."""
    if "amount" in prod:
        amount = float(prod["amount"])
    else:
        amount = (float(prod.get("amount_min", 0)) + float(prod.get("amount_max", 0))) / 2
    amount += float(prod.get("extra_count_fraction") or 0.0)
    return amount * float(prod.get("probability", 1.0))


def _is_integer(value) -> bool:
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value) and value.is_integer()
    return float(value).is_integer()
