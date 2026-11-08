"""fkcore.mapctl: the live map's saves, loads, start-over and pause (3.0, ``fk map``).

Rory, 2026-11-02: "I also want the agent to be able to start over and save or load games", and
"also a pause". Runs on the game host, in the run's game folder (the link is the run's own
server), as root (it stops and starts that server):

    save NAME           the live map saved as ``play-NAME.zip`` beside the run's save
    saves               the run's start, its live save and every ``play-*`` save
    load NAME           the map now saved as ``play-before-load-STAMP``, then the server restarted
                        on NAME (``start`` is the run's start): everyone connected is dropped and
                        joins again
    restart             ``load start``, then the kickoff's setup (``fkcore.kickoff_setup``) if
                        that start is the bare map
    pause | resume      ``game.tick_paused``: the whole game stands still, or goes on

The server always starts the run's own save (``v1-RUN.zip``: what ``sb/current-map`` or
``v1.conf`` names), so a load is: stop the server, copy NAME over that file, start it. Nothing is
registered; each RCON call is one ``game`` statement or two.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

from . import link

__all__ = ["MapError", "Map"]

SB = "/home/computah/sb"
LIVE_SAVES = "/home/computah/factorio/server/factorio/saves"
UP_S = 90          # a Sea Block map takes ~30 s to load its mods and the save
DOWN_S = 40        # a server stopping writes its save first


class MapError(RuntimeError):
    pass


def _name(word):
    if not word or not re.fullmatch(r"[A-Za-z0-9_.-]+", word) or word in (".", ".."):
        raise MapError(f"a save's name is one plain word (letters, digits, - _ .), got {word!r}")
    return word


class Map:
    def __init__(self, game_dir, run=None, instance=None, saves=None, pack=None, rcon=None):
        game_dir = os.path.abspath(game_dir)
        self.run = run or os.path.basename(os.path.dirname(game_dir))
        self.instance = instance or ("sb" if os.path.exists(os.path.join(game_dir, "link.json"))
                                     else "live")
        self.saves = saves or (os.path.join(SB, "saves") if self.instance == "sb" else LIVE_SAVES)
        self.pack = pack
        self.rcon = rcon or link.Rcon         # a laptop test server's own, in the tests
        self.own = os.path.join(self.saves, f"v1-{self.run}.zip")
        self.start_save = os.path.join(self.saves, f"v1-{self.run}-start.zip")

    # -- reads and the pause ---------------------------------------------------------------------
    def lua(self, body):
        with self.rcon() as r:
            return r.lua(body).strip()

    def path(self, name):
        return self.start_save if name == "start" else \
            os.path.join(self.saves, f"play-{_name(name)}.zip")

    def list(self):
        rows = [("start", self.start_save), ("live", self.own)]
        for f in sorted(os.listdir(self.saves)):
            if f.startswith("play-") and f.endswith(".zip"):
                rows.append((f[len("play-"):-len(".zip")], os.path.join(self.saves, f)))
        out = []
        for name, p in rows:
            if os.path.exists(p):
                st = os.stat(p)
                out.append(f"  {name:28} {time.strftime('%m-%d %H:%M', time.gmtime(st.st_mtime))} "
                           f"UTC  {st.st_size // 1024} kB"
                           + ("  (the save the server runs: written when it stops)"
                              if name == "live" else ""))
        return ["map: saves of this run (fk map load NAME):"] + out

    def pause(self, on):
        got = self.lua(f"game.tick_paused = {'true' if on else 'false'} "
                       "rcon.print(game.tick .. ' ' .. tostring(game.tick_paused))").split()
        return [f"map: {'paused' if got[1:] == ['true'] else 'running'} at tick {got[0]}"
                + (" (fk map resume goes on)" if on else "")]

    # -- saves -----------------------------------------------------------------------------------
    def save(self, name):
        p = self.path(name)
        if name == "start":
            raise MapError("start is the run's own start; save under another name")
        tag = os.path.basename(p)[:-len(".zip")]
        before = os.path.getmtime(p) if os.path.exists(p) else 0
        tick = self.lua(f"game.server_save('{tag}') rcon.print(game.tick)")
        for _ in range(60):
            if os.path.exists(p) and os.path.getmtime(p) > before:
                time.sleep(1)
                return [f"map: saved as {name} at tick {tick}"]
            time.sleep(0.5)
        raise MapError(f"the server did not write {p}")

    # -- the server ------------------------------------------------------------------------------
    def _running(self):
        if self.instance == "sb":
            return subprocess.run(["pgrep", "-u", "computah", "-f", "--", "[-]-port 34198"],
                                  capture_output=True).returncode == 0
        return subprocess.run(["systemctl", "is-active", "--quiet", "factorio"]).returncode == 0

    def _stop(self):
        if self.instance == "sb":
            subprocess.run(["pkill", "-u", "computah", "-f", "--", "[-]-port 34198"])
        else:
            subprocess.run(["systemctl", "stop", "factorio"])
        t0 = time.time()
        while self._running():
            if time.time() - t0 > DOWN_S:
                raise MapError(f"the server did not stop within {DOWN_S} s")
            time.sleep(1)

    def _start(self):
        if self.instance == "sb":
            subprocess.run(["su", "computah", "-c",
                            f"cd {SB} && setsid sh start.sh > /dev/null 2>&1 < /dev/null &"])
        else:
            subprocess.run(["systemctl", "start", "factorio"])
        t0 = time.time()
        while time.time() - t0 < UP_S:
            try:
                return int(self.lua("rcon.print(game.tick)"))
            except (OSError, link.LinkError, ValueError):
                time.sleep(2)
        raise MapError(f"the server did not answer within {UP_S} s (its log: "
                       + (f"{SB}/server.log)" if self.instance == "sb" else "journalctl -u factorio)"))

    def load(self, name):
        src = self.path(name)
        if not os.path.exists(src):
            raise MapError(f"no save {name} (fk map saves)")
        out = []
        if self._running():
            out += self.save(f"before-load-{time.strftime('%m%d-%H%M%S', time.gmtime())}")
        self._stop()
        # the stopped server wrote its own save on the way out: the copy goes over it after
        shutil.copyfile(src, self.own)
        shutil.chown(self.own, "computah", "computah")
        tick = self._start()
        return out + [f"map: the server runs {name} again, at tick {tick}; anyone connected "
                      "joins again"]

    def restart(self):
        out = self.load("start")
        from . import kickoff_setup
        set_up = self.lua("rcon.print(tostring(storage.kickoff_setup))")
        if set_up == "nil":
            with self.rcon() as r:
                got = kickoff_setup.run(r, pack=self.pack or None)
            out += [f"map: kickoff {k}: {v}" for k, v in got.items()]
            out += self.save("start-set-up")
            out.append("map: the set-up start is saved as start-set-up too")
        else:
            out.append(f"map: the start was set up already ({set_up})")
        return out


def run(game_dir, verb, args, **kw):
    m = Map(game_dir, **kw)
    if verb == "save" and len(args) == 1:
        return m.save(args[0])
    if verb == "saves" and not args:
        return m.list()
    if verb == "load" and len(args) == 1:
        return m.load(args[0])
    if verb == "restart" and not args:
        return m.restart()
    if verb in ("pause", "resume") and not args:
        return m.pause(verb == "pause")
    raise MapError("fk map save NAME | saves | load NAME | restart | pause | resume")
