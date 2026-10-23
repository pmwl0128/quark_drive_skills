"""One private identity pool and cross-process account leases for both game workflows."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time

POOL_KEY = 'PCUI_GAME_IDENTITY_POOL'
TASKS = {'team_code', 'battle_data'}


class PoolError(Exception):
    def __init__(self, code, retry_after=0):
        self.code, self.retry_after = code, retry_after
        super().__init__(code)


def parse_pool(text):
    try:
        pool = json.loads(text)
        if type(pool['version']) is not int or pool['version'] not in (1, 2):
            raise ValueError
        if not isinstance(pool['identities'], list) or not 1 <= len(pool['identities']) <= 32:
            raise ValueError
        rows, seen = {}, set()
        for row in pool['identities']:
            if (not isinstance(row, dict) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,39}', row['id'])
                    or not re.fullmatch(r'[A-Za-z0-9]{42}', row['uuid'])
                    or type(row['enabled']) is not bool or row['id'] in rows or row['uuid'] in seen):
                raise ValueError
            rows[row['id']] = {'id':row['id'], 'uuid':row['uuid'], 'enabled':row['enabled']}
            seen.add(row['uuid'])
        if not any(row['enabled'] for row in rows.values()):
            raise ValueError
        # Read the installed registry during the explicit v2 migration. Legacy role labels
        # no longer restrict membership: both tasks share all enabled identities.
        if pool['version'] == 2 and ('bindings' in pool or 'pools' in pool
                                    or any('roles' in row for row in pool['identities'])):
            raise ValueError
        return rows
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError('invalid_identity_pool') from None


def pool_identities(text, task):
    if task not in TASKS:
        raise ValueError('invalid_identity_task')
    return [dict(row) for row in parse_pool(text).values() if row['enabled']]


def summary(text):
    return {'version':2, 'shared_tasks':sorted(TASKS), 'identities':[
        {'id':row['id'], 'enabled':row['enabled']} for row in parse_pool(text).values()]}


def state_directory(config):
    return Path(config.get('PCUI_GAME_IDENTITY_STATE_DIR')
                or Path.home() / '.pokemon-champions/game-identities')


class Pool:
    def __init__(self, text, root, *, clock=time.time, rng=None):
        self.rows = pool_identities(text, 'battle_data')
        self.clock, self.rng = clock, rng or secrets.SystemRandom()
        try:
            root = Path(root)
            root.mkdir(parents=True, exist_ok=True)
            os.chmod(root, 0o700)
            self.db = sqlite3.connect(root / 'state.sqlite3', timeout=5, isolation_level=None)
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS control(id INTEGER PRIMARY KEY CHECK(id=1),
                    last_identity TEXT, circuit TEXT, blocked_until REAL NOT NULL DEFAULT 0);
                INSERT OR IGNORE INTO control(id) VALUES(1);
                CREATE TABLE IF NOT EXISTS leases(identity TEXT PRIMARY KEY, owner TEXT,
                    task TEXT, until REAL NOT NULL DEFAULT 0);
            ''')
        except (sqlite3.Error, OSError):
            raise PoolError('state_unavailable') from None

    def acquire(self, task, seconds):
        if task not in TASKS or not 60 <= seconds <= 1800:
            raise ValueError('invalid_identity_lease')
        now = self.clock()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            last, circuit, blocked = self.db.execute(
                'SELECT last_identity,circuit,blocked_until FROM control WHERE id=1').fetchone()
            if circuit or blocked > now:
                raise PoolError('circuit_open', max(1, int(blocked-now)+1))
            active = dict(self.db.execute('SELECT identity,until FROM leases WHERE until>?', (now,)))
            candidates = {hashlib.sha256(row['uuid'].encode()).hexdigest():row for row in self.rows}
            free = [key for key in candidates if key not in active]
            if not free:
                raise PoolError('busy', max(1, int(min(active[key] for key in candidates)-now)+1))
            selected = self.rng.choice([key for key in free if key != last] or free)
            owner = secrets.token_hex(16)
            self.db.execute('INSERT OR REPLACE INTO leases VALUES(?,?,?,?)',
                            (selected, owner, task, now+seconds))
            self.db.execute('UPDATE control SET last_identity=? WHERE id=1', (selected,))
            self.db.commit()
            return Lease(self, candidates[selected], selected, owner, seconds)
        except BaseException:
            self.db.rollback()
            raise

    def reset_circuit(self, expected):
        changed = self.db.execute('UPDATE control SET circuit=NULL WHERE id=1 AND circuit=?', (expected,)).rowcount
        if not changed:
            raise PoolError('circuit_mismatch')

    def close(self):
        self.db.close()


class Lease:
    def __init__(self, pool, identity, key, owner, seconds):
        self.pool, self.identity, self.key = pool, identity, key
        self.owner, self.seconds = owner, seconds

    def check(self):
        now, db = self.pool.clock(), self.pool.db
        db.execute('BEGIN IMMEDIATE')
        try:
            row = db.execute('SELECT owner,until FROM leases WHERE identity=?', (self.key,)).fetchone()
            if not row or row[0] != self.owner or row[1] <= now:
                raise PoolError('lease_lost')
            circuit, blocked = db.execute('SELECT circuit,blocked_until FROM control WHERE id=1').fetchone()
            if circuit or blocked > now:
                raise PoolError('circuit_open', max(1, int(blocked-now)+1))
            db.execute('UPDATE leases SET until=? WHERE identity=? AND owner=?',
                       (now+self.seconds, self.key, self.owner))
            db.commit()
        except BaseException:
            db.rollback()
            raise

    def fail(self, code, *, retry_after=0, permanent=False):
        if not re.fullmatch(r'[a-z0-9_]{1,64}', code):
            raise ValueError('invalid_pool_error')
        # Refusals block selection across tasks; they never cause account failover.
        self.pool.db.execute('UPDATE control SET blocked_until=MAX(blocked_until,?),'
                             'circuit=COALESCE(?,circuit) WHERE id=1',
            (self.pool.clock()+max(0,retry_after), code if permanent else None))

    def release(self):
        self.pool.db.execute('DELETE FROM leases WHERE identity=? AND owner=?', (self.key,self.owner))
