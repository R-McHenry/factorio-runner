"""The source as a build began (2.1.19).

A build reads its ``.fk`` files more than once: ``elaborate`` reads world.fk and its imports, and
``size`` reads them again for each count it tries. The live run's labs paste (~4300k, 2026-10-31)
was laid from a world.fk the player was editing under it, half the old file and half the new. While
:func:`held` is open, every source read goes through :func:`read`: the first read of a file keeps
its bytes, and every later read in the build gets those same bytes back. :func:`changed` tells the
build which files moved on under it, so it can say so.

Outside a build (``fk layout``, the tests), :func:`read` reads the disk each time.
"""
import contextlib
import os

__all__ = ["held", "read", "read_bytes", "changed"]

_HELD = None                    # {absolute path: bytes} while a build runs


def read_bytes(path) -> bytes:
    """``path``'s bytes: as the build first read them while one is held, else the disk's."""
    full = os.path.abspath(path)
    if _HELD is not None and full in _HELD:
        return _HELD[full]
    with open(full, "rb") as fh:
        data = fh.read()
    if _HELD is not None:
        _HELD[full] = data
    return data


def read(path) -> str:
    """``path`` as text (UTF-8, newlines as ``open`` reads them)."""
    text = read_bytes(path).decode("utf-8")
    return text.replace("\r\n", "\n").replace("\r", "\n")


@contextlib.contextmanager
def held():
    """Hold the source for one build: nested builds (``fk lab`` runs one) share the outer one's."""
    global _HELD
    if _HELD is not None:
        yield _HELD
        return
    _HELD = {}
    try:
        yield _HELD
    finally:
        _HELD = None


def changed(snap) -> list:
    """The files of ``snap`` (a :func:`held` dict) that differ on disk now, or are gone."""
    out = []
    for full, data in sorted(snap.items()):
        try:
            with open(full, "rb") as fh:
                now = fh.read()
        except OSError:
            now = None
        if now != data:
            out.append(full)
    return out
