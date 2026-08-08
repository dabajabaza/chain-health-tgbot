import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from chain_health.config import Settings
from chain_health.db.base import Base
from chain_health.db.models import *  # noqa: F401,F403  (registers models on Base.metadata)

config = context.config
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


# Tests (and any other embedded caller) hand us a live sync Connection via
# config.attributes["connection"], bypassing async engine creation and .env
# entirely. This also means tests can run Alembic without BOT_TOKEN set, and
# without fileConfig() clobbering pytest's log capture.
injected_connection = config.attributes.get("connection")

if injected_connection is not None:
    do_run_migrations(injected_connection)
else:
    if config.config_file_name is not None:
        # disable_existing_loggers=False is required: by default fileConfig
        # permanently disables every logger created so far, and migrations run
        # inside the bot process at startup — every chain_health.* and aiogram
        # logger would go silent for the rest of the process, taking the access
        # denials, delivery failures and watchdog warnings with them.
        fileConfig(config.config_file_name, disable_existing_loggers=False)
    config.set_main_option("sqlalchemy.url", Settings().database_url)
    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
