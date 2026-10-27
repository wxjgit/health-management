"""Trusted host control plane; never mount the Docker socket in the app."""
import argparse
from collections import deque
import json
import re
import subprocess
import sys
import time


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise RuntimeError('docker_operation_failed')
    return result.stdout.strip()


def app_container(project):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,62}', project):
        raise ValueError('invalid_project')
    ids = docker('ps', '-aq', '--filter', f'label=com.docker.compose.project={project}',
                 '--filter', 'label=com.docker.compose.service=app').splitlines()
    if len(ids) != 1 or not re.fullmatch(r'[a-f0-9]{12,64}', ids[0]):
        raise RuntimeError('expected_one_app_container')
    labels = json.loads(docker('inspect', '--format', '{{json .Config.Labels}}', ids[0]))
    if labels.get('com.docker.compose.project') != project or labels.get('com.docker.compose.service') != 'app':
        raise RuntimeError('container_identity_changed')
    return ids[0]


def supervise(project, once=False):
    restarts = deque()
    while True:
        container = app_container(project)
        state = json.loads(docker('inspect', '--format', '{{json .State}}', container))
        health = state.get('Health', {}).get('Status')
        if health == 'unhealthy' or state.get('Status') in ('exited', 'dead'):
            now = time.monotonic()
            while restarts and now - restarts[0] >= 600:
                restarts.popleft()
            if len(restarts) >= 3:
                docker('stop', '--time', '5', container)
                raise RuntimeError('runtime_restart_limit_exceeded')
            # Revalidate immediately before mutation; no caller supplied ID.
            if app_container(project) != container:
                raise RuntimeError('container_identity_changed')
            docker('restart', '--time', '5', container)
            restarts.append(now)
            print('runtime_restarted', flush=True)
        elif health not in ('starting', 'healthy'):
            raise RuntimeError('runtime_healthcheck_missing')
        if once:
            return
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default='qingji-hardened')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    try:
        supervise(args.project, args.once)
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        print('runtime_supervision_failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
