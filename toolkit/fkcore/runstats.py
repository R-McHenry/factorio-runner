"""fkcore.runstats: a run's history, sampled every 5 game minutes (2.1, Rory 2026-10-31).

Every run records its own history from the first minute, whatever the player does. A sample is
the game's cumulative counts (``SAMPLE_LUA``, the same Lua the fk-play scenario's recorder runs):

- **items and fluids** made and used, from the force's production statistics on the surface;
- **power** made and used per entity, from each electric network's statistics (joules), summed
  over the networks; the entity's prototype type comes along, so power made reads per generator
  class (steam engines, turbines, solar panels, accumulators) and power used per machine type;
- **research**: the technology in progress, its progress and its cost, and how many are done;
- **machine states**: how many machines of each kind, and of each recipe, stand in each state
  (working, short of input, output full, no power, ...).

Each sample stores the change since the one before (``delta``), so a late or missed sample loses
nothing: that stretch is one long window. A count that goes down (a save loaded again, another
map) is a reset, and shows as a gap. The first sample of a run counts from the map's start.

**Where samples come from.** A map made from the fk-play scenario since 2.1 has the recorder
(``scenario/fk-play/fk_stats.lua``): an ``on_nth_tick`` handler that appends a line to
``script-output/fk-stats/samples.jsonl`` every 5 game minutes, with no RCON and nothing on the
laptop running. A map made before keeps the script it was made with (a scenario's script lives in
the save), and nothing may be registered in it over RCON (it made saves unjoinable on 2026-10-18),
so ``sample`` reads the same counts for it with a read-only RCON call. ``sample`` takes the
recorder's new lines; when the recorder has written nothing for this map lately, it makes the
RCON read itself, at most once per 5 game minutes (``--now``: at once).

**Where they go.** ``<game>/out/stats/``: ``items.jsonl``, ``fluids.jsonl``, ``power.jsonl``,
``research.jsonl`` and ``machines.jsonl`` (one line per sample), ``state.json`` (the last
cumulative sample and the recorder file's offset) and ``index.html`` (``page``). ``sample`` also
runs the capital read (``fkcore.capital``) every 10 game minutes, into ``out/capital/``'s
``history.jsonl`` as ``fk capital`` does.

``history_lines`` is ``fk stats --history``: the research first, then the latest window and the
trend (capital and reinvestment, the top items made and used, the power balance, the machines).
``page`` is a static HTML page with inline SVG charts of every series over game time. Both are
pure; ``sample`` uses the game link. Stdlib only.
"""
from __future__ import annotations

import html
import json
import os

from . import link

__all__ = ["SAMPLE_LUA", "lua_sample", "lua_tick", "EVERY", "CAPITAL_EVERY", "RECORDER",
           "SERIES", "norm", "delta", "recorder", "Store", "ingest", "sample", "windows",
           "history_lines", "page", "write_page"]

#: ticks between two samples (5 game minutes), and between two capital reads (10)
EVERY = 5 * 60 * 60
CAPITAL_EVERY = 10 * 60 * 60
#: ticks a sample may come late and still count as on time
SLACK = 30 * 60
#: the recorder's file in script-output (scenario/fk-play/fk_stats.lua)
RECORDER = "fk-stats/samples.jsonl"
SERIES = ("items", "fluids", "power", "research", "machines")

# fk_stats sample: the same text as scenario/fk-play/fk_stats.lua's, between its markers
# (tests/test_runstats.py checks they match)
SAMPLE_LUA = r'''local function fk_stats_sample(f, s)
  local out = {tick = game.tick, seed = s.map_gen_settings.seed, force = f.name, surface = s.name}
  local types = {}
  local function kind(n)
    if types[n] == nil then
      local p = prototypes.entity[n]
      types[n] = p and p.type or false
    end
  end
  local items = f.get_item_production_statistics(s)
  out.items = {made = items.input_counts, used = items.output_counts}
  local fluids = f.get_fluid_production_statistics(s)
  out.fluids = {made = fluids.input_counts, used = fluids.output_counts}
  local power, seen, at, nets = {}, {}, {}, 0
  for _, p in pairs(s.find_entities_filtered{type = "electric-pole", force = f}) do
    local id = p.electric_network_id
    if id and not seen[id] then
      seen[id] = true
      nets = nets + 1
      local st = p.electric_network_statistics
      -- a network's input is what its machines used, its output what was made, in joules
      power[tostring(id)] = {made = st.output_counts, used = st.input_counts}
      for n in pairs(st.input_counts) do kind(n) end
      for n in pairs(st.output_counts) do kind(n) end
      at[id] = {}
    end
    if id then
      -- 2.1: where its poles stand (doubled, so whole numbers), to tell power domains apart
      local a = at[id]
      a[#a + 1] = math.floor(p.position.x * 2 + 0.5)
      a[#a + 1] = math.floor(p.position.y * 2 + 0.5)
    end
  end
  if nets > 1 then
    for id, a in pairs(at) do power[tostring(id)].at = a end
  end
  out.power = power
  local done = 0
  for _, t in pairs(f.technologies) do
    if t.researched then done = done + 1 end
  end
  local res = f.current_research
  out.research = {researched = done, queue = #(f.research_queue or {})}
  if res then
    local ing = {}
    for _, i in pairs(res.research_unit_ingredients) do ing[#ing + 1] = {i.name, i.amount} end
    out.research.name = res.name
    out.research.level = res.level
    out.research.progress = f.research_progress
    out.research.units = res.research_unit_count
    out.research.ingredients = ing
  end
  local names = {}
  for k, v in pairs(defines.entity_status) do names[v] = k end
  local machines, recipes = {}, {}
  for _, e in pairs(s.find_entities_filtered{type = {"assembling-machine", "furnace",
      "mining-drill", "lab", "rocket-silo"}, force = f}) do
    local st = names[e.status] or "none"
    local m = machines[e.name] or {}
    m[st] = (m[st] or 0) + 1
    machines[e.name] = m
    kind(e.name)
    if e.type ~= "mining-drill" and e.type ~= "lab" then
      local r = e.get_recipe()
      local rn = r and r.name
      if not rn and e.type == "furnace" then
        local ok, pr = pcall(function() return e.previous_recipe end)
        if ok and pr then rn = type(pr.name) == "string" and pr.name or pr.name.name end
      end
      rn = rn or "(no recipe)"
      local q = recipes[rn] or {}
      q[st] = (q[st] or 0) + 1
      recipes[rn] = q
    end
  end
  out.machines, out.recipes = machines, recipes
  local t = {}
  for n, v in pairs(types) do if v then t[n] = v end end
  out.types = t
  return out
end
'''


def _q(s):
    return json.dumps(str(s))


def lua_sample(force, surface, tag):
    """The RCON read of one sample: ``SAMPLE_LUA`` on the force and surface, into script-output
    (a sample is too big for one RCON reply). It only reads."""
    return SAMPLE_LUA + f"""
local f = game.forces[{_q(force)}]
local s = game.get_surface({_q(surface)})
if not f or not s then rcon.print('no force or surface') return end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json(fk_stats_sample(f, s)), false)
rcon.print(1)
"""


def lua_tick(surface):
    return (f"local s = game.get_surface({_q(surface)}) "
            "rcon.print(game.tick .. ' ' .. (s and s.map_gen_settings.seed or 0))")


def _dict(x):
    """Lua's empty table comes back as a JSON list."""
    return x if isinstance(x, dict) else {}


def _num(v):
    v = float(v)
    return int(round(v)) if abs(v) >= 100 or v == int(v) else round(v, 3)


def norm(raw, src):
    """A raw sample (the recorder's line or the RCON read) as plain dicts, with where it came
    from ("recorder" or "rcon")."""
    def pair(x):
        x = _dict(x)
        return {"made": _dict(x.get("made")), "used": _dict(x.get("used"))}

    def net(x):
        out = pair(x)
        at = _dict(x).get("at")
        if isinstance(at, list) and at:
            out["at"] = at                 # 2.1: its poles, doubled (x, y, x, y ...)
        return out
    res = _dict(raw.get("research"))
    ing = res.get("ingredients") or []
    return {"tick": int(raw.get("tick") or 0), "seed": raw.get("seed"), "src": src,
            "items": pair(raw.get("items")), "fluids": pair(raw.get("fluids")),
            "power": {str(k): net(v) for k, v in _dict(raw.get("power")).items()},
            "research": {**res, "ingredients": [list(i) for i in ing if isinstance(i, list)]},
            "machines": {k: _dict(v) for k, v in _dict(raw.get("machines")).items()},
            "recipes": {k: _dict(v) for k, v in _dict(raw.get("recipes")).items()},
            "types": _dict(raw.get("types"))}


def _sub(now, before):
    """The change of each count since ``before`` (only the ones that grew), or None when a count
    went down or vanished: the counts were reset."""
    out = {}
    for k, v in before.items():
        if k not in now and float(v) > 0.5:
            return None
    for k, v in now.items():
        d = float(v) - float(before.get(k, 0))
        if d < -0.5:
            return None
        if d > 1e-9:
            out[k] = _num(d)
    return out


def _add(into, d):
    for k, v in d.items():
        into[k] = _num(into.get(k, 0) + v)


#: where the build writes the power domains' poles (2.1), in the game folder
DOMAINS_FILE = os.path.join("out", "power-domains.json")
#: a network none of whose poles is a domain's
REST = "rest"


def load_domains(game):
    """{(2x, 2y): domain} from ``<game>/out/power-domains.json`` (the build's), or None."""
    try:
        with open(os.path.join(game, DOMAINS_FILE), encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError):
        return None
    out = {}
    for name, poles in _dict(got.get("domains")).items():
        for xy in poles or ():
            if isinstance(xy, list) and len(xy) == 2:
                out[(int(round(float(xy[0]) * 2)), int(round(float(xy[1]) * 2)))] = name
    return out or None


def domain_of(at, domains):
    """The domain most of a network's poles (``at``: x2, y2, ...) belong to, else ``REST``."""
    count = {}
    for i in range(0, len(at or ()) - 1, 2):
        d = domains.get((int(at[i]), int(at[i + 1])))
        if d is not None:
            count[d] = count.get(d, 0) + 1
    if not count:
        return REST
    return sorted(count.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def delta(prev, cur, domains=None):
    """One sample against the one before -> [(series, record)]. ``prev`` None: the counts since
    the map began. A reset (another map, a save loaded again, a count that went down) gives each
    series a gap record ``{"tick", "gap": why}`` and the machines' states as they are. 2.1:
    ``domains`` ({(2x, 2y): name}, ``load_domains``) splits power made and used by power domain
    too, the power record's ``domains`` ({name: {"made": J, "used": J}}), when the sample has
    more than one network."""
    t = cur["tick"]
    gap = None
    if prev is not None:
        if cur.get("seed") != prev.get("seed"):
            gap = "another map"
        elif t <= prev["tick"]:
            gap = f"the game went back to tick {t} (a save loaded again)"
    base = prev or {"tick": 0, "items": {"made": {}, "used": {}},
                    "fluids": {"made": {}, "used": {}}, "power": {}}
    out = []
    if gap is None:
        recs = {}
        for name in ("items", "fluids"):
            made = _sub(cur[name]["made"], base[name]["made"])
            used = _sub(cur[name]["used"], base[name]["used"])
            if made is None or used is None:
                gap = f"the {name} counts went down (a save loaded again)"
                break
            recs[name] = {"made": made, "used": used}
    if gap is not None:
        for name in ("items", "fluids", "power"):
            out.append((name, {"tick": t, "gap": gap}))
        res = dict(cur["research"])
        out.append(("research", {"tick": t, "gap": gap, **_research(res)}))
    else:
        frm = base["tick"]
        for name in ("items", "fluids"):
            out.append((name, {"tick": t, "from": frm, **recs[name]}))
        made, used, by = {}, {}, {}
        for net, now in cur["power"].items():
            was = base["power"].get(net, {"made": {}, "used": {}})
            m, u = _sub(now["made"], was["made"]), _sub(now["used"], was["used"])
            if m is None or u is None:
                continue                   # a network that changed: its counts start over
            _add(made, m)
            _add(used, u)
            if domains and now.get("at"):
                d = by.setdefault(domain_of(now["at"], domains), {"made": 0, "used": 0})
                d["made"] = _num(d["made"] + sum(m.values()))
                d["used"] = _num(d["used"] + sum(u.values()))
        types = cur.get("types") or {}
        rec = {"tick": t, "from": frm, "made": made, "used": used,
               "types": {n: types[n] for n in sorted(set(made) | set(used)) if n in types}}
        if by:
            rec["domains"] = by
        out.append(("power", rec))
        packs = {n: v for n, v in recs["items"]["used"].items() if n.endswith("science-pack")}
        out.append(("research", {"tick": t, "from": frm, **_research(cur["research"]),
                                 "packs": packs}))
    types = cur.get("types") or {}
    out.append(("machines", {"tick": t, "machines": cur["machines"], "recipes": cur["recipes"],
                             "types": {n: types[n] for n in cur["machines"] if n in types}}))
    return out


def _research(res):
    keep = ("name", "level", "progress", "units", "ingredients", "researched", "queue")
    return {k: res[k] for k in keep if res.get(k) is not None}


def recorder(path, offset=0, first=False, seed=None):
    """The recorder's whole lines past ``offset`` -> (raw samples, the new offset). A file that
    shrank was started over: read from its start. ``seed``: only that map's lines. ``first`` (no
    offset kept yet): only the lines since the tick last went back, the current map's (a file
    may hold an older map's lines, or this map's before a save was loaded again)."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], 0
    if size < offset:
        offset = 0
    with open(path, "rb") as fh:
        fh.seek(offset)
        data = fh.read(size - offset)
    end = data.rfind(b"\n") + 1
    out = []
    for ln in data[:end].splitlines():
        try:
            row = json.loads(ln.decode("utf-8", "replace"))
        except ValueError:
            continue
        if isinstance(row, dict) and "tick" in row and (seed is None or row.get("seed") == seed):
            out.append(row)
    if first:
        cut = 0
        for i in range(1, len(out)):
            if int(out[i]["tick"]) <= int(out[i - 1]["tick"]) or \
                    out[i].get("seed") != out[i - 1].get("seed"):
                cut = i
        out = out[cut:]
    return out, offset + end


class Store:
    """``<game>/out/stats/``: one jsonl per series, ``state.json`` and the page."""

    def __init__(self, game):
        self.game = game
        self.dir = os.path.join(game, "out", "stats")

    def path(self, name):
        return os.path.join(self.dir, name)

    def state(self):
        try:
            with open(self.path("state.json"), encoding="utf-8") as fh:
                st = json.load(fh)
            return st if isinstance(st, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_state(self, st):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path("state.json") + f".{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        os.replace(tmp, self.path("state.json"))

    def append(self, series, rec):
        os.makedirs(self.dir, exist_ok=True)
        with open(self.path(f"{series}.jsonl"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")

    def read(self, series):
        out = []
        try:
            with open(self.path(f"{series}.jsonl"), encoding="utf-8") as fh:
                for ln in fh:
                    try:
                        out.append(json.loads(ln))
                    except ValueError:
                        pass
        except OSError:
            pass
        return out


def ingest(store, state, samples, domains=None):
    """Each sample (normed) against the last one kept: its records appended, and it becomes the
    last. -> how many were kept. A sample at the last one's tick is the same sample: skipped.
    ``domains``: as :func:`delta` takes them."""
    n = 0
    for s in samples:
        prev = state.get("last")
        if prev and prev.get("seed") == s.get("seed") and prev["tick"] == s["tick"]:
            continue
        for series, rec in delta(prev, s, domains):
            store.append(series, rec)
        state["last"] = s
        n += 1
    return n


def _clock(tick):
    m = int(tick) // 3600
    return f"{m // 60}:{m % 60:02d}"


def sample(r, game, cat, force, surface, now=False, out_dir=None):
    """Take what is due: the recorder's new lines, or (no recorder lately) one RCON read when
    5 game minutes have passed since the last sample (``now``: at once); and the capital read
    when 10 game minutes have passed since its last. -> the report's lines."""
    from . import capital
    store = Store(game)
    state = store.state()
    got = r.lua(lua_tick(surface)).split()
    if len(got) != 2 or not got[0].isdigit():
        raise link.LinkError(f"stats: no tick: {' '.join(got)[:200]}")
    tick, seed = int(got[0]), int(float(got[1]))
    path = os.path.join(out_dir or link.OUT_DIR, *RECORDER.split("/"))
    raw, state["offset"] = recorder(path, int(state.get("offset") or 0), "offset" not in state,
                                    seed)
    rows = [x for x in (norm(x, "recorder") for x in raw) if x["tick"] <= tick]
    if rows:
        state["recorder"] = {"tick": rows[-1]["tick"], "seed": seed}
    rec = state.get("recorder") or {}
    live = rec.get("seed") == seed and 0 <= tick - int(rec.get("tick") or -1) <= EVERY + SLACK
    last = state.get("last")
    due = (now or not last or last.get("seed") != seed or tick < last["tick"]
           or tick - last["tick"] >= EVERY)
    via = "the recorder" if rows else ""
    if due and (now or not live):
        tag = f"stats-{os.getpid()}"
        rows.append(norm(link.read_output(r, lua_sample(force, surface, tag), tag, "stats sample",
                                          keep=False), "rcon"))
        via = "the recorder and RCON" if via else "RCON"
    kept = ingest(store, state, rows, load_domains(game))
    store.save_state(state)
    last = state.get("last") or {}
    nxt = (int(rec.get("tick") or 0) if live else int(last.get("tick") or tick)) + EVERY
    out = [f"stats: {kept} sample{'s' if kept != 1 else ''} kept from {via}, up to game time "
           f"{_clock(last.get('tick', tick))}" if kept else
           f"stats: nothing new at game time {_clock(tick)}; the next sample is due at "
           f"{_clock(nxt)}" + (" (the scenario's recorder writes it)" if live else "")]
    hist = capital.Store(game).history()
    if now or not hist or tick < hist[-1][0] or tick - hist[-1][0] >= CAPITAL_EVERY - SLACK:
        try:
            out += capital.run(r, game, cat, force, surface, brief=True)
        except (link.LinkError, OSError, ValueError, KeyError, TypeError) as exc:
            out.append(f"capital: not read ({type(exc).__name__}: {exc})")
    return out


# --- reading the history --------------------------------------------------------------------

def _mins(rec):
    return max(1e-9, (rec["tick"] - rec["from"]) / 3600.0)


def windows(recs):
    """The records with a window (not gaps), oldest first."""
    return [r for r in recs if "from" in r and r["tick"] > r["from"]]


def _sum_windows(ws, key):
    tot = {}
    for w in ws:
        _add(tot, w.get(key) or {})
    return tot


def _rate(ws, key):
    """Per game minute over the windows ``ws``: {name: count/min}."""
    mins = sum(_mins(w) for w in ws)
    return {k: v / mins for k, v in _sum_windows(ws, key).items()} if mins > 0 else {}


def _fmt(v):
    v = float(v)
    a = abs(v)
    if a < 0.005:
        return "0"
    if a >= 1e6:
        return f"{v / 1e6:.1f}M"
    if a >= 1e4:
        return f"{v / 1e3:.0f}k"
    if a >= 1e3:
        return f"{v / 1e3:.1f}k"
    if a >= 10:
        return f"{v:.0f}"
    return f"{v:.1f}" if a >= 1 else f"{v:.2f}"


def _mw(j_per_min):
    return j_per_min / 60.0 / 1e6


def _watts(j_per_min):
    """Joules per minute as power: kW, MW or GW."""
    w = j_per_min / 60.0
    if w >= 1e9:
        return f"{w / 1e9:.2f} GW"
    if w >= 1e6:
        return f"{w / 1e6:.1f} MW"
    return f"{w / 1e3:.0f} kW"


#: the power classes: what makes power, by entity name; what uses it, by prototype type
MADE_CLASS = {"steam-engine": "steam engines", "steam-turbine": "steam turbines",
              "solar-panel": "solar panels", "accumulator": "accumulators"}
USED_CLASS = {"assembling-machine": "assemblers", "furnace": "furnaces",
              "mining-drill": "drills", "lab": "labs", "inserter": "inserters",
              "roboport": "roboports", "beacon": "beacons", "radar": "radars",
              "accumulator": "accumulators (charging)", "lamp": "lamps",
              "electric-turret": "laser turrets", "offshore-pump": "pumps", "pump": "pumps",
              "rocket-silo": "rocket silos", "boiler": "boilers"}


def power_classes(rec):
    """A power record per class: ({class: J} made, {class: J} used)."""
    types = rec.get("types") or {}
    made, used = {}, {}
    for n, v in (rec.get("made") or {}).items():
        k = MADE_CLASS.get(n) or MADE_CLASS.get(types.get(n), n)
        made[k] = made.get(k, 0) + v
    for n, v in (rec.get("used") or {}).items():
        t = types.get(n, n)
        k = "accumulators (charging)" if n == "accumulator" else USED_CLASS.get(t, t)
        used[k] = used.get(k, 0) + v
    return made, used


#: a machine state (defines.entity_status) as the history says it
STATE_WORDS = {"working": "working", "normal": "working", "low_power": "low power",
               "no_power": "no power", "item_ingredient_shortage": "short of input",
               "fluid_ingredient_shortage": "short of input", "no_input_fluid": "short of input",
               "missing_science_packs": "short of input", "no_ingredients": "short of input",
               "full_output": "output full", "waiting_for_space_in_destination": "output full",
               "no_minable_resources": "nothing to mine", "no_recipe": "no recipe",
               "disabled_by_control_behavior": "off by circuit", "no_fuel": "no fuel",
               "marked_for_deconstruction": "to be taken up"}


def _states(counts):
    out = {}
    for k, n in (counts or {}).items():
        w = STATE_WORDS.get(k, k.replace("_", " "))
        out[w] = out.get(w, 0) + int(n)
    return out


def _research_head(res):
    """The research line from the research records (the last two)."""
    if not res:
        return "research: no sample yet"
    cur = res[-1]
    done = cur.get("researched")
    tail = f"; {done} technologies done" if done is not None else ""
    if not cur.get("name"):
        return "RESEARCH: NONE in the last sample: nothing researches" + tail
    pct = float(cur.get("progress") or 0)
    head = f"research {cur['name']} {pct * 100:.0f}%"
    w = windows([cur])
    if w:
        packs = sum((cur.get("packs") or {}).values())
        rate = packs / _mins(cur)
        head += f", {rate:.0f} packs/min over the last {_mins(cur):.0f} min"
        units = float(cur.get("units") or 0)
        per_unit = sum(float(a) for _n, a in cur.get("ingredients") or []) or 1
        if rate > 0 and units:
            left = max(0.0, 1 - pct) * units / (rate / per_unit)
            head += f", ~{left:.0f} min left" if left < 120 else f", ~{left / 60:.1f} h left"
        elif rate <= 0:
            head = "RESEARCH STALLED: " + head
    return head + tail


def history_lines(game, last_n=6, top=5):
    """``fk stats --history``: the research, then the latest window and the trend (the last
    ``last_n`` windows against the ``last_n`` before them)."""
    from . import capital
    store = Store(game)
    items, power = store.read("items"), store.read("power")
    res, mach = store.read("research"), store.read("machines")
    out = [_research_head(res)]
    wi = windows(items)
    if not wi:
        out.append(f"stats: no samples yet in {store.dir} (fk stats --sample takes one)")
        return out
    last = wi[-1]
    gaps = [r for r in items if "gap" in r]
    out.append(f"latest window: game time {_clock(last['from'])}-{_clock(last['tick'])} "
               f"({_mins(last):.0f} min); {len(wi)} windows since {_clock(wi[0]['from'])}"
               + (f", {len(gaps)} gap{'s' if len(gaps) != 1 else ''} (resets)" if gaps else ""))
    hist = capital.Store(game).history()
    if hist:
        t, w, tot = hist[-1]
        line = (f"  capital {_fmt(tot)} raw units, {w / tot:.0%} working (reinvested)"
                if tot else "  capital: 0")
        back = [h for h in hist if h[0] <= t - 3600 * 60]
        if back and back[-1][2]:
            b = back[-1]
            line += (f"; an hour before: {_fmt(b[2])}, {b[1] / b[2]:.0%} working "
                     f"({(tot / b[2] - 1) * 100:+.0f}% capital)")
        out.append(line)
    else:
        out.append("  capital: no read yet")
    rate = _rate([last], "made")
    used = _rate([last], "used")
    tops = lambda d: ", ".join(f"{n} {_fmt(v)}" for n, v in
                               sorted(d.items(), key=lambda kv: -kv[1])[:top]) or "nothing"
    out.append(f"  made/min: {tops(rate)}")
    out.append(f"  used/min: {tops(used)}")
    wp = [p for p in windows(power) if p["tick"] == last["tick"]] or windows(power)[-1:]
    if wp:
        made, usedp = power_classes(wp[-1])
        mins = _mins(wp[-1])
        tm, tu = sum(made.values()) / mins, sum(usedp.values()) / mins
        out.append(f"  power: made {_watts(tm)}, used {_watts(tu)} (balance "
                   f"{'+' if tm >= tu else '-'}{_watts(abs(tm - tu))}); made by " + (", ".join(
                       f"{k} {_watts(v / mins)}" for k, v in
                       sorted(made.items(), key=lambda kv: -kv[1])[:top]) or "nothing")
                   + "; used by " + (", ".join(f"{k} {_watts(v / mins)}" for k, v in
                                               sorted(usedp.items(), key=lambda kv: -kv[1])[:top])
                                     or "nothing"))
        doms = wp[-1].get("domains") or {}
        if doms:
            # 2.1: by power domain (the build's out/power-domains.json)
            out.append("  power by domain: " + "; ".join(
                f"{k} made {_watts(v.get('made', 0) / mins)}, used {_watts(v.get('used', 0) / mins)}"
                for k, v in sorted(doms.items(), key=lambda kv: (kv[0] == REST, kv[0]))))
    if mach:
        m = mach[-1]
        per = []
        for n, c in sorted((m.get("machines") or {}).items(),
                           key=lambda kv: -sum(kv[1].values())):
            st = _states(c)
            tot = sum(st.values())
            rest = ", ".join(f"{v} {k}" for k, v in sorted(st.items(), key=lambda kv: -kv[1])
                             if k != "working")
            per.append(f"{n} {st.get('working', 0)}/{tot} working" + (f" ({rest})" if rest else ""))
        if per:
            out.append("  machines: " + "; ".join(per[:8]))
        stuck = []
        for rn, c in (m.get("recipes") or {}).items():
            st = _states(c)
            tot = sum(st.values())
            if tot >= 2 and st.get("working", 0) < tot / 2:
                worst = max((kv for kv in st.items() if kv[0] != "working"), key=lambda kv: kv[1],
                            default=None)
                if worst:
                    stuck.append((worst[1], f"{rn} {worst[1]} of {tot} {worst[0]}"))
        if stuck:
            out.append("  recipes mostly standing: " + "; ".join(
                s for _n, s in sorted(stuck, reverse=True)[:top]))
    if len(wi) >= 2 * last_n:
        a, b = wi[-last_n:], wi[-2 * last_n:-last_n]
        ra, rb = _rate(a, "made"), _rate(b, "made")
        names = sorted(ra, key=lambda n: -ra[n])[:top]
        span = sum(_mins(w) for w in a)
        out.append(f"trend, the last {span:.0f} min against the {sum(_mins(w) for w in b):.0f} "
                   "before: " + ", ".join(
                       f"{n} {_fmt(rb.get(n, 0))} -> {_fmt(ra[n])}/min" for n in names))
        pa = [p for p in windows(power) if a[0]["from"] <= p["from"] and p["tick"] <= a[-1]["tick"]]
        pb = [p for p in windows(power) if b[0]["from"] <= p["from"] and p["tick"] <= b[-1]["tick"]]
        if pa and pb:
            ma, mb = (sum(sum(p.get("made", {}).values()) for p in ps) / sum(_mins(p) for p in ps)
                      for ps in (pa, pb))
            out.append(f"  power made {_watts(mb)} -> {_watts(ma)}")
    else:
        out.append(f"trend: after {2 * last_n} windows ({2 * last_n * 5} game minutes)")
    tot = _sum_windows(wi, "made")
    span = sum(_mins(w) for w in wi)
    out.append(f"since {_clock(wi[0]['from'])} ({span / 60:.1f} h of windows): made " + ", ".join(
        f"{n} {_fmt(v)}" for n, v in sorted(tot.items(), key=lambda kv: -kv[1])[:top]))
    return out


# --- the page --------------------------------------------------------------------------------

#: the categorical colours in a fixed order (light, dark)
PALETTE = [("#2a78d6", "#3987e5"), ("#eb6834", "#d95926"), ("#1baf7a", "#199e70"),
           ("#eda100", "#c98500"), ("#e87ba4", "#d55181"), ("#008300", "#008300"),
           ("#6250d6", "#9085e9"), ("#e34948", "#e66767")]
#: science packs in tier order: each keeps its colour slot
PACKS = ["automation-science-pack", "logistic-science-pack", "military-science-pack",
         "chemical-science-pack", "production-science-pack", "utility-science-pack",
         "space-science-pack"]
#: machine states, in the order the charts draw them
STATE_ORDER = ["working", "short of input", "output full", "no power", "low power"]
BUCKETS = 240


def _buckets(ws, n=BUCKETS):
    """At most ``n`` windows: neighbours merged (their counts summed), so a long run's page stays
    small. Windows across a gap are never merged."""
    if len(ws) <= n:
        return ws
    k = -(-len(ws) // n)
    out, cur = [], None
    for i, w in enumerate(ws):
        if cur is not None and (i % k == 0 or w["from"] != cur["tick"]):
            out.append(cur)
            cur = None
        if cur is None:
            cur = {"from": w["from"], "tick": w["tick"]}
            for key in ("made", "used", "packs"):
                if key in w:
                    cur[key] = dict(w[key])
            if "types" in w:
                cur["types"] = dict(w["types"])
        else:
            cur["tick"] = w["tick"]
            for key in ("made", "used", "packs"):
                if key in w:
                    _add(cur.setdefault(key, {}), w[key])
            cur.setdefault("types", {}).update(w.get("types") or {})
    if cur is not None:
        out.append(cur)
    return out


def _hours(tick):
    return tick / 3600.0 / 60.0


class _Chart:
    """One SVG line chart over game time: series of step windows or points, one y axis."""

    W, H, L, R, T, B = 640, 180, 52, 10, 10, 24

    def __init__(self, x0, x1, unit=""):
        self.x0, self.x1 = x0, max(x1, x0 + 1e-6)
        self.series = []   # (name, slot, [(x0, x1, y) or None for a break])
        self.unit = unit

    def add(self, name, slot, pts):
        self.series.append((name, slot, pts))

    def svg(self, small=False, title=""):
        W, H = (300, 90) if small else (self.W, self.H)
        L, R, T, B = (36, 6, 6, 16) if small else (self.L, self.R, self.T, self.B)
        ys = [p[2] for _n, _s, pts in self.series for p in pts if p]
        top = max(ys + [0]) or 1
        top = _nice(top)
        sx = lambda x: L + (x - self.x0) / (self.x1 - self.x0) * (W - L - R)
        sy = lambda y: T + (1 - y / top) * (H - T - B)
        parts = [f'<svg viewBox="0 0 {W} {H}" class="chart{" small" if small else ""}" '
                 f'role="img" aria-label="{html.escape(title)}">']
        if title:
            parts.append(f"<title>{html.escape(title)}</title>")
        for f in (0, 0.5, 1):
            y = sy(top * f)
            parts.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
            parts.append(f'<text x="{L - 4}" y="{y + 3:.1f}" class="ax" text-anchor="end">'
                         f'{_fmt(top * f)}</text>')
        for h in _ticks(self.x0, self.x1, 3 if small else 6):
            x = sx(h)
            parts.append(f'<text x="{x:.1f}" y="{H - 3}" class="ax" text-anchor="middle">'
                         f'{h:g}h</text>')
        for name, slot, pts in self.series:
            path, pen = [], False
            for i, p in enumerate(pts):
                if p is None:
                    pen = False
                    continue
                a, b, y = p
                alone = a == b and not pen and (i + 1 == len(pts) or pts[i + 1] is None)
                if alone:                  # a point with no neighbour: a dot
                    parts.append(f'<circle cx="{sx(b):.1f}" cy="{sy(y):.1f}" r="3" '
                                 f'class="d{slot}"/>')
                if a == b:                 # a point: joined to the one before by a line
                    path.append(f'{"L" if pen else "M"}{sx(b):.1f},{sy(y):.1f}')
                else:                      # a window: a step at its rate
                    path.append(f'{"L" if pen else "M"}{sx(a):.1f},{sy(y):.1f}'
                                f'L{sx(b):.1f},{sy(y):.1f}')
                pen = True
            if path:
                parts.append(f'<path d="{"".join(path)}" class="s{slot}"><title>'
                             f'{html.escape(name)}</title></path>')
        parts.append("</svg>")
        return "".join(parts)

    def data(self):
        """For the hover readout: the window ends and each series' values."""
        ends = sorted({p[1] for _n, _s, pts in self.series for p in pts if p})
        out = []
        for name, slot, pts in self.series:
            by = {p[1]: p[2] for p in pts if p}
            out.append([name, slot, [round(by[e], 3) if e in by else None for e in ends]])
        return {"x": [round(e, 4) for e in ends], "x0": self.x0, "x1": self.x1, "s": out,
                "u": self.unit}


def _nice(v):
    import math
    e = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if m * e >= v:
            return m * e
    return 10 * e


def _ticks(a, b, n):
    span = b - a
    if span <= 0:
        return []
    step = _nice(span / n)
    import math
    k = math.ceil(a / step)
    out = []
    while k * step <= b + 1e-9:
        out.append(round(k * step, 6))
        k += 1
    return out


def _steps(ws, value):
    """[(x0, x1, y)] per window, None between windows that don't touch (a gap)."""
    pts, end = [], None
    for w in ws:
        if end is not None and w["from"] != end:
            pts.append(None)
        y = value(w)
        pts.append((_hours(w["from"]), _hours(w["tick"]), y) if y is not None else None)
        end = w["tick"]
    return pts


def _legend(names):
    return '<div class="legend">' + "".join(
        f'<span><i class="k{slot}"></i>{html.escape(n)}</span>' for n, slot in names) + "</div>"


def _top_slots(totals, limit=7):
    """The ``limit`` biggest names keep slots 0..; the rest fold into 'other' (the last slot)."""
    names = [n for n, _v in sorted(totals.items(), key=lambda kv: -kv[1]) if _v > 0]
    keep = names[:limit] if len(names) > limit + 1 else names[:limit + 1]
    return keep, [n for n in names if n not in keep]


CSS = """
:root{--bg:#fcfcfb;--fg:#0b0b0b;--muted:#52514e;--grid:#e4e3df;--card:#ffffff}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#1a1a19;--fg:#f0efec;
--muted:#c3c2b7;--grid:#3a3a37;--card:#222221}}
:root[data-theme="dark"]{--bg:#1a1a19;--fg:#f0efec;--muted:#c3c2b7;--grid:#3a3a37;--card:#222221}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,sans-serif}
main{max-width:1000px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
h3{font-size:13px;margin:0 0 2px;font-weight:600;overflow:hidden;text-overflow:ellipsis;
white-space:nowrap}
.sub,.ax,.note{color:var(--muted);fill:var(--muted)}.ax{font-size:10px}
pre{background:var(--card);padding:10px;overflow-x:auto;font-size:12px;border-radius:6px}
.chart{width:100%;height:auto;display:block}.grid{stroke:var(--grid);stroke-width:1}
.card{background:var(--card);border-radius:8px;padding:8px 10px;margin:8px 0;position:relative}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:8px}
.grid2 .card{margin:0}
path{fill:none;stroke-width:2;stroke-linejoin:round}.small path{stroke-width:1.5}
.legend{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--muted);margin:2px 0 6px}
.legend i{display:inline-block;width:12px;height:3px;border-radius:2px;margin-right:5px;
vertical-align:middle}
.tip{position:absolute;pointer-events:none;background:var(--bg);border:1px solid var(--grid);
border-radius:6px;padding:4px 8px;font-size:12px;display:none;white-space:nowrap;z-index:2}
"""

JS = """
document.querySelectorAll('[data-c]').forEach(function(card){
  var d=CH[+card.dataset.c],svg=card.querySelector('svg'),tip=card.querySelector('.tip');
  svg.addEventListener('mousemove',function(e){
    var r=svg.getBoundingClientRect(),vb=svg.viewBox.baseVal,
        x=(e.clientX-r.left)/r.width*vb.width,L=52,R=10,
        h=d.x0+(x-L)/(vb.width-L-R)*(d.x1-d.x0),best=-1,bd=1e9;
    d.x.forEach(function(v,i){var dd=Math.abs(v-h);if(dd<bd){bd=dd;best=i}});
    if(best<0){tip.style.display='none';return}
    var s='<b>'+d.x[best].toFixed(2)+' h</b>';
    d.s.forEach(function(q){if(q[2][best]!==null)s+='<br><i class="k'+q[1]+'" style="display:inline-block;width:10px;height:3px;margin-right:4px;vertical-align:middle"></i>'+q[0]+': '+q[2][best]+(d.u?' '+d.u:'')});
    tip.innerHTML=s;tip.style.display='block';
    tip.style.left=Math.min(e.clientX-card.getBoundingClientRect().left+12,card.clientWidth-tip.offsetWidth-4)+'px';
    tip.style.top='8px';
  });
  svg.addEventListener('mouseleave',function(){tip.style.display='none'});
});
"""


def _css():
    """The page's style: the series colours per slot, light, and stepped for the dark surface."""
    def rules(scope, k):
        return "".join(f"{scope}.s{i}{{stroke:{pair[k]}}}{scope}.k{i}{{background:{pair[k]}}}"
                       f"{scope}.d{i}{{fill:{pair[k]}}}" for i, pair in enumerate(PALETTE))
    other = ".s8{stroke:#8a8984}.k8{background:#8a8984}.d8{fill:#8a8984}"
    return (CSS + rules("", 0) + other + "@media (prefers-color-scheme: dark){"
            + rules(':root:not([data-theme="light"]) ', 1) + "}"
            + rules(':root[data-theme="dark"] ', 1))


def page(game, title=None):
    """The run's stats page: every series over game time, as one static HTML file."""
    from . import capital
    store = Store(game)
    run = title or os.path.basename(os.path.dirname(os.path.abspath(game))) or "run"
    items, fluids = windows(store.read("items")), windows(store.read("fluids"))
    power, res = windows(store.read("power")), store.read("research")
    mach = store.read("machines")
    hist = capital.Store(game).history()
    ends = [w["tick"] for w in items + power] + [h[0] for h in hist] + [m["tick"] for m in mach]
    starts = [w["from"] for w in items + power] + [h[0] for h in hist]
    x0, x1 = _hours(min(starts or [0])), _hours(max(ends or [3600]))
    charts, body = [], []

    def card(chart, title, legend=None, small=False, hover=True):
        i = len(charts)
        charts.append(chart.data() if hover else None)
        attrs = f' data-c="{i}"' if hover else ""
        return (f'<div class="card"{attrs}><h3>{html.escape(title)}</h3>'
                + (_legend(legend) if legend else "") + chart.svg(small, title)
                + ('<div class="tip"></div>' if hover else "") + "</div>")

    head = history_lines(game)
    body.append(f"<h1>{html.escape(run)}: run stats</h1>")
    body.append(f'<div class="sub">game time {x0:.2f} h to {x1:.2f} h; one window every 5 game '
                f'minutes (merged into at most {BUCKETS} on this page). Written by fkcore.runstats '
                f'from out/stats.</div>')
    body.append("<pre>" + html.escape("\n".join(head)) + "</pre>")

    # research
    body.append("<h2>Research</h2>")
    rw = [r for r in res if "from" in r]
    c = _Chart(x0, x1, "%")
    pts, prev = [], None
    for r in res:
        if r.get("name") is None:
            pts.append(None)
            prev = None
            continue
        if prev is not None and prev != r.get("name"):
            pts.append(None)
        t = _hours(r["tick"])
        pts.append((t, t, float(r.get("progress") or 0) * 100))
        prev = r.get("name")
    c.add("progress of the research under way", 0, pts)
    names = sorted({r.get("name") for r in res if r.get("name")}, key=lambda n: [
        r["tick"] for r in res if r.get("name") == n][0])
    body.append(card(c, "research progress, % (" + (", ".join(names[-6:]) or "none") + ")"))
    rb = _buckets(rw)
    packs = sorted({p for r in rb for p in (r.get("packs") or {})},
                   key=lambda p: PACKS.index(p) if p in PACKS else 99)
    c = _Chart(x0, x1, "/min")
    leg = []
    for i, p in enumerate(packs[:8]):
        slot = PACKS.index(p) if p in PACKS else min(i, 7)
        c.add(p, slot, _steps(rb, lambda w, p=p: (w.get("packs") or {}).get(p, 0) / _mins(w)))
        leg.append((p, slot))
    body.append(card(c, "science packs used per minute", leg))

    # capital
    body.append("<h2>Capital</h2>")
    if hist:
        c = _Chart(x0, x1, "raw")
        c.add("total", 0, list([(_hours(t), _hours(t), tot) for t, _w, tot in hist]))
        c.add("working", 2, list([(_hours(t), _hours(t), w) for t, w, _tot in hist]))
        body.append(card(c, "capital, raw units", [("total", 0), ("working", 2)]))
        c = _Chart(x0, x1, "%")
        c.add("reinvested", 0, list([(_hours(t), _hours(t), (w / tot * 100 if tot else 0))
                                      for t, w, tot in hist]))
        body.append(card(c, "reinvestment: % of capital working"))
    else:
        body.append('<p class="note">No capital read yet.</p>')

    # power
    body.append("<h2>Power</h2>")
    pb = _buckets(power)
    cls = [power_classes(w) for w in pb]
    for idx, what in ((0, "made per generator class"), (1, "used per machine type")):
        tot = {}
        for pair in cls:
            _add(tot, pair[idx])
        keep, rest = _top_slots(tot)
        c = _Chart(x0, x1, "MW")
        leg = []
        for slot, k in enumerate(keep):
            c.add(k, slot, _steps(pb, lambda w, k=k, i=idx: _mw(power_classes(w)[i].get(k, 0)
                                                                 / _mins(w))))
            leg.append((k, slot))
        if rest:
            c.add("other", 8, _steps(pb, lambda w, i=idx: _mw(sum(
                v for k, v in power_classes(w)[i].items() if k in rest) / _mins(w))))
            leg.append(("other", 8))
        body.append(card(c, f"power {what}, MW", leg))
    c = _Chart(x0, x1, "MW")
    c.add("made", 0, _steps(pb, lambda w: _mw(sum((w.get("made") or {}).values()) / _mins(w))))
    c.add("used", 1, _steps(pb, lambda w: _mw(sum((w.get("used") or {}).values()) / _mins(w))))
    body.append(card(c, "power balance, MW", [("made", 0), ("used", 1)]))

    # machines
    body.append("<h2>Machines</h2>")
    body.append(_legend([(s, i) for i, s in enumerate(STATE_ORDER)] + [("other", 8)]))
    kinds = sorted({n for m in mach for n in (m.get("machines") or {})},
                   key=lambda n: -max(sum((m.get("machines") or {}).get(n, {}).values())
                                      for m in mach))
    recs = sorted({n for m in mach for n in (m.get("recipes") or {})},
                  key=lambda n: -max(sum((m.get("recipes") or {}).get(n, {}).values())
                                     for m in mach))
    for label, key, names in (("per machine", "machines", kinds), ("per recipe", "recipes", recs)):
        body.append(f"<h3 class=\"note\">{label}</h3><div class=\"grid2\">")
        for n in names:
            c = _Chart(x0, x1)
            for slot, s in enumerate(STATE_ORDER):
                c.add(s, slot, list([(_hours(m["tick"]), _hours(m["tick"]),
                                       _states((m.get(key) or {}).get(n)).get(s, 0)) for m in mach]))
            c.add("other", 8, list([(_hours(m["tick"]), _hours(m["tick"]), sum(
                v for k, v in _states((m.get(key) or {}).get(n)).items() if k not in STATE_ORDER))
                for m in mach]))
            body.append(card(c, n, small=True, hover=False))
        body.append("</div>")

    # items and fluids
    for label, ws in (("Items", items), ("Fluids", fluids)):
        body.append(f"<h2>{label}: made and used per minute</h2>")
        body.append(_legend([("made", 0), ("used", 1)]))
        b = _buckets(ws)
        tot = _sum_windows(b, "made")
        _add(tot, {k: v * 1e-9 for k, v in _sum_windows(b, "used").items()})
        body.append('<div class="grid2">')
        for n in sorted(tot, key=lambda k: -tot[k]):
            c = _Chart(x0, x1, "/min")
            c.add("made", 0, _steps(b, lambda w, n=n: (w.get("made") or {}).get(n, 0) / _mins(w)))
            c.add("used", 1, _steps(b, lambda w, n=n: (w.get("used") or {}).get(n, 0) / _mins(w)))
            made, used = _sum_windows(b, "made").get(n, 0), _sum_windows(b, "used").get(n, 0)
            body.append(card(c, f"{n} (made {_fmt(made)}, used {_fmt(used)})", small=True,
                             hover=False))
        body.append("</div>")
    data = json.dumps(charts, separators=(",", ":"))
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>Run stats: {html.escape(run)}</title><style>{_css()}</style></head><body>"
            f"<main>{''.join(body)}</main><script>var CH={data};{JS}</script></body></html>")


def write_page(game, title=None):
    """``page`` into ``<game>/out/stats/index.html`` -> its path."""
    store = Store(game)
    os.makedirs(store.dir, exist_ok=True)
    path = store.path("index.html")
    tmp = path + f".{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(page(game, title))
    os.replace(tmp, path)
    return path
