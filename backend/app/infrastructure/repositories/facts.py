"""Versioned long-term facts, with conservative conflict and expiry handling."""
import json

from ...common import clock
from ...contracts.facts import FactInput
from ...health.facts import is_restriction, normalize_fact, parse_fact, uncertain_assertion, validate_fact_content
from ...health.fact_resolution import overlaps, pending_decisions, resolve_records, same_subject
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
        return normalize_fact({**defaults, **dict(row), **(json.loads(detail[0]) if detail else {})})

    def _fact_rows(self):
        return [self._fact(row) for row in self.conn.execute('SELECT * FROM facts ORDER BY created_at,rowid')]

    def _fact_display(self, record, view):
        conflicts = {item for group in view['conflicts'] for item in group['ids']}
        return {**record, 'confirmation': ('conflict' if record['id'] in conflicts else
                                          'candidate' if record['confirmation'] == 'candidate' else 'confirmed'),
                'lifecycle': 'superseded' if record['id'] in view['superseded_ids'] else record['lifecycle'],
                'temporarily_covered': record['id'] in view['masked_ids'] and record['id'] not in view['superseded_ids'],
                'review_reason': ('临时信息缺少结束日期' if record['time_scope'] == 'temporary_unknown' else
                                  '陈述或提取结果待核对' if record['confirmation'] == 'candidate' else
                                  '保护性信息已到期待核对' if record['lifecycle'] == 'needs_review' else '')}

    def fact(self, fact_id):
        with self.lock:
            record = self._fact(self.conn.execute('SELECT * FROM facts WHERE id=?', (fact_id,)).fetchone())
            if record is None:
                return None
            return self._fact_display(record, resolve_records(self._fact_rows(), clock.now().date().isoformat()))

    def _write_fact(self, record, reason, source_message_id=None):
        record = {k: v for k, v in record.items() if k not in ('temporarily_covered', 'review_reason')}
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
            for snapshot in sorted(self._fact_rows(), key=lambda r: (r.get('valid_from') or '', r['created_at'], r['id'])):
                record = self._fact(self.conn.execute('SELECT * FROM facts WHERE id=?', (snapshot['id'],)).fetchone())
                if record['lifecycle'] == 'scheduled' and record['valid_from'] <= day:
                    if record.get('auto_update'):
                        for previous in self._fact_rows():
                            if (previous['id'] != record['id'] and same_subject(previous, record)
                                    and previous['type'] == record['type'] and not is_restriction(previous)
                                    and not previous.get('valid_until') and previous['lifecycle'] == 'active'
                                    and (previous.get('valid_from') or '') <= record['valid_from']):
                                previous.update(lifecycle='superseded', version=previous['version'] + 1)
                                self._write_fact(previous, '定时长期更新生效')
                    record.update(lifecycle='active', version=record['version'] + 1)
                    self._write_fact(record, '生效日期已到')
                if record['lifecycle'] == 'active' and record.get('valid_until') and record['valid_until'] < day:
                    record.update(lifecycle='needs_review' if is_restriction(record) else 'expired',
                                  version=record['version'] + 1)
                    self._write_fact(record, '有效期结束')

    def resolve_facts(self, reference_date=None, purpose='advice'):
        day = str(reference_date or clock.now().date().isoformat())
        with self.lock:
            self.expire_facts()
            records = self._fact_rows()
            view = resolve_records(records, day)
            # Include empty scopes and covered baselines: a new fact or a change to
            # a hidden baseline must invalidate artifacts using this view.
            if purpose != 'safety':
                self.observe_scope('facts', reference_date=str(reference_date) if reference_date else None, purpose=purpose)
            safety_ids = {r['id'] for r in view['constraints'] + pending_decisions(view)}
            safety_ids.update(item for group in view['conflicts'] for item in group['ids'])
            for record in records if purpose != 'safety' else [r for r in records if r['id'] in safety_ids]:
                self.observe('fact', record['id'])
            return view

    def facts(self, include_inactive=False, reference_date=None, purpose='advice'):
        with self.lock:
            view = self.resolve_facts(reference_date, purpose)
            if include_inactive:
                self.observe_scope('facts', include_inactive=True)
                return [self._fact_display(r, view) for r in self._fact_rows()]
            return [r for r in view['effective'] if r['confirmation'] == 'confirmed']

    def safety_restrictions(self, reference_date=None):
        return self.resolve_facts(reference_date, 'safety')['constraints']

    def save_fact(self, fact_id, content, *, source_message_id=None, context_text=None,
                  structured=None, draft=None, expected_version=None):
        # Operation replay precedes parsing relative dates: retries cannot move a deadline.
        with self.lock, self.conn:
            if expected_version is None:
                operation = self.conn.execute('SELECT result FROM operations WHERE key=?', (f'fact:{fact_id}',)).fetchone()
                if operation:
                    return self.fact(json.loads(operation[0])['id'])
                prior = self.fact(fact_id)
                if prior:
                    return prior
            self.expire_facts()
            prior = self.fact(fact_id)
            if expected_version is not None and (not prior or prior['version'] != expected_version):
                raise ValueError('长期信息不存在或版本已变化，请刷新后修改')
            payload = structured or (FactInput(content=content, **draft) if draft else parse_fact(content))
            if not isinstance(payload, FactInput):
                payload = FactInput.model_validate(payload)
            if payload.content != content:
                raise ValueError('事实内容必须与本次原文一致')
            parsed = parse_fact(content)
            if parsed.time_scope == 'bounded' and parsed.type == payload.type:
                if ((payload.valid_until and payload.valid_until != parsed.valid_until)
                        or (payload.valid_from and payload.valid_from != parsed.valid_from)):
                    raise ValueError('事实期限与原文不一致，请核对')
                payload = payload.model_copy(update={'valid_from': parsed.valid_from,
                                                    'valid_until': parsed.valid_until, 'time_scope': 'bounded'})
            elif parsed.time_scope == 'temporary_unknown' and not payload.valid_until:
                payload = payload.model_copy(update={'time_scope': 'temporary_unknown'})
            review = validate_fact_content(payload, model_draft=draft is not None)
            data = normalize_fact(payload.model_dump(mode='json'))
            if draft is not None and parsed.type == 'food_preference':
                data['restriction_reason'] = parsed.restriction_reason
                data = normalize_fact(data)
            # A model or unsupported statement cannot silently acquire authority.
            candidate = review or uncertain_assertion(content, context_text) or (
                structured is None and context_text is not None and payload.type == 'legacy_text'
                and not content.strip().startswith('我'))
            if uncertain_assertion(content, context_text):
                data['assertion'] = 'hypothetical' if any(w in (context_text or content) for w in ('如果', '假如')) else 'uncertain'
            if prior and is_restriction(prior) and (candidate or data['assertion'] != 'affirmed'):
                raise ValueError('原限制仍需保留，请先明确核对；解除限制请使用撤回操作')
            if candidate:
                data['source_kind'] = 'unconfirmed_extraction'
            auto = (data['type'] == 'food_preference' and not is_restriction(data)
                    and not data['valid_until'] and not candidate)
            data['auto_update'] = auto
            if auto and not data.get('valid_from'):
                data['valid_from'] = clock.now().date().isoformat()
            if auto and data['time_scope'] == 'unknown':
                data['time_scope'] = 'persistent'
            records = self._fact_rows()
            siblings = [r for r in records if r['id'] != fact_id and same_subject(r, data)
                        and r['type'] == data['type'] and r['lifecycle'] not in ('superseded', 'retracted', 'expired')
                        and r['confirmation'] in ('confirmed', 'conflict') and overlaps(r, data)]
            duplicate = next((r for r in siblings if not candidate and all(r.get(k) == data.get(k) for k in (
                'value', 'valid_from', 'valid_until', 'time_scope', 'source_kind', 'source_details',
                'assertion', 'restriction_reason', 'reported_severity'))), None)
            if duplicate and prior is None:
                evidence = duplicate.get('evidence', [])
                extra = [e for e in data['evidence'] if e not in evidence]
                if extra or source_message_id:
                    duplicate.update(version=duplicate['version'] + 1, evidence=data['evidence'], content=content)
                    self._write_fact(duplicate, '补充明确来源证据', source_message_id)
                self._bind_fact_operation(fact_id, duplicate)
                return self.fact(duplicate['id'])
            scheduled = bool(data['valid_from'] and data['valid_from'] > clock.now().date().isoformat())
            replacing = next((r for r in siblings if r['id'] == data.get('supersedes')), None)
            if data.get('supersedes') and not replacing:
                raise ValueError('被替代的事实不存在、类型不同或已经失效')
            if replacing and (is_restriction(replacing) or is_restriction(data)):
                raise ValueError('保护性信息请通过明确编辑或撤回处理，不能被偏好替代')
            if data.get('supersedes') and data['valid_until']:
                raise ValueError('临时信息应使用临时覆盖，不能永久替代旧信息')
            if not candidate and (auto or replacing):
                for sibling in siblings:
                    if is_restriction(sibling) or (replacing and replacing['id'] != sibling['id']):
                        continue
                    if (sibling.get('valid_from') or '') > (data.get('valid_from') or ''):
                        continue
                    data['supersedes'] = sibling['id']
                    if not scheduled:
                        sibling.update(lifecycle='superseded', version=sibling['version'] + 1)
                        self._write_fact(sibling, '用户明确更新')
            confirmation = 'candidate' if candidate else 'confirmed'
            if not prior:
                self.conn.execute('INSERT INTO facts VALUES (?,?,?,?,?,?)',
                                  (fact_id, content, 'user', None, confirmation, clock.stamp()))
            record = {**(prior or self._fact(self.conn.execute('SELECT * FROM facts WHERE id=?', (fact_id,)).fetchone())),
                      **data, 'confirmation': confirmation, 'lifecycle': 'scheduled' if scheduled else 'active',
                      'version': prior['version'] + 1 if prior else 1, 'schema_version': 2,
                      'evidence_message_id': source_message_id or (prior or {}).get('evidence_message_id')}
            # An edit invalidates old covering links. The user must resolve its new scope explicitly.
            record['overrides'] = []
            if prior and not record['supersedes']:
                record['supersedes'] = prior.get('supersedes')
            self._write_fact(record, '用户修改' if prior else '用户明确陈述', source_message_id)
            saved = self.fact(fact_id)
            if expected_version is None:
                self._bind_fact_operation(fact_id, saved)
            return saved

    def _bind_fact_operation(self, mutation_id, record):
        key = f'fact:{mutation_id}'
        self.conn.execute('INSERT OR IGNORE INTO operations VALUES (?,?)', (key, dump({'id': record['id']})))
        self.observe('fact', record['id'])
        self.persist_provenance('operation', key, sources=[self.provenance_node('fact', record['id'])], use_reads=False)

    def decide_fact(self, fact_id, action, expected_version, resolution='select', peer_versions=None):
        with self.lock, self.conn:
            self.expire_facts()
            record = self.fact(fact_id)
            if not record or record['version'] != expected_version:
                raise ValueError('长期信息不存在或版本已变化，请刷新')
            if action == 'confirm':
                if record['lifecycle'] not in ('active', 'needs_review'):
                    raise ValueError('此信息已失效，请重新编辑有效期后确认')
                if record['time_scope'] == 'temporary_unknown' or record['assertion'] != 'affirmed':
                    raise ValueError('请先补充期限或明确陈述内容，再确认')
                peers = [r for r in self._fact_rows() if r['id'] != fact_id and same_subject(record, r)
                         and r['type'] == record['type'] and overlaps(record, r)
                         and r['lifecycle'] in ('active', 'needs_review', 'scheduled')
                         and r['confirmation'] != 'candidate']
                if peer_versions is not None and peer_versions != {r['id']: r['version'] for r in peers}:
                    raise ValueError('冲突信息版本已变化，请刷新后选择')
                if peers and any(is_restriction(r) for r in [record, *peers]):
                    raise ValueError('保护性信息存在冲突，请明确编辑或撤回相关限制')
                if resolution == 'permanent' and record.get('valid_until'):
                    raise ValueError('永久更新请先编辑为长期信息，再确认')
                if resolution == 'temporary' and not record.get('valid_until'):
                    raise ValueError('临时覆盖需要明确的结束日期')
                record['confirmation'] = 'confirmed'
                if record['source_kind'] == 'unconfirmed_extraction':
                    record['source_kind'] = ('user_reported_professional' if record['type'] == 'professional_advice'
                                             else 'user_explicit')
                if record.get('valid_until'):
                    if any(r.get('valid_until') for r in peers):
                        raise ValueError('多个临时期限重叠，请编辑日期或撤回其中一条')
                    record['overrides'] = [r['id'] for r in peers]
                else:
                    for other in peers:
                        # Superseding a covering fact also retires its hidden baselines.
                        for previous in [other, *[r for r in self._fact_rows() if r['id'] in other.get('overrides', [])]]:
                            previous.update(lifecycle='superseded', version=previous['version'] + 1)
                            self._write_fact(previous, '用户选择长期更新')
                        record['supersedes'] = other['id']
            elif action == 'retract':
                record['lifecycle'] = 'retracted'
            else:
                raise ValueError('不支持的事实操作')
            record['version'] += 1
            self._write_fact(record, '用户确认' if action == 'confirm' else '用户撤回')
            return self.fact(fact_id)

    def fact_revisions(self, fact_id):
        if not self.fact(fact_id):
            raise ValueError('长期信息不存在')
        return [json.loads(row[0]) for row in self.conn.execute(
            'SELECT data FROM fact_revisions WHERE fact_id=? ORDER BY version', (fact_id,))]

    def erase_fact_links(self, fact_id):
        """Privacy redaction removes deleted identifiers from current and historical links."""
        for table in ('fact_details', 'fact_revisions'):
            for row in self.conn.execute(f'SELECT rowid,data FROM {table}').fetchall():
                data = json.loads(row['data'])
                changed = False
                if fact_id in data.get('overrides', []):
                    data['overrides'] = [item for item in data['overrides'] if item != fact_id]
                    changed = True
                if data.get('supersedes') == fact_id:
                    data['supersedes'] = None
                    changed = True
                if changed:
                    self.conn.execute(f'UPDATE {table} SET data=? WHERE rowid=?', (dump(data), row['rowid']))

    def delete_fact(self, fact_id):
        raise RuntimeError('请通过 engine.delete_fact 执行可恢复的隐私清理')
