"""Pruebas de protocolo MCP en memoria (sin red, sin Fineract real)."""
from __future__ import annotations

import httpx
import httpx2
import pytest
import respx
from mcp import ClientSession
from pydantic import SecretStr
from starlette.responses import JSONResponse

from app.core.config import Settings
from app.core.logging import configure_logging
from app.mcp.server import DevTokenMiddleware
from app.main import create_app
from conftest import MCP_URL, connected_mcp_session, make_test_settings


def _tool(tools, name: str):
    for t in tools.tools:
        if t.name == name:
            return t
    raise AssertionError(f"herramienta {name!r} no publicada")


async def test_initialize_identifies_server(mcp_settings: Settings) -> None:
    async with connected_mcp_session(mcp_settings) as session:
        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == {"search_clients", "get_client"}


async def test_tool_descriptions_state_read_only(mcp_settings: Settings) -> None:
    async with connected_mcp_session(mcp_settings) as session:
        tools = await session.list_tools()
        for name in ("search_clients", "get_client"):
            description = (_tool(tools, name).description or "").lower()
            assert "lectura" in description


async def test_search_clients_schema_contract(mcp_settings: Settings) -> None:
    async with connected_mcp_session(mcp_settings) as session:
        schema = _tool(await session.list_tools(), "search_clients").input_schema
        props = schema["properties"]

        assert set(props) == {"displayName", "limit"}
        display_types = {option["type"] for option in props["displayName"]["anyOf"]}
        assert display_types == {"string", "null"}
        limit_types = {option.get("type") for option in props["limit"]["anyOf"]}
        assert limit_types == {"integer", "null"}
        limit_constraints = next(
            option for option in props["limit"]["anyOf"] if option.get("type") == "integer"
        )
        assert limit_constraints["minimum"] == 1
        assert limit_constraints["maximum"] == 100
        assert schema.get("required", []) == []


async def test_get_client_schema_contract(mcp_settings: Settings) -> None:
    async with connected_mcp_session(mcp_settings) as session:
        schema = _tool(await session.list_tools(), "get_client").input_schema
        assert schema["required"] == ["clientId"]
        assert schema["properties"]["clientId"]["type"] == "integer"
        assert schema["properties"]["clientId"]["minimum"] == 1


async def test_unknown_tool_is_error(mcp_settings: Settings) -> None:
    async with connected_mcp_session(mcp_settings) as session:
        result = await session.call_tool("get_account", {"accountId": 1})
        assert result.is_error is True


@pytest.mark.parametrize(
    "name,args",
    [
        ("search_clients", {"displayName": 123}),
        ("search_clients", {"limit": 0}),
        ("get_client", {}),
        ("get_client", {"clientId": "abc"}),
    ],
)
async def test_invalid_arguments_are_errors_without_fineract(
    mcp_settings: Settings, name: str, args: dict
) -> None:
    async with connected_mcp_session(mcp_settings) as session:
        with respx.mock:
            fineract_route = respx.route().mock(return_value=httpx.Response(200, json=[]))
            result = await session.call_tool(name, args)
            assert result.is_error is True
            assert fineract_route.call_count == 0


async def test_extra_arguments_are_rejected_by_protocol(mcp_settings: Settings) -> None:
    """El protocolo rechaza argumentos extra antes de invocar la herramienta.

    El esquema publicado deriva de ``app.tools.schemas`` (``extra="forbid"``),
    así que el protocolo no descarta en silencio ni reenvía el argumento extra:
    responde con error y Fineract nunca se consulta.
    """
    async with connected_mcp_session(mcp_settings) as session:
        with respx.mock:
            fineract_route = respx.route().mock(return_value=httpx.Response(200, json=[]))
            result = await session.call_tool(
                "search_clients", {"displayName": "Ana", "officeId": 1}
            )
            assert result.is_error is True
            assert fineract_route.call_count == 0


@pytest.mark.parametrize(
    "name,args,field",
    [
        ("search_clients", {"limit": 0}, "limit"),
        ("search_clients", {"displayName": "Ana", "officeId": 1}, "officeId"),
        ("get_client", {"clientId": "abc"}, "clientId"),
    ],
)
async def test_validation_errors_are_sanitized(
    mcp_settings: Settings, name: str, args: dict, field: str
) -> None:
    """El error conserva el campo y el tipo, sin eco ni detalles internos (H4)."""
    async with connected_mcp_session(mcp_settings) as session:
        result = await session.call_tool(name, args)
    assert result.is_error is True
    message = "".join(c.text for c in result.content if getattr(c, "type", None) == "text")
    assert field in message
    assert "input_value" not in message
    assert "Arguments" not in message
    assert "errors.pydantic.dev" not in message
    assert "Traceback" not in message


async def test_docs_disabled_by_default() -> None:
    application = create_app(make_test_settings())
    async with application.router.lifespan_context(application):
        transport = httpx2.ASGITransport(app=application)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as hc:
            for path in ("/docs", "/redoc", "/openapi.json"):
                assert (await hc.get(path)).status_code == 404, path
            assert (await hc.get("/health")).status_code == 200


async def test_docs_enabled_when_configured() -> None:
    application = create_app(make_test_settings(api_docs_enabled=True))
    async with application.router.lifespan_context(application):
        transport = httpx2.ASGITransport(app=application)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000"
        ) as hc:
            for path in ("/docs", "/redoc", "/openapi.json"):
                assert (await hc.get(path)).status_code == 200, path


@pytest.mark.parametrize("name", ["search_clients", "get_client"])
async def test_published_schema_forbids_additional_properties(
    mcp_settings: Settings, name: str
) -> None:
    async with connected_mcp_session(mcp_settings) as session:
        schema = _tool(await session.list_tools(), name).input_schema
        assert schema.get("additionalProperties") is False


async def test_mcp_route_does_not_redirect(mcp_settings: Settings) -> None:
    """``/mcp`` y ``/mcp/`` sirven MCP sin redirección 307/308."""
    application = create_app(mcp_settings)
    async with application.router.lifespan_context(application):
        transport = httpx2.ASGITransport(app=application)
        async with httpx2.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8000", follow_redirects=False
        ) as hc:
            for path in ("/mcp", "/mcp/"):
                response = await hc.post(
                    path, json={}, headers={"Accept": "application/json"}
                )
                assert response.status_code not in (301, 302, 307, 308), path


async def test_mcp_without_trailing_slash_serves_tools(mcp_settings: Settings) -> None:
    url = "http://127.0.0.1:8000/mcp"
    async with connected_mcp_session(mcp_settings, url=url) as session:
        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == {"search_clients", "get_client"}


async def test_apps_do_not_share_session_manager(mcp_settings: Settings) -> None:
    """Construir una segunda app no rompe el gestor de sesiones de la primera.

    Cada ``build_mcp_app`` crea su propio ``StreamableHTTPSessionManager``; el
    lifespan debe arrancar el de su app, no el último global.
    """
    first = create_app(mcp_settings)
    create_app(mcp_settings)
    async with connected_mcp_session(mcp_settings, application=first) as session:
        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == {"search_clients", "get_client"}


async def _asgi_status(app, headers: dict[bytes, bytes]) -> int:
    status: dict[str, int] = {}

    async def receive() -> dict:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: dict) -> None:
        if message["type"] == "http.response.start":
            status["code"] = message["status"]

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/mcp/",
        "headers": list(headers.items()),
    }
    await app(scope, receive, send)
    return status["code"]


async def test_dev_token_non_ascii_does_not_crash() -> None:
    """Un token de desarrollo no ASCII no debe provocar un 500 (H1)."""
    calls: list[bool] = []

    async def downstream(scope, receive, send) -> None:
        calls.append(True)
        await JSONResponse({"ok": True})(scope, receive, send)

    token = SecretStr("contraseña-ñ-✓")
    app = DevTokenMiddleware(downstream, token)

    accepted = await _asgi_status(
        app, {b"x-mcp-token": token.get_secret_value().encode("utf-8")}
    )
    assert accepted == 200
    assert calls == [True]

    rejected = await _asgi_status(app, {b"x-mcp-token": b"otro"})
    assert rejected == 401
    assert calls == [True]


async def test_dev_token_missing_is_rejected() -> None:
    async def downstream(scope, receive, send) -> None:
        await JSONResponse({"ok": True})(scope, receive, send)

    app = DevTokenMiddleware(downstream, SecretStr("dev-token-123"))
    assert await _asgi_status(app, {}) == 401


def test_http2_loggers_are_quieted() -> None:
    import logging

    configure_logging("INFO")
    assert logging.getLogger("httpx2").level == logging.WARNING
    assert logging.getLogger("httpcore2").level == logging.WARNING


def test_disabled_by_default_and_no_mcp_mounted() -> None:
    settings = Settings(
        fineract_base_url="https://fineract.test:8443/fineract-provider",
        fineract_tenant_id="t",
        fineract_username="u",
        fineract_password=SecretStr("p"),
    )
    assert settings.mcp_enabled is False
    application = create_app(settings)
    assert not any(getattr(r, "path", "") == "/mcp" for r in application.routes)


async def test_dev_token_enforced_when_configured() -> None:
    settings = make_test_settings(mcp_dev_token=SecretStr("dev-token-123"))
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        transport = httpx2.ASGITransport(app=application)
        async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:8000") as hc:
            unauth = await hc.post(MCP_URL, json={}, headers={"Accept": "application/json"})
            assert unauth.status_code == 401

            auth = await hc.post(
                MCP_URL,
                json={},
                headers={"Accept": "application/json", "X-MCP-Token": "dev-token-123"},
            )
            assert auth.status_code != 401
