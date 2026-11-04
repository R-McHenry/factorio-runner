"""The game's facts, as read from the game (``fkcore/data/facts.json``, 2.1.1).

``tools/read_facts.py`` reads them over RCON from a laptop server (Factorio 2.0.77, base + Space
Age) and writes the file; see its ``source`` and ``notes``. Nothing here is a choice: which
machines a row may use (the prelude's ``tiers`` lines, 2.1) and the default furnace are in
``fkcore/prelude.fk``; the default inserter stays in Python.

What the file holds:
- ``entities``: every placeable entity's energy (source, W, drain, burner fuel categories), mining
  speed and radius, crafting speed, module slots and allowed effects, steam and heat values;
- ``resources`` (mining time, category, results), ``fuels`` (fuel value, category), ``modules``
  (category, tier, effects), ``fluids`` (heat capacity, temperatures), ``recipes`` (allowed
  effects: which take productivity);
- ``steam_per_s`` and ``heat_w``, worked out from the above (see ``notes``);
- ``circuit``: the usual wire reach and the wire connector ids; ``signals``: every signal that
  can go on a wire, by type, and ``never_transmit``, those a combinator takes but never sends.

Pure, stdlib only.
"""
from __future__ import annotations

import json
import os

from . import pack as _pack

__all__ = ["PATH", "load", "entity", "energy_w", "is_burner", "burns", "mining_radius",
           "mining_time", "mines", "solid_resources", "fuel_value", "fuels", "module_slots",
           "module_effects", "productive", "steam_per_s", "heat_w", "wire_reach", "signals",
           "never_transmit", "electric_consumer", "signal_type", "is_signal", "never_sent",
           "SIGNAL_TYPES"]

#: the run's pack has its own (``tools/read_facts.py --attach``, :mod:`fkcore.pack`); else the base game's
PATH = _pack.path("facts.json")

_CACHE = {}


def load(path=PATH) -> dict:
    """The facts file as a dict (read once per path)."""
    path = os.path.abspath(path)
    if path not in _CACHE:
        with open(path, encoding="utf-8") as fh:
            _CACHE[path] = json.load(fh)
    return _CACHE[path]


def entity(name) -> dict:
    """The entity's facts, {} for a name the game has no placeable entity of."""
    return load()["entities"].get(name) or {}


def energy_w(name):
    """W the entity uses at full work (its burner's or the grid's), None if it uses none."""
    return entity(name).get("energy_w")


def is_burner(name) -> bool:
    return bool(entity(name).get("burner"))


def burns(category="chemical") -> dict:
    """{entity: W} of every entity with a burner that takes ``category`` fuel, in name order."""
    return {n: float(r["energy_w"]) for n, r in load()["entities"].items()
            if r.get("burner") and category in (r.get("fuel_categories") or ())}


def electric_consumer(name) -> bool:
    """The entity draws power from the grid (an electric energy source that takes input; not a
    generator, a solar panel, an accumulator or an energy interface)."""
    r = entity(name)
    return r.get("energy_source") == "electric" and r.get("priority") in (
        "primary-input", "secondary-input", "lamp")


def mining_radius(name):
    return entity(name).get("mining_drill_radius")


def mining_time(resource):
    r = load()["resources"].get(resource)
    return None if r is None else float(r["mining_time"])


def mines(drill, resource) -> bool:
    """``drill`` can mine ``resource`` (the resource's category is one of the drill's)."""
    r = load()["resources"].get(resource) or {}
    return r.get("category") in (entity(drill).get("resource_categories") or ())


def required_fluid(resource):
    """(fluid, per item) a drill takes to mine ``resource`` (uranium ore: sulfuric acid, 1 an
    ore: the game's ``fluid_amount`` is for 10 mining cycles), or None when it needs none."""
    r = load()["resources"].get(resource) or {} if resource else {}
    if not r.get("required_fluid"):
        return None
    return r["required_fluid"], float(r.get("fluid_amount") or 0) / 10


def solid_resources() -> dict:
    """{resource: mining time} of every resource a drill mines into items, in the game's order."""
    return {n: float(r["mining_time"]) for n, r in load()["resources"].items()
            if not r.get("fluid_results")}


def fuel_value(item):
    r = load()["fuels"].get(item)
    return None if r is None else float(r["fuel_value_j"])


def fuels(category="chemical") -> dict:
    """{item: J} of every fuel of ``category``, in name order."""
    return {n: float(r["fuel_value_j"]) for n, r in load()["fuels"].items()
            if r.get("category") == category}


def module_slots() -> dict:
    """{entity: slots} of every entity with module slots."""
    return {n: int(r["module_slots"]) for n, r in load()["entities"].items()
            if r.get("module_slots")}


def module_effects() -> dict:
    """{module: (speed, productivity)} of every module, in the game's order."""
    return {n: (float(r["effects"].get("speed", 0.0)), float(r["effects"].get("productivity", 0.0)))
            for n, r in load()["modules"].items()}


def productive() -> frozenset:
    """The recipes that allow productivity (``allowed_effects``)."""
    return frozenset(n for n, r in load()["recipes"].items()
                     if "productivity" in (r.get("effects") or ()))


def steam_per_s() -> dict:
    return {n: float(v) for n, v in load()["steam_per_s"].items()}


def heat_w() -> dict:
    return {n: float(v) for n, v in load()["heat_w"].items()}


def wire_reach() -> float:
    """How far a red or green wire reaches between most entities (the game's usual value)."""
    return float(load()["circuit"]["wire_reach"])


def signals() -> dict:
    """{type: [name]} of every signal that can go on a wire."""
    return load()["signals"]


def never_transmit() -> dict:
    """{type: [name]}: signals a combinator takes but never puts on its wire."""
    return load()["never_transmit"]


#: a signal name's type when the game has it under several (an item that is also a recipe and an
#: entity is the item, as the game reads a signal written without its type)
SIGNAL_TYPES = ("virtual", "item", "fluid", "entity", "recipe", "quality", "space-location",
                "asteroid-chunk")
_TYPE_OF = {}


def _types():
    if not _TYPE_OF:
        got = {}
        sigs, never = signals(), never_transmit()
        for kind in SIGNAL_TYPES:
            for name in list(sigs.get(kind) or ()) + list(never.get(kind) or ()):
                got.setdefault(name, kind)
        for kind in sorted(set(sigs) - set(SIGNAL_TYPES)):
            for name in sigs[kind]:
                got.setdefault(name, kind)
        _TYPE_OF.update(got)
    return _TYPE_OF


def is_signal(name) -> bool:
    """``name`` is a signal of the game (one that can go on a wire, or one the game takes and
    never sends, like ``signal-each``)."""
    return name in _types()


def signal_type(name):
    """A signal's type by its name (2.1, from the game): ``virtual``, ``item``, ``fluid``,
    ``entity``, ``recipe``, ``quality``, ``space-location`` or ``asteroid-chunk``; ``item`` for a
    name the game has no signal of (the game's default type)."""
    return _types().get(name, "item")


def never_sent() -> frozenset:
    """Every signal the game takes but never puts on a wire, of any type."""
    return frozenset(n for names in never_transmit().values() for n in names)
