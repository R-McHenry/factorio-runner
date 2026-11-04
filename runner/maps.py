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

#: one surface's entities, written to script-output (too big for one RCON reply on a large base),
#: one per line: type|name|x|y|w|h|ghost|force|recipe|status|r1|r2|decon
#: recipe: what a crafting machine makes; status: its defines.entity_status name; r1/r2: a
#: roboport's logistic and construction radius, or a pole's supply-area distance (r1)
LUA_DUMP = """
local s = game.surfaces[%s]
local o = {}
local ST = {}
for k, v in pairs(defines.entity_status) do ST[v] = k end
local function add(e, ghost)
  local b = e.bounding_box
  local w, h = b.right_bottom.x - b.left_top.x, b.right_bottom.y - b.left_top.y
  local t = ghost and e.ghost_type or e.type
  local n = ghost and e.ghost_name or e.name
  local recipe, status, r1, r2 = '', '', '', ''
  if not ghost then
    local ok, r = pcall(function() return e.get_recipe() end)
    if ok and r then recipe = r.name end
    if recipe == '' and t == 'furnace' then
      local ok2, pr = pcall(function() return e.previous_recipe end)
      if ok2 and pr then
        local nm = pr.name
        if type(nm) == 'table' then nm = nm.name end
        if type(nm) == 'string' then recipe = nm end
      end
    end
    local ok3, st = pcall(function() return e.status end)
    if ok3 and st then status = ST[st] or '' end
    if t == 'roboport' and e.logistic_cell then
      r1 = string.format('%%.1f', e.logistic_cell.logistic_radius)
      r2 = string.format('%%.1f', e.logistic_cell.construction_radius)
    elseif t == 'electric-pole' then
      local ok4, d = pcall(function() return e.prototype.get_supply_area_distance(e.quality) end)
      if ok4 and d then r1 = string.format('%%.1f', d) end
    end
  end
  local decon = (not ghost) and e.to_be_deconstructed() and 1 or 0
  o[#o + 1] = string.format('%%s|%%s|%%.1f|%%.1f|%%.1f|%%.1f|%%d|%%s|%%s|%%s|%%s|%%s|%%d', t, n, e.position.x, e.position.y, w, h,
    ghost and 1 or 0, e.force.name, recipe, status, r1, r2, decon)
end
for _, e in pairs(s.find_entities_filtered{force = 'player'}) do
  if e.type == 'entity-ghost' then add(e, true) elseif e.type ~= 'character' then add(e, false) end
end
for _, e in pairs(s.find_entities_filtered{force = 'enemy', type = {'unit-spawner', 'turret'}}) do add(e, false) end
local units = s.find_entities_filtered{force = 'enemy', type = 'unit', limit = 3000}
for _, e in pairs(units) do add(e, false) end
helpers.write_file(%s, table.concat(o, '\\n'), false)
rcon.print(#o)
"""

#: pollution in each generated chunk of a window: [[chunk x, chunk y, amount], ...] (nonzero only)
LUA_POLLUTION = """
local s = game.surfaces[%s]
local cx, cy, size = %s, %s, %s
local x0, y0 = math.floor((cx - size / 2) / 32), math.floor((cy - size / 2) / 32)
local x1, y1 = math.floor((cx + size / 2) / 32), math.floor((cy + size / 2) / 32)
local o = {}
for x = x0, x1 do
  for y = y0, y1 do
    if s.is_chunk_generated({x, y}) then
      local p = s.get_pollution({x * 32 + 16, y * 32 + 16})
      if p > 0.01 then o[#o + 1] = {x, y, math.floor(p * 10 + 0.5) / 10} end
    end
  end
end
rcon.print(helpers.table_to_json(o))
"""

#: what the game's own map draws with, from data.raw (`--dump-data`)
PALETTE_KEYS = ("default_friendly_color", "default_enemy_color", "default_friendly_color_by_type",
                "default_color_by_type", "entity_ghost_color", "electric_power_pole_color",
                "chart_construction_robot_color", "chart_logistic_robot_color", "rail_color")


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
    """-> [(type, name, x, y, w, h, ghost, force, recipe, status, r1, r2, decon)]; older lines too."""
    rows = []
    for line in text.splitlines():
        p = line.split("|")
        if len(p) == 8:
            p += ["", "", "", ""]
        if len(p) == 12:
            p.append("0")
        if len(p) != 13:
            continue
        try:
            r1 = float(p[10]) if p[10] else 0.0
            r2 = float(p[11]) if p[11] else 0.0
            rows.append((p[0], p[1], float(p[2]), float(p[3]), float(p[4]), float(p[5]), p[6] == "1", p[7],
                         p[8], p[9], r1, r2, p[12] == "1"))
        except ValueError:
            continue
    return rows


STRIDE = 8   # per entity in "types": x2, y2, w2, h2 (half tiles), name, recipe, status (-1: none), decon


def _pack(rows: list) -> dict:
    """Compact entity lists for the page: names, recipes and statuses indexed into `names`,
    coordinates in half tiles. Roboport and pole coverage separately, in metres."""
    names, idx = [], {}

    def ix(v):
        if not v:
            return -1
        if v not in idx:
            idx[v] = len(names)
            names.append(v)
        return idx[v]

    out, cover = {}, {"roboport": [], "pole": []}
    for t, n, x, y, w, h, ghost, force, recipe, status, r1, r2, decon in rows:
        key = "enemy:" + t if force == "enemy" else ("ghost:" if ghost else "") + t
        out.setdefault(key, []).extend([round(x * 2), round(y * 2), max(1, round(w * 2)), max(1, round(h * 2)),
                                        ix(n), ix(recipe), ix(status), 1 if decon else 0])
        if not ghost and force != "enemy":
            if t == "roboport" and (r1 or r2):
                cover["roboport"].append([x, y, r1, r2])
            elif t == "electric-pole" and r1:
                cover["pole"].append([x, y, r1])
    return {"names": names, "types": out, "stride": STRIDE, "cover": cover}


def _rgb(c) -> list | None:
    """A data.raw colour ({r,g,b}, [r,g,b] in 0-1 or 0-255) -> [r, g, b] in 0-255."""
    if isinstance(c, dict):
        c = [c.get("r", 0), c.get("g", 0), c.get("b", 0)]
    if not isinstance(c, (list, tuple)) or len(c) < 3:
        return None
    c = [float(v) for v in c[:3]]
    if max(c) <= 1.0:
        c = [v * 255 for v in c]
    return [int(round(v)) for v in c]


def palette_from_dump(raw: dict) -> dict:
    """The game map's colours from data.raw: chart constants, colours by entity type, and each
    entity prototype's own friendly_map_color / map_color."""
    chart = (raw.get("utility-constants", {}).get("default", {}) or {}).get("chart", {})
    out = {"by_type": {}, "by_name": {}}
    for k in PALETTE_KEYS:
        v = chart.get(k)
        if isinstance(v, dict) and k.endswith("_by_type"):
            out["by_type"].update({t: _rgb(c) for t, c in v.items() if _rgb(c)})
        elif _rgb(v):
            out[k] = _rgb(v)
    for t, protos in raw.items():
        if not isinstance(protos, dict):
            continue
        for n, p in protos.items():
            if isinstance(p, dict) and ("friendly_map_color" in p or "map_color" in p) and t != "tile":
                c = _rgb(p.get("friendly_map_color") or p.get("map_color"))
                if c:
                    out["by_name"][n] = c
    return out


def palette(store, exe: str, mods: str | None, work: str) -> str | None:
    """The store key of the game map's palette for this binary and mod set, dumping it if missing."""
    mh = ""
    if mods and os.path.isdir(mods):
        h = hashlib.sha256()
        for f in sorted(os.listdir(mods)):
            h.update(f.encode())
        mh = h.hexdigest()[:12]
    key = f"maps/palette/{hashlib.sha256(f'{exe}|{mh}'.encode()).hexdigest()[:20]}.json"
    if store.get_versioned(key)[1] is not None:
        return key
    root = os.path.join(work, "dump")
    os.makedirs(root, exist_ok=True)
    cfg = os.path.join(root, "config.ini")
    with open(cfg, "w") as fh:
        fh.write(f"[path]\nread-data=__PATH__executable__/../../data\nwrite-data={os.path.join(root, 'write')}\n")
    args = [exe, "-c", cfg, "--dump-data"] + (["--mod-directory", mods] if mods else [])
    try:
        subprocess.run(args, capture_output=True, timeout=300, check=True)
        path = os.path.join(root, "write", "script-output", "data-raw-dump.json")
        with open(path) as fh:
            pal = palette_from_dump(json.load(fh))
        os.remove(path)
        store.put_json(key, pal)
        return key
    except (subprocess.SubprocessError, OSError, ValueError):
        return None


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
    index = {"tick": tick, "time": time.time(), "surfaces": [],
             "palette": palette(store, server.exe, server.mods, work)}
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
        window = _window([r for r in rows if r[7] != "enemy" and not r[0].endswith("-robot")] or rows)
        try:
            pollution = json.loads(server.lua(LUA_POLLUTION % (s["index"], window[0], window[1], window[2])) or "[]")
        except (ValueError, Exception):
            pollution = []
        tkey = None
        if not s.get("platform") and s.get("planet"):
            tkey = terrain(store, server.exe, server.mods, map_gen, work, s.get("seed"), s["planet"], window)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in s["name"])
        ekey = f"maps/{arm}/{session}/{safe}.json"
        store.put_json(ekey, dict(_pack(rows), surface=s["name"], tick=tick, pollution=pollution))
        index["surfaces"].append({"name": s["name"], "planet": s.get("planet"), "platform": s.get("platform"),
                                  "seed": s.get("seed"), "entities": ekey, "terrain": tkey,
                                  "window": list(window), "count": len(rows)})
    store.put_json(f"maps/{arm}/{session}/index.json", index)
    return index
