"""A process-lifetime OS lock; never unlink the locked file."""
import os


class DataDirectoryInUse(RuntimeError):
    def __init__(self):
        super().__init__('data_directory_in_use')


class DataOwner:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        self.file = (directory / '.engine.lock').open('a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.file.seek(0, 2) == 0:
                    self.file.write(b'0')
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.file.close()
            raise DataDirectoryInUse() from error

    def close(self):
        if self.file.closed:
            return
        if os.name == 'nt':
            import msvcrt
            self.file.seek(0)
            msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()
