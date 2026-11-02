import json
import shutil
import sqlite3
import time
import uuid
from .config import Problem

ACTIVE = ('uploading', 'queued', 'processing')


def connect(cfg):
    db = sqlite3.connect(cfg.data / 'queue.sqlite', timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=30000')
    return db


def init(cfg):
    with connect(cfg) as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS jobs (
          id TEXT PRIMARY KEY, owner TEXT NOT NULL, model TEXT NOT NULL,
          filename TEXT NOT NULL, ext TEXT NOT NULL, state TEXT NOT NULL,
          stage TEXT NOT NULL DEFAULT '', error TEXT, created REAL NOT NULL,
          updated REAL NOT NULL, expires REAL, cancel INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS limits (key TEXT PRIMARY KEY, start REAL, count INTEGER);
        INSERT OR IGNORE INTO settings VALUES ('default_model','"big-beta-4"');
        INSERT OR IGNORE INTO settings VALUES ('allow_choice','false');
        ''')


def setting(cfg, key, default=None):
    with connect(cfg) as db:
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else default


def put(cfg, key, value):
    with connect(cfg) as db:
        db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (key, json.dumps(value)))


def rate(cfg, key, maximum, window=60):
    with connect(cfg) as db:
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        db.execute('DELETE FROM limits WHERE start < ?', (now-window,))
        row = db.execute('SELECT count FROM limits WHERE key=?', (key,)).fetchone()
        if row and row[0] >= maximum:
            raise Problem('RATE_LIMIT', 'Too many requests. Try again shortly.', 429)
        db.execute('INSERT INTO limits VALUES (?,?,1) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, now))


def reserve(cfg, owner, model, filename, ext):
    with connect(cfg) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT count(*) FROM jobs WHERE owner=? AND state IN ('uploading','queued','processing')", (owner,)).fetchone()[0] >= cfg.per_user:
            raise Problem('USER_LIMIT', 'You already have an outstanding job.', 429)
        if db.execute("SELECT count(*) FROM jobs WHERE state IN ('uploading','queued')").fetchone()[0] >= cfg.queue:
            raise Problem('QUEUE_FULL', 'The queue is full. Please try later.', 429)
        count = db.execute("SELECT count(*) FROM jobs WHERE state IN ('uploading','queued','processing')").fetchone()[0]
        if shutil.disk_usage(cfg.data).free < cfg.floor + (count+1)*cfg.budget:
            raise Problem('DISK_SPACE', 'Insufficient processing space.', 503)
        ident, now = str(uuid.uuid4()), time.time()
        db.execute('INSERT INTO jobs(id,owner,model,filename,ext,state,created,updated) VALUES (?,?,?,?,?,?,?,?)', (ident, owner, model, filename, ext, 'uploading', now, now))
    (cfg.data / 'jobs' / ident).mkdir()
    return ident


def update(cfg, ident, **values):
    assert set(values) <= {'state','stage','error','expires','cancel'}
    values['updated'] = time.time()
    with connect(cfg) as db:
        db.execute('UPDATE jobs SET '+','.join(f'{key}=?' for key in values)+' WHERE id=?', (*values.values(), ident))


def get(cfg, ident, owner=None):
    with connect(cfg) as db:
        row = db.execute('SELECT * FROM jobs WHERE id=?', (ident,)).fetchone()
    if not row or (owner is not None and row['owner'] != owner):
        raise Problem('NOT_FOUND', 'Job not found.', 404)
    return dict(row)


def claim(cfg):
    with connect(cfg) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM jobs WHERE state='processing'").fetchone():
            return None
        row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET state='processing',updated=? WHERE id=?", (time.time(), row['id']))
            return dict(row)


def cleanup(cfg):
    now = time.time()
    with connect(cfg) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("UPDATE jobs SET state='failed',error='UPLOAD_EXPIRED',updated=? WHERE state='uploading' AND updated<?", (now, now-1200))
        db.execute("UPDATE jobs SET state='failed',error='QUEUE_EXPIRED',updated=? WHERE state='queued' AND created<?", (now, now-3600))
        rows = db.execute("SELECT * FROM jobs WHERE state IN ('failed','cancelled','expired') OR (state='complete' AND expires<?)", (now,)).fetchall()
        for row in rows:
            shutil.rmtree(cfg.data / 'jobs' / row['id'], ignore_errors=True)
            db.execute("UPDATE jobs SET state='expired' WHERE id=? AND state='complete'", (row['id'],))
        db.execute("DELETE FROM jobs WHERE state IN ('failed','cancelled','expired') AND updated<?", (now-86400,))
