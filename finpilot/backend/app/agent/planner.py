from __future__ import annotations
from typing import Any
from pydantic import BaseModel, ConfigDict, Field
from app.core.config import get_settings

class PlanStepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_name: str
    arguments: dict[str, Any]
    justification: str = ""
class OperationalPlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    steps: list[PlanStepSpec] = Field(min_length=1, max_length=20)

SYSTEM_PROMPT = "Devuelve solo JSON {steps:[{tool_name,arguments,justification}]}. Usa únicamente herramientas disponibles y nunca inventes identificadores."

async def plan_instruction(instruction: str) -> tuple[dict[str, Any], str | None]:
    settings = get_settings()
    if not (settings.watsonx_api_key.get_secret_value() and settings.watsonx_project_id):
        raise RuntimeError("Watsonx no está configurado; envía un plan estructurado o configura WATSONX_API_KEY y WATSONX_PROJECT_ID")
    from langchain_ibm import ChatWatsonx
    from langchain_core.messages import SystemMessage, HumanMessage
    from langchain_core.tools import StructuredTool
    from pydantic import create_model
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from uuid import uuid4
    import json
    import os
    os.environ["LANGCHAIN_TRACING_V2"] = "true" if settings.langchain_tracing_v2 else "false"
    os.environ["LANGCHAIN_PROJECT"] = settings.langchain_project
    if settings.langchain_api_key.get_secret_value():
        os.environ["LANGCHAIN_API_KEY"] = settings.langchain_api_key.get_secret_value()
    model = ChatWatsonx(model_id=settings.watsonx_model_id, url=settings.watsonx_url, apikey=settings.watsonx_api_key.get_secret_value(), project_id=settings.watsonx_project_id, params={"temperature": 0})
    async with streamable_http_client(settings.mcp_url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as mcp_session:
            await mcp_session.initialize()
            listed = await mcp_session.list_tools()
            tools = []
            for remote in listed.tools:
                props = remote.inputSchema.get("properties", {})
                required = set(remote.inputSchema.get("required", []))
                fields = {}
                for field_name, definition in props.items():
                    typ = {"string": str, "integer": int, "number": float, "boolean": bool}.get(definition.get("type"), Any)
                    fields[field_name] = (typ, ... if field_name in required else None)
                args_model = create_model(f"{remote.name.title()}Args", **fields)
                async def never_execute(**_kwargs):
                    raise RuntimeError("Las tools MCP se ejecutan solo tras autorización determinística")
                tools.append(StructuredTool(name=remote.name, description=remote.description or remote.name, args_schema=args_model, coroutine=never_execute))
    # Advertise MCP schemas to the model but never execute returned tool calls here.
    # The deterministic gateway validates, classifies, and authorizes every call.
    trace_id = uuid4()
    bound_model = model.bind_tools(tools)
    result = await bound_model.ainvoke(
        [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=instruction)],
        config={"run_id": trace_id, "run_name": "finpilot-plan"},
    )
    calls = getattr(result, "tool_calls", []) or []
    if calls:
        plan = {"steps": [{"tool_name": call["name"], "arguments": {key: value for key, value in call.get("args", {}).items() if key not in {"approvalId", "payloadHash"}}, "justification": call.get("justification", "")} for call in calls]}
    else:
        content = result.content if isinstance(result.content, str) else str(result.content)
        plan = json.loads(content)
    return OperationalPlanSpec.model_validate(plan).model_dump(), str(trace_id)
