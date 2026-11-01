import fcntl
import logging
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class LockHeld(Exception):
    pass


@contextmanager
def pipeline_lock(data_dir, wait: bool = False):
    """Exclusive lock on data/pipeline.lock; the kernel drops it if we die.

    If another process holds it: wait=False raises LockHeld, wait=True blocks.
    """
    with open(Path(data_dir) / "pipeline.lock", "w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if not wait:
                raise LockHeld from None
            logger.info("pipeline lock held; waiting")
            fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
