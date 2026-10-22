import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,            -- 正規化済み(小文字・trim)
    password_hash TEXT,                    -- 有効化前はNULL
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('admin','user')),
    activation_code_hash TEXT,             -- 6桁コードのハッシュ。使用後NULL
    activation_attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    company TEXT NOT NULL DEFAULT '',
    department TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    address TEXT NOT NULL DEFAULT '',
    website TEXT NOT NULL DEFAULT '',
    image_file TEXT NOT NULL,              -- IMAGE_DIR 配下のファイル名
    image_mime TEXT NOT NULL,
    company_key TEXT NOT NULL DEFAULT '',  -- normalize.company_key(company)。空=識別不能
    department_key TEXT NOT NULL DEFAULT '',
    person_key TEXT NOT NULL DEFAULT '',   -- normalize.person_key(email)。空=識別不能
    registered_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cards_company_key ON cards(company_key);
CREATE INDEX IF NOT EXISTS idx_cards_person_key ON cards(person_key);
CREATE TABLE IF NOT EXISTS grants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('company','department','person')),
    company_key TEXT NOT NULL DEFAULT '',
    department_key TEXT NOT NULL DEFAULT '',
    person_key TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL,                   -- 画面表示用
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, kind, company_key, department_key, person_key)
);
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    image_file TEXT NOT NULL,
    image_mime TEXT NOT NULL,
    extracted_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as db:
        db.executescript(SCHEMA)
