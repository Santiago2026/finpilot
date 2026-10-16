"""Operaciones de lectura de clientes sobre ``FineractClient``.

Contratos verificados contra el código fuente de Fineract
(``ClientsApiResource``): ``GET api/v1/clients`` admite ``displayName``
(coincidencia parcial), ``limit``, ``orderBy`` y ``fields``;
``GET api/v1/clients/{clientId}`` admite ``fields``. No existe ningún
camino de escritura en este módulo.
"""
from __future__ import annotations

from pydantic import ValidationError

from app.integrations.fineract.client import FineractClient, FineractResponseError
from app.tools.schemas import ClientSummary, GetClientArgs, SearchClientsArgs, SearchClientsResult

# Campos reducidos solicitados a Fineract (data minimization en origen).
CLIENT_FIELDS = "id,displayName,active,officeName"

# Límite aplicado cuando el llamador no lo especifica (tamaño de página acotado).
DEFAULT_SEARCH_LIMIT = 20


async def search_clients(
    client: FineractClient, args: SearchClientsArgs
) -> SearchClientsResult:
    """Busca clientes por nombre (coincidencia parcial) y devuelve una vista reducida."""
    params: dict[str, object] = {
        "limit": args.limit if args.limit is not None else DEFAULT_SEARCH_LIMIT,
        "fields": CLIENT_FIELDS,
        "orderBy": "displayName",
        "sortOrder": "ASC",
    }
    if args.display_name is not None:
        params["displayName"] = args.display_name
    raw = await client.get("api/v1/clients", params=params)
    try:
        page_items = raw["pageItems"]
        total = raw["totalFilteredRecords"]
        if not isinstance(page_items, list) or not isinstance(total, int):
            raise TypeError("formato de página inesperado")
        clients = [ClientSummary.model_validate(item) for item in page_items]
        return SearchClientsResult(totalFilteredRecords=total, clients=clients)
    except (KeyError, TypeError, ValidationError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de página de clientes"
        ) from exc


async def get_client(client: FineractClient, args: GetClientArgs) -> ClientSummary:
    """Obtiene la vista reducida de un cliente por identificador."""
    raw = await client.get(
        f"api/v1/clients/{args.client_id}", params={"fields": CLIENT_FIELDS}
    )
    try:
        return ClientSummary.model_validate(raw)
    except (ValidationError, TypeError) as exc:
        raise FineractResponseError(
            "La respuesta de Fineract no tiene la forma esperada de cliente"
        ) from exc
