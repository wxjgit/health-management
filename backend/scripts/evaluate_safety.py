"""Synthetic classifier evaluation; no user database or business writes.

From backend/: python -m scripts.evaluate_safety --run --output PATH
Without --run only validate/count the fixture. Reports never contain API keys,
upstream response bodies, or user history. This is not clinical validation.
"""
import argparse
import json
from pathlib import Path
import statistics
import time

from app.common.config import Settings
from app.contracts.safety import SafetyContext
from app.health.safety import POLICY_VERSION, PROMPT_VERSION, merge_classification, text_risk
from app.modeling.parser import Parser


def evaluate(parser, cases):
    rows = []
    for case in cases:
        started = time.perf_counter()
        category = text_risk(case['text'])
        source = 'local'
        failed = False
        if category == 'general':
            source = 'model'
            context = SafetyContext.model_validate({
                'sources': [{'source_id': 'current', 'text': case['text']}],
            }).model_dump()
            try:
                with parser.boundary.request(False, lambda *args: None):
                    parser.boundary.grant('safety_classification', {'context': context})
                    result = parser.classify_safety(context)
                    category = merge_classification(result.model_dump(), context)['category']
            except (ValueError, TypeError):
                category, failed = 'uncertain', True
        row = {'id': case['id'], 'expected': case['expected'], 'predicted': category,
               'source': source, 'classifier_failed': failed,
               'latency_ms': round((time.perf_counter() - started) * 1000)}
        rows.append(row)
        print(f'{len(rows)}/{len(cases)} {case["id"]}: {category}', flush=True)
    risk = [row for row in rows if row['expected'] != 'general']
    ordinary = [row for row in rows if row['expected'] == 'general']
    times = sorted(row['latency_ms'] for row in rows)
    return {
        'policy_version': POLICY_VERSION, 'prompt_version': PROMPT_VERSION,
        'model': parser.settings.model_name, 'count': len(rows),
        'risk_routed_recall': sum(row['predicted'] != 'general' for row in risk) / len(risk) if risk else None,
        'ordinary_false_positive_rate': sum(row['predicted'] != 'general' for row in ordinary) / len(ordinary) if ordinary else None,
        'classifier_failure_count': sum(row['classifier_failed'] for row in rows),
        'mean_latency_ms': round(statistics.mean(times)) if times else None,
        'p95_latency_ms': times[min(len(times) - 1, int(len(times) * .95))] if times else None,
        'category_results': {category: {
            'count': sum(row['expected'] == category for row in rows),
            'correct': sum(row['expected'] == category and row['predicted'] == category for row in rows),
        } for category in sorted({row['expected'] for row in rows})},
        'rows': rows,
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--run', action='store_true', help='Send synthetic cases to the configured model provider')
    cli.add_argument('--cases', type=Path, default=Path(__file__).resolve().parents[1] / 'tests/fixtures/safety_semantic_cases.json')
    cli.add_argument('--output', type=Path)
    args = cli.parse_args()
    cases = json.loads(args.cases.read_text(encoding='utf-8'))
    assert cases and all(set(case) == {'id', 'text', 'expected'} for case in cases)
    if not args.run:
        print(f'Validated {len(cases)} synthetic cases. Add --run to evaluate the configured provider.')
        return
    settings = Settings.from_env()
    if settings.mode != 'model' or settings.local_only:
        raise SystemExit('Model configuration must be available and allow external calls.')
    report = evaluate(Parser(settings), cases)
    encoded = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded + '\n', encoding='utf-8')
    print(encoded)


if __name__ == '__main__':
    main()
