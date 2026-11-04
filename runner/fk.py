"""The fk toolkit as an agent tool: `python -m fkcore ...` in the session's game codebase, linked to
the episode's own server.

The toolkit (toolkit/fkcore, 2.1.30) reaches the game through env vars (fkcore.link): FK_LINK=on,
FK_RCON_HOST/PORT/PW_FILE and FK_SCRIPT_OUTPUT (the server's script-output, where big replies are
written). FK_PACK picks the mod pack (toolkit/fkcore/data/packs/<pack>). The codebase is the work
dir's `game/`, so it is frozen with every node and restored with it.

A few commands don't fit the lab's lockstep clock or its saved states, and get a pointer instead:
`wait` (the game is paused while you think: use evaluate), `sim` (save_state/load_state are the
rehearsal), `lab` (its lab surface comes from a scenario these maps don't run). `lua` goes through
the runner's referee like the lua tool.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLKIT = os.path.join(ROOT, "toolkit")

REDIRECT = {
    "wait": "the game stands still while you think; run it with the evaluate tool, then look again",
    "sim": "there is no separate planner here: save_state, try it, load_state to undo",
    "lab": "the lab surface isn't on these maps: try a design for real after save_state, and "
           "load_state to undo",
}


def env_for(server, pack: str | None) -> dict:
    pw = os.path.join(server.root, "rcon.pw")
    with open(pw, "w") as fh:
        fh.write(server.password)
    env = dict(os.environ)
    for k in list(env):
        if k.endswith("_API_KEY") or k.startswith("AWS_"):
            env.pop(k)    # the agent's commands never see the runner's credentials
    env.update({
        "PYTHONPATH": TOOLKIT + os.pathsep + env.get("PYTHONPATH", ""),
        "FK_LINK": "on", "FK_RCON_HOST": "127.0.0.1", "FK_RCON_PORT": str(server.rcon_port),
        "FK_RCON_PW_FILE": pw, "FK_SCRIPT_OUTPUT": os.path.join(server.write, "script-output"),
    })
    if pack:
        env["FK_PACK"] = pack
    return env


def help_text() -> str:
    from importlib import util
    spec = util.spec_from_file_location("fkmain_doc", os.path.join(TOOLKIT, "fkcore", "__main__.py"))
    with open(spec.origin) as fh:
        src = fh.read()
    start = src.find('"""') + 3
    return src[start:src.find('"""', start)]


def run(args: str, cwd: str, server, pack: str | None, timeout: float = 900) -> tuple[int, str]:
    """Run `fk ARGS` in `cwd` (the codebase). -> (exit code, output)."""
    raw = args.strip()
    if raw.split(None, 1)[:1] == ["lua"] and "--file" not in raw.split()[:3]:
        # `lua GAME_DIR CODE`: the code goes through as written (shell-style splitting would eat its
        # quotes and split it into words)
        parts = raw.split(None, 2)
        argv = parts if len(parts) == 3 else shlex.split(raw)
    else:
        argv = shlex.split(raw)
    if not argv or argv[0] in ("help", "-h", "--help"):
        return 0, help_text()
    if argv[0] in REDIRECT:
        return 2, f"fk {argv[0]} isn't used in this lab: {REDIRECT[argv[0]]}."
    os.makedirs(os.path.join(server.write, "script-output"), exist_ok=True)
    try:
        p = subprocess.run([sys.executable, "-m", "fkcore", *argv], cwd=cwd, env=env_for(server, pack),
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + (("\n" + p.stderr) if p.stderr.strip() else "")).strip()
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        return 124, f"{out}\n(fk {argv[0]} stopped after {timeout:.0f} s)"


def lua_code(args: str) -> str | None:
    """The Lua of an `fk lua GAME_DIR CODE` call (for the referee), or None."""
    parts = args.strip().split(None, 2)
    if len(parts) == 3 and parts[0] == "lua" and not parts[2].startswith("--file"):
        return parts[2]
    return None
