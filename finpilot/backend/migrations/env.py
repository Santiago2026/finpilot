from alembic import context
from app.db.models import Base
from app.core.config import get_settings
config = context.config
config.set_main_option("sqlalchemy.url", get_settings().database_url)
target_metadata = Base.metadata
def run_migrations_offline():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction(): context.run_migrations()
def run_migrations_online():
    from sqlalchemy import create_engine, pool
    url = config.get_main_option("sqlalchemy.url")
    # SQLite's async driver can be unreliable in restricted development
    # runtimes; Alembic itself is synchronous, so use the native SQLite driver.
    if url.startswith("sqlite+aiosqlite://"):
        url = url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
if context.is_offline_mode(): run_migrations_offline()
else: run_migrations_online()
