"""Contratos de argumentos y respuestas de las herramientas de solo lectura.

Los modelos de argumentos son idénticos en restricciones a los del harness de
evaluación (``scripts/eval_tool_calling.py``): la prueba
``test/test_schema_consistency.py`` impide la deriva entre ambos.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SearchClientsArgs(BaseModel):
    """Argumentos de ``search_clients`` (coincidencia parcial de nombre)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    display_name: str | None = Field(default=None, alias="displayName")
    limit: int | None = Field(default=None, ge=1, le=100)


class GetClientArgs(BaseModel):
    """Argumentos de ``get_client``."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    client_id: int = Field(alias="clientId", ge=1)


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
