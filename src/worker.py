"""Cloudflare Python Workers entrypoint for TUTnext.

* HTTP requests are served by the existing FastAPI application through the
  Workers ASGI adapter.
* Cron Triggers replace the background asyncio loops of ``python -m tutnext``
  (see ``tutnext.scheduler``).

Deploy with ``uv run pywrangler deploy`` (or ``cf deploy``).
"""
from workers import WorkerEntrypoint, asgi

from tutnext import runtime
from tutnext.api.app import app


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        runtime.set_env(self.env)
        return await asgi.fetch(app, request, self.env, self.ctx)

    async def scheduled(self, controller, env, ctx):
        # `self.env` is the SDK-wrapped env (bindings convert Python args/results);
        # the positional `env` here is the raw JS object.
        runtime.set_env(self.env)
        from tutnext.scheduler import run_cron

        await run_cron(controller.cron)
