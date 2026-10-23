"""Servidor MCP de FinPilot: expone únicamente herramientas de solo lectura.

Las herramientas delegan en ``app.tools.registry`` (allowlist y validación
estricta), que a su vez usa ``FineractClient``. El modelo nunca accede
directamente a Fineract. El transporte por defecto es *streamable HTTP* y,
en desarrollo, queda limitado a localhost con protección anti DNS-rebinding
activada por el SDK.

El esquema publicado de cada herramienta se deriva de los modelos canónicos de
``app.tools.schemas`` (única fuente de verdad) y rechaza campos desconocidos
(``additionalProperties: false``), de modo que el protocolo y el registro
aplican exactamente las mismas restricciones.
"""
from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.mcpserver.tools.base import Tool
from mcp.server.mcpserver.utilities.func_metadata import ArgModelBase
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, create_model
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.integrations.fineract.client import FineractClient
from app.tools import registry
from app.tools.schemas import (
    ClientAccountsResult,
    ClientSummary,
    ClientTransactionsResult,
    LoansResult,
    LoanSummary,
    LoanTransactionsResult,
    LoanTransactionSummary,
    SavingsAccountsResult,
    SavingsAccountSummary,
    SavingsTransactionsResult,
    SavingsTransactionSummary,
    SearchClientsResult,
)
from app.tools.schemas import (TransferFundsArgs, UpdateFraudStatusArgs, TransferFundsMCPArgs, UpdateFraudStatusMCPArgs)
from app.core.config import get_settings

ClientProvider = Callable[[], FineractClient]

_client_provider: ClientProvider | None = None


def set_client_provider(provider: ClientProvider | None) -> None:
    """Registra (o limpia) el proveedor del ``FineractClient`` compartido."""
    global _client_provider
    _client_provider = provider


def _get_client() -> FineractClient:
    if _client_provider is None:
        raise ToolError("Servicio de consulta no disponible")
    return _client_provider()


class _StrictArgsBase(ArgModelBase):
    """Base de argumentos MCP que rechaza campos desconocidos.

    El SDK usa ``extra="ignore"`` por defecto; ``extra="forbid"`` alinea el
    protocolo con la validación estricta del registro de herramientas.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)


def _strict_arg_model(name: str, canonical: type[BaseModel]) -> type[ArgModelBase]:
    """Deriva un modelo de argumentos estricto desde el modelo canónico.

    Se conservan alias, tipos, restricciones y valores por defecto, de modo que
    el esquema publicado coincide con ``app.tools.schemas`` salvo el título.
    """
    fields: dict[str, Any] = {}
    for field_name, field_info in canonical.model_fields.items():
        key = field_info.alias or field_name
        fields[key] = (field_info.annotation, field_info)
    return create_model(f"{name}Arguments", __base__=_StrictArgsBase, **fields)


def _format_arg_error(name: str, exc: ValidationError) -> str:
    """Mensaje de validación conciso, sin eco de la entrada ni detalles internos."""
    details = []
    for error in exc.errors(include_url=False, include_input=False):
        location = ".".join(str(part) for part in error.get("loc", ())) or "valor"
        details.append(f"{location}: {error.get('type', 'invalid')}")
    return f"Argumentos inválidos para {name} ({'; '.join(details)})"


class _SanitizedTool(Tool):
    """``Tool`` cuyos errores de validación no filtran detalles internos.

    El SDK devuelve el ``ValidationError`` de Pydantic tal cual, incluyendo el
    valor enviado por el cliente, el nombre de la clase interna del modelo y una
    URL de documentación. Aquí se reemplaza por un mensaje con el campo y el
    tipo de error, conservando lo útil para depurar y corregir la llamada.
    """

    async def run(self, arguments, context, convert_result=False):
        try:
            return await super().run(arguments, context, convert_result=convert_result)
        except ToolError as exc:
            cause = exc.__cause__
            if isinstance(cause, ValidationError):
                raise ToolError(_format_arg_error(self.name, cause)) from None
            raise


def _canonical_tool(fn: Callable[..., Any], name: str) -> Tool:
    """Construye una ``Tool`` con esquema y validación derivados del registro."""
    canonical, _handler, description = registry.READ_TOOLS[name]
    tool = _SanitizedTool.from_function(fn, name=name, description=description)
    tool.fn_metadata.arg_model = _strict_arg_model(name, canonical)
    tool.parameters = canonical.model_json_schema(by_alias=True)
    return tool


def _write_tool(fn: Callable[..., Any], name: str, model: type[BaseModel], description: str) -> Tool:
    tool = _SanitizedTool.from_function(fn, name=name, description=description)
    tool.fn_metadata.arg_model = _strict_arg_model(name, model)
    tool.parameters = model.model_json_schema(by_alias=True)
    return tool


async def _require_approved_write(approval_id: int | None, provided_hash: str | None, name: str, arguments: dict[str, Any]) -> None:
    if not approval_id or not provided_hash:
        raise ToolError("Se requiere una aprobación HITL registrada")
    from sqlalchemy import select
    from app.db.session import SessionLocal
    from app.db.models import ApprovalRequest, PlanStep
    from app.approvals.service import payload_hash
    async with SessionLocal() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        step = await session.get(PlanStep, approval.step_id) if approval else None
        if not approval or not step or approval.status != "APPROVED" or step.mcp_tool_name != name:
            raise ToolError("Aprobación no válida")
        actual = payload_hash(name, arguments)
        if actual != provided_hash or actual != approval.payload_hash or step.arguments != arguments:
            raise ToolError("El payload no coincide con la aprobación")


async def transfer_funds(fromAccountId: int, toAccountId: int, transactionDate: str, amount: float, currency: str = "USD", description: str = "FinPilot transfer", approvalId: int | None = None, payloadHash: str | None = None):
    """Transfiere fondos entre cuentas de ahorro; solo disponible con escritura habilitada."""
    settings = get_settings()
    if not settings.writes_enabled:
        raise ToolError("Las herramientas de escritura están deshabilitadas")
    await _require_approved_write(approvalId, payloadHash, "transfer_funds", {"fromAccountId": fromAccountId, "toAccountId": toAccountId, "transactionDate": transactionDate, "amount": amount, "currency": currency, "description": description})
    args = TransferFundsArgs.model_validate({"fromAccountId": fromAccountId, "toAccountId": toAccountId, "transactionDate": transactionDate, "amount": amount, "currency": currency, "description": description})
    from datetime import datetime
    client = _get_client()
    template = await client.get(
        "api/v1/accounttransfers/template",
        params={
            "fromAccountId": args.from_account_id,
            "fromAccountType": 2,
            "toAccountId": args.to_account_id,
            "toAccountType": 2,
        },
    )
    return await client.post(
        "api/v1/accounttransfers",
        json={
            "fromOfficeId": template["fromOffice"]["id"],
            "fromClientId": template["fromClient"]["id"],
            "fromAccountType": 2,
            "fromAccountId": args.from_account_id,
            "toOfficeId": template["toOffice"]["id"],
            "toClientId": template["toClient"]["id"],
            "toAccountType": 2,
            "toAccountId": args.to_account_id,
            "transferDate": datetime.strptime(args.transaction_date, "%Y-%m-%d").strftime("%d %B %Y"),
            "transferAmount": args.amount,
            "transferDescription": args.description,
            "dateFormat": "dd MMMM yyyy",
            "locale": "en",
        },
    )


async def update_fraud_status(transactionId: str, status: str, reason: str, approvalId: int | None = None, payloadHash: str | None = None):
    """Actualiza un estado sintético de fraude; su ejecución queda en el gateway auditada."""
    if not get_settings().writes_enabled:
        raise ToolError("Las herramientas de escritura están deshabilitadas")
    await _require_approved_write(approvalId, payloadHash, "update_fraud_status", {"transactionId": transactionId, "status": status, "reason": reason})
    args = UpdateFraudStatusArgs.model_validate({"transactionId": transactionId, "status": status, "reason": reason})
    return {"transactionId": args.transaction_id, "status": args.status, "reason": args.reason}


async def search_clients(
    displayName: str | None = None,
    limit: int | None = None,
) -> SearchClientsResult:
    """Busca clientes por nombre o parte del nombre. Solo lectura."""
    raw: dict[str, Any] = {}
    if displayName is not None:
        raw["displayName"] = displayName
    if limit is not None:
        raw["limit"] = limit
    try:
        return await registry.dispatch("search_clients", raw, _get_client())
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


async def get_client(clientId: int) -> ClientSummary:
    """Obtiene los datos de un cliente por su identificador. Solo lectura."""
    try:
        return await registry.dispatch("get_client", {"clientId": clientId}, _get_client())
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


async def get_client_accounts(clientId: int) -> ClientAccountsResult:
    """Obtiene el resumen de cuentas de ahorro y préstamo de un cliente. Solo lectura."""
    try:
        return await registry.dispatch(
            "get_client_accounts", {"clientId": clientId}, _get_client()
        )
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


async def get_savings_account(savingsId: int) -> SavingsAccountSummary:
    """Obtiene los datos de una cuenta de ahorro por su identificador. Solo lectura."""
    try:
        return await registry.dispatch(
            "get_savings_account", {"savingsId": savingsId}, _get_client()
        )
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


async def get_loan(loanId: int) -> LoanSummary:
    """Obtiene los datos de un préstamo por su identificador. Solo lectura."""
    try:
        return await registry.dispatch("get_loan", {"loanId": loanId}, _get_client())
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


def _compact(**values: Any) -> dict[str, Any]:
    """Construye los argumentos crudos descartando los valores ``None``."""
    return {key: value for key, value in values.items() if value is not None}


async def list_savings_accounts(
    offset: int | None = None,
    limit: int | None = None,
) -> SavingsAccountsResult:
    """Lista las cuentas de ahorro (paginado). Solo lectura."""
    raw = _compact(offset=offset, limit=limit)
    try:
        return await registry.dispatch("list_savings_accounts", raw, _get_client())
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


async def list_loans(
    clientId: int | None = None,
    offset: int | None = None,
    limit: int | None = None,
) -> LoansResult:
    """Lista los préstamos, opcionalmente por cliente (paginado). Solo lectura."""
    raw = _compact(clientId=clientId, offset=offset, limit=limit)
    try:
        return await registry.dispatch("list_loans", raw, _get_client())
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


async def list_client_transactions(
    clientId: int,
    offset: int | None = None,
    limit: int | None = None,
) -> ClientTransactionsResult:
    """Lista las transacciones de un cliente. Solo lectura."""
    raw = _compact(clientId=clientId, offset=offset, limit=limit)
    try:
        return await registry.dispatch("list_client_transactions", raw, _get_client())
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


async def search_savings_transactions(
    savingsId: int,
    offset: int | None = None,
    limit: int | None = None,
    orderBy: str | None = None,
    sortOrder: str | None = None,
) -> SavingsTransactionsResult:
    """Lista las transacciones de una cuenta de ahorro. Solo lectura."""
    raw = _compact(
        savingsId=savingsId,
        offset=offset,
        limit=limit,
        orderBy=orderBy,
        sortOrder=sortOrder,
    )
    try:
        return await registry.dispatch(
            "search_savings_transactions", raw, _get_client()
        )
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


async def list_loan_transactions(
    loanId: int,
    page: int | None = None,
    size: int | None = None,
) -> LoanTransactionsResult:
    """Lista las transacciones de un préstamo. Solo lectura."""
    raw = _compact(loanId=loanId, page=page, size=size)
    try:
        return await registry.dispatch("list_loan_transactions", raw, _get_client())
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


async def get_savings_transaction(
    savingsId: int,
    transactionId: int,
) -> SavingsTransactionSummary:
    """Obtiene una transacción concreta de una cuenta de ahorro. Solo lectura."""
    raw = _compact(savingsId=savingsId, transactionId=transactionId)
    try:
        return await registry.dispatch("get_savings_transaction", raw, _get_client())
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


async def get_loan_transaction(
    loanId: int,
    transactionId: int,
) -> LoanTransactionSummary:
    """Obtiene una transacción concreta de un préstamo. Solo lectura."""
    raw = _compact(loanId=loanId, transactionId=transactionId)
    try:
        return await registry.dispatch("get_loan_transaction", raw, _get_client())
    except registry.ToolErrorBase as exc:
        raise ToolError(str(exc)) from exc


mcp_server = MCPServer(
    name="finpilot",
    instructions=(
        "FinPilot expone herramientas estrictas sobre un entorno financiero simulado. "
        "Las escrituras requieren aprobación HITL del API Gateway."
    ),
    tools=[
        _canonical_tool(search_clients, "search_clients"),
        _canonical_tool(get_client, "get_client"),
        _canonical_tool(get_client_accounts, "get_client_accounts"),
        _canonical_tool(get_savings_account, "get_savings_account"),
        _canonical_tool(get_loan, "get_loan"),
        _canonical_tool(list_savings_accounts, "list_savings_accounts"),
        _canonical_tool(list_loans, "list_loans"),
        _canonical_tool(list_client_transactions, "list_client_transactions"),
        _canonical_tool(search_savings_transactions, "search_savings_transactions"),
        _canonical_tool(list_loan_transactions, "list_loan_transactions"),
        _canonical_tool(get_savings_transaction, "get_savings_transaction"),
        _canonical_tool(get_loan_transaction, "get_loan_transaction"),
        *([_write_tool(transfer_funds, "transfer_funds", TransferFundsMCPArgs, "Transfiere fondos; requiere aprobación HITL."), _write_tool(update_fraud_status, "update_fraud_status", UpdateFraudStatusMCPArgs, "Actualiza el estado sintético de fraude; requiere aprobación HITL.")] if get_settings().writes_enabled else []),
    ],
)


class DevTokenMiddleware:
    """Comprueba la cabecera ``X-MCP-Token`` cuando hay token de desarrollo.

    Es una guarda mínima de desarrollo, no un mecanismo de autorización: la
    política real es el allowlist de solo lectura del registro de herramientas.
    La comparación se hace sobre bytes para no fallar con tokens no ASCII.
    """

    def __init__(self, app: ASGIApp, token: SecretStr) -> None:
        self._app = app
        self._token = token.get_secret_value().encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        provided = headers.get(b"x-mcp-token")
        if provided is None or not hmac.compare_digest(provided, self._token):
            response = JSONResponse({"detail": "No autorizado"}, status_code=401)
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


class MCPPathNormalizer:
    """Reescribe ``/mcp`` a ``/mcp/`` antes del enrutado.

    ``Mount("/mcp")`` solo casa con ``/mcp/``; sin esto, Starlette responde con
    un 307 desde ``/mcp``. Aquí la reescritura es interna, así que ambos
    caminos sirven MCP sin redirección externa.
    """

    def __init__(self, app: ASGIApp, path: str = "/mcp") -> None:
        self._app = app
        self._path = path.rstrip("/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path") == self._path:
            scope = {**scope, "path": f"{self._path}/"}
        await self._app(scope, receive, send)


def build_mcp_app(
    *, dev_token: SecretStr | None = None
) -> tuple[ASGIApp, StreamableHTTPSessionManager]:
    """Construye la sub-app Starlette de MCP montada bajo ``/mcp``.

    Devuelve también el gestor de sesiones creado por ``streamable_http_app``:
    cada llamada crea uno nuevo, por lo que hay que asociar cada app con el
    suyo en lugar de leer ``mcp_server.session_manager`` de forma global.
    """
    app: ASGIApp = mcp_server.streamable_http_app(streamable_http_path="/", host="127.0.0.1")
    session_manager = mcp_server.session_manager
    if dev_token is not None and dev_token.get_secret_value():
        app = DevTokenMiddleware(app, dev_token)
    return app, session_manager
