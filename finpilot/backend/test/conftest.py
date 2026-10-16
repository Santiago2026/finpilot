from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app

TEST_BASE_URL = "http://127.0.0.1:8000"
MCP_URL = f"{TEST_BASE_URL}/mcp/"


def make_test_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "fineract_base_url": "https://fineract.test:8443/fineract-provider",
        "fineract_tenant_id": "test-tenant",
        "fineract_username": "test-user",
        "fineract_password": SecretStr("test-pass"),
        "fineract_timeout_seconds": 5.0,
        "fineract_verify_tls": True,
        "mcp_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def mcp_settings() -> Settings:
    return make_test_settings()


@asynccontextmanager
async def connected_mcp_session(
    settings: Settings | None = None,
    *,
    url: str | None = None,
    application=None,
) -> AsyncIterator[ClientSession]:
    """Arranca la app (lifespan + gestor de sesiones MCP) y conecta un cliente.

    Se usa como context manager dentro del test (y no como async fixture)
    para que los cancel scopes de anyio entren y salgan en la misma tarea.
    ``application`` permite reutilizar una app ya construida; ``url`` permite
    variar el endpoint (por ejemplo, sin barra final).
    """
    app = application or create_app(settings or make_test_settings())
    target = url or MCP_URL
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=TEST_BASE_URL) as hc:
            async with streamable_http_client(target, http_client=hc) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    yield session
