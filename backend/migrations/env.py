"""Alembic entry point: runs migrations against DATABASE_URL."""
from alembic import context
from sqlalchemy import create_engine, pool

from app import config as app_config

connectable = create_engine(app_config.DATABASE_URL, poolclass=pool.NullPool)
with connectable.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
