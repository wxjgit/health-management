"""Health event, revision and completion persistence."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from ...common.clock import local_day, stamp
from .common import dump
from ...privacy.fragments import minimize_event


class EventRepositoryMixin:
    @staticmethod
    def event_row(row):
        if row is None:
            return None
        return {**dict(row), 'data': json.loads(row['data'])}

    def events(self, start=None, end=None, kind=None, search=None, limit=None):
        where, args = [], []
        for column, op, value in [
            ('day', '>=', start),
            ('day', '<=', end),
            ('kind', '=', kind),
        ]:
            if value:
                where.append(f'{column}{op}?')
                args.append(value)
        if search:
            where.append('raw_text LIKE ?')
            args.append(f'%{search}%')
        query = 'SELECT * FROM events' + (
            ' WHERE ' + ' AND '.join(where) if where else ''
        )
        query += ' ORDER BY occurred_at DESC, created_at DESC'
        if limit:
            query += ' LIMIT ?'
            args.append(limit)
        with self.lock:
            records = [
                self.event_row(row)
                for row in self.conn.execute(query, args).fetchall()
            ]
            self.observe_scope('events', start=start, end=end, event_kind=kind, search=search, limit=limit)
            for record in records:
                self.observe('event', record['id'], record['version'])
            return records

    def event(self, event_id):
        with self.lock:
            record = self.event_row(self.conn.execute(
                'SELECT * FROM events WHERE id=?', (event_id,)
            ).fetchone())
            if record:
                self.observe('event', record['id'], record['version'])
            return record

    def save_events(self, drafts, op_key, correction_id=None):
        with self.lock, self.conn:
            old = self.conn.execute(
                'SELECT result FROM operations WHERE key=?', (op_key,)
            ).fetchone()
            if old:
                return json.loads(old['result'])
            saved = []
            if correction_id and len(drafts) != 1:
                raise ValueError('一次纠错只能对应一条记录')
            for index, draft in enumerate(drafts):
                event_id = correction_id or str(uuid4())
                prior = self.event(event_id) if correction_id else None
                if correction_id and prior is None:
                    raise ValueError('要修正的记录已不存在')
                revision = prior['version'] + 1 if prior else 1
                occurred = datetime.fromisoformat(
                    draft['occurred_at']
                ).astimezone(timezone.utc).isoformat()
                draft = {**draft, 'occurred_at': occurred}
                original_draft = draft
                try:
                    draft = minimize_event(draft, sources_only=True)
                except ValueError:
                    # Legacy/imported inputs remain explicitly indexed; they
                    # must not be silently upgraded to verified field evidence.
                    pass
                data = dump(draft)
                if prior:
                    self.conn.execute(
                        'UPDATE events SET kind=?, occurred_at=?, day=?, raw_text=?, data=?, version=? WHERE id=?',
                        (
                            draft['kind'],
                            occurred,
                            local_day(occurred),
                            draft['raw_text'],
                            data,
                            revision,
                            event_id,
                        ),
                    )
                else:
                    self.conn.execute(
                        'INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)',
                        (
                            event_id,
                            f'{op_key}:{index}',
                            draft['kind'],
                            occurred,
                            local_day(occurred),
                            draft['raw_text'],
                            data,
                            revision,
                            stamp(),
                        ),
                    )
                self.conn.execute(
                    'INSERT INTO revisions VALUES (?,?,?,?,?,?)',
                    (
                        event_id,
                        revision,
                        data,
                        draft['raw_text'],
                        '纠正记录' if prior else '首次记录',
                        stamp(),
                    ),
                )
                saved.append(event_id)
                self.invalidate_dependencies('event', event_id, before=prior,
                                             after={'day': local_day(occurred), 'kind': draft['kind'], 'raw_text': draft['raw_text']})
                self.register_event_provenance(event_id, revision, original_draft, op_key, stored=draft, prior=prior)
            self.conn.execute(
                'INSERT INTO operations VALUES (?,?)', (op_key, dump(saved))
            )
            if saved:
                self._bump()
                self.persist_provenance('operation', op_key, sources=[
                    self.provenance_node('event', event_id) for event_id in saved
                ])
            return saved

    def mark_complete(self, day, complete):
        with self.lock, self.conn:
            self.conn.execute(
                'INSERT OR REPLACE INTO meta VALUES (?,?)',
                (f'complete:{day}', dump(complete)),
            )
            self._bump()
            self.invalidate_dependencies('meta', f'complete:{day}')
            self.provenance_node('meta', f'complete:{day}', stamp(), state='complete')

    def delete_event(self, event_id):
        raise RuntimeError('请通过 engine.delete_event 执行可恢复的隐私清理')
