import os
import tomllib

from sqlalchemy import create_engine


def parse_config() -> dict:
    with open("settings.toml", "rb") as f:
        config = tomllib.load(f)
    return config


def create_sqlite_db_if_it_doesnt_exist(db_url: str) -> None:
    engine = create_engine(db_url, echo=True)
    with engine.connect():
        ...


def get_db_connection_string() -> str | NotImplementedError:
    config = parse_config()
    database_section = config.get("database", {})
    provider = os.getenv("CHAIN_HEALTH_BOT_DB_PROVIDER", database_section.get("provider"))
    host = os.getenv("CHAIN_HEALTH_BOT_DB_HOST", database_section.get("host"))
    port = os.getenv("CHAIN_HEALTH_BOT_DB_PORT", database_section.get("port"))
    db_name = os.getenv("CHAIN_HEALTH_BOT_DB_NAME", database_section.get("db_name"))
    username = os.getenv("CHAIN_HEALTH_BOT_DB_USERNAME", database_section.get("username"))
    password = os.getenv("CHAIN_HEALTH_BOT_DB_PASSWORD", database_section.get("password"))
    data_path = os.getenv("CHAIN_HEALTH_BOT_DB_DATA_PATH", config.get("paths", {}).get("data_path"))
    if provider == "sqlite":
        filename = os.path.join(data_path, db_name + ".db")
        db_url = f"{provider}:///{filename}"
        create_sqlite_db_if_it_doesnt_exist(db_url)
        return db_url
    elif provider.startswith("postgresql"):
        return f"{provider}://{username}:{password}@{host}:{port}/{db_name}"
    raise NotImplementedError(f"Database provider <{provider}> not supported")
