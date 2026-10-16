from __future__ import annotations

import base64
import logging

import httpx
import pytest
import respx
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

BASE_URL = "https://fineract.test:8443/fineract-provider"
CLIENTS_URL = f"{BASE_URL}/api/v1/clients"
USERNAME = "test-user"
PASSWORD = "test-pass"


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "fineract_base_url": BASE_URL,
        "fineract_tenant_id": "test-tenant",
        "fineract_username": USERNAME,
        "fineract_password": SecretStr(PASSWORD),
        "fineract_timeout_seconds": 5.0,
        "fineract_verify_tls": True,
    }
    values.update(overrides)
    return Settings(**values)


@respx.mock
async def test_sends_basic_auth_and_tenant_headers() -> None:
    route = respx.get(CLIENTS_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    async with FineractClient(make_settings()) as client:
        await client.get("api/v1/clients")

    request = route.calls.last.request
    expected = "Basic " + base64.b64encode(f"{USERNAME}:{PASSWORD}".encode()).decode()
    assert request.headers["authorization"] == expected
    assert request.headers["Fineract-Platform-TenantId"] == "test-tenant"


@respx.mock
async def test_builds_correct_url_with_params() -> None:
    route = respx.get(CLIENTS_URL).mock(return_value=httpx.Response(200, json={}))

    async with FineractClient(make_settings()) as client:
        await client.get("/api/v1/clients", params={"limit": 1})

    assert route.called
    request = route.calls.last.request
    assert str(request.url.copy_with(query=None)) == CLIENTS_URL
    assert request.url.params["limit"] == "1"


async def test_applies_timeout_and_tls_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    original_init = httpx.AsyncClient.__init__

    def spy_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy_init)

    client = FineractClient(make_settings())
    try:
        assert captured["auth"] == (USERNAME, PASSWORD)
        assert captured["headers"]["Fineract-Platform-TenantId"] == "test-tenant"  # type: ignore[index]
        assert captured["verify"] is True
        timeout = captured["timeout"]
        assert isinstance(timeout, httpx.Timeout)
        assert timeout.read == 5.0
        assert timeout.connect == 5.0
    finally:
        await client.aclose()


async def test_verify_tls_can_be_disabled_explicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    original_init = httpx.AsyncClient.__init__

    def spy_init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        captured.update(kwargs)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", spy_init)

    client = FineractClient(make_settings(fineract_verify_tls=False))
    try:
        assert captured["verify"] is False
    finally:
        await client.aclose()


async def test_raises_configuration_error_when_credentials_missing() -> None:
    with pytest.raises(FineractConfigurationError):
        FineractClient(make_settings(fineract_username="", fineract_password=SecretStr("")))
    with pytest.raises(FineractConfigurationError):
        FineractClient(make_settings(fineract_password=SecretStr("")))


@respx.mock
async def test_processes_successful_json_response() -> None:
    payload = {"totalFilteredRecords": 1, "pageItems": [{"id": 42}]}
    respx.get(CLIENTS_URL).mock(return_value=httpx.Response(200, json=payload))

    async with FineractClient(make_settings()) as client:
        result = await client.get("api/v1/clients")

    assert result == payload


@respx.mock
async def test_raises_http_error_on_server_error() -> None:
    respx.get(CLIENTS_URL).mock(return_value=httpx.Response(500, text="internal-error"))

    async with FineractClient(make_settings()) as client:
        with pytest.raises(FineractHTTPError) as excinfo:
            await client.get("api/v1/clients")

    assert excinfo.value.status_code == 500
    assert not hasattr(excinfo.value, "response_snippet")


@respx.mock
async def test_error_response_body_is_not_exposed_in_message_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sensitive_body = '{"clientName":"Jane Doe","iban":"ES91-9999","amount":"1234.56"}'
    respx.get(CLIENTS_URL).mock(return_value=httpx.Response(403, text=sensitive_body))

    client = FineractClient(make_settings())
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(FineractHTTPError) as excinfo:
            await client.get("api/v1/clients")
    await client.aclose()

    assert excinfo.value.status_code == 403
    combined = str(excinfo.value) + caplog.text
    for fragment in ("Jane Doe", "ES91-9999", "1234.56", "clientName"):
        assert fragment not in combined


@respx.mock
async def test_raises_http_error_on_unauthorized() -> None:
    respx.get(CLIENTS_URL).mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))

    async with FineractClient(make_settings()) as client:
        with pytest.raises(FineractHTTPError) as excinfo:
            await client.get("api/v1/clients")

    assert excinfo.value.status_code == 401


@respx.mock
async def test_raises_response_error_on_invalid_json() -> None:
    respx.get(CLIENTS_URL).mock(
        return_value=httpx.Response(200, text="not-json", headers={"content-type": "text/plain"})
    )

    async with FineractClient(make_settings()) as client:
        with pytest.raises(FineractResponseError):
            await client.get("api/v1/clients")


@respx.mock
async def test_converts_timeout_into_domain_error() -> None:
    respx.get(CLIENTS_URL).mock(side_effect=httpx.ReadTimeout("timed out"))

    async with FineractClient(make_settings()) as client:
        with pytest.raises(FineractTimeoutError):
            await client.get("api/v1/clients")


@respx.mock
async def test_converts_connection_failure_into_domain_error() -> None:
    respx.get(CLIENTS_URL).mock(side_effect=httpx.ConnectError("connection refused"))

    async with FineractClient(make_settings()) as client:
        with pytest.raises(FineractConnectionError):
            await client.get("api/v1/clients")


async def test_context_manager_closes_client() -> None:
    client = FineractClient(make_settings())
    assert client.is_closed is False
    async with client:
        assert client.is_closed is False
    assert client.is_closed is True


async def test_aclose_is_idempotent() -> None:
    client = FineractClient(make_settings())
    await client.aclose()
    assert client.is_closed is True
    await client.aclose()
    assert client.is_closed is True


@respx.mock
async def test_credentials_are_not_leaked_in_errors_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret_password = "S3cr3t-Pass-xyz"
    secret_user = "user-secret-abc"
    respx.get(CLIENTS_URL).mock(return_value=httpx.Response(500, text="boom"))

    client = FineractClient(
        make_settings(
            fineract_username=secret_user,
            fineract_password=SecretStr(secret_password),
        )
    )
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(FineractHTTPError) as excinfo:
            await client.get("api/v1/clients")
    await client.aclose()

    combined = str(excinfo.value) + caplog.text + repr(client)
    assert secret_password not in combined
    assert secret_user not in combined
    assert "Basic " not in combined
    encoded = base64.b64encode(f"{secret_user}:{secret_password}".encode()).decode()
    assert encoded not in combined
