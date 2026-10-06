# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""The per-model lock that keeps two containers off the same model directory.

Every download and delete holds an exclusive ``flock`` on
``<models root>/.locks/<models_repository>__<key>.lock`` for as long as it runs, and
``check`` tests it. The host starts one container per request and holds no lock of its
own, so this file is the only thing that tells a second download of the same model it
must not touch the directory: without it, the second run would read the first one's
``.download`` marker as the leftover of a killed run and wipe its files.

The key is the model directory inside its repository: ``model_directory`` for AI Hub,
the ``model_name`` stem for Edge Impulse, the ``repo_id`` for Hugging Face (every
quantization of a repository shares its directory, so they share its lock too).

The models root is the whole ``MODELS_PATH``, mounted at ``/models_root`` next to the
handler's own repository mount (override with ``MODELS_ROOT``). The lock is released by
the kernel when the process exits, however it exits, so a SIGKILL leaves nothing stale.

A busy lock is reported as an ``error`` event with ``code: install_in_progress`` and
exit status 75 (EX_TEMPFAIL). The host maps the code to its "install in progress"
error; the shell wrappers use the status to pass the event through without adding
their own. Nothing is cleaned up on the way out: the files belong to the other run.

This module imports nothing from the package, so the shell scripts can run it::

    python /app/common/model_lock.py busy <key>        # exit 0 if held, 1 if free
    python /app/common/model_lock.py run <key> -- cmd  # run cmd holding the lock
"""

import argparse
import fcntl
import functools
import json
import os
import subprocess
import sys

MODELS_ROOT_DEFAULT = "/models_root"
LOCKS_DIRNAME = ".locks"
INSTALL_IN_PROGRESS = "install_in_progress"
EXIT_BUSY = 75

# The "status" every handler's check action reports. in_progress means the lock is
# held; a ".download" marker with the lock free is a killed run, so not_installed.
CHECK_INSTALLED = "installed"
CHECK_NOT_INSTALLED = "not_installed"
CHECK_IN_PROGRESS = "in_progress"


# Descriptors of the locks this process holds, closed by release_all.
_held = []


class ModelBusy(Exception):
    """Another process holds the lock of this model."""


def models_root():
    """The mount point of the whole models tree."""
    return os.environ.get("MODELS_ROOT") or MODELS_ROOT_DEFAULT


def lock_name(models_repository, key):
    """The lock file name for *key* inside *models_repository*, one path component."""
    parts = [p for p in (models_repository or "", key or "") if p]
    return "__".join(p.strip("/").replace("/", "__") for p in parts) + ".lock"


def lock_path(key, models_repository=None, root=None):
    """The lock file of *key*; *models_repository* defaults to the environment's."""
    if models_repository is None:
        models_repository = os.environ.get("models_repository", "")
    return os.path.join(root or models_root(), LOCKS_DIRNAME, lock_name(models_repository, key))


def acquire(key, models_repository=None, root=None):
    """Take the lock of *key* without waiting and return its file descriptor.

    The lock lasts until the descriptor is closed or the process exits, and an
    ``exec`` keeps it. Raises ModelBusy when another process holds it, and OSError
    when the models root is not mounted.
    """
    root = root or models_root()
    if not os.path.isdir(root):
        raise OSError(f"models root {root} is not mounted")
    path = lock_path(key, models_repository, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise ModelBusy(key) from None
    except BaseException:
        os.close(fd)
        raise
    _held.append(fd)
    return fd


def release(fd):
    """Release the lock *fd* was returned for."""
    if fd in _held:
        _held.remove(fd)
    os.close(fd)


def release_all():
    """Release every lock this process holds."""
    while _held:
        os.close(_held.pop())


def releases_locks(func):
    """Release the locks *func* took when it returns or raises.

    A downloader's locks are meant to last until its process exits; this makes a
    ``main()`` called in-process (by the tests) leave none behind.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            release_all()

    return wrapper


def is_busy(key, models_repository=None, root=None):
    """Whether another process holds the lock of *key*; a missing root reads as free."""
    try:
        fd = acquire(key, models_repository, root)
    except ModelBusy:
        return True
    except OSError:
        return False
    release(fd)
    return False


def busy_event(key):
    """The event a run that finds *key* locked reports."""
    return {"event": "error", "code": INSTALL_IN_PROGRESS, "description": f"Another operation is in progress on model: {key}"}


def acquire_or_exit(key, models_repository=None, root=None):
    """``acquire``, or report why not and exit: busy with EXIT_BUSY, unmounted with 1."""
    try:
        return acquire(key, models_repository, root)
    except ModelBusy:
        print(json.dumps(busy_event(key)), flush=True)
        sys.exit(EXIT_BUSY)
    except OSError as exc:
        print(json.dumps({"event": "error", "description": f"Cannot lock model {key}: {exc}"}), flush=True)
        sys.exit(1)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Test or hold the lock of a model.")
    sub = parser.add_subparsers(dest="action", required=True)
    busy = sub.add_parser("busy", help="Exit 0 when the lock is held by another process, 1 when free.")
    busy.add_argument("key")
    run = sub.add_parser("run", help="Run a command holding the lock; exit 75 without running it when busy.")
    run.add_argument("key")
    run.add_argument("cmd", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)

    if args.action == "busy":
        return 0 if is_busy(args.key) else 1

    cmd = args.cmd[1:] if args.cmd[:1] == ["--"] else args.cmd
    if not cmd:
        parser.error("run: missing command")
    fd = acquire_or_exit(args.key)
    try:
        return subprocess.call(cmd)
    finally:
        release(fd)


if __name__ == "__main__":
    sys.exit(main())
