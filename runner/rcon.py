"""RCON client for a Factorio server, and Lua flattened to one console line.

Adapted from factorio-playable's toolkit (fkcore/link.py: Rcon, lua_one_line). Stdlib only.
"""
from __future__ import annotations

import itertools
import socket
import struct


class RconError(RuntimeError):
    pass


def _drop_comment(line: str) -> str:
    q = None
    i = 0
    while i < len(line):
        c = line[i]
        if q:
            if c == "\\":
                i += 1
            elif c == q:
                q = None
        elif c in "'\"":
            q = c
        elif line.startswith("--", i):
            return line[:i]
        i += 1
    return line


def lua_one_line(body: str) -> str:
    """Lua as one console line: lines trimmed, `--` comments and blank lines dropped."""
    lines = (_drop_comment(line).strip() for line in body.splitlines())
    return " ".join(line for line in lines if line)


class Rcon:
    def __init__(self, port: int, password: str, host: str = "127.0.0.1", timeout: float = 30.0):
        self.ids = itertools.count(1)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._send(3, password)
        while True:
            rid, typ, _ = self._recv()
            if typ == 2:
                if rid == -1:
                    raise RconError("RCON auth failed")
                break

    def _send(self, typ: int, body: str) -> int:
        rid = next(self.ids)
        payload = struct.pack("<ii", rid, typ) + body.encode() + b"\x00\x00"
        self.sock.sendall(struct.pack("<i", len(payload)) + payload)
        return rid

    def _readn(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise RconError("RCON socket closed")
            buf += chunk
        return buf

    def _recv(self):
        (ln,) = struct.unpack("<i", self._readn(4))
        data = self._readn(ln)
        rid, typ = struct.unpack("<ii", data[:8])
        return rid, typ, data[8:-2].decode("utf-8", "replace")

    def call(self, cmd: str) -> str:
        self._send(2, cmd)
        return self._recv()[2]

    def lua(self, body: str) -> str:
        """Run Lua silently; whatever it rcon.print()s is the reply."""
        return self.call("/sc " + lua_one_line(body))

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass
