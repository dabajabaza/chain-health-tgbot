from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def apply_migrations(sync_url: str) -> None:
    """Run `alembic upgrade head` against a sync SQLite URL.

    Alembic's own migration runner is sync (`command.upgrade` ultimately calls
    `asyncio.run(...)` inside migrations/env.py's online path), which cannot
    be invoked from within a running event loop. Handing env.py a live sync
    Connection via `config.attributes["connection"]` sidesteps that entirely
    and lets tests run the real migration chain instead of `create_all`.
    """
    engine = create_engine(sync_url)
    try:
        with engine.begin() as connection:
            cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
    finally:
        engine.dispose()
