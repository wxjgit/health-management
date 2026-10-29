"""Consistent stopped-service snapshots; no live raw SQLite copies."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import tempfile
from urllib.parse import quote

from ..infrastructure.data_owner import DataOwner
from ..infrastructure.tenants import tenant_path


def readonly(path):
    return sqlite3.connect('file:' + quote(str(path.resolve())) + '?mode=ro', uri=True)


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot(root, output):
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root) or output.exists():
        raise ValueError('snapshot_requires_new_external_directory')
    owner = DataOwner(root)
    try:
        if not (root / 'accounts.db').is_file() or not (root / 'usage.db').is_file():
            raise ValueError('account_and_usage_databases_required')
        output.mkdir(parents=True, mode=0o700)
        files = ['accounts.db', 'usage.db']
        conn = readonly(root / 'accounts.db')
        try:
            users = [row[0] for row in conn.execute('SELECT id FROM users')]
        finally:
            conn.close()
        for uid in users:
            directory = tenant_path(root, uid)
            if not directory.exists():
                continue
            for name in ('health.db', 'checkpoints.db'):
                if not (directory / name).is_file() or (directory / name).is_symlink():
                    raise ValueError('user_database_pair_required')
                files.append(f'users/{uid}/{name}')
        manifest = {'format': 1, 'files': {}}
        for name in files:
            source = root / name
            if source.is_symlink():
                raise ValueError('symlink_not_allowed')
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            src, dst = readonly(source), sqlite3.connect(target)
            try:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('snapshot_integrity_failed')
            finally:
                src.close()
                dst.close()
            target.chmod(0o600)
            manifest['files'][name] = file_hash(target)
        journal = root / 'account-deletions.jsonl'
        if not journal.is_file() or journal.is_symlink():
            raise ValueError('deletion_journal_required')
        shutil.copyfile(journal, output / journal.name)
        (output / journal.name).chmod(0o600)
        manifest['files'][journal.name] = file_hash(output / journal.name)
        (output / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        (output / 'manifest.json').chmod(0o600)
    except BaseException:
        if output.exists():
            shutil.rmtree(output)
        raise
    finally:
        owner.close()


def journal_ids(path):
    result = set()
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            uid = json.loads(line)['user_id']
            # Validate UUIDs without creating a directory.
            tenant_path(path.parent, uid)
            result.add(uid)
    return result


def restore(snapshot_dir, target, latest_deletions):
    source, target = snapshot_dir.resolve(), target.resolve()
    if target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('restore_directories_must_be_separate')
    manifest = json.loads((source / 'manifest.json').read_text())
    if manifest.get('format') != 1:
        raise ValueError('unsupported_snapshot_format')
    files = manifest['files']
    if not {'accounts.db', 'usage.db', 'account-deletions.jsonl'}.issubset(files):
        raise ValueError('incomplete_snapshot')
    for name, expected in files.items():
        parts = PurePosixPath(name).parts
        allowed = name in ('accounts.db', 'usage.db', 'account-deletions.jsonl')
        if len(parts) == 3 and parts[0] == 'users' and parts[2] in ('health.db', 'checkpoints.db'):
            tenant_path(source, parts[1])
            allowed = True
        if not allowed or (source / name).is_symlink() or file_hash(source / name) != expected:
            raise ValueError('snapshot_verification_failed')
    # The latest external journal is compulsory: an old backup must never revive
    # an account deleted since that backup was created.
    deleted = journal_ids(latest_deletions) | journal_ids(source / 'account-deletions.jsonl')
    with tempfile.TemporaryDirectory(prefix='qingji-restore-') as temporary:
        stage = Path(temporary)
        for name in files:
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copyfile(source / name, path)
            path.chmod(0o600)
        conn = sqlite3.connect(stage / 'accounts.db')
        try:
            deleted |= {row[0] for row in conn.execute('SELECT user_id FROM account_deletions')}
            for uid in deleted:
                shutil.rmtree(tenant_path(stage, uid), ignore_errors=True)
                for table in ('sessions', 'consent_events', 'user_consents', 'account_deletions'):
                    conn.execute(f'DELETE FROM {table} WHERE user_id=?', (uid,))
                conn.execute('DELETE FROM users WHERE id=?', (uid,))
            conn.execute('DELETE FROM sessions')  # Require fresh login after restore.
            conn.commit()
        finally:
            conn.close()
        conn = sqlite3.connect(stage / 'usage.db')
        try:
            for uid in deleted:
                conn.execute('DELETE FROM counters WHERE principal=?', ('user:' + uid,))
            conn.commit()
        finally:
            conn.close()
        (stage / 'account-deletions.jsonl').write_text(''.join(json.dumps({'user_id': uid}) + '\n' for uid in sorted(deleted)))
        for path in stage.rglob('*.db'):
            conn = readonly(path)
            try:
                if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise ValueError('restored_database_integrity_failed')
            finally:
                conn.close()
        owner = DataOwner(target)
        try:
            if any(path.name != '.engine.lock' for path in target.iterdir()):
                raise ValueError('restore_target_must_be_empty')
            target.chmod(0o700)
            marker = target / '.restore-in-progress'
            marker.touch(mode=0o600)
            for path in stage.iterdir():
                if path.is_dir():
                    shutil.copytree(path, target / path.name)
                else:
                    shutil.copyfile(path, target / path.name)
                    (target / path.name).chmod(0o600)
            marker.unlink()
        finally:
            owner.close()


def main():
    parser = argparse.ArgumentParser(description='Stop the service before snapshot or restore.')
    sub = parser.add_subparsers(dest='action', required=True)
    create = sub.add_parser('create')
    create.add_argument('--data-dir', type=Path, required=True)
    create.add_argument('--output', type=Path, required=True)
    recover = sub.add_parser('restore')
    recover.add_argument('--snapshot', type=Path, required=True)
    recover.add_argument('--data-dir', type=Path, required=True)
    recover.add_argument('--latest-deletions', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == 'create':
            snapshot(args.data_dir, args.output)
        else:
            restore(args.snapshot, args.data_dir, args.latest_deletions)
    except Exception:
        parser.exit(2, 'backup_or_restore_refused: check service ownership, snapshot integrity and empty destination.\n')
    print(json.dumps({'ok': True, 'action': args.action}))


if __name__ == '__main__':
    main()
