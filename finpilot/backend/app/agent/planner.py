"""Planificador del agente FinPilot sobre LangChain.

El modelo (Hugging Face o Watsonx) solo **propone** un plan de llamadas a
herramientas. Nunca ejecuta nada: la API valida el plan, clasifica el riesgo y,
si es una escritura, exige aprobación HITL antes de invocar MCP.

LangSmith se activa con ``LANGCHAIN_TRACING_V2``, ``LANGCHAIN_API_KEY`` y
``LANGCHAIN_PROJECT``; cada planificación se envía con un ``run_name`` estable y
un ``run_id`` propio.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings


class PlanStepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str
    arguments: dict[str, Any]
    justification: str = ""


class OperationalPlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[PlanStepSpec] = Field(min_length=1, max_length=20)


class PlannerConfigurationError(RuntimeError):
    """El proveedor del modelo no está configurado o no es válido."""


SYSTEM_PROMPT = (
    "Devuelve solo JSON {steps:[{tool_name,arguments,justification}]}. "
    "Usa únicamente herramientas disponibles y nunca inventes identificadores."
)

_JSON_BLOCK = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def _provider(settings: Settings) -> str:
    return (settings.model_provider or "huggingface").strip().lower()


def _trace_config(settings: Settings, trace_id: str) -> dict[str, Any]:
    os.environ["LANGCHAIN_TRACING_V2"] = "true" if settings.langchain_tracing_v2 else "false"
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
    if settings.langchain_api_key.get_secret_value():
        os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key.get_secret_value()
    return {"run_id": trace_id, "run_name": "finpilot-plan"}


def _require_hf(settings: Settings) -> None:
    if not settings.hf_token.get_secret_value():
        raise PlannerConfigurationError(
            "Hugging Face no está configurado: define HF_TOKEN y HF_MODEL_ID "
            "(o envía un plan estructurado en el campo `plan`)"
        )


def _require_watsonx(settings: Settings) -> None:
    if not (settings.watsonx_api_key.get_secret_value() and settings.watsonx_project_id):
        raise PlannerConfigurationError(
            "Watsonx no está configurado: define WATSONX_API_KEY y "
            "WATSONX_PROJECT_ID (o envía un plan estructurado en el campo `plan`)"
        )


def _require_ollama(settings: Settings) -> None:
    if not settings.ollama_model.strip():
        raise PlannerConfigurationError(
            "Ollama no está configurado: define OLLAMA_MODEL "
            "(o envía un plan estructurado en el campo `plan`)"
        )


def _build_hf_model(settings: Settings):
    _require_hf(settings)
    from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint

    endpoint = HuggingFaceEndpoint(
        repo_id=settings.hf_model_id,
        provider=settings.hf_provider or None,
        task="text-generation",
        huggingfacehub_api_token=settings.hf_token.get_secret_value(),
        temperature=0.0,
        max_new_tokens=settings.hf_max_new_tokens,
    )
    return ChatHuggingFace(llm=endpoint)


def _build_watsonx_model(settings: Settings):
    _require_watsonx(settings)
    from langchain_ibm import ChatWatsonx

    return ChatWatsonx(
        model_id=settings.watsonx_model_id,
        url=settings.watsonx_url,
        apikey=settings.watsonx_api_key.get_secret_value(),
        project_id=settings.watsonx_project_id,
        params={"temperature": 0},
    )


def _build_ollama_model(settings: Settings):
    _require_ollama(settings)
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=settings.ollama_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        format="json",
        num_ctx=settings.ollama_num_ctx,
        num_predict=settings.ollama_num_predict,
    )


async def _list_mcp_tools(settings: Settings) -> list[dict[str, Any]]:
    """Descubre las tools publicadas por el servidor MCP de FinPilot."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(settings.mcp_url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            listed = await session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description or tool.name,
                    "input_schema": _planning_schema(
                        getattr(tool, "input_schema", None)
                        or getattr(tool, "inputSchema", None)
                        or {}
                    ),
                }
                for tool in listed.tools
            ]


def _planning_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Quita los campos de aprobación: los inyecta la capa HITL, no el modelo."""
    cleaned = json.loads(json.dumps(schema))
    properties = cleaned.get("properties", {})
    for field in ("approvalId", "payloadHash"):
        properties.pop(field, None)
    if "required" in cleaned:
        cleaned["required"] = [
            name
            for name in cleaned["required"]
            if name not in {"approvalId", "payloadHash"}
        ]
    return cleaned


def _langchain_tools(tools: list[dict[str, Any]]):
    """Convierte los esquemas MCP en tools de LangChain que no ejecutan nada."""
    from langchain_core.tools import StructuredTool
    from pydantic import create_model

    built = []
    for remote in tools:
        schema = remote.get("input_schema") or remote.get("inputSchema") or {}
        props = schema.get("properties", {})
        required = set(schema.get("required", []))
        fields = {}
        for field_name, definition in props.items():
            typ = {"string": str, "integer": int, "number": float, "boolean": bool}.get(
                definition.get("type"), Any
            )
            fields[field_name] = (typ, ... if field_name in required else None)
        args_model = create_model(f"{remote['name'].title()}Args", **fields)

        async def never_execute(**_kwargs):
            raise RuntimeError("Las tools MCP se ejecutan solo tras autorización determinística")

        built.append(
            StructuredTool(
                name=remote["name"],
                description=remote["description"],
                args_schema=args_model,
                coroutine=never_execute,
            )
        )
    return built


def _hf_system_prompt(tools: list[dict[str, Any]]) -> str:
    from datetime import date

    catalog = json.dumps(tools, ensure_ascii=False)
    today = date.today().isoformat()
    return (
        "Eres el planificador de FinPilot, un agente de operaciones financieras "
        "sobre un sandbox de Apache Fineract.\n"
        "Devuelve SOLO un objeto JSON válido con esta forma:\n"
        '{"steps":[{"tool_name":"<nombre>","arguments":{...},"justification":"..."}]}\n'
        "Reglas:\n"
        "- Usa exclusivamente las herramientas del catálogo.\n"
        "- Nunca inventes identificadores: si falta un ID, no lo adivines.\n"
        "- Si piden saldo, dinero o cuánto hay en una cuenta, usa "
        "get_savings_account con savingsId (NO get_client_accounts).\n"
        "- Para escrituras incluye todos los argumentos; la aprobación HITL la "
        "agrega el sistema, no la incluyas tú.\n"
        "- No incluyas texto fuera del JSON.\n"
        f"Fecha de hoy (para transactionDate): {today}.\n"
        "Ejemplos:\n"
        '- "¿Cuánto dinero hay en la cuenta 1?" -> {"steps":[{"tool_name":'
        '"get_savings_account","arguments":{"savingsId":1},"justification":'
        '"Consultar saldo"}]}\n'
        '- "Dame los datos del cliente 2" -> {"steps":[{"tool_name":"get_client",'
        '"arguments":{"clientId":2},"justification":"Consultar cliente"}]}\n'
        '- "Busca al cliente Ana" -> {"steps":[{"tool_name":"search_clients",'
        '"arguments":{"displayName":"Ana"},"justification":"Buscar cliente"}]}\n'
        '- "Movimientos de la cuenta 1" -> {"steps":[{"tool_name":'
        '"search_savings_transactions","arguments":{"savingsId":1},'
        '"justification":"Ver movimientos"}]}\n'
        '- "Transfiere 25 USD de la cuenta 1 a la 2" -> {"steps":[{"tool_name":'
        '"transfer_funds","arguments":{"fromAccountId":1,"toAccountId":2,'
        f'"transactionDate":"{today}","amount":25,"currency":"USD",'
        '"description":"FinPilot transfer"},"justification":"Transferir"}]}\n'
        f"Catálogo de herramientas:\n{catalog}"
    )


def _parse_plan(content: str) -> dict[str, Any]:
    text = _JSON_BLOCK.sub("", content.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError as exc:
                raise PlannerConfigurationError(
                    "El modelo no devolvió un plan JSON válido"
                ) from exc
        raise PlannerConfigurationError("El modelo no devolvió un plan JSON válido")


def _calls_to_plan(calls: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "steps": [
            {
                "tool_name": call["name"],
                "arguments": {
                    key: value
                    for key, value in call.get("args", {}).items()
                    if key not in {"approvalId", "payloadHash"}
                },
                "justification": call.get("justification", ""),
            }
            for call in calls
        ]
    }


async def plan_instruction(instruction: str) -> tuple[dict[str, Any], str | None]:
    """Genera un plan validable a partir de una instrucción en lenguaje natural.

    Hugging Face y Ollama usan salida JSON guiada por prompt (compatible con
    modelos sin tool calling nativo). Watsonx usa tool calling nativo de
    LangChain. En todos los casos el modelo solo propone; la ejecución es
    determinística y auditada.
    """
    settings = get_settings()
    provider = _provider(settings)
    # Validar el proveedor antes de abrir la sesión MCP evita errores opacos
    # del TaskGroup cuando falta la credencial del modelo.
    if provider == "huggingface":
        _require_hf(settings)
    elif provider == "watsonx":
        _require_watsonx(settings)
    elif provider == "ollama":
        _require_ollama(settings)
    else:
        raise PlannerConfigurationError(
            f"MODEL_PROVIDER no soportado: {provider!r} (usa huggingface, ollama o watsonx)"
        )

    tools = await _list_mcp_tools(settings)
    trace_id = str(uuid4())
    config = _trace_config(settings, trace_id)

    from langchain_core.messages import HumanMessage, SystemMessage

    if provider in {"huggingface", "ollama"}:
        model = _build_hf_model(settings) if provider == "huggingface" else _build_ollama_model(settings)
        result = await model.ainvoke(
            [
                SystemMessage(content=_hf_system_prompt(tools)),
                HumanMessage(content=instruction),
            ],
            config=config,
        )
        calls = getattr(result, "tool_calls", []) or []
        plan = _calls_to_plan(calls) if calls else _parse_plan(str(result.content))
    else:
        model = _build_watsonx_model(settings)
        result = await model.bind_tools(_langchain_tools(tools)).ainvoke(
            [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=instruction)],
            config=config,
        )
        calls = getattr(result, "tool_calls", []) or []
        plan = _calls_to_plan(calls) if calls else _parse_plan(str(result.content))

    return OperationalPlanSpec.model_validate(plan).model_dump(), trace_id
