"""Emite un JWT local para una cuenta ya creada; destinado a demos/desarrollo."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime, timedelta, timezone
import jwt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.db.models import User

def main():
    if len(sys.argv) != 2:
        raise SystemExit("Uso: python scripts/issue_token.py <username>")
    settings = get_settings()
    secret = settings.jwt_secret.get_secret_value()
    if len(secret) < 32:
        raise SystemExit("Configure JWT_SECRET (mínimo 32 caracteres)")
    url = settings.database_url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    engine = create_engine(url)
    with Session(engine) as session:
        user = session.scalar(select(User).where(User.username == sys.argv[1], User.active.is_(True)))
        if user is None:
            raise SystemExit("Usuario activo no encontrado")
        now = datetime.now(timezone.utc)
        token = jwt.encode({"sub": user.username, "role": user.role, "iat": now, "exp": now + timedelta(minutes=settings.jwt_expiration_minutes)}, secret, algorithm=settings.jwt_algorithm)
        print(token)
    engine.dispose()
if __name__ == "__main__": main()
