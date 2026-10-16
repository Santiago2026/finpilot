"""Operaciones de lectura de transacciones sobre ``FineractClient``.

Contratos verificados contra el código fuente de Fineract:

* ``ClientTransactionsApiResource`` (``/v1/clients``):
  ``GET api/v1/clients/{clientId}/transactions`` admite ``offset`` y ``limit``.
* ``SavingsAccountTransactionsApiResource`` (``/v1/savingsaccounts``): el
  listado es ``GET api/v1/savingsaccounts/{savingsId}/transactions/search``
  (no existe un listado plano en ``/transactions``); admite ``offset``,
  ``limit``, ``orderBy`` y ``sortOrder``.
* ``LoanTransactionsApiResource`` (``/v1/loans``):
  ``GET api/v1/loans/{loanId}/transactions`` usa paginación ``page``/``size``
  (no ``offset``/``limit``).

Los tres endpoints de listado devuelven una página con ``totalFilteredRecords``
y ``pageItems``. Los dos endpoints de detalle (
``GET api/v1/savingsaccounts/{savingsId}/transactions/{transactionId}`` y
``GET api/v1/loans/{loanId}/transactions/{transactionId}``) devuelven un objeto
único con todos los campos del data class; aquí se validan contra los resúmenes
de solo lectura, descartando los campos sensibles (staff, ``note``,
``paymentDetailData``, porciones financieras, etc.).

Los resúmenes aplicados aquí son exactamente los de los listados: se reutilizan
sin añadir campos. Fineract serializa ``LocalDate`` como ``[año, mes, día]``.

No existe ningún camino de escritura en este módulo.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.integrations.fineract.client import FineractClient, FineractResponseError
from app.tools.schemas import (
    ClientTransactionsResult,
    ClientTransactionSummary,
    GetLoanTransactionArgs,
    GetSavingsTransactionArgs,
    ListClientTransactionsArgs,
    ListLoanTransactionsArgs,
    LoanTransactionsResult,
    LoanTransactionSummary,
    SavingsTransactionsResult,
    SavingsTransactionSummary,
    SearchSavingsTransactionsArgs,
)

# Límite/tamaño de página aplicado cuando el llamador no lo especifica.
DEFAULT_TRANSACTION_LIMIT = 20


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


async def list_client_transactions(
    client: FineractClient, args: ListClientTransactionsArgs
) -> ClientTransactionsResult:
    """Lista las transacciones de un cliente (vista reducida)."""
    params: dict[str, object] = {
        "limit": args.limit if args.limit is not None else DEFAULT_TRANSACTION_LIMIT,
    }
    if args.offset is not None:
        params["offset"] = args.offset

    raw = await client.get(f"api/v1/clients/{args.client_id}/transactions", params=params)
    try:
        page_items, total = _page_items(raw)
        transactions = [
            ClientTransactionSummary.model_validate(item) for item in page_items
        ]
        return ClientTransactionsResult(
            totalFilteredRecords=total, transactions=transactions
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de transacciones de cliente"
        ) from exc


async def search_savings_transactions(
    client: FineractClient, args: SearchSavingsTransactionsArgs
) -> SavingsTransactionsResult:
    """Busca/listas las transacciones de una cuenta de ahorro (vista reducida)."""
    params: dict[str, object] = {
        "limit": args.limit if args.limit is not None else DEFAULT_TRANSACTION_LIMIT,
    }
    if args.offset is not None:
        params["offset"] = args.offset
    if args.order_by is not None:
        params["orderBy"] = args.order_by
    if args.sort_order is not None:
        params["sortOrder"] = args.sort_order

    raw = await client.get(
        f"api/v1/savingsaccounts/{args.savings_id}/transactions/search", params=params
    )
    try:
        page_items, total = _page_items(raw)
        transactions = [
            SavingsTransactionSummary.model_validate(item) for item in page_items
        ]
        return SavingsTransactionsResult(
            totalFilteredRecords=total, transactions=transactions
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de transacciones de ahorro"
        ) from exc


async def list_loan_transactions(
    client: FineractClient, args: ListLoanTransactionsArgs
) -> LoanTransactionsResult:
    """Lista las transacciones de un préstamo (vista reducida)."""
    params: dict[str, object] = {
        "page": args.page if args.page is not None else 0,
        "size": args.size if args.size is not None else DEFAULT_TRANSACTION_LIMIT,
    }

    raw = await client.get(f"api/v1/loans/{args.loan_id}/transactions", params=params)
    try:
        page_items, total = _page_items(raw)
        transactions = [
            LoanTransactionSummary.model_validate(item) for item in page_items
        ]
        return LoanTransactionsResult(
            totalFilteredRecords=total, transactions=transactions
        )
    except (KeyError, TypeError, ValidationError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de transacciones de préstamo"
        ) from exc


async def get_savings_transaction(
    client: FineractClient, args: GetSavingsTransactionArgs
) -> SavingsTransactionSummary:
    """Obtiene una transacción concreta de una cuenta de ahorro (vista reducida).

    El endpoint no declara ``fields``; Fineract devuelve el objeto completo,
    que aquí se minimiza localmente mediante el resumen (``extra="ignore"``).
    """
    raw = await client.get(
        f"api/v1/savingsaccounts/{args.savings_id}/transactions/{args.transaction_id}"
    )
    try:
        return SavingsTransactionSummary.model_validate(raw)
    except (TypeError, ValidationError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de la transacción de ahorro"
        ) from exc


async def get_loan_transaction(
    client: FineractClient, args: GetLoanTransactionArgs
) -> LoanTransactionSummary:
    """Obtiene una transacción concreta de un préstamo (vista reducida).

    El endpoint admite ``fields``, pero su whitelist soportada no incluye
    ``manuallyReversed``; por eso no se envía ``fields`` y la respuesta completa
    se minimiza localmente mediante el resumen (``extra="ignore"``).
    """
    raw = await client.get(
        f"api/v1/loans/{args.loan_id}/transactions/{args.transaction_id}"
    )
    try:
        return LoanTransactionSummary.model_validate(raw)
    except (TypeError, ValidationError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de la transacción de préstamo"
        ) from exc
