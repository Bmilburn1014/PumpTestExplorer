# data/cache_db.py
#
# SQLite cache manager. Stores DataFrames as SQLite tables with
# metadata tracking (source path, sync timestamp, row count).

import sqlite3
import pandas as pd
from datetime import datetime
from config import CACHE_DB_PATH


class CacheDB:
    """Local SQLite cache for index and detail DataFrames."""

    def __init__(self, db_path=None):
        self.db_path = str(db_path or CACHE_DB_PATH)
        self._ensure_metadata_table()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _ensure_metadata_table(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS _sync_metadata (
                    table_name TEXT PRIMARY KEY,
                    source_path TEXT,
                    last_synced TEXT,
                    row_count INTEGER
                )
            """)

    def store_dataframe(self, table_name: str, df: pd.DataFrame,
                        source_path: str = ""):
        """
        Store a DataFrame as a SQLite table, replacing any existing data.
        Also records sync metadata.
        """
        # Sanitize table name for SQLite
        safe_name = table_name.replace("-", "_").replace(" ", "_")

        with self._connect() as conn:
            df.to_sql(safe_name, conn, if_exists="replace", index=False)

            conn.execute("""
                INSERT OR REPLACE INTO _sync_metadata
                    (table_name, source_path, last_synced, row_count)
                VALUES (?, ?, ?, ?)
            """, (
                safe_name,
                source_path,
                datetime.now().isoformat(),
                len(df),
            ))

    def read_table(self, table_name: str,
                   filters: dict = None) -> pd.DataFrame:
        """
        Read a table from cache, optionally with SQL-level filters.

        filters: {"column_name": value} for exact match, or
                 {"column_name": (">=", value)} for operators.
        """
        safe_name = table_name.replace("-", "_").replace(" ", "_")

        with self._connect() as conn:
            if not filters:
                return pd.read_sql(f'SELECT * FROM "{safe_name}"', conn)

            where_parts = []
            params = []
            for col, val in filters.items():
                if isinstance(val, tuple) and len(val) == 2:
                    op, v = val
                    where_parts.append(f'"{col}" {op} ?')
                    params.append(v)
                else:
                    where_parts.append(f'"{col}" = ?')
                    params.append(val)

            where = " AND ".join(where_parts)
            query = f'SELECT * FROM "{safe_name}" WHERE {where}'
            return pd.read_sql(query, conn, params=params)

    def table_exists(self, table_name: str) -> bool:
        safe_name = table_name.replace("-", "_").replace(" ", "_")
        with self._connect() as conn:
            result = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (safe_name,)
            ).fetchone()
            return result is not None

    def list_cached_tables(self) -> list[dict]:
        """Return metadata for all cached tables."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM _sync_metadata ORDER BY table_name"
            ).fetchall()
            return [
                {
                    "table_name": r[0],
                    "source_path": r[1],
                    "last_synced": r[2],
                    "row_count": r[3],
                }
                for r in rows
            ]

    def drop_table(self, table_name: str):
        safe_name = table_name.replace("-", "_").replace(" ", "_")
        with self._connect() as conn:
            conn.execute(f'DROP TABLE IF EXISTS "{safe_name}"')
            conn.execute(
                "DELETE FROM _sync_metadata WHERE table_name = ?",
                (safe_name,)
            )

    def clear_all(self):
        """Drop all cached data and metadata."""
        with self._connect() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            for (name,) in tables:
                conn.execute(f'DROP TABLE IF EXISTS "{name}"')