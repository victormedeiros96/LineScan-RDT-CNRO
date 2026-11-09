from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import URL, create_engine, make_url, text

BACKEND_DIR = Path(__file__).resolve().parents[1]


def create_database(base_url: str, prefix: str) -> tuple[str, URL]:
    url = make_url(base_url)
    dbname = f"{prefix}_{uuid.uuid4().hex[:10]}"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    admin.dispose()
    return dbname, url.set(database=dbname)


def drop_database(base_url: str, dbname: str) -> None:
    url = make_url(base_url)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
    admin.dispose()
