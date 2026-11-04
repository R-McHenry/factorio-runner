"""fkcore.cost: what a design costs to build, in raw resources (2.0.60).

Rory, 2026-10-29: the size step "should have the rate model, and just optimise the params to get
the desired rate"; with module counts and machine tier among the parameters, two designs that both
meet the rate are told apart by what they cost. An item costs what its recipe's ingredients cost,
per unit it makes, down to the raw resources (an ore, coal, stone, wood, a fluid: one unit each),
so an iron plate is 1, a gear 2, a green circuit 2.5 and an assembling-machine-2 is 54. A design
costs its machines and the modules in them; belts, inserters and poles are the router's and the
power step's, not yet known when the size step chooses. Pure, stdlib only.
"""
from __future__ import annotations

from . import facts as _facts
from .catalog import expected

__all__ = ["item_cost", "design_cost"]

#: what one unit of a fluid costs: a pump moves 1200/s of water, so a fluid is nearly free
FLUID = 0.0


def item_cost(cat, item, memo=None, open_=None):
    """Raw resources one ``item`` takes: its own recipe (the one of the same name, else any recipe
    that makes it) down to the resources, which cost 1 (a fluid ``FLUID``)."""
    memo = {} if memo is None else memo
    open_ = set() if open_ is None else open_
    if item in memo:
        return memo[item]
    if cat.is_fluid(item):
        return FLUID
    # an ore is mined: it costs 1 whatever else makes it (2.1: with the game's probabilities read
    # in, ``iron-ore-recycling`` gives ore back at 0.25 and asteroid crushing gives it too). A
    # recycler is no recipe for anything: it unmakes
    if any(item in (r.get("results") or ()) for r in _facts.load()["resources"].values()):
        memo[item] = 1.0
        return 1.0
    recipe = item if cat.has_recipe(item) else next(
        (r["name"] for r in cat.recipes() if r.get("category") != "recycling"
         and any(p.get("name") == item for p in r.get("products", ()))), None)
    if recipe is None or item in open_:
        memo[item] = 1.0
        return 1.0
    r = cat.recipe(recipe)
    made = sum(expected(p) if "amount" in p or "amount_min" in p else p.get("probability", 1)
               for p in r.get("products", ()) if p.get("name") == item) or 1
    open_.add(item)
    total = sum(i.get("amount", 1) * item_cost(cat, i["name"], memo, open_)
                for i in r.get("ingredients", ()))
    open_.discard(item)
    memo[item] = total / made
    return memo[item]


def design_cost(flat, cat, memo=None):
    """Raw resources a factory's machines and their modules take (not the route's belts and
    inserters, nor poles): the size step's measure of a choice. -> float."""
    memo = {} if memo is None else memo
    total = 0.0
    for key, m in flat.machines.items():
        if key in getattr(flat, "ports", {}) or m.extra.get("port"):
            continue
        total += item_cost(cat, m.prototype, memo)
        for module in m.extra.get("modules", ()):
            total += item_cost(cat, module, memo)
    return total
