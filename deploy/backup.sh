#!/usr/bin/env bash
set -euo pipefail
: "${QINGJI_STACK_DIR:?required}"
: "${HEALTH_DATA_SOURCE:?absolute host data directory required}"
: "${QINGJI_BACKUP_STAGING:?private staging directory required}"
: "${RESTIC_REPOSITORY:?required}"
: "${RESTIC_PASSWORD_FILE:?required}"
: "${QINGJI_PYTHON:?host Python with backend dependencies required}"
[[ "$HEALTH_DATA_SOURCE" = /* && "$QINGJI_BACKUP_STAGING" = /* ]]
cd "$QINGJI_STACK_DIR"
umask 077
mkdir -p "$QINGJI_BACKUP_STAGING" /var/lib/qingji
work=$(mktemp -d "$QINGJI_BACKUP_STAGING/qingji.XXXXXXXX")
snapshot="$work/snapshot"
compose=(docker compose -p qingji-hardened -f compose.hardened.yaml)
was_running=false
supervised=false
[[ -n "$("${compose[@]}" ps --status running -q app)" ]] && was_running=true
systemctl is-active --quiet qingji-supervisor.service && supervised=true
cleanup() {
  result=$?
  if [[ "$was_running" = true ]]; then "${compose[@]}" up -d app || result=1; fi
  if [[ "$supervised" = true ]]; then systemctl start qingji-supervisor.service || result=1; fi
  rm -rf -- "$work"
  if [[ "$result" != 0 ]]; then touch /var/lib/qingji/backup-failed; fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 1' TERM INT
if [[ "$supervised" = true ]]; then systemctl stop qingji-supervisor.service; fi
"${compose[@]}" stop app
PYTHONPATH=backend "$QINGJI_PYTHON" -m app.operations.backup create --data-dir "$HEALTH_DATA_SOURCE" --output "$snapshot"
if [[ "$was_running" = true ]]; then "${compose[@]}" up -d app; fi
if [[ "$supervised" = true ]]; then systemctl start qingji-supervisor.service; supervised=false; fi
restic backup "$snapshot" --tag qingji-health --host qingji-health
restic forget --tag qingji-health --host qingji-health --group-by host,tags --keep-within "${BACKUP_RETENTION_DAYS:-7}d" --prune
restic check
date +%s > /var/lib/qingji/last-backup
rm -f /var/lib/qingji/backup-failed
