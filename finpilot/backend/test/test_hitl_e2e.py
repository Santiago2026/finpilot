from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from fastapi import Request
from sqlalchemy import select

from app.api.routes import current_user
from app.approvals.service import payload_hash
from app.core.config import Settings
from app.db.models import (User, AgentRequest, OperationalPlan, PlanStep, RiskEvaluation,
                           ApprovalRequest, SandboxExecution, AuditEvent)
from app.db.session import get_session
from app.main import create_app

class MemorySession:
    """Async session fake for exercising HTTP orchestration without a real DB."""
    def __init__(self):
        self.rows = defaultdict(dict)
        self.counts = defaultdict(int)
    def add(self, obj):
        cls=type(obj)
        if cls is ApprovalRequest and obj.status is None:
            obj.status="PENDING"
        if getattr(obj,"id",None) is None:
            self.counts[cls]+=1; obj.id=self.counts[cls]
        self.rows[cls][obj.id]=obj
    async def flush(self): return None
    async def commit(self): return None
    async def rollback(self): return None
    async def get(self, cls, ident): return self.rows[cls].get(ident)
    async def scalar(self, statement):
        entity=statement.column_descriptions[0].get("entity")
        if entity is AuditEvent:
            return max(self.rows[AuditEvent], default=None)
        return None
    async def execute(self, statement): return SimpleNamespace(all=lambda: [])
    async def scalars(self, statement): return SimpleNamespace(all=lambda: [])

def make_settings():
    return Settings(
        fineract_base_url="http://fineract.test:8080/fineract-provider",
        fineract_tenant_id="default", fineract_username="mifos",
        fineract_password="password", fineract_verify_tls=True,
        mcp_enabled=True, mcp_url="http://mcp.test/mcp/",
        mcp_dev_token="mcp-test-token", auth_disabled=True, writes_enabled=True,
        approval_ttl_seconds=900,
    )

@pytest.fixture
def hitl(monkeypatch):
    import app.api.routes as routes
    import app.approvals.service as approval_service
    import app.integrations.fineract.client as fineract_client
    settings=make_settings()
    monkeypatch.setattr(routes,"get_settings",lambda:settings)
    monkeypatch.setattr(approval_service,"get_settings",lambda:settings)
    monkeypatch.setattr(fineract_client,"get_settings",lambda:settings)
    memory=MemorySession()
    async def session_override(): yield memory
    async def user_override(request:Request):
        role=request.headers.get("x-test-role","OPERATOR")
        return User(id=1,username=f"test-{role.lower()}",role=role,active=True)
    application=create_app(settings)
    application.dependency_overrides[get_session]=session_override
    application.dependency_overrides[current_user]=user_override
    return application,memory

def transfer_plan():
    return {"steps":[{"tool_name":"transfer_funds","arguments":{
        "fromAccountId":101,"toAccountId":202,"transactionDate":"2026-10-21",
        "amount":25.0,"currency":"USD","description":"HITL test transfer",
    },"justification":"Transfer requested by account operator"}]}

async def submit(client):
    response=await client.post("/api/v1/ops",json={"instruction":"Transfer 25 USD between test accounts","plan":transfer_plan()})
    assert response.status_code==202, response.text
    assert response.json()["status"]=="AWAITING_APPROVAL"
    body=response.json()
    # Contrato que consume el dashboard: plan y nombre de la tool por paso.
    assert body["plan"]["steps"][0]["tool_name"]=="transfer_funds"
    assert body["steps"][0]["tool_name"]=="transfer_funds"
    return body

def audit_types(memory):
    return [row.event_type for row in memory.rows[AuditEvent].values()]


def mock_mcp_call(result_message):
    """Mock initialize, initialized notification and tools/call HTTP exchanges."""
    def respond(request):
        message=json.loads(request.content)
        if message["method"]=="initialize":
            return httpx.Response(200,json={"jsonrpc":"2.0","id":message["id"],"result":{
                "protocolVersion":"2025-11-25","capabilities":{},
                "serverInfo":{"name":"finpilot-mcp","version":"0.1.0"},
            }},headers={"MCP-Session-Id":"test-session"})
        if message["method"]=="notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200,json={"jsonrpc":"2.0","id":message["id"],"result":result_message})
    return respx.post("http://mcp.test/mcp/").mock(side_effect=respond)

@pytest.mark.asyncio
async def test_sensitive_approval_executes_only_after_approval_and_audits(hitl):
    application,memory=hitl
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url="http://test") as client:
        result=await submit(client)
        approval_id=result["steps"][0]["approval_id"]
        assert result["steps"][0]["status"]=="AWAITING_APPROVAL"
        assert not memory.rows[SandboxExecution]
        with respx.mock:
            mcp=mock_mcp_call({"content":[{"type":"text","text":"{\"resourceId\":901,\"status\":\"COMPLETED\"}"}],"structuredContent":{"resourceId":901,"status":"COMPLETED"}})
            response=await client.post(f"/api/v1/approvals/{approval_id}/decide",json={"decision":"APPROVED"},headers={"x-test-role":"APPROVER"})
        assert response.status_code==200, response.text
        body=response.json()
        assert body["status"]=="APPROVED"
        execution=body["execution"]
        assert execution["status"]=="COMPLETED"
        assert execution["request_id"]==next(iter(memory.rows[AgentRequest]))
        assert execution["plan"]==transfer_plan()
        assert execution["steps"][0]["tool_name"]=="transfer_funds"
        assert execution["steps"][0]["status"]=="COMPLETED"
        assert execution["steps"][0]["result"]["resourceId"]==901
        assert mcp.call_count==3
        rpc= json.loads(mcp.calls.last.request.content)
        assert rpc["method"]=="tools/call"
        assert rpc["params"]["name"]=="transfer_funds"
        assert rpc["params"]["arguments"]=={
            **transfer_plan()["steps"][0]["arguments"],
            "approvalId":approval_id,
            "payloadHash":memory.rows[ApprovalRequest][approval_id].payload_hash,
        }
        assert mcp.calls.last.request.headers["X-MCP-Token"]=="mcp-test-token"
    assert memory.rows[ApprovalRequest][approval_id].status=="APPROVED"
    assert next(iter(memory.rows[SandboxExecution].values())).response["resourceId"]==901
    assert {"REQUEST_RECEIVED","RISK_EVALUATED","APPROVAL_CREATED","APPROVAL_APPROVED","SENSITIVE_STEP_EXECUTED"}.issubset(audit_types(memory))


@pytest.mark.asyncio
async def test_mcp_tool_error_becomes_http_conflict_and_is_not_recorded_as_execution(hitl):
    application,memory=hitl
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url="http://test") as client:
        result=await submit(client); approval_id=result["steps"][0]["approval_id"]
        with respx.mock:
            mock_mcp_call({"isError":True,"content":[{"type":"text","text":"El payload no coincide con la aprobación"}]})
            response=await client.post(f"/api/v1/approvals/{approval_id}/decide",json={"decision":"APPROVED"},headers={"x-test-role":"APPROVER"})
    assert response.status_code==409
    assert "payload" in response.json()["detail"].lower()
    assert not memory.rows[SandboxExecution]


@pytest.mark.asyncio
async def test_mcp_disabled_returns_503_without_execution(hitl,monkeypatch):
    import app.api.routes as routes
    application,memory=hitl
    disabled=make_settings()
    disabled.mcp_enabled=False
    monkeypatch.setattr(routes,"get_settings",lambda:disabled)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url="http://test") as client:
        result=await submit(client); approval_id=result["steps"][0]["approval_id"]
        response=await client.post(f"/api/v1/approvals/{approval_id}/decide",json={"decision":"APPROVED"},headers={"x-test-role":"APPROVER"})
    assert response.status_code==503
    assert response.json()["detail"]=="Servidor MCP no disponible"
    assert not memory.rows[SandboxExecution]


@pytest.mark.asyncio
async def test_writes_disabled_never_creates_or_executes_approval(hitl,monkeypatch):
    import app.api.routes as routes
    application,memory=hitl
    disabled=make_settings()
    disabled.writes_enabled=False
    monkeypatch.setattr(routes,"get_settings",lambda:disabled)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url="http://test") as client:
        response=await client.post("/api/v1/ops",json={"instruction":"transfer funds","plan":transfer_plan()})
    assert response.status_code==403
    assert not memory.rows[ApprovalRequest]
    assert not memory.rows[SandboxExecution]

@pytest.mark.asyncio
async def test_rejection_never_executes_and_updates_request_state(hitl):
    application,memory=hitl
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url="http://test") as client:
        result=await submit(client); approval_id=result["steps"][0]["approval_id"]
        response=await client.post(f"/api/v1/approvals/{approval_id}/decide",json={"decision":"REJECTED"},headers={"x-test-role":"APPROVER"})
    assert response.status_code==200
    body=response.json()
    assert body["status"]=="REJECTED"
    assert body["execution"] is None
    assert memory.rows[ApprovalRequest][approval_id].status=="REJECTED"
    assert next(iter(memory.rows[AgentRequest].values())).status=="REJECTED"
    assert not memory.rows[SandboxExecution]
    assert "APPROVAL_REJECTED" in audit_types(memory)

@pytest.mark.asyncio
async def test_operator_cannot_decide_and_attempt_is_audited(hitl):
    application,memory=hitl
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url="http://test") as client:
        result=await submit(client); approval_id=result["steps"][0]["approval_id"]
        response=await client.post(f"/api/v1/approvals/{approval_id}/decide",json={"decision":"APPROVED"},headers={"x-test-role":"OPERATOR"})
    assert response.status_code==403
    assert memory.rows[ApprovalRequest][approval_id].status=="PENDING"
    assert not memory.rows[SandboxExecution]
    assert "APPROVAL_DECISION_FORBIDDEN" in audit_types(memory)

@pytest.mark.asyncio
async def test_expired_approval_is_not_executed_and_audited(hitl):
    application,memory=hitl
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url="http://test") as client:
        result=await submit(client); approval_id=result["steps"][0]["approval_id"]
        memory.rows[ApprovalRequest][approval_id].expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
        response=await client.post(f"/api/v1/approvals/{approval_id}/decide",json={"decision":"APPROVED"},headers={"x-test-role":"APPROVER"})
    assert response.status_code==410
    assert memory.rows[ApprovalRequest][approval_id].status=="EXPIRED"
    assert not memory.rows[SandboxExecution]
    assert "APPROVAL_EXPIRED" in audit_types(memory)

@pytest.mark.asyncio
async def test_modified_payload_conflicts_and_is_not_executed(hitl):
    application,memory=hitl
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application),base_url="http://test") as client:
        result=await submit(client); approval_id=result["steps"][0]["approval_id"]
        approval=memory.rows[ApprovalRequest][approval_id]
        step=memory.rows[PlanStep][approval.step_id]
        step.arguments={**step.arguments,"amount":26.0}
        assert payload_hash(step.mcp_tool_name,step.arguments)!=approval.payload_hash
        response=await client.post(f"/api/v1/approvals/{approval_id}/decide",json={"decision":"APPROVED"},headers={"x-test-role":"APPROVER"})
    assert response.status_code==409
    assert approval.status=="PENDING"
    assert not memory.rows[SandboxExecution]
    assert "APPROVAL_DECISION_CONFLICT" in audit_types(memory)
