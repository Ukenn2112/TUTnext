# tests/conftest.py
# Shared pytest fixtures for TUTnext test suite.
import os
import pytest
import fakeredis.aioredis
from unittest.mock import AsyncMock, patch

# Set environment variables BEFORE importing any tutnext modules
# so pydantic-settings picks them up at import time.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost/testdb")
os.environ.setdefault("APNS_KEY_FILE", "")
os.environ.setdefault("APNS_KEY_ID", "")
os.environ.setdefault("APNS_TEAM_ID", "")
os.environ.setdefault("APNS_TOPIC", "")


# ---------------------------------------------------------------------------
# Settings fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def test_settings():
    """Return a Settings instance pointing to the test database and no APNs."""
    from tutnext.config import settings
    return settings


# ---------------------------------------------------------------------------
# Fakeredis fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def fake_redis():
    """Async fakeredis client, isolated per test."""
    r = fakeredis.aioredis.FakeRedis()
    yield r
    await r.aclose()


# ---------------------------------------------------------------------------
# Patch tutnext.config.redis with fakeredis for route/monitor tests
# ---------------------------------------------------------------------------

@pytest.fixture
async def patched_redis(fake_redis):
    """Replace the module-level redis singleton with fakeredis."""
    with patch("tutnext.config.redis", fake_redis), \
         patch("tutnext.api.routes.kadai.redis", fake_redis), \
         patch("tutnext.api.routes.bus.redis", fake_redis), \
         patch("tutnext.services.push.pool.redis", fake_redis), \
         patch("tutnext.services.push.monitor.redis", fake_redis):
        yield fake_redis


# ---------------------------------------------------------------------------
# FastAPI TestClient (httpx-based async client)
# ---------------------------------------------------------------------------

@pytest.fixture
async def async_client(patched_redis):
    """
    Async HTTPX client backed by the FastAPI app.
    Database init is mocked so no real Postgres is needed.
    """
    from httpx import AsyncClient, ASGITransport
    from unittest.mock import AsyncMock

    with patch("tutnext.core.database.db_manager.init_db", AsyncMock()), \
         patch("tutnext.core.database.db_manager.close", AsyncMock()), \
         patch("tutnext.core.database.db_manager.get_user_tokens", AsyncMock(return_value=None)):
        from tutnext.api.app import app
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client


# ---------------------------------------------------------------------------
# pytest markers
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks tests as integration tests that hit real external services",
    )
