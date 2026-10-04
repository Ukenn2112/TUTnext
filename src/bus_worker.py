"""Cloudflare Python Workers entrypoint for the ``tutnext-bus`` Worker (/bus routes).

Reached only through ``tutnext-gateway``'s ``BUS`` service binding.
Deploy with ``uv run pywrangler deploy -c wrangler.bus.jsonc``.
"""
from workers import WorkerEntrypoint, asgi

from tutnext import runtime
from tutnext.api.bus_app import app


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        runtime.set_env(self.env)
        return await asgi.fetch(app, request, self.env, self.ctx)
