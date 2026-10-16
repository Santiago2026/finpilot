from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.fineract.client import (
    FineractClient,
    FineractConfigurationError,
    FineractConnectionError,
    FineractHTTPError,
    FineractResponseError,
    FineractTimeoutError,
)
from app.tools.registry import (
    ToolNotFoundError,
    ToolValidationError,
    UpstreamError,
    dispatch,
    tool_names,
)
from app.tools.schemas import ClientSummary, SearchClientsResult


def make_client() -> FineractClient:
    settings = Settings(
        fineract_base_url="https://fineract.test:8443/fineract-provider",
        fineract_tenant_id="t",
        fineract_username="u",
        fineract_password=SecretStr("p"),
        fineract_verify_tls=True,
    )
    return FineractClient(settings)


def test_allowlist_contains_only_read_tools() -> None:
    assert tool_names() == frozenset({"search_clients", "get_client"})


@pytest.mark.parametrize("name", ["create_client", "transfer_funds", "delete_client", "", "SEARCH_CLIENTS"])
async def test_unknown_or_write_tools_are_rejected(name: str) -> None:
    fineract = AsyncMock(spec=FineractClient)
    with pytest.raises(ToolNotFoundError):
        await dispatch(name, {}, fineract)
    fineract.get.assert_not_called()


async def test_search_clients_dispatches_validated_args(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = SearchClientsResult(totalFilteredRecords=0, clients=[])
    mock_search = AsyncMock(return_value=expected)
    monkeypatch.setattr("app.tools.registry.fineract_clients.search_clients", mock_search)

    async with make_client() as fineract:
        result = await dispatch("search_clients", {"displayName": "Ana", "limit": 5}, fineract)

    assert result is expected
    args = mock_search.await_args.args[1]
    assert args.display_name == "Ana" and args.limit == 5


async def test_get_client_dispatches_validated_args(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = ClientSummary(id=42, displayName="Ana")
    mock_get = AsyncMock(return_value=expected)
    monkeypatch.setattr("app.tools.registry.fineract_clients.get_client", mock_get)

    async with make_client() as fineract:
        result = await dispatch("get_client", {"clientId": 42}, fineract)

    assert result is expected
    args = mock_get.await_args.args[1]
    assert args.client_id == 42


@pytest.mark.parametrize(
    "name,raw",
    [
        ("search_clients", {"limit": 0}),
        ("search_clients", {"limit": 101}),
        ("search_clients", {"displayName": "Ana", "officeId": 1}),
        ("get_client", {}),
        ("get_client", {"clientId": 0}),
        ("get_client", {"clientId": 1, "extra": True}),
    ],
)
async def test_invalid_arguments_are_rejected(name: str, raw: dict[str, Any]) -> None:
    fineract = AsyncMock(spec=FineractClient)
    with pytest.raises(ToolValidationError):
        await dispatch(name, raw, fineract)
    fineract.get.assert_not_called()


async def test_validation_error_message_does_not_echo_arguments() -> None:
    fineract = AsyncMock(spec=FineractClient)
    with pytest.raises(ToolValidationError) as excinfo:
        await dispatch("search_clients", {"limit": "NombreSecreto123"}, fineract)
    assert "NombreSecreto123" not in str(excinfo.value)
    assert "limit" in str(excinfo.value)


@pytest.mark.parametrize(
    "exc",
    [
        FineractHTTPError(500, "sentinel-secret-500"),
        FineractTimeoutError("sentinel-secret-timeout"),
        FineractConnectionError("sentinel-secret-refused"),
        FineractResponseError("sentinel-secret-badjson"),
        FineractConfigurationError("sentinel-secret-missing"),
    ],
)
async def test_fineract_errors_map_to_safe_upstream_errors(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    mock_search = AsyncMock(side_effect=exc)
    monkeypatch.setattr("app.tools.registry.fineract_clients.search_clients", mock_search)

    async with make_client() as fineract:
        with pytest.raises(UpstreamError) as excinfo:
            await dispatch("search_clients", {"displayName": "Ana"}, fineract)

    error = excinfo.value
    assert "sentinel-secret" not in str(error)
    if isinstance(exc, FineractHTTPError):
        assert error.category == "http" and error.status_code == 500
        assert "HTTP 500" in str(error)


async def test_http_error_message_contains_no_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_get = AsyncMock(
        side_effect=FineractHTTPError(403)
    )
    monkeypatch.setattr("app.tools.registry.fineract_clients.get_client", mock_get)

    async with make_client() as fineract:
        with pytest.raises(UpstreamError) as excinfo:
            await dispatch("get_client", {"clientId": 1}, fineract)

    assert "403" in str(excinfo.value)
    assert excinfo.value.status_code == 403
