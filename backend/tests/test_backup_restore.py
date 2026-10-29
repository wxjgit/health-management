import json
from pathlib import Path

import pytest

from app.api.policies import policy
from app.common.config import Settings
from app.infrastructure.data_owner import DataDirectoryInUse
from app.main import create_app
from app.operations.backup import snapshot, restore
from fastapi.testclient import TestClient


def test_snapshot_is_consistent_and_restore_does_not_revive_deleted_account(tmp_path):
    root = tmp_path / 'data'
    settings = Settings(data_dir=root, wechat_app_id='synthetic', wechat_app_secret='synthetic',
                        privacy_operator='Test', privacy_contact='test@example.invalid',
                        local_only=True, scheduler_enabled=False, job_runner_enabled=False)
    with TestClient(create_app(settings)) as client:
        app = client.app
        sessions = []
        for name in ('a', 'b'):
            session = app.state.auth.issue(name)
            app.state.auth.save_consent(session['user']['id'], policy(settings)['version'], False)
            sessions.append(session)
            with app.state.engine.lease(session['user']['id']) as engine:
                engine.repo.set_meta('private-marker', name)
        with pytest.raises(DataDirectoryInUse):
            snapshot(root, tmp_path / 'live-snapshot')
    backup = tmp_path / 'snapshot'
    snapshot(root, backup)
    assert (backup / 'accounts.db').is_file()
    with TestClient(create_app(settings)) as client:
        deleted = client.request('DELETE', '/api/auth/account', json={'confirmation': '注销账号'},
                                 headers={'Authorization': 'Bearer ' + sessions[0]['access_token']})
        assert deleted.json()['state'] == 'completed'
    target = tmp_path / 'restored'
    restore(backup, target, root / 'account-deletions.jsonl')
    settings.data_dir = target
    with TestClient(create_app(settings)) as client:
        with client.app.state.auth.connect() as conn:
            assert conn.execute('SELECT count(*) FROM users').fetchone()[0] == 1
            assert conn.execute('SELECT count(*) FROM sessions').fetchone()[0] == 0
        with client.app.state.engine.lease(sessions[1]['user']['id']) as engine:
            assert engine.repo.get_meta('private-marker') == 'b'
        assert not (target / 'users' / sessions[0]['user']['id']).exists()
    with pytest.raises(ValueError, match='empty'):
        restore(backup, target, root / 'account-deletions.jsonl')


def test_snapshot_hash_or_missing_latest_journal_blocks_restore(tmp_path):
    source = tmp_path / 'snapshot'
    source.mkdir()
    (source / 'manifest.json').write_text(json.dumps({'format': 1, 'files': {
        'accounts.db': 'bad', 'usage.db': 'bad', 'account-deletions.jsonl': 'bad',
    }}))
    (source / 'accounts.db').write_text('tampered')
    with pytest.raises(ValueError):
        restore(source, tmp_path / 'target', tmp_path / 'missing-journal')
    assert not (tmp_path / 'target').exists()


def test_failed_restore_marker_blocks_service_start(tmp_path):
    root = tmp_path / 'data'
    root.mkdir()
    (root / '.restore-in-progress').touch()
    settings = Settings(data_dir=root, wechat_app_id='test', wechat_app_secret='test')
    with pytest.raises(RuntimeError, match='incomplete_restore'):
        with TestClient(create_app(settings)):
            pytest.fail('partial restore was served')
