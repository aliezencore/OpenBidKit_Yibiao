from __future__ import annotations

import sqlite3

from app.infra.db.engine import Database
from app.infra.db.schema import SCHEMA_STATEMENTS


def apply_schema(db: Database) -> None:
    """幂等建表。沿用现有 Node 侧 sqliteDatabase.cjs 的做法。"""
    for statement in SCHEMA_STATEMENTS:
        db.execute(statement)


def table_columns(db: Database, table: str) -> set[str]:
    return {row["name"] for row in db.query(f"PRAGMA table_info({table})")}


def add_column_if_missing(db: Database, table: str, column: str, ddl: str) -> None:
    """对齐现有 Node 侧 addColumnIfMissing 的语义：已存在则空操作。"""
    if column in table_columns(db, table):
        return
    try:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    except sqlite3.OperationalError as error:
        if "duplicate column name" not in str(error).lower():
            raise
