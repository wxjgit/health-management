"""Synthetic integration checks against the dedicated test project only."""
import argparse
from datetime import datetime, timezone
import http.client
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import time
from uuid import UUID, uuid4


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError('test_docker_operation_failed')
    return result.stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18000)
    parser.add_argument('--output', type=Path, default=Path('docs/evidence/runtime-gateway-smoke.json'))
    args = parser.parse_args()
    project = 'qingji-runtime-test'
    label = docker('inspect', '--format', '{{index .Config.Labels "com.docker.compose.project"}}', project + '-app-1').strip()
    if label != project:
        raise RuntimeError('test_project_required')
    checks = {}

    def request(method, path, body=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', args.port, timeout=15)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            payload = response.read()
            UUID(response.getheader('X-Request-ID'))
            return response.status, json.loads(payload)
        finally:
            conn.close()

    checks['health'] = request('GET', '/api/health')[0] == 200
    token = os.environ.get('QINGJI_TEST_TOKEN', '')
    if not token:
        # Docker ownership was verified above; mint only a synthetic account in
        # the dedicated test project, without a production login bypass route.
        seed = "from app.common.config import Settings; from app.api.auth import AuthStore; from app.api.policies import policy; s=Settings.from_env(); a=AuthStore(s); r=a.issue('qingji-runtime-synthetic-probe'); a.save_consent(r['user']['id'],policy(s)['version'],False); print(r['access_token'])"
        token = docker('exec', '-w', '/app/backend', project + '-app-1', 'python', '-c', seed).strip()
        if not token:
            raise RuntimeError('authenticated_synthetic_test_token_required')
    headers = {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token}
    checks['body_65536_allowed_to_validation'] = request('POST', '/api/chat', b'x' * 65536, headers)[0] == 422
    status, body = request('POST', '/api/chat', b'x' * 65537, headers)
    checks['body_65537_rejected'] = status == 413 and body['code'] == 'request_body_too_large'
    checks['compressed_body_rejected'] = request('POST', '/api/chat', b'x', {**headers, 'Content-Encoding': 'gzip'})[0] == 415
    payload = json.dumps({'request_id': str(uuid4()), 'text': '今天体重65公斤', 'processing_mode': 'local_only'}).encode()
    first = request('POST', '/api/chat', payload, headers)
    second = request('POST', '/api/chat', payload, headers)
    checks['local_only_and_idempotent_replay'] = first == second and first[0] == 200
    for event_id in first[1].get('saved_record_ids', []):
        checks['synthetic_record_cleanup'] = request('DELETE', '/api/events/' + event_id, headers=headers)[0] == 200

    sock = socket.create_connection(('127.0.0.1', args.port), timeout=15)
    started, response = time.monotonic(), b''
    try:
        sock.sendall(b'POST /api/chat HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n')
        while time.monotonic() - started < 14:
            if select.select([sock], [], [], 0.9)[0]:
                response += sock.recv(8192)
                if b'408' in response.split(b'\r\n', 1)[0]:
                    break
            sock.sendall(b'1\r\nx\r\n')
        checks['gateway_absolute_drip_deadline'] = b'408' in response.split(b'\r\n', 1)[0] and time.monotonic() - started < 13
    finally:
        sock.close()

    proxy_probe = '''import json,socket
result={}
for name,target in [('other_host','example.com:443'),('literal','127.0.0.1:443'),('metadata','169.254.169.254:443'),('wrong_port','dashscope.aliyuncs.com:80'),('suffix','dashscope.aliyuncs.com.attacker.invalid:443')]:
 s=socket.create_connection(('egress',3128),timeout=3)
 s.sendall(('CONNECT '+target+' HTTP/1.1\\r\\nHost: '+target+'\\r\\n\\r\\n').encode())
 line=s.recv(4096).split(b'\\r\\n',1)[0]
 result[name]=b'403' in line;s.close()
print(json.dumps(result))
'''
    checks.update({'proxy_denies_' + k: v for k, v in json.loads(docker('exec', project + '-app-1',
                                                                 'python', '-c', proxy_probe)).items()})
    approved_probe = '''import socket
s=socket.create_connection(('egress',3128),timeout=15)
s.sendall(b'CONNECT dashscope.aliyuncs.com:443 HTTP/1.1\\r\\nHost: dashscope.aliyuncs.com:443\\r\\n\\r\\n')
print(int(b'200' in s.recv(4096).split(b'\\r\\n',1)[0]));s.close()
'''
    checks['proxy_approved_provider_connect'] = docker('exec', project + '-app-1', 'python', '-c', approved_probe).strip() == '1'
    checks['synthetic_account_cleanup'] = request('DELETE', '/api/auth/account', json.dumps({'confirmation': '注销账号'}).encode(), headers)[0] == 200
    report = {'date': datetime.now(timezone.utc).isoformat(), 'project': project, 'checks': checks,
              'passed': all(checks.values()), 'real_model_called': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
