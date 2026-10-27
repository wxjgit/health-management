"""Bounded metadata logging, including sanitization before Python handlers run."""
from datetime import datetime, timedelta, timezone
import logging
import os
import re
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
import time


class SafeEvent(str):
    """Only constructed after event validation and JSON encoding."""


_guard = Lock()
_users = 0
_original_factory = None


def acquire_logging_boundary():
    global _users, _original_factory
    with _guard:
        if not _users:
            _original_factory = logging.getLogRecordFactory()
            original = _original_factory
            def factory(*args, **kwargs):
                record = original(*args, **kwargs)
                if not isinstance(record.msg, SafeEvent):
                    record.msg = 'unclassified_library_log'
                record.args = ()
                record.exc_info = record.exc_text = record.stack_info = None
                return record
            logging.setLogRecordFactory(factory)
        _users += 1


def release_logging_boundary():
    global _users
    with _guard:
        _users -= 1
        if not _users:
            logging.setLogRecordFactory(_original_factory)


class FileSink:
    def __init__(self, directory, dropped, depth, *, capacity=1024, budget=30*1024*1024):
        self.directory = Path(directory)
        self.dropped, self.depth, self.budget = dropped, depth, budget
        self.queue = Queue(capacity)
        self.stop = Event()
        self.closed = False
        self.thread = Thread(target=self.run, name='safe-observability-log', daemon=True)
        self.thread.start()

    def submit(self, line):
        if self.closed:
            self.dropped('shutdown')
            return
        try:
            self.queue.put_nowait(line)
            self.depth(self.queue.qsize())
        except Full:
            self.dropped('queue_full')

    def cleanup(self, moment=None, reserve=0):
        moment = moment or datetime.now(timezone.utc)
        cutoff = (moment.date() - timedelta(days=6)).isoformat()
        files = []
        for path in self.directory.glob('????-??-??-??????.jsonl'):
            if path.is_symlink() or not re.fullmatch(r'\d{4}-\d{2}-\d{2}-\d{6}\.jsonl', path.name):
                continue
            if path.name[:10] < cutoff:
                path.unlink()
            else:
                files.append(path)
        files.sort()
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total + reserve <= self.budget:
                break
            size = path.stat().st_size
            path.unlink()
            total -= size

    def write(self, line):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.is_symlink():
            raise OSError('unsafe_log_directory')
        if os.name != 'nt':
            self.directory.chmod(0o700)
        data = (line + '\n').encode('utf-8')
        self.cleanup(reserve=len(data))
        day = datetime.now(timezone.utc).date().isoformat()
        paths = sorted(self.directory.glob(day + '-??????.jsonl'))
        index = int(paths[-1].stem[-6:]) if paths else 0
        target = self.directory / f'{day}-{index:06d}.jsonl'
        if target.exists() and target.stat().st_size + len(data) > 10*1024*1024:
            target = self.directory / f'{day}-{index+1:06d}.jsonl'
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
        if target.is_symlink():
            raise OSError('unsafe_log_file')
        descriptor = os.open(target, flags, 0o600)
        with os.fdopen(descriptor, 'ab') as handle:
            handle.write(data)

    def run(self):
        while not self.stop.is_set() or not self.queue.empty():
            try:
                line = self.queue.get(timeout=.2)
            except Empty:
                try:
                    if self.directory.exists():
                        self.cleanup()
                except OSError:
                    self.dropped('io_error')
                continue
            lines = [line]
            # One filesystem append for a short, bounded batch. Business threads
            # still only use put_nowait; batching never holds a business lock.
            deadline = time.monotonic() + .02
            while len(lines) < 32:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    break
                try:
                    lines.append(self.queue.get(timeout=remaining))
                except Empty:
                    break
            try:
                self.write('\n'.join(lines))
            except (OSError, ValueError):
                for _ in lines:
                    self.dropped('io_error')
            finally:
                for _ in lines:
                    self.queue.task_done()
                self.depth(self.queue.qsize())

    def close(self):
        self.closed = True
        self.stop.set()
        self.thread.join(timeout=2)

