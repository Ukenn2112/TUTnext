# Re-export public API for convenience
from tutnext.services.gakuen.errors import (
    GakuenAPIError,
    GakuenDataError,
    GakuenLoginError,
    GakuenNetworkError,
    GakuenPermissionError,
)
from tutnext.services.gakuen.client import GakuenAPI

__all__ = [
    "GakuenAPI",
    "GakuenAPIError",
    "GakuenDataError",
    "GakuenLoginError",
    "GakuenNetworkError",
    "GakuenPermissionError",
]
