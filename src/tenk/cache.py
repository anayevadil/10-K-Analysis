"""A small SQLite key-value cache for JSON responses.

EDGAR data changes at most a few times a day, so caching keeps the app fast
and keeps us well under SEC's rate limit.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

DEFAULT_CACHE_PATH = Path(".cache/tenk.sqlite3")


class JsonCache:
    """Stores JSON-serialisable values with a per-entry time-to-live."""

    def __init__(self, path: str | Path | None = None) -> None:
        # TENK_CACHE_PATH can point somewhere else, or be ":memory:" for tests.
        path = path or os.environ.get("TENK_CACHE_PATH") or DEFAULT_CACHE_PATH
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS cache ("
            " key TEXT PRIMARY KEY, value TEXT NOT NULL, expires_at REAL NOT NULL)"
        )
        self._conn.commit()

    def get(self, key: str) -> Any | None:
        row = self._conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        value, expires_at = row
        if expires_at < time.time():
            self._conn.execute("DELETE FROM cache WHERE key = ?", (key,))
            self._conn.commit()
            return None
        return json.loads(value)

    def set(self, key: str, value: Any, ttl_seconds: float) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, json.dumps(value), time.time() + ttl_seconds),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
