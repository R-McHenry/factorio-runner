"""A headless Factorio server for one episode, on a lockstep clock.

The game stands still (game.tick_paused) except while `run` moves it on, so every action lands on
a known tick and model latency costs no game time. Pausing, running and saving follow
factorio-playable's speedrun planner (fkcore/sim.py), including its fix for saves written by a
running server: they boot unpaused, so `start` pauses and saves again.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.request

from .rcon import Rcon, RconError

BOOT_S = 90
DOWNLOAD = "https://factorio.com/get-download/{version}/headless/linux64"


class ServerError(RuntimeError):
    pass


def ensure_factorio(version: str, cache: str) -> str:
    """The headless binary for `version` under `cache`, downloaded from factorio.com if missing.
    FACTORIO_BIN in the environment names one directly."""
    if os.environ.get("FACTORIO_BIN"):
        return os.environ["FACTORIO_BIN"]
    root = os.path.join(cache, f"factorio-{version}")
    exe = os.path.join(root, "factorio", "bin", "x64", "factorio")
    if os.path.exists(exe):
        return exe
    os.makedirs(cache, exist_ok=True)
    os.makedirs(root, exist_ok=True)
    tar = os.path.join(cache, f"factorio-headless-{version}.tar.xz")
    if not os.path.exists(tar):
        # factorio.com refuses urllib's default User-Agent (403)
        req = urllib.request.Request(DOWNLOAD.format(version=version),
                                     headers={"User-Agent": "factorio-runner"})
        with urllib.request.urlopen(req, timeout=600) as r, open(tar + ".part", "wb") as fh:
            shutil.copyfileobj(r, fh)
        os.replace(tar + ".part", tar)
    with tarfile.open(tar) as t:
        t.extractall(root, filter="tar")
    return exe


def _free_port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    """One server process under `root`: config, write-data, saves, log."""

    def __init__(self, exe: str, root: str, mods: str | None = None):
        self.exe = exe
        self.root = os.path.abspath(root)
        self.write = os.path.join(self.root, "write")
        self.saves = os.path.join(self.write, "saves")
        self.mods = mods
        self.proc: subprocess.Popen | None = None
        self.rcon: Rcon | None = None
        self.password = os.urandom(12).hex()
        os.makedirs(self.saves, exist_ok=True)
        with open(os.path.join(self.root, "config.ini"), "w") as fh:
            fh.write("[path]\nread-data=__PATH__executable__/../../data\n"
                     f"write-data={self.write}\n")
        with open(os.path.join(self.root, "server-settings.json"), "w") as fh:
            json.dump({"name": "runner", "description": "factorio-runner episode",
                       "visibility": {"public": False, "lan": False},
                       "require_user_verification": False, "max_players": 0,
                       "auto_pause": False, "autosave_interval": 0,
                       "only_admins_can_pause_the_game": True}, fh)

    def _base_args(self) -> list[str]:
        args = [self.exe, "-c", os.path.join(self.root, "config.ini")]
        if self.mods:
            args += ["--mod-directory", self.mods]
        return args

    def save_path(self, name: str) -> str:
        return os.path.join(self.saves, f"{name}.zip")

    # -- maps -----------------------------------------------------------------------------------
    def create(self, name: str, map_gen: str | None = None, map_settings: str | None = None,
               seed: int | None = None) -> str:
        args = self._base_args() + ["--create", self.save_path(name)]
        if map_gen:
            args += ["--map-gen-settings", map_gen]
        if map_settings:
            args += ["--map-settings", map_settings]
        if seed is not None:
            args += ["--map-gen-seed", str(seed)]
        out = subprocess.run(args, capture_output=True, text=True, timeout=300)
        if out.returncode != 0 or not os.path.exists(self.save_path(name)):
            raise ServerError(f"map create failed ({out.returncode}): {out.stdout[-2000:]}")
        return self.save_path(name)

    # -- process --------------------------------------------------------------------------------
    def start(self, save: str) -> int:
        """Start from save file `save` (a path), paused. -> the tick it stands at."""
        if self.proc is not None:
            raise ServerError("already running")
        if not os.path.isabs(save):
            save = self.save_path(save)
        self.rcon_port = _free_port()
        args = self._base_args() + [
            "--start-server", save, "--port", str(_free_port(socket.SOCK_DGRAM)),
            "--rcon-bind", f"127.0.0.1:{self.rcon_port}", "--rcon-password", self.password,
            "--server-settings", os.path.join(self.root, "server-settings.json")]
        self.log_path = os.path.join(self.root, "server.log")
        self._log = open(self.log_path, "w")
        self.proc = subprocess.Popen(args, stdout=self._log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL)
        t0 = time.time()
        while time.time() - t0 < BOOT_S:
            if self.proc.poll() is not None:
                self.proc = None
                raise ServerError(f"server exited: {self._log_tail()}")
            try:
                self.rcon = Rcon(self.rcon_port, self.password)
                break
            except OSError:
                time.sleep(0.5)
        else:
            self.stop()
            raise ServerError(f"no RCON within {BOOT_S}s: {self._log_tail()}")
        # The first /sc of a session can come back empty; repeat until the sentinel answers.
        for _ in range(10):
            got = self.lua("local was = game.tick_paused game.tick_paused = true "
                           "rcon.print('OK ' .. game.tick .. ' ' .. tostring(was))").split()
            if got[:1] == ["OK"]:
                if got[2:] == ["false"]:
                    # saved while running: it ran on a few ticks before this pause
                    self.save(os.path.splitext(os.path.basename(save))[0] + "-paused")
                return int(got[1])
            time.sleep(0.5)
        raise ServerError("the server never answered Lua")

    def _log_tail(self) -> str:
        try:
            with open(self.log_path) as fh:
                return fh.read()[-2000:]
        except OSError:
            return ""

    def stop(self):
        """Kill the server without saving. A Factorio server saves into the file it loaded when it
        quits (/quit, SIGTERM), which overwrote restored nodes and saved states; the runner always
        saves explicitly, so it never needs that."""
        if self.rcon is not None:
            self.rcon.close()
            self.rcon = None
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait()
            self.proc = None
        if getattr(self, "_log", None) is not None:
            self._log.close()
            self._log = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()

    # -- game -----------------------------------------------------------------------------------
    def lua(self, body: str) -> str:
        if self.rcon is None:
            raise ServerError("not running")
        return self.rcon.lua(body).strip()

    def tick(self) -> int:
        return int(self.lua("rcon.print(game.tick)"))

    def run(self, n: int, speed: float = 100.0, budget_s: float | None = None) -> int:
        """Move the game on `n` ticks as fast as the machine allows, then stand still."""
        if n <= 0:
            return self.tick()
        budget = budget_s if budget_s is not None else 60 + n / 60.0 * 2
        self.lua(f"game.speed = {speed} game.tick_paused = true game.ticks_to_run = {int(n)} "
                 "rcon.print('ok')")
        t0 = time.time()
        while time.time() - t0 < budget:
            left = int(self.lua("rcon.print(game.ticks_to_run)") or 0)
            if left == 0:
                return self.tick()
            time.sleep(0.05 if left < 600 else 0.25)
        raise ServerError(f"the game did not run {n} ticks within {budget:.0f}s")

    def save(self, name: str) -> str:
        """Save as `name` (paused), wait for the file. -> its path."""
        path = self.save_path(name)
        before = os.path.getmtime(path) if os.path.exists(path) else 0
        self.lua(f"game.server_save('{name}') rcon.print('ok')")
        for _ in range(240):
            if os.path.exists(path) and os.path.getmtime(path) > before:
                size = -1
                while size != os.path.getsize(path):  # written completely
                    size = os.path.getsize(path)
                    time.sleep(0.2)
                return path
            time.sleep(0.25)
        raise ServerError(f"the game did not write {path}")

    def load(self, save: str) -> int:
        self.stop()
        return self.start(save)

    def copy_save_in(self, src: str, name: str) -> str:
        shutil.copyfile(src, self.save_path(name))
        return self.save_path(name)
