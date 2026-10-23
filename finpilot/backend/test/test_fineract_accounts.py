from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.fineract.accounts import (
    DEFAULT_LIST_LIMIT,
    LOAN_FIELDS,
    get_client_accounts,
    get_loan,
    get_savings_account,
    list_loans,
    list_savings_accounts,
)
from app.integrations.fineract.client import (
    FineractClient,
    FineractHTTPError,
    FineractResponseError,
)
from app.tools.schemas import (
    GetClientAccountsArgs,
    GetLoanArgs,
    GetSavingsAccountArgs,
    ListLoansArgs,
    ListSavingsAccountsArgs,
)

BASE_URL = "https://fineract.test:8443/fineract-provider"
CLIENT_ACCOUNTS_URL = f"{BASE_URL}/api/v1/clients/7/accounts"
SAVINGS_URL = f"{BASE_URL}/api/v1/savingsaccounts/3"
LOANS_URL = f"{BASE_URL}/api/v1/loans/9"
SAVINGS_LIST_URL = f"{BASE_URL}/api/v1/savingsaccounts"
LOANS_LIST_URL = f"{BASE_URL}/api/v1/loans"

CLIENT_ACCOUNTS_PAYLOAD = {
    "loanAccounts": [
        {
            "id": 11,
            "accountNo": "L-1",
            "productName": "Préstamo A",
            "status": {"id": 300, "code": "loanStatusType.active", "value": "Active"},
            "loanType": {"id": 1, "code": "loanType.individual", "value": "Individual"},
            "currency": {"code": "USD"},
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
    "workingCapitalLoanAccounts": [{"id": 99}],
}

SAVINGS_PAYLOAD = {
    "id": 3,
    "accountNo": "S-3",
    "externalId": "ext-3",
    "clientId": 7,
    "clientName": "Ana Pérez",
    "savingsProductName": "Ahorro Plus",
    "status": {
        "id": 300,
        "code": "savingsAccountStatusType.active",
        "value": "Active",
        "active": True,
    },
    "currency": {"code": "USD", "name": "US Dollar"},
    "summary": {"accountBalance": 1234.56, "availableBalance": 1000.0},
    "timeline": {"activatedOnDate": [2024, 1, 1]},
}

LOAN_PAYLOAD = {
    "id": 9,
    "accountNo": "L-9",
    "clientId": 7,
    "clientName": "Ana Pérez",
    "loanProductName": "Préstamo Personal",
    "loanType": {"id": 1, "code": "loanType.individual", "value": "Individual"},
    "status": {"id": 300, "code": "loanStatusType.active", "value": "Active"},
    "currency": {"code": "USD", "name": "US Dollar"},
    "principal": 5000,
    "summary": {"totalOutstanding": 4000},
    "transactions": [{"id": 1}],
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
async def test_get_client_accounts_parses_and_minimizes() -> None:
    route = respx.get(CLIENT_ACCOUNTS_URL).mock(
        return_value=httpx.Response(200, json=CLIENT_ACCOUNTS_PAYLOAD)
    )

    async with make_client() as client:
        result = await get_client_accounts(
            client, GetClientAccountsArgs.model_validate({"clientId": 7})
        )

    assert route.called
    request = route.calls.last.request
    assert str(request.url.copy_with(query=None)) == CLIENT_ACCOUNTS_URL
    assert request.method == "GET"

    assert result.loan_accounts[0].id == 11
    assert result.loan_accounts[0].product_name == "Préstamo A"
    assert result.loan_accounts[0].status is not None
    assert result.loan_accounts[0].status.code == "loanStatusType.active"
    assert result.savings_accounts[0].id == 21
    assert result.savings_accounts[0].account_no == "S-1"

    dumped = result.loan_accounts[0].model_dump(by_alias=True, exclude_none=True)
    assert "loanType" not in dumped
    assert "currency" not in dumped


@respx.mock
async def test_get_client_accounts_requires_identifier_to_parse() -> None:
    respx.get(CLIENT_ACCOUNTS_URL).mock(
        return_value=httpx.Response(200, json={"loanAccounts": [{"accountNo": "x"}]})
    )

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await get_client_accounts(
                client, GetClientAccountsArgs.model_validate({"clientId": 7})
            )


@respx.mock
async def test_get_client_accounts_rejects_non_object_shape() -> None:
    respx.get(CLIENT_ACCOUNTS_URL).mock(return_value=httpx.Response(200, json=[]))

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await get_client_accounts(
                client, GetClientAccountsArgs.model_validate({"clientId": 7})
            )


@respx.mock
async def test_get_client_accounts_propagates_http_error() -> None:
    respx.get(CLIENT_ACCOUNTS_URL).mock(
        return_value=httpx.Response(403, text="sentinel-secret-body")
    )

    async with make_client() as client:
        with pytest.raises(FineractHTTPError) as excinfo:
            await get_client_accounts(
                client, GetClientAccountsArgs.model_validate({"clientId": 7})
            )

    assert excinfo.value.status_code == 403
    assert "sentinel-secret-body" not in str(excinfo.value)


@respx.mock
async def test_get_savings_account_parses_and_minimizes() -> None:
    route = respx.get(SAVINGS_URL).mock(
        return_value=httpx.Response(200, json=SAVINGS_PAYLOAD)
    )

    async with make_client() as client:
        summary = await get_savings_account(
            client, GetSavingsAccountArgs.model_validate({"savingsId": 3})
        )

    assert route.called
    request = route.calls.last.request
    assert str(request.url.copy_with(query=None)) == SAVINGS_URL
    assert "fields" not in request.url.params

    assert summary.id == 3
    assert summary.product_name == "Ahorro Plus"
    assert summary.client_id == 7
    assert summary.status is not None and summary.status.value == "Active"
    assert summary.currency is not None and summary.currency.code == "USD"

    dumped = summary.model_dump(by_alias=True, exclude_none=True)
    assert summary.summary is not None
    assert summary.summary.account_balance == 1234.56
    assert summary.summary.available_balance == 1000.0
    assert dumped["summary"] == {"accountBalance": 1234.56, "availableBalance": 1000.0}
    assert "timeline" not in dumped
    assert "accountBalance" in dumped["summary"]


@respx.mock
async def test_get_savings_account_rejects_unexpected_shape() -> None:
    respx.get(SAVINGS_URL).mock(return_value=httpx.Response(200, json={"name": "x"}))

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await get_savings_account(
                client, GetSavingsAccountArgs.model_validate({"savingsId": 3})
            )


@respx.mock
async def test_get_loan_requests_fields_and_minimizes() -> None:
    route = respx.get(LOANS_URL).mock(
        return_value=httpx.Response(200, json=LOAN_PAYLOAD)
    )

    async with make_client() as client:
        loan = await get_loan(client, GetLoanArgs.model_validate({"loanId": 9}))

    assert route.called
    request = route.calls.last.request
    assert str(request.url.copy_with(query=None)) == LOANS_URL
    assert request.url.params["fields"] == LOAN_FIELDS

    assert loan.id == 9
    assert loan.product_name == "Préstamo Personal"
    assert loan.client_name == "Ana Pérez"
    assert loan.loan_type is not None and loan.loan_type.code == "loanType.individual"
    assert loan.status is not None and loan.status.value == "Active"
    assert loan.currency is not None and loan.currency.code == "USD"

    dumped = loan.model_dump(by_alias=True, exclude_none=True)
    assert "principal" not in dumped
    assert "summary" not in dumped
    assert "transactions" not in dumped


@respx.mock
async def test_get_loan_rejects_unexpected_shape() -> None:
    respx.get(LOANS_URL).mock(return_value=httpx.Response(200, json={"name": "x"}))

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await get_loan(client, GetLoanArgs.model_validate({"loanId": 9}))


@respx.mock
async def test_get_loan_maps_not_found_to_http_error() -> None:
    respx.get(LOANS_URL).mock(return_value=httpx.Response(404, json={}))

    async with make_client() as client:
        with pytest.raises(FineractHTTPError) as excinfo:
            await get_loan(client, GetLoanArgs.model_validate({"loanId": 9}))

    assert excinfo.value.status_code == 404


SAVINGS_LIST_PAYLOAD = {
    "totalFilteredRecords": 1,
    "pageItems": [
        {
            "id": 3,
            "accountNo": "S-3",
            "externalId": "ext-3",
            "clientId": 7,
            "clientName": "Ana Pérez",
            "savingsProductName": "Ahorro Plus",
            "status": {"id": 300, "code": "savingsAccountStatusType.active", "value": "Active"},
            "currency": {"code": "USD", "name": "US Dollar"},
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
            "currency": {"code": "USD", "name": "US Dollar"},
            "principal": 5000,
            "summary": {"totalOutstanding": 4000},
            "loanOfficerName": "sentinel-secret",
        }
    ],
}


@respx.mock
async def test_list_savings_accounts_builds_params_and_minimizes() -> None:
    route = respx.get(SAVINGS_LIST_URL).mock(
        return_value=httpx.Response(200, json=SAVINGS_LIST_PAYLOAD)
    )

    async with make_client() as client:
        result = await list_savings_accounts(
            client, ListSavingsAccountsArgs.model_validate({"offset": 0, "limit": 25})
        )

    assert route.called
    request = route.calls.last.request
    assert str(request.url.copy_with(query=None)) == SAVINGS_LIST_URL
    assert {call.request.method for call in route.calls} == {"GET"}
    assert request.url.params["offset"] == "0"
    assert request.url.params["limit"] == "25"
    assert "clientId" not in request.url.params

    assert result.total_filtered_records == 1
    account = result.savings_accounts[0]
    assert account.id == 3
    assert account.product_name == "Ahorro Plus"
    assert account.client_name == "Ana Pérez"

    dumped = account.model_dump(by_alias=True, exclude_none=True)
    assert account.summary is not None
    assert account.summary.account_balance == 1234.56
    assert dumped["summary"] == {"accountBalance": 1234.56}
    assert "subStatus" not in dumped
    assert "sentinel" not in json.dumps(dumped)


@respx.mock
async def test_list_savings_accounts_applies_default_limit() -> None:
    route = respx.get(SAVINGS_LIST_URL).mock(
        return_value=httpx.Response(200, json={"totalFilteredRecords": 0, "pageItems": []})
    )

    async with make_client() as client:
        await list_savings_accounts(
            client, ListSavingsAccountsArgs.model_validate({})
        )

    params = route.calls.last.request.url.params
    assert params["limit"] == str(DEFAULT_LIST_LIMIT)
    assert "offset" not in params


@respx.mock
async def test_list_savings_accounts_rejects_unexpected_shape() -> None:
    respx.get(SAVINGS_LIST_URL).mock(return_value=httpx.Response(200, json=[]))

    async with make_client() as client:
        with pytest.raises(FineractResponseError):
            await list_savings_accounts(
                client, ListSavingsAccountsArgs.model_validate({})
            )


@respx.mock
async def test_list_loans_builds_params_and_minimizes() -> None:
    route = respx.get(LOANS_LIST_URL).mock(
        return_value=httpx.Response(200, json=LOANS_LIST_PAYLOAD)
    )

    async with make_client() as client:
        result = await list_loans(
            client,
            ListLoansArgs.model_validate({"clientId": 7, "offset": 0, "limit": 50}),
        )

    assert route.called
    request = route.calls.last.request
    assert str(request.url.copy_with(query=None)) == LOANS_LIST_URL
    assert {call.request.method for call in route.calls} == {"GET"}
    assert request.url.params["clientId"] == "7"
    assert request.url.params["offset"] == "0"
    assert request.url.params["limit"] == "50"

    assert result.total_filtered_records == 1
    loan = result.loans[0]
    assert loan.id == 9
    assert loan.product_name == "Préstamo Personal"
    assert loan.loan_type is not None and loan.loan_type.code == "loanType.individual"

    dumped = loan.model_dump(by_alias=True, exclude_none=True)
    assert "principal" not in dumped
    assert "summary" not in dumped
    assert "loanOfficerName" not in dumped
    assert "sentinel" not in json.dumps(dumped)


@respx.mock
async def test_list_loans_without_client_filter_omits_param() -> None:
    route = respx.get(LOANS_LIST_URL).mock(
        return_value=httpx.Response(200, json={"totalFilteredRecords": 0, "pageItems": []})
    )

    async with make_client() as client:
        await list_loans(client, ListLoansArgs.model_validate({}))

    params = route.calls.last.request.url.params
    assert "clientId" not in params
    assert params["limit"] == str(DEFAULT_LIST_LIMIT)


@respx.mock
async def test_list_loans_maps_not_found_safely() -> None:
    respx.get(LOANS_LIST_URL).mock(
        return_value=httpx.Response(404, text="sentinel-secret-body")
    )

    async with make_client() as client:
        with pytest.raises(FineractHTTPError) as excinfo:
            await list_loans(client, ListLoansArgs.model_validate({}))

    assert excinfo.value.status_code == 404
    assert "sentinel-secret-body" not in str(excinfo.value)
