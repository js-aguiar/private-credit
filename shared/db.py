"""Database engine/session management.

Credentials come either from AWS Secrets Manager (``DB_SECRET_ARN``) or from direct
environment variables (local development). Connections require TLS by default.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import ScraperConfig
from .logging_config import get_logger
from .models import Base

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None

logger = get_logger(__name__)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _resolve_credentials(config: ScraperConfig) -> dict:
    """Return connection parameters, preferring Secrets Manager when configured."""
    if config.db_secret_arn:
        if boto3 is None:  # pragma: no cover
            raise RuntimeError("boto3 is required to read DB_SECRET_ARN")
        client = boto3.client("secretsmanager", region_name=config.aws_region)
        secret = json.loads(client.get_secret_value(SecretId=config.db_secret_arn)["SecretString"])
        return {
            "host": secret.get("host") or config.db_host,
            "port": int(secret.get("port") or config.db_port),
            "dbname": secret.get("dbname") or secret.get("dbInstanceIdentifier") or config.db_name,
            "user": secret["username"],
            "password": secret["password"],
        }
    missing = [
        name
        for name, value in {
            "DB_HOST": config.db_host,
            "DB_USER": config.db_user,
            "DB_PASSWORD": config.db_password,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing DB configuration: {', '.join(missing)}")
    return {
        "host": config.db_host,
        "port": config.db_port,
        "dbname": config.db_name,
        "user": config.db_user,
        "password": config.db_password,
    }


def build_engine(config: ScraperConfig) -> Engine:
    creds = _resolve_credentials(config)
    url = (
        f"postgresql+psycopg2://{creds['user']}:{creds['password']}"
        f"@{creds['host']}:{creds['port']}/{creds['dbname']}"
    )
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        future=True,
        connect_args={"sslmode": config.db_sslmode, "connect_timeout": 15},
    )


# Cache engines per config instance (ScraperConfig is a mutable dataclass, so it is not
# hashable and cannot be used with functools.lru_cache).
_ENGINE_CACHE: dict[int, Engine] = {}


def get_engine(config: ScraperConfig) -> Engine:
    key = id(config)
    engine = _ENGINE_CACHE.get(key)
    if engine is None:
        engine = build_engine(config)
        _ENGINE_CACHE[key] = engine
    return engine


def get_session_factory(config: ScraperConfig) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(config), expire_on_commit=False, future=True)


@contextmanager
def session_scope(config: ScraperConfig) -> Iterator[Session]:
    """Provide a transactional session that commits on success and rolls back on error."""
    factory = get_session_factory(config)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ensure_schema(config: ScraperConfig) -> None:
    """Create all tables/indexes if they do not exist (uses schema.sql when present)."""
    engine = get_engine(config)
    if _SCHEMA_PATH.exists():
        ddl = _SCHEMA_PATH.read_text(encoding="utf-8")
        with engine.begin() as conn:
            for statement in _split_sql(ddl):
                conn.execute(text(statement))
    else:  # pragma: no cover - fallback to ORM metadata.
        Base.metadata.create_all(engine)
    logger.info("schema_ensured", extra={"source": config.source_name})


def _split_sql(ddl: str) -> list[str]:
    statements = []
    for chunk in ddl.split(";"):
        cleaned = "\n".join(
            line for line in chunk.splitlines() if not line.strip().startswith("--")
        ).strip()
        if cleaned:
            statements.append(cleaned)
    return statements
