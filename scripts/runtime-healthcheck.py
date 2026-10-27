"""Independent process: no import of the application or personal data."""
import json
import math
from pathlib import Path
import sys
import time


def healthy(payload, now):
    if not isinstance(payload, dict) or payload.get('ready') is not True:
        return False
    heartbeat = payload.get('heartbeat')
    deadlines = payload.get('deadlines')
    if not isinstance(heartbeat, (int, float)) or not math.isfinite(heartbeat) or not 0 <= now - heartbeat <= 5:
        return False
    return (isinstance(deadlines, list) and len(deadlines) <= 32
            and all(isinstance(d, (int, float)) and math.isfinite(d) and now <= d + 15 for d in deadlines))


def main():
    try:
        path = Path('/tmp/runtime-status.json')
        if path.stat().st_size > 8192:
            return 1
        return 0 if healthy(json.loads(path.read_text()), time.monotonic()) else 1
    except (OSError, ValueError, TypeError):
        return 1


if __name__ == '__main__':
    sys.exit(main())
