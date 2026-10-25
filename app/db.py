"""SQLite persistence: sessions, user profiles (cross-session memory), bookings."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

DB_PATH = os.getenv("DB_PATH", "/data/onlygo.db")
_lock = threading.Lock()


def _conn():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


_c = None


def conn():
    global _c
    if _c is None:
        _c = _conn()
        _c.executescript("""
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, user_id TEXT, state TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS profiles(user_id TEXT PRIMARY KEY, prefs TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS bookings(id TEXT PRIMARY KEY, session_id TEXT, user_id TEXT, data TEXT, created TEXT);
        CREATE INDEX IF NOT EXISTS bookings_user ON bookings(user_id);
        """)
    return _c


def _get(table, key, val, col):
    with _lock:
        r = conn().execute(f"SELECT {col} FROM {table} WHERE {key}=?", (val,)).fetchone()
    return json.loads(r[0]) if r else None


def get_session(sid):
    return _get("sessions", "id", sid, "state")


def save_session(sid, user_id, state):
    with _lock:
        # keep the owner when a follow-up call (select/correct/checkout) does not know user_id
        conn().execute("INSERT INTO sessions VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                       "user_id=COALESCE(excluded.user_id, sessions.user_id), state=excluded.state, updated=excluded.updated",
                       (sid, user_id, json.dumps(state, ensure_ascii=False), datetime.now().isoformat()))
        conn().commit()


def session_user(sid):
    with _lock:
        r = conn().execute("SELECT user_id FROM sessions WHERE id=?", (sid,)).fetchone()
    return r[0] if r else None


def get_profile(uid):
    return _get("profiles", "user_id", uid, "prefs") or {}


def save_profile(uid, prefs):
    with _lock:
        conn().execute("INSERT OR REPLACE INTO profiles VALUES(?,?,?)", (uid, json.dumps(prefs, ensure_ascii=False), datetime.now().isoformat()))
        conn().commit()


def save_booking(bid, sid, uid, data):
    with _lock:
        conn().execute("INSERT OR REPLACE INTO bookings VALUES(?,?,?,?,?)", (bid, sid, uid, json.dumps(data, ensure_ascii=False), datetime.now().isoformat()))
        conn().commit()


def get_booking(bid):
    return _get("bookings", "id", bid, "data")


def list_bookings(uid):
    with _lock:
        rows = conn().execute("SELECT data FROM bookings WHERE user_id=? ORDER BY created DESC", (uid,)).fetchall()
    return [json.loads(r[0]) for r in rows]
