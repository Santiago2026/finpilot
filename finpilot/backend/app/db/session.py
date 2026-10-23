from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import get_settings

engine = create_async_engine(get_settings().database_url, future=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
async def init_db():
    from .models import Base
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
async def get_session():
    async with SessionLocal() as session:
        yield session
