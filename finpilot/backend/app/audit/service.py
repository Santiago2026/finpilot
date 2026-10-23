from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import AuditEvent

async def record_event(session: AsyncSession, event_type: str, event_data: dict[str, Any], *, actor_id: int | None = None, trace_id: str | None = None) -> AuditEvent:
    event = AuditEvent(event_type=event_type, event_data=event_data, actor_id=actor_id, langsmith_trace_id=trace_id)
    session.add(event)
    await session.flush()
    return event
