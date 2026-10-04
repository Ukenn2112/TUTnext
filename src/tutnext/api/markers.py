"""Response marker shared by every FastAPI app behind ``tutnext-gateway``.

The gateway retries a 5xx only when ``x-tutnext-app`` is absent, i.e. when the Workers
runtime (not the app) failed.  App answers, including unhandled errors, must carry it:
by then the handler may already have logged in to T-NEXT, and a replay would repeat that.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

MARKER = "x-tutnext-app"

logger = logging.getLogger(__name__)


def install_gateway_markers(app: FastAPI) -> None:
    @app.middleware("http")
    async def mark_app_response(request: Request, call_next):
        response = await call_next(request)
        response.headers[MARKER] = "1"
        return response

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        # Built by Starlette's outermost ServerErrorMiddleware, outside mark_app_response.
        logger.error("unhandled error on %s: %s", request.url.path, exc, exc_info=exc)
        return JSONResponse({"status": False, "message": str(exc)}, status_code=500, headers={MARKER: "1"})
