"""LangGraph checkpointer from `CHECKPOINT_URL`.

- `memory`                      tests (lost on restart)
- `sqlite:///path/to/file.db`   local development (dev_local.py)
- `postgresql://user:pw@host/db` production: tables live in their own `langgraph` schema, so they
                                  never collide with the application's Alembic-managed tables.
"""

from __future__ import annotations

from typing import Any


def make_checkpointer(url: str) -> Any:
    if not url or url == "memory":
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver()
    if url.startswith("sqlite:///"):
        import sqlite3
        from pathlib import Path

        from langgraph.checkpoint.sqlite import SqliteSaver

        path = Path(url.removeprefix("sqlite:///"))
        path.parent.mkdir(parents=True, exist_ok=True)
        saver = SqliteSaver(sqlite3.connect(str(path), check_same_thread=False))
        saver.setup()
        return saver
    if url.startswith(("postgresql://", "postgres://")):
        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        with Connection.connect(url, autocommit=True) as conn:
            conn.execute("CREATE SCHEMA IF NOT EXISTS langgraph")
        # A small pool with health checks survives database restarts; every connection is pinned
        # to the `langgraph` schema.
        pool = ConnectionPool(
            url,
            min_size=1,
            max_size=4,
            check=ConnectionPool.check_connection,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": dict_row,
                "options": "-c search_path=langgraph",
            },
            open=True,
        )
        saver = PostgresSaver(pool)  # type: ignore[arg-type]
        saver.setup()
        return saver
    raise ValueError("CHECKPOINT_URL must be 'memory', 'sqlite:///...' or 'postgresql://...'")
