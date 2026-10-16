from __future__ import annotations

import json

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
from app.integrations.fineract.transactions import (
    DEFAULT_TRANSACTION_LIMIT,
    get_loan_transaction,
    get_savings_transaction,
    list_client_transactions,
    list_loan_transactions,
    search_savings_transactions,
)
from app.tools.schemas import (
    GetLoanTransactionArgs,
    GetSavingsTransactionArgs,
    ListClientTransactionsArgs,
    ListLoanTransactionsArgs,
    SearchSavingsTransactionsArgs,
)

BASE_URL = "https://fineract.test:8443/fineract-provider"
CLIENT_TX_URL = f"{BASE_URL}/api/v1/clients/7/transactions"
SAVINGS_TX_URL = f"{BASE_URL}/api/v1/savingsaccounts/3/transactions/search"
LOAN_TX_URL = f"{BASE_URL}/api/v1/loans/9/transactions"
SAVINGS_TX_DETAIL_URL = f"{BASE_URL}/api/v1/savingsaccounts/3/transactions/33"
LOAN_TX_DETAIL_URL = f"{BASE_URL}/api/v1/loans/9/transactions/3"

CLIENT_TX_PAYLOAD = {
    "totalFilteredRecords": 1,
    "pageItems": [
        {
            "id": 226,
            "officeId": 1,
            "officeName": "Head Office",
            "type": {"id": 1, "code": "clientTransactionType.payCharge", "description": "PAY_CHARGE"},
            "date": [2015, 9, 2],
            "currency": {"code": "USD", "name": "US Dollar"},
            "amount": 22.0,
            "submittedOnDate": [2015, 9, 2],
            "reversed": False,
        }
    ],
}

SAVINGS_TX_PAYLOAD = {
    "totalFilteredRecords": 1,
    "pageItems": [
        {
            "id": 33,
            "transactionType": {"id": 2, "code": "savingsAccountTransactionType.deposit", "value": "Deposit"},
            "date": [2024, 3, 1],
            "currency": {"code": "USD"},
            "amount": 500.0,
            "runningBalance": 1500.0,
            "reversed": False,
        }
    ],
}

LOAN_TX_PAYLOAD = {
    "totalFilteredRecords": 2,
    "pageItems": [
        {
            "id": 3,
            "type": {"id": 2, "code": "loanTransactionType.repayment", "description": "Repayment"},
            "date": [2012, 5, 14],
            "currency": {"code": "USD"},
            "amount": 559.88,
            "manuallyReversed": False,
            "principalPortion": 240.0,
        }
    ],
}


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


@respx.mock
async def test_list_client_transactions_builds_params_and_minimizes() -> None:
    route = respx.get(CLIENT_TX_URL).mock(
        return_value=httpx.Response(200, json=CLIENT_TX_PAYLOAD)
    )

    async with make_client() as client:
        result = await list_client_transactions(
            client, ListClientTransactionsArgs.model_validate({"clientId": 7, "limit": 5, "offset": 10})
        )

    assert route.called
    request = route.calls.last.request
    assert str(request.url.copy_with(query=None)) == CLIENT_TX_URL
    params = request.url.params
    assert params["limit"] == "5"
    assert params["offset"] == "10"

    assert result.total_filtered_records == 1
    tx = result.transactions[0]
    assert tx.id == 226
    assert tx.type is not None and tx.type.code == "clientTransactionType.payCharge"
    assert tx.date == [2015, 9, 2]
    assert tx.amount == 22.0
    assert tx.reversed is False

    dumped = tx.model_dump(by_alias=True, exclude_none=True)
    assert "officeId" not in dumped
    assert "officeName" not in dumped
    assert "submittedOnDate" not in dumped


@respx.mock
async def test_list_client_transactions_applies_default_limit() -> None:
    route = respx.get(CLIENT_TX_URL).mock(
        return_value=httpx.Response(200, json={"totalFilteredRecords": 0, "pageItems": []})
    )

    async with make_client() as client:
        await list_client_transactions(
            client, ListClientTransactionsArgs.model_validate({"clientId": 7})
        )

    params = route.calls.last.request.url.params
    assert params["limit"] == str(DEFAULT_TRANSACTION_LIMIT)
    assert "offset" not in params


@respx.mock
async def test_list_client_transactions_rejects_unexpected_shape() -> None:
    respx.get(CLIENT_TX_URL).mock(return_value=httpx.Response(200, json={"pageItems": []}))

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await list_client_transactions(
                client, ListClientTransactionsArgs.model_validate({"clientId": 7})
            )


@respx.mock
async def test_search_savings_transactions_builds_params_and_minimizes() -> None:
    route = respx.get(SAVINGS_TX_URL).mock(
        return_value=httpx.Response(200, json=SAVINGS_TX_PAYLOAD)
    )

    async with make_client() as client:
        result = await search_savings_transactions(
            client,
            SearchSavingsTransactionsArgs.model_validate(
                {"savingsId": 3, "offset": 0, "limit": 25, "orderBy": "id", "sortOrder": "DESC"}
            ),
        )

    assert route.called
    params = route.calls.last.request.url.params
    assert str(route.calls.last.request.url.copy_with(query=None)) == SAVINGS_TX_URL
    assert params["offset"] == "0"
    assert params["limit"] == "25"
    assert params["orderBy"] == "id"
    assert params["sortOrder"] == "DESC"

    tx = result.transactions[0]
    assert tx.id == 33
    assert tx.transaction_type is not None
    assert tx.transaction_type.code == "savingsAccountTransactionType.deposit"
    dumped = tx.model_dump(by_alias=True, exclude_none=True)
    assert "runningBalance" not in dumped


@respx.mock
async def test_search_savings_transactions_rejects_unexpected_shape() -> None:
    respx.get(SAVINGS_TX_URL).mock(return_value=httpx.Response(200, json=[]))

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await search_savings_transactions(
                client, SearchSavingsTransactionsArgs.model_validate({"savingsId": 3})
            )


@respx.mock
async def test_list_loan_transactions_uses_page_size_and_minimizes() -> None:
    route = respx.get(LOAN_TX_URL).mock(
        return_value=httpx.Response(200, json=LOAN_TX_PAYLOAD)
    )

    async with make_client() as client:
        result = await list_loan_transactions(
            client, ListLoanTransactionsArgs.model_validate({"loanId": 9, "page": 1, "size": 50})
        )

    assert route.called
    params = route.calls.last.request.url.params
    assert str(route.calls.last.request.url.copy_with(query=None)) == LOAN_TX_URL
    assert params["page"] == "1"
    assert params["size"] == "50"

    assert result.total_filtered_records == 2
    tx = result.transactions[0]
    assert tx.id == 3
    assert tx.type is not None and tx.type.code == "loanTransactionType.repayment"
    assert tx.manually_reversed is False
    dumped = tx.model_dump(by_alias=True, exclude_none=True)
    assert "principalPortion" not in dumped


@respx.mock
async def test_list_loan_transactions_maps_not_found_safely() -> None:
    respx.get(LOAN_TX_URL).mock(
        return_value=httpx.Response(404, text="sentinel-secret-body")
    )

    async with make_client() as client:
        with pytest.raises(FineractHTTPError) as excinfo:
            await list_loan_transactions(
                client, ListLoanTransactionsArgs.model_validate({"loanId": 9})
            )

    assert excinfo.value.status_code == 404
    assert "sentinel-secret-body" not in str(excinfo.value)


SAVINGS_TX_DETAIL_PAYLOAD = {
    "id": 33,
    "accountId": 3,
    "accountNo": "SAV-001",
    "externalId": "ext-33",
    "transactionType": {
        "id": 2,
        "code": "savingsAccountTransactionType.deposit",
        "value": "Deposit",
    },
    "date": [2024, 3, 1],
    "currency": {"code": "USD", "name": "US Dollar"},
    "amount": 500.0,
    "runningBalance": 1500.0,
    "reversed": False,
    "paymentDetailData": {"id": 5, "paymentType": {"name": "CASH"}},
    "note": "sentinel-secret-note",
    "submittedByUsername": "sentinel-secret-staff",
    "checkNumber": "sentinel-secret-check",
    "routingCode": "sentinel-secret-routing",
}

LOAN_TX_DETAIL_PAYLOAD = {
    "id": 3,
    "loanId": 9,
    "officeId": 1,
    "officeName": "Head Office",
    "externalId": "ext-3",
    "type": {"id": 2, "code": "loanTransactionType.repayment", "description": "Repayment"},
    "date": [2012, 5, 14],
    "currency": {"code": "USD", "name": "US Dollar"},
    "amount": 559.88,
    "manuallyReversed": False,
    "principalPortion": 240.0,
    "interestPortion": 319.88,
    "outstandingLoanBalance": 10000.0,
    "paymentDetailData": {"id": 7},
    "loanChargePaidByList": [{"id": 1, "name": "sentinel-secret-charge"}],
    "transactionRelations": [{"id": 2}],
    "reversedOnDate": [2012, 5, 15],
}


@respx.mock
async def test_get_savings_transaction_builds_url_and_minimizes() -> None:
    route = respx.get(SAVINGS_TX_DETAIL_URL).mock(
        return_value=httpx.Response(200, json=SAVINGS_TX_DETAIL_PAYLOAD)
    )

    async with make_client() as client:
        result = await get_savings_transaction(
            client,
            GetSavingsTransactionArgs.model_validate(
                {"savingsId": 3, "transactionId": 33}
            ),
        )

    assert route.called
    assert str(route.calls.last.request.url) == SAVINGS_TX_DETAIL_URL
    assert {call.request.method for call in route.calls} == {"GET"}

    assert result.id == 33
    assert result.transaction_type is not None
    assert result.transaction_type.code == "savingsAccountTransactionType.deposit"
    assert result.date == [2024, 3, 1]
    assert result.amount == 500.0
    assert result.reversed is False

    dumped = result.model_dump(by_alias=True, exclude_none=True)
    for sensitive in (
        "accountId",
        "accountNo",
        "externalId",
        "runningBalance",
        "paymentDetailData",
        "note",
        "submittedByUsername",
        "checkNumber",
        "routingCode",
    ):
        assert sensitive not in dumped
    assert "sentinel" not in json.dumps(dumped)


@respx.mock
async def test_get_savings_transaction_rejects_unexpected_shape() -> None:
    respx.get(SAVINGS_TX_DETAIL_URL).mock(return_value=httpx.Response(200, json=[]))

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await get_savings_transaction(
                client,
                GetSavingsTransactionArgs.model_validate(
                    {"savingsId": 3, "transactionId": 33}
                ),
            )


@respx.mock
async def test_get_savings_transaction_maps_not_found_safely() -> None:
    respx.get(SAVINGS_TX_DETAIL_URL).mock(
        return_value=httpx.Response(404, text="sentinel-secret-body")
    )

    async with make_client() as client:
        with pytest.raises(FineractHTTPError) as excinfo:
            await get_savings_transaction(
                client,
                GetSavingsTransactionArgs.model_validate(
                    {"savingsId": 3, "transactionId": 33}
                ),
            )

    assert excinfo.value.status_code == 404
    assert "sentinel-secret-body" not in str(excinfo.value)


@respx.mock
async def test_get_loan_transaction_builds_url_and_minimizes() -> None:
    route = respx.get(LOAN_TX_DETAIL_URL).mock(
        return_value=httpx.Response(200, json=LOAN_TX_DETAIL_PAYLOAD)
    )

    async with make_client() as client:
        result = await get_loan_transaction(
            client,
            GetLoanTransactionArgs.model_validate({"loanId": 9, "transactionId": 3}),
        )

    assert route.called
    request = route.calls.last.request
    assert str(request.url.copy_with(query=None)) == LOAN_TX_DETAIL_URL
    assert "fields" not in request.url.params
    assert {call.request.method for call in route.calls} == {"GET"}

    assert result.id == 3
    assert result.type is not None and result.type.code == "loanTransactionType.repayment"
    assert result.date == [2012, 5, 14]
    assert result.amount == 559.88
    assert result.manually_reversed is False

    dumped = result.model_dump(by_alias=True, exclude_none=True)
    for sensitive in (
        "loanId",
        "officeId",
        "officeName",
        "externalId",
        "principalPortion",
        "interestPortion",
        "outstandingLoanBalance",
        "paymentDetailData",
        "loanChargePaidByList",
        "transactionRelations",
        "reversedOnDate",
    ):
        assert sensitive not in dumped
    assert "sentinel" not in json.dumps(dumped)


@respx.mock
async def test_get_loan_transaction_rejects_unexpected_shape() -> None:
    respx.get(LOAN_TX_DETAIL_URL).mock(return_value=httpx.Response(200, json={"foo": 1}))

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await get_loan_transaction(
                client,
                GetLoanTransactionArgs.model_validate({"loanId": 9, "transactionId": 3}),
            )


@respx.mock
async def test_get_loan_transaction_maps_not_found_safely() -> None:
    respx.get(LOAN_TX_DETAIL_URL).mock(
        return_value=httpx.Response(404, text="sentinel-secret-body")
    )

    async with make_client() as client:
        with pytest.raises(FineractHTTPError) as excinfo:
            await get_loan_transaction(
                client,
                GetLoanTransactionArgs.model_validate({"loanId": 9, "transactionId": 3}),
            )

    assert excinfo.value.status_code == 404
    assert "sentinel-secret-body" not in str(excinfo.value)
