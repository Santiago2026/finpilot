from __future__ import annotations
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import DateTime, ForeignKey, String, JSON, Text, Boolean, Integer, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def now(): return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
class User(Base):
    __tablename__="users"
    id: Mapped[int]=mapped_column(primary_key=True)
    username: Mapped[str]=mapped_column(String(120), unique=True, index=True)
    role: Mapped[str]=mapped_column(String(20))
    active: Mapped[bool]=mapped_column(Boolean, default=True)
class AgentRequest(Base):
    __tablename__="agent_requests"
    id: Mapped[int]=mapped_column(primary_key=True)
    user_id: Mapped[int|None]=mapped_column(ForeignKey("users.id"), nullable=True)
    instruction: Mapped[str]=mapped_column(Text)
    status: Mapped[str]=mapped_column(String(30), default="RECEIVED", index=True)
    result: Mapped[dict[str,Any]|None]=mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class OperationalPlan(Base):
    __tablename__="operational_plans"
    id: Mapped[int]=mapped_column(primary_key=True)
    request_id: Mapped[int]=mapped_column(ForeignKey("agent_requests.id"), unique=True)
    plan: Mapped[dict[str,Any]]=mapped_column(JSON)
    langsmith_trace_id: Mapped[str|None]=mapped_column(String(100), nullable=True)
class PlanStep(Base):
    __tablename__="plan_steps"
    id: Mapped[int]=mapped_column(primary_key=True)
    plan_id: Mapped[int]=mapped_column(ForeignKey("operational_plans.id"))
    step_order: Mapped[int]=mapped_column(Integer)
    mcp_tool_name: Mapped[str]=mapped_column(String(100))
    arguments: Mapped[dict[str,Any]]=mapped_column(JSON)
    justification: Mapped[str]=mapped_column(Text, default="")
    status: Mapped[str]=mapped_column(String(30), default="PLANNED")
class RiskEvaluation(Base):
    __tablename__="risk_evaluations"
    id: Mapped[int]=mapped_column(primary_key=True)
    step_id: Mapped[int]=mapped_column(ForeignKey("plan_steps.id"))
    risk_level: Mapped[str]=mapped_column(String(30))
    rule_triggered: Mapped[str]=mapped_column(String(255))
class ApprovalRequest(Base):
    __tablename__="approval_requests"
    id: Mapped[int]=mapped_column(primary_key=True)
    step_id: Mapped[int]=mapped_column(ForeignKey("plan_steps.id"))
    payload_hash: Mapped[str]=mapped_column(String(64))
    status: Mapped[str]=mapped_column(String(20), default="PENDING", index=True)
    approver_id: Mapped[int|None]=mapped_column(ForeignKey("users.id"), nullable=True)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    decision: Mapped[str|None]=mapped_column(Text, nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class SandboxExecution(Base):
    __tablename__="sandbox_executions"
    id: Mapped[int]=mapped_column(primary_key=True)
    step_id: Mapped[int]=mapped_column(ForeignKey("plan_steps.id"))
    payload: Mapped[dict[str,Any]]=mapped_column(JSON)
    response: Mapped[dict[str,Any]|None]=mapped_column(JSON, nullable=True)
    mcp_tool_name: Mapped[str]=mapped_column(String(100))
    fineract_resource_id: Mapped[str|None]=mapped_column(String(100), nullable=True)
    langsmith_trace_id: Mapped[str|None]=mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)
class AuditEvent(Base):
    __tablename__="audit_events"
    id: Mapped[int]=mapped_column(primary_key=True)
    event_type: Mapped[str]=mapped_column(String(100), index=True)
    actor_id: Mapped[int|None]=mapped_column(ForeignKey("users.id"), nullable=True)
    event_data: Mapped[dict[str,Any]]=mapped_column(JSON)
    langsmith_trace_id: Mapped[str|None]=mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True), default=now)

@event.listens_for(AuditEvent, "before_update")
@event.listens_for(AuditEvent, "before_delete")
def _audit_is_immutable(mapper, connection, target):
    raise ValueError("audit_events es append-only")
