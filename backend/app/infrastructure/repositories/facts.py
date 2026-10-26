"""Versioned long-term facts, with conservative conflict and expiry handling."""
import json

from ...common import clock
from ...contracts.facts import FactInput
from ...health.facts import is_restriction, parse_fact, subject_key, uncertain_assertion
from ...privacy.provenance import collectors, untracked
from .common import dump


class FactRepositoryMixin:
    def _fact(self, row):
        if row is None:
            return None
        detail = self.conn.execute('SELECT data FROM fact_details WHERE fact_id=?', (row['id'],)).fetchone()
        defaults = {'type': 'legacy_text', 'subject': '', 'value': '', 'source_kind': 'legacy',
                    'confirmation': 'confirmed', 'lifecycle': 'active', 'version': 1,
                    'valid_from': None, 'valid_until': None, 'supersedes': None,
                    'evidence_message_id': None, 'schema_version': 1}
        return {**defaults, **dict(row), **(json.loads(detail[0]) if detail else {})}

    def fact(self, fact_id):
        with self.lock:
            return self._fact(self.conn.execute('SELECT * FROM facts WHERE id=?', (fact_id,)).fetchone())

    def _write_fact(self, record, reason, source_message_id=None):
        if record['version'] > 1 and not self.conn.execute(
            'SELECT 1 FROM fact_revisions WHERE fact_id=?', (record['id'],)).fetchone():
            previous = self.fact(record['id'])
            self.conn.execute('INSERT INTO fact_revisions VALUES (?,?,?,?)',
                              (record['id'], previous['version'], dump(previous), previous['created_at']))
        record = {**record, 'status': record['confirmation'], 'updated_at': clock.stamp(), 'change_reason': reason}
        self.conn.execute('UPDATE facts SET content=?,status=? WHERE id=?',
                          (record['content'], record['confirmation'], record['id']))
        self.conn.execute('INSERT OR REPLACE INTO fact_details VALUES (?,?)', (record['id'], dump(record)))
        self.conn.execute('INSERT INTO fact_revisions VALUES (?,?,?,?)',
                          (record['id'], record['version'], dump(record), record['updated_at']))
        self.invalidate_dependencies('fact', record['id'])
        node = self.provenance_node('fact', record['id'], record['version'], state='complete')
        # A current fact revision still owns source copies from earlier revisions.
        self.conn.execute('INSERT OR IGNORE INTO provenance_occurrences '
                          'SELECT source_id,?,field_path FROM provenance_occurrences o '
                          'JOIN provenance_nodes n ON n.node_id=o.holder_node_id '
                          "WHERE n.object_type='fact' AND n.object_id=?", (node, record['id']))
        if source_message_id:
            self.link_source(node, source_message_id, 'content')
        for reads in collectors(self):
            reads.sources.add(node)
            if reads.target:
                self.conn.execute('INSERT OR IGNORE INTO provenance_dependencies VALUES (?,?)', (node, reads.target))
        self._bump()
        return record

    def expire_facts(self, reference=None):
        day = (reference or clock.now().date()).isoformat()
        with self.lock, self.provenance_transaction(), untracked():
            for row in self.conn.execute('SELECT * FROM facts').fetchall():
                record = self._fact(row)
                if record['lifecycle'] == 'scheduled' and record['valid_from'] <= day:
                    peers = [self._fact(other) for other in self.conn.execute('SELECT * FROM facts WHERE id!=?', (record['id'],))]
                    peers = [other for other in peers if record['subject'] and other['subject'] == record['subject']
                             and other['type'] == record['type'] and other['lifecycle'] in ('active', 'needs_review')
                             and other['confirmation'] in ('confirmed', 'conflict')]
                    for peer in peers:
                        if record['type'] == 'food_preference' and not record['valid_until'] and not is_restriction(record) and not is_restriction(peer):
                            peer.update(lifecycle='superseded', version=peer['version'] + 1)
                        else:
                            peer.update(confirmation='conflict', version=peer['version'] + 1)
                            record['confirmation'] = 'conflict'
                        self._write_fact(peer, '定时事实生效')
                    record.update(lifecycle='active', version=record['version'] + 1)
                    self._write_fact(record, '生效日期已到')
                if record['lifecycle'] == 'active' and record.get('valid_until') and record['valid_until'] < day:
                    record.update(lifecycle='needs_review' if is_restriction(record) else 'expired',
                                  version=record['version'] + 1)
                    self._write_fact(record, '有效期结束')
            records = [self._fact(row) for row in self.conn.execute('SELECT * FROM facts')]
            for record in records:
                if record['confirmation'] == 'conflict' and record['lifecycle'] == 'active' and not is_restriction(record):
                    peers = [r for r in records if r['id'] != record['id'] and r['subject'] == record['subject']
                             and r['type'] == record['type'] and r['lifecycle'] in ('active', 'needs_review')
                             and r['confirmation'] in ('confirmed', 'conflict')]
                    if not peers:
                        record.update(confirmation='confirmed', version=record['version'] + 1)
                        self._write_fact(record, '冲突信息已失效')

    def facts(self, include_inactive=False):
        with self.lock:
            self.expire_facts()
            records = [self._fact(row) for row in self.conn.execute('SELECT * FROM facts ORDER BY created_at')]
            today = clock.now().date().isoformat()
            records = [r for r in records if include_inactive or (
                r['confirmation'] == 'confirmed' and r['lifecycle'] in ('active', 'needs_review')
                and (not r.get('valid_from') or r['valid_from'] <= today))]
            self.observe_scope('facts', **({'include_inactive': True} if include_inactive else {}))
            for record in records:
                self.observe('fact', record['id'])
            return records

    def safety_restrictions(self):
        # Conflicting restrictions remain protective until explicitly resolved.
        self.expire_facts()
        records = [self._fact(row) for row in self.conn.execute('SELECT * FROM facts ORDER BY created_at')]
        records = [r for r in records if is_restriction(r)
                   and r['lifecycle'] in ('active', 'needs_review')
                   and r['confirmation'] in ('confirmed', 'conflict')]
        for record in records:
            self.observe('fact', record['id'])
        return records

    def save_fact(self, fact_id, content, *, source_message_id=None, context_text=None, structured=None, expected_version=None):
        explicit_structure = structured is not None
        payload = structured or parse_fact(content)
        if not isinstance(payload, FactInput):
            payload = FactInput.model_validate(payload)
        data = payload.model_dump(mode='json')
        candidate = not explicit_structure and (uncertain_assertion(content, context_text)
                    or (context_text is not None and payload.type == 'legacy_text' and not content.strip().startswith('我')))
        if candidate:
            data['source_kind'] = 'unconfirmed_extraction'
        data['subject'] = subject_key(data['subject'])
        if data['type'] == 'food_preference':
            data['value'] = {'不喜欢': 'dislike', '喜欢': 'like', '不吃': 'avoid',
                             '可以接受': 'accept', '接受': 'accept'}.get(data['value'], data['value'])
        with self.lock, self.conn:
            self.expire_facts()
            if expected_version is None:
                operation = self.conn.execute('SELECT result FROM operations WHERE key=?', (f'fact:{fact_id}',)).fetchone()
                if operation:
                    return self.fact(json.loads(operation[0])['id'])
            prior = self.fact(fact_id)
            if prior:
                if expected_version is None:
                    return prior  # Workflow replay.
                if prior['version'] != expected_version:
                    raise ValueError('长期信息版本已变化，请刷新后修改')
            elif expected_version is not None:
                raise ValueError('长期信息不存在')
            records = [self._fact(row) for row in self.conn.execute('SELECT * FROM facts')]
            siblings = [r for r in records if r['id'] != fact_id
                        and r['type'] == data['type'] and r['subject'] == data['subject']
                        and data['subject'] and r['lifecycle'] in ('active', 'needs_review')
                        and r['confirmation'] in ('confirmed', 'conflict')]
            scheduled = bool(data['valid_from'] and data['valid_from'] > clock.now().date().isoformat())
            duplicate = next((r for r in siblings if not candidate and not scheduled and all(r.get(k) == data.get(k) for k in
                             ('value', 'valid_until', 'source_kind'))
                             and r['confirmation'] == 'confirmed'), None)
            if duplicate and prior is None:
                self._bind_fact_operation(fact_id, duplicate)
                return duplicate
            replacing = next((r for r in siblings if r['id'] == data.get('supersedes')), None)
            if data.get('supersedes') and not replacing:
                raise ValueError('被替代的事实不存在、类型不同或已经失效')
            # Only a permanent ordinary preference can replace a permanent one
            # automatically. Temporary exceptions must not erase older limits.
            auto = data['type'] == 'food_preference' and not is_restriction(data) and not data['valid_until']
            if siblings and not candidate and not scheduled and (auto or replacing):
                for sibling in siblings:
                    if is_restriction(sibling) and not replacing:
                        continue
                    if replacing and sibling['id'] != replacing['id']:
                        continue
                    sibling.update(lifecycle='superseded', version=sibling['version'] + 1)
                    self._write_fact(sibling, '用户明确更新')
                    data['supersedes'] = sibling['id']
            unresolved = [r for r in siblings if not candidate and not scheduled and self.fact(r['id'])['lifecycle'] in ('active', 'needs_review')]
            confirmation = 'conflict' if unresolved else 'confirmed'
            if candidate:
                confirmation = 'candidate'
            for sibling in unresolved:
                sibling.update(confirmation='conflict', version=sibling['version'] + 1)
                self._write_fact(sibling, '同一对象存在冲突')
            if not prior:
                self.conn.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',
                                  (fact_id, content, 'user', None, confirmation, clock.stamp()))
            elif not self.conn.execute('SELECT 1 FROM fact_revisions WHERE fact_id=?', (fact_id,)).fetchone():
                self.conn.execute('INSERT INTO fact_revisions VALUES (?,?,?,?)',
                                  (fact_id, prior['version'], dump(prior), prior['created_at']))
            record = {**(prior or self.fact(fact_id)), **data, 'confirmation': confirmation,
                      'lifecycle': 'scheduled' if scheduled else 'active', 'version': prior['version'] + 1 if prior else 1,
                      'schema_version': 1, 'evidence_message_id': source_message_id}
            if prior and not record['supersedes']:
                record['supersedes'] = prior.get('supersedes')
            saved = self._write_fact(record, '用户修改' if prior else '用户明确陈述', source_message_id)
            if expected_version is None:
                self._bind_fact_operation(fact_id, saved)
            return saved

    def _bind_fact_operation(self, mutation_id, record):
        key = f'fact:{mutation_id}'
        self.conn.execute('INSERT OR IGNORE INTO operations VALUES (?,?)', (key, dump({'id': record['id']})))
        self.observe('fact', record['id'])
        self.persist_provenance('operation', key, sources=[self.provenance_node('fact', record['id'])], use_reads=False)

    def decide_fact(self, fact_id, action, expected_version):
        with self.lock, self.conn:
            self.expire_facts()
            record = self.fact(fact_id)
            if not record or record['version'] != expected_version:
                raise ValueError('长期信息不存在或版本已变化，请刷新')
            if action == 'confirm':
                if record['lifecycle'] not in ('active', 'needs_review'):
                    raise ValueError('此信息已失效，请重新编辑有效期后确认')
                record['confirmation'] = 'confirmed'
                if record['source_kind'] == 'unconfirmed_extraction':
                    record['source_kind'] = 'user_explicit'
                # Resolve conflicts only by explicitly selecting one fact.
                for row in self.conn.execute('SELECT * FROM facts').fetchall():
                    other = self._fact(row)
                    if (other['id'] != fact_id and record['subject'] and other['subject'] == record['subject']
                            and other['type'] == record['type'] and other['lifecycle'] in ('active', 'needs_review')):
                        other.update(lifecycle='superseded', version=other['version'] + 1)
                        self._write_fact(other, '用户选择另一条事实')
            else:
                record['lifecycle'] = 'retracted'
            record['version'] += 1
            return self._write_fact(record, '用户确认' if action == 'confirm' else '用户撤回')

    def fact_revisions(self, fact_id):
        if not self.fact(fact_id):
            raise ValueError('长期信息不存在')
        return [json.loads(row[0]) for row in self.conn.execute(
            'SELECT data FROM fact_revisions WHERE fact_id=? ORDER BY version', (fact_id,))]

    def delete_fact(self, fact_id):
        raise RuntimeError('请通过 engine.delete_fact 执行可恢复的隐私清理')
