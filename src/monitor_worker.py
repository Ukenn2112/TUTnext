"""Cloudflare Python Workers entrypoint for the ``tutnext-monitor`` Worker.

Consumes the ``tutnext-monitor`` queue filled by ``tutnext-cron`` (assignment monitor
every 5 minutes, next-day push at 20:30 JST); see ``tutnext.services.push.monitor_queue``.
Deploy with ``uv run pywrangler deploy -c wrangler.monitor.jsonc``.
"""
from workers import Response, WorkerEntrypoint

from tutnext import runtime

# Module-level on purpose: top-level imports are in the deploy-time memory snapshot, so a
# cold start does not re-import them (see cron_worker.py; lazy imports cost the cron
# Worker ~2.5 s CPU per cold start). None reads the Worker env at import time.
from tutnext.services.gakuen import client, session_manager  # noqa: E402,F401
from tutnext.services.push import breaker, live_activity, monitor, monitor_queue, pool, sender  # noqa: E402,F401
from tutnext.services import google_classroom  # noqa: E402,F401


def _body(message):
    body = message.body if hasattr(message, "body") else message["body"]
    return body if isinstance(body, dict) else {}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response("tutnext-monitor: no HTTP routes", status=404)

    async def queue(self, batch, env, ctx):
        runtime.set_env(self.env)
        from tutnext.services.push.monitor_queue import handle_batch

        messages = list(batch.messages)
        await handle_batch([_body(m) for m in messages], ack=lambda i: messages[i].ack())
