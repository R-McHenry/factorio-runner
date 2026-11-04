"""Monthly spend, kept in the store, and the guard that refuses work past the cap.

DeepSeek keys have no spending cap of their own, so the runner enforces the budget: every episode
adds its cost to `ledger/<YYYY-MM>.json`, and `check` refuses a new episode once the month's spend
for that provider has reached its cap.
"""
from __future__ import annotations

import time


class OverBudget(RuntimeError):
    pass


def month() -> str:
    return time.strftime("%Y-%m", time.gmtime())


def spent(store, provider: str, m: str | None = None) -> float:
    led = store.get_json(f"ledger/{m or month()}.json") or {}
    return float(led.get("usd", {}).get(provider, 0.0))


def check(store, provider: str, cap: float, reserve: float = 0.0):
    s = spent(store, provider)
    if s + reserve > cap:
        raise OverBudget(f"{provider}: ${s:.2f} spent this month, cap ${cap:.2f}")


def add(store, provider: str, usd: float, episodes: int = 1):
    def fn(led):
        led = led or {"usd": {}, "episodes": {}}
        led["usd"][provider] = round(led["usd"].get(provider, 0.0) + usd, 6)
        led["episodes"][provider] = led["episodes"].get(provider, 0) + episodes
        return led
    store.update_json(f"ledger/{month()}.json", fn)
