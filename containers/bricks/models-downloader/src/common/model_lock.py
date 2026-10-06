# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""The per-model lock that keeps two runs off the same model directory.

A download or delete holds an exclusive ``flock`` on ``<models>/.locks/<key>.lock`` for
as long as it runs; check tests it. The kernel releases the lock when the process exits,
however it exits, so a killed run leaves no stale lock behind: a ".download" marker with
the lock free is the leftover of a dead run, never a download in progress.

The file lives outside the model directory on purpose. A cleanup removes the whole model
directory, and a lock file removed and created again is a new file whose lock guards
nothing. Nothing removes ``.locks``.

``common/model_lock.sh`` is the shell side of the same lock, with the same file names.
"""

import fcntl
import os
import time

LOCKS_DIRNAME = ".locks"
# The code of the error event a run reports when another run holds the lock.
BUSY_CODE = "download_in_progress"
# How long a run waits for the lock: long enough to outlast a check, which holds it for
# an instant, too short to wait for another download.
WAIT_SECONDS = 2.0

# The descriptors of the locks this process holds, closed by release_all.
_held: list[int] = []


def lock_path(models_dir: str, key: str) -> str:
    """The lock file of the model directory *key*, relative to *models_dir*."""
    return os.path.join(models_dir, LOCKS_DIRNAME, key.strip("/").replace("/", "__") + ".lock")


def acquire(models_dir: str, key: str) -> bool:
    """Take the lock of *key* and hold it until the process exits or release_all.

    Returns False when another process holds it for longer than WAIT_SECONDS.
    """
    path = lock_path(models_dir, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o664)
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return False
            time.sleep(0.1)
            continue
        _held.append(fd)
        return True


def release_all() -> None:
    """Release every lock this process holds."""
    while _held:
        os.close(_held.pop())


def is_locked(models_dir: str, key: str) -> bool:
    """Whether another process holds the lock of *key*. Creates nothing."""
    try:
        fd = os.open(lock_path(models_dir, key), os.O_RDONLY)
    except FileNotFoundError:
        return False
    try:
        # Shared, so two checks never block each other.
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False
