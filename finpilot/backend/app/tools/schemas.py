"""Contratos de argumentos y respuestas de las herramientas de solo lectura.

Los modelos de argumentos son idénticos en restricciones a los del harness de
evaluación (``scripts/eval_tool_calling.py``): la prueba
``test/test_schema_consistency.py`` impide la deriva entre ambos.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

class TransferFundsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_account_id: int = Field(alias="fromAccountId", ge=1)
    to_account_id: int = Field(alias="toAccountId", ge=1)
    transaction_date: str = Field(alias="transactionDate", pattern=r"^\d{4}-\d{2}-\d{2}$")
    amount: float = Field(gt=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    description: str = Field(default="FinPilot transfer", max_length=250)
    approval_id: int | None = Field(default=None, alias="approvalId", ge=1)
    payload_hash: str | None = Field(default=None, alias="payloadHash", pattern="^[0-9a-f]{64}$")

class UpdateFraudStatusArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str = Field(alias="transactionId", min_length=1, max_length=100)
    status: str = Field(pattern="^(REVIEW|CLEARED|CONFIRMED)$")
    reason: str = Field(min_length=1, max_length=500)
    approval_id: int | None = Field(default=None, alias="approvalId", ge=1)
    payload_hash: str | None = Field(default=None, alias="payloadHash", pattern="^[0-9a-f]{64}$")

class TransferFundsMCPArgs(TransferFundsArgs):
    approval_id: int = Field(alias="approvalId", ge=1)
    payload_hash: str = Field(alias="payloadHash", pattern="^[0-9a-f]{64}$")

class UpdateFraudStatusMCPArgs(UpdateFraudStatusArgs):
    approval_id: int = Field(alias="approvalId", ge=1)
    payload_hash: str = Field(alias="payloadHash", pattern="^[0-9a-f]{64}$")


class SearchClientsArgs(BaseModel):
    """Argumentos de ``search_clients`` (coincidencia parcial de nombre)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    display_name: str | None = Field(default=None, alias="displayName")
    limit: int | None = Field(default=None, ge=1, le=100)


class GetClientArgs(BaseModel):
    """Argumentos de ``get_client``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    client_id: int = Field(alias="clientId", ge=1)


class GetClientAccountsArgs(BaseModel):
    """Argumentos de ``get_client_accounts``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    client_id: int = Field(alias="clientId", ge=1)


class GetSavingsAccountArgs(BaseModel):
    """Argumentos de ``get_savings_account``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    savings_id: int = Field(alias="savingsId", ge=1)


class GetLoanArgs(BaseModel):
    """Argumentos de ``get_loan``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    loan_id: int = Field(alias="loanId", ge=1)


class ListSavingsAccountsArgs(BaseModel):
    """Argumentos de ``list_savings_accounts``.

    El endpoint no admite filtrar por cliente; solo paginación.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    offset: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1, le=100)


class ListLoansArgs(BaseModel):
    """Argumentos de ``list_loans``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    client_id: int | None = Field(default=None, alias="clientId", ge=1)
    offset: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1, le=100)


class ClientSummary(BaseModel):
    """Vista reducida de un cliente (data minimization)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    display_name: str | None = Field(default=None, alias="displayName")
    active: bool | None = None
    office_name: str | None = Field(default=None, alias="officeName")


class SearchClientsResult(BaseModel):
    """Resultado de la búsqueda de clientes."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    total_filtered_records: int = Field(alias="totalFilteredRecords")
    clients: list[ClientSummary]


class OptionSummary(BaseModel):
    """Opción enumerada reducida (estado, tipo, etc.)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int | None = None
    code: str | None = None
    value: str | None = None
    description: str | None = None


class CurrencySummary(BaseModel):
    """Moneda reducida (solo el código ISO)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    code: str | None = None


class ClientAccountSummary(BaseModel):
    """Cuenta (préstamo o ahorro) asociada a un cliente, vista reducida."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    account_no: str | None = Field(default=None, alias="accountNo")
    product_name: str | None = Field(default=None, alias="productName")
    status: OptionSummary | None = None


class ClientAccountsResult(BaseModel):
    """Resumen de cuentas de ahorro y préstamo de un cliente."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    loan_accounts: list[ClientAccountSummary] = Field(
        default_factory=list, alias="loanAccounts"
    )
    savings_accounts: list[ClientAccountSummary] = Field(
        default_factory=list, alias="savingsAccounts"
    )


class SavingsAccountBalance(BaseModel):
    """Saldos de una cuenta de ahorro (vista reducida)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    account_balance: float | None = Field(default=None, alias="accountBalance")
    available_balance: float | None = Field(default=None, alias="availableBalance")


class SavingsAccountSummary(BaseModel):
    """Vista reducida de una cuenta de ahorro (data minimization)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    account_no: str | None = Field(default=None, alias="accountNo")
    external_id: str | None = Field(default=None, alias="externalId")
    client_id: int | None = Field(default=None, alias="clientId")
    client_name: str | None = Field(default=None, alias="clientName")
    product_name: str | None = Field(default=None, alias="savingsProductName")
    status: OptionSummary | None = None
    currency: CurrencySummary | None = None
    summary: SavingsAccountBalance | None = None


class LoanSummary(BaseModel):
    """Vista reducida de un préstamo (data minimization)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    account_no: str | None = Field(default=None, alias="accountNo")
    client_id: int | None = Field(default=None, alias="clientId")
    client_name: str | None = Field(default=None, alias="clientName")
    product_name: str | None = Field(default=None, alias="loanProductName")
    loan_type: OptionSummary | None = Field(default=None, alias="loanType")
    status: OptionSummary | None = None
    currency: CurrencySummary | None = None


class SavingsAccountsResult(BaseModel):
    """Resultado del listado de cuentas de ahorro."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    total_filtered_records: int = Field(alias="totalFilteredRecords")
    savings_accounts: list[SavingsAccountSummary] = Field(alias="savingsAccounts")


class LoansResult(BaseModel):
    """Resultado del listado de préstamos."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    total_filtered_records: int = Field(alias="totalFilteredRecords")
    loans: list[LoanSummary]


class ListClientTransactionsArgs(BaseModel):
    """Argumentos de ``list_client_transactions``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    client_id: int = Field(alias="clientId", ge=1)
    offset: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1, le=100)


class SearchSavingsTransactionsArgs(BaseModel):
    """Argumentos de ``search_savings_transactions``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    savings_id: int = Field(alias="savingsId", ge=1)
    offset: int | None = Field(default=None, ge=0)
    limit: int | None = Field(default=None, ge=1, le=100)
    order_by: str | None = Field(default=None, alias="orderBy")
    sort_order: str | None = Field(default=None, alias="sortOrder")


class ListLoanTransactionsArgs(BaseModel):
    """Argumentos de ``list_loan_transactions``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    loan_id: int = Field(alias="loanId", ge=1)
    page: int | None = Field(default=None, ge=0)
    size: int | None = Field(default=None, ge=1, le=100)


class GetSavingsTransactionArgs(BaseModel):
    """Argumentos de ``get_savings_transaction``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    savings_id: int = Field(alias="savingsId", ge=1)
    transaction_id: int = Field(alias="transactionId", ge=1)


class GetLoanTransactionArgs(BaseModel):
    """Argumentos de ``get_loan_transaction``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    loan_id: int = Field(alias="loanId", ge=1)
    transaction_id: int = Field(alias="transactionId", ge=1)


class ClientTransactionSummary(BaseModel):
    """Vista reducida de una transacción de cliente."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    type: OptionSummary | None = None
    date: list[int] | str | None = None
    amount: float | None = None
    reversed: bool | None = None
    currency: CurrencySummary | None = None


class ClientTransactionsResult(BaseModel):
    """Resultado del listado de transacciones de un cliente."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    total_filtered_records: int = Field(alias="totalFilteredRecords")
    transactions: list[ClientTransactionSummary]


class SavingsTransactionSummary(BaseModel):
    """Vista reducida de una transacción de cuenta de ahorro."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    transaction_type: OptionSummary | None = Field(default=None, alias="transactionType")
    date: list[int] | str | None = None
    amount: float | None = None
    reversed: bool | None = None
    currency: CurrencySummary | None = None


class SavingsTransactionsResult(BaseModel):
    """Resultado del listado de transacciones de una cuenta de ahorro."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    total_filtered_records: int = Field(alias="totalFilteredRecords")
    transactions: list[SavingsTransactionSummary]


class LoanTransactionSummary(BaseModel):
    """Vista reducida de una transacción de préstamo."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: int
    type: OptionSummary | None = None
    date: list[int] | str | None = None
    amount: float | None = None
    manually_reversed: bool | None = Field(default=None, alias="manuallyReversed")
    currency: CurrencySummary | None = None


class LoanTransactionsResult(BaseModel):
    """Resultado del listado de transacciones de un préstamo."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    total_filtered_records: int = Field(alias="totalFilteredRecords")
    transactions: list[LoanTransactionSummary]
