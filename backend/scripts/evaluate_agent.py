"""Run isolated synthetic end-to-end agent evaluations.

From backend/:
  python -m scripts.evaluate_agent
  python -m scripts.evaluate_agent --suite smoke --repeat 5

Provider calls are disabled unless --run-provider is supplied. Evaluation cases
must contain synthetic data and never use the application's data directory.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from app.common.config import ROOT
from evals import EvaluationRunner, load_scenarios
from evals.reports import write_report


def _run_id(scenario_set):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    try:
        revision = subprocess.run(
            ['git', 'rev-parse', '--short=8', 'HEAD'], cwd=ROOT,
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip()
        dirty = bool(subprocess.run(
            ['git', 'status', '--porcelain'], cwd=ROOT,
            capture_output=True, text=True, timeout=3, check=True,
        ).stdout.strip())
    except (OSError, subprocess.SubprocessError):
        revision, dirty = 'unknown', None
    marker = 'dirty' if dirty else 'clean' if dirty is False else 'status-unknown'
    return f'{stamp}-{revision}-{marker}-{scenario_set.digest[:8]}'


def _unique_directory(parent, name):
    candidate = parent / name
    suffix = 1
    while candidate.exists():
        suffix += 1
        candidate = parent / f'{name}-{suffix}'
    candidate.mkdir(parents=True)
    return candidate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--cases', type=Path,
        default=Path(__file__).resolve().parents[1] / 'evals' / 'cases',
    )
    parser.add_argument('--suite', action='append', choices=[
        'smoke', 'regression', 'capability', 'safety_adversarial', 'fault_recovery',
    ])
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--run-provider', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    scenario_set = load_scenarios(args.cases, set(args.suite) if args.suite else None)
    run_id = _run_id(scenario_set)
    if args.output:
        report_output = args.output.resolve()
        artifact_root = _unique_directory(
            report_output.parent, f'{report_output.stem}-artifacts-{run_id}',
        )
    else:
        artifact_root = _unique_directory(ROOT / 'output' / 'evaluations', run_id)
        report_output = artifact_root / 'report.json'
    runner = EvaluationRunner(artifact_root / 'trials', allow_provider=args.run_provider)
    report = runner.run(scenario_set, args.repeat)
    report['run_id'] = artifact_root.name
    report['artifact_root'] = str(artifact_root)
    json_path, markdown_path = write_report(report, report_output)
    manifest = {
        'schema_version': report['schema_version'],
        'run_id': report['run_id'],
        'generated_at': report['generated_at'],
        'git_revision': report['git_revision'],
        'worktree_dirty': report['worktree_dirty'],
        'dataset_digest': report['dataset_digest'],
        'python_version': report['python_version'],
        'suites': sorted(set(args.suite or [])),
        'repetitions': args.repeat,
        'reference_times': sorted({
            scenario.reference_time.isoformat()
            for scenario in scenario_set.scenarios
        }),
        'report_json': str(json_path),
        'report_markdown': str(markdown_path),
        'artifacts': sorted(
            str(path.relative_to(artifact_root))
            for path in artifact_root.rglob('*') if path.is_file()
        ),
    }
    (artifact_root / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8',
    )
    summary = report['summary']
    print(
        f"{summary['passed_trials']}/{summary['trial_count']} trials passed; "
        f'reports: {json_path} and {markdown_path}'
    )
    if not summary['release_passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
