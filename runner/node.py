"""A node: everything the agent's next move depends on, frozen.

- the game: a save written while paused (a blob);
- the work dir: the design source and the project notes (NOTES.md and the rest), as a tar (a blob);
- the cell, the tick, and the ledger of what the path from fresh cost (tokens, $, turns);
- lineage: the arm, the parent node, the episode that made it.

Restoring a node loads its save into a server and unpacks its work dir; the agent starts a fresh
conversation from there.
"""
from __future__ import annotations

import io
import json
import os
import tarfile
import time
import uuid
from dataclasses import asdict, dataclass, field

from . import cell as cells

SKIP_DIRS = {".git", "__pycache__"}
MAX_FILE = 5_000_000


@dataclass
class Node:
    id: str
    arm: str
    parent: str | None
    episode: str | None
    tick: int
    levels: dict
    save: str            # blob sha
    work: str            # blob sha
    ledger: dict = field(default_factory=dict)   # cumulative from fresh: usd, tokens_in, tokens_out, turns
    created: float = 0.0
    synthetic: bool = False
    flags: list = field(default_factory=list)
    researched: int = 0

    @property
    def key(self) -> str:
        return cells.key(self.levels)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> "Node":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def pack_dir(path: str) -> bytes:
    """A deterministic tar.gz of a work dir (sorted, no mtimes, no owners)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=6) as tar:
        for dirpath, dirs, files in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
            for f in sorted(files):
                full = os.path.join(dirpath, f)
                if os.path.getsize(full) > MAX_FILE:
                    continue
                info = tar.gettarinfo(full, arcname=os.path.relpath(full, path))
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                with open(full, "rb") as fh:
                    tar.addfile(info, fh)
    return buf.getvalue()


def unpack_dir(data: bytes, path: str):
    os.makedirs(path, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(path, filter="data")


def read_state(server) -> dict:
    """{tick, made, can, researched} from the game (reads are free in the lab)."""
    got = json.loads(server.lua(cells.LUA_MADE) or "{}")
    made = got.get("made") or {}
    return {"tick": int(got.get("tick", 0)), "made": made if isinstance(made, dict) else {},
            "can": list(got.get("can") or []), "researched": int(got.get("researched", 0))}


def snapshot(store, server, workdir: str, arm: str, parent: str | None, episode: str | None,
             ledger: dict, flags=(), synthetic=False) -> Node:
    """Freeze the running game and the work dir into a node (written to the store)."""
    st = read_state(server)
    nid = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    path = server.save(f"node-{nid}")
    with open(path, "rb") as fh:
        save_sha = store.put_blob(fh.read())
    os.remove(path)
    node = Node(id=nid, arm=arm, parent=parent, episode=episode, tick=st["tick"],
                levels=cells.cell(st["made"]), save=save_sha, work=store.put_blob(pack_dir(workdir)),
                ledger=dict(ledger), created=time.time(), synthetic=synthetic, flags=list(flags),
                researched=st["researched"])
    store.put_json(f"nodes/{arm}/{nid}.json", node.to_json())
    return node


def load(store, arm: str, nid: str) -> Node:
    d = store.get_json(f"nodes/{arm}/{nid}.json")
    if d is None:
        raise KeyError(f"no node {arm}/{nid}")
    return Node.from_json(d)


def restore(store, node: Node, server, workdir: str) -> int:
    """Load `node` into `server` (stopping whatever ran) and unpack its work dir. -> tick."""
    save = server.save_path(f"from-{node.id}")
    with open(save, "wb") as fh:
        fh.write(store.get_blob(node.save))
    unpack_dir(store.get_blob(node.work), workdir)
    server.stop()
    return server.start(save)
