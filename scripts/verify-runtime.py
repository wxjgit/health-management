"""Read-only deployment inspection and synthetic probes. No real database reads."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys


def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise RuntimeError('docker_probe_failed')
    return result.stdout


def verify(project):
    import importlib.util
    spec = importlib.util.spec_from_file_location('supervision', Path(__file__).with_name('supervise-runtime.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    app = module.app_container(project)
    info = json.loads(docker('inspect', app))[0]
    host, config = info['HostConfig'], info['Config']
    mounts = info['Mounts']
    allowed = {'/data', '/run/secrets/model_api_key'}
    checks = {
        'non_root': config.get('User') == '10001:10001',
        'read_only': host.get('ReadonlyRootfs') is True,
        'capabilities': host.get('CapDrop') == ['ALL'] and not host.get('CapAdd'),
        'no_new_privileges': 'no-new-privileges:true' in host.get('SecurityOpt', []),
        'no_privileged_or_host_namespaces': not host.get('Privileged') and host.get('NetworkMode') != 'host' and host.get('PidMode') != 'host',
        'mount_allowlist': all(m['Destination'] in allowed for m in mounts),
        'secret_readonly': any(m['Destination'] == '/run/secrets/model_api_key' and not m['RW'] for m in mounts),
        'memory': host.get('Memory') == 512 * 1024 * 1024,
        'cpu': host.get('NanoCpus') == 1000000000,
        'pids': host.get('PidsLimit') == 128,
        'no_app_port_publishing': not host.get('PortBindings'),
        'healthcheck': info['State'].get('Health', {}).get('Status') == 'healthy',
    }
    probe = '''import json,os,socket,tempfile
result={'uid':os.getuid()==10001}
for name,path,expected in [('code','/app/backend/app/.permission-probe',False),('data','/data/.permission-probe',True),('temporary','/tmp/.permission-probe',True)]:
 try:
  fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd);os.unlink(path);actual=True
 except OSError:actual=False
 result[name]=actual==expected
for name,host,port in [('metadata','169.254.169.254',80),('host_gateway','172.30.80.1',80),('public_direct','1.1.1.1',443)]:
 try:
  s=socket.create_connection((host,port),timeout=1);s.close();result[name]=False
 except OSError:result[name]=True
print(json.dumps(result))
'''
    probes = json.loads(docker('exec', app, 'python', '-c', probe))
    checks.update({'probe_' + k: v for k, v in probes.items()})
    # These require controlled endpoints and host firewall evidence, not just a timeout.
    return {'date': datetime.now(timezone.utc).isoformat(), 'project': project,
            'image': info['Image'], 'checks': checks,
            'unverified': ['controlled_public_positive_and_negative', 'proxy_dns_rebinding',
                           'host_firewall_and_ipv6', 'hard_recovery_and_two_database_consistency',
                           'resource_stress_and_disk_full'],
            'accepted': False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', default='qingji-hardened')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = verify(args.project)
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        result = {'accepted': False, 'error': 'runtime_verification_unavailable'}
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    return 0 if result.get('accepted') else 1


if __name__ == '__main__':
    sys.exit(main())
