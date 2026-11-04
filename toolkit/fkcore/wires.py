"""fkcore.wires: the world's wires matched to the design's, quietly (2.1; Rory, 2026-10-31: adding
and removing wires by script is fair).

A paste lays the wires its blueprint has and never takes one away (measured on a laptop server,
2.0.77): a wire made by hand stays, and so does one a pole made itself. A pole that a robot (or a
player) builds joins itself to the poles in its reach, whatever the blueprint said, so two power
networks the design keeps apart become one. The wire pass puts the design's wires back:

- it reads every red, green and copper wire of the built entities in an area (``lua_read``);
- the design's wires are the circuit wires (``w`` records and ``wire`` attrs) and the copper wires
  ``codec.pole_wires`` derives, over the whole design (so an apply of one plot never cuts what an
  apply of the surface laid);
- it adds the design's wires that the game lacks between two built design entities, and cuts the
  ones the game has between two design entities that the design lacks. A wire with an end outside
  the design (a ``Literal``'s neighbour, the world's own furniture, a pole left up as scaffolding)
  is left alone, and so is a ghost: the paste wires its ghosts itself.

The apply runs it after each paste, and ``fk wait built`` once the robots are done. It reports a
count line (``wires: 3 added, 1 cut``), not a check. A circuit wire it cuts was made by hand (only
a player or a script makes one), so ``Pass.hand`` names each; a copper wire is a pole's own more
often than a hand's, and is cut without a word. Every game call is one RCON command; a wire is
added with the game's reach check, as a player would make it. Stdlib only.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from . import codec
from . import link

__all__ = ["Pass", "design_wires", "world_wires", "plan", "lua_read", "lua_edit", "read",
           "run", "COLOUR"]

#: connector ids -> the wire's colour, as a line says it
COLOUR = {1: "red", 2: "green", 3: "red", 4: "green", 5: "copper", 6: "copper"}
#: wire edits per RCON command
BATCH = 30


@dataclass
class Pass:
    added: int = 0
    cut: int = 0
    refused: int = 0                  # the game would not make or cut it (out of reach)
    waiting: int = 0                  # an end is not built yet (a ghost): the next pass
    hand: list = field(default_factory=list)   # the circuit wires cut, a line each
    error: str | None = None

    def line(self):
        """The report's count line, or None when nothing changed."""
        if self.error:
            return f"wires: not matched ({self.error})"
        if not (self.added or self.cut or self.refused):
            return None
        text = f"wires: {self.added} added, {self.cut} cut"
        if self.refused:
            text += f", {self.refused} the game refused (out of reach)"
        return text


def _end(name, x, y, c):
    return (name, round(float(x), 3), round(float(y), 3), int(c))


def _wire(a, b):
    return (a, b) if a <= b else (b, a)


def design_wires(rows, s, cat, poles=None):
    """{wire}: each wire ``((name, x, y, connector), (name, x, y, connector))``, the lower end
    first, of the design rows ``rows`` (all of the surface's): their circuit wires and the copper
    wires between their poles (``poles``: [(i, j)], ``codec.pole_wires`` when not given)."""
    out = set()
    if poles is None:
        poles = codec.pole_wires(rows, cat)
    for a, ca, b, cb in codec.wire_list(rows, poles,
                                        codec.links_of(rows, getattr(s, "wires", ()))):
        ra, rb = rows[a - 1], rows[b - 1]
        out.add(_wire(_end(ra["name"], ra["x"], ra["y"], ca), _end(rb["name"], rb["x"], rb["y"], cb)))
    return out


def world_wires(data):
    """{wire} from ``lua_read``'s file (each wire is read from both ends, and kept once)."""
    out = set()
    for an, ax, ay, ac, bn, bx, by, bc in data.get("w") or ():
        out.add(_wire(_end(an, ax, ay, ac), _end(bn, bx, by, bc)))
    return out


def _inside(end, area):
    return area[0] <= end[1] < area[2] and area[1] <= end[2] < area[3]


def plan(want, have, ends, area):
    """-> (add, cut): the design's wires ``want`` with an end in ``area`` that the game's ``have``
    lacks, and the game's wires between two design entities (``ends``: {(name, x, y)}) that the
    design lacks."""
    add = sorted(w for w in want - have if _inside(w[0], area) or _inside(w[1], area))
    cut = sorted(w for w in have - want if w[0][:3] in ends and w[1][:3] in ends)
    return add, cut


def lua_read(surface, area, tag):
    """Lua that writes ``fk5/<tag>.json``: every wire a player could see (origin player) of the
    built entities in ``area``, [name, x, y, connector, other's name, x, y, connector]."""
    return f"""
local s = game.get_surface({link._q(surface)})
if not s then rcon.print('no surface') return end
local P = defines.wire_origin.player
local out = {{}}
for _, e in pairs(s.find_entities_filtered{{area = {link._area(area)}}}) do
  if e.valid and e.type ~= 'entity-ghost' then
    local ok, cs = pcall(e.get_wire_connectors, false)
    if ok and cs then
      for id, c in pairs(cs) do
        for _, w in pairs(c.connections) do
          local t = w.target.owner
          if w.origin == P and t.valid and t.type ~= 'entity-ghost' then
            out[#out + 1] = {{e.name, e.position.x, e.position.y, id, t.name, t.position.x,
              t.position.y, w.target.wire_connector_id}}
          end
        end
      end
    end
  end
end
helpers.write_file('fk5/{tag}.json', helpers.table_to_json({{tick = game.tick, w = out}}), false)
rcon.print(#out)
"""


def lua_edit(surface, ops):
    """Lua that adds or cuts wires: ``ops`` [[\"add\"|\"cut\", name, x, y, connector, name, x, y,
    connector]], each end a built entity found by name and position. Replies JSON [1 done, 0
    refused, -1 an end not built] per op."""
    data = json.dumps(ops, separators=(",", ":"))
    return f"""
local s = game.get_surface({link._q(surface)})
local P = defines.wire_origin.player
local function find(n, x, y)
  for _, e in pairs(s.find_entities_filtered{{name = n, position = {{x, y}}}}) do
    if e.valid then return e end
  end
end
local out = {{}}
for i, o in ipairs(helpers.json_to_table({link._q(data)})) do
  local a, b = find(o[2], o[3], o[4]), find(o[6], o[7], o[8])
  local r = -1
  if a and b then
    r = 0
    local add = o[1] == 'add'
    local ok, done = pcall(function()
      local ca, cb = a.get_wire_connector(o[5], add), b.get_wire_connector(o[9], add)
      if not ca or not cb then return false end
      if add then return ca.is_connected_to(cb) or ca.connect_to(cb, true, P) end
      return ca.disconnect_from(cb, P)
    end)
    if ok and done then r = 1 end
  end
  out[i] = r
end
rcon.print(helpers.table_to_json(out))
"""


def read(r, surface, area, tag="wires"):
    """The game's wires in ``area`` ({wire})."""
    tag = f"{tag}-{os.getpid()}"
    return world_wires(link.read_output(r, lua_read(surface, area, tag), tag, "wires",
                                        keep=False))


def _say(w):
    (an, ax, ay, ac), (bn, bx, by, _bc) = w
    return (f"a {COLOUR.get(ac, 'circuit')} wire between {an} at {_fmt(ax)},{_fmt(ay)} and {bn} "
            f"at {_fmt(bx)},{_fmt(by)}")


def _fmt(v):
    v = float(v)
    return str(int(v)) if v.is_integer() else repr(v)


def run(r, s, cat, area, poles=None, tag="wires"):
    """The wire pass over ``area``: -> ``Pass``. ``poles``: (the whole design's codec rows, their
    copper wires), ``apply.design_poles(s, cat)``, worked out when not given."""
    rep = Pass()
    if area is None:
        return rep
    if poles is None:
        from .apply import design_poles
        poles = design_poles(s, cat)
    rows, edges = poles
    try:
        have = read(r, s.name, area, tag)
        want = design_wires(rows, s, cat, edges)
        ends = {(row["name"], round(float(row["x"]), 3), round(float(row["y"]), 3)) for row in rows}
        add, cut = plan(want, have, ends, area)
        ops = [["add", *a, *b] for a, b in add] + [["cut", *a, *b] for a, b in cut]
        got = []
        for i in range(0, len(ops), BATCH):
            reply = r.lua(lua_edit(s.name, ops[i:i + BATCH])).strip()
            part = json.loads(reply) if reply.startswith("[") else None
            if not isinstance(part, list) or len(part) != len(ops[i:i + BATCH]):
                raise link.LinkError(f"wire edits failed: {reply[:200]}")
            got += part
    except (link.LinkError, OSError, ValueError, KeyError, TypeError) as exc:
        rep.error = f"{type(exc).__name__}: {exc}"
        return rep
    for op, res in zip(ops, got):
        w = (tuple(op[1:5]), tuple(op[5:9]))
        if res == -1:
            rep.waiting += 1
        elif res == 0:
            rep.refused += 1
        elif op[0] == "add":
            rep.added += 1
        else:
            rep.cut += 1
            if w[0][3] not in codec.COPPER:
                rep.hand.append(f"{_say(w)}, which the design doesn't have: cut")
    return rep
