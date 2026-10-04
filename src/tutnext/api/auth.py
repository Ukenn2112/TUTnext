"""Caller verification for routes that read or change a student's data.

Routes identify the student by ``username`` (an enumerable student ID), so the
credential that comes with the request is what proves the caller is that student:

* :func:`stored_password_matches` — the request's ``encryptedPassword`` equals the one
  stored in D1 for that user (constant-time; no school login needed);
* :func:`ensure_caller` — the above, or else one T-NEXT login with the request's
  credential (raises :class:`GakuenAPIError` when the school rejects it).

Cached personal data (``{user}:kadai``, ``schedule:ical:{user}``) must only be
served after one of these checks.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from tutnext.core.database import db_manager
from tutnext.services.gakuen.session_manager import get_session_manager

logger = logging.getLogger(__name__)


def _same(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


async def stored_password_matches(username: str, encrypted_password: str) -> bool:
    user = await db_manager.get_user(username)
    return bool(user) and _same(user.get("encryptedpassword"), encrypted_password)


async def ensure_caller(username: str, encrypted_password: str) -> None:
    """Return if the caller holds this student's credential; raise GakuenAPIError otherwise."""
    if await stored_password_matches(username, encrypted_password):
        return
    # Not (or differently) stored: let T-NEXT decide. The session is cached, so a route
    # that logs in right after this does not log in twice.
    async with get_session_manager().acquire(username, encrypted_password):
        pass


def _pbkdf2_sha256(password: bytes, salt: bytes, iterations: int) -> bytes:
    """PBKDF2-HMAC-SHA256, one 32-byte block. Pyodide's hashlib has no pbkdf2_hmac."""
    if hasattr(hashlib, "pbkdf2_hmac"):
        return hashlib.pbkdf2_hmac("sha256", password, salt, iterations)
    block = 64
    key = hashlib.sha256(password).digest() if len(password) > block else password
    key = key.ljust(block, b"\0")
    inner = hashlib.sha256(bytes(b ^ 0x36 for b in key))
    outer = hashlib.sha256(bytes(b ^ 0x5C for b in key))

    def prf(msg: bytes) -> bytes:
        i, o = inner.copy(), outer.copy()
        i.update(msg)
        o.update(i.digest())
        return o.digest()

    u = prf(salt + b"\x00\x00\x00\x01")
    acc = int.from_bytes(u, "big")
    for _ in range(iterations - 1):
        u = prf(u)
        acc ^= int.from_bytes(u, "big")
    return acc.to_bytes(32, "big")


def credential_digest(username: str, password: str) -> str:
    """Tag binding a cache entry to the credential that produced it (slow hash: the
    plaintext school password must not be cheaply recoverable from a D1 dump)."""
    return _pbkdf2_sha256(password.encode(), b"tutnext-cache:" + username.encode(), 2000).hex()


def digest_matches(expected: str, username: str, password: str) -> bool:
    return hmac.compare_digest(expected, credential_digest(username, password))


async def reject_unless_caller(response, username: str, encrypted_password: str) -> dict | None:
    """Route helper: None when the caller is verified, else an error body (status set on *response*)."""
    from tutnext.services.gakuen.errors import GakuenAPIError

    try:
        await ensure_caller(username, encrypted_password)
        return None
    except GakuenAPIError as e:
        response.status_code = 403
        return {"status": False, "message": str(e)}  # the school's own (user-facing) message
    except Exception:  # noqa: BLE001 — T-NEXT unreachable etc.; nothing was verified
        logger.exception("caller verification failed for %s", username)
        response.status_code = 503
        return {"status": False, "message": "認証を確認できませんでした。しばらくしてから再度お試しください。"}
