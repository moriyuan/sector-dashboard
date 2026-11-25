"""Small flock-compatible exclusive lock for Linux/macOS and local Windows runs."""
import os

if os.name != 'nt':
    from fcntl import flock, LOCK_EX, LOCK_NB, LOCK_UN
else:
    import errno
    import msvcrt
    import time

    LOCK_EX, LOCK_NB, LOCK_UN = 2, 4, 8

    def flock(file, operation):
        fd = file if isinstance(file, int) else file.fileno()
        os.lseek(fd, 0, os.SEEK_SET)
        if operation == LOCK_UN:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            return
        if operation not in (LOCK_EX, LOCK_EX | LOCK_NB):
            raise ValueError('Only exclusive file locks are supported')
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if operation & LOCK_NB:
                    raise BlockingIOError(exc.errno, 'File is already locked') from exc
                time.sleep(0.05)
