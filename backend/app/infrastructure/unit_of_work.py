"""Explicit outer write transactions; nested repository contexts use savepoints."""
from contextlib import contextmanager
import sqlite3


@contextmanager
def atomic(repo):
    with repo.lock:
        outer = not repo.conn.in_transaction
        previous = None
        if outer:
            previous = repo.conn.execute('PRAGMA busy_timeout').fetchone()[0]
            repo.conn.execute('PRAGMA busy_timeout=1000')
        try:
            with repo.conn:
                if not repo.conn.in_transaction:
                    repo.conn.execute('BEGIN IMMEDIATE')
                yield repo.conn
        finally:
            if previous is not None:
                # Safety cleanup must also run after the execution budget expires.
                sqlite3.Connection.execute(repo.conn, f'PRAGMA busy_timeout={int(previous)}')
