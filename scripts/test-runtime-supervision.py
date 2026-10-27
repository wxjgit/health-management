"""Fault injection is restricted to the dedicated synthetic test project."""
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import time


def main():
    path = Path(__file__).with_name('supervise-runtime.py')
    spec = importlib.util.spec_from_file_location('supervision', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    project = 'qingji-runtime-test'
    container = module.app_container(project)
    before = json.loads(module.docker('inspect', '--format', '{{json .State}}', container))
    if before.get('Health', {}).get('Status') != 'healthy':
        raise RuntimeError('healthy_test_container_required')
    started = time.monotonic()
    module.docker('exec', container, 'python', '-c',
        "import os,signal; from pathlib import Path; p=Path('/proc/1/task/1/children').read_text().split(); "
        "assert len(p)==1 and int(p[0])>1; os.kill(int(p[0]),signal.SIGSTOP)")
    recovered = False
    try:
        for _ in range(20):
            state = json.loads(module.docker('inspect', '--format', '{{json .State}}', container))
            if state.get('Health', {}).get('Status') == 'unhealthy':
                break
            time.sleep(1)
        else:
            raise RuntimeError('unresponsive_process_not_detected')
        module.supervise(project, once=True)
        for _ in range(25):
            state = json.loads(module.docker('inspect', '--format', '{{json .State}}', container))
            if state.get('Health', {}).get('Status') == 'healthy' and state['StartedAt'] != before['StartedAt']:
                recovered = True
                break
            time.sleep(1)
        report = {'date': datetime.now(timezone.utc).isoformat(), 'project': project,
                  'unresponsive_process_detected': True, 'external_supervisor_restarted_app': recovered,
                  'seconds': round(time.monotonic() - started, 2), 'real_model_called': False}
        output = Path('docs/evidence/runtime-supervision.json')
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report))
        return 0 if recovered else 1
    finally:
        if not recovered:
            module.docker('restart', '--time', '1', container)


if __name__ == '__main__':
    raise SystemExit(main())
