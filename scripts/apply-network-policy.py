"""Explicit Linux iptables policy for the fixed Compose subnets; never automatic."""
import argparse
import os
import subprocess
import sys

CHAIN = 'QINGJI-HARDENED'
APPS = ('172.30.80.3/32', '172.30.81.3/32')


def iptables(*args, check=True):
    result = subprocess.run(['iptables', '-w', '5', *args], capture_output=True, timeout=10)
    if check and result.returncode:
        raise RuntimeError('network_policy_operation_failed')
    return result.returncode == 0


def apply():
    if sys.platform != 'linux' or os.geteuid() != 0:
        raise RuntimeError('linux_root_required')
    if not iptables('-S', 'DOCKER-USER', check=False):
        raise RuntimeError('docker_iptables_backend_required')
    if not iptables('-S', CHAIN, check=False):
        iptables('-N', CHAIN)
    rules = [('-m', 'conntrack', '--ctstate', 'ESTABLISHED,RELATED', '-j', 'RETURN'),
             ('-d', '172.30.81.2/32', '-p', 'tcp', '--dport', '3128', '-j', 'RETURN'),
             ('-j', 'DROP')]
    # Only append a complete initial policy. Existing chains must match exactly.
    existing = subprocess.run(['iptables', '-w', '5', '-S', CHAIN], capture_output=True, text=True, timeout=10)
    entries = [line for line in existing.stdout.splitlines() if line.startswith('-A ')]
    if entries and (len(entries) != len(rules) or not all(iptables('-C', CHAIN, *r, check=False) for r in rules)):
        raise RuntimeError('existing_policy_differs')
    if not entries:
        for rule in rules:
            iptables('-A', CHAIN, *rule)
    for source in APPS:
        if not iptables('-C', 'DOCKER-USER', '-s', source, '-j', CHAIN, check=False):
            iptables('-I', 'DOCKER-USER', '1', '-s', source, '-j', CHAIN)
        # Docker's FORWARD chain does not protect services on the host itself.
        rule = ('-s', source, '-j', 'DROP')
        if not iptables('-C', 'INPUT', *rule, check=False):
            iptables('-I', 'INPUT', '1', *rule)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true', required=True)
    parser.parse_args()
    try:
        apply()
        print('network_policy_applied')
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        sys.exit('network_policy_failed')
