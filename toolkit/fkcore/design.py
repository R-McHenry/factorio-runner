"""The design database on disk (3.0, PLAN-3.0 §1 and §1b): the route step's work, one net at a
time, under ``out/design/<factory>/``.

Rory, 2026-11-01: "keep routes ... done inherently by having the router work on placement data
on the disk in the script. Possibly even supporting the default mode of the build going in the
background, and the player is allowed to look at or even modify placement data to pin tough nets,
and see brief stats reports on nets that are in progress too."

- ``base.fkp``: the layout the last route step began on (instances, machines; no routes).
- ``nets/<net>.net``: one file per net, written the moment the router lands it: a few ``# key:
  value`` lines (``status``: routed, kept, patched, unrouted, held, manual, skipped; ``why``;
  ``took``; ``key``, the ground an unrouted net failed on), then its carriers as a small .fkp. A
  net inside an instance (``mall/fe``) is ``nets/mall/fe.net``.
- ``route.now``: what the route step is doing (the net, how far, since when).

The next build starts from these files: a routed net's carriers are kept (judged, patched or
routed again as a committed route is), an unrouted one is not searched again on the same ground,
and a ``held`` one stands as it is, whatever the router would do (``fk hold NET``, or ``status:
held`` written by hand, read even while a build runs). ``fk status`` reads them.

A ``seed`` line names carriers of the file the player pinned (``fk seed NET``: a splitter, a
belt, a short run): the router lays the rest of the net through them, now and whenever it routes
the net again. ``status: seeded`` asks for that on the next build.
"""
from __future__ import annotations

import os
import time

from . import fkp

STATUSES = ("routed", "kept", "patched", "unrouted", "held", "manual", "skipped", "seeded")
_FIELDS = ("status", "why", "took", "key", "seed")
#: the reason ``fk unroute`` writes: the next build routes the net again, committed route or not
UNROUTED_BY_YOU = "unrouted by you"
_UNSAFE = '<>:"|?*'


def folder(out, factory):
    return os.path.join(out, "design", factory)


def net_path(out, factory, net):
    """A lane pair's name (``a|b``) has a character Windows won't take in a file name."""
    safe = "".join(f"%{ord(c):02X}" if c in _UNSAFE else c for c in net)
    return os.path.join(folder(out, factory), "nets", *safe.split("/")) + ".net"


def _write(path, text):
    """Whole or not at all: a reader (``fk status``, a build) never sees half a file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def net_text(factory, net, status, ents=(), why="", took=None, key=None, seed=()):
    if status not in STATUSES:
        raise ValueError(f"status is one of {', '.join(STATUSES)}, got {status!r}")
    lines = [f"# net: {net}", f"# status: {status}"]
    if why:
        lines.append(f"# why: {' '.join(str(why).split())}")
    if took is not None:
        lines.append(f"# took: {took:.1f} s")
    if key:
        lines.append(f"# key: {key}")
    if seed:
        lines.append(f"# seed: {','.join(sorted(seed, key=fkp.natural_key))}")
    lines.append(f"fkp 1 factory {factory}")
    lines += [fkp.entity_line(e) for e in sorted(ents, key=lambda e: fkp.natural_key(e.key))]
    return "\n".join(lines) + "\n"


def write_net(out, factory, net, status, ents=(), why="", took=None, key=None, seed=()):
    _write(net_path(out, factory, net), net_text(factory, net, status, ents, why, took, key, seed))


def read_net(path):
    """-> {net, status, why, took, key, ents: [Entity], mtime}, or None when it can't be read."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    rec = {"net": None, "status": None, "why": "", "took": None, "key": None, "seed": None,
           "mtime": mtime}
    for line in text.splitlines():
        if not line.startswith("#"):
            continue
        name, _, value = line[1:].strip().partition(":")
        name, value = name.strip(), value.strip()
        if name == "net":
            rec["net"] = value
        elif name in _FIELDS:
            rec[name] = value
    if rec["took"]:
        try:
            rec["took"] = float(rec["took"].split()[0])
        except ValueError:
            rec["took"] = None
    try:
        lay = fkp.loads(text, path)
    except ValueError:
        return None
    rec["ents"] = list(lay.entities.values()) if lay else []
    rec["seed"] = [k for k in (rec["seed"] or "").split(",") if k.strip()]
    if rec["status"] not in STATUSES or not rec["net"]:
        return None
    return rec


def seed_of(rec):
    """The carriers of a net file its ``seed`` line names."""
    names = set(rec.get("seed") or ())
    return [e for e in rec["ents"] if e.key in names]


def seed(out, factory, net, ents=None):
    """``fk seed NET``: the net's file becomes a seed, ``status: seeded``: ``ents`` (each keyed
    ``~NET/pK``), or with none, the carriers the file holds now (the player cut it down to the
    piece to keep, or wrote the piece in). -> the file's text, or None when there is neither."""
    path = net_path(out, factory, net)
    rec = read_net(path)
    if ents is None:
        if rec is None or not rec["ents"]:
            return None
        ents = rec["ents"]
    else:
        ents = [fkp.Entity(f"~{net}/p{k}", e.name, e.x, e.y, e.dir, dict(e.attrs, net=net))
                for k, e in enumerate(ents)]
    text = net_text(factory, net, "seeded", ents, seed=[e.key for e in ents])
    _write(path, text)
    return text


def read_all(out, factory):
    """{net: record} of every readable net file of ``factory``."""
    root = os.path.join(folder(out, factory), "nets")
    got = {}
    for here, _dirs, files in os.walk(root):
        for name in files:
            if name.endswith(".net"):
                rec = read_net(os.path.join(here, name))
                if rec is not None:
                    got[rec["net"]] = rec
    return got


def factories(out):
    root = os.path.join(out, "design")
    try:
        return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    except OSError:
        return []


def write_base(out, factory, lay):
    """The layout the route step begins on, without routes: with the net files, the layout it
    left (``layout``)."""
    base = lay.copy(entities={k: e for k, e in lay.entities.items()
                              if not e.owner.startswith("~")})
    _write(os.path.join(folder(out, factory), "base.fkp"), fkp.dumps(base))


def layout(out, factory, statuses=("routed", "kept", "patched", "held")):
    """``base.fkp`` with the carriers of the nets whose status is in ``statuses``, or None. A
    carrier on a tile an earlier net's carrier already holds (a file edited by hand) is left out."""
    path = os.path.join(folder(out, factory), "base.fkp")
    try:
        lay = fkp.load(path)
    except (OSError, ValueError):
        return None
    keys = set(lay.entities)
    for net, rec in sorted(read_all(out, factory).items()):
        if rec["status"] not in statuses:
            continue
        for e in rec["ents"]:
            if e.key not in keys:
                lay.entities[e.key] = e
                keys.add(e.key)
    return lay


def write_now(out, factory, text):
    _write(os.path.join(folder(out, factory), "route.now"),
           f"{time.strftime('%H:%M:%S')} {text}\n")


def clear_now(out, factory):
    try:
        os.remove(os.path.join(folder(out, factory), "route.now"))
    except OSError:
        pass


def read_now(out, factory):
    try:
        with open(os.path.join(folder(out, factory), "route.now"), encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return None


def status_lines(out, factory, net=None):
    """``fk status``: the counts per status, the unrouted nets with their reasons and times, and
    what the route step is doing now; with ``net``, that net's record."""
    recs = read_all(out, factory)
    if net is not None:
        rec = recs.get(net)
        if rec is None:
            return [f"{factory}: no net file for {net}"]
        out_ = [f"{factory} net {net}: {rec['status']}"
                + (f", {rec['took']:.1f} s" if rec["took"] is not None else "")
                + f", {len(rec['ents'])} carriers"]
        if rec["why"]:
            out_.append(f"  why: {rec['why']}")
        segs = chain_of(recs, net)
        if len(segs) > 1:
            out_.append("  chain: " + ", ".join(f"{s} ({recs[s]['status']})" for s in segs))
        out_.append(f"  file: {net_path(out, factory, net)}")
        return out_
    count = {}
    for rec in recs.values():
        count[rec["status"]] = count.get(rec["status"], 0) + 1
    lines = [f"{factory}: {len(recs)} nets: "
             + ", ".join(f"{count[s]} {s}" for s in STATUSES if count.get(s))]
    now = read_now(out, factory)
    if now:
        lines.append(f"  now: {now}")
    bad = sorted((r for r in recs.values() if r["status"] == "unrouted"),
                 key=lambda r: -(r["took"] or 0))
    for rec in bad:
        took = f" {rec['took']:.1f} s" if rec["took"] is not None else ""
        lines.append(f"  unrouted {rec['net']}{took}: {rec['why'][:160]}")
    held = sorted(r["net"] for r in recs.values() if r["status"] == "held")
    if held:
        lines.append(f"  held: {', '.join(held)}")
    roots = sorted({n.split("@")[0] for n in recs if "@" in n})
    if roots:
        lines.append("  chains: " + ", ".join(f"{c} ({len(chain_of(recs, c))} segments)"
                                             for c in roots))
    return lines


def chain_of(recs, net):
    """3.0 chained nets: the segments of the chain ``net`` is in, its first segment first (a
    segment is ``CHAIN@PIECE`` or ``CHAIN@PIECE.WAY``; ``@`` is in no net name you write)."""
    root = net.split("@")[0]
    return [n for n in sorted(recs, key=lambda n: (n != root, n))
            if n == root or n.startswith(root + "@")]


def set_status(out, factory, net, status, why=""):
    """``fk hold`` / ``fk unhold`` / ``fk unroute``: rewrite one net file's status (``unrouted``
    drops its carriers too). -> the new record's text, or None when there is no file."""
    path = net_path(out, factory, net)
    rec = read_net(path)
    if rec is None:
        return None
    if status == "unrouted":
        ents, keep = [], ()            # fk unroute drops a seed too
    else:
        ents, keep = rec["ents"], rec["seed"]
    text = net_text(factory, net, status, ents, why, rec["took"], seed=keep)
    _write(path, text)
    return text
