"""Arm specs: one TOML file per arm, every axis written out, resolved to a lock with hashes."""
from __future__ import annotations

import hashlib
import json
import os
import tomllib

DEFAULTS = {
    "episode": {"max_turns": 60, "max_usd": 0.25, "max_ticks": 216000, "goal_dist": 3,
                "eval_cap": 36000, "check_every": 600, "snapshot_every": 7200},
    "session": {"episodes": 3, "next": "select", "max_usd": 1.0, "context_limit": 120000,
                "trim_at": 40000, "trim_keep": 8},
    "prompt": {"layers": ["l0-lab-bare"], "facts_p": 0.5},
    "toolkit": {"profile": "bare"},
    "objective": {"mode": "cells"},   # "cells": open-ended production goals; "ladder": win a rocket
    "budget": {"monthly_usd": 10.0, "hard_usd": 0},   # target (shown) and limit (0: none)
}


def _file_hash(path: str | None) -> str | None:
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha256()
    if os.path.isdir(path):
        for dirpath, dirs, files in os.walk(path):
            dirs.sort()
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                h.update(os.path.relpath(full, path).encode())
                with open(full, "rb") as fh:
                    h.update(fh.read())
    else:
        with open(path, "rb") as fh:
            h.update(fh.read())
    return h.hexdigest()[:16]


def load(path: str) -> dict:
    with open(path, "rb") as fh:
        spec = tomllib.load(fh)
    for k, v in DEFAULTS.items():
        spec[k] = {**v, **spec.get(k, {})}
    base = os.path.dirname(os.path.abspath(path))
    paths = {}
    for k in ("mods", "map_gen", "map_settings", "codebase"):
        p = spec["game"].get(k)
        paths[k] = os.path.normpath(os.path.join(base, p)) if p else None
    spec["_paths"] = paths
    return spec


def lock(spec: dict) -> dict:
    """Every axis resolved: the spec itself plus hashes of the files it names."""
    from . import __version__
    pure = {k: v for k, v in spec.items() if not k.startswith("_")}
    return {"spec": pure, "runner": __version__,
            "hashes": {k: _file_hash(p) for k, p in spec["_paths"].items()},
            "prompt_hash": hashlib.sha256("".join(
                open(os.path.join(os.path.dirname(__file__), "prompts", f"{layer}.md")).read()
                for layer in spec["prompt"]["layers"]).encode()).hexdigest()[:16],
            "spec_hash": hashlib.sha256(json.dumps(pure, sort_keys=True).encode()).hexdigest()[:16]}
