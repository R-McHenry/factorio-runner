"""fkcore.wait: a bounded wait on the game (2.0.4 A11), ``fk wait built [AREA] --max 5m``.

Workers hand-rolled their own polling loops in shell and Lua (the second lead run: one waited
forever on a mall whose underground belts never paired) because the toolkit had no bounded wait.
``fk wait built`` polls the area through the game link, as a look does (``link.snapshot``), until
no ghosts are left there (or, with ``--name``, until the named entity's ghosts are gone) or the
bound expires. It prints one line at the end and never progress: the clear line, or what is left
by name. Exit 0 when clear, 1 when the bound expired with ghosts left, 2 on a link error (the
caller catches that; ``wait.run`` raises ``link.LinkError``).

The clock is injected (``clock`` and ``sleep``), so the tests need no game and no real sleep. A
wait is safe to interrupt: each poll is one RCON call with the link's 5 s timeout, and the link is
closed by the caller's ``with r:``, so no game call is left running. Nothing here writes.
"""
from __future__ import annotations

import re
import time

from . import link

__all__ = ["DEFAULT_MAX", "DEFAULT_POLL", "MAX_TEXT", "POLL_TEXT", "parse_duration",
           "ghost_counts", "left_line", "clear_line", "run"]

#: The default bound and poll interval (``fk wait built`` uses both).
MAX_TEXT = "5m"
POLL_TEXT = "10s"
DEFAULT_MAX = 300.0
DEFAULT_POLL = 10.0

_DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(h|min|m|s)")


def parse_duration(text):
    """``90s``, ``5m``, ``30min`` or ``1h`` -> seconds (a float; the tests inject it)."""
    m = _DURATION.fullmatch((text or "").strip())
    if not m:
        raise ValueError(f"a duration is like 90s, 5m or 1h, got {text!r}")
    per = {"h": 3600, "min": 60, "m": 60, "s": 1}[m.group(2)]
    return float(m.group(1)) * per


def ghost_counts(capture, names=None):
    """{entity name: ghost count} of a ``link.snapshot`` capture, only ``names`` when given."""
    want = set(names) if names else None
    out = {}
    for row in capture.get("things") or ():
        if not row.get("g"):
            continue
        name = row.get("n")
        if want is not None and name not in want:
            continue
        out[name] = out.get(name, 0) + 1
    return out


def _area_text(area):
    return ",".join(str(int(v)) for v in area)


def clear_line(area, names=None):
    a = _area_text(area)
    if names:
        return f"built: no ghosts of {', '.join(names)} left in {a}"
    return f"built: no ghosts left in {a}"


def left_line(area, counts, names=None):
    """The one line of a bound that ran out: how many ghosts are left, by name."""
    total = sum(counts.values())
    word = "ghost" if total == 1 else "ghosts"
    what = f"{total} {word}" + (f" of {', '.join(names)}" if names else "")
    by_name = ", ".join(f"{name} {n}" for name, n in sorted(counts.items()))
    return f"wait built: {what} left in {_area_text(area)} ({by_name})"


def run(r, surface, area, names=None, max_s=DEFAULT_MAX, poll_s=DEFAULT_POLL,
        clock=None, sleep=None, tag="wait"):
    """Poll ``area`` until its ghosts are gone or ``max_s`` has passed -> (exit code, lines).

    ``link.snapshot(..., keep=False)`` is the read (the same capture a look takes, without the
    report or the render). ``clock``/``sleep`` default to ``time.monotonic``/``time.sleep``; the
    tests pass a fake pair, so nothing really sleeps. The first check is immediate: ``--max 0``
    checks once and, with ghosts left, says so.
    """
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    deadline = clock() + max(0.0, float(max_s))
    while True:
        left = ghost_counts(link.snapshot(r, surface, area, tag, keep=False), names)
        if not left:
            return 0, [clear_line(area, names)]
        now = clock()
        if now >= deadline:
            return 1, [left_line(area, left, names)]
        sleep(min(float(poll_s), deadline - now))
