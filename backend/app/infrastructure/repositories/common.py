"""Repository connection, metadata and shared versioning primitives."""

from ...common.errors import WorkflowStateError

import json
from pathlib import Path
from threading import RLock
from ...common.execution_budget import BudgetLock

from ...common.clock import stamp
from ...contracts.profile import Preferences
from ..database import open_database


def dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


class RepositoryBase:
    def __init__(self, path: Path):
        self.lock = BudgetLock()
        self.conn = open_database(path)
        if self.get_meta('preferences') is None:
            self.set_meta('preferences', Preferences().model_dump())
        if self.get_meta('data_version') is None:
            self.set_meta('data_version', 0)
        self.migrate_provenance()

    def close(self):
        self.conn.close()

    def account_model_call(self, thread_id, purpose, payload_bytes):
        """Reserve the workflow budget before dispatch, also for failed/retried calls.

        Only metadata is kept; neither request bodies nor health text are logged.
        """
        with self.lock, self.conn:
            count, size = self.conn.execute(
                'SELECT COUNT(*), COALESCE(SUM(payload_bytes),0) FROM model_calls WHERE thread_id=?',
                (thread_id,),
            ).fetchone()
            if count >= 32 or size + payload_bytes > 400000:
                raise WorkflowStateError(code='model_budget_exceeded')
            if purpose == 'safety_classification':
                checks = self.conn.execute(
                    "SELECT COUNT(*) FROM model_calls WHERE thread_id=? AND purpose='safety_classification'",
                    (thread_id,),
                ).fetchone()[0]
                if checks >= 8:
                    raise WorkflowStateError(code='model_budget_exceeded')
            if purpose == 'processing_plan':
                plans = self.conn.execute(
                    "SELECT COUNT(*) FROM model_calls WHERE thread_id=? AND purpose='processing_plan'",
                    (thread_id,),
                ).fetchone()[0]
                if plans >= 4:
                    raise WorkflowStateError(code='model_budget_exceeded')
            self.conn.execute(
                'INSERT INTO model_calls VALUES (?,?,?,?)',
                (thread_id, purpose, payload_bytes, stamp()),
            )

    def get_meta(self, key, default=None):
        with self.lock:
            row = self.conn.execute(
                'SELECT value FROM meta WHERE key=?', (key,)
            ).fetchone()
            if key in ('profile', 'preferences') or key.startswith('complete:'):
                self.observe('meta', key)
            return json.loads(row['value']) if row else default

    def set_meta(self, key, value):
        with self.lock, self.conn:
            previous = self.conn.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
            self.conn.execute(
                'INSERT OR REPLACE INTO meta VALUES (?,?)', (key, dump(value))
            )
            if (not previous or previous[0] != dump(value)) and (key in ('profile', 'preferences') or key.startswith('complete:')):
                self.invalidate_dependencies('meta', key)
                self.provenance_node('meta', key, stamp(), state='complete')

    def version(self):
        return self.get_meta('data_version', 0)

    def _bump(self):
        value = self.version() + 1
        self.conn.execute(
            'INSERT OR REPLACE INTO meta VALUES (?,?)',
            ('data_version', dump(value)),
        )

    def update_profile(self, value):
        with self.lock, self.conn:
            self.conn.execute(
                'INSERT OR REPLACE INTO meta VALUES (?,?)',
                ('profile', dump(value)),
            )
            self._bump()
            self.invalidate_dependencies('meta', 'profile')
            self.provenance_node('meta', 'profile', stamp(), state='complete')
