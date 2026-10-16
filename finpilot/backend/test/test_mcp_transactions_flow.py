"""Pruebas de flujo MCP para las herramientas de transacciones (A2).

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
CLIENT_TX_URL = f"{BASE}/clients/7/transactions"
SAVINGS_TX_URL = f"{BASE}/savingsaccounts/3/transactions/search"
LOAN_TX_URL = f"{BASE}/loans/9/transactions"
SAVINGS_TX_DETAIL_URL = f"{BASE}/savingsaccounts/3/transactions/33"
LOAN_TX_DETAIL_URL = f"{BASE}/loans/9/transactions/3"

CLIENT_TX_PAYLOAD = {
    "totalFilteredRecords": 1,
    "pageItems": [
        {
            "id": 226,
            "type": {"id": 1, "code": "clientTransactionType.payCharge"},
            "date": [2015, 9, 2],
            "amount": 22.0,
            "reversed": False,
            "currency": {"code": "USD"},
        }
    ],
}
SAVINGS_TX_PAYLOAD = {
    "totalFilteredRecords": 1,
    "pageItems": [
        {
            "id": 33,
            "transactionType": {"id": 2, "code": "savingsAccountTransactionType.deposit"},
            "date": [2024, 3, 1],
            "amount": 500.0,
            "reversed": False,
        }
    ],
}
LOAN_TX_PAYLOAD = {
    "totalFilteredRecords": 1,
    "pageItems": [
        {
            "id": 3,
            "type": {"id": 2, "code": "loanTransactionType.repayment"},
            "date": [2012, 5, 14],
            "amount": 559.88,
            "manuallyReversed": False,
        }
    ],
}

SAVINGS_TX_DETAIL_PAYLOAD = {
    "id": 33,
    "accountId": 3,
    "accountNo": "SAV-001",
    "transactionType": {"id": 2, "code": "savingsAccountTransactionType.deposit"},
    "date": [2024, 3, 1],
    "currency": {"code": "USD"},
    "amount": 500.0,
    "runningBalance": 1500.0,
    "reversed": False,
    "paymentDetailData": {"id": 5},
    "note": "sentinel-secret-note",
    "submittedByUsername": "sentinel-secret-staff",
}
LOAN_TX_DETAIL_PAYLOAD = {
    "id": 3,
    "loanId": 9,
    "officeName": "Head Office",
    "type": {"id": 2, "code": "loanTransactionType.repayment"},
    "date": [2012, 5, 14],
    "currency": {"code": "USD"},
    "amount": 559.88,
    "manuallyReversed": False,
    "principalPortion": 240.0,
    "outstandingLoanBalance": 10000.0,
    "paymentDetailData": {"id": 7},
    "loanChargePaidByList": [{"id": 1}],
}


def _structured(result) -> dict:
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def _text(result) -> str:
    return "".join(c.text for c in result.content if getattr(c, "type", None) == "text")


async def test_list_client_transactions_flow_success(mcp_settings: Settings) -> None:
    with respx.mock:
        route = respx.get(CLIENT_TX_URL).mock(
            return_value=httpx.Response(200, json=CLIENT_TX_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool(
                "list_client_transactions", {"clientId": 7, "limit": 5}
            )

    assert result.is_error is False
    data = _structured(result)
    assert data["totalFilteredRecords"] == 1
    assert data["transactions"][0]["id"] == 226
    assert route.calls[0].request.url.params["limit"] == "5"
    assert {call.request.method for call in route.calls} == {"GET"}


async def test_search_savings_transactions_flow_success(
    mcp_settings: Settings,
) -> None:
    with respx.mock:
        route = respx.get(SAVINGS_TX_URL).mock(
            return_value=httpx.Response(200, json=SAVINGS_TX_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool(
                "search_savings_transactions",
                {"savingsId": 3, "orderBy": "id", "sortOrder": "ASC"},
            )

    assert result.is_error is False
    data = _structured(result)
    assert data["transactions"][0]["transactionType"]["code"].endswith("deposit")
    assert route.calls[0].request.url.params["sortOrder"] == "ASC"


async def test_list_loan_transactions_flow_success(mcp_settings: Settings) -> None:
    with respx.mock:
        route = respx.get(LOAN_TX_URL).mock(
            return_value=httpx.Response(200, json=LOAN_TX_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool(
                "list_loan_transactions", {"loanId": 9, "page": 0, "size": 10}
            )

    assert result.is_error is False
    data = _structured(result)
    assert data["transactions"][0]["id"] == 3
    assert route.calls[0].request.url.params["page"] == "0"
    assert route.calls[0].request.url.params["size"] == "10"


async def test_transactions_upstream_500_is_safe_error(mcp_settings: Settings) -> None:
    with respx.mock:
        respx.get(LOAN_TX_URL).mock(
            return_value=httpx.Response(500, json={"message": "sentinel-secret-body"})
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool("list_loan_transactions", {"loanId": 9})

    assert result.is_error is True
    message = _text(result)
    assert "HTTP 500" in message
    assert "sentinel-secret-body" not in message


async def test_get_savings_transaction_flow_success_and_minimized(
    mcp_settings: Settings,
) -> None:
    with respx.mock:
        route = respx.get(SAVINGS_TX_DETAIL_URL).mock(
            return_value=httpx.Response(200, json=SAVINGS_TX_DETAIL_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool(
                "get_savings_transaction", {"savingsId": 3, "transactionId": 33}
            )

    assert result.is_error is False
    data = _structured(result)
    assert data["id"] == 33
    assert data["transactionType"]["code"].endswith("deposit")
    assert {call.request.method for call in route.calls} == {"GET"}
    assert route.calls[0].request.url.params == httpx.QueryParams()
    for sensitive in (
        "accountId",
        "accountNo",
        "runningBalance",
        "paymentDetailData",
        "note",
        "submittedByUsername",
    ):
        assert sensitive not in data
    assert "sentinel" not in json.dumps(data)


async def test_get_loan_transaction_flow_success_and_minimized(
    mcp_settings: Settings,
) -> None:
    with respx.mock:
        route = respx.get(LOAN_TX_DETAIL_URL).mock(
            return_value=httpx.Response(200, json=LOAN_TX_DETAIL_PAYLOAD)
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool(
                "get_loan_transaction", {"loanId": 9, "transactionId": 3}
            )

    assert result.is_error is False
    data = _structured(result)
    assert data["id"] == 3
    assert data["manuallyReversed"] is False
    assert "fields" not in route.calls[0].request.url.params
    for sensitive in (
        "loanId",
        "officeName",
        "principalPortion",
        "outstandingLoanBalance",
        "paymentDetailData",
        "loanChargePaidByList",
    ):
        assert sensitive not in data


async def test_get_loan_transaction_upstream_404_is_safe_error(
    mcp_settings: Settings,
) -> None:
    with respx.mock:
        respx.get(LOAN_TX_DETAIL_URL).mock(
            return_value=httpx.Response(404, text="sentinel-secret-body")
        )
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool(
                "get_loan_transaction", {"loanId": 9, "transactionId": 3}
            )

    assert result.is_error is True
    message = _text(result)
    assert "HTTP 404" in message
    assert "sentinel-secret-body" not in message


@pytest.mark.parametrize(
    "name,args",
    [
        ("list_client_transactions", {}),
        ("list_client_transactions", {"clientId": 1, "limit": 101}),
        ("list_client_transactions", {"clientId": 1, "offset": -1}),
        ("search_savings_transactions", {}),
        ("search_savings_transactions", {"savingsId": 1, "extra": True}),
        ("list_loan_transactions", {}),
        ("list_loan_transactions", {"loanId": 1, "page": -1}),
        ("get_savings_transaction", {}),
        ("get_savings_transaction", {"savingsId": 1}),
        ("get_savings_transaction", {"savingsId": 1, "transactionId": 0}),
        ("get_savings_transaction", {"savingsId": 1, "transactionId": 1, "extra": True}),
        ("get_loan_transaction", {}),
        ("get_loan_transaction", {"loanId": 1, "transactionId": -1}),
        ("get_loan_transaction", {"loanId": 1, "transactionId": 1, "extra": True}),
    ],
)
async def test_invalid_transaction_arguments_rejected_before_fineract(
    mcp_settings: Settings, name: str, args: dict
) -> None:
    with respx.mock:
        fineract_route = respx.route().mock(return_value=httpx.Response(200, json=[]))
        async with connected_mcp_session(mcp_settings) as session:
            result = await session.call_tool(name, args)
        assert result.is_error is True
        assert fineract_route.call_count == 0


async def test_transaction_tools_published_with_strict_schemas(
    mcp_settings: Settings,
) -> None:
    async with connected_mcp_session(mcp_settings) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
    for name, field in (
        ("list_client_transactions", "clientId"),
        ("search_savings_transactions", "savingsId"),
        ("list_loan_transactions", "loanId"),
    ):
        schema = tools[name].input_schema
        assert schema["additionalProperties"] is False
        assert schema["required"] == [field]
        assert schema["properties"][field]["minimum"] == 1


async def test_detail_transaction_tools_published_with_strict_schemas(
    mcp_settings: Settings,
) -> None:
    async with connected_mcp_session(mcp_settings) as session:
        tools = {t.name: t for t in (await session.list_tools()).tools}
    for name, id_field in (
        ("get_savings_transaction", "savingsId"),
        ("get_loan_transaction", "loanId"),
    ):
        schema = tools[name].input_schema
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == {id_field, "transactionId"}
        assert schema["properties"][id_field]["minimum"] == 1
        assert schema["properties"]["transactionId"]["minimum"] == 1
