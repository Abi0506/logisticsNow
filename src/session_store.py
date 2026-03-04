"""
Session Store – SQLite-backed persistence for negotiation sessions.

Enables multi-hour/day negotiations to survive process restarts.
Uses WAL journal mode for concurrent read access from the dashboard.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SessionStore:
    """Persists negotiation sessions, rounds, and raw messages in SQLite."""

    def __init__(self, db_path: str | Path = "data/negotiations.db") -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def initialize(self) -> None:
        """Open the database and create tables if needed."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        assert self._conn is not None
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id        TEXT PRIMARY KEY,
                lane_id           TEXT NOT NULL,
                lsp_id            TEXT NOT NULL,
                lsp_name          TEXT NOT NULL,
                channel_type      TEXT NOT NULL,
                persona           TEXT NOT NULL,
                target_price      REAL NOT NULL,
                budget            REAL NOT NULL,
                zopa_low          REAL NOT NULL,
                zopa_high         REAL NOT NULL,
                initial_quote     REAL NOT NULL,
                current_offer     REAL DEFAULT 0.0,
                lsp_current_price REAL DEFAULT 0.0,
                status            TEXT DEFAULT 'active',
                round_num         INTEGER DEFAULT 0,
                final_price       REAL,
                savings           REAL DEFAULT 0.0,
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS rounds (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL REFERENCES sessions(session_id),
                round_num   INTEGER NOT NULL,
                our_offer   REAL NOT NULL,
                our_message TEXT,
                lsp_price   REAL,
                lsp_message TEXT,
                sentiment   TEXT,
                accepted    INTEGER DEFAULT 0,
                timestamp   TEXT DEFAULT (datetime('now')),
                UNIQUE(session_id, round_num)
            );

            CREATE TABLE IF NOT EXISTS messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL REFERENCES sessions(session_id),
                direction    TEXT NOT NULL,
                channel_type TEXT NOT NULL,
                body         TEXT NOT NULL,
                raw_payload  TEXT,
                timestamp    TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
            CREATE INDEX IF NOT EXISTS idx_sessions_lsp ON sessions(lsp_id);
            CREATE INDEX IF NOT EXISTS idx_rounds_session ON rounds(session_id);
            CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
        """)

    # ── session CRUD ──

    def save_session(self, session_data: dict[str, Any], channel_type: str) -> None:
        """Insert or replace a session record."""
        assert self._conn is not None
        sid = session_data.get(
            "session_id",
            f"{session_data['lane_id']}_{session_data['lsp_id']}",
        )
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT OR REPLACE INTO sessions
                (session_id, lane_id, lsp_id, lsp_name, channel_type, persona,
                 target_price, budget, zopa_low, zopa_high, initial_quote,
                 current_offer, lsp_current_price, status, round_num,
                 final_price, savings, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sid,
                session_data["lane_id"],
                session_data["lsp_id"],
                session_data.get("lsp_name", ""),
                channel_type,
                session_data.get("persona", ""),
                session_data.get("target_price", 0),
                session_data.get("budget", 0),
                session_data.get("zopa_low", 0),
                session_data.get("zopa_high", 0),
                session_data.get("initial_quote", 0),
                session_data.get("current_offer", 0),
                session_data.get("lsp_current_price", 0),
                session_data.get("status", "active"),
                session_data.get("round_num", 0),
                session_data.get("final_price"),
                session_data.get("savings", 0),
                now,
            ),
        )
        self._conn.commit()

    def update_session_status(
        self,
        session_id: str,
        status: str,
        final_price: float | None = None,
        savings: float = 0.0,
    ) -> None:
        assert self._conn is not None
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE sessions SET status=?, final_price=?, savings=?, updated_at=? WHERE session_id=?",
            (status, final_price, savings, now, session_id),
        )
        self._conn.commit()

    def load_session(self, session_id: str) -> dict[str, Any] | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id=?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_active_sessions(self) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM sessions WHERE status IN ('active', 'waiting_for_reply')"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_sessions_summary(self) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT session_id, lane_id, lsp_id, lsp_name, channel_type, "
            "persona, status, round_num, initial_quote, final_price, savings, "
            "updated_at FROM sessions ORDER BY updated_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── round tracking ──

    def save_round(self, session_id: str, round_data: dict[str, Any]) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT OR REPLACE INTO rounds
                (session_id, round_num, our_offer, our_message, lsp_price,
                 lsp_message, sentiment, accepted)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                round_data["round"],
                round_data.get("our_offer", 0),
                round_data.get("our_message", ""),
                round_data.get("lsp_price"),
                round_data.get("lsp_message", ""),
                json.dumps(round_data.get("sentiment", {})),
                int(round_data.get("accepted", False)),
            ),
        )
        # Also update session round_num
        self._conn.execute(
            "UPDATE sessions SET round_num=?, updated_at=? WHERE session_id=?",
            (round_data["round"], datetime.now(timezone.utc).isoformat(), session_id),
        )
        self._conn.commit()

    def get_rounds(self, session_id: str) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM rounds WHERE session_id=? ORDER BY round_num",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── raw message audit log ──

    def log_message(
        self,
        session_id: str,
        direction: str,
        channel_type: str,
        body: str,
        raw_payload: dict[str, Any] | None = None,
    ) -> None:
        assert self._conn is not None
        self._conn.execute(
            """
            INSERT INTO messages (session_id, direction, channel_type, body, raw_payload)
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, direction, channel_type, body, json.dumps(raw_payload or {})),
        )
        self._conn.commit()

    def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE session_id=? ORDER BY timestamp",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── cleanup ──

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
