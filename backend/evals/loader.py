"""Load evaluation cases without executing or importing user data."""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import yaml

from .schema import EvalScenario


@dataclass(frozen=True)
class ScenarioSet:
    scenarios: tuple[EvalScenario, ...]
    digest: str
    files: tuple[str, ...]


def load_scenarios(path: Path, suites: set[str] | None = None) -> ScenarioSet:
    path = Path(path)
    files = [path] if path.is_file() else sorted((*path.rglob('*.yaml'), *path.rglob('*.yml')))
    if not files:
        raise ValueError('evaluation_cases_not_found')
    scenarios, sources = [], []
    for file in files:
        raw = yaml.safe_load(file.read_text(encoding='utf-8'))
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            scenario = EvalScenario.model_validate(value)
            if suites is None or scenario.suite in suites:
                scenarios.append(scenario)
                sources.append(str(file.resolve()))
    if not scenarios:
        raise ValueError('evaluation_suite_empty')
    ids = [scenario.id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate_evaluation_case_id')
    canonical = json.dumps(
        [scenario.model_dump(mode='json') for scenario in sorted(scenarios, key=lambda item: item.id)],
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    )
    return ScenarioSet(
        scenarios=tuple(scenarios),
        digest=hashlib.sha256(canonical.encode('utf-8')).hexdigest(),
        files=tuple(sources),
    )
