"""Runtime detection and Cloudflare Workers ``env`` access.

TUTnext runs in two modes:

* **server** — the classic long-running process (``python -m tutnext``) with
  PostgreSQL, Redis and aiohttp.
* **workers** — Cloudflare Python Workers (Pyodide).  Configuration comes from
  Worker vars/secrets, storage from the ``DB`` D1 binding and HTTP from
  ``fetch``.

Nothing in this module touches the Worker ``env`` at import time: Python
Workers snapshot the module graph at deploy time, when no bindings exist, so
every access has to be lazy.
"""
from __future__ import annotations

import sys
from typing import Any

IS_WORKERS: bool = sys.platform == "emscripten"

_env: Any = None


def set_env(env: Any) -> None:
    """Remember the ``env`` object handed to the current fetch/scheduled invocation."""
    global _env
    _env = env


def get_env() -> Any:
    """Return the Worker ``env`` (bindings, vars and secrets)."""
    if _env is not None:
        return _env
    if not IS_WORKERS:
        raise RuntimeError("Cloudflare Workers env is only available inside a Worker")
    from workers import env as workers_env  # cloudflare:workers global env

    return workers_env


def binding(name: str) -> Any:
    """Return a binding (D1, KV, Assets, ...) by its wrangler binding name."""
    return getattr(get_env(), name)


def env_var(name: str, default: str | None = None) -> str | None:
    """Return a string var/secret from the Worker env, or *default*."""
    try:
        value = getattr(get_env(), name)
    except Exception:  # noqa: BLE001 — missing attribute surfaces as AttributeError/JsException
        return default
    return value if isinstance(value, str) else default


def env_vars() -> dict[str, str]:
    """Return every string-valued var/secret of the Worker env."""
    try:
        env = get_env()
        import js  # type: ignore[import-not-found]

        raw = getattr(env, "_env", env)
        keys = js.Object.keys(raw).to_py()
    except Exception:  # noqa: BLE001
        return {}
    result: dict[str, str] = {}
    for key in keys:
        value = env_var(key)
        if value is not None:
            result[key] = value
    return result
