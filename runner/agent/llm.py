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
    usage: dict          # in_hit, in_miss, out (out includes reasoning), reasoning
    usd: float
    finish: str = ""     # the API's finish_reason of the last call ("length": cut off at max_tokens)
    retried: int = 0     # calls made again with a larger max_tokens


def price(usage: dict, p: dict) -> float:
    """$ for `usage` at `p` = {in_hit, in_miss, out} in $ per million tokens."""
    return (usage.get("in_hit", 0) * p.get("in_hit", 0) + usage.get("in_miss", 0) * p.get("in_miss", 0)
            + usage.get("out", 0) * p.get("out", 0)) / 1e6


class OpenAICompat:
    def __init__(self, base_url: str, model: str, api_key_env: str | None = None,
                 temperature: float = 0.7, max_tokens: int = 16384, price: dict | None = None,
                 timeout: float = 600.0, extra: dict | None = None, max_tokens_ceiling: int = 65536):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.key = os.environ.get(api_key_env, "") if api_key_env else ""
        if api_key_env and not self.key:
            raise LLMError(f"{api_key_env} is not set")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.max_tokens_ceiling = max(max_tokens, max_tokens_ceiling)
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
        """One reply. A reasoning model's thinking counts against max_tokens, so a hard turn can
        end at the limit with no answer and no tool call (finish_reason "length"); that turn is
        asked again with 4x the limit, up to max_tokens_ceiling, and both calls are paid for."""
        total = {"in_hit": 0, "in_miss": 0, "out": 0, "reasoning": 0}
        limit, retried = self.max_tokens, 0
        while True:
            msg, usage, finish = self._once(messages, tools, limit)
            for k in total:
                total[k] += usage.get(k, 0)
            empty = not msg.get("tool_calls") and not (msg.get("content") or "").strip()
            if finish == "length" and empty and limit < self.max_tokens_ceiling:
                limit = min(self.max_tokens_ceiling, limit * 4)
                retried += 1
                continue
            return Reply(msg, total, price(total, self.prices), finish, retried)

    def _once(self, messages, tools, max_tokens):
        body = {"model": self.model, "messages": messages, "temperature": self.temperature,
                "max_tokens": max_tokens, **self.extra}
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
        choice = got["choices"][0]
        msg = {k: v for k, v in choice["message"].items() if v is not None and k in
               ("role", "content", "tool_calls")}   # reasoning_content is never sent back or stored
        msg.setdefault("role", "assistant")
        u = got.get("usage") or {}
        hit = u.get("prompt_cache_hit_tokens",
                    (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0)) or 0
        prompt = u.get("prompt_tokens", 0) or 0
        usage = {"in_hit": hit, "in_miss": max(0, prompt - hit), "out": u.get("completion_tokens", 0) or 0,
                 "reasoning": (u.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0}
        return msg, usage, choice.get("finish_reason") or ""


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
                        max_tokens=int(agent.get("max_tokens", 16384)),
                        max_tokens_ceiling=int(agent.get("max_tokens_ceiling", 65536)),
                        price=agent.get("price"), extra=agent.get("extra"))
