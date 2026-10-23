from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
import respx
from mcp.server.mcpserver.exceptions import ToolError

import app.db.session as db_session
import app.mcp.server as mcp_server
from app.approvals.service import payload_hash
from app.core.config import Settings
from app.db.models import ApprovalRequest, PlanStep
from app.integrations.fineract.client import FineractClient


BASE_URL = "http://fineract.test:8080/fineract-provider"
ARGUMENTS = {
    "fromAccountId": 101,
    "toAccountId": 202,
    "transactionDate": "2026-10-21",
    "amount": 25.0,
    "currency": "USD",
    "description": "FinPilot transfer",
}


class ApprovalSession:
    def __init__(self, approval: object, step: object) -> None:
        self.rows = {ApprovalRequest: approval, PlanStep: step}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def get(self, model: type, ident: int):
        row = self.rows.get(model)
        return row if row and row.id == ident else None


def make_settings(*, writes_enabled: bool = True) -> Settings:
    return Settings(
        fineract_base_url=BASE_URL,
        fineract_tenant_id="default",
        fineract_username="mifos",
        fineract_password="password",
        fineract_verify_tls=True,
        writes_enabled=writes_enabled,
    )


@pytest.mark.asyncio
async def test_transfer_funds_reads_template_and_posts_complete_body(monkeypatch):
    settings = make_settings()
    monkeypatch.setattr(mcp_server, "get_settings", lambda: settings)
    arguments = dict(ARGUMENTS)
    approval_id = 7
    step = SimpleNamespace(id=12, mcp_tool_name="transfer_funds", arguments=arguments)
    approval = SimpleNamespace(id=approval_id, step_id=step.id, status="APPROVED", payload_hash=payload_hash("transfer_funds", arguments))
    monkeypatch.setattr(db_session, "SessionLocal", lambda: ApprovalSession(approval, step))

    client = FineractClient(settings)
    mcp_server.set_client_provider(lambda: client)
    try:
        with respx.mock:
            template = respx.get(f"{BASE_URL}/api/v1/accounttransfers/template").mock(
                return_value=httpx.Response(200, json={
                    "fromOffice": {"id": 1}, "fromClient": {"id": 11},
                    "toOffice": {"id": 2}, "toClient": {"id": 22},
                })
            )
            transfer = respx.post(f"{BASE_URL}/api/v1/accounttransfers").mock(
                return_value=httpx.Response(200, json={"resourceId": 901})
            )
            result = await mcp_server.transfer_funds(
                **ARGUMENTS, approvalId=approval_id,
                payloadHash=approval.payload_hash,
            )

        assert result == {"resourceId": 901}
        assert template.call_count == transfer.call_count == 1
        template_request = template.calls.last.request
        assert dict(template_request.url.params) == {
            "fromAccountId": "101", "fromAccountType": "2",
            "toAccountId": "202", "toAccountType": "2",
        }
        body = json.loads(transfer.calls.last.request.content)
        assert {key: body[key] for key in (
            "fromOfficeId", "fromClientId", "toOfficeId", "toClientId",
        )} == {
            "fromOfficeId": 1, "fromClientId": 11,
            "toOfficeId": 2, "toClientId": 22,
        }
    finally:
        mcp_server.set_client_provider(None)
        await client.aclose()


@pytest.mark.asyncio
async def test_transfer_funds_requires_approval(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_settings", lambda: make_settings())
    with pytest.raises(ToolError, match="Se requiere una aprobación HITL registrada"):
        await mcp_server.transfer_funds(**ARGUMENTS)


@pytest.mark.asyncio
async def test_transfer_funds_rejects_when_writes_are_disabled(monkeypatch):
    monkeypatch.setattr(mcp_server, "get_settings", lambda: make_settings(writes_enabled=False))
    with pytest.raises(ToolError, match="Las herramientas de escritura están deshabilitadas"):
        await mcp_server.transfer_funds(**ARGUMENTS, approvalId=7, payloadHash="0" * 64)
