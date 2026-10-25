from __future__ import annotations
from typing import Any
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import httpx
from mcp_types.version import LATEST_PROTOCOL_VERSION
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import jwt
from app.core.config import get_settings
from app.db.session import get_session
from app.db.models import User, AgentRequest, OperationalPlan, PlanStep, RiskEvaluation, ApprovalRequest, SandboxExecution, AuditEvent
from app.audit.service import record_event
from app.approvals.service import create_approval, decide_approval, payload_hash
from app.policies.engine import validate_tool_call, classify_risk
from app.tools.registry import READ_TOOLS, dispatch
from app.integrations.fineract.client import FineractClient
from app.agent.planner import OperationalPlanSpec, plan_instruction


def _jsonrpc_message(response: httpx.Response) -> dict[str, Any]:
    """Lee una respuesta MCP JSON o el evento JSON-RPC de una respuesta SSE."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line.removeprefix("data:").strip())
                except json.JSONDecodeError:
                    continue
        raise HTTPException(502, "Respuesta MCP SSE inválida")
    try:
        message = response.json()
    except ValueError as exc:
        raise HTTPException(502, "Respuesta MCP inválida") from exc
    if not isinstance(message, dict):
        raise HTTPException(502, "Respuesta MCP inválida")
    return message


def _raise_mcp_error(message: str, code: int | None = None) -> None:
    lowered = message.lower()
    if "aprobación" in lowered or "payload" in lowered:
        status_code = 409
    elif "argument" in lowered or "inválid" in lowered or code == -32602:
        status_code = 400
    elif "deshabilitadas" in lowered:
        status_code = 403
    elif code == -32603:
        status_code = 500
    else:
        status_code = 500
    raise HTTPException(status_code, message)


async def _invoke_mcp_tool(tool_name: str, arguments: dict[str, Any], settings) -> Any:
    """Inicializa una sesión MCP Streamable HTTP y ejecuta tools/call."""
    if not settings.mcp_enabled:
        raise HTTPException(503, "Servidor MCP no disponible")

    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    dev_token = settings.mcp_dev_token.get_secret_value()
    if dev_token:
        headers["X-MCP-Token"] = dev_token

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            initialize = await client.post(settings.mcp_url, headers=headers, json={
                "jsonrpc": "2.0",
                "id": str(uuid4()),
                "method": "initialize",
                "params": {
                    "protocolVersion": LATEST_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "finpilot-api", "version": "0.1.0"},
                },
            })
            if initialize.status_code >= 400:
                raise HTTPException(503, "Servidor MCP no disponible")
            init_message = _jsonrpc_message(initialize)
            if "error" in init_message:
                _raise_mcp_error(init_message["error"].get("message", "Error al inicializar MCP"), init_message["error"].get("code"))

            session_headers = dict(headers)
            session_id = initialize.headers.get("mcp-session-id")
            if session_id:
                session_headers["MCP-Session-Id"] = session_id
            protocol_version = init_message.get("result", {}).get("protocolVersion")
            if protocol_version:
                session_headers["MCP-Protocol-Version"] = protocol_version
            initialized = await client.post(settings.mcp_url, headers=session_headers, json={
                "jsonrpc": "2.0", "method": "notifications/initialized",
            })
            if initialized.status_code >= 400:
                raise HTTPException(503, "Servidor MCP no disponible")

            rpc_response = await client.post(settings.mcp_url, headers=session_headers, json={
                "jsonrpc": "2.0",
                "id": str(uuid4()),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            })
            if rpc_response.status_code >= 400:
                raise HTTPException(503, "Servidor MCP no disponible")
            rpc_message = _jsonrpc_message(rpc_response)
    except httpx.HTTPError as exc:
        raise HTTPException(503, "Servidor MCP no disponible") from exc

    if "error" in rpc_message:
        error = rpc_message["error"]
        _raise_mcp_error(error.get("message", "Error de protocolo MCP"), error.get("code"))
    result = rpc_message.get("result")
    if not isinstance(result, dict):
        raise HTTPException(502, "Respuesta MCP sin resultado")
    if result.get("isError"):
        content = result.get("content", [])
        message = "Error al ejecutar herramienta MCP"
        if content and isinstance(content[0], dict):
            message = content[0].get("text", message)
        _raise_mcp_error(message)
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    content = result.get("content", [])
    if content and isinstance(content[0], dict) and isinstance(content[0].get("text"), str):
        text = content[0]["text"]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"result": text}
    return result

router = APIRouter(prefix="/api/v1")
class OpsInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    instruction: str = Field(min_length=1, max_length=4000)
    plan: OperationalPlanSpec | None = None
class DecisionInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(APPROVED|REJECTED)$")
class DevTokenInput(BaseModel):
    model_config=ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)

async def current_user(authorization: str | None = Header(default=None), session: AsyncSession=Depends(get_session)):
    settings=get_settings()
    if settings.auth_disabled:
        user=await session.scalar(select(User).where(User.username=="local-operator"))
        if user is None:
            user=User(username="local-operator", role="ADMIN"); session.add(user); await session.flush()
        return user
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Bearer requerido")
    if len(settings.jwt_secret.get_secret_value()) < 32:
        raise HTTPException(503, "JWT_SECRET debe tener al menos 32 caracteres")
    try: claims=jwt.decode(authorization[7:], settings.jwt_secret.get_secret_value(), algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError: raise HTTPException(401, "Token inválido")
    user=await session.scalar(select(User).where(User.username==claims.get("sub"), User.active.is_(True)))
    if user is None: raise HTTPException(401, "Usuario inválido")
    return user

@router.post("/dev/token")
async def dev_token(body: DevTokenInput, session: AsyncSession=Depends(get_session)):
    """Emite un JWT de desarrollo para el dashboard (solo localhost).

    Deshabilitado por defecto con ``DEV_TOKEN_ENABLED=false``. Permite elegir un
    usuario activo (por ejemplo ``operator``, ``approver`` o ``admin``) sin
    autenticación previa; es una comodidad exclusiva del entorno de desarrollo.
    """
    settings=get_settings()
    if not settings.dev_token_enabled:
        raise HTTPException(404, "El endpoint de tokens de desarrollo está deshabilitado")
    secret=settings.jwt_secret.get_secret_value()
    if len(secret) < 32:
        raise HTTPException(503, "JWT_SECRET debe tener al menos 32 caracteres")
    user=await session.scalar(select(User).where(User.username==body.username, User.active.is_(True)))
    if user is None:
        raise HTTPException(404, "Usuario no encontrado o inactivo")
    now=datetime.now(timezone.utc)
    expires_at=now+timedelta(minutes=settings.jwt_expiration_minutes)
    token=jwt.encode({"sub":user.username,"role":user.role,"iat":now,"exp":expires_at}, secret, algorithm=settings.jwt_algorithm)
    await record_event(session,"DEV_TOKEN_ISSUED",{"username":user.username,"role":user.role},actor_id=user.id)
    await session.commit()
    return {"access_token":token,"token_type":"bearer","expires_in":settings.jwt_expiration_minutes*60,"username":user.username,"role":user.role}

@router.post("/ops", status_code=202)
async def submit_ops(body: OpsInput, session: AsyncSession=Depends(get_session), user: User=Depends(current_user)):
    settings=get_settings()
    request=AgentRequest(user_id=user.id, instruction=body.instruction, status="PLANNING")
    session.add(request); await session.flush()
    await record_event(session,"REQUEST_RECEIVED",{"request_id":request.id,"instruction":body.instruction},actor_id=user.id)
    try:
        if body.plan:
            plan, trace_id = body.plan.model_dump(), None
        else:
            plan, trace_id = await plan_instruction(body.instruction)
    except Exception as exc:
        request.status="FAILED"
        await record_event(session,"PLANNING_FAILED",{"request_id":request.id,"reason":str(exc)},actor_id=user.id)
        await session.commit()
        raise HTTPException(503, str(exc))
    spec=OperationalPlanSpec.model_validate(plan)
    stored=OperationalPlan(request_id=request.id, plan=spec.model_dump(), langsmith_trace_id=trace_id)
    session.add(stored); await session.flush()
    outcomes=[]; needs_approval=False
    client=None
    for index, item in enumerate(spec.steps, 1):
        try: args=validate_tool_call(item.tool_name,item.arguments)
        except Exception as exc:
            request.status="FAILED"; await record_event(session,"PLAN_VALIDATION_FAILED",{"step":index,"error":str(exc)},actor_id=user.id); await session.commit(); raise HTTPException(422,str(exc))
        step=PlanStep(plan_id=stored.id,step_order=index,mcp_tool_name=item.tool_name,arguments=args,justification=item.justification)
        session.add(step); await session.flush()
        risk, rule=classify_risk(item.tool_name)
        session.add(RiskEvaluation(step_id=step.id,risk_level=risk,rule_triggered=rule))
        await record_event(session,"RISK_EVALUATED",{"request_id":request.id,"step_id":step.id,"risk":risk,"tool":item.tool_name},actor_id=user.id,trace_id=trace_id)
        if risk=="HIGH_SENSITIVE":
            if not settings.writes_enabled:
                step.status="REJECTED"; request.status="FAILED"
                await record_event(session,"WRITE_DISABLED",{"step_id":step.id,"tool":item.tool_name},actor_id=user.id,trace_id=trace_id)
                await session.commit(); raise HTTPException(403,"Las herramientas de escritura están deshabilitadas")
            approval=await create_approval(session,step,user.id); step.status="AWAITING_APPROVAL"; needs_approval=True
            outcomes.append({"step":index,"status":"AWAITING_APPROVAL","tool_name":item.tool_name,"approval_id":approval.id,"risk":risk})
        else:
            request.status="EXECUTING"
            if client is None: client=FineractClient(settings)
            try:
                result=await dispatch(item.tool_name,args,client)
                data=result.model_dump(mode="json",by_alias=True) if hasattr(result,"model_dump") else result
                session.add(SandboxExecution(step_id=step.id,payload=args,response=data,mcp_tool_name=item.tool_name,langsmith_trace_id=trace_id))
                step.status="COMPLETED"; outcomes.append({"step":index,"status":"COMPLETED","tool_name":item.tool_name,"result":data})
                await record_event(session,"STEP_EXECUTED",{"request_id":request.id,"step_id":step.id,"tool":item.tool_name},actor_id=user.id,trace_id=trace_id)
            except Exception as exc:
                step.status="FAILED"; request.status="FAILED"
                await record_event(session,"STEP_FAILED",{"step_id":step.id,"error":type(exc).__name__},actor_id=user.id,trace_id=trace_id)
                raise HTTPException(502,"La herramienta falló")
            finally:
                if client: await client.aclose(); client=None
    request.status="AWAITING_APPROVAL" if needs_approval else "COMPLETED"
    request.result={"steps":outcomes}
    await record_event(session,"REQUEST_AWAITING_APPROVAL" if needs_approval else "REQUEST_COMPLETED",{"request_id":request.id},actor_id=user.id,trace_id=trace_id)
    await session.commit()
    audit_id = await session.scalar(select(AuditEvent.id).where(AuditEvent.event_data["request_id"].as_integer()==request.id).order_by(AuditEvent.id.desc()).limit(1))
    return {"request_id":request.id,"status":request.status,"plan":spec.model_dump(),"trace_id":trace_id,"steps":outcomes,"auditId":audit_id}

@router.get("/approvals")
async def list_approvals(session: AsyncSession=Depends(get_session), user: User=Depends(current_user)):
    rows=(await session.execute(
        select(ApprovalRequest,PlanStep,OperationalPlan)
        .select_from(ApprovalRequest)
        .join(PlanStep, PlanStep.id==ApprovalRequest.step_id)
        .join(OperationalPlan, OperationalPlan.id==PlanStep.plan_id)
        .where(ApprovalRequest.status=="PENDING")
    )).all()
    return [{"id":a.id,"request_id":p.request_id,"step":s.step_order,"tool_name":s.mcp_tool_name,"arguments":s.arguments,"payload_hash":a.payload_hash,"expires_at":a.expires_at.isoformat()} for a,s,p in rows]

async def execute_approved(session: AsyncSession, approval: ApprovalRequest, actor: User):
    step=await session.get(PlanStep,approval.step_id)
    plan=await session.get(OperationalPlan,step.plan_id)
    request=await session.get(AgentRequest,plan.request_id) if plan else None
    settings=get_settings()
    if not settings.writes_enabled: raise HTTPException(403,"Las herramientas de escritura están deshabilitadas")
    if payload_hash(step.mcp_tool_name,step.arguments)!=approval.payload_hash: raise HTTPException(409,"Payload alterado")
    args=validate_tool_call(step.mcp_tool_name,step.arguments)
    if step.mcp_tool_name not in {"transfer_funds", "update_fraud_status"}:
        raise HTTPException(422,"No es una herramienta de escritura")
    if not settings.mcp_enabled:
        raise HTTPException(503, "Servidor MCP no disponible")
    # La tool MCP valida la aprobación desde su propia sesión; confirmar aquí
    # hace visible APPROVED antes de enviar la invocación al servidor MCP.
    await session.commit()
    tool_arguments = {**args, "approvalId": approval.id, "payloadHash": approval.payload_hash}
    result = await _invoke_mcp_tool(step.mcp_tool_name, tool_arguments, settings)
    data=result if isinstance(result,dict) else {"result":str(result)}
    session.add(SandboxExecution(step_id=step.id,payload=args,response=data,mcp_tool_name=step.mcp_tool_name))
    step.status="COMPLETED"
    if request:
        request.status="COMPLETED"
        request.result={"status":"COMPLETED","tool_name":step.mcp_tool_name,"result":data}
    await record_event(session,"SENSITIVE_STEP_EXECUTED",{"approval_id":approval.id,"step_id":step.id,"tool":step.mcp_tool_name},actor_id=actor.id)
    return {
        "request_id": request.id if request else None,
        "status": "COMPLETED",
        "plan": plan.plan if plan else None,
        "steps": [{"step":step.step_order,"status":"COMPLETED","tool_name":step.mcp_tool_name,"result":data}],
    }

@router.post("/approvals/{approval_id}/decide")
async def decide(approval_id:int, body:DecisionInput, session:AsyncSession=Depends(get_session), user:User=Depends(current_user)):
    if user.role not in {"APPROVER","ADMIN"}:
        await record_event(session,"APPROVAL_DECISION_FORBIDDEN",{"approval_id":approval_id,"role":user.role},actor_id=user.id)
        await session.commit()
        raise HTTPException(403,"Se requiere rol APPROVER")
    execution=None
    try:
        approval=await decide_approval(session,approval_id,body.decision,user.id)
        if body.decision=="APPROVED": execution=await execute_approved(session,approval,user)
    except LookupError as exc: raise HTTPException(404,str(exc))
    except TimeoutError as exc: await session.commit(); raise HTTPException(410,str(exc))
    except ValueError as exc:
        await record_event(session,"APPROVAL_DECISION_CONFLICT",{"approval_id":approval_id,"reason":str(exc)},actor_id=user.id)
        await session.commit()
        raise HTTPException(409,str(exc))
    await session.commit()
    return {"approval_id":approval.id,"status":approval.status,"execution":execution}

@router.get("/ops")
async def request_history(session:AsyncSession=Depends(get_session),user:User=Depends(current_user)):
    rows=(await session.scalars(select(AgentRequest).order_by(AgentRequest.created_at.desc()).limit(100))).all()
    plans={}
    if rows:
        stored=(await session.scalars(select(OperationalPlan).where(OperationalPlan.request_id.in_([r.id for r in rows])))).all()
        plans={plan.request_id:plan.plan for plan in stored}
    return [{"id":r.id,"instruction":r.instruction,"status":r.status,"plan":plans.get(r.id),"result":r.result,"created_at":r.created_at.isoformat()} for r in rows]
