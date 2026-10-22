"""Anonymous rate limiting + daily model-budget circuit breaker (frontend/design.md §7.4).

Identity is two-keyed and never stores a raw IP: a signed anonymous device cookie AND the
IP hashed under a daily-rotating key (key = H(secret + utc-day), so yesterday's hashes are
unlinkable). A request counts against BOTH ids and is refused when EITHER is over the day
limit — clearing cookies alone does not reset the allowance. Counters live in SQLite so a
process restart does not reset the day; rows expire after three days (§7.4: 24-72h).

Budget: before each LLM task a pessimistic token amount is RESERVED; after it the real
usage is SETTLED. Reservations mean N concurrent requests can never overshoot the daily
budget by racing the counter."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .database import connect


class RateLimited(Exception):
    pass


class BudgetExhausted(Exception):
    pass


@dataclass
class QaLimitConfig:
    daily_limit: int = 15              # questions per identity per UTC day
    daily_token_budget: int = 1_500_000  # prompt+completion tokens across ALL users/day (§7.4 breaker, 2026-07-16)
    pessimistic_tokens: int = 12_000   # reserved per request until settled with real usage
    # Builder wizard (§7.3/§7.4): separate, much smaller per-identity allowance, and a larger
    # pessimistic reservation (multi-gate pipeline: grounding + generation + up to one audit
    # repair — several LLM calls with ranking/frame context in the prompt). Both draw on the
    # SAME daily token budget as QA: one breaker protects the one wallet.
    builder_daily_limit: int = 2
    builder_pessimistic_tokens: int = 60_000
    # A thinking-mode diagnose reading: the ~28 KB report as input plus a long answer whose reasoning
    # counts against the same cap (qa.DIAGNOSE_EXPLAIN_THINKING_MAX_TOKENS) — twice the plain QA
    # reservation, so the breaker admits it only when the day can actually afford it.
    diagnose_thinking_pessimistic_tokens: int = 24_000
    # Team diagnose has a separate allowance from QA. A thinking explanation consumes two
    # units; the ordinary deterministic/quick path consumes one.
    diagnose_daily_limit: int = 4
    team_code_daily_limit: int = 30  # Cache misses only; never projected into public quota UI.
    # CPU-heavy actual-set batteries use workload units, not a flat request count. One unit is
    # 30 source-set x Top-K pairs; 24 units lets a client run the full 6 x 60 battery twice while
    # allowing proportionally more small reads. Device and IP both consume the same units.
    matchup_daily_limit: int = 24
    # Precise SP tuning is charged per benchmark. A benchmark costs tens of milliseconds on the
    # in-process calc engine, so this is an abuse ceiling rather than a cost budget: ten maximal
    # (12-target) solves, or thirty-odd untargeted ones, a day. It exists because tune shares the
    # single heavy deterministic slot with actual matchup, and an unmetered script could hold it.
    tune_daily_limit: int = 120


class OnlineLimits:
    def __init__(self, db_path: Path, secret: str, cfg: QaLimitConfig | None = None):
        self.cfg = cfg or QaLimitConfig()
        self._secret = secret
        self._lock = threading.Lock()
        # Every quota check, reservation and settlement is a write serialized behind one lock on
        # one connection. The shared opener enforces the explicit data contract, WAL and NORMAL
        # durability before this object can consume any state.
        self._con = connect(db_path)

    @staticmethod
    def _day(offset_days: int = 0) -> str:
        return time.strftime("%Y-%m-%d", time.gmtime(time.time() - offset_days * 86400))

    def _expire(self) -> None:
        """Drop rows past the 3-day retention window (§7.4). Called by EVERY request entry
        point (check_and_consume / reserve_budget / record_spent) — not just the quota path —
        so a day served only by dev traffic still prunes. Runs inside the caller's txn+lock."""
        cutoff = self._day(3)
        self._con.execute("DELETE FROM qa_quota WHERE day < ?", (cutoff,))
        self._con.execute("DELETE FROM qa_budget WHERE day < ?", (cutoff,))

    # -- identity ------------------------------------------------------------------------

    def _sign(self, device_id: str) -> str:
        return hmac.new(self._secret.encode("utf-8"), device_id.encode("utf-8"),
                        hashlib.sha256).hexdigest()[:16]

    def device_cookie(self, raw: str | None) -> tuple[str, str | None]:
        """(device_id, new_cookie_value or None). A missing/forged cookie mints a fresh
        signed identity — forging only ever gets you a NEW empty allowance, same as
        clearing cookies, and the IP key still counts."""
        if raw and "." in raw:
            device_id, sig = raw.rsplit(".", 1)
            if hmac.compare_digest(self._sign(device_id), sig):
                return device_id, None
        device_id = secrets.token_hex(8)
        return device_id, f"{device_id}.{self._sign(device_id)}"

    @staticmethod
    def _ip_identity(ip: str) -> str:
        """Collapse an IP to its rate-limit identity. IPv6 is keyed by the /64 PREFIX, not
        the full address: an attacker holding a single /64 (the common home/VPS allocation)
        could otherwise mint effectively unlimited identities by rotating the host bits and
        bypass the per-identity count, bounded only by the global budget breaker. IPv4 and
        unparseable values pass through unchanged."""
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return ip
        if addr.version == 6:
            return str(ipaddress.ip_network(f"{addr}/64", strict=False).network_address)
        return str(addr)

    def ip_hash(self, ip: str) -> str:
        day_key = hashlib.sha256(f"{self._secret}:{self._day()}".encode("utf-8")).hexdigest()
        return hashlib.sha256(
            f"{day_key}:{self._ip_identity(ip)}".encode("utf-8")).hexdigest()[:32]

    # -- per-identity daily count ----------------------------------------------------------

    def check_and_consume(self, ids: list[str], limit: int | None = None, *,
                          cost: int = 1) -> tuple[int, int, str]:
        """Count ``cost`` units against every identity; RateLimited if ANY would exceed
        already. Returns (used, limit, day) for ids[0] (the device id) AFTER counting — the
        `day` lets a later refund hit the SAME day even across a UTC-midnight rollover.
        `limit` selects the surface allowance (default: QA); the builder passes its own ids
        (distinct prefix) with its own limit, so the two counters never mix."""
        if cost < 1:
            raise ValueError("cost must be positive")
        limit = self.cfg.daily_limit if limit is None else limit
        day = self._day()
        with self._lock, self._con:
            self._expire()
            for i in ids:
                row = self._con.execute("SELECT used FROM qa_quota WHERE id=? AND day=?",
                                        (i, day)).fetchone()
                if (row[0] if row else 0) + cost > limit:
                    raise RateLimited(f"daily limit {limit} reached")
            for i in ids:
                self._con.execute(
                    "INSERT INTO qa_quota (id, day, used) VALUES (?, ?, ?) "
                    "ON CONFLICT (id, day) DO UPDATE SET used = used + ?",
                    (i, day, cost, cost))
            used = self._con.execute("SELECT used FROM qa_quota WHERE id=? AND day=?",
                                     (ids[0], day)).fetchone()[0]
        return used, limit, day

    def usage(self, identity: str, limit: int | None = None) -> tuple[int, int]:
        """Read-only (used, limit) for one identity today — the dev bypass reports real
        state without consuming."""
        with self._lock:
            row = self._con.execute("SELECT used FROM qa_quota WHERE id=? AND day=?",
                                    (identity, self._day())).fetchone()
        return (row[0] if row else 0), (self.cfg.daily_limit if limit is None else limit)

    def refund(self, ids: list[str], day: str | None = None, *, cost: int = 1) -> None:
        """Give the count back when WE failed (provider down, queue full) — a user should
        not lose allowance to a server-side failure. `day` binds the refund to the day the
        count was booked (check_and_consume's return), so a midnight-straddling request
        refunds the right row instead of a fresh, empty one."""
        if cost < 1:
            raise ValueError("cost must be positive")
        day = day or self._day()
        with self._lock, self._con:
            for i in ids:
                self._con.execute(
                    "UPDATE qa_quota SET used = MAX(0, used - ?) WHERE id=? AND day=?",
                    (cost, i, day))

    # -- daily token budget (reserve -> settle) ---------------------------------------------

    def reserve_budget(self, pessimistic: int | None = None) -> str:
        """Reserve one pessimistic chunk against the breaker. Returns the day it booked
        against — settle_budget MUST be given this day (and the SAME `pessimistic`) so a
        request that crosses UTC midnight releases its real reservation instead of leaking it
        on the old day and mis-booking spend on the new one (external audit)."""
        amount = self.cfg.pessimistic_tokens if pessimistic is None else pessimistic
        day = self._day()
        with self._lock, self._con:
            self._expire()
            row = self._con.execute("SELECT reserved, spent FROM qa_budget WHERE day=?",
                                    (day,)).fetchone()
            reserved, spent = row or (0, 0)
            if reserved + spent + amount > self.cfg.daily_token_budget:
                raise BudgetExhausted("daily model budget exhausted")
            self._con.execute(
                "INSERT INTO qa_budget (day, reserved, spent) VALUES (?, ?, 0) "
                "ON CONFLICT (day) DO UPDATE SET reserved = reserved + ?",
                (day, amount, amount))
        return day

    def settle_budget(self, actual_tokens: int, day: str | None = None,
                      pessimistic: int | None = None) -> None:
        """Release this request's reservation and record what was really spent. Called on
        every path after reserve_budget — success (real usage) and failure (0). `day` and
        `pessimistic` are the reserve_budget() call's values, so the reservation released
        matches the one booked."""
        amount = self.cfg.pessimistic_tokens if pessimistic is None else pessimistic
        day = day or self._day()
        with self._lock, self._con:
            self._con.execute(
                "UPDATE qa_budget SET reserved = MAX(0, reserved - ?), spent = spent + ? "
                "WHERE day = ?",
                (amount, max(0, int(actual_tokens)), day))

    def record_spent(self, actual_tokens: int, day: str | None = None) -> None:
        """Count real spend WITHOUT a reservation (dev bypass): the breaker never rejects
        dev traffic, but the day's spent total must stay truthful — dev tokens cost the
        same money."""
        if actual_tokens <= 0:
            return
        day = day or self._day()
        with self._lock, self._con:
            self._expire()
            self._con.execute(
                "INSERT INTO qa_budget (day, reserved, spent) VALUES (?, 0, ?) "
                "ON CONFLICT (day) DO UPDATE SET spent = spent + ?",
                (day, int(actual_tokens), int(actual_tokens)))
