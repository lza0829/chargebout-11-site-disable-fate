from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  address TEXT NOT NULL DEFAULT '',
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS rate_plans (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  peak_price REAL NOT NULL,
  flat_price REAL NOT NULL,
  valley_price REAL NOT NULL,
  active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS piles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  rated_kw REAL NOT NULL DEFAULT 7,
  status TEXT NOT NULL DEFAULT '空闲',
  rate_plan_id INTEGER NOT NULL,
  UNIQUE(site_id, name),
  FOREIGN KEY(site_id) REFERENCES sites(id),
  FOREIGN KEY(rate_plan_id) REFERENCES rate_plans(id)
);
CREATE TABLE IF NOT EXISTS guns (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  pile_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '可用',
  UNIQUE(pile_id, name),
  FOREIGN KEY(pile_id) REFERENCES piles(id)
);
CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  slip_no TEXT NOT NULL UNIQUE,
  site_id INTEGER NOT NULL,
  pile_id INTEGER NOT NULL,
  gun_id INTEGER NOT NULL,
  rate_plan_id INTEGER NOT NULL,
  business_day TEXT NOT NULL,
  status TEXT NOT NULL,
  kwh REAL NOT NULL DEFAULT 0,
  amount REAL NOT NULL DEFAULT 0,
  price_locked REAL,
  started_at TEXT NOT NULL,
  closed_at TEXT,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(site_id) REFERENCES sites(id),
  FOREIGN KEY(pile_id) REFERENCES piles(id),
  FOREIGN KEY(gun_id) REFERENCES guns(id),
  FOREIGN KEY(rate_plan_id) REFERENCES rate_plans(id)
);
CREATE TABLE IF NOT EXISTS pulses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL,
  delta_kwh REAL NOT NULL,
  power_kw REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS session_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  created_at TEXT NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(session_id) REFERENCES sessions(id)
);
CREATE TABLE IF NOT EXISTS day_seals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  business_day TEXT NOT NULL UNIQUE,
  sealed INTEGER NOT NULL DEFAULT 0,
  sealed_at TEXT,
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS site_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  seq INTEGER NOT NULL,
  open_sessions INTEGER NOT NULL DEFAULT 0,
  open_slips TEXT NOT NULL DEFAULT '[]',
  note TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  UNIQUE(site_id, seq),
  FOREIGN KEY(site_id) REFERENCES sites(id)
);
"""


def connect(timeout: float = 5.0) -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False, timeout=timeout)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db(immediate: bool = False):
    """immediate=True 时以 BEGIN IMMEDIATE 开启事务：进入即取写锁，
    闸判定与后续写入同事务串行，杜绝"检查通过后被下线挤入"的半截写。"""
    conn = connect()
    try:
        if immediate:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)


def row_dict(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def rows(rs) -> list[dict]:
    return [row_dict(r) for r in rs]
