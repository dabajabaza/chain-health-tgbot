from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from chain_health.db.base import Base
from chain_health.db.models import *  # noqa: F401,F403  (register tables on Base.metadata)
from tests.helpers.schema import apply_migrations


def test_migrated_schema_matches_the_orm_models(tmp_path):
    """The one test that would have caught the models/migrations drift from
    commit 3ca9e24: compares the schema Alembic actually produces against
    what Base.metadata declares. An empty diff means every migration and
    every model stayed in sync.
    """
    db_path = tmp_path / "schema.db"
    apply_migrations(f"sqlite:///{db_path}")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert diff == []


def test_rotation_activated_dt_is_not_nullable(tmp_path):
    db_path = tmp_path / "schema.db"
    apply_migrations(f"sqlite:///{db_path}")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("rotations")}
    finally:
        engine.dispose()

    assert columns["activated_dt"]["nullable"] is False


def test_downgrade_to_base_and_back_upgrade_is_clean(tmp_path):
    """Migrations run at startup (docs/ARCHITECTURE.md D2), so the downgrade
    path is production code too, not just a rollback escape hatch.
    """
    from alembic import command
    from alembic.config import Config as AlembicConfig

    from tests.helpers.schema import PROJECT_ROOT

    db_path = tmp_path / "schema.db"
    apply_migrations(f"sqlite:///{db_path}")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
            cfg.attributes["connection"] = connection
            command.downgrade(cfg, "base")
        with engine.connect() as connection:
            tables = inspect(connection).get_table_names()
            assert tables == ["alembic_version"]
        with engine.begin() as connection:
            cfg = AlembicConfig(str(PROJECT_ROOT / "alembic.ini"))
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    assert diff == []
