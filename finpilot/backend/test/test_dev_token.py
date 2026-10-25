from __future__ import annotations

import httpx
import jwt
import pytest

from app.core.config import Settings
from app.db.models import AuditEvent, User
from app.db.session import get_session
from app.main import create_app


class FakeSession:
    """Sesión mínima: el endpoint solo consulta un usuario y audita el evento."""

    def __init__(self, user: User | None):
        self.user = user
        self.events: list[AuditEvent] = []

    def add(self, obj) -> None:
        if isinstance(obj, AuditEvent):
            self.events.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def scalar(self, statement):  # noqa: ANN001 - firma de SQLAlchemy
        return self.user


USER = User(id=7, username="operator", role="OPERATOR", active=True)


def _client(monkeypatch, settings: Settings, user: User | None):
    import app.api.routes as routes

    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    session = FakeSession(user)

    async def session_override():
        yield session

    application = create_app(settings)
    application.dependency_overrides[get_session] = session_override
    return application, session


async def _post(application, payload: dict) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    ) as client:
        return await client.post("/api/v1/dev/token", json=payload)


@pytest.mark.asyncio
async def test_dev_token_disabled_by_default(monkeypatch):
    settings = Settings(jwt_secret="x" * 40, dev_token_enabled=False)
    application, session = _client(monkeypatch, settings, USER)

    response = await _post(application, {"username": "operator"})

    assert response.status_code == 404
    assert not session.events


@pytest.mark.asyncio
async def test_dev_token_issues_jwt_for_active_user_and_audits(monkeypatch):
    secret = "x" * 40
    settings = Settings(jwt_secret=secret, dev_token_enabled=True, jwt_expiration_minutes=30)
    application, session = _client(monkeypatch, settings, USER)

    response = await _post(application, {"username": "operator"})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 30 * 60
    assert body["username"] == "operator"
    assert body["role"] == "OPERATOR"
    claims = jwt.decode(body["access_token"], secret, algorithms=["HS256"])
    assert claims["sub"] == "operator"
    assert claims["role"] == "OPERATOR"
    assert session.events[0].event_type == "DEV_TOKEN_ISSUED"
    assert session.events[0].event_data == {"username": "operator", "role": "OPERATOR"}


@pytest.mark.asyncio
async def test_dev_token_requires_strong_secret(monkeypatch):
    settings = Settings(jwt_secret="short", dev_token_enabled=True)
    application, _ = _client(monkeypatch, settings, USER)

    response = await _post(application, {"username": "operator"})

    assert response.status_code == 503


@pytest.mark.asyncio
async def test_dev_token_unknown_user_returns_404(monkeypatch):
    settings = Settings(jwt_secret="x" * 40, dev_token_enabled=True)
    application, session = _client(monkeypatch, settings, None)

    response = await _post(application, {"username": "desconocido"})

    assert response.status_code == 404
    assert not session.events
