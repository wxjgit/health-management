import sqlite3


SCHEMA = '''
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY, op_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
        occurred_at TEXT NOT NULL, day TEXT NOT NULL, raw_text TEXT NOT NULL,
        data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS events_day ON events(day, kind);
    CREATE TABLE IF NOT EXISTS revisions (
        event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
        version INTEGER NOT NULL, data TEXT NOT NULL, raw_text TEXT NOT NULL,
        reason TEXT NOT NULL, created_at TEXT NOT NULL,
        PRIMARY KEY(event_id, version));
    CREATE TABLE IF NOT EXISTS operations (key TEXT PRIMARY KEY, result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS workflows (
        id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
        kind TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS receipts (
        request_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
        result TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
        role TEXT NOT NULL, text TEXT NOT NULL, cards TEXT NOT NULL,
        evidence_refs TEXT NOT NULL, created_at TEXT NOT NULL,
        process_trace TEXT NOT NULL DEFAULT '[]', duration_ms INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS model_calls (
        thread_id TEXT NOT NULL, purpose TEXT NOT NULL, payload_bytes INTEGER NOT NULL,
        created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS facts (
        id TEXT PRIMARY KEY, content TEXT NOT NULL, source TEXT NOT NULL,
        source_event TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS plans (
        id TEXT PRIMARY KEY, op_key TEXT UNIQUE NOT NULL, data TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS insights (
        id TEXT PRIMARY KEY, template TEXT UNIQUE NOT NULL, data TEXT NOT NULL,
        status TEXT NOT NULL, feedback TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS reviews (
        id TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS notifications (
        id TEXT PRIMARY KEY, dedupe_key TEXT UNIQUE NOT NULL, title TEXT NOT NULL,
        body TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS jobs (
        key TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS monthly_summaries (
        month TEXT PRIMARY KEY, data TEXT NOT NULL, created_at TEXT NOT NULL);
    PRAGMA user_version=1;
'''


def open_database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA foreign_keys=ON')
    connection.executescript(SCHEMA)
    columns = {
        row['name'] for row in connection.execute('PRAGMA table_info(messages)').fetchall()
    }
    if 'process_trace' not in columns:
        connection.execute(
            "ALTER TABLE messages ADD COLUMN process_trace TEXT NOT NULL DEFAULT '[]'"
        )
    if 'duration_ms' not in columns:
        connection.execute(
            'ALTER TABLE messages ADD COLUMN duration_ms INTEGER NOT NULL DEFAULT 0'
        )
    connection.execute('PRAGMA user_version=3')
    connection.commit()
    return connection
