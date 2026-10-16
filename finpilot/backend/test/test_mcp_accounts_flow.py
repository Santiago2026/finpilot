"""Pruebas de flujo MCP para las herramientas de cuentas y préstamos (A1).

Todo se ejecuta en memoria; las llamadas HTTP a Fineract se interceptan con
``respx``. No hay red ni servidor externo.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.core.config import Settings
from conftest import connected_mcp_session

BASE = "https://fineract.test:8443/fineract-provider/api/v1"
CLIENT_ACCOUNTS_URL = f"{BASE}/clients/7/accounts"
SAVINGS_URL = f"{BASE}/savingsaccounts/3"
LOANS_URL = f"{BASE}/loans/9"
SAVINGS_LIST_URL = f"{BASE}/savingsaccounts"
LOANS_LIST_URL = f"{BASE}/loans"

CLIENT_ACCOUNTS_PAYLOAD = {
    "loanAccounts": [
        {
            "id": 11,
            "accountNo": "L-1",
            "productName": "Préstamo A",
            "status": {"id": 300, "code": "loanStatusType.active", "value": "Active"},
        }
    ],
    "savingsAccounts": [
        {
            "id": 21,
            "accountNo": "S-1",
            "productName": "Ahorro A",
            "status": {
                "id": 300,
                "code": "savingsAccountStatusType.active",
                "value": "Active",
            },
        }
    ],
}

SAVINGS_PAYLOAD = {
    "id": 3,
    "accountNo": "S-3",
    "clientId": 7,
    "clientName": "Ana Pérez",
    "savingsProductName": "Ahorro Plus",
    "status": {"id": 300, "code": "savingsAccountStatusType.active", "value": "Active"},
    "currency": {"code": "USD"},
    "summary": {"accountBalance": 1234.56},
}

LOAN_PAYLOAD = {
    "id": 9,
    "accountNo": "L-9",
    "clientId": 7,
    "clientName": "Ana Pérez",
    "loanProductName": "Préstamo Personal",
    "loanType": {"id": 1, "code": "loanType.individual", "value": "Individual"},
    "status": {"id": 300, "code": "loanStatusType.active", "value": "Active"},
    "currency": {"code": "USD"},
    "principal": 5000,
}

SAVINGS_LIST_PAYLOAD = {
    "totalFilteredRecords": 1,
    "pageItems": [
        {
            "id": 3,
            "accountNo": "S-3",
            "clientId": 7,
            "clientName": "Ana Pérez",
            "savingsProductName": "Ahorro Plus",
            "status": {"id": 300, "code": "savingsAccountStatusType.active", "value": "Active"},
            "currency": {"code": "USD"},
            "summary": {"accountBalance": 1234.56},
            "subStatus": {"id": 1, "value": "sentinel-secret"},
        }
    ],
}

LOANS_LIST_PAYLOAD = {
    "totalFilteredRecords": 1,
    "pageItems": [
        {
            "id": 9,
            "accountNo": "L-9",
            "clientId": 7,
            "clientName": "Ana Pérez",
            "loanProductName": "Préstamo Personal",
            "loanType": {"id": 1, "code": "loanType.individual", "value": "Individual"},
            "status": {"id": 300, "code": "loanStatusType.active", "value": "Active"},
            "currency": {"code": "USD"},
            "principal": 5000,
            "loanOfficerName": "sentinel-secret",
        }
    ],
}


def _structured(result) -> dict:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", None) == "text")


async def test_get_client_accounts_flow_success(mcp_settings: Settings) -> None:
    with respx.mock:
        route = respx.get(CLIENT_ACCOUNTS_URL).mock(
            return_value=httpx.Response(200, json=CLIENT_ACCOUNTS_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("get_client_accounts", {"clientId": 7})

    assert result.is_error is False
    data = _structured(result)
    assert data["loanAccounts"][0]["id"] == 11
    assert data["savingsAccounts"][0]["accountNo"] == "S-1"
    assert {call.request.method for call in route.calls} == {"GET"}


async def test_get_savings_account_flow_minimizes_fields(
    mcp_settings: Settings,
) -> None:
    with respx.mock:
        respx.get(SAVINGS_URL).mock(return_value=httpx.Response(200, json=SAVINGS_PAYLOAD))
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("get_savings_account", {"savingsId": 3})

    assert result.is_error is False
    data = _structured(result)
    assert data["id"] == 3
    assert data["savingsProductName"] == "Ahorro Plus"
    assert data["currency"] == {"code": "USD"}
    assert "summary" not in data
    assert "accountBalance" not in json.dumps(data)


async def test_get_loan_flow_requests_fields_and_minimizes(
    mcp_settings: Settings,
) -> None:
    with respx.mock:
        route = respx.get(LOANS_URL).mock(
            return_value=httpx.Response(200, json=LOAN_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("get_loan", {"loanId": 9})

    assert result.is_error is False
    data = _structured(result)
    assert data["id"] == 9
    assert data["loanProductName"] == "Préstamo Personal"
    assert "principal" not in data
    assert route.calls[0].request.url.params["fields"]


async def test_get_loan_not_found_is_safe_error(mcp_settings: Settings) -> None:
    with respx.mock:
        respx.get(LOANS_URL).mock(
            return_value=httpx.Response(
                404, json={"developerMessage": "sentinel-secret-body"}
            )
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("get_loan", {"loanId": 9})

    assert result.is_error is True
    message = _text(result)
    assert "HTTP 404" in message
    assert "sentinel-secret-body" not in message


async def test_list_savings_accounts_flow_minimizes_fields(
    mcp_settings: Settings,
) -> None:
    with respx.mock:
        route = respx.get(SAVINGS_LIST_URL).mock(
            return_value=httpx.Response(200, json=SAVINGS_LIST_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool(
                "list_savings_accounts", {"offset": 0, "limit": 25}
            )

    assert result.is_error is False
    data = _structured(result)
    assert data["totalFilteredRecords"] == 1
    account = data["savingsAccounts"][0]
    assert account["id"] == 3
    assert account["savingsProductName"] == "Ahorro Plus"
    assert "summary" not in account
    assert "accountBalance" not in json.dumps(data)
    assert "sentinel-secret" not in json.dumps(data)
    assert {call.request.method for call in route.calls} == {"GET"}
    assert route.calls.last.request.url.params["limit"] == "25"


async def test_list_loans_flow_filters_by_client_and_minimizes(
    mcp_settings: Settings,
) -> None:
    with respx.mock:
        route = respx.get(LOANS_LIST_URL).mock(
            return_value=httpx.Response(200, json=LOANS_LIST_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("list_loans", {"clientId": 7})

    assert result.is_error is False
    data = _structured(result)
    assert data["totalFilteredRecords"] == 1
    loan = data["loans"][0]
    assert loan["id"] == 9
    assert loan["loanProductName"] == "Préstamo Personal"
    assert "principal" not in loan
    assert "loanOfficerName" not in loan
    assert "sentinel-secret" not in json.dumps(data)
    assert route.calls.last.request.url.params["clientId"] == "7"


async def test_list_loans_not_found_is_safe_error(mcp_settings: Settings) -> None:
    with respx.mock:
        respx.get(LOANS_LIST_URL).mock(
            return_value=httpx.Response(404, text="sentinel-secret-body")
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("list_loans", {})

    assert result.is_error is True
    message = _text(result)
    assert "HTTP 404" in message
    assert "sentinel-secret-body" not in message


@pytest.mark.parametrize(
    "name,args",
    [
        ("get_client_accounts", {}),
        ("get_client_accounts", {"clientId": 0}),
        ("get_client_accounts", {"clientId": 1, "extra": True}),
        ("get_savings_account", {}),
        ("get_savings_account", {"savingsId": "abc"}),
        ("get_loan", {}),
        ("get_loan", {"loanId": -3}),
        ("list_savings_accounts", {"limit": 0}),
        ("list_savings_accounts", {"limit": 101}),
        ("list_savings_accounts", {"clientId": 1}),
        ("list_loans", {"clientId": 0}),
        ("list_loans", {"offset": -1}),
        ("list_loans", {"limit": 101}),
    ],
)
async def test_invalid_arguments_rejected_before_fineract(
    mcp_settings: Settings, name: str, args: dict
) -> None:
    with respx.mock:
        fineract_route = respx.route().mock(return_value=httpx.Response(200, json=[]))
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool(name, args)
        assert result.is_error is True
        assert fineract_route.call_count == 0


async def test_new_tools_published_with_strict_schemas(
    mcp_settings: Settings,
) -> None:
    async with connected_mcp_session(mcp_settings) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
    for name, field in (
        ("get_client_accounts", "clientId"),
        ("get_savings_account", "savingsId"),
        ("get_loan", "loanId"),
    ):
        schema = tools[name].input_schema
        assert schema["additionalProperties"] is False
        assert schema["required"] == [field]
        assert schema["properties"][field]["minimum"] == 1

    for name in ("list_savings_accounts", "list_loans"):
        schema = tools[name].input_schema
        assert schema["additionalProperties"] is False
        assert "required" not in schema or schema["required"] == []
        limit = schema["properties"]["limit"]["anyOf"][0]
        assert limit["minimum"] == 1
        assert limit["maximum"] == 100
        assert schema["properties"]["offset"]["anyOf"][0]["minimum"] == 0
    assert tools["list_loans"].input_schema["properties"]["clientId"]["anyOf"][0]["minimum"] == 1
