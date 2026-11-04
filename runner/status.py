"""The status page: reads the store, writes `data.json` (and `index.html` once) into a directory a
web server serves. Run it every minute (computah's timer); the page polls data.json and redraws
without losing what you picked.

    python -m runner status --store s3://bucket/prefix --site /srv/factorio-status
"""
from __future__ import annotations

import json
import os
import shutil
import time

from . import archive, cell as cells, ledger

PAGE = os.path.join(os.path.dirname(__file__), "status_page.html")
MAX_POINTS = 300          # per session series, after thinning
TOP_ITEMS = 12            # items kept per session series (by final count)
RUNNING_S = 300           # a session whose status is older than this is shown as stalled


def _thin(samples: list[dict], n: int) -> list[dict]:
    if len(samples) <= n:
        return samples
    step = len(samples) / n
    keep = [samples[int(i * step)] for i in range(n)]
    if keep[-1] is not samples[-1]:
        keep.append(samples[-1])
    return keep


def _arms(store) -> list[str]:
    return sorted({k.split("/")[1][:-len(".json")] for k in store.list("archives/") if k.endswith(".json")}
                  | {k.split("/")[1] for k in store.list("status/")})


def frontier(arch: dict) -> list[dict]:
    """Best height reached for at most each $ spent (from fresh): the archive's anytime curve."""
    rows = sorted((e.get("usd", 0.0), e["height"], e["tick"]) for e in arch["cells"].values()
                  if not e.get("synthetic"))
    out, best = [], -1
    for usd, h, tick in rows:
        if h > best:
            best = h
            out.append({"usd": round(usd, 5), "height": h, "tick": tick})
    return out


def collect(store, stale_s: int = RUNNING_S) -> dict:
    now = time.time()
    data = {"generated": now, "month": ledger.month(), "arms": {}, "sessions": [], "stats": {},
            "episodes": [], "spend": (store.get_json(f"ledger/{ledger.month()}.json") or {})}
    for arm in _arms(store):
        arch = archive.get(store, arm)
        lock = store.get_json(f"arms/{arm}.lock.json") or {}
        spec = lock.get("spec", {})
        data["arms"][arm] = {
            "cells": len(arch["cells"]), "episodes": arch.get("episodes", 0),
            "frontier": frontier(arch),
            "model": spec.get("agent", {}).get("model"),
            "provider": spec.get("agent", {}).get("provider_name"),
            "cap": spec.get("budget", {}).get("monthly_usd"),
            "hard": spec.get("budget", {}).get("hard_usd"),
            "best": max((e["height"] for e in arch["cells"].values()), default=0)}
        for k in store.list(f"status/{arm}/"):
            st = store.get_json(k)
            if not st:
                continue
            if st.get("state") == "running" and now - st.get("updated", 0) > stale_s:
                st["state"] = "stalled"
            data["sessions"].append(st)
            sid = st["session"]
            ser = (store.get_json(f"stats/{arm}/{sid}.json") or {}).get("samples", [])
            if ser:
                final = ser[-1].get("made", {})
                top = sorted(final, key=lambda i: -final[i])[:TOP_ITEMS]
                data["stats"][sid] = [{"tick": s["tick"], "valid": s["valid"], "ep": s.get("ep", 0),
                                       "researched": s.get("researched", 0),
                                       "made": {i: s.get("made", {}).get(i, 0) for i in top}}
                                      for s in _thin(ser, MAX_POINTS)]
        eps = []
        for k in store.list(f"episodes/{arm}/"):
            e = store.get_json(k)
            if e:
                eps.append({f: e.get(f) for f in ("id", "session", "index", "arm", "start_tick", "end_tick",
                                                  "reached_tick", "stop", "turns", "usd", "end_valid",
                                                  "invalid_marks", "ended", "candidates", "goal", "moved")})
        data["episodes"] += eps
    data["sessions"].sort(key=lambda s: -s.get("updated", 0))
    data["episodes"].sort(key=lambda e: -(e.get("ended") or 0))
    data["episodes"] = data["episodes"][:300]
    return data


MAP_SESSIONS = 12        # sessions whose latest maps the site carries (the most recently updated)


def _copy_maps(store, site: str, data: dict):
    """Each recent session's latest maps into DIR/maps/, with site-relative paths in data["maps"].
    Terrain previews are copied once (their names are content keys); entity files every run."""
    data["maps"] = {}
    for st in data["sessions"][:MAP_SESSIONS]:
        arm, sid = st["arm"], st["session"]
        idx = store.get_json(f"maps/{arm}/{sid}/index.json")
        if not idx:
            continue
        surfaces = []
        for s in idx.get("surfaces", []):
            ent = store.get(s["entities"])
            if ent is None:
                continue
            ename = f"maps/{sid}/{os.path.basename(s['entities'])}"
            os.makedirs(os.path.join(site, os.path.dirname(ename)), exist_ok=True)
            with open(os.path.join(site, ename), "wb") as fh:
                fh.write(ent)
            tname = None
            if s.get("terrain"):
                tname = f"maps/terrain/{os.path.basename(s['terrain'])}"
                tpath = os.path.join(site, tname)
                if not os.path.exists(tpath):
                    png = store.get(s["terrain"])
                    if png is not None:
                        os.makedirs(os.path.dirname(tpath), exist_ok=True)
                        with open(tpath, "wb") as fh:
                            fh.write(png)
                    else:
                        tname = None
            surfaces.append({"name": s["name"], "planet": s.get("planet"), "platform": s.get("platform"),
                             "entities": ename, "terrain": tname, "window": s.get("window"),
                             "count": s.get("count")})
        data["maps"][sid] = {"tick": idx.get("tick"), "time": idx.get("time"), "surfaces": surfaces}


def write_site(store, site: str):
    os.makedirs(site, exist_ok=True)
    data = collect(store)
    _copy_maps(store, site, data)
    tmp = os.path.join(site, "data.json.tmp")
    with open(tmp, "w") as fh:
        json.dump(data, fh, separators=(",", ":"))
    os.replace(tmp, os.path.join(site, "data.json"))
    shutil.copyfile(PAGE, os.path.join(site, "index.html"))
    return data
