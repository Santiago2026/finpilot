import hashlib, json
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.db.models import ApprovalRequest, PlanStep, OperationalPlan, AgentRequest
from app.audit.service import record_event

def payload_hash(tool_name: str, arguments: dict[str, Any]) -> str:
    raw = json.dumps([tool_name, arguments], sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()
async def create_approval(session: AsyncSession, step: PlanStep, actor_id: int | None = None) -> ApprovalRequest:
    item = ApprovalRequest(step_id=step.id, payload_hash=payload_hash(step.mcp_tool_name, step.arguments), expires_at=datetime.now(timezone.utc)+timedelta(seconds=get_settings().approval_ttl_seconds))
    session.add(item); await session.flush()
    await record_event(session, "APPROVAL_CREATED", {"approval_id": item.id, "step_id": step.id, "payload_hash": item.payload_hash}, actor_id=actor_id)
    return item
async def decide_approval(session: AsyncSession, approval_id: int, decision: str, actor_id: int | None):
    item = await session.get(ApprovalRequest, approval_id)
    if item is None: raise LookupError("Aprobación no encontrada")
    if item.status != "PENDING": raise ValueError("La aprobación ya fue decidida")
    expires = item.expires_at
    if expires.tzinfo is None: expires = expires.replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        item.status = "EXPIRED"
        await record_event(session, "APPROVAL_EXPIRED", {"approval_id": item.id}, actor_id=actor_id)
        raise TimeoutError("La aprobación expiró")
    step = await session.get(PlanStep, item.step_id)
    if payload_hash(step.mcp_tool_name, step.arguments) != item.payload_hash:
        raise ValueError("El payload de la aprobación fue modificado")
    item.status = decision
    item.approver_id = actor_id
    item.decision = decision
    if decision == "REJECTED":
        step.status = "REJECTED"
        plan = await session.get(OperationalPlan, step.plan_id)
        request = await session.get(AgentRequest, plan.request_id) if plan else None
        if request:
            request.status = "REJECTED"
    await record_event(session, f"APPROVAL_{decision}", {"approval_id": item.id, "payload_hash": item.payload_hash}, actor_id=actor_id)
    return item
