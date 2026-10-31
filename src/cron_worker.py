"""Cloudflare Python Workers entrypoint for the ``tutnext-cron`` Worker.

Runs only the Cron Trigger (see ``tutnext.scheduler``); HTTP traffic is served by
the separate ``tutnext`` API Worker behind ``tutnext-gateway``.  Keeping cron in its
own Worker means its isolates never share Pyodide with API requests ("Cannot enter a
promising task" collisions) and a broken cron isolate cannot take the API down.

Nothing here imports FastAPI or pdfplumber at module level, so cold starts only load
the push / D1 path (the weekly bus job imports pdfplumber lazily).

Deploy with ``uv run pywrangler deploy -c wrangler.cron.jsonc``.
"""
from workers import Response, WorkerEntrypoint

from tutnext import runtime

# Imported at module level on purpose: Python Workers snapshot top-level imports at deploy
# time, so a cold start does not re-import them. Since 2026-10-22 ~11:00 JST every cron
# invocation starts cold, and with these as lazy imports each minute paid ~2.5 s of CPU
# re-importing them (cron CPU went from ~35 ms to ~2500 ms per run). None of them reads
# the Worker env at import time. pdfplumber (weekly bus job) stays lazy.
from tutnext import scheduler  # noqa: E402,F401
from tutnext.services.push import apns_client, breaker, live_activity, monitor_queue, pool, sender  # noqa: E402,F401


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("tutnext-cron: no HTTP routes", status=404)

    async def scheduled(self, controller, env, ctx):
        # `self.env` is the SDK-wrapped env (bindings convert Python args/results);
        # the positional `env` here is the raw JS object.
        runtime.set_env(self.env)
        from tutnext.scheduler import run_cron

        await run_cron(controller.cron, getattr(controller, "scheduledTime", None))
