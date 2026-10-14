"""Alembic migrations use the same settings as the application."""

from alembic import context
from app.core.config import Settings
from app.infrastructure.database import create_db_engine
from app.infrastructure.models import Base

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    settings = Settings()
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(Settings())
    try:
        with engine.connect() as connection:
            context.configure(connection=connection, target_metadata=target_metadata)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
