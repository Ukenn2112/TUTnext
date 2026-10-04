"""FastAPI app of the ``tutnext-bus`` Worker: only the /bus routes.

School-bus data is public and independent of T-NEXT logins, and it is the only HTTP
path that parses PDFs, so it runs in its own Worker behind ``tutnext-gateway``.
"""
from fastapi import FastAPI

from tutnext.api.markers import install_gateway_markers
from tutnext.api.routes import bus

app = FastAPI()
install_gateway_markers(app)
app.include_router(bus.router, prefix="/bus", tags=["Bus"])
