import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

import shared.models  # noqa: F401  (registers the tables)
from shared.config import get_settings
from shared.db import Base

target_metadata = Base.metadata
url = get_settings().database_url


def do_run(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    engine = create_async_engine(url)
    async with engine.connect() as conn:
        await conn.run_sync(do_run)
    await engine.dispose()


if context.is_offline_mode():
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())