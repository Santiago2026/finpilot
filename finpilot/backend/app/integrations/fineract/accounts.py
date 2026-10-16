"""Operaciones de lectura de cuentas (ahorro y préstamo) sobre ``FineractClient``.

Contratos verificados contra el código fuente de Fineract:

* ``ClientsApiResource`` (``/v1/clients``): ``GET api/v1/clients/{clientId}/accounts``
  devuelve ``loanAccounts`` y ``savingsAccounts`` (además de
  ``workingCapitalLoanAccounts``, que se ignora deliberadamente).
* ``SavingsAccountsApiResource`` (``/v1/savingsaccounts``):
  ``GET api/v1/savingsaccounts`` (listado) admite ``externalId``, ``offset``,
  ``limit``, ``orderBy`` y ``sortOrder`` (no ``clientId``); ``GET
  api/v1/savingsaccounts/{accountId}``; el endpoint no declara el parámetro
  ``fields``, por lo que los campos se minimizan localmente (``extra="ignore"``).
* ``LoansApiResource`` (``/v1/loans``): ``GET api/v1/loans`` (listado) admite
  ``externalId``, ``offset``, ``limit``, ``orderBy``, ``sortOrder``,
  ``accountNo``, ``associations``, ``clientId`` y ``status``; ``GET
  api/v1/loans/{loanId}`` admite el parámetro ``fields`` (p. ej.
  ``loans/1?fields=id,principal``).

Los listados devuelven una página con ``totalFilteredRecords`` y ``pageItems``
(``org.apache.fineract.infrastructure.core.service.Page``).

No existe ningún camino de escritura en este módulo.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.integrations.fineract.client import FineractClient, FineractResponseError
from app.tools.schemas import (
    ClientAccountsResult,
    GetClientAccountsArgs,
    GetLoanArgs,
    GetSavingsAccountArgs,
    ListLoansArgs,
    ListSavingsAccountsArgs,
    LoansResult,
    LoanSummary,
    SavingsAccountsResult,
    SavingsAccountSummary,
)

# Subconjunto reducido solicitado a Fineract para préstamos (data minimization).
LOAN_FIELDS = "id,accountNo,clientId,clientName,loanProductName,loanType,status,currency"

# Límite de página aplicado cuando el llamador no lo especifica.
DEFAULT_LIST_LIMIT = 20


def _page_items(raw: Any) -> tuple[list[Any], int]:
    """Extrae ``pageItems`` y ``totalFilteredRecords`` de una página de Fineract."""
    page_items = raw["pageItems"]
    total = raw["totalFilteredRecords"]
    if (
        not isinstance(page_items, list)
        or not isinstance(total, int)
        or isinstance(total, bool)
    ):
        raise TypeError("formato de página inesperado")
    return page_items, total


async def get_client_accounts(
    client: FineractClient, args: GetClientAccountsArgs
) -> ClientAccountsResult:
    """Devuelve el resumen de cuentas de ahorro y préstamo de un cliente."""
    raw = await client.get(f"api/v1/clients/{args.client_id}/accounts")
    try:
        return ClientAccountsResult.model_validate(raw)
    except (ValidationError, TypeError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de cuentas de cliente"
        ) from exc


async def get_savings_account(
    client: FineractClient, args: GetSavingsAccountArgs
) -> SavingsAccountSummary:
    """Obtiene la vista reducida de una cuenta de ahorro por identificador."""
    raw = await client.get(f"api/v1/savingsaccounts/{args.savings_id}")
    try:
        return SavingsAccountSummary.model_validate(raw)
    except (ValidationError, TypeError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de cuenta de ahorro"
        ) from exc


async def get_loan(client: FineractClient, args: GetLoanArgs) -> LoanSummary:
    """Obtiene la vista reducida de un préstamo por identificador."""
    raw = await client.get(
        f"api/v1/loans/{args.loan_id}", params={"fields": LOAN_FIELDS}
    )
    try:
        return LoanSummary.model_validate(raw)
    except (ValidationError, TypeError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de préstamo"
        ) from exc


async def list_savings_accounts(
    client: FineractClient, args: ListSavingsAccountsArgs
) -> SavingsAccountsResult:
    """Lista las cuentas de ahorro (vista reducida).

    El endpoint no admite filtrar por cliente; la respuesta se minimiza
    localmente mediante ``SavingsAccountSummary`` (``extra="ignore"``).
    """
    params: dict[str, object] = {
        "limit": args.limit if args.limit is not None else DEFAULT_LIST_LIMIT,
    }
    if args.offset is not None:
        params["offset"] = args.offset

    raw = await client.get("api/v1/savingsaccounts", params=params)
    try:
        page_items, total = _page_items(raw)
        savings_accounts = [
            SavingsAccountSummary.model_validate(item) for item in page_items
        ]
        return SavingsAccountsResult(
            totalFilteredRecords=total, savings_accounts=savings_accounts
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de cuentas de ahorro"
        ) from exc


async def list_loans(client: FineractClient, args: ListLoansArgs) -> LoansResult:
    """Lista los préstamos (vista reducida).

    Acepta filtrar por ``clientId``; el resto de la respuesta se minimiza
    localmente mediante ``LoanSummary`` (``extra="ignore"``).
    """
    params: dict[str, object] = {
        "limit": args.limit if args.limit is not None else DEFAULT_LIST_LIMIT,
    }
    if args.offset is not None:
        params["offset"] = args.offset
    if args.client_id is not None:
        params["clientId"] = args.client_id

    raw = await client.get("api/v1/loans", params=params)
    try:
        page_items, total = _page_items(raw)
        loans = [LoanSummary.model_validate(item) for item in page_items]
        return LoansResult(totalFilteredRecords=total, loans=loans)
    except (KeyError, TypeError, ValidationError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de préstamos"
        ) from exc
