"""Session/artifact store (frontend/design.md §4.2): one SQLite file, three relations.

- artifacts: immutable, content-addressed (sha256 over kind + NUL + raw payload), shared
  across sessions; `raw_payload` keeps the EXACT bytes (audit receipts must round-trip).
- session_artifacts: append-only ledger (session_id, sequence).
- sessions: heads (kind -> artifact hash) + revision in ONE row — optimistic locking is a
  single-row compare-and-swap; a stale writer gets a Conflict with the current state.
- session_flags: the reader's own bookkeeping (a display title, archived). It never touches the
  ledger or the revision, so it cannot invalidate an agent's or the panel's CAS.

Listeners registered via `subscribe` receive every committed artifact append (the SSE feed).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

Event = dict
Listener = Callable[[Event], None]


class Conflict(Exception):
    def __init__(self, session_id: str, expected: int, actual: int):
        super().__init__(f"session {session_id}: expected revision {expected}, actual {actual}")
        self.session_id, self.expected, self.actual = session_id, expected, actual


class NotFound(Exception):
    pass


_SCHEMA = """
create table if not exists artifacts(
  hash text primary key,
  kind text not null,
  raw_payload blob not null,
  created_at text not null
);
create table if not exists session_artifacts(
  session_id text not null,
  sequence integer not null,
  kind text not null,
  artifact_hash text not null references artifacts(hash),
  created_at text not null,
  primary key(session_id, sequence)
);
create table if not exists sessions(
  id text primary key,
  created_at text not null,
  revision integer not null default 0,
  heads_json text not null default '{}',
  meta_json text not null default '{}'
);
create table if not exists session_flags(
  session_id text primary key,
  title text,
  archived_at text
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def artifact_hash(kind: str, payload: bytes) -> str:
    return hashlib.sha256(kind.encode("utf-8") + b"\0" + payload).hexdigest()


class Store:
    def __init__(self, path: Path | str):
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("pragma journal_mode=wal")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.Lock()
        self._listeners: list[Listener] = []

    # -- sessions ------------------------------------------------------------------

    def create_session(self, meta: dict | None = None) -> dict:
        if meta is not None and not isinstance(meta, dict):
            raise ValueError("meta must be an object")
        sid = uuid.uuid4().hex[:12]
        with self._lock, self._conn:
            self._conn.execute(
                "insert into sessions(id, created_at, meta_json) values(?,?,?)",
                (sid, _now(), json.dumps(meta or {}, ensure_ascii=False)))
        return self.get_session(sid)

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> dict:
        # Legacy malformed metadata must not poison every session list. Preserve stored bytes.
        meta = json.loads(row["meta_json"])
        return {"id": row["id"], "createdAt": row["created_at"], "revision": row["revision"],
                "heads": json.loads(row["heads_json"]), "meta": meta if isinstance(meta, dict) else {}}

    def get_session(self, session_id: str) -> dict:
        # Reads take the lock too: the connection is shared (check_same_thread=False), so an
        # unsynchronized read on another thread could observe a writer's mid-transaction state.
        with self._lock:
            row = self._conn.execute(
                "select * from sessions where id=?", (session_id,)).fetchone()
        if row is None:
            raise NotFound(session_id)
        return self._row_to_session(row)

    def list_sessions(self) -> list[dict]:
        # One query builds every session (previously a select-ids then per-row get_session
        # N+1, also re-probed on every CLI liveness check).
        with self._lock:
            rows = self._conn.execute(
                "select * from sessions order by created_at desc").fetchall()
        return [self._row_to_session(r) for r in rows]

    # -- artifacts -----------------------------------------------------------------

    def list_session_views(self, limit: int | None = None, prioritize_decisions: bool = False,
                           archived: str = "exclude") -> list[dict]:
        """`archived`: exclude (default), include, or only the archived sessions."""
        from .session_views import NEEDS_READER, SESSION_VIEW_SQL, session_view
        with self._lock:
            rows = self._conn.execute(SESSION_VIEW_SQL).fetchall()
        views = [session_view(row) for row in rows]
        if archived != "include":
            views = [row for row in views if row["archived"] == (archived == "only")]
        views.sort(key=lambda row: (prioritize_decisions and row["state"] in NEEDS_READER,
                                    row["updatedAt"], row["createdAt"], row["id"]), reverse=True)
        return views if limit is None else views[:limit]

    def get_session_view(self, session_id: str) -> dict:
        from .session_views import SESSION_VIEW_SQL, session_view
        with self._lock:
            row = self._conn.execute(SESSION_VIEW_SQL + " where s.id=?", (session_id,)).fetchone()
            if row is None:
                raise NotFound(session_id)
            entries = self._conn.execute(
                "select sequence, kind, artifact_hash, created_at from session_artifacts "
                "where session_id=? order by sequence", (session_id,)).fetchall()
        out = session_view(row)
        out["ledger"] = [{"sequence": e["sequence"], "kind": e["kind"],
                          "artifactHash": e["artifact_hash"], "createdAt": e["created_at"]}
                         for e in entries]
        return out

    def append_artifact(self, session_id: str, kind: str, payload: bytes,
                        expected_revision: int) -> dict:
        return self.append_artifacts(session_id, [(kind, payload)], expected_revision)[0]

    def append_artifacts(self, session_id: str, artifacts: list[tuple[str, bytes]],
                         expected_revision: int, *, deduplicate: bool = False) -> list[dict]:
        """Commit a gate's inputs and outputs together against the revision it actually read.

        Content is reusable, but ledger order is evidence: only an identical *suffix* is a rerun.
        After an intervening draft/decision the same bytes must be recorded as a new application.
        """
        pairs = [(kind, artifact_hash(kind, payload), payload) for kind, payload in artifacts]
        events = []
        with self._lock, self._conn:
            row = self._conn.execute(
                "select revision, heads_json from sessions where id=?", (session_id,)).fetchone()
            if row is None:
                raise NotFound(session_id)
            if row["revision"] != expected_revision:
                raise Conflict(session_id, expected_revision, row["revision"])
            if not pairs:
                return []
            if deduplicate:
                tail = self._conn.execute(
                    "select kind, artifact_hash from session_artifacts where session_id=? "
                    "order by sequence desc limit ?", (session_id, len(pairs))).fetchall()
                if [(r["kind"], r["artifact_hash"]) for r in reversed(tail)] == [(k, h) for k, h, _ in pairs]:
                    return []
            seq = self._conn.execute(
                "select coalesce(max(sequence), -1) + 1 from session_artifacts "
                "where session_id=?", (session_id,)).fetchone()[0]
            heads = json.loads(row["heads_json"])
            new_rev = row["revision"]
            for kind, h, payload in pairs:
                self._conn.execute(
                    "insert or ignore into artifacts(hash, kind, raw_payload, created_at) "
                    "values(?,?,?,?)", (h, kind, payload, _now()))
                self._conn.execute(
                    "insert into session_artifacts(session_id, sequence, kind, artifact_hash, "
                    "created_at) values(?,?,?,?,?)", (session_id, seq, kind, h, _now()))
                heads[kind] = h
                new_rev += 1
                events.append({"sessionId": session_id, "kind": kind, "artifactHash": h,
                               "revision": new_rev, "sequence": seq})
                seq += 1
            self._conn.execute("update sessions set heads_json=?, revision=? where id=?",
                               (json.dumps(heads, ensure_ascii=False), new_rev, session_id))
        for event in events:
            for fn in list(self._listeners):
                fn(event)
        return events

    def artifact_pair(self, session_id: str, output_kind: str, input_kind: str) -> dict | None:
        """Latest output and the input preceding that ledger application, in one consistent read."""
        with self._lock:
            session = self._conn.execute("select meta_json from sessions where id=?", (session_id,)).fetchone()
            if session is None:
                raise NotFound(session_id)
            output = self._conn.execute(
                "select l.sequence, a.* from session_artifacts l join artifacts a on a.hash=l.artifact_hash "
                "where l.session_id=? and l.kind=? order by l.sequence desc limit 1",
                (session_id, output_kind)).fetchone()
            if output is None:
                return None
            source = self._conn.execute(
                "select a.* from session_artifacts l join artifacts a on a.hash=l.artifact_hash "
                "where l.session_id=? and l.kind=? and l.sequence<? order by l.sequence desc limit 1",
                (session_id, input_kind, output["sequence"])).fetchone()
        return {"output": dict(output), "input": dict(source) if source else None,
                "meta": json.loads(session["meta_json"])}

    def session_overview(self, team_id: str | None = None, limit: int = 4) -> dict:
        """Bounded home cards and exact counts, without loading every attached team snapshot."""
        from .session_views import NEEDS_READER, SESSION_SUMMARY_SQL, SESSION_VIEW_SQL, session_view
        with self._lock:
            rows = self._conn.execute(SESSION_SUMMARY_SQL).fetchall()
            views = [(session_view(row), row["team_ref"]) for row in rows if row["archived_at"] is None]
            views.sort(key=lambda pair: (pair[0]["state"] in NEEDS_READER,
                                        pair[0]["updatedAt"], pair[0]["createdAt"], pair[0]["id"]), reverse=True)
            ids = [view["id"] for view, _ in views[:limit]]
            full = self._conn.execute(SESSION_VIEW_SQL + " where s.id in (" + ",".join("?" for _ in ids) + ")",
                                      ids).fetchall() if ids else []
        cards = {row["id"]: session_view(row) for row in full}
        return {"sessions": [cards[sid] for sid in ids], "totalCount": len(views),
                "attentionCount": sum(view["state"] in NEEDS_READER for view, _ in views),
                "relatedCount": sum(ref == team_id for _, ref in views) if team_id else 0}

    # -- the reader's bookkeeping ------------------------------------------------------

    def _flag(self, session_id: str, column: str, value: str | None) -> None:
        with self._lock, self._conn:
            if self._conn.execute("select 1 from sessions where id=?", (session_id,)).fetchone() is None:
                raise NotFound(session_id)
            self._conn.execute("insert or ignore into session_flags(session_id) values(?)", (session_id,))
            self._conn.execute(f"update session_flags set {column}=? where session_id=?", (value, session_id))

    def set_title(self, session_id: str, title: str | None) -> None:
        self._flag(session_id, "title", (title or "").strip() or None)

    def set_archived(self, session_id: str, archived: bool) -> None:
        self._flag(session_id, "archived_at", _now() if archived else None)

    def delete_session(self, session_id: str) -> None:
        """Remove a session and its ledger; artifacts no other session references go with it."""
        with self._lock, self._conn:
            if self._conn.execute("delete from sessions where id=?", (session_id,)).rowcount == 0:
                raise NotFound(session_id)
            self._conn.execute("delete from session_artifacts where session_id=?", (session_id,))
            self._conn.execute("delete from session_flags where session_id=?", (session_id,))
            self._conn.execute("delete from artifacts where hash not in "
                               "(select artifact_hash from session_artifacts)")

    def get_artifact(self, h: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "select * from artifacts where hash=?", (h,)).fetchone()
        if row is None:
            raise NotFound(h)
        return {"hash": row["hash"], "kind": row["kind"], "createdAt": row["created_at"],
                "payload": bytes(row["raw_payload"])}

    def session_ledger(self, session_id: str) -> list[dict]:
        # camelCase like every other store output (sessions, append events) — this crosses
        # the wire as LedgerEntryDto, not as a raw sqlite row.
        with self._lock:
            rows = self._conn.execute(
                "select sequence, kind, artifact_hash, created_at from session_artifacts "
                "where session_id=? order by sequence", (session_id,)).fetchall()
        return [{"sequence": r["sequence"], "kind": r["kind"],
                 "artifactHash": r["artifact_hash"], "createdAt": r["created_at"]}
                for r in rows]

    def changes_since_revision(self, session_id: str, revision: int) -> list[dict]:
        """Committed ledger entries after a caller's revision baseline.

        Revision increments once per append and sequence starts at zero, so a writer that read
        revision N has seen sequences 0..N-1. This is conflict evidence only: the opaque store never
        decides whether different artifact kinds are semantically mergeable.
        """
        with self._lock:
            rows = self._conn.execute(
                "select sequence, kind, artifact_hash, created_at from session_artifacts "
                "where session_id=? and sequence>=? order by sequence",
                (session_id, max(0, revision))).fetchall()
        return [{"sequence": r["sequence"], "kind": r["kind"],
                 "artifactHash": r["artifact_hash"], "createdAt": r["created_at"]}
                for r in rows]

    # -- events --------------------------------------------------------------------

    def subscribe(self, fn: Listener) -> Callable[[], None]:
        self._listeners.append(fn)
        return lambda: self._listeners.remove(fn)

    def close(self) -> None:
        self._conn.close()

    def backup(self, path: Path) -> None:
        with self._lock:
            destination = sqlite3.connect(str(path))
            try:
                with destination:
                    self._conn.backup(destination)
            finally:
                destination.close()
