"""Aggregate-only local checks. Optional webhooks run only with --notify."""
import argparse
import os
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sqlite3
import time
from urllib.request import Request, urlopen
from urllib.parse import urlsplit, quote

from ..common.clock import now
from ..common.config import Settings


def traffic_snapshot(directory):
    counts = {'requests': 0, 'server_errors': 0, 'model_calls': 0, 'model_errors': 0}
    cutoff = time.time() - 300
    for path in sorted(directory.glob('????-??-??-??????.jsonl'))[-3:]:
        if path.is_symlink():
            continue
        with path.open('rb') as handle:
            start = max(0, path.stat().st_size - 2 * 1024 * 1024)
            handle.seek(start)
            if start:
                handle.readline()
            for line in handle:
                try:
                    event = json.loads(line)
                    if datetime.fromisoformat(event['timestamp']).timestamp() < cutoff:
                        continue
                    if event.get('event') == 'http_finished':
                        counts['requests'] += 1
                        counts['server_errors'] += event.get('status_class') == '5xx'
                    if event.get('event') == 'model_transport_finished':
                        counts['model_calls'] += 1
                        counts['model_errors'] += event.get('outcome') != 'ok'
                except (ValueError, KeyError, TypeError):
                    continue
    return counts


def inspect(settings, health_url):
    problems = []
    try:
        with urlopen(health_url, timeout=5) as response:
            payload = json.loads(response.read(65536))
        if payload.get('status') != 'ok':
            problems.append('api_unhealthy')
    except Exception:
        problems.append('api_unavailable')
    try:
        if shutil.disk_usage(settings.data_dir).free < 1024 * 1024 * 1024:
            problems.append('disk_free_below_1gb')
    except OSError:
        problems.append('runtime_directory_missing')
    counts = {'pending_jobs': 0, 'failed_jobs': 0}
    try:
        traffic = traffic_snapshot(settings.data_dir / 'observability')
        if traffic['requests'] >= 10 and traffic['server_errors'] / traffic['requests'] >= .1:
            problems.append('api_5xx_rate_above_10_percent')
        if traffic['model_calls'] >= 5 and traffic['model_errors'] / traffic['model_calls'] >= .5:
            problems.append('model_error_rate_above_50_percent')
        counts.update(traffic)
        for path in (settings.data_dir / 'users').glob('*/health.db'):
            conn = sqlite3.connect('file:' + quote(str(path.resolve())) + '?mode=ro', uri=True, timeout=1)
            try:
                counts['pending_jobs'] += conn.execute("SELECT count(*) FROM scheduled_jobs WHERE status='pending'").fetchone()[0]
                counts['failed_jobs'] += conn.execute("SELECT count(*) FROM scheduled_jobs WHERE status='failed'").fetchone()[0]
                row = conn.execute("SELECT min(next_attempt_at) FROM scheduled_jobs WHERE status='pending'").fetchone()
                if row[0] and int(time.time() * 1000) - row[0] > 30 * 60 * 1000:
                    problems.append('background_jobs_delayed_30m')
            finally:
                conn.close()
        if counts['failed_jobs']:
            problems.append('background_jobs_failed')
        accounts = sqlite3.connect('file:' + quote(str((settings.data_dir / 'accounts.db').resolve())) + '?mode=ro', uri=True)
        try:
            pending = accounts.execute('SELECT min(requested_at) FROM account_deletions').fetchone()[0]
            if pending and time.time() - pending > 300:
                problems.append('account_cleanup_delayed_5m')
        finally:
            accounts.close()
        conn = sqlite3.connect('file:' + quote(str((settings.data_dir / 'usage.db').resolve())) + '?mode=ro', uri=True)
        try:
            row = conn.execute("SELECT count FROM counters WHERE kind='model' AND principal='*' AND period=?", (now().date().isoformat(),)).fetchone()
            if row and row[0] >= settings.global_model_calls_per_day * .8:
                problems.append('model_daily_quota_above_80_percent')
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        problems.append('runtime_database_unavailable')
    return {'checked_at': now().isoformat(), 'ok': not problems, 'problems': sorted(set(problems)), **counts}


def notify_changed(report, state_path, webhook):
    if urlsplit(webhook).scheme != 'https':
        raise ValueError('https_webhook_required')
    try:
        previous = json.loads(state_path.read_text()) if state_path.exists() else None
    except (OSError, ValueError):
        previous = None
    state = {'problems': report['problems']}
    # First healthy run stays quiet. Changes and recovery are actionable.
    if (previous is None and report['problems']) or (previous is not None and previous != state):
        body = json.dumps({'text': '轻记运行检查：' + (', '.join(report['problems']) or '已恢复正常')}).encode()
        with urlopen(Request(webhook, data=body, headers={'Content-Type': 'application/json'}), timeout=8) as response:
            if not 200 <= response.status < 300:
                raise ValueError('alert_delivery_failed')
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state))
    temporary.chmod(0o600)
    temporary.replace(state_path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--health-url', default='http://127.0.0.1:8000/api/health')
    parser.add_argument('--container')
    parser.add_argument('--backup-status-dir', type=Path)
    parser.add_argument('--notify', action='store_true')
    parser.add_argument('--webhook-file', type=Path)
    parser.add_argument('--state-file', type=Path, default=Path('/tmp/qingji-monitor-state.json'))
    args = parser.parse_args()
    settings = Settings(data_dir=args.data_dir.resolve(), global_model_calls_per_day=int(os.environ.get('GLOBAL_MODEL_CALLS_PER_DAY', '5000')))
    report = inspect(settings, args.health_url)
    if args.container:
        try:
            stats = subprocess.run(['docker', 'stats', '--no-stream', '--format', '{{.MemPerc}}', args.container], capture_output=True, text=True, timeout=10, check=True)
            if float(stats.stdout.strip().rstrip('%')) >= 85:
                report['problems'].append('container_memory_above_85_percent')
        except Exception:
            report['problems'].append('container_metrics_unavailable')
    if args.backup_status_dir:
        try:
            stamp = int((args.backup_status_dir / 'last-backup').read_text().strip())
            if time.time() - stamp > 26 * 3600:
                report['problems'].append('backup_overdue_26h')
        except (OSError, ValueError):
            report['problems'].append('backup_missing')
        if (args.backup_status_dir / 'backup-failed').exists():
            report['problems'].append('backup_failed')
    report['problems'] = sorted(set(report['problems']))
    report['ok'] = not report['problems']
    if args.notify:
        if not args.webhook_file:
            parser.error('--notify requires --webhook-file')
        try:
            notify_changed(report, args.state_file, args.webhook_file.read_text().strip())
        except Exception:
            report['ok'] = False
            report['problems'].append('alert_delivery_failed')
    print(json.dumps(report))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
