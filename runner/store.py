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
    # STS sessions handed to the job by aws-actions/configure-aws-credentials last one hour.
    # A 1000-turn episode runs for hours, so once the session expires every store call fails
    # (and the final node is never saved). The job still has its OIDC request URL and token,
    # so the store mints a fresh session on the first expiry and retries the call.
    EXPIRED = {"ExpiredToken", "ExpiredTokenException", "RequestExpired",
               "InvalidClientTokenId", "TokenRefreshRequired"}

    def __init__(self, bucket: str, prefix: str = ""):
        import boto3  # only needed for S3
        from botocore.exceptions import ClientError
        self.s3 = boto3.client("s3")
        self.ClientError = ClientError
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        # The role to re-assume when the session expires. The workflow does not pass it to
        # the job, so read it off our own assumed-role ARN now, while the session still works.
        self.role_arn = os.environ.get("AWS_ROLE_ARN") or self._caller_role_arn()

    def _caller_role_arn(self):
        try:
            import boto3
            arn = boto3.client("sts").get_caller_identity()["Arn"]
            if ":assumed-role/" in arn:
                account = arn.split(":")[4]
                name = arn.split(":assumed-role/", 1)[1].split("/", 1)[0]
                return f"arn:aws:iam::{account}:role/{name}"
        except Exception:
            pass
        return None


    def _k(self, key):
        return self.prefix + key

    def _expired(self, e) -> bool:
        return e.response.get("Error", {}).get("Code") in self.EXPIRED

    def _reclient(self):
        import boto3
        c = refresh_role_credentials(self.role_arn)
        kw = dict(aws_access_key_id=c["access_key"], aws_secret_access_key=c["secret_key"],
                  aws_session_token=c["token"])
        if os.environ.get("AWS_REGION"):
            kw["region_name"] = os.environ["AWS_REGION"]
        self.s3 = boto3.client("s3", **kw)

    def _run(self, fn):
        """Run an S3 call; on an expired STS session, refresh once and retry it."""
        try:
            return fn()
        except self.ClientError as e:
            if not self._expired(e):
                raise
            try:
                self._reclient()
            except Exception as re:
                raise e from re
            return fn()

    def get(self, key):
        return self.get_versioned(key)[0]

    def put(self, key, data):
        self._run(lambda: self.s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=data))

    def get_versioned(self, key):
        def get():
            return self.s3.get_object(Bucket=self.bucket, Key=self._k(key))
        try:
            r = self._run(get)
            return r["Body"].read(), r["ETag"]
        except self.ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return None, None
            raise

    def put_if(self, key, data, version):
        cond = {"IfNoneMatch": "*"} if version is None else {"IfMatch": version}
        def put():
            return self.s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, **cond)
        try:
            self._run(put)
        except self.ClientError as e:
            if e.response["Error"]["Code"] in ("PreconditionFailed", "ConditionalRequestConflict",
                                               "412", "409"):
                raise Conflict(key) from e
            raise

    def list(self, prefix):
        def ls():
            out = []
            pag = self.s3.get_paginator("list_objects_v2")
            for page in pag.paginate(Bucket=self.bucket, Prefix=self._k(prefix)):
                out += [o["Key"][len(self.prefix):] for o in page.get("Contents", [])]
            return sorted(out)
        return self._run(ls)


def refresh_role_credentials(role: str | None = None) -> dict:
    """Assume the workflow role again from a fresh GitHub OIDC token.

    Only works inside an Actions job (the OIDC request env is set there); outside one the
    caller re-raises the original expired-session error.
    """
    import urllib.request
    import boto3
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    tok = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    role = role or os.environ.get("AWS_ROLE_ARN")
    if not (url and tok and role):
        raise RuntimeError("no OIDC request env or role ARN; cannot refresh the AWS session")
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(f"{url}{sep}audience=sts.amazonaws.com",
                                 headers={"Authorization": f"bearer {tok}"})
    with urllib.request.urlopen(req, timeout=20) as r:
        web = json.loads(r.read())["value"]
    sts = boto3.client("sts")
    c = sts.assume_role_with_web_identity(
        RoleArn=role, RoleSessionName="factorio-runner-store-refresh",
        WebIdentityToken=web, DurationSeconds=3600)["Credentials"]
    return {"access_key": c["AccessKeyId"], "secret_key": c["SecretAccessKey"],
            "token": c["SessionToken"]}



def open_store(url: str) -> Store:
    if url.startswith("s3://"):
        bucket, _, prefix = url[5:].partition("/")
        return S3Store(bucket, prefix)
    if url.startswith("file://"):
        url = url[7:]
    return LocalStore(url)
