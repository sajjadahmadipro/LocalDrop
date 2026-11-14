"""SQLite persistence: devices, transfers and settings. Standard library only."""

import json
import os
import sqlite3
import threading
import time

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL DEFAULT 'unknown',
    role        TEXT NOT NULL DEFAULT 'remote',
    token_hash  TEXT NOT NULL DEFAULT '',
    paired      INTEGER NOT NULL DEFAULT 0,
    user_agent  TEXT NOT NULL DEFAULT '',
    remote      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    extras      TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS transfers (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    stored_path   TEXT NOT NULL DEFAULT '',
    size          INTEGER NOT NULL DEFAULT 0,
    mime          TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT '',
    dest          TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    offer         TEXT NOT NULL DEFAULT 'none',
    offset        INTEGER NOT NULL DEFAULT 0,
    chunk_size    INTEGER NOT NULL DEFAULT 1048576,
    sha256        TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    completed_at  REAL NOT NULL DEFAULT 0,
    dl_bytes      INTEGER NOT NULL DEFAULT 0,
    dl_started    REAL NOT NULL DEFAULT 0,
    dl_done       REAL NOT NULL DEFAULT 0,
    extras        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transfers_updated ON transfers(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_transfers_dest ON transfers(dest, status);
CREATE INDEX IF NOT EXISTS idx_transfers_source ON transfers(source, status);
CREATE INDEX IF NOT EXISTS idx_devices_seen ON devices(last_seen);
"""

COLUMNS = [
    "id", "filename", "stored_path", "size", "mime", "source", "dest", "status",
    "offer", "offset", "chunk_size", "sha256", "error", "created_at", "updated_at",
    "completed_at", "dl_bytes", "dl_started", "dl_done", "extras",
]


class Database:
    """One connection per thread; WAL keeps concurrent readers cheap."""

    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self._ready = False
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.connection()

    def connection(self):
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15.0, isolation_level=None,
                                   check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                conn.execute("PRAGMA journal_mode=DELETE")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA busy_timeout=15000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn = conn
            if not self._ready:
                with self._init_lock:
                    if not self._ready:
                        conn.executescript(SCHEMA)
                        version = self.get_meta("schema_version") or "0"
                        if version != str(SCHEMA_VERSION):
                            self.set_meta("schema_version", str(SCHEMA_VERSION))
                        self._ready = True
        return conn

    def close(self):
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            finally:
                self._local.conn = None

    # -- meta ----------------------------------------------------------
    def get_meta(self, key, default=None):
        row = self.connection().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key, value):
        self.connection().execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

    # -- devices -------------------------------------------------------
    def device(self, device_id):
        row = self.connection().execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        return dict(row) if row else None

    def device_by_token(self, token_hash):
        if not token_hash:
            return None
        row = self.connection().execute(
            "SELECT * FROM devices WHERE token_hash=? AND paired=1", (token_hash,)).fetchone()
        return dict(row) if row else None

    def upsert_device(self, device_id, **fields):
        now = fields.pop("now", time.time())
        existing = self.device(device_id)
        conn = self.connection()
        extras = fields.pop("extras", None)
        if existing is None:
            conn.execute(
                "INSERT INTO devices(id,name,kind,role,token_hash,paired,user_agent,remote,"
                "created_at,last_seen,extras) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (device_id, fields.get("name", ""), fields.get("kind", "unknown"),
                 fields.get("role", "remote"), fields.get("token_hash", ""),
                 int(fields.get("paired", 0)), fields.get("user_agent", ""),
                 fields.get("remote", ""), now, now,
                 json.dumps(extras if extras is not None else {})))
        else:
            sets, args = [], []
            for key in ("name", "kind", "role", "token_hash", "paired", "user_agent", "remote"):
                if key in fields:
                    sets.append("%s=?" % key)
                    args.append(fields[key])
            if extras is not None:
                merged = json.loads(existing.get("extras") or "{}")
                merged.update(extras)
                sets.append("extras=?")
                args.append(json.dumps(merged))
            sets.append("last_seen=?")
            args.extend([now, device_id])
            conn.execute("UPDATE devices SET %s WHERE id=?" % ",".join(sets), args)
        return self.device(device_id)

    def touch_device(self, device_id, now=None):
        self.connection().execute("UPDATE devices SET last_seen=? WHERE id=?",
                                  (now or time.time(), device_id))

    def devices(self):
        rows = self.connection().execute(
            "SELECT * FROM devices ORDER BY created_at ASC").fetchall()
        return [dict(r) for r in rows]

    def delete_device(self, device_id):
        self.connection().execute("DELETE FROM devices WHERE id=?", (device_id,))

    # -- transfers -----------------------------------------------------
    def insert_transfer(self, record):
        conn = self.connection()
        conn.execute(
            "INSERT INTO transfers(id,filename,stored_path,size,mime,source,dest,status,"
            "offer,offset,chunk_size,sha256,error,created_at,updated_at,completed_at,"
            "dl_bytes,dl_started,dl_done,extras) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record["id"], record["filename"], record.get("stored_path", ""),
             record.get("size", 0), record.get("mime", ""), record.get("source", ""),
             record.get("dest", ""), record.get("status", "pending"),
             record.get("offer", "none"), record.get("offset", 0),
             record.get("chunk_size", 1048576), record.get("sha256", ""),
             record.get("error", ""), record["created_at"], record["updated_at"],
             record.get("completed_at", 0), record.get("dl_bytes", 0),
             record.get("dl_started", 0), record.get("dl_done", 0),
             json.dumps(record.get("extras", {}))))
        # Hand back the persisted row so callers see column defaults instead of the
        # partial dict they passed in.
        return self.transfer(record["id"])

    def transfer(self, transfer_id):
        row = self.connection().execute(
            "SELECT * FROM transfers WHERE id=?", (transfer_id,)).fetchone()
        return dict(row) if row else None

    def transfers(self, limit=500):
        rows = self.connection().execute(
            "SELECT * FROM transfers ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def transfers_for_device(self, device_id):
        rows = self.connection().execute(
            "SELECT * FROM transfers WHERE source=? OR dest=? ORDER BY created_at DESC",
            (device_id, device_id)).fetchall()
        return [dict(r) for r in rows]

    def update_transfer(self, transfer_id, **fields):
        fields.setdefault("updated_at", time.time())
        sets, args = [], []
        for key, value in fields.items():
            if key not in COLUMNS or key == "id":
                raise KeyError("unknown transfer column: %s" % key)
            if key == "extras":
                value = json.dumps(value)
            sets.append("%s=?" % key)
            args.append(value)
        args.append(transfer_id)
        self.connection().execute(
            "UPDATE transfers SET %s WHERE id=?" % ",".join(sets), args)
        return self.transfer(transfer_id)

    def delete_transfer(self, transfer_id):
        self.connection().execute("DELETE FROM transfers WHERE id=?", (transfer_id,))

    def incomplete_transfers(self):
        return [r for r in self.transfers()
                if r["status"] not in ("complete", "rejected", "canceled")]
