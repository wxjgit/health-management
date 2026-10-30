"""Timezone-aware application clock helpers."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')
_NOW_OVERRIDE = ContextVar('qingji_now_override', default=None)


def now():
    value = _NOW_OVERRIDE.get()
    return value if value is not None else datetime.now(SHANGHAI)


@contextmanager
def override_now(value):
    """Temporarily fix business time for one isolated execution context."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('clock_override_requires_timezone')
    token = _NOW_OVERRIDE.set(value.astimezone(SHANGHAI))
    try:
        yield
    finally:
        _NOW_OVERRIDE.reset(token)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def local_day(value):
    return datetime.fromisoformat(value).astimezone(SHANGHAI).date().isoformat()


def date_from_text(text, reference=None):
    import re
    reference = reference or now()
    found = re.search(r'(20\d{2}-\d{2}-\d{2})', text)
    if found:
        return datetime.fromisoformat(found[1]).replace(tzinfo=SHANGHAI)
    offset = 2 if '前天' in text else 1 if any(w in text for w in ['昨天', '昨晚']) else 0
    return (reference - timedelta(days=offset)).replace(hour=12, minute=0, second=0, microsecond=0)
