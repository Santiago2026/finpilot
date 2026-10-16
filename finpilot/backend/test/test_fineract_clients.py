from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.fineract.client import (
    FineractClient,
    FineractHTTPError,
    FineractResponseError,
)
from app.integrations.fineract.clients import (
    CLIENT_FIELDS,
    DEFAULT_SEARCH_LIMIT,
    get_client,
    search_clients,
)
from app.tools.schemas import GetClientArgs, SearchClientsArgs

BASE_URL = "https://fineract.test:8443/fineract-provider"
CLIENTS_URL = f"{BASE_URL}/api/v1/clients"


def make_client() -> FineractClient:
    settings = Settings(
        fineract_base_url=BASE_URL,
        fineract_tenant_id="test-tenant",
        fineract_username="test-user",
        fineract_password=SecretStr("test-pass"),
        fineract_timeout_seconds=5.0,
        fineract_verify_tls=True,
    )
    return FineractClient(settings)


def make_args(**overrides: object) -> SearchClientsArgs:
    return SearchClientsArgs.model_validate(overrides)


@respx.mock
async def test_search_builds_verified_query_params() -> None:
    route = respx.get(CLIENTS_URL).mock(
        return_value=httpx.Response(200, json={"totalFilteredRecords": 0, "pageItems": []})
    )

    async with make_client() as client:
        result = await search_clients(client, make_args(displayName="Ana", limit=5))

    assert route.called
    params = route.calls.last.request.url.params
    assert params["displayName"] == "Ana"
    assert params["limit"] == "5"
    assert params["fields"] == CLIENT_FIELDS
    assert params["orderBy"] == "displayName"
    assert params["sortOrder"] == "ASC"
    assert result.total_filtered_records == 0
    assert result.clients == []


@respx.mock
async def test_search_omits_display_name_and_applies_default_limit() -> None:
    route = respx.get(CLIENTS_URL).mock(
        return_value=httpx.Response(200, json={"totalFilteredRecords": 0, "pageItems": []})
    )

    async with make_client() as client:
        await search_clients(client, make_args())

    params = route.calls.last.request.url.params
    assert "displayName" not in params
    assert params["limit"] == str(DEFAULT_SEARCH_LIMIT)


@respx.mock
async def test_search_parses_page_into_reduced_summaries() -> None:
    payload = {
        "totalFilteredRecords": 2,
        "pageItems": [
            {
                "id": 1,
                "displayName": "Ana Gómez",
                "active": True,
                "officeName": "Head Office",
                "mobileNo": "600111222",
                "emailAddress": "ana@example.com",
                "timeline": {"submittedOnDate": [2024, 1, 1]},
            },
            {"id": 2, "displayName": "Luis Paz", "active": False},
        ],
    }
    respx.get(CLIENTS_URL).mock(return_value=httpx.Response(200, json=payload))

    async with make_client() as client:
        result = await search_clients(client, make_args(displayName="a"))

    assert result.total_filtered_records == 2
    first = result.clients[0]
    assert first.id == 1
    assert first.display_name == "Ana Gómez"
    assert first.active is True
    assert first.office_name == "Head Office"
    dumped = first.model_dump(by_alias=True, exclude_none=True)
    assert "mobileNo" not in dumped
    assert "emailAddress" not in dumped
    assert "timeline" not in dumped
    assert result.clients[1].id == 2


@respx.mock
async def test_search_rejects_unexpected_page_shape() -> None:
    respx.get(CLIENTS_URL).mock(return_value=httpx.Response(200, json={"data": []}))

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await search_clients(client, make_args(displayName="Ana"))


@respx.mock
async def test_search_propagates_http_error() -> None:
    respx.get(CLIENTS_URL).mock(return_value=httpx.Response(403, text="secret-body"))

    async with make_client() as client:
        with pytest.raises(FineractHTTPError) as excinfo:
            await search_clients(client, make_args(displayName="Ana"))

    assert excinfo.value.status_code == 403
    assert "secret-body" not in str(excinfo.value)


@respx.mock
async def test_get_client_builds_verified_url_and_fields() -> None:
    route = respx.get(f"{CLIENTS_URL}/42").mock(
        return_value=httpx.Response(
            200, json={"id": 42, "displayName": "Ana", "active": True, "officeName": "HQ"}
        )
    )

    async with make_client() as client:
        summary = await get_client(client, GetClientArgs.model_validate({"clientId": 42}))

    assert route.called
    request = route.calls.last.request
    assert str(request.url.copy_with(query=None)) == f"{CLIENTS_URL}/42"
    assert request.url.params["fields"] == CLIENT_FIELDS
    assert summary.id == 42
    assert summary.display_name == "Ana"


@respx.mock
async def test_get_client_maps_missing_client_to_http_404() -> None:
    respx.get(f"{CLIENTS_URL}/999").mock(return_value=httpx.Response(404, json={}))

    async with make_client() as client:
        with pytest.raises(FineractHTTPError) as excinfo:
            await get_client(client, GetClientArgs.model_validate({"clientId": 999}))

    assert excinfo.value.status_code == 404


@respx.mock
async def test_get_client_rejects_unexpected_shape() -> None:
    respx.get(f"{CLIENTS_URL}/7").mock(return_value=httpx.Response(200, json={"name": "x"}))

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await get_client(client, GetClientArgs.model_validate({"clientId": 7}))
