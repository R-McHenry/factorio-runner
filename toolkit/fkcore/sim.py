"""fkcore.sim: the speedrun planner, a second game of the run's own map on this machine (2.0.61).

Rory, 2026-10-29: "a speedrun planning tool for the player ... another session of the game they
can run with the same seed, and they should be able to run deterministic playback sequences of
blueprint + destruction actions, as well as save or restore a save state, and also generate a
state by pasting down buildings for free", and "tickrate acceleration should be a feature".

The planner is a headless Factorio server on this machine (the laptop's own Factorio, as
``tools/laptop_server.py`` runs it), kept in ``<run>\\sim``: its config, its write-data (the save
states are ``sim\\write\\saves\\NAME.zip``), its RCON password and ``sim\\state.json`` (the running
server's pid and ports). It starts from a save state: ``start`` is the run's own map as it stood
right after its kickoff (``fk sim fetch`` copies it from the game host), so it is the same seed and
the same first tick. It stays up between commands, and it never touches the live game.

The game in it stands still (``game.tick_paused``) except while ``run`` moves it on, so every
action lands on a known tick and the same save with the same actions gives the same game, every
time (Factorio is deterministic). ``speed`` sets ``game.speed``: the planner runs ticks as fast as
the machine allows, not in real time.

    start [NAME]        the server from save state NAME (default start), paused
    stop | status
    save NAME | load NAME | saves
    speed X             ticks run X times as fast (game.speed)
    run N | until T     move the game on N ticks, or to tick T
    place FILE @X,Y     a blueprint (.txt, .json {"bp": ...}) or a laid-out design (.fkp) built at
                        once and free: no robots, no items; the modules it asks for put in
    paste FILE @X,Y     the same as ghosts, for the game's robots to build
    remove X0,Y0,X1,Y1  everything there gone at once (not characters, not resources)
    decon X0,Y0,X1,Y1   everything there ordered out (the robots take it up)
    play SCRIPT         a playback: those actions in order, each at its tick (``play_lines``)
    explore on | next | status | off | archive | best
                        Go-Explore (2.0.64, ``fkcore.explore``): the planner picks a cell to start
                        from and a goal cell, records what you do, and keeps every new cell and
                        every faster way to one

A position ``@X,Y`` is added to the blueprint's own coordinates (a design's are its factory's).
Pure but for the server: stdlib, the toolkit's link, codec and catalog.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import socket
import subprocess
import time

from . import codec, link

__all__ = ["SimError", "Sim", "play_lines", "EXE"]

EXE = os.environ.get("FK_LAPTOP_FACTORIO",
                     r"C:\Program Files (x86)\Steam\steamapps\common\Factorio\bin\x64\factorio.exe")
BOOT_S = 60
SURFACE, FORCE = "nauvis", "player"
#: ``start --spectate``: the port Rory's client joins (a fixed one, the server listed on the LAN)
SPECTATE_PORT = 34199


class SimError(RuntimeError):
    pass


def _free_port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
                             capture_output=True, text=True).stdout
        return str(int(pid)) in out
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


class Sim:
    """The planner of the run folder ``run`` (its server under ``run\\sim``)."""

    def __init__(self, run, exe=EXE):
        self.root = os.path.join(os.path.abspath(run), "sim")
        self.write = os.path.join(self.root, "write")
        self.saves = os.path.join(self.write, "saves")
        self.state_file = os.path.join(self.root, "state.json")
        self.pw_file = os.path.join(self.root, "rcon.pw")
        self.exe = exe

    # -- files and the server ---------------------------------------------------------------------
    def _files(self, spectate=False):
        os.makedirs(self.saves, exist_ok=True)
        with open(os.path.join(self.root, "config.ini"), "w", encoding="utf-8") as fh:
            fh.write("[path]\nread-data=__PATH__executable__/../../data\n"
                     f"write-data={self.write.replace(os.sep, '/')}\n")
        with open(os.path.join(self.root, "server-settings.json"), "w", encoding="utf-8") as fh:
            json.dump({"name": "fk speedrun planner", "description": "the run's own map, planned",
                       "visibility": {"public": False, "lan": bool(spectate)},
                       "require_user_verification": False, "max_players": 0,
                       "auto_pause": False, "autosave_interval": 0}, fh)
        if not os.path.exists(self.pw_file):
            with open(self.pw_file, "w", encoding="utf-8") as fh:
                fh.write(os.urandom(12).hex())

    def state(self):
        try:
            with open(self.state_file, encoding="utf-8") as fh:
                st = json.load(fh)
        except (OSError, ValueError):
            return None
        return st if _alive(st.get("pid")) else None

    def save_path(self, name):
        return os.path.join(self.saves, f"{name}.zip")

    def names(self):
        if not os.path.isdir(self.saves):
            return []
        return sorted(f[:-4] for f in os.listdir(self.saves) if f.endswith(".zip"))

    def start(self, name="start", spectate=False):
        """The server from save state ``name``, paused. ``spectate``: on ``SPECTATE_PORT`` and
        listed on the LAN, for a client to join and watch (at a speed it can keep up with: a
        client simulates every tick too). -> a report line."""
        st = self.state()
        if st is not None:
            return f"sim: already running from {st['save']} (fk sim stop first, or fk sim load)"
        if not os.path.exists(self.save_path(name)):
            raise SimError(f"no save state {name} in {self.saves}"
                           + (" (fk sim fetch copies the run's start)" if name == "start" else ""))
        if not os.path.exists(self.exe):
            raise SimError(f"no Factorio at {self.exe} (FK_LAPTOP_FACTORIO names another)")
        self._files(spectate)
        port = SPECTATE_PORT if spectate else _free_port(socket.SOCK_DGRAM)
        rcon_port = _free_port()
        with open(self.pw_file, encoding="utf-8") as fh:
            pw = fh.read().strip()
        args = [self.exe, "--config", os.path.join(self.root, "config.ini"), "--port", str(port),
                "--rcon-bind", f"127.0.0.1:{rcon_port}", "--rcon-password", pw,
                "--server-settings", os.path.join(self.root, "server-settings.json"),
                "--start-server", self.save_path(name)]
        log = open(os.path.join(self.root, "server.log"), "w", encoding="utf-8")
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        proc = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, creationflags=flags)
        st = {"pid": proc.pid, "port": port, "rcon_port": rcon_port, "save": name,
              "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "spectate": bool(spectate)}
        with open(self.state_file, "w", encoding="utf-8") as fh:
            json.dump(st, fh)
        t0 = time.time()
        while time.time() - t0 < BOOT_S:
            if proc.poll() is not None:
                raise SimError(f"the server exited ({proc.returncode}): "
                               f"{os.path.join(self.root, 'server.log')}")
            try:
                # a save keeps its speed; one being watched starts at real time
                got = self.lua("local was = game.tick_paused game.tick_paused = true "
                               + ("game.speed = 1 " if spectate else "")
                               + "rcon.print(game.tick .. ' ' .. tostring(was))").strip().split()
                if got and got[0].isdigit():
                    tick = got[0]
                    line = (f"sim: running from save state {name}, paused at tick {tick}"
                            + (f"; join to watch at {self.address()}" if spectate else ""))
                    if got[1:] == ["false"]:
                        # 2.0.64: a save written by a running server (the fetched start, a
                        # fetch --live, the live game's own) isn't paused, so every boot ran on
                        # until this call paused it, at its saved speed: tens to hundreds of
                        # ticks, a different number each time (the opus player saw it). Saved
                        # again now, paused, it loads at this tick every time
                        self.save(name)
                        line += (f" (it was saved running, so a boot ran on to here; saved again "
                                 f"paused, it loads at tick {tick} from now on)")
                    return line
            except (OSError, link.LinkError):
                pass
            time.sleep(1)
        self.stop()
        raise SimError(f"no RCON answer within {BOOT_S} s: {os.path.join(self.root, 'server.log')}")

    def stop(self):
        st = self.state()
        if st is None:
            return "sim: not running"
        if os.name == "nt":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(st["pid"])], capture_output=True)
        else:
            os.kill(int(st["pid"]), 9)
        for _ in range(20):
            if not _alive(st["pid"]):
                break
            time.sleep(0.25)
        return f"sim: stopped (it ran from {st['save']}; unsaved ticks are gone)"

    def address(self):
        """Where a client joins the planner: this machine's LAN address and the server's port."""
        st = self.state()
        if st is None:
            return None
        ip = "127.0.0.1"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("192.168.1.1", 9))          # no packet is sent: it picks the route
                ip = s.getsockname()[0]
            except OSError:
                pass
        return f"{ip}:{st['port']}"

    def rcon(self, timeout=30.0):
        st = self.state()
        if st is None:
            raise SimError("the planner is not running (fk sim start)")
        return link.Rcon("127.0.0.1", st["rcon_port"], self.pw_file, timeout=timeout)

    def lua(self, text, timeout=30.0):
        with self.rcon(timeout) as r:
            return r.lua(text)

    def env(self):
        """The link settings that point any fkcore game command at the planner."""
        st = self.state()
        if st is None:
            raise SimError("the planner is not running (fk sim start)")
        return {"FK_RCON_HOST": "127.0.0.1", "FK_RCON_PORT": str(st["rcon_port"]),
                "FK_RCON_PW_FILE": self.pw_file,
                "FK_SCRIPT_OUTPUT": os.path.join(self.write, "script-output")}

    # -- save states ------------------------------------------------------------------------------
    def save(self, name):
        if not name or any(c in name for c in "\\/:*?\"<>| "):
            raise SimError(f"a save state's name is one word, got {name!r}")
        path = self.save_path(name)
        before = os.path.getmtime(path) if os.path.exists(path) else 0
        tick = self.lua(f"game.server_save('{name}') rcon.print(game.tick)").strip()
        for _ in range(60):
            if os.path.exists(path) and os.path.getmtime(path) > before:
                time.sleep(0.5)
                return f"sim: saved state {name} at tick {tick}"
            time.sleep(0.5)
        raise SimError(f"the game did not write {path}")

    def load(self, name):
        if self.archive().on:
            raise SimError("explore mode picks the state: fk sim explore next for another "
                           "episode, or fk sim explore off")
        if not os.path.exists(self.save_path(name)):
            raise SimError(f"no save state {name} (fk sim saves)")
        st = self.state()
        self.stop()
        return self.start(name, spectate=bool(st and st.get("spectate")))

    def add_save(self, src, name):
        """Copy a save file in as save state ``name`` (the run's start, fetched from the host)."""
        os.makedirs(self.saves, exist_ok=True)
        shutil.copyfile(src, self.save_path(name))
        return f"sim: save state {name} from {src}"

    # -- time ---------------------------------------------------------------------------------------
    def tick(self):
        return int(self.lua("rcon.print(game.tick)").strip())

    def speed(self, x):
        x = float(x)
        if not 0.01 <= x <= 1000:
            raise SimError(f"speed {x}: from 0.01 to 1000 (1 is real time)")
        return self.lua(f"game.speed = {x} rcon.print('sim: speed ' .. game.speed)").strip()

    def run(self, ticks=None, until=None, wait_s=None):
        """Move the game on ``ticks`` ticks (or to tick ``until``), then stand still again.
        -> (the tick it stands at, a report line). In explore mode it runs in chunks and checks
        the cell after each (``_explore_run``)."""
        now = self.tick()
        n = int(ticks) if ticks is not None else int(until) - now
        if n < 0:
            raise SimError(f"tick {until} is past: the game is at {now}")
        if n == 0:
            return now, f"sim: at tick {now}"
        arch = self.archive()
        if arch.on and arch.episode is not None:
            return self._explore_run(arch, now, n, wait_s)
        return self._run(now, n, wait_s)

    def _run(self, now, n, wait_s=None):
        speed = float(self.lua("rcon.print(game.speed)").strip() or 1)
        budget = wait_s if wait_s is not None else max(30.0, n / 60.0 / max(speed, 0.01) * 3 + 30)
        self.lua(f"game.tick_paused = true game.ticks_to_run = {n} rcon.print('ok')")
        t0 = time.time()
        while time.time() - t0 < budget:
            left = int(self.lua("rcon.print(game.ticks_to_run)").strip() or 0)
            if left == 0:
                at = self.tick()
                return at, f"sim: ran {n} ticks to tick {at} in {time.time() - t0:.1f} s"
            time.sleep(0.2 if left < 600 else 1.0)
        raise SimError(f"the game did not run {n} ticks within {budget:.0f} s")

    # -- building -----------------------------------------------------------------------------------
    def _rows(self, path, cat):
        from . import fkp
        if path.endswith(".fkp"):
            return codec.rows_of(fkp.load(path))
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
        if path.endswith(".json"):
            text = json.loads(text)["bp"]
        rows, _wires = codec.read_blueprint(text)
        return rows

    def _built(self, path, at, cat, free):
        from . import apply as applymod
        rows = [dict(r, x=float(r["x"]) + at[0], y=float(r["y"]) + at[1])
                for r in self._rows(path, cat)]
        if not rows:
            raise SimError(f"{path}: no entities in it")
        bp = codec.blueprint(rows, wires=codec.pole_wires(rows, cat))
        x0, y0, x1, y1 = applymod.scope_area(rows, cat, margin=0)
        area = (x0 - 1, y0 - 1, x1 + 1, y1 + 1)
        with self.rcon() as r:
            r.lua(link.lua_generate_area(SURFACE, FORCE, area))
            r.lua(f"game.forces['{FORCE}'].chart(game.get_surface('{SURFACE}'), "
                  f"{{{{{area[0]}, {area[1]}}}, {{{area[2]}, {area[3]}}}}}) rcon.print('ok')")
            placed = link.paste(r, SURFACE, FORCE, bp, ((x0 + x1) / 2, (y0 + y1) / 2)).strip()
            if not free:
                return f"sim: pasted {len(rows)} entities as ghosts at {at[0]},{at[1]} ({placed} new)"
            # free: what the paste ordered out of its way (trees, rocks, what stood there) goes at
            # once, then every ghost is built, then the modules it asks for are put in
            cleared = r.lua(_lua_clear_marked(area)).strip()
            r.lua(_lua_tiles(area))                  # landfill the paste asks for on water
            revived = r.lua(link.lua_revive(SURFACE, area)).strip().split()
            modules = r.lua(_lua_fill(area)).strip()
            left = r.lua(f"rcon.print(game.get_surface('{SURFACE}').count_entities_filtered"
                         f"{{area = {{{{{area[0]}, {area[1]}}}, {{{area[2]}, {area[3]}}}}}, "
                         "type = 'entity-ghost'})").strip()
        built = int(revived[0]) if revived else 0
        short = len(rows) - built
        why = (f"; {short} not: the game refuses them there (a cliff, an enemy base, a drill off "
               f"its ore), as it would in the run; fk sim remove clears an area first"
               if short > 0 else "")
        return (f"sim: placed {built} of {len(rows)} entities at {at[0]},{at[1]}, free "
                f"({cleared} trees or rocks in the way removed, {modules} modules put in, "
                f"{left} ghosts left){why}")

    def place(self, path, at, cat):
        self._fair("place")
        return self._built(path, at, cat, True)

    def paste(self, path, at, cat):
        got = self._built(path, at, cat, False)
        arch = self.archive()
        if arch.on and arch.episode is not None:
            arch.record(self.tick(), "paste", [arch.keep_file(os.path.abspath(path)), at[0], at[1]])
            arch.write()
        return got

    def remove(self, area):
        self._fair("remove")
        return self.lua(f"""
local s = game.get_surface('{SURFACE}')
local n = 0
for _, e in pairs(s.find_entities_filtered{{area = {{{{{area[0]}, {area[1]}}}, {{{area[2]}, {area[3]}}}}}}}) do
  if e.valid and e.type ~= 'character' and e.type ~= 'resource' then e.destroy() n = n + 1 end
end
rcon.print('sim: removed ' .. n .. ' entities')
""").strip()

    def decon(self, area):
        n = self.lua(link.lua_decon(SURFACE, FORCE, area)).strip()
        arch = self.archive()
        if arch.on and arch.episode is not None:
            arch.record(self.tick(), "decon", list(area))
            arch.write()
        return f"sim: {n} entities ordered out"

    # -- explore (2.0.64, fkcore.explore) -------------------------------------------------------------
    def archive(self):
        from . import explore
        return explore.Archive(self.root)

    def _fair(self, verb):
        """In explore mode a trajectory is what the live game could do: ghosts and robots,
        decon, research and Lua. A free build or a free removal would make its cells worthless."""
        arch = self.archive()
        if arch.on:
            raise SimError(f"{verb} is off in explore mode (a free build makes the cells worthless "
                           "as a speedrun): paste ghosts for the robots, decon for taking up; "
                           "fk sim explore off ends the mode")

    def made(self):
        """{tick, made: {item: produced}, can: [items the force can make]} from the game."""
        from . import explore
        got = json.loads(self.lua(explore.LUA_MADE).strip() or "{}")
        made = got.get("made") or {}
        return {"tick": int(got.get("tick", 0)), "made": made if isinstance(made, dict) else {},
                "can": list(got.get("can") or []),
                "researched": list(got.get("researched") or [])}

    def tech_tree(self, arch):
        """The game's tech tree (``fkcore.techcost``), read once into the archive's folder
        (prototypes don't change while the game runs). -> a techcost.Tree."""
        from . import techcost
        path = os.path.join(arch.root, "techtree.json")
        if os.path.exists(path):
            tree = techcost.Tree.load(path)
            # a 2.0.66 read has no recipe amounts, and worth (2.0.67) needs them: read it again
            if any(isinstance(r.get("ina"), dict) and r["ina"] for r in tree.recipes.values()):
                return tree
            os.remove(path)
        if not os.path.exists(path):
            tag = f"techtree-{os.getpid()}"
            got = self.lua(techcost.LUA_DUMP.replace("{tag}", tag), timeout=60).strip()
            src = os.path.join(self.write, "script-output", "fk5", f"{tag}.json")
            if got != "1" or not os.path.exists(src):
                raise SimError(f"the tech tree read failed: {got[:200]}")
            os.makedirs(arch.root, exist_ok=True)
            shutil.move(src, path)
        return techcost.Tree.load(path)

    def _explore_check(self, arch):
        """The cell now, offered to the archive, and the round's manifest brought up to date.
        Silent: the player sees the manifest, never the cells (2.0.65)."""
        from . import explore
        st = arch.settings
        got = self.made()
        levels = explore.cell(got["made"], float(st["base"]))
        ep = arch.episode
        k = explore.key(levels)
        path = ep.setdefault("path", [ep["go"]])
        if path[-1] != k:
            path.append(k)                  # every cell the round passes, in order (2.0.67)
        verdict = arch.offer(levels, got["tick"], ep["traj"], lambda name: self.save(name),
                             path=path[:-1])
        if verdict:
            (ep["found"] if verdict == "new" else ep["faster"]).append(explore.key(levels))
        if ep.get("reached") is None and explore.reached(levels, ep["goal"]):
            ep["reached"] = got["tick"]
        ep["now"] = levels
        ep["made"] = {i: got["made"].get(i, 0) for i in ep["goal"]}
        arch.write()

    def _explore_run(self, arch, now, n, wait_s):
        """``run`` in a round: chunks, the cell checked after each, and the round ended when its
        manifest is met, its budget of ticks spent or its minutes up (2.0.65, Rory: "it shouldn't
        make the agent wait until it gets to the goal cell"). Then, with ``auto``, the next round."""
        from . import explore
        st = arch.settings
        chunk = max(60, int(st["chunk"]))
        done, t0 = 0, time.time()
        why = explore.round_end(arch.episode, st, now, time.time())
        while why is None and done < n:
            left = explore.ticks_left(arch.episode, st, self.tick())
            step = min(chunk, n - done, left if left is not None else chunk)
            if step <= 0:
                break
            self._run(self.tick(), step, wait_s)
            done += step
            self._explore_check(arch)
            arch = self.archive()
            why = explore.round_end(arch.episode, st, self.tick(), time.time())
        at = self.tick()
        line = f"sim: ran {done} ticks to tick {at} in {time.time() - t0:.1f} s" + (
            f" (of the {n} asked)" if done < n else "")
        if why is None:
            return at, f"{line}; manifest: {arch.goal_text()}"
        ep = arch.episode
        ep["ended"] = why
        arch.write()
        out = [line, f"ROUND {ep['n']} OVER: {why}. Manifest: {arch.goal_text()}"]
        if int(st.get("auto", 1)):
            out += self.explore_next()
            at = self.tick()
        else:
            out.append("fk sim explore next starts the next round")
        return at, "\n".join(out)

    def explore_on(self, base=None, dist=None, budget=None, chunk=None, prior=None, seed=None,
                   minutes=None, auto=None, pick=None, rise=None, steps=None):
        """Explore mode on (settings given are kept), the archive started from save state
        ``start`` when it is empty, and an episode begun. -> report lines."""
        from . import explore
        arch = self.archive()
        st = dict(arch.data.get("settings") or {})
        for k, v in (("base", base), ("dist", dist), ("budget", budget), ("chunk", chunk),
                     ("prior", prior), ("minutes", minutes), ("auto", auto), ("pick", pick),
                     ("rise", rise), ("steps", steps)):
            if v is not None:
                st[k] = v
        if float(st.get("base", 2)) <= 1:
            raise SimError("--base is the log's base, above 1 (2 by default; smaller is finer)")
        if arch.cells and st.get("base") is not None and \
                float(st["base"]) != float(arch.settings["base"]):
            raise SimError(f"the archive's cells are at base {arch.settings['base']}: "
                           "fk sim explore reset starts another")
        if st.get("pick", "grid") not in ("grid", "uniform", "track"):
            raise SimError("--pick grid (the map's V x H bins alike, the default), uniform (every "
                           "cell alike) or track (V bands, near the best path)")
        if float(st.get("rise", 0.1)) <= 0:
            raise SimError("--rise is how far up V a goal is, above 0 (0.1 is about 7% more worth)")
        arch.data["settings"] = st
        arch.data["on"] = True
        out = []
        if not arch.cells:
            if not os.path.exists(self.save_path("start")):
                raise SimError("no save state start (fk sim fetch)")
            # the start as saved, never a game that has run on from it since. A save made by a
            # running server isn't paused, so each boot runs a few ticks before RCON pauses it;
            # the root is saved again here, paused, and loads at the same tick every time
            self.stop()
            self.start("start")
            self.save("x0")
            arch.data["root"] = "x0"
            got = self.made()
            levels = explore.cell(got["made"], float(arch.settings["base"]))
            arch.cells[explore.key(levels)] = {"levels": levels, "tick": got["tick"],
                                               "save": "x0", "traj": [], "visits": 0,
                                               "path": [explore.key(levels)],
                                               "found": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                               "episode": 0, "parent": None, "beaten": 0}
            out.append(f"explore: archive started from save state start, kept paused as x0 "
                       f"(tick {got['tick']}, {len(levels)} items made)")
        arch.write()
        return out + self.explore_next(seed)

    def explore_next(self, seed=None):
        """A new episode: a go cell loaded, a goal given. -> report lines."""
        from . import explore
        arch = self.archive()
        if not arch.on:
            raise SimError("explore mode is off (fk sim explore on)")
        rng = random.Random(seed)
        go = arch.pick_go(rng)
        row = arch.cells[go]
        self.stop()
        self.start(row["save"])
        self.lua("game.speed = 100 rcon.print('ok')")
        got = self.made()
        # 2.0.66: what each item costs in science from here, so a step far up the tech tree costs
        # the science levels the goal lacks for it
        tree = self.tech_tree(arch)
        costs = tree.costs(got["researched"])
        needs = {item: tree.packs(techs) for item, techs in costs.items()}
        # 2.0.68: the goal picked in coordinates (V risen by --rise, an H band); the 2.0.66 walk
        # when no walk rises at all
        goal = arch.pick_goal_vh(row["levels"], needs, got["made"], rng) or \
            arch.pick_goal(row["levels"], got["can"], arch.settings["dist"], rng,
                           needs=needs, made=got["made"])
        ep = arch.start_episode(go, goal, got["tick"])
        ep["now"] = row["levels"]
        ep["made"] = {i: got["made"].get(i, 0) for i in goal}
        arch.write()
        st = arch.settings
        ends = [f"its budget of {int(st['budget']):,} ticks"] if int(st["budget"] or 0) else []
        if float(st.get("minutes") or 0):
            ends.append(f"{float(st['minutes']):g} minutes of yours")
        return [f"ROUND {ep['n']}: a game loaded at tick {got['tick']:,}, paused, speed 100. Make "
                "as fast as you can, in all (counting what was made before):",
                f"  manifest: {arch.goal_text()}",
                "  the round ends when it's met" + (f", or after {' or '.join(ends)}"
                                                     if ends else "")
                + ", and the next one loads by itself. Paste, decon, fk sim game research / lua, "
                "and run",
                "  an item far off or out of reach for now: think of the way that gets to it in the "
                "end, and take the first step on it (the research it needs, the factory before it)"]

    def explore_off(self):
        arch = self.archive()
        arch.data["on"] = False
        arch.data["episode"] = None
        arch.write()
        return f"explore: off ({len(arch.cells)} cells kept in the archive; the game as it stands)"

    def explore_game(self, args):
        """Record a game command (``fk sim game research ...``) in the episode's trajectory."""
        arch = self.archive()
        if arch.on and arch.episode is not None:
            args = list(args)
            if args[:1] == ["lua"] and "--file" in args:
                i = args.index("--file")
                with open(args[i + 1], encoding="utf-8") as fh:
                    args = ["lua", fh.read()]
            arch.record(self.tick(), "game", args)
            arch.write()
            return True
        return False


def _lua_clear_marked(area):
    """Remove at once what a paste ordered out of its way (a free build has no robots)."""
    x0, y0, x1, y1 = area
    return f"""
local s = game.get_surface('{SURFACE}')
local n = 0
for _, e in pairs(s.find_entities_filtered{{area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}}, to_be_deconstructed = true}}) do
  if e.valid and e.type ~= 'character' then e.destroy() n = n + 1 end
end
rcon.print(n)
"""


def _lua_tiles(area):
    """Lay the tile ghosts a paste left (the landfill the game puts under a blueprint on water)."""
    x0, y0, x1, y1 = area
    return f"""
local s = game.get_surface('{SURFACE}')
local tiles = {{}}
for _, g in pairs(s.find_entities_filtered{{area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}}, type = 'tile-ghost'}}) do
  tiles[#tiles + 1] = {{name = g.ghost_name, position = g.position}}
  g.destroy()
end
if #tiles > 0 then s.set_tiles(tiles) end
rcon.print(#tiles)
"""


def _lua_fill(area):
    """Put into their slots the items the pasted machines ask for (their modules): a free build."""
    x0, y0, x1, y1 = area
    return f"""
local s = game.get_surface('{SURFACE}')
local n = 0
for _, p in pairs(s.find_entities_filtered{{area = {{{{{x0}, {y0}}}, {{{x1}, {y1}}}}}, name = 'item-request-proxy'}}) do
  local t = p.proxy_target
  if t and t.valid then
    for _, plan in pairs(p.insert_plan or {{}}) do
      for _, at in pairs((plan.items and plan.items.in_inventory) or {{}}) do
        local inv = t.get_inventory(at.inventory)
        local slot = inv and inv[at.stack + 1]
        if slot and slot.set_stack{{name = plan.id.name, count = at.count or 1,
                                   quality = plan.id.quality or 'normal'}} then
          n = n + (at.count or 1)
        end
      end
    end
    p.destroy()
  end
end
rcon.print(n)
"""


# -- playback -----------------------------------------------------------------------------------------
def _xy(word):
    if not word.startswith("@"):
        raise SimError(f"a position is @X,Y, got {word!r}")
    x, y = word[1:].split(",")
    return float(x), float(y)


def _area(word):
    v = [float(p) for p in word.split(",")]
    if len(v) != 4 or v[2] <= v[0] or v[3] <= v[1]:
        raise SimError(f"an area is X0,Y0,X1,Y1, got {word!r}")
    return tuple(v)


def play_lines(text):
    """A playback script -> [(line number, verb, args)]. One action a line, in order; ``#``
    starts a comment:

        from start          the save state it plays from (first, once; else the game as it stands)
        speed 100           ticks 100 times as fast
        at 3600             move on to tick 3600 (``at +600``: 600 ticks on)
        run 600             600 ticks on
        place mine.fkp @0,0   paste mine.fkp @0,0   remove X0,Y0,X1,Y1   decon X0,Y0,X1,Y1
        save after-mine     a save state, as the game stands
        game research automation    any fkcore game command, on the planner (2.0.64)
    """
    out = []
    verbs = {"from": 1, "speed": 1, "at": 1, "run": 1, "place": 2, "paste": 2, "remove": 1,
             "decon": 1, "save": 1, "game": None}
    for i, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        words = line.split()                 # a Windows path keeps its backslashes
        verb, args = words[0], words[1:]
        if verb not in verbs:
            raise SimError(f"line {i}: {verb}: a playback line is one of {', '.join(verbs)}")
        if verbs[verb] is None:
            if not args:
                raise SimError(f"line {i}: game COMMAND ARGS ... (research NAME, lua --file F)")
        elif len(args) != verbs[verb]:
            raise SimError(f"line {i}: {verb} takes {verbs[verb]} argument"
                           f"{'s' if verbs[verb] > 1 else ''}, got {len(args)}")
        if verb == "from" and out:
            raise SimError(f"line {i}: from goes first")
        if verb in ("place", "paste"):
            args = [args[0], _xy(args[1])]
        elif verb in ("remove", "decon"):
            args = [_area(args[0])]
        elif verb == "at":
            a = args[0]
            args = [("+", int(a[1:])) if a.startswith("+") else ("=", int(a))]
        elif verb in ("run",):
            args = [int(args[0])]
        out.append((i, verb, args))
    return out


def game_command(sim, args):
    """An fkcore game command (``research NAME``, ``lua --file F``) run on the planner, as
    ``fk sim game`` runs it. -> what it printed, on one line."""
    import sys
    game = os.path.join(os.path.dirname(sim.root), "game")
    env = dict(os.environ, FK_LINK="on", **sim.env())
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-m", "fkcore", args[0], "."] + list(args[1:]),
                       cwd=game if os.path.isdir(game) else None, env=env, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    out = " ".join((r.stdout + r.stderr).split())
    return f"game {args[0]}: {out[:200]}"


def play(sim, text, cat, base_dir="."):
    """Play a script on the planner (``play_lines``). -> the report lines, one per action, each
    with the tick it acted on."""
    steps = play_lines(text)
    lines = []
    for i, verb, args in steps:
        if verb == "from":
            lines.append(sim.load(args[0]) if sim.state() else sim.start(args[0]))
            continue
        tick = sim.tick()
        if verb == "speed":
            got = sim.speed(args[0])
        elif verb == "at":
            kind, t = args[0]
            got = sim.run(ticks=t)[1] if kind == "+" else sim.run(until=t)[1]
        elif verb == "run":
            got = sim.run(ticks=args[0])[1]
        elif verb in ("place", "paste"):
            path = args[0] if os.path.isabs(args[0]) else os.path.join(base_dir, args[0])
            got = (sim.place if verb == "place" else sim.paste)(path, args[1], cat)
        elif verb == "remove":
            got = sim.remove(args[0])
        elif verb == "decon":
            got = sim.decon(args[0])
        elif verb == "game":
            got = game_command(sim, args)
            sim.explore_game(args)
        else:
            got = sim.save(args[0])
        lines.append(f"[{i:>3}] tick {tick:>8}  {got}")
    return lines
