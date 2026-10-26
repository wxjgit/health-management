"""Versioned input reads, copies and dependency traversal in the business DB."""

import hashlib
import json
from contextlib import contextmanager
from uuid import uuid4

from ...privacy.provenance import ReadSet, collectors, current_reads, untracked
from .common import dump


class ProvenanceRepositoryMixin:
    @contextmanager
    def provenance_transaction(self):
        outer = self.conn.in_transaction
        try:
            yield
            if not outer:
                self.conn.commit()
        except Exception:
            if not outer:
                self.conn.rollback()
            raise

    def provenance_node(self, kind, object_id, version=None, *, part='whole', state='unknown'):
        with self.lock:
            if version is None:
                row = self.conn.execute(
                    'SELECT * FROM provenance_nodes WHERE object_type=? AND object_id=? '
                    'AND part=? AND is_current=1', (kind, object_id, part),
                ).fetchone()
                if row:
                    return row['node_id']
                version = '1'
            row = self.conn.execute(
                'SELECT node_id FROM provenance_nodes WHERE object_type=? AND object_id=? '
                'AND version=? AND part=?', (kind, object_id, str(version), part),
            ).fetchone()
            if row:
                return row['node_id']
            node_id = str(uuid4())
            self.conn.execute(
                'UPDATE provenance_nodes SET is_current=0 WHERE object_type=? AND object_id=? AND part=?',
                (kind, object_id, part),
            )
            self.conn.execute(
                'INSERT INTO provenance_nodes VALUES (?,?,?,?,?,?,?,?)',
                (node_id, kind, object_id, str(version), part, state, 'live', 1),
            )
            return node_id

    def observe(self, kind, object_id, version=None, *, part='whole'):
        active = collectors(self)
        if not active:
            return
        with self.lock, self.provenance_transaction():
            node = self.provenance_node(kind, object_id, version, part=part)
            for reads in active:
                reads.sources.add(node)
                if reads.target and reads.target != node:
                    self.conn.execute(
                        'INSERT OR IGNORE INTO provenance_dependencies VALUES (?,?)', (node, reads.target),
                    )

    def observe_scope(self, kind, **criteria):
        active = collectors(self)
        if not active:
            return
        scope = {'kind': kind, **criteria}
        encoded = dump(scope)
        key = hashlib.sha256(encoded.encode()).hexdigest()
        with self.lock, self.provenance_transaction():
            for reads in active:
                reads.scopes[key] = scope
                if reads.target:
                    self.conn.execute(
                        'INSERT OR IGNORE INTO provenance_scopes VALUES (?,?,?)',
                        (reads.target, key, encoded),
                    )

    def persist_provenance(self, kind, object_id, data=None, *, sources=None, part='whole', independent=False, use_reads=True):
        reads = (getattr(data, 'provenance', None) or current_reads(self)) if use_reads else None
        known = independent or reads is not None or sources is not None
        node = self.provenance_node(kind, object_id, str(uuid4()), part=part,
                                    state='complete' if known else 'unknown')
        inputs = set(sources or ()) | (reads.sources if reads else set())
        for source in inputs - {node}:
            self.conn.execute('INSERT OR IGNORE INTO provenance_dependencies VALUES (?,?)', (source, node))
        for key, scope in (reads.scopes.items() if reads else ()):
            self.conn.execute('INSERT OR IGNORE INTO provenance_scopes VALUES (?,?,?)', (node, key, dump(scope)))
        return node

    def node_reads(self, node):
        return ReadSet(self, sources={row[0] for row in self.conn.execute(
            'SELECT source_node_id FROM provenance_dependencies WHERE target_node_id=?', (node,),
        )}, scopes={row['scope_key']: json.loads(row['criteria']) for row in self.conn.execute(
            'SELECT * FROM provenance_scopes WHERE target_node_id=?', (node,),
        )})

    def link_source(self, node, source_id, path):
        self.conn.execute('INSERT OR IGNORE INTO provenance_occurrences VALUES (?,?,?)',
                          (source_id, node, path))

    def workflow_node(self, thread_id):
        return self.provenance_node('workflow', thread_id)

    def origin_locators(self, node):
        result = []
        for row in self.conn.execute('SELECT * FROM provenance_occurrences WHERE holder_node_id=?', (node,)):
            if row['field_path'].startswith('{'):
                result.append((row['source_id'], json.loads(row['field_path'])))
        return result

    def register_event_provenance(self, event_id, version, draft, op_key, *, stored=None, prior=None):
        node = self.provenance_node('event', event_id, version, state='complete')
        stored = stored or draft
        mapping = {old['message_id']: new['message_id'] for old, new in zip(
            draft.get('evidence_sources', []), stored.get('evidence_sources', []),
        )}
        prior_origins = self.origin_locators(self.provenance_node('event', event_id, prior['version'])) if prior else []
        for source in draft.get('evidence_sources', []):
            self.link_source(node, source['message_id'], 'evidence_sources')
        for entry in draft.get('evidence', []):
            if entry.get('start') is None or not entry.get('source_message_id'):
                continue
            source_id = entry['source_message_id']
            inherited = [(origin, locator) for origin, locator in prior_origins
                         if locator['fragment_id'] == source_id and locator['field'] == entry['field']
                         and locator.get('item_index') == entry.get('item_index')]
            if not inherited:
                inherited = [(source_id, {'field': entry['field'], 'item_index': entry.get('item_index'),
                                         'start': entry['start'], 'end': entry['end']})]
            for origin, locator in inherited:
                self.link_source(node, origin, dump({**locator, 'fragment_id': mapping.get(source_id, source_id)}))
        # Source IDs locate copies of raw_text/feedback too; they contain no
        # source text or hash and remain useful after fragment compaction.
        for origin, _ in prior_origins:
            self.link_source(node, origin, 'raw_text')
        # The creating workflow contains drafts and copies, but its other events
        # are independently persisted business records, not owned by this edge.
        for reads in collectors(self):
            reads.sources.add(node)
            if reads.target:
                self.conn.execute('INSERT OR IGNORE INTO provenance_dependencies VALUES (?,?)', (node, reads.target))
        return node

    def dependency_closure(self, roots):
        found, frontier = set(roots), set(roots)
        while frontier:
            following = set()
            for node in frontier:
                following.update(row[0] for row in self.conn.execute(
                    'SELECT target_node_id FROM provenance_dependencies WHERE source_node_id=?', (node,),
                ))
            frontier = following - found
            found.update(frontier)
        return found

    def dependency_metadata(self, node):
        ancestors, frontier = set(), {node}
        while frontier:
            previous = set()
            for current in frontier:
                previous.update(row[0] for row in self.conn.execute(
                    'SELECT source_node_id FROM provenance_dependencies WHERE target_node_id=?', (current,),
                ))
            frontier = previous - ancestors - {node}
            ancestors.update(frontier)
        versions = []
        for source in sorted(ancestors):
            row = self.conn.execute('SELECT * FROM provenance_nodes WHERE node_id=?', (source,)).fetchone()
            if row['object_type'] in ('event', 'fact'):
                versions.append({'type': row['object_type'], 'id': row['object_id'], 'version': row['version']})
        return {
            'source_event_ids': sorted({v['id'] for v in versions if v['type'] == 'event'}),
            'source_fact_ids': sorted({v['id'] for v in versions if v['type'] == 'fact'}),
            'source_version': versions,
        }

    def read_stamp(self, reads):
        """Re-evaluate the actual read set, including empty/limited queries."""
        values = []
        with untracked():
            for node in sorted(reads.sources):
                row = self.conn.execute('SELECT * FROM provenance_nodes WHERE node_id=?', (node,)).fetchone()
                latest = self.conn.execute(
                    'SELECT version,lifecycle FROM provenance_nodes WHERE object_type=? AND object_id=? '
                    'AND part=? AND is_current=1', (row['object_type'], row['object_id'], row['part']),
                ).fetchone()
                values.append((node, tuple(latest) if latest else None))
            for key, scope in sorted(reads.scopes.items()):
                kind = scope['kind']
                args = {k: v for k, v in scope.items() if k != 'kind'}
                if kind == 'events':
                    args['kind'] = args.pop('event_kind', None)
                    rows = self.events(**args)
                    value = [(row['id'], row['version']) for row in rows]
                elif kind == 'facts':
                    value = [(row['id'], row['version'], row['content'], row['lifecycle'], row['confirmation'])
                             for row in self.facts(**args)]
                elif kind == 'insights':
                    value = [(row['id'], row['data'], row['status']) for row in self.insights(**args)]
                elif kind == 'active_plan':
                    value = self.active_plan()
                elif kind == 'references':
                    row = self.conn.execute(
                        "SELECT id,evidence_refs FROM messages WHERE conversation_id=? AND role='assistant' "
                        'AND thread_id!=? ORDER BY created_at DESC,rowid DESC LIMIT 1',
                        (args['conversation_id'], args['excluding_thread']),
                    ).fetchone()
                    value = tuple(row) if row else None
                else:
                    value = None
                values.append((key, value))
        return hashlib.sha256(dump(values).encode()).hexdigest()

    def invalidate_dependencies(self, kind, object_id, *, before=None, after=None):
        from ...health.derived import invalidate_data
        roots = {row[0] for row in self.conn.execute(
            'SELECT node_id FROM provenance_nodes WHERE object_type=? AND object_id=?', (kind, object_id),
        )}
        roots.update(self.matching_scopes(kind, before))
        roots.update(self.matching_scopes(kind, after))
        roots.update(row[0] for row in self.conn.execute(
            "SELECT node_id FROM provenance_nodes WHERE object_type='insight' AND provenance_state='unknown' AND is_current=1",
        ))
        for node in self.dependency_closure(roots):
            row = self.conn.execute('SELECT * FROM provenance_nodes WHERE node_id=?', (node,)).fetchone()
            if row['object_type'] == 'insight':
                self.conn.execute("UPDATE insights SET status='stale' WHERE id=? AND status='active'", (row['object_id'],))
            if row['object_type'] in ('insight', 'review', 'plan') and row['part'] == 'whole':
                table = {'insight': 'insights', 'review': 'reviews', 'plan': 'plans'}[row['object_type']]
                artifact = self.conn.execute(f'SELECT data FROM {table} WHERE id=?', (row['object_id'],)).fetchone()
                if artifact:
                    data = invalidate_data(json.loads(artifact[0]), 'source_changed', plan=row['object_type'] == 'plan')
                    self.conn.execute(f'UPDATE {table} SET data=? WHERE id=?', (dump(data), row['object_id']))

    def matching_scopes(self, kind, record):
        result = set()
        for row in self.conn.execute('SELECT * FROM provenance_scopes'):
            scope = json.loads(row['criteria'])
            if kind == 'event' and scope['kind'] == 'events' and record:
                if scope.get('start') and record['day'] < scope['start']:
                    continue
                if scope.get('end') and record['day'] > scope['end']:
                    continue
                # Repository event scopes use event_kind to avoid collision with scope kind.
                if scope.get('event_kind') and record['kind'] != scope['event_kind']:
                    continue
                if scope.get('search') and not self.conn.execute('SELECT ? LIKE ?', (record['raw_text'], f"%{scope['search']}%" )).fetchone()[0]:
                    continue
                result.add(row['target_node_id'])
            elif kind == 'fact' and scope['kind'] == 'facts':
                result.add(row['target_node_id'])
        return result

    def migrate_provenance(self):
        """Inventory old objects without inventing missing lineage."""
        with self.lock, self.conn:
            for kind, table, id_column in [
                ('event', 'events', 'id'), ('fact', 'facts', 'id'),
                ('workflow', 'workflows', 'id'), ('message', 'messages', 'id'),
                ('plan', 'plans', 'id'), ('insight', 'insights', 'id'),
                ('review', 'reviews', 'id'), ('notification', 'notifications', 'id'),
                ('operation', 'operations', 'key'), ('summary', 'monthly_summaries', 'month'),
            ]:
                for row in self.conn.execute(f'SELECT * FROM {table}').fetchall():
                    node = self.provenance_node(kind, row[id_column], row['version'] if kind == 'event' else None)
                    if kind == 'event':
                        for source in json.loads(row['data']).get('evidence_sources', []):
                            self.link_source(node, source['message_id'], 'evidence_sources')
                        versions = self.conn.execute('SELECT version,data FROM revisions WHERE event_id=?', (row['id'],)).fetchall()
                        for revision in versions:
                            rev_node = self.provenance_node(kind, row['id'], revision['version'])
                            for source in json.loads(revision['data']).get('evidence_sources', []):
                                self.link_source(rev_node, source['message_id'], 'evidence_sources')
                        # Restore current selector after inventorying historical revisions.
                        self.conn.execute('UPDATE provenance_nodes SET is_current=(version=?) WHERE object_type=? AND object_id=?',
                                          (str(row['version']), kind, row['id']))
                    elif kind == 'message':
                        self.link_source(node, row['id'], 'text')
                        workflow = self.workflow_node(row['thread_id'])
                        self.conn.execute('INSERT OR IGNORE INTO provenance_dependencies VALUES (?,?)', (workflow, node))
