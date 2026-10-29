"""Short-lived host-owned diagnosis evidence. Never restore reports from client input."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import secrets
import threading
import time

from ..team_assist import _digest


class ContextError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Lease:
    id: str
    revision: int
    request_key: str
    state: dict


class DiagnosisContexts:
    """Bounded in-memory LRU; restart/expiry requires a fresh diagnosis, not a rebuild."""

    def __init__(self, *, turns=3, ttl=1800, max_entries=24, max_bytes=32*1024*1024, clock=time.time):
        self.turns, self.ttl = turns, ttl
        self.max_entries, self.max_bytes, self.clock = max_entries, max_bytes, clock
        self._entries = {}
        self._lock = threading.RLock()

    def _prune(self):
        now = self.clock()
        for key, entry in list(self._entries.items()):
            if entry["expires"] <= now and not entry["busy"]:
                del self._entries[key]

    def _metadata(self, key, entry):
        return {"id": key, "revision": entry["revision"], "remainingTurns": max(0, self.turns-entry["revision"]),
                "expiresAt": datetime.fromtimestamp(entry["expires"], timezone.utc).isoformat()}

    @staticmethod
    def _state(report, reads):
        report = deepcopy(report)
        report.pop("continuation", None)
        return {"report": report, "reads": deepcopy(reads)}

    @staticmethod
    def _size(state):
        # Cache keys are tuples internal to TeamAssist; measure their JSON equivalent.
        return len(json.dumps({"report": state["report"], "reads": list(state["reads"].items())},
                              ensure_ascii=False, separators=(",", ":")).encode("utf-8"))

    def create(self, owner, binding, report, reads):
        state = self._state(report, reads)
        size = self._size(state)
        if size > self.max_bytes or self.turns < 1:
            return None
        with self._lock:
            self._prune()
            while len(self._entries) >= self.max_entries or sum(e["size"] for e in self._entries.values()) + size > self.max_bytes:
                victim = next((k for k, e in self._entries.items() if not e["busy"]), None)
                if victim is None:
                    return None
                del self._entries[victim]
            key = secrets.token_hex(24)
            entry = {"owner": owner, "binding": binding, "state": state, "revision": 0,
                     "expires": self.clock()+self.ttl, "busy": False, "size": size, "lastRequest": None}
            self._entries[key] = entry
            return self._metadata(key, entry)

    def claim(self, owner, binding, token, request):
        with self._lock:
            self._prune()
            key, revision = token["id"], token["revision"]
            entry = self._entries.get(key)
            if not entry or entry["owner"] != owner or entry["binding"] != binding or entry["expires"] <= self.clock():
                raise ContextError("context_unavailable")
            request_key = _digest(request)
            if entry["busy"]:
                raise ContextError("context_busy")
            if revision == entry["revision"]-1 and request_key == entry["lastRequest"]:
                result = deepcopy(entry["state"]["report"])
                result["continuation"] = self._metadata(key, entry)
                return result  # An exact retry replays the host result without model/quota spend.
            if revision != entry["revision"]:
                raise ContextError("context_conflict")
            if revision >= self.turns:
                raise ContextError("followup_limit")
            entry["busy"] = True
            self._entries.pop(key)
            self._entries[key] = entry
            return Lease(key, revision, request_key, deepcopy(entry["state"]))

    def complete(self, lease, report, reads):
        state = self._state(report, reads)
        size = self._size(state)
        with self._lock:
            entry = self._entries.get(lease.id)
            if not entry or entry["revision"] != lease.revision or not entry["busy"]:
                raise ContextError("context_conflict")
            if entry["expires"] <= self.clock() or size > self.max_bytes:
                del self._entries[lease.id]
                return None
            # A turn may enlarge its evidence. Evict only idle other contexts.
            while sum(e["size"] for k, e in self._entries.items() if k != lease.id) + size > self.max_bytes:
                victim = next((k for k, e in self._entries.items() if k != lease.id and not e["busy"]), None)
                if victim is None:
                    del self._entries[lease.id]
                    return None
                del self._entries[victim]
            entry.update(state=state, size=size, revision=lease.revision+1, busy=False, lastRequest=lease.request_key)
            return self._metadata(lease.id, entry)

    def release(self, lease):
        with self._lock:
            entry = self._entries.get(lease.id)
            if entry and entry["revision"] == lease.revision:
                entry["busy"] = False
            self._prune()

    def discard(self, key):
        with self._lock:
            self._entries.pop(key, None)

    def prune(self):
        with self._lock:
            self._prune()

    def clear(self):
        with self._lock:
            self._entries.clear()
