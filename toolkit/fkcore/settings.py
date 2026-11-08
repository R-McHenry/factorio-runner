"""fkcore.settings: a standing entity's settings against the design's (2.0.12).

Rory, 2026-10-24: "it needs to be able to see my circuit changes and know that it is overriding
them too". A diff judged an entity by its name, position and direction, so a condition, a wire, a
filter, a request or a recipe changed on an entity the toolkit placed went unseen (the solo run,
2026-10-23 21:52: "a couple circuit tweaks I did since some of the inserters are out of roboport
range"). A paste then set it back without a word: on a laptop server (2.0.77) a paste over a
standing inserter and requester chest wrote the design's logistic condition and request over
the ones set by hand, and placed nothing.

``lua_read`` blueprints the area in the game and writes each entity's blueprint fields with its
position (the game's own export of its settings, as a paste reads them). ``design`` gives the same
fields for the design's rows (``codec``, as the paste writes them), and ``drifts`` compares them
for the entities that stand where the design has them (a diff's ok and settings-changed): ``KEYS``
(recipe, control behaviour, filters, requests, a chest's limit) and the circuit wires between
them. Before comparing, what the game exports by default is dropped. Each difference is a line
for the hand edits (``sense.hand_edits``): who changed what, and what the paste does about it.
What a paste does, as measured on that server (2.0.77, again for 2.1 on 2026-10-31):
- where the design sets a field, the paste writes it, and a control behaviour whole;
- where the design has none, a request, the trash and buffer flags, a chest's limit, a storage
  chest's filter, an inserter's filters and a combinator's settings are cleared. A control
  behaviour is not: one at the game's defaults is ignored, and with none at all a wired machine
  keeps its own. So since 2.1 the codec writes ``codec.RESET_CB`` (off, a condition with no
  signal) on a machine whose design sets none, and the paste clears it too;
- a mining drill, a pumpjack or an offshore pump takes nothing from a paste
  (``codec.PASTE_SKIPS``): the apply lays one fresh;
- a wire stays: the apply's wire pass (``fkcore.wires``) cuts it.
So the design's explicit defaults (``RESET_CB``, a false flag, an empty list, a whitelist) and an
absent field compare equal here: ``_clean`` drops both.

Raw settings (2.1): a ``cb.`` field is part of the control behaviour, compared whole as before; a
``bp.`` field (a speaker's ``parameters``, a display panel's ``text``, a lamp's ``color``) is
compared where the design sets it. The game's defaults for the buildings they reach (a
roboport's output signals, a speaker's instrument) are dropped like the others, and a number is
compared to 6 decimals (the game keeps a volume or a colour as a 32-bit float: 0.8 comes back
0.800000011920929).
Pure but for ``read``; stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import arms as armsmod
from . import codec
from . import fkp as fkpmod

__all__ = ["KEYS", "Drift", "lua_read", "read", "design", "live", "drifts", "describe"]

#: the blueprint fields a diff compares (the recipe also makes a DRIFT verdict; here it is a hand
#: edit with who made it)
KEYS = ("recipe", "control_behavior", "use_filters", "filters", "filter_mode", "request_filters",
        "bar", "vectors")
#: 3.0: an inserter's pickup_position / drop_position (Bob's adjustable inserters), compared as
#: whole tiles (``vectors``, ``fkcore.arms``): the game keeps a drop of 1.2 as 1.19921875, and
#: exports only the one that is not the prototype's
_VECTORS = ("pickup_position", "drop_position")
#: fields at the game's defaults: what the game exports when nobody set them (2.0.77: every
#: inserter on a wire reads stack size from signal-S; 2.1: a belt's read mode, a lamp's colour
#: signals, a drill's resource read, on any of them with a control behaviour), and the codec's
#: explicit defaults (2.1)
_V = "virtual"
_DEFAULTS = {"stack_control_input_signal": {"type": _V, "name": "signal-S"},
             "filter_mode": "whitelist", "is_on": True, "circuit_contents_read_mode": 0,
             "red_signal": {"type": _V, "name": "signal-red"},
             "green_signal": {"type": _V, "name": "signal-green"},
             "blue_signal": {"type": _V, "name": "signal-blue"},
             "rgb_signal": {"type": _V, "name": "signal-white"},
             "circuit_read_resources": True, "circuit_resource_read_mode": 0,
             # 2.1, measured on a laptop server (2.0.77): a roboport's and a speaker's own
             "available_logistic_output_signal": {"type": _V, "name": "signal-X"},
             "total_logistic_output_signal": {"type": _V, "name": "signal-Y"},
             "available_construction_output_signal": {"type": _V, "name": "signal-Z"},
             "total_construction_output_signal": {"type": _V, "name": "signal-T"},
             "roboport_count_output_signal": {"type": _V, "name": "signal-R"},
             "circuit_parameters": {"instrument_id": 0, "note_id": 0,
                                    "signal_value_is_pitch": False,
                                    "stop_playing_sounds": False}}
#: the blueprint fields that say what and where an entity is, not how it is set: never compared
_PLACE = frozenset({"entity_number", "name", "position", "direction", "tags", "wires",
                    "quality", "type"})
#: conditions: one with no first signal never holds (``codec.RESET_CB``), as good as none
_CONDITIONS = ("circuit_condition", "logistic_condition")
#: the source's comparators, back from the game's
_COMPARATOR = {"<": "<", ">": ">", "=": "=", "≤": "<=", "≥": ">=", "≠": "!="}
_COLOUR = {1: "red", 3: "red", 2: "green", 4: "green"}


@dataclass
class Drift:
    key: str                      # the design entity's composed key
    name: str
    x: float
    y: float
    user: str | None              # the standing entity's last_user
    have: str                     # what stands, as describe() says it
    want: str                     # what the design has ("" for none)
    clears: bool = False          # the design has none, and a paste clears it (a request)
    wires: bool = False           # a difference in circuit wires (the wire pass sets them)
    #: 3.1 (fk adopt): the standing entity's differing fields, as the game exports them
    fields: dict = None

    def line(self):
        who = (f"{self.user} changed" if self.user else
               "Someone changed (the game names no one: a script, or the map editor)")
        where = f"{_fmt(self.x)},{_fmt(self.y)}"
        if self.wires:
            then = (f"the build wires it as designed: {self.want}" if self.want else
                    "the design has none: the build cuts them")
        elif self.want:
            then = f"the paste sets {self.want}"
        elif self.clears:
            then = "the design has none: the paste clears it"
        else:
            then = "the design has none: the paste leaves it"
        return f"{who} {self.name} at {where} ({_local(self.key)}): {self.have} ({then})"


def lua_read(surface, force, area, tag):
    """Lua that blueprints ``area`` and writes ``fk5/<tag>.json``: each blueprint entity with its
    position in the world, and its fields (wires among them, as the blueprint numbers them)."""
    x0, y0, x1, y1 = area
    return f"""
local s, f = game.get_surface({_q(surface)}), game.forces[{_q(force)}]
if not s or not f then rcon.print('no surface or force') return end
local inv = game.create_inventory(1)
inv.insert{{name = 'blueprint'}}
local st = inv[1]
local map = st.create_blueprint{{surface = s, force = f, area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}},
  include_trains = false, include_fuel = false}} or {{}}
local at = {{}}
for n, e in pairs(map) do
  if e.valid and e.type ~= 'entity-ghost' then at[n] = {{name = e.name, x = e.position.x, y = e.position.y}} end
end
local rows = {{}}
for _, b in pairs(st.is_blueprint_setup() and st.get_blueprint_entities() or {{}}) do
  local w = at[b.entity_number]
  if w then rows[#rows + 1] = {{n = b.entity_number, name = w.name, x = w.x, y = w.y, e = b}} end
end
inv.destroy()
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({{tick = game.tick, rows = rows}}), false)
rcon.print(#rows)
"""


def _q(text):
    return "'" + str(text).replace("\\", "\\\\").replace("'", "\\'") + "'"


def read(r, surface, force, area, tag="settings"):
    """The standing entities' settings in ``area`` (``live``)."""
    import os
    from . import link
    tag = f"{tag}-{os.getpid()}"
    return live(link.read_output(r, lua_read(surface, force, area, tag), tag, "settings",
                                 keep=False))


def _at(name, x, y):
    return (name, round(float(x), 3), round(float(y), 3))


def _clean(v):
    """Fields as compared: the game's defaults, empty values and a normal quality dropped; an item
    signal without its type gets it (``codec``)."""
    if isinstance(v, dict):
        out = {}
        for k, x in v.items():
            if k in _DEFAULTS and codec._normal(x, k) == codec._normal(_DEFAULTS[k], k):
                continue
            if k in _CONDITIONS and isinstance(x, dict) and not x.get("first_signal"):
                continue
            if k == "quality" and x == "normal":
                continue
            if k == "index" and set(v) <= {"index"}:
                continue
            x = _clean(x)
            if not _empty(x):
                out[k] = x
        return out
    if isinstance(v, list):
        return [x for x in (_clean(x) for x in v) if not _empty(x)]
    if isinstance(v, float):
        v = round(v, 6)
        return int(v) if v.is_integer() else v
    return v


def _empty(x):
    """None, False, [] or {} (not 0: a limit of 0 slots, a condition against 0)."""
    return x is None or x is False or x == [] or x == {}


def _fields(entity, extra=()):
    """The entity's settings as compared: ``KEYS`` and ``extra`` (the design's ``bp.`` fields,
    2.1); ``extra=None`` keeps every field but those that place it (what stands, which the
    design's fields pick from)."""
    if extra is None:
        got = {k: x for k, x in entity.items() if k not in _PLACE and k not in _VECTORS}
    else:
        got = {k: entity[k] for k in tuple(KEYS) + tuple(extra) if k in entity}
    vec = armsmod.from_blueprint(entity.get("name"), entity.get("direction") or 0,
                                 {k: entity[k] for k in _VECTORS if k in entity})
    if vec:
        got["vectors"] = f"pick={vec['pick']} drop={vec['drop']}"
    return codec._normal(_clean(got))


def live(data):
    """{at: (fields, wires)} from ``lua_read``'s file; wires {(own connector, other at, its
    connector)} without copper ones."""
    rows = data.get("rows") or []
    by_n = {int(r["n"]): _at(r["name"], r["x"], r["y"]) for r in rows}
    out = {}
    for r in rows:
        e = r.get("e") or {}
        mine, wires = int(r["n"]), set()
        for w in e.get("wires") or ():
            a, ca, b, cb = (int(v) for v in w)
            if ca in codec.COPPER or cb in codec.COPPER:
                continue
            # both ifs: a combinator's wire from its own output to its own input (a latch, 2.1)
            # has the entity at both ends, and the design counts it from each
            if a == mine and b in by_n:
                wires.add((ca, by_n[b], cb))
            if b == mine and a in by_n:
                wires.add((cb, by_n[a], ca))
        out[by_n[mine]] = (_fields(e, None), wires)
    return out


def design(rows, s):
    """{at: (fields, wires)} of the codec rows the paste writes, with the surface's circuit
    wires among them (``w`` records)."""
    out, ats = {}, []
    for i, row in enumerate(rows):
        e = codec.entity_json(row, i + 1)
        ats.append(_at(row["name"], row["x"], row["y"]))
        _cb, bp = codec.raw_fields(row.get("attrs") or {}, row.get("src"))
        out[ats[-1]] = (_fields(e, sorted(bp)), set())
    for a, ca, b, cb in codec.wire_list(rows, None, codec.links_of(rows, getattr(s, "wires", ()))):
        if ca in codec.COPPER or cb in codec.COPPER:
            continue
        out[ats[a - 1]][1].add((ca, ats[b - 1], cb))
        out[ats[b - 1]][1].add((cb, ats[a - 1], ca))
    return out


def drifts(want, have, verdicts, users):
    """The design's entities that stand (a diff's ok or settings-changed verdicts) with other settings than
    ``want`` (``design``) has, in ``have`` (``live``): [Drift]. ``users``: {at: last_user}. Wires
    count only between entities both sides judge."""
    judged = {_at(v.name, v.x, v.y): v for v in verdicts if v.verdict in ("ok", "settings-changed")}
    both = {k for k in judged if k in want and k in have}
    out = []
    for k in sorted(both, key=lambda k: (fkpmod.natural_key(judged[k].key or ""), k)):
        (wf, ww), (hf, hw) = want[k], have[k]
        ww = {w for w in ww if w[1] in both}
        hw = {w for w in hw if w[1] in both}
        # 2.1: the design's bp. fields too, where it sets them
        keys = [f for f in list(KEYS) + sorted(set(wf) - set(KEYS)) if wf.get(f) != hf.get(f)]
        v = judged[k]
        if keys:
            w = describe({f: wf[f] for f in keys if f in wf})
            # 2.1: the codec writes every setting (codec.RESET_CB), so a paste clears what the
            # design has none of, but on a drill or an offshore pump (laid fresh instead)
            clears = not w
            out.append(Drift(v.key, v.name, v.x, v.y, users.get(k),
                             describe({f: hf[f] for f in keys if f in hf}) or "nothing set", w,
                             clears, fields={f: hf[f] for f in keys if f in hf}))
        if ww != hw:
            # a wire the design lacks stays after a paste (the laptop server, 2026-10-24): the
            # apply's wire pass cuts it (2.1)
            out.append(Drift(v.key, v.name, v.x, v.y, users.get(k), describe({}, hw),
                             describe({}, ww) if ww else "", True, True))
    return out


def describe(fields, wires=None):
    """Settings in a few plain words, in the source's terms where it has them (``if=``,
    ``logistic=``, ``filter=``, ``read=``)."""
    parts = []
    if fields.get("recipe"):
        parts.append(f"recipe {fields['recipe']}")
    cb = dict(fields.get("control_behavior") or {})
    for key, word in (("circuit_condition", "if"), ("logistic_condition", "logistic")):
        cond = cb.pop(key, None)
        on = cb.pop("circuit_enabled" if word == "if" else "connect_to_logistic_network", None)
        if cond or on:
            parts.append(f"{word}={_condition(cond)}" if cond else f"{word} on, no condition")
    if cb.pop("circuit_set_filters", None):
        parts.append("filter=circuit")
    for key, word in (("read_temperature", "temperature"), ("read_ingredients", "ingredients"),
                      ("read_contents", "contents")):
        if cb.pop(key, None):
            parts.append(f"read={word}")
    if cb.pop("circuit_read_hand_contents", None):
        mode = cb.pop("circuit_contents_read_mode", None)
        parts.append({0: "read=pulse", 1: "read=hold", 2: "read=hold-all"}.get(mode, "read=hand"))
    sections = cb.pop("sections", None)
    if sections:
        sig = [f"{f.get('name')}:{f.get('count')}" for s in sections.get("sections") or ()
               for f in s.get("filters") or ()]
        parts.append("signals=" + "|".join(sig))
    for key in sorted(cb):
        # 2.1: any other control behaviour field, as a raw cb. setting writes it
        parts.append(f"cb.{key}={codec.config_text(cb[key])}")
    for key in sorted(k for k in fields if k not in KEYS):
        # 2.1: a raw bp. field, as the source writes it
        parts.append(f"bp.{key}={codec.config_text(fields[key])}")
    if fields.get("filters") or fields.get("use_filters"):
        names = [f.get("name") for f in fields.get("filters") or () if f.get("name")]
        mode = " (blacklist)" if fields.get("filter_mode") == "blacklist" else ""
        parts.append("filter=" + ("|".join(names) or "none") + mode)
    req = fields.get("request_filters")
    if req:
        got = [f"{f.get('name')} {f.get('count', 0)}" for s in req.get("sections") or ()
               for f in s.get("filters") or () if f.get("name")]
        parts.append("requests " + (", ".join(got) or "none"))
    if fields.get("bar") is not None:
        parts.append(f"limit {fields['bar']} slots")
    if fields.get("vectors"):
        parts.append(fields["vectors"])
    if wires is not None:
        parts.append(", ".join(f"{_COLOUR.get(c, c)} wire to {o[0]} at {_fmt(o[1])},{_fmt(o[2])}"
                               for c, o, _oc in sorted(wires)) or "no circuit wires")
    return "; ".join(parts)


def _condition(c):
    a = (c.get("first_signal") or {}).get("name", "?")
    op = _COMPARATOR.get(c.get("comparator", "<"), c.get("comparator", "<"))
    b = (c.get("second_signal") or {}).get("name") if c.get("second_signal") else c.get("constant", 0)
    return f"{a}{op}{b}"


def _fmt(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else repr(v)


def _local(key):
    return (key or "").split(":", 1)[-1]
