"""SQLite persistence for sessions, the event log and utterances.

The event log is append-only. Every piece of session state (records, applications, evidence)
is a fold over it, so replaying a stored log reproduces the state that was graded.
"""

import json
import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue

from app.engine.events import Event, EventType, Utterance

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    at TEXT NOT NULL,
    t_ms INTEGER NOT NULL,
    utt_id TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
CREATE TABLE IF NOT EXISTS utterances (
    session_id TEXT NOT NULL,
    utt_id TEXT NOT NULL,
    client_utt_id TEXT NOT NULL,
    data TEXT NOT NULL,
    PRIMARY KEY (session_id, utt_id),
    UNIQUE (session_id, client_utt_id)
);
"""


class SessionStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    ENDED = "ended"


class SessionRow(BaseModel):
    """Everything a session pins. Persona rolls (mood, voice) are customer state and never go
    to the learner."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    scenario_id: str
    scenario_version: int
    catalog_version: str
    ruleset_version: str
    rubric_id: str
    rubric_version: int
    frame_id: str
    prompt_version: str | None = None
    """Customer prompt template the session's customer plays from."""
    actor: str | None = None
    """The model playing the customer, e.g. ``scripted`` or ``gemini:<model>``."""
    seed: int
    mood: str
    voice: str
    status: SessionStatus
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None


class Store:
    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self._depth = 0
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Serializes writers in this process and makes a multi-row write atomic. Nested calls
        join the outer transaction, so a read-check-write sequence can wrap smaller writes."""
        with self._lock:
            if self._depth:
                self._depth += 1
                try:
                    yield self._conn
                finally:
                    self._depth -= 1
                return
            self._conn.execute("BEGIN IMMEDIATE")
            self._depth = 1
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
            finally:
                self._depth = 0

    # Sessions

    def put_session(self, row: SessionRow) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO sessions (session_id, data) VALUES (?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET data = excluded.data",
                (row.session_id, row.model_dump_json()),
            )

    def get_session(self, session_id: str) -> SessionRow | None:
        with self._lock:
            found = self._conn.execute(
                "SELECT data FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()
        return None if found is None else SessionRow.model_validate_json(found[0])

    # Events

    def append_event(
        self,
        session_id: str,
        type: EventType,
        *,
        at: datetime,
        t_ms: int,
        payload: dict[str, JsonValue] | None = None,
        utt_id: str | None = None,
    ) -> Event:
        with self.transaction() as conn:
            return self._append_event(conn, session_id, type, at, t_ms, payload or {}, utt_id)

    def _append_event(
        self,
        conn: sqlite3.Connection,
        session_id: str,
        type: EventType,
        at: datetime,
        t_ms: int,
        payload: dict[str, JsonValue],
        utt_id: str | None,
    ) -> Event:
        (last,) = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM events WHERE session_id = ?", (session_id,)
        ).fetchone()
        event = Event(
            session_id=session_id,
            seq=last + 1,
            type=type,
            at=at,
            t_ms=t_ms,
            utt_id=utt_id,
            payload=payload,
        )
        conn.execute(
            "INSERT INTO events (session_id, seq, type, at, t_ms, utt_id, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                event.seq,
                event.type.value,
                event.at.isoformat(),
                event.t_ms,
                utt_id,
                json.dumps(payload, sort_keys=True),
            ),
        )
        return event

    def last_event_type(self, session_id: str, types: Iterable[EventType]) -> EventType | None:
        """The type of the latest event among ``types``, without reading the log."""
        wanted = [t.value for t in types]
        with self._lock:
            row = self._conn.execute(
                "SELECT type FROM events WHERE session_id = ? "
                f"AND type IN ({', '.join('?' * len(wanted))}) ORDER BY seq DESC LIMIT 1",
                (session_id, *wanted),
            ).fetchone()
        return EventType(row[0]) if row else None

    def events(self, session_id: str) -> list[Event]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT seq, type, at, t_ms, utt_id, payload FROM events "
                "WHERE session_id = ? ORDER BY seq",
                (session_id,),
            ).fetchall()
        return [
            Event(
                session_id=session_id,
                seq=seq,
                type=EventType(type_),
                at=datetime.fromisoformat(at),
                t_ms=t_ms,
                utt_id=utt_id,
                payload=json.loads(payload),
            )
            for seq, type_, at, t_ms, utt_id, payload in rows
        ]

    # Utterances

    def append_utterance(
        self,
        session_id: str,
        *,
        client_utt_id: str,
        at: datetime,
        build: Callable[[str], Utterance],
    ) -> tuple[Utterance, bool]:
        """Stores an utterance and its log event in one transaction. ``build`` receives the next
        ``utt_id``. Returns the stored utterance and whether it is new; a retried
        ``client_utt_id`` returns the original instead of a duplicate."""
        with self.transaction() as conn:
            found = conn.execute(
                "SELECT data FROM utterances WHERE session_id = ? AND client_utt_id = ?",
                (session_id, client_utt_id),
            ).fetchone()
            if found is not None:
                return Utterance.model_validate_json(found[0]), False
            (count,) = conn.execute(
                "SELECT COUNT(*) FROM utterances WHERE session_id = ?", (session_id,)
            ).fetchone()
            utterance = build(f"u{count + 1:04d}")
            conn.execute(
                "INSERT INTO utterances (session_id, utt_id, client_utt_id, data) "
                "VALUES (?, ?, ?, ?)",
                (session_id, utterance.utt_id, client_utt_id, utterance.model_dump_json()),
            )
            self._append_event(
                conn,
                session_id,
                EventType.UTTERANCE,
                at,
                utterance.t_ms,
                {"speaker": utterance.speaker.value, "channel": utterance.channel.value},
                utterance.utt_id,
            )
            return utterance, True

    def utterance_by_client_id(self, session_id: str, client_utt_id: str) -> Utterance | None:
        with self._lock:
            found = self._conn.execute(
                "SELECT data FROM utterances WHERE session_id = ? AND client_utt_id = ?",
                (session_id, client_utt_id),
            ).fetchone()
        return None if found is None else Utterance.model_validate_json(found[0])

    def utterances(self, session_id: str) -> list[Utterance]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM utterances WHERE session_id = ? ORDER BY rowid", (session_id,)
            ).fetchall()
        return [Utterance.model_validate_json(r[0]) for r in rows]


def dump_payload(model: BaseModel) -> dict[str, Any]:
    """A model as JSON-safe values for an event payload."""
    data: dict[str, Any] = model.model_dump(mode="json")
    return data
