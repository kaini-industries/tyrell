"""One nonblocking host lock for operations on Tyrell's shared build VM."""
from contextlib import contextmanager
import fcntl
from pathlib import Path


@contextmanager
def builder_lock(root):
    path = Path(root) / 'build/builder.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another operation owns the Tyrell builder; retry after it finishes') from error
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)
