# Evaluation cases

These YAML files contain synthetic data only. The default command runs `demo`,
`local_only`, and `mock` scenarios without opening the application's `data/`
directory or calling a real provider.

Each case declares:

- `turns`: chat or resume inputs, expected workflow status, restart and replay behavior;
- `expected_state`: observable persisted health-event state;
- `expected_calls`: minimum/maximum calls per approved model purpose and forbidden markers;
- `hard_invariants`: security rules that cannot be offset by a quality score;
- `mock_responses`: strict responses consumed in order by controlled model scenarios.

Run all bundled cases from `backend/`:

```powershell
..\.venv\Scripts\python.exe -m scripts.evaluate_agent
```

Use `--suite smoke --repeat 5` for repeat stability. A provider scenario must set
`mode: provider`; the CLI rejects it unless `--run-provider` is also supplied.
Provider cases must remain synthetic and should live outside the default case
directory when they are not intended for routine CI.
