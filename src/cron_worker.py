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


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("tutnext-cron: no HTTP routes", status=404)

    async def scheduled(self, controller, env, ctx):
        # `self.env` is the SDK-wrapped env (bindings convert Python args/results);
        # the positional `env` here is the raw JS object.
        runtime.set_env(self.env)
        from tutnext.scheduler import run_cron

        await run_cron(controller.cron, getattr(controller, "scheduledTime", None))
