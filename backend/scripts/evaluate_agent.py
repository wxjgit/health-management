"""Run isolated synthetic end-to-end agent evaluations.

From backend/:
  python -m scripts.evaluate_agent
  python -m scripts.evaluate_agent --suite smoke --repeat 5

Provider calls are disabled unless --run-provider is supplied. Evaluation cases
must contain synthetic data and never use the application's data directory.
"""

import argparse
from pathlib import Path
import tempfile

from app.common.config import ROOT
from evals import EvaluationRunner, load_scenarios
from evals.reports import write_report


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
    parser.add_argument(
        '--output', type=Path, default=ROOT / 'output' / 'agent-evaluation.json',
    )
    args = parser.parse_args()
    scenario_set = load_scenarios(args.cases, set(args.suite) if args.suite else None)
    work_parent = ROOT / 'output' / 'eval-work'
    work_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='run-', dir=work_parent) as directory:
        runner = EvaluationRunner(Path(directory), allow_provider=args.run_provider)
        report = runner.run(scenario_set, args.repeat)
    json_path, markdown_path = write_report(report, args.output)
    summary = report['summary']
    print(
        f"{summary['passed_trials']}/{summary['trial_count']} trials passed; "
        f'reports: {json_path} and {markdown_path}'
    )
    if summary['passed_trials'] != summary['trial_count']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
