"""Schemathesis contract checks for a REST projection of MCP tool schemas.

MCP streamable HTTP exposes JSON-RPC and tools/list (not OpenAPI). This test
projects the exact Pydantic schemas installed on the two write tools to an
OpenAPI document so Schemathesis can fuzz their HTTP validation boundary.
"""
from __future__ import annotations
import asyncio

import schemathesis
import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
import requests
from schemathesis.core.transport import Response as SchemathesisResponse
from hypothesis import given, settings

from app.tools.schemas import TransferFundsMCPArgs, UpdateFraudStatusMCPArgs

MODELS={
    "transfer_funds": TransferFundsMCPArgs,
    "update_fraud_status": UpdateFraudStatusMCPArgs,
}

def contract_document():
    paths={}
    for name, model in MODELS.items():
        paths[f"/mcp/tools/{name}"]={"post":{
            "operationId":name,
            "requestBody":{"required":True,"content":{"application/json":{"schema":model.model_json_schema(by_alias=True)}}},
            "responses":{
                "200":{"description":"Argumentos válidos","content":{"application/json":{"schema":{"type":"object"}}}},
                "403":{"description":"Aprobación ausente o inválida","content":{"application/json":{"schema":{"type":"object"}}}},
                "409":{"description":"Payload no coincide con la aprobación","content":{"application/json":{"schema":{"type":"object"}}}},
                "422":{"description":"Argumentos inválidos","content":{"application/json":{"schema":{"type":"object"}}}},
            },
        }}
    return {"openapi":"3.1.0","info":{"title":"FinPilot MCP write tools","version":"1"},"paths":paths}

CONTRACT=contract_document()
SCHEMA=schemathesis.openapi.from_dict(CONTRACT)
app=FastAPI()

@app.post("/mcp/tools/{tool_name}")
async def validate_tool(tool_name: str, request: Request):
    model=MODELS.get(tool_name)
    if model is None:
        return JSONResponse({"detail":"unknown tool"},status_code=404)
    try:
        model.model_validate(await request.json())
    except (ValidationError, ValueError, TypeError):
        return JSONResponse({"detail":"invalid arguments"},status_code=422)
    return {"accepted":True,"tool":tool_name}

def as_requests_response(response):
    return SchemathesisResponse(
        status_code=response.status_code,
        headers={key:[value] for key,value in response.headers.items()},
        content=response.content,
        request=requests.Request(method=response.request.method,url=str(response.url)).prepare(),
        elapsed=0.0,
        verify=True,
        message=response.reason_phrase,
    )

def test_write_tool_openapi_contract_and_successful_responses():
    for name in MODELS:
        operation=SCHEMA[f"/mcp/tools/{name}"]["POST"]
        @settings(max_examples=3, derandomize=True, deadline=None)
        @given(case=operation.as_strategy())
        def exercise_generated_case(case):
            async def send():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                    return await client.post(f"/mcp/tools/{name}",json=case.body)
            response=asyncio.run(send())
            case.validate_response(as_requests_response(response))
            assert response.status_code==200
        exercise_generated_case()

@pytest.mark.asyncio
async def test_write_tool_contract_returns_standard_422_for_invalid_arguments():
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
        response=await client.post("/mcp/tools/transfer_funds",json={"fromAccountId":0,"unexpected":True})
    assert response.status_code==422
    assert "detail" in response.json()
