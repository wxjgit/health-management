"""Redacted machine and reviewer reports for synthetic evaluations."""

import json
from pathlib import Path


def render_markdown(report):
    summary = report['summary']
    lines = [
        '# Agent evaluation report', '',
        f"- Dataset digest: `{report['dataset_digest']}`",
        f"- Git revision: `{report['git_revision']}`",
        f"- Worktree dirty: {report['worktree_dirty']}",
        f"- Repetitions: {report['repetitions']}",
        f"- Scenarios: {summary['scenario_count']}",
        f"- Trials passed: {summary['passed_trials']}/{summary['trial_count']}",
        f"- First-run pass rate: {summary['pass_at_1']:.3f}",
        f"- All-runs pass rate: {summary['pass_power_k']:.3f}", '',
        '| Scenario | Trial | Result | Duration ms | Model calls |',
        '|---|---:|---|---:|---:|',
    ]
    for row in report['trials']:
        lines.append(
            f"| `{row['scenario_id']}` | {row['trial']} | "
            f"{'PASS' if row['passed'] else 'FAIL'} | {row['duration_ms']} | {row['model_call_count']} |"
        )
    failures = [row for row in report['trials'] if row['failures']]
    if failures:
        lines.extend(['', '## Failures', ''])
        for row in failures:
            lines.append(f"### {row['scenario_id']} / trial {row['trial']}")
            lines.append('')
            for failure in row['failures']:
                lines.append(f"- `{failure['code']}`: {failure['detail']}")
            lines.append('')
    return '\n'.join(lines).rstrip() + '\n'


def write_report(report, output: Path):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == '.md':
        markdown, json_path = output, output.with_suffix('.json')
    else:
        json_path = output if output.suffix.lower() == '.json' else output.with_suffix('.json')
        markdown = json_path.with_suffix('.md')
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    markdown.write_text(render_markdown(report), encoding='utf-8')
    return json_path, markdown
