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
from app.tools.schemas import ClientSummary, SearchClientsResult

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


mcp_server = MCPServer(
    name="finpilot",
    instructions=(
        "FinPilot expone exclusivamente herramientas de SOLO LECTURA sobre un "
        "entorno financiero simulado. No existen herramientas de escritura."
    ),
    tools=[
        _canonical_tool(search_clients, "search_clients"),
        _canonical_tool(get_client, "get_client"),
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
