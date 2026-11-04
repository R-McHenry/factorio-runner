"""Model clients: OpenAI-compatible chat completions with tools (DeepSeek, vLLM, ...) and a
scripted fake for tests. Stdlib only.

A client's `complete(messages, tools)` -> Reply(message, usage, usd), where `message` is the
assistant message as the API returned it (appended to the conversation as-is) and `usage` is
{in_hit, in_miss, out} tokens.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class LLMError(RuntimeError):
    pass


@dataclass
class Reply:
    message: dict
    usage: dict
    usd: float


def price(usage: dict, p: dict) -> float:
    """$ for `usage` at `p` = {in_hit, in_miss, out} in $ per million tokens."""
    return (usage.get("in_hit", 0) * p.get("in_hit", 0) + usage.get("in_miss", 0) * p.get("in_miss", 0)
            + usage.get("out", 0) * p.get("out", 0)) / 1e6


class OpenAICompat:
    def __init__(self, base_url: str, model: str, api_key_env: str | None = None,
                 temperature: float = 0.7, max_tokens: int = 4096, price: dict | None = None,
                 timeout: float = 300.0, extra: dict | None = None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.key = os.environ.get(api_key_env, "") if api_key_env else ""
        if api_key_env and not self.key:
            raise LLMError(f"{api_key_env} is not set")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.prices = price or {}
        self.timeout = timeout
        self.extra = extra or {}

    def _post(self, url: str, body: dict | None) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method="POST" if body is not None else "GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read())

    def models(self) -> list[str]:
        return [m.get("id") for m in self._post(self.base_url + "/models", None).get("data", [])]

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        body = {"model": self.model, "messages": messages, "temperature": self.temperature,
                "max_tokens": self.max_tokens, **self.extra}
        if tools:
            body["tools"] = tools
        delay = 2.0
        for attempt in range(6):
            try:
                got = self._post(self.url, body)
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:500]
                if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(f"HTTP {e.code}: {detail}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 5:
                    time.sleep(delay)
                    delay *= 2
                    continue
                raise LLMError(str(e)) from e
        msg = got["choices"][0]["message"]
        msg = {k: v for k, v in msg.items() if v is not None and k in
               ("role", "content", "tool_calls", "reasoning_content")}
        msg.pop("reasoning_content", None)   # not sent back; never stored either
        msg.setdefault("role", "assistant")
        u = got.get("usage") or {}
        hit = u.get("prompt_cache_hit_tokens",
                    (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)) or 0
        prompt = u.get("prompt_tokens", 0) or 0
        usage = {"in_hit": hit, "in_miss": max(0, prompt - hit), "out": u.get("completion_tokens", 0) or 0}
        return Reply(msg, usage, price(usage, self.prices))


class Scripted:
    """A fake model that replays a script: a list of turns, each either
    {"tool": NAME, "args": {...}} (one tool call), {"tools": [[NAME, {...}], ...]} or
    {"say": TEXT} (plain text). Past the end it calls finish."""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.i = 0

    def complete(self, messages, tools=None) -> Reply:
        turn = self.script[self.i] if self.i < len(self.script) else {"tool": "finish", "args": {"summary": "script over"}}
        self.i += 1
        usage = {"in_hit": 0, "in_miss": sum(len(json.dumps(m)) for m in messages) // 4, "out": 20}
        if "say" in turn:
            return Reply({"role": "assistant", "content": turn["say"]}, usage, 0.0)
        calls = turn.get("tools") or [[turn["tool"], turn.get("args", {})]]
        tc = [{"id": f"call_{self.i}_{j}", "type": "function",
               "function": {"name": n, "arguments": json.dumps(a)}} for j, (n, a) in enumerate(calls)]
        return Reply({"role": "assistant", "content": "", "tool_calls": tc}, usage, 0.0)


def from_spec(agent: dict):
    if agent.get("provider") == "scripted":
        with open(agent["script"]) as fh:
            return Scripted(json.load(fh))
    return OpenAICompat(base_url=agent["base_url"], model=agent["model"],
                        api_key_env=agent.get("api_key_env"),
                        temperature=float(agent.get("temperature", 0.7)),
                        max_tokens=int(agent.get("max_tokens", 4096)),
                        price=agent.get("price"), extra=agent.get("extra"))
