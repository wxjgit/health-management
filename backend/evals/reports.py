"""Redacted machine and reviewer reports for synthetic evaluations."""

import json
from pathlib import Path

from .schema import METRIC_NAMES


def _rate(value):
    return 'N/A' if value is None else f'{value:.3f}'


def render_markdown(report):
    summary = report['summary']
    lines = [
        '# Agent evaluation report', '',
        f"- Dataset digest: `{report['dataset_digest']}`",
        f"- Git revision: `{report['git_revision']}`",
        f"- Worktree dirty: {report['worktree_dirty']}",
        f"- Repetitions: {report['repetitions']}",
        f"- Loaded scenarios: {summary['loaded_scenario_count']}",
        f"- Executed scenarios: {summary['executed_scenario_count']}",
        f"- Ignored scenarios: {summary['ignored_scenario_count']}",
        f"- Trials passed: {summary['passed_trials']}/{summary['trial_count']}",
        f"- First-run pass rate: {summary['pass_at_1']:.3f}",
        f"- All-runs pass rate: {summary['pass_power_k']:.3f}",
        f"- Release result: {'PASS' if summary['release_passed'] else 'FAIL'}", '',
        '## Hard gates', '',
        '| Gate | Status | Assertions | Failures | Leaks | Coverage | Ignored |',
        '|---|---|---:|---:|---:|---|---:|',
    ]
    if report['hard_gates']:
        for name, value in report['hard_gates'].items():
            lines.append(
                f"| `{name}` | {value['status']} | {value['assertion_count']} | "
                f"{value['failure_count']} | {value.get('leak_count', 'N/A')} | "
                f"{value['coverage']} | {value['ignored_case_count']} |"
            )
    else:
        lines.append('| _No selected hard gates_ | NOT_EVALUATED | 0 | 0 | N/A | NONE | 0 |')

    lines.extend([
        '', '## Engineering metrics', '',
        '| Metric | Score | Datasets | Cases | Trials | N/A | Not annotated | Ignored |',
        '|---|---:|---:|---:|---:|---:|---:|---:|',
    ])
    for metric in METRIC_NAMES:
        value = report['metrics'][metric]
        lines.append(
            f"| `{metric}` | {_rate(value['score'])} | {value['valid_dataset_count']} | "
            f"{value['valid_case_count']} | {value['valid_trial_count']} | "
            f"{value['not_applicable_count']} | {value['not_annotated_count']} | "
            f"{value['ignored_count']} |"
        )

    lines.extend([
        '', '## Dataset matrix', '',
        '| Dataset | Primary question | Primary | State | Grounding | Abstention | Temporal | Safety | Reliability | Ignored |',
        '|---|---|---|---:|---:|---:|---:|---:|---:|---:|',
    ])
    for name, value in report['datasets'].items():
        primary = value['primary_metric'] or value['primary_hard_gate'] or 'N/A'
        cells = ' | '.join(_rate(value['scores'][metric]) for metric in METRIC_NAMES)
        lines.append(
            f"| `{name}` | {value['question']} | `{primary}` | {cells} | {value['ignored_count']} |"
        )

    abstention = report['abstention']
    lines.extend([
        '', '## Abstention diagnostics', '',
        f"- Status: {abstention['status']}",
        f"- Score: {_rate(abstention['score'])}",
        f"- Annotated turns: {abstention['annotated_turns']}",
        f"- False answers: {abstention['false_answer_count']}",
        f"- False abstentions: {abstention['false_abstain_count']}",
        f"- Empty replies: {abstention['empty_reply_count']}",
    ])

    if report['ignored']:
        lines.extend([
            '', '## Ignored scenarios', '',
            '| Scenario | Suite | Dataset | Reason |',
            '|---|---|---|---|',
        ])
        for item in report['ignored']:
            reason = str(item['reason']).replace('|', '\\|').replace('\n', ' ')
            lines.append(
                f"| `{item['scenario_id']}` | `{item['suite']}` | `{item['dataset']}` | {reason} |"
            )

    lines.extend([
        '', '## Trials', '',
        '| Scenario | Dataset | Trial | Result | Duration ms | Model calls | Artifact |',
        '|---|---|---:|---|---:|---:|---|',
    ])
    for row in report['trials']:
        lines.append(
            f"| `{row['scenario_id']}` | `{row['dataset']}` | {row['trial']} | "
            f"{'PASS' if row['passed'] else 'FAIL'} | {row['duration_ms']} | "
            f"{row['model_call_count']} | `{row['artifact_path']}` |"
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
