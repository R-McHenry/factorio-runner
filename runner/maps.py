"""Maps of a running game for the status page: every surface's entities, on terrain drawn by
Factorio's own map-preview renderer.

The headless server can't take screenshots, but the same binary renders terrain previews
(`--generate-map-preview`, 1 m per pixel, any planet). Terrain depends only on the seed, the
planet, the map-gen settings and the window, so each preview is made once and cached in the store.
Entities are dumped from the game (the player's, their ghosts, and enemy structures) and drawn by
the page on top, so they stay sharp at any zoom.

Store layout:
- `maps/<arm>/<session>/index.json`: {tick, surfaces: [{name, planet, platform, seed, entities,
  terrain, window}]}, rewritten at each capture;
- `maps/<arm>/<session>/<surface>.json`: one surface's entities;
- `maps/terrain/<key>.png`: cached terrain previews.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time

#: every surface with something on it, and what it is
LUA_SURFACES = """
local out = {}
for _, s in pairs(game.surfaces) do
  local n = s.count_entities_filtered{force = 'player'}
  local m = s.count_entities_filtered{force = 'enemy', type = {'unit-spawner', 'turret'}}
  local mg = s.map_gen_settings
  out[#out + 1] = {name = s.name, index = s.index, entities = n, enemies = m,
    planet = s.planet and s.planet.name or nil, platform = s.platform and s.platform.name or nil,
    seed = mg and mg.seed or nil}
end
rcon.print(helpers.table_to_json(out))
"""

#: one surface's entities, written to script-output (too big for one RCON reply on a large base):
#: type|name|x|y|w|h|ghost|force per line
LUA_DUMP = """
local s = game.surfaces[%s]
local o = {}
local function add(e, ghost)
  local b = e.bounding_box
  local w, h = b.right_bottom.x - b.left_top.x, b.right_bottom.y - b.left_top.y
  local t = ghost and e.ghost_type or e.type
  local n = ghost and e.ghost_name or e.name
  o[#o + 1] = string.format('%%s|%%s|%%.1f|%%.1f|%%.1f|%%.1f|%%d|%%s', t, n, e.position.x, e.position.y, w, h, ghost and 1 or 0, e.force.name)
end
for _, e in pairs(s.find_entities_filtered{force = 'player'}) do
  if e.type == 'entity-ghost' then add(e, true) elseif e.type ~= 'character' and e.type ~= 'construction-robot' and e.type ~= 'logistic-robot' then add(e, false) end
end
for _, e in pairs(s.find_entities_filtered{force = 'enemy', type = {'unit-spawner', 'turret'}}) do add(e, false) end
helpers.write_file(%s, table.concat(o, '\\n'), false)
rcon.print(#o)
"""


def _window(rows: list, margin: int = 96) -> tuple[int, int, int]:
    """A square terrain window (center x, center y, size in m) covering the entities. Snapped to
    multiples of 64 and powers of two from 256 to 2048, so captures reuse cached terrain."""
    if not rows:
        return 0, 0, 512
    xs = [r[2] for r in rows]
    ys = [r[3] for r in rows]
    cx = round((min(xs) + max(xs)) / 2 / 64) * 64
    cy = round((min(ys) + max(ys)) / 2 / 64) * 64
    need = max(max(xs) - min(xs), max(ys) - min(ys)) + 2 * margin
    size = 256
    while size < need and size < 2048:
        size *= 2
    return int(cx), int(cy), size


def _parse(text: str) -> list:
    rows = []
    for line in text.splitlines():
        p = line.split("|")
        if len(p) != 8:
            continue
        try:
            rows.append((p[0], p[1], float(p[2]), float(p[3]), float(p[4]), float(p[5]), p[6] == "1", p[7]))
        except ValueError:
            continue
    return rows


def _pack(rows: list) -> dict:
    """Compact entity lists for the page: names indexed, coordinates in half tiles."""
    names, idx = [], {}
    out = {}
    for t, n, x, y, w, h, ghost, force in rows:
        if n not in idx:
            idx[n] = len(names)
            names.append(n)
        key = "enemy:" + t if force == "enemy" else ("ghost:" if ghost else "") + t
        out.setdefault(key, []).extend([round(x * 2), round(y * 2), max(1, round(w * 2)), max(1, round(h * 2)), idx[n]])
    return {"names": names, "types": out}


def terrain(store, exe: str, mods: str | None, map_gen: str | None, work: str, seed, planet: str,
            window: tuple[int, int, int]) -> str | None:
    """The cached terrain preview's store key for this window, rendering it if it's missing."""
    cx, cy, size = window
    settings = ""
    if map_gen and os.path.exists(map_gen):
        with open(map_gen, "rb") as fh:
            settings = hashlib.sha256(fh.read()).hexdigest()[:12]
    key = hashlib.sha256(f"{exe}|{mods}|{settings}|{seed}|{planet}|{cx},{cy}|{size}".encode()).hexdigest()[:20]
    skey = f"maps/terrain/{key}.png"
    if store.get_versioned(skey)[1] is not None:
        return skey
    root = os.path.join(work, "preview")
    os.makedirs(root, exist_ok=True)
    cfg = os.path.join(root, "config.ini")
    with open(cfg, "w") as fh:   # its own write-data: the running server holds a lock on its own
        fh.write(f"[path]\nread-data=__PATH__executable__/../../data\nwrite-data={os.path.join(root, 'write')}\n")
    out = os.path.join(root, f"{key}.png")
    args = [exe, "-c", cfg, "--generate-map-preview", out, "--map-preview-size", str(size),
            "--map-preview-offset", f"{cx},{cy}", "--map-preview-planet", planet or "nauvis"]
    if mods:
        args += ["--mod-directory", mods]
    if map_gen:
        args += ["--map-gen-settings", map_gen]
    if seed is not None:
        args += ["--map-gen-seed", str(seed)]
    try:
        subprocess.run(args, capture_output=True, timeout=180, check=True)
        with open(out, "rb") as fh:
            store.put(skey, fh.read())
        os.remove(out)
        return skey
    except (subprocess.SubprocessError, OSError):
        return None


def capture(store, server, arm: str, session: str, map_gen: str | None, work: str) -> dict:
    """Dump every surface with something on it and make sure its terrain exists. -> the index."""
    surfaces = json.loads(server.lua(LUA_SURFACES) or "[]")
    tick = server.tick()
    out_dir = os.path.join(server.write, "script-output")
    index = {"tick": tick, "time": time.time(), "surfaces": []}
    for s in surfaces:
        if not s.get("entities") and not s.get("enemies"):
            continue
        fname = f"runner-map-{s['index']}.txt"
        server.lua(LUA_DUMP % (s["index"], json.dumps(fname)))
        try:
            with open(os.path.join(out_dir, fname)) as fh:
                rows = _parse(fh.read())
        except OSError:
            continue
        window = _window([r for r in rows if r[7] != "enemy"] or rows)
        tkey = None
        if not s.get("platform") and s.get("planet"):
            tkey = terrain(store, server.exe, server.mods, map_gen, work, s.get("seed"), s["planet"], window)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in s["name"])
        ekey = f"maps/{arm}/{session}/{safe}.json"
        store.put_json(ekey, dict(_pack(rows), surface=s["name"], tick=tick))
        index["surfaces"].append({"name": s["name"], "planet": s.get("planet"), "platform": s.get("platform"),
                                  "seed": s.get("seed"), "entities": ekey, "terrain": tkey,
                                  "window": list(window), "count": len(rows)})
    store.put_json(f"maps/{arm}/{session}/index.json", index)
    return index
