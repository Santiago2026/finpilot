"""Registro de herramientas de solo lectura: allowlist, validación y despacho.

Es la política de seguridad del lado servidor: solo las herramientas
declaradas aquí pueden ejecutarse, y sus argumentos se validan de forma
estricta (``extra="forbid"``) antes de tocar Fineract. El protocolo MCP no
es, por sí mismo, un mecanismo de autorización.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.integrations.fineract import clients as fineract_clients
from app.integrations.fineract.client import (
    FineractClient,
    FineractConfigurationError,
    FineractConnectionError,
    FineractError,
    FineractHTTPError,
    FineractResponseError,
    FineractTimeoutError,
)
from app.tools.schemas import (
    ClientSummary,
    GetClientArgs,
    SearchClientsArgs,
    SearchClientsResult,
)


class ToolErrorBase(Exception):
    """Error base del registro de herramientas."""


class ToolNotFoundError(ToolErrorBase):
    """La herramienta solicitada no está en el allowlist de solo lectura."""


class ToolValidationError(ToolErrorBase):
    """Los argumentos no validan contra el esquema de la herramienta."""


class UpstreamError(ToolErrorBase):
    """Fallo de Fineract, con mensaje seguro (sin cuerpos ni credenciales)."""

    def __init__(self, message: str, *, category: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code


ToolHandler = Callable[[FineractClient, BaseModel], Awaitable[SearchClientsResult | ClientSummary]]


async def _run_search(client: FineractClient, args: BaseModel) -> SearchClientsResult:
    assert isinstance(args, SearchClientsArgs)
    return await fineract_clients.search_clients(client, args)


async def _run_get(client: FineractClient, args: BaseModel) -> ClientSummary:
    assert isinstance(args, GetClientArgs)
    return await fineract_clients.get_client(client, args)


READ_TOOLS: dict[str, tuple[type[BaseModel], ToolHandler, str]] = {
    "search_clients": (
        SearchClientsArgs,
        _run_search,
        "Busca clientes por nombre o parte del nombre (coincidencia parcial). Solo lectura.",
    ),
    "get_client": (
        GetClientArgs,
        _run_get,
        "Obtiene los datos de un cliente por su identificador. Solo lectura.",
    ),
}


def tool_names() -> frozenset[str]:
    return frozenset(READ_TOOLS)


def _to_upstream_error(exc: FineractError) -> UpstreamError:
    if isinstance(exc, FineractHTTPError):
        return UpstreamError(
            f"Fineract respondió con HTTP {exc.status_code}",
            category="http",
            status_code=exc.status_code,
        )
    if isinstance(exc, FineractTimeoutError):
        return UpstreamError("Tiempo de espera agotado al consultar Fineract", category="timeout")
    if isinstance(exc, FineractConnectionError):
        return UpstreamError("No se pudo conectar con Fineract", category="connection")
    if isinstance(exc, FineractResponseError):
        return UpstreamError("Respuesta inesperada de Fineract", category="response")
    if isinstance(exc, FineractConfigurationError):
        return UpstreamError("Configuración de Fineract incompleta", category="configuration")
    return UpstreamError("Fallo al consultar Fineract", category="unknown")


async def dispatch(
    name: str, raw_arguments: dict[str, Any], fineract: FineractClient
) -> SearchClientsResult | ClientSummary:
    """Valida y ejecuta una herramienta del allowlist.

    Lanza ``ToolNotFoundError`` para cualquier herramienta no permitida,
    ``ToolValidationError`` para argumentos inválidos y ``UpstreamError``
    (mensaje seguro) ante fallos de Fineract.
    """
    entry = READ_TOOLS.get(name)
    if entry is None:
        raise ToolNotFoundError(f"Herramienta no permitida: {name}")
    args_model, handler, _description = entry
    try:
        args = args_model.model_validate(raw_arguments or {})
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(loc) for loc in err['loc']) or 'valor'}: {err['type']}"
            for err in exc.errors(include_url=False)
        )
        raise ToolValidationError(f"Argumentos inválidos para {name} ({detail})") from exc
    try:
        return await handler(fineract, args)
    except FineractError as exc:
        raise _to_upstream_error(exc) from exc
