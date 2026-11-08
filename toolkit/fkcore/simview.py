"""fkcore.simview: the planner's save states as a live web page (3.0).

Rory, 2026-11-02: "a live sim view for me ... a tree of all the save states as a directed graph,
with their x,y based on our hor vert metrics, and also maybe a basic visual viewer that shows the
map, we could build off the belt viewer".

- **The lineage** (``sim\\lineage.json``). ``fk sim save NAME`` records NAME's parent (the save
  state the running game came from: the one it started or loaded, or the last one saved since),
  its tick, what the force had made (the production statistics, as explore reads them) and a
  snapshot of the map (``sim\\views\\NAME.json``: every entity of the force and the land tiles).
  ``record`` is best effort: a save never fails on it.
- **The metrics** are explore's (``fkcore.explore``): V, how far up the game a state is (the worth
  of what it made and the research that implies, log2), and H, its Manhattan distance in cell
  levels to the nearest state of the best line (the saves on the way to the highest V) no higher
  than itself. Without the tech tree (``sim\\explore\\techtree.json``) V is log2 of the items
  made, and the page says so.
- **The page** (``python -m fkcore.simview RUN [--port 8791]``): the graph, H across and V up,
  the running game a live node off its head, polled every few seconds; a click shows a state's
  map, drawn as the lane viewer (``tools/lanes_png.py``) colours belts.
- **Backfill** (``--backfill``): saves made before the lineage, measured on a scratch planner of
  their own (``C:\\fk-player\\_simview``, short: Sea Block's deepest mod files pass Windows' 260
  characters from a long path), never the player's. They have no parent.

Stdlib only. The server reads the running planner by RCON, reads only.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import sys
import threading
import time

from . import explore

__all__ = ["record", "Lineage", "graph", "serve", "LUA_VIEW"]

#: the force's entities and the land under them -> script-output/fk5/{tag}.json
LUA_VIEW = r"""
local s = game.surfaces[1]
local SKIP = {['character'] = true, ['construction-robot'] = true, ['logistic-robot'] = true,
  ['combat-robot'] = true, ['character-corpse'] = true}
local es, x0, y0, x1, y1 = {}, 1e9, 1e9, -1e9, -1e9
for _, e in pairs(s.find_entities_filtered{force = 'player'}) do
  if not SKIP[e.type] and #es < 30000 then
    local ghost = e.type == 'entity-ghost'
    local p = ghost and e.ghost_prototype or e.prototype
    local x, y = e.position.x, e.position.y
    es[#es + 1] = {ghost and e.ghost_name or e.name, ghost and e.ghost_type or e.type, x, y,
      e.supports_direction and e.direction or 0, p.tile_width or 1, p.tile_height or 1, ghost}
    if x < x0 then x0 = x end if y < y0 then y0 = y end
    if x > x1 then x1 = x end if y > y1 then y1 = y end
  end
end
if #es == 0 then x0, y0, x1, y1 = -16, -16, 16, 16 end
x0 = math.floor(x0) - 12 y0 = math.floor(y0) - 12 x1 = math.ceil(x1) + 12 y1 = math.ceil(y1) + 12
if x1 - x0 > 600 then local c = (x0 + x1) / 2 x0 = math.floor(c - 300) x1 = x0 + 600 end
if y1 - y0 > 600 then local c = (y0 + y1) / 2 y0 = math.floor(c - 300) y1 = y0 + 600 end
local land = {}
for _, t in pairs(s.find_tiles_filtered{area = {{x0, y0}, {x1, y1}}, collision_mask = 'water_tile', invert = true}) do
  land[#land + 1] = {t.position.x, t.position.y}
end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({tick = game.tick, box = {x0, y0, x1, y1}, e = es, land = land}), false)
rcon.print('1')
"""


def _runs(land):
    """[[x, y], ...] -> {y: [[x0, x1], ...]}: the land as runs along each row."""
    rows = {}
    for x, y in land:
        rows.setdefault(int(y), []).append(int(x))
    out = {}
    for y, xs in rows.items():
        xs.sort()
        runs, a, b = [], xs[0], xs[0]
        for x in xs[1:]:
            if x == b + 1:
                b = x
            else:
                runs.append([a, b])
                a = b = x
        runs.append([a, b])
        out[str(y)] = runs
    return out


def snapshot(sim, tag):
    """The map as the running planner stands: {tick, box, e, land (runs)}."""
    got = sim.lua(LUA_VIEW.replace("{tag}", tag), timeout=30).strip()
    src = os.path.join(sim.write, "script-output", "fk5", f"{tag}.json")
    if got != "1" or not os.path.exists(src):
        raise RuntimeError(f"the map read failed: {got[:200]}")
    with open(src, encoding="utf-8") as fh:
        data = json.load(fh)
    os.remove(src)
    data["land"] = _runs(data.get("land") or [])
    return data


class Lineage:
    """``sim\\lineage.json``: {name: {parent, tick, at, made, backfilled}}."""

    def __init__(self, sim_root):
        self.root = sim_root
        self.path = os.path.join(sim_root, "lineage.json")
        self.views = os.path.join(sim_root, "views")
        self.data = {}
        try:
            with open(self.path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (OSError, ValueError):
            self.data = {}

    def write(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(self.data, fh, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def put_view(self, name, view):
        os.makedirs(self.views, exist_ok=True)
        tmp = os.path.join(self.views, f"{name}.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(view, fh, separators=(",", ":"))
        os.replace(tmp, os.path.join(self.views, f"{name}.json"))

    def view(self, name):
        try:
            with open(os.path.join(self.views, f"{name}.json"), encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None


def record(sim, name, parent, backfilled=False):
    """NAME's lineage row and map, read from the running planner (best effort: -> a note or '')."""
    try:
        lin = Lineage(sim.root)
        old = lin.data.get(name) or {}
        if parent == name:              # saved over the state it came from: keep that one's parent
            parent = old.get("parent")
        made = sim.made()
        lin.data[name] = {"parent": parent, "tick": made["tick"], "made": made["made"],
                          "researched": len(made.get("researched") or ()),
                          "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "backfilled": backfilled}
        lin.put_view(name, snapshot(sim, f"view-{os.getpid()}"))
        lin.write()
        return ""
    except Exception as exc:            # noqa: BLE001 - the save stands without its lineage
        return f" (its lineage not recorded: {exc})"


# -- the metrics ------------------------------------------------------------------------------------
def _tree(sim_root):
    from . import techcost
    path = os.path.join(sim_root, "explore", "techtree.json")
    try:
        return techcost.Tree.load(path) if os.path.exists(path) else None
    except Exception:                   # noqa: BLE001
        return None


_V = {}      # (name, tick, items) -> V: a state's worth doesn't change, and the tree walk is slow


def _v(name, row, lv, tree, base):
    """explore's V (log2 of the worth), not floored at 0 (worth 1): an early state made little,
    and the view tells those apart. Without a tree: log2 of the items made."""
    key = (name, row.get("tick"), len(lv), sum(lv.values()))
    if key in _V:
        return _V[key]
    if tree is not None:
        try:
            w = explore.Worth(tree, base, lv)
            worth = w.made + w.research
            return _keep(name, key, math.log2(worth) if worth > 0 else None)
        except Exception:               # noqa: BLE001 - an item the tree doesn't know
            pass
    total = sum(float(c) for c in (row.get("made") or {}).values())
    return _keep(name, key, math.log2(total) if total > 0 else None)


def _keep(name, key, v):
    if name != "(live)":
        _V[key] = v
    return v


def place(rows, tree, base=2.0):
    """{name: {made, parent}} -> ({name: (V, H)}, best line, exact). The best line: the parents
    up from the highest V. H as explore's ``positions``: the Manhattan distance in cell levels to
    the nearest state of the best line no higher than itself."""
    levels = {n: explore.cell(r.get("made") or {}, base) for n, r in rows.items()}
    v = {n: _v(n, rows[n], lv, tree, base) for n, lv in levels.items()}
    # a state that made nothing has no worth: one below the lowest that made something
    low = min([x for x in v.values() if x is not None] or [1.0]) - 1
    v = {n: low if x is None else x for n, x in v.items()}
    if not v:
        return {}, [], tree is not None
    top = max(v, key=lambda n: (v[n], -int(rows[n].get("tick") or 0)))
    line, seen, n = [], set(), top
    while n in rows and n not in seen:
        seen.add(n)
        line.append(n)
        n = rows[n].get("parent")
    out = {}
    for n in rows:
        near = [p for p in line if v[p] <= v[n] + 1e-9] or line
        out[n] = (v[n], min(explore.distance(levels[n], levels[p]) for p in near))
    return out, line[::-1], tree is not None


def graph(sim, live=True):
    """The page's /api/graph: the states, their places and the running game."""
    lin = Lineage(sim.root)
    rows = {n: dict(r) for n, r in lin.data.items()}
    saves = set(sim.names()) if os.path.isdir(sim.saves) else set()
    st = sim.state()
    run = None
    if live and st is not None:
        try:
            made = sim.made()
            run = {"head": st.get("head") or st.get("save"), "tick": made["tick"],
                   "made": made["made"], "speed": float(sim.lua("rcon.print(game.speed)") or 1),
                   "paused": sim.lua("rcon.print(tostring(game.tick_paused))").strip() == "true"}
            rows["(live)"] = {"parent": run["head"], "tick": run["tick"], "made": run["made"]}
        except Exception:               # noqa: BLE001
            run = {"head": st.get("head") or st.get("save"), "error": "no answer"}
    tree = _tree(sim.root)
    pos, line, exact = place(rows, tree)
    nodes = []
    for n, r in sorted(rows.items(), key=lambda kv: int(kv[1].get("tick") or 0)):
        vh = pos.get(n, (0.0, 0))
        made = r.get("made") or {}
        top = sorted(made.items(), key=lambda kv: -float(kv[1]))[:8]
        nodes.append({"name": n, "parent": r.get("parent"), "tick": int(r.get("tick") or 0),
                      "V": round(vh[0], 3), "H": vh[1], "best": n in line,
                      "on_disk": n in saves or n == "(live)", "backfilled": bool(r.get("backfilled")),
                      "items": len(made), "top": [[k, int(float(c))] for k, c in top],
                      "view": n == "(live)" or os.path.exists(os.path.join(lin.views, f"{n}.json"))})
    missing = sorted(saves - set(rows))
    return {"nodes": nodes, "unmeasured": missing, "exact": exact, "running": st is not None,
            "live": run, "now": time.strftime("%H:%M:%S")}


# -- the build ----------------------------------------------------------------------------------------
_MODULE = re.compile(r"^\s*module\s+(\w+)(<[^>]*>)?\s*((?!size\b)\w+\s+)?(?:size\s+(\d+)x(\d+))?")


def _catalog(game):
    """The pack's catalog with the run's learned machines (``game\\learned.json``)."""
    from . import catalog as catmod
    from . import learned
    with open(catmod.DEFAULT_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    p = os.path.join(game, "learned.json")
    return catmod.Catalog.from_dict(learned.merge_catalog(data, learned.load(p) if os.path.exists(p) else None))


def module_defs(game):
    """{NAME: {file, line, w, h, text}}: every ``module NAME ... {`` block in the run's .fk files."""
    out = {}
    for f in sorted(os.listdir(game)) if os.path.isdir(game) else []:
        if not f.endswith(".fk"):
            continue
        try:
            with open(os.path.join(game, f), encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except OSError:
            continue
        for i, ln in enumerate(lines):
            m = _MODULE.match(ln)
            # a name in several files (a copy kept aside): its own file wins, else the first
            if not m or (m.group(1) in out and f != m.group(1).lower() + ".fk"):
                continue
            depth, j, seen = 0, i, False
            while j < len(lines):
                code = lines[j].split("#", 1)[0]
                depth += code.count("{") - code.count("}")
                seen = seen or "{" in code
                if seen and depth <= 0:
                    break
                j += 1
            out[m.group(1)] = {"file": f, "line": i + 1, "w": int(m.group(4) or 0),
                               "h": int(m.group(5) or 0), "text": "\n".join(lines[i:j + 1])}
    return out


def build_view(game, surface="nauvis", cat=None):
    """The latest fk build (the plots' ``.fkp`` layouts, what the next paste puts down) as a map
    view in map coordinates, with its plots and the frames and ports of their top-level instances."""
    from . import fkp
    sp = os.path.join(game, f"{surface}.fkp")
    if not os.path.exists(sp):
        return None
    cat = cat or _catalog(game)
    defs = module_defs(game)
    es, plots, frames, at = [], [], [], os.path.getmtime(sp)
    for p in fkp.load(sp).plots.values():
        plots.append({"name": p.factory, "box": [p.x, p.y, p.x + p.w, p.y + p.h],
                      "module": p.params.get("module", p.factory)})
        path = os.path.join(game, f"{p.factory}.fkp")
        if not os.path.exists(path):
            continue
        at = max(at, os.path.getmtime(path))
        lay = fkp.load(path)
        boxes = {}
        for e in lay.entities.values():
            try:
                typ, (w, h) = cat.kind(e.name), cat.size(e.name)
            except Exception:           # noqa: BLE001
                typ, w, h = "", 1, 1
            x, y = float(e.x) + p.x, float(e.y) + p.y
            es.append([e.name, typ, x, y, e.dir or 0, w, h, False, e.key])
            top = e.key.split("/", 1)[0]
            if (e.dir or 0) in (4, 12):
                w, h = h, w
            b = boxes.setdefault(top, [x - w / 2, y - h / 2, x + w / 2, y + h / 2])
            b[:] = [min(b[0], x - w / 2), min(b[1], y - h / 2), max(b[2], x + w / 2), max(b[3], y + h / 2)]
        ports = {}
        for k, r in lay.ports.items():
            ports.setdefault(k.rpartition(".")[0], []).append([k.rpartition(".")[2], r.x, r.y, r.dir, r.items])
        for i in lay.insts.values():
            if "/" in i.path:
                continue
            d = defs.get(i.kind)
            x0, y0 = i.x + p.x, i.y + p.y
            if d and d["w"] and i.rot == 0:
                box = [x0, y0, x0 + d["w"], y0 + d["h"]]
            elif i.path in boxes:
                box = [round(v, 1) for v in boxes[i.path]]
            else:
                continue
            frames.append({"name": i.path, "kind": i.kind, "plot": p.factory, "at": [x0, y0], "rot": i.rot,
                           "box": box, "module": bool(d),
                           "params": {k: v for k, v in i.params.items()},
                           "n": sum(1 for e in lay.entities if e.split("/", 1)[0] == i.path),
                           "ports": [[n, x0 + px + .5, y0 + py + .5, pd, it] for n, px, py, pd, it in ports.get(i.path, [])]
                           if i.rot == 0 else []})
    if es:
        xs, ys = [e[2] for e in es], [e[3] for e in es]
        box = [math.floor(min(xs)) - 12, math.floor(min(ys)) - 12, math.ceil(max(xs)) + 12, math.ceil(max(ys)) + 12]
    else:
        box = [-16, -16, 16, 16]
    frames.sort(key=lambda f: (not f["module"], f["name"]))
    return {"tick": None, "built": time.strftime("%m-%d %H:%M", time.localtime(at)), "box": box, "e": es,
            "land": {}, "plots": plots, "frames": frames}


# -- the page ---------------------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sim states</title>
<style>
:root{--bg:#15171b;--panel:#1d2026;--ink:#e6e6e6;--muted:#9aa0aa;--grid:#2c3038;--best:#f0aa6e;
--node:#7aa2f7;--live:#9ece6a;--warn:#e0af68}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:13px system-ui,sans-serif}
header{display:flex;gap:16px;align-items:baseline;padding:10px 16px;border-bottom:1px solid var(--grid)}
header h1{font-size:15px;margin:0}header .m{color:var(--muted)}
main{display:grid;grid-template-columns:minmax(320px,1fr) minmax(320px,1fr);grid-template-rows:calc(100vh - 70px) auto;gap:12px;padding:12px 16px}
@media (max-width:900px){main{grid-template-columns:1fr;grid-template-rows:auto}}
.card{background:var(--panel);border:1px solid var(--grid);border-radius:8px;padding:8px;display:flex;flex-direction:column;min-height:420px}
.wide{grid-column:1/-1;min-height:0}.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.row h2{margin:0;flex:1}
.seg{display:inline-flex;border:1px solid var(--grid);border-radius:6px;overflow:hidden}.seg button{background:none;border:0;color:var(--muted);padding:3px 10px;font:inherit;cursor:pointer}
.seg button.on{background:#2d3340;color:var(--ink)}label.ck{color:var(--muted);font-size:12px;cursor:pointer}
.mods{display:grid;grid-template-columns:minmax(300px,1fr) minmax(300px,1.2fr);gap:12px;align-items:start}.mods>div{min-width:0}@media (max-width:900px){.mods{grid-template-columns:1fr}}
table{border-collapse:collapse;width:100%;font-size:12px}th,td{text-align:left;padding:3px 6px;border-bottom:1px solid var(--grid);white-space:nowrap}
th{color:var(--muted);font-weight:600}tbody tr{cursor:pointer}tbody tr:hover{background:#262a32}tbody tr.sel{background:#2d3340}td.k{color:var(--best)}td.d{color:var(--muted)}
.tw{max-height:420px;overflow:auto}pre{margin:0;background:#15171b;border:1px solid var(--grid);border-radius:6px;padding:8px;font:12px ui-monospace,Consolas,monospace;max-height:420px;overflow:auto;white-space:pre}
.card h2{font-size:13px;margin:0 0 6px;color:var(--muted);font-weight:600}
svg,canvas{flex:1;width:100%;min-height:360px;display:block}
.info{font-size:12px;color:var(--muted);min-height:3.2em;margin-top:6px;white-space:pre-wrap}
.n circle{fill:var(--node);stroke:#0008;stroke-width:1}.n.best circle{fill:var(--best)}
.n.live circle{fill:var(--live)}.n.gone circle{fill:#555}.n.sel circle{stroke:#fff;stroke-width:2.5}
.n text{fill:var(--ink);font-size:11px;paint-order:stroke;stroke:var(--panel);stroke-width:3px}
.e{stroke:#556;stroke-width:1.4;fill:none}.e.best{stroke:var(--best);stroke-width:2.2}.e.live{stroke:var(--live);stroke-dasharray:4 3}
.ax{stroke:var(--grid)}.axt{fill:var(--muted);font-size:10px}
.legend{display:flex;gap:12px;flex-wrap:wrap;font-size:11px;color:var(--muted)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;vertical-align:-1px}
</style></head><body>
<header><h1>Sim states</h1><span class="m" id="status">loading</span></header>
<main>
<div class="card"><h2>Save states: H across (off the best line), V up (how far up the game)</h2>
<div class="legend"><span><i style="background:var(--best)"></i>best line</span><span><i style="background:var(--node)"></i>state</span>
<span><i style="background:var(--live)"></i>running game</span><span><i style="background:#555"></i>save file gone</span></div>
<svg id="g"></svg><div class="info" id="ginfo">Click a state to see its map.</div></div>
<div class="card"><div class="row"><h2 id="mtitle">Map</h2><span class="seg"><button id="mlive" class="on">Live / state</button><button id="mbuild">Latest fk build</button></span>
<label class="ck"><input type="checkbox" id="mframes" checked> module frames</label></div><canvas id="map"></canvas><div class="info" id="minfo">Wheel to zoom, drag to pan.</div></div>
<div class="card wide"><h2 id="dtitle">Plots and modules (the latest fk build)</h2><div class="info" id="plots" style="min-height:0;margin:0 0 6px"></div>
<div class="mods"><div class="tw"><table><thead><tr><th>instance</th><th>module</th><th>at (map)</th><th>size</th><th>entities</th><th>ports</th></tr></thead><tbody id="mtab"></tbody></table></div>
<div><div class="info" id="dhead" style="min-height:0;margin:0 0 6px">Click a module (here or on the map) to see its definition.</div><pre id="dsrc"></pre></div></div></div>
</main>
<script>
var G=null,SEL='(live)',VIEW=null,cam={s:6,x:0,y:0},fit=true,MODE='live',BUILD=null,SELMOD=null,FRAMES=true;
function esc(s){return String(s).replace(/[&<>]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;'}[c]})}
function poll(){fetch('api/graph').then(function(r){return r.json()}).then(function(d){G=d;draw();
 var L=d.live;document.getElementById('status').textContent=(d.running?('running '+(L&&L.head?'from '+L.head:'')+(L&&L.tick!=null?', tick '+L.tick.toLocaleString():'')+(L&&L.paused?', paused':'')+(L&&L.speed?', speed '+L.speed:'')):'planner stopped')
 +' · '+d.nodes.length+' states'+(d.unmeasured.length?' · not measured yet: '+d.unmeasured.join(', '):'')+(d.exact?'':' · V is rough (no tech tree yet)')+' · '+d.now;
 if(SEL==='(live)')loadView()}).catch(function(){document.getElementById('status').textContent='no answer from the viewer'});loadBuild()}
function loadBuild(){fetch('api/build').then(function(r){return r.ok?r.json():null}).then(function(b){var first=!BUILD;BUILD=b;mods();if(MODE==='build'){if(first)fit=true;title()}paint()})}
function title(){var v=MODE==='build'?BUILD:VIEW;document.getElementById('mtitle').textContent=MODE==='build'?('Map: latest fk build'+(v?'  ('+v.built+', '+v.e.length+' entities)':'  (none)')):('Map: '+SEL+(v?'  (tick '+v.tick.toLocaleString()+', '+v.e.length+' entities)':''))}
function mode(m){MODE=m;if(!(m==='build'?VIEW:BUILD))fit=true;document.getElementById('mlive').className=m==='live'?'on':'';document.getElementById('mbuild').className=m==='build'?'on':'';title();paint()}
function mods(){var b=BUILD,t=document.getElementById('mtab');if(!b){t.innerHTML='<tr><td colspan="6" class="d">no build yet (game\\nauvis.fkp)</td></tr>';return}
 document.getElementById('plots').textContent='Plots: '+b.plots.map(function(p){return p.name+' (module '+p.module+') '+p.box[0]+','+p.box[1]+' to '+p.box[2]+','+p.box[3]}).join(' · ')+'   ·   built '+b.built;
 t.innerHTML=b.frames.map(function(f){var w=f.box[2]-f.box[0],h=f.box[3]-f.box[1];return '<tr data-m="'+esc(f.name)+'"'+(f.name===SELMOD?' class="sel"':'')+'><td'+(f.module?' class="k"':'')+'>'+esc(f.name)+'</td><td>'+esc(f.kind)+(f.rot?' (rot '+f.rot+')':'')+'</td><td class="d">'+f.at[0]+', '+f.at[1]+'</td><td class="d">'+w+'x'+h+'</td><td>'+f.n+'</td><td class="d">'+(f.ports.map(function(p){return p[0]}).join(', ')||'')+'</td></tr>'}).join('');
 t.querySelectorAll('tr').forEach(function(r){r.onclick=function(){pick(r.getAttribute('data-m'),true)}})}
function pick(name,go){SELMOD=name;var f=BUILD&&BUILD.frames.filter(function(f){return f.name===name})[0];if(!f)return;mods();
 if(go){var c=document.getElementById('map');cam.x=(f.box[0]+f.box[2])/2;cam.y=(f.box[1]+f.box[3])/2;cam.s=Math.max(4,Math.min(40,.8*Math.min(c.clientWidth/(f.box[2]-f.box[0]+2),c.clientHeight/(f.box[3]-f.box[1]+2))));fit=false}paint();
 var hd=document.getElementById('dhead'),src=document.getElementById('dsrc');hd.textContent=f.name+' = '+f.kind+' at map '+f.at[0]+', '+f.at[1]+(f.rot?' rot '+f.rot:'')+', '+f.n+' entities'+(Object.keys(f.params).length?'  ·  '+Object.keys(f.params).map(function(k){return k+'='+f.params[k]}).join(' '):'')+(f.ports.length?'\nports: '+f.ports.map(function(p){return p[0]+' '+p[4]}).join(' · '):'');src.textContent='';
 if(!f.module){src.textContent='(not a module: a '+f.kind+' placed in the plot)';return}
 fetch('api/def?kind='+encodeURIComponent(f.kind)).then(function(r){return r.ok?r.json():null}).then(function(d){if(SELMOD!==name)return;src.textContent=d?('# '+d.file+':'+d.line+'\n'+d.text):'(no definition found in the run\'s .fk files)'})}
function draw(){var svg=document.getElementById('g'),W=svg.clientWidth,H=svg.clientHeight,P=36;
 var ns=G.nodes;if(!ns.length){svg.innerHTML='<text x="20" y="30" class="axt">no recorded states yet: fk sim save NAME records one</text>';return}
 var hx=Math.max(1,Math.max.apply(null,ns.map(function(n){return n.H}))),vlo=Math.min.apply(null,ns.map(function(n){return n.V})),vhi=Math.max.apply(null,ns.map(function(n){return n.V}));
 if(vhi-vlo<1){vhi=vlo+1}
 var R=Math.min(170,W*.3),X=function(h){return P+(W-P-R)*Math.sqrt(h/hx)},Y=function(v){return H-P-(H-2*P)*(v-vlo)/(vhi-vlo)};
 var by={};ns.forEach(function(n){by[n.name]=n});
 var s='';for(var i=0;i<=4;i++){var v=vlo+(vhi-vlo)*i/4;s+='<line class="ax" x1="'+P+'" x2="'+(W-P)+'" y1="'+Y(v)+'" y2="'+Y(v)+'"/><text class="axt" x="2" y="'+(Y(v)+3)+'">V '+v.toFixed(1)+'</text>'}
 [4,16,64,256,1024].forEach(function(h){if(h<hx*.8&&X(h)-P>40)s+='<line class="ax" x1="'+X(h)+'" x2="'+X(h)+'" y1="'+P+'" y2="'+(H-P)+'"/><text class="axt" x="'+X(h)+'" y="'+(H-8)+'" text-anchor="middle">'+h+'</text>'});
 s+='<text class="axt" x="'+X(hx)+'" y="'+(H-8)+'" text-anchor="middle">H '+hx+'</text><text class="axt" x="'+P+'" y="'+(H-8)+'">H 0</text><text class="axt" x="'+(W-4)+'" y="'+(H-8)+'" text-anchor="end">(square-root scale)</text>';
 // a few states can share a spot: spread them a little
 var at={};ns.forEach(function(n){var k=Math.round(X(n.H)/8)+','+Math.round(Y(n.V)/8);(at[k]=at[k]||[]).push(n);n._x=X(n.H)+(at[k].length-1)*9;n._y=Y(n.V);n._lab=at[k].length===1?k:null});
 var labs={};for(var k in at)labs[k]=at[k].map(function(n){return n.name}).join(', ');
 ns.forEach(function(n){var p=by[n.parent];if(!p)return;var cls='e'+(n.best&&p.best?' best':'')+(n.name==='(live)'?' live':'');
  var mx=(p._x+n._x)/2;s+='<path class="'+cls+'" marker-end="url(#a)" d="M'+p._x+','+p._y+' C'+mx+','+p._y+' '+mx+','+n._y+' '+n._x+','+n._y+'"/>'});
 ns.forEach(function(n){var cls='n'+(n.best?' best':'')+(n.name==='(live)'?' live':'')+(!n.on_disk?' gone':'')+(n.name===SEL?' sel':'');
  s+='<g class="'+cls+'" data-n="'+esc(n.name)+'" style="cursor:pointer"><circle cx="'+n._x+'" cy="'+n._y+'" r="'+(n.name==='(live)'?7:6)+'"/>'+(n._lab?'<text x="'+(n._x+9+(at[n._lab].length-1)*9)+'" y="'+(n._y+4)+'">'+esc(labs[n._lab])+'</text>':'')+'<title>'+esc(n.name)+'</title></g>'});
 svg.innerHTML='<defs><marker id="a" viewBox="0 0 10 10" refX="16" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0L10,5L0,10z" fill="#667"/></marker></defs>'+s;
 svg.querySelectorAll('.n').forEach(function(g){g.onclick=function(){SEL=g.getAttribute('data-n');fit=true;if(MODE!=='live')mode('live');draw();info();loadView()}});info()}
function info(){var n=(G.nodes.filter(function(n){return n.name===SEL})[0]);if(!n)return;
 document.getElementById('ginfo').textContent=n.name+(n.parent?'  <- '+n.parent:'  (no parent)')+'   tick '+n.tick.toLocaleString()+' ('+(n.tick/216000).toFixed(2)+' h)   V '+n.V.toFixed(2)+'  H '+n.H+'   '+n.items+' items made'+(n.backfilled?'   (measured afterwards)':'')+'\n'+n.top.map(function(t){return t[0]+' '+t[1].toLocaleString()}).join(' · ')}
function loadView(){var n=SEL;fetch('api/view?name='+encodeURIComponent(n)).then(function(r){return r.ok?r.json():null}).then(function(v){if(n!==SEL)return;VIEW=v;
 if(MODE==='live'){title();if(!v){document.getElementById('minfo').textContent='no map for '+n;}}paint()})}
var COL={'transport-belt':'#c8a03a','underground-belt':'#a8822a','splitter':'#d08a30','inserter':'#9aa0b0','pipe':'#5b7fa8','pipe-to-ground':'#4a6a90',
 'assembling-machine':'#6d7f5a','furnace':'#a0603a','electric-pole':'#8a7050','roboport':'#b05a8a','container':'#8d6a3d','logistic-container':'#c07040',
 'mining-drill':'#7a6a4a','offshore-pump':'#3a8aa0','boiler':'#a05030','generator':'#708090','solar-panel':'#304a70','accumulator':'#506070','lab':'#4a9a8a',
 'storage-tank':'#4a6a7a','pump':'#3a7a90','reactor':'#9a6a40','burner-generator':'#806040','electric-energy-interface':'#555'};
function paint(){var c=document.getElementById('map'),W=c.clientWidth,H=c.clientHeight,dpr=window.devicePixelRatio||1;c.width=W*dpr;c.height=H*dpr;var g=c.getContext('2d');g.setTransform(dpr,0,0,dpr,0,0);
 g.fillStyle='#1b3a52';g.fillRect(0,0,W,H);var v=MODE==='build'?BUILD:VIEW;if(!v)return;var b=v.box;
 if(fit){cam.s=Math.max(1,Math.min(W/(b[2]-b[0]),H/(b[3]-b[1])));cam.x=(b[0]+b[2])/2;cam.y=(b[1]+b[3])/2;fit=false}
 var S=cam.s,ox=W/2-cam.x*S,oy=H/2-cam.y*S;g.fillStyle='#3b3a30';
 for(var y in v.land){v.land[y].forEach(function(r){g.fillRect(ox+r[0]*S,oy+(+y)*S,(r[1]-r[0]+1)*S,S)})}
 if(S>=10){g.strokeStyle='rgba(255,255,255,.04)';g.beginPath();for(var x=Math.floor(b[0]);x<=b[2];x++){g.moveTo(ox+x*S,oy+b[1]*S);g.lineTo(ox+x*S,oy+b[3]*S)}
  for(var y2=Math.floor(b[1]);y2<=b[3];y2++){g.moveTo(ox+b[0]*S,oy+y2*S);g.lineTo(ox+b[2]*S,oy+y2*S)}g.stroke()}
 var D=[[0,-1],[.92,-.38],[.71,-.71],[.38,-.92],[1,0],[.92,.38],[.71,.71],[.38,.92],[0,1],[-.38,.92],[-.71,.71],[-.92,.38],[-1,0],[-.92,-.38],[-.71,-.71],[-.38,-.92]];
 v.e.forEach(function(e){var name=e[0],type=e[1],x=e[2],y=e[3],dir=e[4]||0,w=e[5]||1,h=e[6]||1,ghost=e[7];
  if(dir===4||dir===12){var t=w;w=h;h=t}
  var col=COL[type]||'#777',px=ox+(x-w/2)*S,py=oy+(y-h/2)*S;g.globalAlpha=ghost?.35:1;
  if(type==='transport-belt'||type==='underground-belt'||type==='splitter'||type==='loader'){g.fillStyle=col;g.fillRect(px+.08*S,py+.08*S,w*S-.16*S,h*S-.16*S);
   var d=D[dir%16]||D[0];if(S>=5){g.strokeStyle='#2a2210';g.lineWidth=Math.max(1,S/8);g.beginPath();g.moveTo(ox+x*S-d[0]*S*.3,oy+y*S-d[1]*S*.3);g.lineTo(ox+x*S+d[0]*S*.3,oy+y*S+d[1]*S*.3);
    g.moveTo(ox+x*S+d[0]*S*.3,oy+y*S+d[1]*S*.3);g.lineTo(ox+x*S+(d[0]*.05-d[1]*.18)*S,oy+y*S+(d[1]*.05+d[0]*.18)*S);g.moveTo(ox+x*S+d[0]*S*.3,oy+y*S+d[1]*S*.3);g.lineTo(ox+x*S+(d[0]*.05+d[1]*.18)*S,oy+y*S+(d[1]*.05-d[0]*.18)*S);g.stroke()}
   if(type==='underground-belt'&&S>=8){g.fillStyle='#1118';g.fillRect(px+S*.3,py+S*.3,S*.4,S*.4)}}
  else if(type==='inserter'){g.fillStyle=col;var q=S*.5;g.fillRect(ox+x*S-q/2,oy+y*S-q/2,q,q);var d2=D[dir%16]||D[0];g.strokeStyle=col;g.lineWidth=Math.max(1,S/10);g.beginPath();g.moveTo(ox+x*S-d2[0]*S*.45,oy+y*S-d2[1]*S*.45);g.lineTo(ox+x*S+d2[0]*S*.45,oy+y*S+d2[1]*S*.45);g.stroke()}
  else if(type==='pipe'||type==='pipe-to-ground'){g.fillStyle=col;g.fillRect(px+S*.3,py+S*.3,S*.4,S*.4)}
  else{g.fillStyle=col;g.fillRect(px+.06*S,py+.06*S,w*S-.12*S,h*S-.12*S);if(S*w>=40){g.fillStyle='#fffc';g.font=Math.min(12,S*.45)+'px system-ui';g.fillText(name.replace(/-/g,' ').slice(0,Math.floor(w*S/6)),px+3,py+12)}}});
 g.globalAlpha=1;
 if(FRAMES&&BUILD){g.font='11px system-ui';BUILD.frames.forEach(function(f){var on=f.name===SELMOD;if(!f.module&&!on)return;var x=ox+f.box[0]*S,y=oy+f.box[1]*S,w=(f.box[2]-f.box[0])*S,h=(f.box[3]-f.box[1])*S;
  g.strokeStyle=on?'#ffffff':'rgba(240,170,110,.75)';g.lineWidth=on?2.5:1.3;g.setLineDash(on?[]:[5,3]);g.strokeRect(x,y,w,h);g.setLineDash([]);
  if(w>24){g.fillStyle=on?'#fff':'#f0aa6e';g.fillText(f.name+(f.kind!==f.name?' : '+f.kind:''),x+3,y-4)}
  f.ports.forEach(function(p){g.fillStyle='#9ece6a';g.beginPath();g.arc(ox+p[1]*S,oy+p[2]*S,Math.max(2.5,S*.3),0,7);g.fill();if(on&&S>=6){g.fillStyle='#9ece6a';g.fillText(p[0],ox+p[1]*S+S*.4,oy+p[2]*S+4)}})})}
 document.getElementById('minfo').textContent='Wheel to zoom, drag to pan, click a frame to see its module. '+(MODE==='build'?'The build is what fk last compiled (game\\world.fkp): the next paste puts it down.':v.e.filter(function(e){return e[7]}).length+' ghosts drawn faint.')}
(function(){var c=document.getElementById('map'),drag=null;c.onwheel=function(ev){ev.preventDefault();var k=ev.deltaY<0?1.25:.8,r=c.getBoundingClientRect(),W=c.clientWidth,H=c.clientHeight;
 var wx=cam.x+(ev.clientX-r.left-W/2)/cam.s,wy=cam.y+(ev.clientY-r.top-H/2)/cam.s;cam.s=Math.max(.5,Math.min(64,cam.s*k));cam.x=wx-(ev.clientX-r.left-W/2)/cam.s;cam.y=wy-(ev.clientY-r.top-H/2)/cam.s;paint()};
 var moved=0;c.onmousedown=function(ev){drag=[ev.clientX,ev.clientY];moved=0};
 window.onmouseup=function(ev){if(drag&&moved<4&&ev.target===c&&BUILD){var r=c.getBoundingClientRect(),wx=cam.x+(ev.clientX-r.left-c.clientWidth/2)/cam.s,wy=cam.y+(ev.clientY-r.top-c.clientHeight/2)/cam.s;
  var hit=BUILD.frames.filter(function(f){return wx>=f.box[0]&&wx<=f.box[2]&&wy>=f.box[1]&&wy<=f.box[3]}).sort(function(a,b){return (a.box[2]-a.box[0])*(a.box[3]-a.box[1])-(b.box[2]-b.box[0])*(b.box[3]-b.box[1])})[0];if(hit)pick(hit.name,false)}drag=null};
 window.onmousemove=function(ev){if(!drag)return;moved+=Math.abs(ev.clientX-drag[0])+Math.abs(ev.clientY-drag[1]);cam.x-=(ev.clientX-drag[0])/cam.s;cam.y-=(ev.clientY-drag[1])/cam.s;drag=[ev.clientX,ev.clientY];paint()}})();
document.getElementById('mlive').onclick=function(){mode('live')};document.getElementById('mbuild').onclick=function(){mode('build')};
document.getElementById('mframes').onchange=function(ev){FRAMES=ev.target.checked;paint()};
window.onresize=function(){if(G)draw();paint()};poll();setInterval(poll,4000);
</script></body></html>"""


def serve(run, port=8791, host="127.0.0.1"):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse
    from . import sim as simmod
    s = simmod.Sim(run)
    game = os.path.join(os.path.abspath(run), "game")
    lock = threading.Lock()                 # one RCON read at a time
    cache = {}

    def cached(key, ttl, fn):
        now = time.time()
        if key in cache and now - cache[key][0] < ttl:
            return cache[key][1]
        with lock:
            got = fn()
        cache[key] = (time.time(), got)
        return got

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, kind="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", kind + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            u = urlparse(self.path)
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, PAGE, "text/html")
                if u.path == "/api/graph":
                    return self._send(200, json.dumps(cached("graph", 3, lambda: graph(s))))
                if u.path == "/api/view":
                    name = (parse_qs(u.query).get("name") or [""])[0]
                    if name == "(live)":
                        if s.state() is None:
                            return self._send(404, "{}")
                        v = cached("live-view", 5, lambda: snapshot(s, "view-live"))
                    else:
                        v = Lineage(s.root).view(name)
                    return self._send(200 if v else 404, json.dumps(v or {}))
                if u.path == "/api/build":
                    v = cached("build", 5, lambda: build_view(game))
                    return self._send(200 if v else 404, json.dumps(v or {}))
                if u.path == "/api/def":
                    kind = (parse_qs(u.query).get("kind") or [""])[0]
                    d = cached("defs", 5, lambda: module_defs(game)).get(kind)
                    return self._send(200 if d else 404, json.dumps(d or {}))
                return self._send(404, "{}")
            except Exception as exc:        # noqa: BLE001
                return self._send(500, json.dumps({"error": str(exc)}))

    class Server(ThreadingHTTPServer):
        # on Windows SO_REUSEADDR lets a second server take a port another already holds
        # (8765 was a device service's): a taken port is an error here
        allow_reuse_address = False

    srv = Server((host, port), H)
    print(f"simview: http://{host}:{port}/ for {run}", flush=True)
    srv.serve_forever()


# -- backfill ---------------------------------------------------------------------------------------
def backfill(run, scratch=r"C:\fk-player\_simview", names=None):
    """The run's saves with no lineage, each measured on a scratch planner of its own (the tech
    tree too, once): their rows and maps go into the run's lineage, with no parent."""
    from . import sim as simmod
    from . import explore as ex
    real = simmod.Sim(run)
    lin = Lineage(real.root)
    todo = [n for n in (names or real.names()) if n not in lin.data]
    if not todo and os.path.exists(os.path.join(real.root, "explore", "techtree.json")):
        return ["simview: nothing to backfill"]
    shutil.rmtree(scratch, ignore_errors=True)
    t = simmod.Sim(scratch)
    os.makedirs(t.saves)
    for f in ("live-mods.json", "mod-settings.dat"):
        if os.path.exists(os.path.join(real.root, f)):
            shutil.copy(os.path.join(real.root, f), os.path.join(t.root, f))
    out = []
    try:
        for i, n in enumerate(todo or real.names()[:1]):
            shutil.copy(real.save_path(n), t.save_path(n))
            t.start(n)
            try:
                if not os.path.exists(os.path.join(real.root, "explore", "techtree.json")):
                    t.tech_tree(ex.Archive(real.root))
                    out.append("simview: the tech tree read")
                if n in todo:
                    note = record(t, n, None, backfilled=True)
                    if not note:
                        moved = Lineage(t.root)
                        lin = Lineage(real.root)
                        lin.data[n] = moved.data[n]
                        lin.put_view(n, moved.view(n))
                        lin.write()
                    out.append(f"simview: {n} measured{note}")
            finally:
                t.stop()
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return out


def main(argv):
    import argparse
    p = argparse.ArgumentParser(prog="python -m fkcore.simview")
    p.add_argument("run")
    p.add_argument("--port", type=int, default=8791)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--backfill", action="store_true")
    a = p.parse_args(argv)
    if a.backfill:
        print("\n".join(backfill(a.run)))
        return 0
    serve(a.run, a.port, a.host)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
