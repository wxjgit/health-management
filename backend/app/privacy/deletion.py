"""Plan exact deletion, fence writes, and recover two-database cleanup."""

from ..common.execution_budget import runtime_operation

import hashlib
import json
from uuid import uuid4

from ..common.clock import stamp
from ..infrastructure.repositories.common import dump
from .fragments import minimize_event
from .provenance import untracked


class DeletionError(RuntimeError):
    def __init__(self, code, message, status=409, preview=None):
        super().__init__(message)
        self.code, self.status, self.preview = code, status, preview


TABLES = {
    'event': ('events', 'id'), 'fact': ('facts', 'id'),
    'workflow': ('workflows', 'id'), 'message': ('messages', 'id'),
    'plan': ('plans', 'id'), 'insight': ('insights', 'id'),
    'review': ('reviews', 'id'), 'notification': ('notifications', 'id'),
    'operation': ('operations', 'key'), 'summary': ('monthly_summaries', 'month'),
}


class PrivacyDeletionService:
    def __init__(self, engine):
        self.engine, self.repo = engine, engine.repo

    def _exists(self, kind, object_id):
        if kind == 'checkpoint':
            return next(self.engine.checkpointer.list({'configurable': {'thread_id': object_id}}, limit=1), None) is not None
        if kind not in TABLES:
            return False
        table, column = TABLES[kind]
        return bool(self.repo.conn.execute(f'SELECT 1 FROM {table} WHERE {column}=?', (object_id,)).fetchone())

    def _archived(self, kind, object_id):
        return kind == 'event' and bool(self.repo.conn.execute(
            "SELECT 1 FROM provenance_nodes WHERE object_type=? AND object_id=? AND lifecycle='archived'", (kind, object_id),
        ).fetchone())

    def _forbidden_spans(self, kind, object_id):
        bounds = {}
        if kind == 'event':
            for node in self.repo.conn.execute(
                "SELECT node_id FROM provenance_nodes WHERE object_type='event' AND object_id=?", (object_id,),
            ):
                for origin, locator in self.repo.origin_locators(node[0]):
                    if locator['field'] != 'occurred_at':
                        group = bounds.setdefault(origin, {'hash': None, 'spans': []})
                        group['spans'].append((locator['start'], locator['end']))
            versions = self.repo.conn.execute('SELECT data FROM revisions WHERE event_id=?', (object_id,)).fetchall()
            for row in versions:
                data = json.loads(row[0])
                for entry in data.get('evidence', []):
                    if entry.get('field') == 'occurred_at' or entry.get('start') is None:
                        continue
                    source_id = entry.get('source_message_id')
                    if source_id:
                        group = bounds.setdefault(source_id, {'hash': entry['source_text_hash'], 'spans': []})
                        group['spans'].append((entry['start'], entry['end']))
        else:
            fact = self.repo.conn.execute('SELECT content FROM facts WHERE id=?', (object_id,)).fetchone()
            contents = [fact[0]] if fact else []
            contents.extend(json.loads(row[0])['content'] for row in self.repo.conn.execute(
                'SELECT data FROM fact_revisions WHERE fact_id=?', (object_id,)))
            for row in self.repo.conn.execute(
                'SELECT o.source_id FROM provenance_occurrences o JOIN provenance_nodes n ON n.node_id=o.holder_node_id '
                "WHERE n.object_type='fact' AND n.object_id=?", (object_id,),
            ):
                message = self.repo.conn.execute('SELECT text FROM messages WHERE id=?', (row[0],)).fetchone()
                if message and fact:
                    text, spans = message[0], []
                    for content in set(contents):
                        offset = 0
                        while (offset := text.find(content, offset)) >= 0:
                            spans.append((offset, offset + len(content)))
                            offset += max(1, len(content))
                    bounds[row[0]] = {'hash': hashlib.sha256(text.encode()).hexdigest(), 'spans': spans}
        return bounds

    def _verify_independent(self, event_id, version, data, forbidden):
        node = self.repo.provenance_node('event', event_id, version)
        for origin, locator in self.repo.origin_locators(node):
            if origin in forbidden and any(locator['start'] < end and start < locator['end']
                                           for start, end in forbidden[origin]['spans']):
                raise ValueError('记录字段共享待删除内容，不能独立保留')
        return minimize_event(data, forbidden)

    def _plan(self, kind, object_id):
        repo = self.repo
        if kind not in ('event', 'fact'):
            raise DeletionError('invalid_target', '只能删除健康事件或长期信息', 422)
        if not self._exists(kind, object_id) and not self._archived(kind, object_id):
            raise DeletionError('not_found', '记录不存在', 404)
        roots = {row[0] for row in repo.conn.execute(
            'SELECT node_id FROM provenance_nodes WHERE object_type=? AND object_id=?', (kind, object_id),
        )}
        if kind == 'event':
            record = repo.event(object_id)
            if record:
                roots.update(repo.matching_scopes(kind, record))
            for fact in repo.conn.execute('SELECT id FROM facts WHERE source_event=?', (object_id,)):
                roots.update(row[0] for row in repo.conn.execute(
                    "SELECT node_id FROM provenance_nodes WHERE object_type='fact' AND object_id=?", (fact['id'],),
                ))
        else:
            roots.update(repo.matching_scopes(kind, None))
        source_ids = set()
        for node in list(roots):
            source_ids.update(row[0] for row in repo.conn.execute(
                'SELECT source_id FROM provenance_occurrences WHERE holder_node_id=?', (node,),
            ))
        sanitize, expanded = set(), set()
        forbidden = self._forbidden_spans(kind, object_id)
        # Source ownership is separate from computation dependency: a sibling
        # event survives if its own evidence can be verified after minimization.
        for source_id in source_ids:
            for row in repo.conn.execute(
                'SELECT n.* FROM provenance_occurrences o JOIN provenance_nodes n ON n.node_id=o.holder_node_id '
                'WHERE o.source_id=? AND n.lifecycle!=?', (source_id, 'deleted'),
            ):
                holder_kind, holder_id = row['object_type'], row['object_id']
                if holder_kind == 'event' and (kind, object_id) != ('event', holder_id):
                    roots.update(dependency[0] for dependency in repo.conn.execute(
                        'SELECT target_node_id FROM provenance_dependencies WHERE source_node_id=?', (row['node_id'],),
                    ))
                    versions = repo.conn.execute('SELECT version,data FROM revisions WHERE event_id=?', (holder_id,)).fetchall()
                    try:
                        if not forbidden:
                            raise ValueError('无法确定共享原文中的目标边界')
                        for version in versions:
                            self._verify_independent(holder_id, version['version'], json.loads(version['data']), forbidden)
                        if not versions:
                            raise ValueError('缺少事件修订')
                        sanitize.add(holder_id)
                    except ValueError:
                        expanded.add(holder_id)
                        roots.update(node[0] for node in repo.conn.execute(
                            "SELECT node_id FROM provenance_nodes WHERE object_type='event' AND object_id=?", (holder_id,),
                        ))
                elif holder_kind == 'fact' and (kind, object_id) != ('fact', holder_id):
                    roots.add(row['node_id'])
                    expanded.add(f'fact:{holder_id}')
                elif holder_kind not in ('event', 'fact'):
                    roots.add(row['node_id'])
        unknown = []
        for row in repo.conn.execute(
            "SELECT * FROM provenance_nodes WHERE provenance_state='unknown' AND is_current=1 AND lifecycle!='deleted'",
        ):
            if row['object_type'] == 'event' and (kind, object_id) != ('event', row['object_id']) and self._exists('event', row['object_id']):
                versions = repo.conn.execute('SELECT version,data FROM revisions WHERE event_id=?', (row['object_id'],)).fetchall()
                try:
                    if not forbidden:
                        raise ValueError('无法证明历史记录与目标内容独立')
                    if not versions:
                        raise ValueError('缺少独立来源')
                    for version in versions:
                        self._verify_independent(row['object_id'], version['version'], json.loads(version['data']), forbidden)
                    sanitize.add(row['object_id'])
                    for version_node in repo.conn.execute(
                        "SELECT node_id FROM provenance_nodes WHERE object_type='event' AND object_id=?", (row['object_id'],),
                    ):
                        roots.update(edge[0] for edge in repo.conn.execute(
                            'SELECT target_node_id FROM provenance_dependencies WHERE source_node_id=?', (version_node[0],),
                        ))
                except ValueError:
                    expanded.add(row['object_id'])
                    roots.update(node[0] for node in repo.conn.execute(
                        "SELECT node_id FROM provenance_nodes WHERE object_type='event' AND object_id=?", (row['object_id'],),
                    ))
                unknown.append(row['node_id'])
            elif row['object_type'] == 'fact' and (kind, object_id) != ('fact', row['object_id']) and self._exists('fact', row['object_id']):
                roots.add(row['node_id'])
                roots.update(repo.matching_scopes('fact', None))
                expanded.add(f"fact:{row['object_id']}")
                unknown.append(row['node_id'])
            elif row['object_type'] not in ('event', 'fact', 'meta') and self._exists(row['object_type'], row['object_id']):
                roots.add(row['node_id'])
                unknown.append(row['node_id'])
        affected = repo.dependency_closure(roots)
        items = {}
        threads = set()
        for node in affected:
            row = repo.conn.execute('SELECT * FROM provenance_nodes WHERE node_id=?', (node,)).fetchone()
            item_kind, item_id = row['object_type'], row['object_id']
            if not self._exists(item_kind, item_id):
                continue
            if item_kind in ('workflow', 'checkpoint'):
                threads.add(item_id)
            if row['is_current'] or item_kind in ('event', 'fact'):
                items[(item_kind, item_id, row['part'])] = {'kind': item_kind, 'id': item_id, 'part': row['part'], 'action': 'delete'}
        # Thread deletion includes all recovery copies, even if created before
        # lineage migration or committed in a different step of that workflow.
        for thread in threads:
            for message in repo.conn.execute('SELECT id FROM messages WHERE thread_id=?', (thread,)):
                items[('message', message['id'], 'whole')] = {'kind': 'message', 'id': message['id'], 'part': 'whole', 'action': 'delete'}
        for event_id in sanitize:
            if ('event', event_id, 'whole') not in items:
                items[('event', event_id, 'whole')] = {'kind': 'event', 'id': event_id, 'part': 'whole', 'action': 'minimize'}
        for item in items.values():
            if item['kind'] == 'plan' and item['part'] == 'whole':
                target_node = repo.conn.execute(
                    "SELECT * FROM provenance_nodes WHERE object_type='plan' AND object_id=? AND part='targets' AND is_current=1",
                    (item['id'],),
                ).fetchone()
                plan = repo.plan(item['id'])
                if (plan['status'] == 'active' and target_node and target_node['provenance_state'] == 'complete'
                        and target_node['node_id'] not in affected):
                    item['action'] = 'keep_targets'
        for item in list(items.values()):
            if item['kind'] == 'plan' and item['part'] == 'targets':
                items[('plan', item['id'], 'whole')] = {'kind': 'plan', 'id': item['id'], 'part': 'whole', 'action': 'delete'}
                del items[('plan', item['id'], 'targets')]
        counts = {}
        for item in items.values():
            key = f"{item['kind']}:{item['action']}"
            counts[key] = counts.get(key, 0) + 1
        for table in ('receipts', 'resume_attempts'):
            counts[table] = sum(repo.conn.execute(f'SELECT COUNT(*) FROM {table} WHERE thread_id=?', (thread,)).fetchone()[0] for thread in threads)
        manifest = {
            'target_type': kind, 'target_id': object_id,
            'nodes': sorted(affected), 'threads': sorted(threads),
            'items': sorted(items.values(), key=lambda item: (item['kind'], item['id'], item['part'])),
            'counts': counts, 'scope': 'legacy_context' if unknown else 'shared_source' if expanded else 'dependencies',
            'reasons': (['历史数据缺少完整来源关系，需要扩大清理范围；可独立验证的健康记录仅清除原文副本，不能验证的记录和长期信息会一并删除。'] if unknown else [])
                       + (['部分共享原文无法独立验证，相关记录需要一并删除。'] if expanded else []),
            'versions': sorted((row['node_id'], row['version']) for row in repo.conn.execute(
                'SELECT * FROM provenance_nodes WHERE is_current=1') if row['node_id'] in affected
                or (row['object_type'] == 'event' and row['object_id'] in sanitize)),
        }
        manifest['scheduled_jobs'] = repo.jobs_for_deletion(manifest['items'], threads, kind, object_id)
        manifest['counts']['scheduled_jobs'] = len(manifest['scheduled_jobs'])
        manifest['counts']['outbox_events'] = sum(repo.conn.execute(
            'SELECT count(*) FROM outbox_events WHERE source_job_id=?', (job_id,)).fetchone()[0]
            for job_id in manifest['scheduled_jobs'])
        subjects = {(item['kind'], item['id']) for item in manifest['items'] if item['kind'] in ('event', 'workflow', 'plan')}
        subjects.update(('workflow', thread) for thread in threads)
        manifest['quality_subjects'] = sorted(subjects)
        manifest['counts']['quality_observations'] = sum(repo.conn.execute(
            'SELECT count(*) FROM quality_observations WHERE subject_type=? AND subject_id=?', subject).fetchone()[0]
            for subject in subjects)
        manifest['scope_token'] = hashlib.sha256(dump(manifest).encode()).hexdigest()
        return manifest

    @runtime_operation('management')
    def preview(self, kind, object_id):
        with self.engine.lock, untracked():
            manifest = self._plan(kind, object_id)
            preview_id = str(uuid4())
            with self.repo.conn:
                self.repo.conn.execute('INSERT INTO deletion_previews VALUES (?,?,?,?,?)',
                                       (preview_id, kind, object_id, manifest['scope_token'], stamp()))
            return self._public(manifest, preview_id=preview_id)

    @staticmethod
    def _public(manifest, **extra):
        return {key: manifest[key] for key in ('target_type', 'target_id', 'counts', 'scope', 'reasons')} | extra

    @runtime_operation('management')
    def delete(self, kind, object_id, *, preview_id=None, request_id=None, require_preview=False):
        request_id = request_id or str(uuid4())
        with self.engine.lock, untracked():
            job = self.repo.conn.execute('SELECT * FROM deletion_jobs WHERE request_id=?', (request_id,)).fetchone()
            if job:
                if (job['target_type'], job['target_id']) != (kind, object_id):
                    raise DeletionError('request_mismatch', '同一个删除请求编号不能用于不同目标')
                return self._finish(job['id'])
            if not self._exists(kind, object_id) and not self._archived(kind, object_id):
                job = self.repo.conn.execute(
                    'SELECT * FROM deletion_jobs WHERE target_type=? AND target_id=? ORDER BY created_at DESC LIMIT 1',
                    (kind, object_id),
                ).fetchone()
                if job:
                    return self._finish(job['id'])
                raise DeletionError('not_found', '记录不存在', 404)
            manifest = self._plan(kind, object_id)
            if preview_id:
                preview = self.repo.conn.execute('SELECT * FROM deletion_previews WHERE id=?', (preview_id,)).fetchone()
                if (not preview or (preview['target_type'], preview['target_id']) != (kind, object_id)
                        or preview['scope_token'] != manifest['scope_token']):
                    raise DeletionError('scope_changed', '清理范围已变化，请重新查看影响范围。', preview=self.preview(kind, object_id))
            elif require_preview or manifest['scope'] != 'dependencies':
                raise DeletionError('preview_required', '请先查看并确认删除范围。', 428, self.preview(kind, object_id))
            deletion_id = str(uuid4())
            with self.repo.conn:
                self.repo.conn.execute('INSERT INTO deletion_jobs VALUES (?,?,?,?,?,?,?,NULL)',
                                       (deletion_id, request_id, kind, object_id, 'business_pending', dump(manifest), stamp()))
                for node in manifest['nodes']:
                    self.repo.conn.execute("UPDATE provenance_nodes SET lifecycle='deleting' WHERE node_id=?", (node,))
                for thread in manifest['threads']:
                    self.repo.conn.execute('INSERT OR IGNORE INTO workflow_generations VALUES (?,0)', (thread,))
                    self.repo.conn.execute('UPDATE workflow_generations SET generation=generation+1 WHERE thread_id=?', (thread,))
                    self.repo.conn.execute('INSERT INTO deletion_items VALUES (?,?,?)', (deletion_id, thread, 'pending'))
            return self._finish(deletion_id)

    def _erase_business(self, job, manifest):
        repo = self.repo
        for subject in manifest.get('quality_subjects', []):
            repo.conn.execute('DELETE FROM quality_observations WHERE subject_type=? AND subject_id=?', subject)
        keys = {row['logical_key'] for job_id in manifest.get('scheduled_jobs', [])
                if (row := repo.scheduled_job(job_id))}
        for key in keys:
            self._reset_job(key)
        retained_plans = []
        for thread in manifest['threads']:
            repo.conn.execute('DELETE FROM context_expirations WHERE thread_id=?', (thread,))
            requests = {row[0] for row in repo.conn.execute('SELECT request_id FROM workflows WHERE id=?', (thread,))}
            for table in ('receipts', 'resume_attempts'):
                requests.update(row[0] for row in repo.conn.execute(f'SELECT request_id FROM {table} WHERE thread_id=?', (thread,)))
            requests.update(row[0] for row in repo.conn.execute("SELECT id FROM messages WHERE thread_id=? AND role='user'", (thread,)))
            requests.update(row[0] for row in repo.conn.execute(
                'SELECT o.source_id FROM provenance_occurrences o JOIN provenance_nodes n ON n.node_id=o.holder_node_id '
                "WHERE n.object_type='checkpoint' AND n.object_id=? AND o.field_path='request_id'", (thread,),
            ))
            for request in requests:
                repo.conn.execute('INSERT OR IGNORE INTO request_tombstones VALUES (?,?)', (request, job['id']))
            for table in ('messages', 'receipts', 'resume_attempts', 'model_calls'):
                repo.conn.execute(f'DELETE FROM {table} WHERE thread_id=?', (thread,))
            repo.conn.execute('DELETE FROM workflows WHERE id=?', (thread,))
        for item in manifest['items']:
            kind, object_id, action = item['kind'], item['id'], item['action']
            if kind == 'checkpoint':
                continue  # The saver owns its physical tables; deletion_items drives cleanup.
            if kind == 'plan' and item['part'] == 'targets':
                continue  # Whole-object action decides whether any target can survive.
            if action == 'minimize':
                self._minimize(object_id)
            elif action == 'keep_targets':
                plan = repo.plan(object_id)
                data = {
                    'targets': plan['data']['targets'], 'confirmed_anchor': plan['data'].get('confirmed_anchor', plan['data']['targets']),
                    'target_source': plan['data'].get('target_source', 'manual'),
                    'target_metadata': None, 'schedule': [], 'evidence_refs': [],
                    'next_meal': '相关来源已删除，请重新生成安排。', 'tomorrow_focus': '请重新生成安排。',
                    'notes': '独立数值目标保留，受影响的安排已清理。', 'needs_confirmation': False,
                    'changes': {}, 'data_version': repo.version(),
                }
                repo.conn.execute('UPDATE plans SET data=? WHERE id=?', (dump(data), object_id))
                retained_plans.append(object_id)
            else:
                if kind == 'event':
                    for expired in repo.conn.execute('SELECT thread_id,record_ids FROM context_expirations').fetchall():
                        ids = json.loads(expired['record_ids'])
                        if object_id in ids:
                            repo.conn.execute('UPDATE context_expirations SET record_ids=? WHERE thread_id=?',
                                              (dump([value for value in ids if value != object_id]), expired['thread_id']))
                if kind == 'notification':
                    row = repo.conn.execute('SELECT dedupe_key FROM notifications WHERE id=?', (object_id,)).fetchone()
                    if row:
                        self._reset_job(row[0])
                elif kind == 'review':
                    period, _, end = object_id.partition(':')
                    self._reset_job(f"{'daily' if period == 'day' else 'weekly'}:{end}")
                table, column = TABLES[kind]
                if kind == 'fact':
                    repo.erase_fact_links(object_id)
                repo.conn.execute(f'DELETE FROM {table} WHERE {column}=?', (object_id,))
        for node in manifest['nodes']:
            repo.conn.execute("UPDATE provenance_nodes SET lifecycle='deleted' WHERE node_id=?", (node,))
            repo.conn.execute('DELETE FROM provenance_scopes WHERE target_node_id=?', (node,))
            repo.conn.execute('DELETE FROM provenance_occurrences WHERE holder_node_id=?', (node,))
        for plan_id in retained_plans:
            repo.persist_provenance('plan', plan_id, sources=[repo.provenance_node('plan', plan_id, part='targets')],
                                    use_reads=False)
        for batch in manifest.get('archive_batches', []):
            repo.conn.execute('INSERT OR REPLACE INTO monthly_summaries VALUES (?,?,?)',
                              (batch['month'], dump(batch['data']), stamp()))
            sources = []
            for event_id in batch['event_ids']:
                repo.conn.execute("UPDATE provenance_nodes SET lifecycle='archived' WHERE object_type='event' AND object_id=?", (event_id,))
                sources.extend(row[0] for row in repo.conn.execute(
                    "SELECT node_id FROM provenance_nodes WHERE object_type='event' AND object_id=? AND is_current=1", (event_id,),
                ))
            repo.persist_provenance('summary', batch['month'], sources=sources, use_reads=False)
            summary_node = repo.provenance_node('summary', batch['month'])
            repo.conn.execute('UPDATE monthly_summaries SET data=? WHERE month=?',
                              (dump({**batch['data'], **repo.dependency_metadata(summary_node)}), batch['month']))
            repo.conn.execute("DELETE FROM meta WHERE key LIKE ?", (f"complete:{batch['month']}-%",))
        # No broad insight invalidation: independent reports stay usable.
        repo._bump()

    def _reset_job(self, key):
        if key.startswith('auto-plan:'):
            key = key[len('auto-plan:'):]
        self.repo.reset_scheduled_key(key)
        self.repo.conn.execute('INSERT INTO job_generations VALUES (?,1) ON CONFLICT(key) DO UPDATE SET generation=generation+1', (key,))

    def _minimize(self, event_id):
        repo = self.repo
        event = repo.event(event_id)
        for row in repo.conn.execute('SELECT version,data FROM revisions WHERE event_id=?', (event_id,)).fetchall():
            previous = json.loads(row['data'])
            data = minimize_event(previous)
            repo.conn.execute('UPDATE revisions SET data=?,raw_text=? WHERE event_id=? AND version=?',
                              (dump(data), data['raw_text'], event_id, row['version']))
            node = repo.provenance_node('event', event_id, row['version'])
            origins = repo.origin_locators(node)
            for entry in previous.get('evidence', []):
                if entry.get('start') is not None and entry.get('source_message_id') and not any(
                    locator['fragment_id'] == entry['source_message_id'] and locator['field'] == entry['field']
                    and locator.get('item_index') == entry.get('item_index') for _, locator in origins
                ):
                    origins.append((entry['source_message_id'], {
                        'field': entry['field'], 'item_index': entry.get('item_index'),
                        'start': entry['start'], 'end': entry['end'], 'fragment_id': entry['source_message_id'],
                    }))
            mapping = {old['message_id']: new['message_id'] for old, new in zip(previous['evidence_sources'], data['evidence_sources'])}
            repo.conn.execute("UPDATE provenance_nodes SET provenance_state='complete' WHERE node_id=?", (node,))
            repo.conn.execute('DELETE FROM provenance_occurrences WHERE holder_node_id=?', (node,))
            for origin, locator in origins:
                repo.link_source(node, origin, dump({**locator, 'fragment_id': mapping.get(locator['fragment_id'], locator['fragment_id'])}))
            for source in data['evidence_sources']:
                repo.link_source(node, source['message_id'], 'evidence_sources')
            if row['version'] == event['version']:
                repo.conn.execute('UPDATE events SET data=?,raw_text=? WHERE id=?', (dump(data), data['raw_text'], event_id))

    def _finish(self, deletion_id):
        repo = self.repo
        job = repo.conn.execute('SELECT * FROM deletion_jobs WHERE id=?', (deletion_id,)).fetchone()
        manifest = json.loads(job['manifest'])
        try:
            if job['state'] == 'business_pending':
                with repo.conn:
                    self._erase_business(job, manifest)
                    # Aggregate payloads are recovery input only, not an unmanaged copy.
                    manifest.pop('archive_batches', None)
                    repo.conn.execute("UPDATE deletion_jobs SET state='checkpoint_pending',manifest=? WHERE id=?", (dump(manifest), deletion_id))
            if job['state'] != 'completed':
                for item in repo.conn.execute('SELECT * FROM deletion_items WHERE deletion_id=? AND state!=?', (deletion_id, 'done')).fetchall():
                    self.engine.checkpointer.delete_thread(item['thread_id'])
                    with repo.conn:
                        repo.conn.execute("UPDATE deletion_items SET state='done' WHERE deletion_id=? AND thread_id=?", (deletion_id, item['thread_id']))
                self.engine.cp_conn.commit()
                self._compact(self.engine.cp_conn)
                repo.compact()
                with repo.conn:
                    repo.conn.execute("UPDATE deletion_jobs SET state='completed',completed_at=? WHERE id=?", (stamp(), deletion_id))
        except Exception:
            # Preserve the durable job and fences; retry cannot re-expose data.
            return self._public(manifest, deletion_id=deletion_id, state='pending', deleted=False)
        return self._public(manifest, deletion_id=deletion_id, state='completed', deleted=True, context_cleared=bool(manifest['threads']))

    @staticmethod
    def _compact(connection):
        if connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
            raise RuntimeError('数据库正忙，清理尚未完成')
        connection.execute('VACUUM')
        if connection.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]:
            raise RuntimeError('数据库正忙，清理尚未完成')

    def recover(self):
        with self.engine.lock, untracked():
            jobs = self.repo.conn.execute("SELECT id FROM deletion_jobs WHERE state!='completed'").fetchall()
            for job in jobs:
                result = self._finish(job['id'])
                if result['state'] != 'completed':
                    raise RuntimeError('隐私清理尚未完成，请重试启动服务')

    def inventory_checkpoints(self):
        """Unmanaged historical/orphan saver state is unknown, never assumed safe."""
        with self.engine.lock, self.repo.conn, untracked():
            for item in self.engine.checkpointer.list(None):
                thread = item.config['configurable']['thread_id']
                if self.repo.workflow(thread):
                    continue
                node = self.repo.provenance_node('checkpoint', thread)
                request = item.checkpoint.get('channel_values', {}).get('source_message_id')
                if isinstance(request, str):
                    self.repo.link_source(node, request, 'request_id')

    def archive(self, batches):
        """Commit verified aggregates and all raw-data erasure in one DB transaction."""
        with self.engine.lock, untracked():
            plans = [self._plan('event', event['id']) for _, events, _ in batches for event in events]
            items, nodes, threads = {}, set(), set()
            for plan in plans:
                nodes.update(plan['nodes'])
                threads.update(plan['threads'])
                for item in plan['items']:
                    key = (item['kind'], item['id'], item['part'])
                    if key not in items or item['action'] == 'delete':
                        items[key] = dict(item)
            # All originals in the batch must be deleted, never minimized.
            archive_batches = []
            for month, events, data in batches:
                if data['record_count'] != len(events):
                    raise ValueError('月度摘要校验失败，保留原记录')
                archive_batches.append({'month': month, 'event_ids': [event['id'] for event in events], 'data': data})
                for event in events:
                    items[('event', event['id'], 'whole')] = {'kind': 'event', 'id': event['id'], 'part': 'whole', 'action': 'delete'}
            manifest = {'target_type': 'archive', 'target_id': ','.join(batch['month'] for batch in archive_batches),
                        'nodes': sorted(nodes), 'threads': sorted(threads), 'items': list(items.values()),
                        'archive_batches': archive_batches, 'counts': {}, 'scope': 'retention', 'reasons': []}
            manifest['scheduled_jobs'] = sorted({job_id for plan in plans for job_id in plan.get('scheduled_jobs', [])})
            deletion_id = str(uuid4())
            with self.repo.conn:
                self.repo.conn.execute('INSERT INTO deletion_jobs VALUES (?,?,?,?,?,?,?,NULL)',
                                       (deletion_id, deletion_id, 'archive', manifest['target_id'], 'business_pending', dump(manifest), stamp()))
                for node in nodes:
                    self.repo.conn.execute("UPDATE provenance_nodes SET lifecycle='deleting' WHERE node_id=?", (node,))
                for thread in threads:
                    self.repo.conn.execute('INSERT OR IGNORE INTO workflow_generations VALUES (?,0)', (thread,))
                    self.repo.conn.execute('UPDATE workflow_generations SET generation=generation+1 WHERE thread_id=?', (thread,))
                    self.repo.conn.execute('INSERT INTO deletion_items VALUES (?,?,?)', (deletion_id, thread, 'pending'))
            if self._finish(deletion_id)['state'] != 'completed':
                raise RuntimeError('归档清理尚未完成')
            return [batch['month'] for batch in archive_batches]

    @runtime_operation('management')
    def status(self, deletion_id, *, retry=False):
        with self.engine.lock, untracked():
            job = self.repo.conn.execute('SELECT * FROM deletion_jobs WHERE id=?', (deletion_id,)).fetchone()
            if not job:
                raise DeletionError('not_found', '删除任务不存在', 404)
            if retry:
                return self._finish(deletion_id)
            return self._public(json.loads(job['manifest']), deletion_id=deletion_id,
                                state='completed' if job['state'] == 'completed' else 'pending',
                                deleted=job['state'] == 'completed')
