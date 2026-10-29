import json
from pathlib import Path
from datetime import datetime, timezone

from app.operations import monitor


def test_alerts_are_quiet_when_unchanged_and_include_recovery(tmp_path, monkeypatch):
    sent = []
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): pass
    def deliver(request, **kwargs):
        sent.append(json.loads(request.data))
        return Response()
    monkeypatch.setattr(monitor, 'urlopen', deliver)
    state = tmp_path / 'monitor-state.json'
    for problems in ([], [], ['api_unavailable'], ['api_unavailable'], []):
        monitor.notify_changed({'problems': problems}, state, 'https://synthetic.invalid/hook')
    assert len(sent) == 2
    assert 'api_unavailable' in sent[0]['text']
    assert '恢复' in sent[1]['text']


def test_traffic_checks_only_structured_metadata_and_skips_bad_lines(tmp_path):
    timestamp = datetime.now(timezone.utc).isoformat()
    records = [{'timestamp': timestamp, 'event': 'http_finished', 'status_class': '5xx'},
               {'timestamp': timestamp, 'event': 'model_transport_finished', 'outcome': 'timeout'}]
    (tmp_path / '2026-10-29-000000.jsonl').write_text('\n'.join(json.dumps(record) for record in records) + '\npartial')
    result = monitor.traffic_snapshot(tmp_path)
    assert result == {'requests': 1, 'server_errors': 1, 'model_calls': 1, 'model_errors': 1}
