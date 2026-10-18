"""Process ownership of a data directory, released by the OS even after a crash."""

from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class DataDirectoryInUse(RuntimeError):
    pass


@contextmanager
def data_directory_lock(directory: Path) -> Iterator[None]:
    # Keep the file: unlinking it could let two processes lock different inodes.
    with (directory.resolve() / ".instance.lock").open("a+b") as lock:
        try:
            if os.name == "nt":
                import msvcrt
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            raise DataDirectoryInUse(str(directory)) from exc
        yield  # Closing the handle releases the lock on every exit path.
