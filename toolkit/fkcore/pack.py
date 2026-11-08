"""fkcore.pack: the run's mod pack (env ``FK_PACK``, else a ``pack`` file in the game codebase,
the working directory fk runs in) and the facts about it that are not prototypes.

A pack lives in ``fkcore/data/packs/<pack>/``: ``catalog.json`` and ``fluidboxes.json`` (dumped
from its game, ``tools/dump_catalog.lua``) and ``pack.json``, written by hand:

    {"landfill": "sand-3", "landfill_item": "landfill-sand", "mods_dir": "C:\\fk-mods\\sb-mods"}
        the tile that makes water ground (the one a game paste lays by itself) and the item that
        places it (default: the tile's name, as in the base game's landfill); ``mods_dir``, the
        folder on this machine holding the pack's mods, which ``fk sim`` loads (3.0);
        ``inserter_vectors``: every inserter takes its own pickup and drop vectors (Bob's
        adjustable inserters, ``fkcore.arms``; 3.0)

Sea Block's world is water: with ``landfill`` set, water is ground that costs landfill (the
terrain stops blocking it, SHORT counts it, and a single ghost on water gets landfill under it,
as a paste does by itself once the landfill recipe is unlocked). Unset pack: the base game.
Read at call time, so a test can set the variable. Pure, stdlib only.
"""
import json
import os

__all__ = ["name", "path", "facts", "landfill", "landfill_item"]

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def name():
    """The pack's name, or None for the base game."""
    if "FK_PACK" in os.environ:
        return os.environ["FK_PACK"] or None
    try:
        with open("pack", encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def path(*parts):
    """A file of the pack (or of the base game's ``data`` when there is no pack)."""
    n = name()
    return os.path.join(DATA, "packs", n, *parts) if n else os.path.join(DATA, *parts)


def facts():
    """The pack's ``pack.json``, {} for the base game or a pack without one."""
    p = path("pack.json") if name() else None
    if not p or not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def landfill():
    """The landfill tile that turns water into ground in this pack, or None (water blocks)."""
    return facts().get("landfill") or None


def landfill_item():
    """The item that places the landfill tile (what a build's bill counts), or None."""
    f = facts()
    return f.get("landfill_item") or f.get("landfill") or None
