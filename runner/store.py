"""The store: content-addressed blobs plus JSON documents with compare-and-swap.

`open_store("file:///dir")` or `open_store("dir")` is a local directory; `open_store("s3://bucket/prefix")`
is S3 (needs boto3), where compare-and-swap uses conditional writes (If-Match / If-None-Match).
Parallel workers of one arm update the same archive document, so every update goes through
`update_json`, which retries on a conflict.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import random
import time


class Conflict(RuntimeError):
    """The document changed since it was read."""


class Store:
    def get(self, key: str) -> bytes | None:
        raise NotImplementedError

    def put(self, key: str, data: bytes):
        raise NotImplementedError

    def get_versioned(self, key: str) -> tuple[bytes | None, str | None]:
        raise NotImplementedError

    def put_if(self, key: str, data: bytes, version: str | None):
        """Write only if the stored version is still `version` (None: only if absent)."""
        raise NotImplementedError

    def list(self, prefix: str) -> list[str]:
        raise NotImplementedError

    # -- built on the above ----------------------------------------------------------------------
    def put_blob(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        k = f"blobs/{sha[:2]}/{sha}"
        if self.get_versioned(k)[1] is None:
            self.put(k, data)
        return sha

    def get_blob(self, sha: str) -> bytes:
        data = self.get(f"blobs/{sha[:2]}/{sha}")
        if data is None:
            raise KeyError(f"no blob {sha}")
        return data

    def get_json(self, key: str, default=None):
        data = self.get(key)
        return default if data is None else json.loads(data)

    def put_json(self, key: str, obj):
        self.put(key, json.dumps(obj, indent=1, sort_keys=True).encode())

    def update_json(self, key: str, fn, default=None, tries: int = 20):
        """Read-modify-write `key` with fn(obj) -> obj, retried on conflict. -> the new object."""
        for i in range(tries):
            data, ver = self.get_versioned(key)
            obj = json.loads(data) if data is not None else default
            new = fn(obj)
            try:
                self.put_if(key, json.dumps(new, indent=1, sort_keys=True).encode(), ver)
                return new
            except Conflict:
                time.sleep(min(2.0, 0.05 * 2 ** i) * random.random())
        raise Conflict(f"{key}: gave up after {tries} conflicts")


class LocalStore(Store):
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        p = os.path.normpath(os.path.join(self.root, key))
        if not p.startswith(self.root + os.sep):
            raise ValueError(f"bad key {key}")
        return p

    def get(self, key):
        try:
            with open(self._path(key), "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return None

    def put(self, key, data):
        p = self._path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = f"{p}.{os.getpid()}.{random.getrandbits(32):x}.tmp"
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, p)

    def get_versioned(self, key):
        data = self.get(key)
        return data, (hashlib.sha256(data).hexdigest() if data is not None else None)

    def put_if(self, key, data, version):
        p = self._path(key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p + ".lock", "w") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            if self.get_versioned(key)[1] != version:
                raise Conflict(key)
            self.put(key, data)

    def list(self, prefix):
        base = self._path(prefix) if prefix else self.root
        out = []
        for dirpath, _, files in os.walk(base):
            for f in files:
                if f.endswith((".lock", ".tmp")):
                    continue
                out.append(os.path.relpath(os.path.join(dirpath, f), self.root))
        return sorted(out)


class S3Store(Store):
    def __init__(self, bucket: str, prefix: str = ""):
        import boto3  # only needed for S3
        from botocore.exceptions import ClientError
        self.s3 = boto3.client("s3")
        self.ClientError = ClientError
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""

    def _k(self, key):
        return self.prefix + key

    def get(self, key):
        return self.get_versioned(key)[0]

    def put(self, key, data):
        self.s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=data)

    def get_versioned(self, key):
        try:
            r = self.s3.get_object(Bucket=self.bucket, Key=self._k(key))
            return r["Body"].read(), r["ETag"]
        except self.ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None, None
            raise

    def put_if(self, key, data, version):
        cond = {"IfNoneMatch": "*"} if version is None else {"IfMatch": version}
        try:
            self.s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, **cond)
        except self.ClientError as e:
            if e.response["Error"]["Code"] in ("PreconditionFailed", "ConditionalRequestConflict",
                                               "412", "409"):
                raise Conflict(key) from e
            raise

    def list(self, prefix):
        out = []
        pag = self.s3.get_paginator("list_objects_v2")
        for page in pag.paginate(Bucket=self.bucket, Prefix=self._k(prefix)):
            out += [o["Key"][len(self.prefix):] for o in page.get("Contents", [])]
        return sorted(out)


def open_store(url: str) -> Store:
    if url.startswith("s3://"):
        bucket, _, prefix = url[5:].partition("/")
        return S3Store(bucket, prefix)
    if url.startswith("file://"):
        url = url[7:]
    return LocalStore(url)
