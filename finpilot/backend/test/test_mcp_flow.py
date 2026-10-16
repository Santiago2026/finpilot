"""Pruebas de flujo end-to-end: MCP -> registro -> recursos -> FineractClient.

Todo se ejecuta en memoria; las llamadas HTTP a Fineract se interceptan con
``respx``. No hay red ni servidor externo.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx
from mcp import ClientSession
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.fineract.client import FineractConfigurationError
from app.main import create_app
from conftest import connected_mcp_session, make_test_settings

FINERACT_CLIENTS_URL = (
    "https://fineract.test:8443/fineract-provider/api/v1/clients"
)
PAGE_PAYLOAD = {
    "pageItems": [
        {"id": 1, "displayName": "Ana Pérez", "active": True, "officeName": "Central"},
        {"id": 2, "displayName": "Ana Gómez", "active": False, "officeName": "Norte"},
    ],
    "totalFilteredRecords": 2,
}
GET_CLIENT_PAYLOAD = {
    "id": 1,
    "displayName": "Ana Pérez",
    "active": True,
    "officeName": "Central",
    "phoneNumber": "555-0100",
}


def _structured(result) -> dict:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", None) == "text")


async def test_search_clients_flow_success(mcp_settings: Settings) -> None:
    with respx.mock:
        route = respx.get(FINERACT_CLIENTS_URL).mock(
            return_value=httpx.Response(200, json=PAGE_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("search_clients", {"displayName": "Ana"})

        assert result.is_error is False
        data = _structured(result)
        assert data["totalFilteredRecords"] == 2
        assert [c["displayName"] for c in data["clients"]] == ["Ana Pérez", "Ana Gómez"]

        request = route.calls[0].request
        params = request.url.params
        assert params["displayName"] == "Ana"
        assert params["limit"] == "20"
        assert params["fields"] == "id,displayName,active,officeName"
        assert params["orderBy"] == "displayName"
        assert params["sortOrder"] == "ASC"
        assert request.headers["Fineract-Platform-TenantId"] == "test-tenant"
        assert request.headers["authorization"].startswith("Basic ")


async def test_search_clients_uses_explicit_limit(mcp_settings: Settings) -> None:
    with respx.mock:
        route = respx.get(FINERACT_CLIENTS_URL).mock(
            return_value=httpx.Response(200, json=PAGE_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool(
                "search_clients", {"displayName": "Ana", "limit": 5}
            )
        assert result.is_error is False
        assert route.calls[0].request.url.params["limit"] == "5"


async def test_get_client_flow_minimizes_fields(mcp_settings: Settings) -> None:
    with respx.mock:
        route = respx.get(f"{FINERACT_CLIENTS_URL}/1").mock(
            return_value=httpx.Response(200, json=GET_CLIENT_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("get_client", {"clientId": 1})

        assert result.is_error is False
        data = _structured(result)
        assert data == {
            "id": 1,
            "displayName": "Ana Pérez",
            "active": True,
            "officeName": "Central",
        }
        assert "phoneNumber" not in data
        assert route.calls[0].request.url.params["fields"] == "id,displayName,active,officeName"


async def test_get_client_not_found_is_safe_error(mcp_settings: Settings) -> None:
    with respx.mock:
        respx.get(f"{FINERACT_CLIENTS_URL}/999").mock(
            return_value=httpx.Response(
                404, json={"developerMessage": "sentinel-secret-body"}
            )
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("get_client", {"clientId": 999})

        assert result.is_error is True
        message = _text(result)
        assert "HTTP 404" in message
        assert "sentinel-secret-body" not in message


async def test_upstream_500_hides_body_and_reports_status(mcp_settings: Settings) -> None:
    with respx.mock:
        respx.get(FINERACT_CLIENTS_URL).mock(
            return_value=httpx.Response(
                500, json={"message": "sentinel-secret-body", "stack": "sentinel-secret-body"}
            )
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("search_clients", {"displayName": "Ana"})

        assert result.is_error is True
        message = _text(result)
        assert "HTTP 500" in message
        assert "sentinel-secret-body" not in message
        assert "Exception" not in message


async def test_timeout_is_reported_as_safe_error(mcp_settings: Settings) -> None:
    with respx.mock:
        respx.get(FINERACT_CLIENTS_URL).mock(side_effect=httpx.ReadTimeout("boom"))
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("search_clients", {"displayName": "Ana"})

        assert result.is_error is True
        message = _text(result)
        assert "tiempo" in message.lower()
        assert "boom" not in message


async def test_no_secrets_in_logs_on_error(
    mcp_settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level("DEBUG"):
        with respx.mock:
            respx.get(FINERACT_CLIENTS_URL).mock(
                return_value=httpx.Response(500, json={"message": "sentinel-secret-body"})
            )
            async with connected_mcp_session(mcp_settings) as session:
                await session.call_tool("search_clients", {"displayName": "Ana"})

    assert "test-pass" not in caplog.text
    assert "test-user:test-pass" not in caplog.text
    assert "sentinel-secret-body" not in caplog.text


async def test_only_get_requests_are_issued(mcp_settings: Settings) -> None:
    with respx.mock:
        route = respx.get(FINERACT_CLIENTS_URL).mock(
            return_value=httpx.Response(200, json=PAGE_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            await session.call_tool("search_clients", {"displayName": "Ana"})

        assert route.calls
        assert {call.request.method for call in route.calls} == {"GET"}


async def test_mcp_startup_fails_fast_with_incomplete_config() -> None:
    """Con MCP habilitado y config incompleta, el arranque falla (fail-closed).

    El ``FineractClient`` se construye una vez en el lifespan; si su
    configuración es inválida, el servidor no arranca a medias. El mensaje
    nombra la variable faltante pero nunca su valor.
    """
    settings = make_test_settings(fineract_password=SecretStr(""))
    application = create_app(settings)
    with pytest.raises(FineractConfigurationError) as excinfo:
        async with application.router.lifespan_context(application):
            pass
    assert "FINERACT_PASSWORD" in str(excinfo.value)
