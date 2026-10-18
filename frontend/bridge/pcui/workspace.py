"""Local user workspace: atomically replaced JSON, CAS library writes, scoped file names."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

from .store import Store

DEFAULT_DIRECTORY = "storage"


class WorkspaceError(Exception):
    def __init__(self, code: str, status: int = 409):
        self.code, self.status = code, status
        super().__init__(code)


def project_directory() -> Path:
    current = Path.cwd().resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def default_workspace() -> Path:
    """`./storage` for a source checkout. A release bundle is replaced on update, so its launcher
    points PCUI_DEFAULT_WORKSPACE outside the unpacked folder."""
    configured = os.environ.get("PCUI_DEFAULT_WORKSPACE")
    return Path(configured).expanduser() if configured else Path.cwd() / DEFAULT_DIRECTORY


def configured_workspace(value: str) -> Path:
    """A remembered workspace must still exist: silently recreating a moved or deleted folder would
    open an empty library where the reader expects theirs."""
    path = workspace_path(value)
    if not path.is_dir():
        raise WorkspaceError("workspace_missing", 404)
    return path


def workspace_path(value: str | Path) -> Path:
    if not str(value) or any(char in str(value) for char in ('\n', '\r', '\0', '"')):
        raise WorkspaceError("bad_input", 400)
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise WorkspaceError("absolute_path_required", 400)
    return path.resolve()


def atomic_json(path: Path, document: dict) -> None:
    payload = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8") + b"\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def save_workspace_default(config_path: Path, path: Path) -> None:
    """Change one setting without changing the values of other settings."""
    source = config_path.read_text(encoding="utf-8-sig") if config_path.exists() else ""
    lines = [line for line in source.splitlines() if line.partition("=")[0].strip() != "PCUI_WORKSPACE_DIR"]
    lines.append(f'PCUI_WORKSPACE_DIR="{path.as_posix()}"')
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pc-env-", dir=config_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(lines) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, config_path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Workspace:
    def __init__(self, path: Path, config_path: Path | None = None, project: Path | None = None):
        self.path = workspace_path(path)
        self.project = project or project_directory()
        self.path.mkdir(parents=True, exist_ok=True)
        self.config_path = config_path
        self.lock = threading.RLock()
        # One local bridge owns a workspace at a time; all windows use that bridge.
        self._lease = (self.path / ".bridge.lock").open("a+b")
        try:
            self._lease.seek(0)
            if self._lease.read(1) == b"":
                self._lease.write(b"0"); self._lease.flush()
            self._lease.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lease.close()
            raise WorkspaceError("workspace_in_use") from exc

    def close(self):
        self._lease.close()

    @property
    def identity(self) -> str:
        return hashlib.sha256(os.path.normcase(str(self.path)).encode()).hexdigest()[:24]

    def info(self) -> dict:
        return {"path": str(self.path), "workspaceId": self.identity,
                "libraryPath": str(self.path / "library.json"),
                "recordsPath": str(self.path / "records.json"), "sessionsPath": str(self.path / "sessions.db")}

    def _read(self, name: str, empty: dict) -> dict:
        path = self.path / name
        if not path.exists():
            return empty
        try:
            doc = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(doc, dict) or doc.get("version") != 1:
                raise ValueError("unsupported document")
            return doc
        except (ValueError, UnicodeError, OSError) as exc:
            raise WorkspaceError("workspace_file_unreadable", 422) from exc

    def library(self) -> dict:
        with self.lock:
            doc = self._read("library.json", {"version": 1, "teams": [], "box": [], "layout": None})
            if not isinstance(doc.get("teams"), list) or not isinstance(doc.get("box"), list) or "layout" not in doc:
                raise WorkspaceError("workspace_file_unreadable", 422)
            revision = hashlib.sha256(json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            return {"workspaceId": self.identity, "revision": revision,
                    "teams": doc["teams"], "box": doc["box"], "layout": doc["layout"]}

    def check(self, identity):
        if identity != self.identity:
            raise WorkspaceError("workspace_changed")

    def put_library(self, body: dict) -> dict:
        with self.lock:
            self.check(body.get("workspaceId"))
            current = self.library()
            if body.get("revision") != current["revision"]:
                raise WorkspaceError("library_conflict")
            if not isinstance(body.get("teams"), list) or not isinstance(body.get("box"), list) or "layout" not in body:
                raise WorkspaceError("bad_input", 400)
            # Preserve unknown envelope properties and rows for recovery/future app versions.
            doc = self._read("library.json", {"version": 1})
            doc.update({key: body[key] for key in ("teams", "box", "layout")})
            atomic_json(self.path / "library.json", doc)
            return self.library()

    def records(self) -> list:
        doc = self._read("records.json", {"version": 1, "records": []})
        if not isinstance(doc.get("records"), list):
            raise WorkspaceError("workspace_file_unreadable", 422)
        return doc["records"]

    def edit_records(self, identity: str, record: dict | None = None, ids: list | None = None):
        with self.lock:
            self.check(identity)
            doc = self._read("records.json", {"version": 1, "records": []})
            records = self.records()
            if record is not None:
                if not isinstance(record, dict) or not isinstance(record.get("id"), str):
                    raise WorkspaceError("bad_input", 400)
                previous = next((r for r in records if isinstance(r, dict) and r.get("id") == record["id"]), None)
                # Reject delayed heartbeat writes that would undo a finished report.
                if previous:
                    previous_time, incoming_time = previous.get("updatedAt", 0), record.get("updatedAt", 0)
                    if not isinstance(incoming_time, (int, float)) or not isinstance(previous_time, (int, float)):
                        raise WorkspaceError("bad_input", 400)
                    if previous_time > incoming_time or (previous_time == incoming_time and previous.get("status") != "running" and record.get("status") == "running"):
                        return
                records = [r for r in records if not isinstance(r, dict) or r.get("id") != record["id"]] + [record]
            if ids:
                records = [r for r in records if not isinstance(r, dict) or r.get("id") not in ids]
            doc["records"] = records
            atomic_json(self.path / "records.json", doc)

    def import_browser(self, body: dict):
        """Copy old browser documents into an empty library, preserving the browser copy."""
        with self.lock:
            self.check(body.get("workspaceId"))
            current = self.library()
            if current["revision"] != body.get("revision"):
                raise WorkspaceError("library_conflict")
            if current["teams"] or current["box"] or self.records():
                raise WorkspaceError("target_not_empty")
            if not isinstance(body.get("records"), list):
                raise WorkspaceError("bad_input", 400)
            library_path = self.path / "library.json"
            previous = self._read("library.json", {"version": 1}) if library_path.exists() else None
            self.put_library(body)
            try:
                atomic_json(self.path / "records.json", {"version": 1, "records": body["records"]})
            except Exception:
                if previous is None:
                    library_path.unlink(missing_ok=True)
                else:
                    atomic_json(library_path, previous)
                raise

    def switch(self, path: str, migrate: bool, store: Store) -> tuple[Workspace, Store]:
        target = workspace_path(path)
        if target == self.path:
            return self, store
        next_workspace = Workspace(target, self.config_path, self.project)
        created: list[Path] = []
        try:
            next_workspace.library(); next_workspace.records()
            files = ("library.json", "records.json", "sessions.db")
            if migrate and any((target / name).exists() for name in files):
                raise WorkspaceError("target_not_empty")
            if migrate:
                # Read and validate before any copy; the source remains untouched.
                self.library(); self.records()
                for name in ("library.json", "records.json"):
                    if (self.path / name).exists():
                        created.append(target / name)
                        atomic_json(target / name, json.loads((self.path / name).read_text(encoding="utf-8-sig")))
                created.append(target / "sessions.db")
                store.backup(target / "sessions.db")
            next_store = Store(target / "sessions.db")
            try:
                if self.config_path:
                    save_workspace_default(self.config_path, target)
            except Exception:
                next_store.close()
                raise
            return next_workspace, next_store
        except Exception:
            next_workspace.close()
            for path in created:
                path.unlink(missing_ok=True)
            raise
