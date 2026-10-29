"""Synthetic local workflow benchmark. Never opens the application's data directory."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))

from app.common.config import Settings
from app.workflows.engine import Engine


def percentile(values, quantile):
    ordered = sorted(values)
    return ordered[min(len(ordered)-1, int((len(ordered)-1)*quantile))]


def measure(enabled, samples, directory):
    engine = Engine(Settings(data_dir=directory, local_only=True,
        scheduler_enabled=False, job_runner_enabled=False, obs_enabled=enabled), user_id=str(uuid4()))
    times = []
    try:
        for index in range(samples+5):
            key = str(uuid4())
            started = time.perf_counter()
            result = engine.run('chat', key, {}, {'input_text': '体重65公斤'})
            elapsed = time.perf_counter()-started
            if result['status'] != 'completed':
                raise RuntimeError('synthetic_benchmark_failed')
            if index >= 5:
                times.append(elapsed)
        dropped = sum(sample.value for family in engine.observability.metrics.registry.collect()
            for sample in family.samples if sample.name == 'health_observability_dropped_total')
        return times, dropped
    finally:
        engine.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--samples', type=int, default=50)
    parser.add_argument('--rounds', type=int, default=4)
    parser.add_argument('--output', type=Path, default=ROOT/'output/observability-benchmark.json')
    args = parser.parse_args()
    if not 20 <= args.samples <= 500 or not 2 <= args.rounds <= 10:
        parser.error('samples must be 20..500; rounds must be 2..10')
    output_root = ROOT/'output'
    output_root.mkdir(exist_ok=True)
    durations = {False: [], True: []}
    rounds = []
    with tempfile.TemporaryDirectory(prefix='observability-benchmark-', dir=output_root) as directory:
        directory = Path(directory).resolve()
        # Verify the sole automatically cleaned directory belongs to this workspace.
        if not directory.is_relative_to(output_root.resolve()):
            raise RuntimeError('unexpected_benchmark_directory')
        for number in range(args.rounds):
            comparison = {}
            for enabled in ((False, True) if number % 2 == 0 else (True, False)):
                values, dropped = measure(enabled, args.samples, directory/f'{number}-{enabled}')
                durations[enabled].extend(values)
                comparison['enabled' if enabled else 'disabled'] = {
                    'p50_seconds': statistics.median(values), 'p95_seconds': percentile(values, .95),
                    'dropped_events': dropped}
            rounds.append(comparison)
    disabled = percentile(durations[False], .95)
    enabled = percentile(durations[True], .95)
    report = {'date_utc': datetime.now(timezone.utc).isoformat(),
        'scenario': 'synthetic_local_weight_workflow', 'samples_per_mode': len(durations[True]),
        'warmup_per_round': 5, 'rounds': rounds,
        'disabled_p95_seconds': disabled, 'enabled_p95_seconds': enabled,
        'p95_relative_increase': enabled/disabled-1,
        'target_relative_increase': .05, 'target_met_in_this_run': enabled <= disabled*1.05,
        'limitations': ['Single machine, local synthetic workflow only.',
                       'Scheduler and model network calls disabled; no production latency claim.',
                       'Concurrent workload and filesystem noise can affect comparisons.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'samples_per_mode': report['samples_per_mode'],
        'p95_relative_increase': report['p95_relative_increase'],
        'target_met_in_this_run': report['target_met_in_this_run']}))


if __name__ == '__main__':
    main()
