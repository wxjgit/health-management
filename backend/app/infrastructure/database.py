import sqlite3

SCHEMA_VERSION = 9


def execute_schema(connection, script):
    # These schemas contain DDL only; execute without executescript implicit commits.
    for statement in script.split(';'):
        if statement.strip():
            connection.execute(statement)


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
    CREATE TABLE IF NOT EXISTS resume_attempts (
        request_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, interrupt_id TEXT NOT NULL,
        fingerprint TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
        created_at TEXT NOT NULL, UNIQUE(thread_id, interrupt_id));
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
'''


PROVENANCE_SCHEMA = '''
    CREATE TABLE IF NOT EXISTS provenance_nodes (
        node_id TEXT PRIMARY KEY, object_type TEXT NOT NULL, object_id TEXT NOT NULL,
        version TEXT NOT NULL, part TEXT NOT NULL DEFAULT 'whole',
        provenance_state TEXT NOT NULL, lifecycle TEXT NOT NULL DEFAULT 'live',
        is_current INTEGER NOT NULL DEFAULT 1,
        UNIQUE(object_type, object_id, version, part));
    CREATE INDEX IF NOT EXISTS provenance_objects
        ON provenance_nodes(object_type, object_id, is_current);
    CREATE TABLE IF NOT EXISTS provenance_dependencies (
        source_node_id TEXT NOT NULL REFERENCES provenance_nodes(node_id),
        target_node_id TEXT NOT NULL REFERENCES provenance_nodes(node_id),
        PRIMARY KEY(source_node_id, target_node_id));
    CREATE INDEX IF NOT EXISTS provenance_dependents ON provenance_dependencies(target_node_id);
    CREATE TABLE IF NOT EXISTS provenance_scopes (
        target_node_id TEXT NOT NULL REFERENCES provenance_nodes(node_id),
        scope_key TEXT NOT NULL, criteria TEXT NOT NULL,
        PRIMARY KEY(target_node_id, scope_key));
    CREATE TABLE IF NOT EXISTS provenance_occurrences (
        source_id TEXT NOT NULL, holder_node_id TEXT NOT NULL REFERENCES provenance_nodes(node_id),
        field_path TEXT NOT NULL,
        PRIMARY KEY(source_id, holder_node_id, field_path));
    CREATE INDEX IF NOT EXISTS provenance_copies ON provenance_occurrences(holder_node_id);
    CREATE TABLE IF NOT EXISTS deletion_previews (
        id TEXT PRIMARY KEY, target_type TEXT NOT NULL, target_id TEXT NOT NULL,
        scope_token TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS deletion_jobs (
        id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, target_type TEXT NOT NULL,
        target_id TEXT NOT NULL, state TEXT NOT NULL, manifest TEXT NOT NULL,
        created_at TEXT NOT NULL, completed_at TEXT);
    CREATE TABLE IF NOT EXISTS deletion_items (
        deletion_id TEXT NOT NULL REFERENCES deletion_jobs(id), thread_id TEXT NOT NULL,
        state TEXT NOT NULL, PRIMARY KEY(deletion_id, thread_id));
    CREATE TABLE IF NOT EXISTS request_tombstones (
        request_id TEXT PRIMARY KEY, deletion_id TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS workflow_generations (
        thread_id TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS job_generations (
        key TEXT PRIMARY KEY, generation INTEGER NOT NULL DEFAULT 0);
'''


def open_database(path):
    from .budget_connection import BudgetConnection
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False, timeout=30, factory=BudgetConnection)
    connection.row_factory = sqlite3.Row
    if connection.execute('PRAGMA user_version').fetchone()[0] > SCHEMA_VERSION:
        connection.close()
        raise ValueError('数据库版本高于当前程序，请升级程序')
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('PRAGMA secure_delete=ON')
    try:
        connection.execute('BEGIN IMMEDIATE')
        execute_schema(connection, SCHEMA)
        execute_schema(connection, PROVENANCE_SCHEMA)
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
        execute_schema(connection, '''
            CREATE TABLE IF NOT EXISTS fact_details (
                fact_id TEXT PRIMARY KEY REFERENCES facts(id) ON DELETE CASCADE,
                data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS fact_revisions (
                fact_id TEXT NOT NULL REFERENCES facts(id) ON DELETE CASCADE,
                version INTEGER NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL,
                PRIMARY KEY(fact_id, version));
            CREATE TABLE IF NOT EXISTS workflow_activity (
                thread_id TEXT PRIMARY KEY REFERENCES workflows(id) ON DELETE CASCADE,
                last_activity_at TEXT NOT NULL, expires_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS expired_requests (
                request_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS context_expirations (
                thread_id TEXT PRIMARY KEY, record_ids TEXT NOT NULL,
                state TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revision_context (
                event_id TEXT NOT NULL, version INTEGER NOT NULL, op_key TEXT NOT NULL,
                PRIMARY KEY(event_id, version),
                FOREIGN KEY(event_id, version) REFERENCES revisions(event_id, version) ON DELETE CASCADE);
        ''')
        from ..jobs.schema import SCHEMA as JOB_SCHEMA, migrate_legacy
        execute_schema(connection, JOB_SCHEMA)
        if connection.execute('PRAGMA user_version').fetchone()[0] < 8:
            migrate_legacy(connection)
        from ..observability.schema import SCHEMA as QUALITY_SCHEMA, install_triggers
        execute_schema(connection, QUALITY_SCHEMA)
        install_triggers(connection)
        connection.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
        connection.commit()
    except BaseException:
        connection.rollback()
        connection.close()
        raise
    return connection
