"""Model clients: OpenAI-compatible chat completions with tools (DeepSeek, vLLM, ...), the
Claude Code CLI for a Claude subscription (Opus/Sonnet), and a scripted fake for tests.
Stdlib only.

A client's `complete(messages, tools)` -> Reply(message, usage, usd), where `message` is the
assistant message as the API returned it (appended to the conversation as-is) and `usage` is
{in_hit, in_miss, out} tokens.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid
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


class ClaudeCLI:
    """Claude Code as the player, one CLI turn per `complete()`.

    This is for a Claude subscription (Rory's account), which has no OpenAI-compatible endpoint
    and no API key: the `claude` binary already knows how to authenticate. Its own tools are
    disabled (the runner owns the game), and `--json-schema` makes every turn exactly one call
    from the runner's tool list. The session is resumed by id, so the CLI keeps the conversation
    and its prompt cache instead of us re-sending the transcript each turn.

    `billing = "subscription"` (the default) reports $0 to the ledger: the month's spend is
    Claude usage, not per-token dollars. Set `billing = "api"` to price the reported tokens.
    """

    def __init__(self, model: str = "opus", binary: str = "claude", config_dir: str | None = None,
                 billing: str = "subscription", price: dict | None = None, timeout: float = 900.0,
                 max_retries: int = 2, cwd: str | None = None):
        self.model = model
        self.binary = binary
        self.config_dir = os.path.expanduser(config_dir) if config_dir else None
        self.billing = billing
        self.prices = price or {}
        self.timeout = timeout
        self.max_retries = max_retries
        self.session_id = str(uuid.uuid4())
        self.started = False
        self.cursor = 0
        self.cwd = cwd or tempfile.mkdtemp(prefix="claude-runner-")
        self.usd = 0.0

    def models(self) -> list[str]:
        return [self.model]

    def _env(self) -> dict:
        env = {k: v for k, v in os.environ.items()
               if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")}
        if self.config_dir:
            env["CLAUDE_CONFIG_DIR"] = self.config_dir
        env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
        return env

    def _system(self, messages: list[dict], tools: list[dict] | None) -> str:
        base = ""
        if messages and messages[0].get("role") == "system":
            base = messages[0].get("content") or ""
        if not tools:
            return base
        return (
            "You are the decision model for a Factorio-playing agent. On every turn choose "
            "exactly one tool call from the list below and reply only in the required JSON "
            "shape. Do not use Claude Code tools: none are enabled.\n\n"
            "# TOOLS\n" + json.dumps(tools, ensure_ascii=False, indent=1) + "\n\n"
            "# GAME INSTRUCTIONS\n" + base
        )

    def _schema(self, tools: list[dict]) -> dict:
        names = [t["function"]["name"] for t in tools]
        return {"type": "object", "properties": {
            "thought": {"type": "string", "description": "one short sentence of reasoning"},
            "tool": {"type": "string", "enum": names},
            "args": {"type": "object", "description": "the tool's arguments"}},
            "required": ["tool", "args"]}

    def _prompt(self, messages: list[dict], tools: list[dict] | None) -> tuple[str, int]:
        start = 1 if self.cursor == 0 else self.cursor
        new = messages[start:]
        parts = []
        for m in new:
            role = m.get("role")
            if role == "user":
                parts.append("USER:\n" + (m.get("content") or ""))
            elif role == "tool":
                parts.append("TOOL RESULT:\n" + (m.get("content") or ""))
            # our own assistant messages are already in the resumed session
        if not parts:
            parts.append("Continue.")
        head = ("Choose the next tool call." if tools else
                "KNOWLEDGE STEP. Do not call a tool; reply in plain text.")
        return head + "\n\n" + "\n\n".join(parts), len(messages)

    def _once(self, prompt: str, system: str, schema: dict | None) -> dict:
        cmd = [self.binary, "-p", "--model", self.model, "--output-format", "json",
               "--tools", "", "--permission-mode", "bypassPermissions", "--safe-mode",
               "--system-prompt", system]
        if schema is not None:
            cmd += ["--json-schema", json.dumps(schema)]
        if self.started:
            cmd += ["--resume", self.session_id]
        else:
            cmd += ["--session-id", self.session_id]
        try:
            proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True,
                                  timeout=self.timeout, cwd=self.cwd, env=self._env())
        except FileNotFoundError as e:
            raise LLMError(f"{self.binary}: not found") from e
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"Claude CLI timed out after {self.timeout:.0f}s") from e
        out, err = proc.stdout or "", (proc.stderr or "")[-2000:]
        try:
            data = json.loads(out)
        except ValueError:
            # `--output-format json` normally prints one object; be tolerant of a stray line.
            line = next((ln for ln in reversed(out.splitlines()) if ln.strip().startswith("{")), "")
            try:
                data = json.loads(line)
            except ValueError as e:
                detail = err.strip() or out.strip()[:500]
                raise LLMError(f"Claude CLI exit {proc.returncode}: {detail}") from e
        if data.get("is_error") or data.get("subtype") not in (None, "success"):
            raise LLMError("Claude CLI: " + str(data.get("result") or data.get("subtype"))[:500])
        if proc.returncode != 0:
            raise LLMError(f"Claude CLI exit {proc.returncode}: {err.strip()[:500]}")
        return data

    def _usage(self, data: dict) -> dict:
        u = data.get("usage") or {}
        return {"in_hit": int(u.get("cache_read_input_tokens") or 0),
                "in_miss": int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0),
                "out": int(u.get("output_tokens") or 0), "reasoning": 0}

    def _reply(self, data: dict, tools: list[dict] | None, usage: dict) -> Reply:
        usd = 0.0
        if self.billing != "subscription":
            usd = float(data.get("total_cost_usd") or price(usage, self.prices))
        self.usd += usd
        raw = data.get("structured_output")
        if raw is None:
            raw = data.get("result")
        if tools and isinstance(raw, str):
            text = raw.strip()
            if text.startswith("```"):
                text = text.strip("`").lstrip("json").strip()
            try:
                raw = json.loads(text)
            except ValueError:
                return Reply({"role": "assistant", "content": text}, usage, usd, "stop")
        if tools and isinstance(raw, dict) and raw.get("tool"):
            name = str(raw["tool"])
            if name not in {t["function"]["name"] for t in tools}:
                return Reply({"role": "assistant", "content": f"(unknown tool {name})"}, usage, usd, "stop")
            args = raw.get("args")
            return Reply({"role": "assistant", "content": raw.get("thought") or "", "tool_calls": [{
                "id": "call_" + uuid.uuid4().hex[:12], "type": "function",
                "function": {"name": name, "arguments": json.dumps(args if isinstance(args, dict) else {})}}]},
                usage, usd, "tool_calls")
        text = raw if isinstance(raw, str) else json.dumps(raw or "")
        return Reply({"role": "assistant", "content": text}, usage, usd, "stop")

    def complete(self, messages: list[dict], tools: list[dict] | None = None) -> Reply:
        system = self._system(messages, tools)
        schema = self._schema(tools) if tools else None
        prompt, cursor = self._prompt(messages, tools)
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                data = self._once(prompt, system, schema)
                self.started = True
                self.cursor = cursor
                return self._reply(data, tools, self._usage(data))
            except LLMError as e:
                last = e
                text = str(e)
                if any(s in text for s in ("not logged in", "OAuth", "401", "403", "authentication")):
                    raise
                # The CLI writes a session before it fails, so a retry must not reuse that id.
                if not self.started:
                    self.session_id = str(uuid.uuid4())
                # A stale/never-written session: start a new one with the whole transcript.
                elif "No conversation found" in text:
                    self.started = False
                    self.session_id = str(uuid.uuid4())
                    self.cursor = 0
                    prompt, cursor = self._prompt(messages, tools)
                if attempt < self.max_retries:
                    time.sleep(3 * (attempt + 1))
        raise last or LLMError("Claude CLI failed")


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
    if agent.get("provider") == "claude":
        return ClaudeCLI(model=agent.get("model", "opus"), binary=agent.get("binary", "claude"),
                         config_dir=agent.get("config_dir"), billing=agent.get("billing", "subscription"),
                         price=agent.get("price"), timeout=float(agent.get("timeout", 900)),
                         max_retries=int(agent.get("max_retries", 2)))
    return OpenAICompat(base_url=agent["base_url"], model=agent["model"],
                        api_key_env=agent.get("api_key_env"),
                        temperature=float(agent.get("temperature", 0.7)),
                        max_tokens=int(agent.get("max_tokens", 16384)),
                        max_tokens_ceiling=int(agent.get("max_tokens_ceiling", 65536)),
                        price=agent.get("price"), extra=agent.get("extra"))
