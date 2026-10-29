"""Offline local administration. Running Engine ownership prevents competing writes."""
import argparse
import json
from pathlib import Path

from ..common.config import Settings
from ..infrastructure.data_owner import DataDirectoryInUse, DataOwner
from ..infrastructure.tenants import tenant_path
from ..workflows.engine import Engine


def main():
    parser = argparse.ArgumentParser(description='Offline task administration; stop the service first.')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--user-id', required=True, help='Internal account UUID')
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('status')
    commands.add_parser('pause')
    commands.add_parser('resume')
    retry = commands.add_parser('retry')
    retry.add_argument('job_id')
    retry.add_argument('--reason', required=True,
                       choices=('operator_retry','configuration_fixed','upstream_recovered'))
    args = parser.parse_args()
    root_owner = None
    try:
        data_dir = args.data_dir.resolve()
        root_owner = DataOwner(data_dir)
        data_dir = tenant_path(data_dir, args.user_id)
        if not (data_dir / 'health.db').is_file():
            raise ValueError('user_database_not_found')
        engine = Engine(Settings(data_dir=data_dir, local_only=True,
                                 scheduler_enabled=False, job_runner_enabled=False), user_id=args.user_id)
    except DataDirectoryInUse:
        if root_owner:
            root_owner.close()
        parser.exit(2, 'data_directory_in_use: stop the service before offline administration.\n')
    except (ValueError, OSError):
        if root_owner:
            root_owner.close()
        parser.exit(2, 'invalid_user_database: specify an existing internal account UUID.\n')
    try:
        if args.action == 'retry':
            engine.repo.retry_failed_job(args.job_id, args.reason)
        elif args.action in ('pause','resume'):
            engine.repo.set_meta('jobs_paused', args.action=='pause')
        print(json.dumps(engine.repo.job_status(), ensure_ascii=False))
    except ValueError:
        parser.exit(2, 'job_retry_not_allowed: inspect job state, expiry and source lifecycle.\n')
    finally:
        engine.close()
        if root_owner:
            root_owner.close()


if __name__ == '__main__':
    main()
