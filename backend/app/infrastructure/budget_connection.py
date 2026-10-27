"""Fence SQL and commits after cooperative execution cancellation."""
import sqlite3
import time

from ..common.execution_budget import check_budget


class BudgetConnection(sqlite3.Connection):
    database_role = 'business'
    observer = None

    def _observe(self, name, value=1, **labels):
        from ..observability.context import current
        observer = self.observer or current.get()
        if observer:
            observer.observe(name, value, database=self.database_role, **labels)

    def _finish_timing(self, outcome):
        started = getattr(self, '_transaction_started', None)
        self._transaction_started = None
        if started is not None:
            self._observe('db_transaction_duration_seconds', time.monotonic()-started, outcome=outcome)

    def __enter__(self):
        check_budget()
        depth = getattr(self, '_context_depth', 0)
        if depth:
            super().execute(f'SAVEPOINT repo_nested_{depth}')
        self._context_depth = depth + 1
        return self

    def _guard_write(self, sql):
        if sql.lstrip().split(None, 1)[0].upper() in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE'):
            from ..jobs.context import check_job
            check_job(self)

    def execute(self, *args, **kwargs):
        check_budget()
        self._guard_write(args[0])
        started = time.monotonic()
        begin = args[0].strip().upper() == 'BEGIN IMMEDIATE'
        try:
            return super().execute(*args, **kwargs)
        except sqlite3.OperationalError as error:
            code = getattr(error, 'sqlite_errorcode', 0) & 255
            if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                self._observe('db_busy', category='busy' if code == sqlite3.SQLITE_BUSY else 'locked')
            raise
        finally:
            if self.in_transaction and getattr(self, '_transaction_started', None) is None:
                self._transaction_started = started
            if begin:
                self._observe('db_write_begin_duration_seconds', time.monotonic()-started)

    def executemany(self, *args, **kwargs):
        check_budget()
        self._guard_write(args[0])
        started = time.monotonic()
        try:
            return super().executemany(*args, **kwargs)
        finally:
            if self.in_transaction and getattr(self, '_transaction_started', None) is None:
                self._transaction_started = started

    def executescript(self, *args, **kwargs):
        check_budget()
        if getattr(self, '_context_depth', 0):
            raise RuntimeError('executescript_inside_transaction')
        return super().executescript(*args, **kwargs)

    def commit(self):
        check_budget()
        result = super().commit()
        self._finish_timing('ok')
        return result

    def rollback(self):
        result = super().rollback()
        self._finish_timing('error')
        return result

    def __exit__(self, kind, value, traceback):
        outer = getattr(self, '_context_depth', 1) <= 1
        outcome = 'ok' if kind is None else 'error'
        try:
            return self._exit_context(kind, value, traceback)
        except BaseException:
            outcome = 'error'
            raise
        finally:
            if outer and not self.in_transaction:
                self._finish_timing(outcome)

    def _exit_context(self, kind, value, traceback):
        depth = max(0, getattr(self, '_context_depth', 1) - 1)
        self._context_depth = depth
        if kind is None:
            try:
                check_budget()
            except Exception as error:
                if depth:
                    super().execute(f'ROLLBACK TO repo_nested_{depth}')
                    super().execute(f'RELEASE repo_nested_{depth}')
                else:
                    super().__exit__(type(error), error, error.__traceback__)
                raise
        if depth:
            if kind is not None:
                super().execute(f'ROLLBACK TO repo_nested_{depth}')
            super().execute(f'RELEASE repo_nested_{depth}')
            return False
        return super().__exit__(kind, value, traceback)
