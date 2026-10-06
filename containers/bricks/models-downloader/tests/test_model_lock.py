# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""Tests for common/model_lock.py and its shell side, common/model_lock.sh."""

import fcntl
import json
import os
import shutil
import subprocess

import pytest

from common import model_lock

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
LOCK_SH = os.path.join(SRC_DIR, "common", "model_lock.sh")

needs_flock = pytest.mark.skipif(shutil.which("flock") is None, reason="flock(1) is not installed")


@pytest.fixture(autouse=True)
def _release():
    yield
    model_lock.release_all()


def hold(models_dir, key):
    """Hold *key*'s lock as another run would, on a descriptor of its own."""
    path = model_lock.lock_path(str(models_dir), key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def shell(models_dir, script):
    """Run *script* in bash with model_lock.sh sourced against *models_dir*."""
    return subprocess.run(
        ["bash", "-c", f'source "{LOCK_SH}"; {script}'],
        env={**os.environ, "MODELS_DIR": str(models_dir)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_lock_path_is_one_file_outside_the_model_directory(tmp_path):
    assert model_lock.lock_path(str(tmp_path), "unsloth/Qwen3-0.6B-GGUF/") == str(tmp_path / ".locks" / "unsloth__Qwen3-0.6B-GGUF.lock")


def test_acquire_refuses_a_lock_another_run_holds(tmp_path, monkeypatch):
    monkeypatch.setattr(model_lock, "WAIT_SECONDS", 0)
    fd = hold(tmp_path, "m")
    try:
        assert model_lock.acquire(str(tmp_path), "m") is False
    finally:
        os.close(fd)
    assert model_lock.acquire(str(tmp_path), "m") is True


def test_is_locked_follows_the_holder_and_survives_its_death(tmp_path):
    assert model_lock.is_locked(str(tmp_path), "m") is False
    # Testing creates nothing: a check must not write to the models directory.
    assert not (tmp_path / ".locks").exists()

    fd = hold(tmp_path, "m")
    assert model_lock.is_locked(str(tmp_path), "m") is True
    # Closing the descriptor is what the kernel does for a killed process.
    os.close(fd)
    assert model_lock.is_locked(str(tmp_path), "m") is False


def test_release_all_frees_what_acquire_took(tmp_path):
    assert model_lock.acquire(str(tmp_path), "m") is True
    model_lock.release_all()
    fd = hold(tmp_path, "m")
    os.close(fd)


@needs_flock
def test_shell_sees_a_lock_python_holds(tmp_path):
    fd = hold(tmp_path, "org/repo")
    try:
        assert shell(tmp_path, 'model_lock_held "org/repo"').returncode == 0
    finally:
        os.close(fd)
    assert shell(tmp_path, 'model_lock_held "org/repo"').returncode == 1


@needs_flock
def test_shell_held_creates_nothing(tmp_path):
    assert shell(tmp_path, 'model_lock_held "m"').returncode == 1
    assert not (tmp_path / ".locks").exists()


@needs_flock
def test_shell_hold_refuses_a_lock_python_holds(tmp_path):
    fd = hold(tmp_path, "m")
    try:
        # flock -w 2: a held lock costs the second run two seconds before it gives up.
        result = shell(tmp_path, 'hold_model_lock "m" "label"; echo reached')
    finally:
        os.close(fd)

    assert result.returncode == 1
    assert json.loads(result.stdout) == {
        "event": "error",
        "code": "download_in_progress",
        "description": "Another operation is in progress on model: label",
    }


@needs_flock
def test_shell_hold_lasts_through_exec(tmp_path, monkeypatch):
    """The downloaders exec python after taking the lock: the lock has to go with it."""
    monkeypatch.setattr(model_lock, "WAIT_SECONDS", 0)
    proc = subprocess.Popen(
        ["bash", "-c", f'source "{LOCK_SH}"; hold_model_lock "m" "m"; echo held; exec sleep 30'],
        env={**os.environ, "MODELS_DIR": str(tmp_path)},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        assert model_lock.is_locked(str(tmp_path), "m") is True
        assert model_lock.acquire(str(tmp_path), "m") is False
    finally:
        proc.kill()
        proc.wait()
    # Killed, and the kernel has dropped the lock with it.
    assert model_lock.is_locked(str(tmp_path), "m") is False
