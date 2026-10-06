# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""Tests for the per-model lock (``common/model_lock.py``) and the stale-directory
handling that runs under it (``common/model_dir.py``)."""

import fcntl
import json
import os
import subprocess
import sys

import pytest

from common import model_lock
from common.model_dir import EXISTS, FRESH, prepare_model_dir

MODEL_LOCK_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "src", "common", "model_lock.py")


def hold(root, key, models_repository=""):
    """Take *key*'s lock the way another container would: its own descriptor."""
    path = model_lock.lock_path(key, models_repository, str(root))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


@pytest.mark.parametrize(
    "repository, key, expected",
    [
        ("llamacpp", "unsloth/Qwen3-0.6B-GGUF", "llamacpp__unsloth__Qwen3-0.6B-GGUF.lock"),
        ("audio-analytics/tts", "melotts", "audio-analytics__tts__melotts.lock"),
        ("", "model", "model.lock"),
    ],
)
def test_lock_name_is_one_path_component(repository, key, expected):
    assert model_lock.lock_name(repository, key) == expected


def test_lock_name_keeps_repositories_apart():
    assert model_lock.lock_name("a", "m") != model_lock.lock_name("b", "m")


def test_lock_path_lives_under_the_models_root(models_root, monkeypatch):
    monkeypatch.setenv("models_repository", "llamacpp")
    assert model_lock.lock_path("x") == os.path.join(str(models_root), ".locks", "llamacpp__x.lock")


def test_acquire_is_exclusive(models_root):
    fd = model_lock.acquire("m")
    with pytest.raises(model_lock.ModelBusy):
        model_lock.acquire("m")
    model_lock.release(fd)
    model_lock.release(model_lock.acquire("m"))


def test_other_models_are_not_blocked(models_root):
    fd = model_lock.acquire("m")
    model_lock.release(model_lock.acquire("other"))
    model_lock.release(fd)


def test_is_busy_sees_another_holder_and_leaves_the_lock_free(models_root):
    assert not model_lock.is_busy("m")
    fd = hold(models_root, "m")
    assert model_lock.is_busy("m")
    os.close(fd)
    assert not model_lock.is_busy("m")
    model_lock.release(model_lock.acquire("m"))


def test_unmounted_root_fails_instead_of_creating_it(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "absent"
    monkeypatch.setenv("MODELS_ROOT", str(missing))
    with pytest.raises(OSError):
        model_lock.acquire("m")
    with pytest.raises(SystemExit) as exc:
        model_lock.acquire_or_exit("m")
    assert exc.value.code == 1
    assert events(capsys)[-1]["event"] == "error"
    assert not missing.exists()
    # A check cannot tell a busy model on an unmounted root: it reads as free.
    assert not model_lock.is_busy("m")


def test_busy_reports_install_in_progress(models_root, capsys):
    fd = hold(models_root, "m")
    with pytest.raises(SystemExit) as exc:
        model_lock.acquire_or_exit("m")
    os.close(fd)
    assert exc.value.code == model_lock.EXIT_BUSY
    event = events(capsys)[-1]
    assert event["event"] == "error"
    assert event["code"] == "install_in_progress"


def test_releases_locks_frees_what_the_function_took(models_root):
    @model_lock.releases_locks
    def take():
        model_lock.acquire("m")
        raise RuntimeError

    with pytest.raises(RuntimeError):
        take()
    assert not model_lock.is_busy("m")


def _cli(*args):
    return subprocess.run([sys.executable, MODEL_LOCK_SCRIPT, *args], capture_output=True, text=True, env=os.environ.copy())


def test_cli_busy(models_root):
    assert _cli("busy", "m").returncode == 1
    fd = hold(models_root, "m")
    assert _cli("busy", "m").returncode == 0
    os.close(fd)


def test_cli_run_holds_the_lock_and_passes_the_status_through(models_root, tmp_path):
    probe = tmp_path / "probe"
    result = _cli("run", "m", "--", sys.executable, MODEL_LOCK_SCRIPT, "busy", "m")
    # The command saw its own lock held.
    assert result.returncode == 0
    result = _cli("run", "m", "--", sys.executable, "-c", f"open({str(probe)!r}, 'w'); raise SystemExit(3)")
    assert result.returncode == 3
    assert probe.exists()


def test_cli_run_does_not_run_the_command_when_busy(models_root, tmp_path):
    probe = tmp_path / "probe"
    fd = hold(models_root, "m")
    result = _cli("run", "m", "--", sys.executable, "-c", f"open({str(probe)!r}, 'w')")
    os.close(fd)
    assert result.returncode == model_lock.EXIT_BUSY
    assert json.loads(result.stdout)["code"] == "install_in_progress"
    assert not probe.exists()


# --------------------------------------------------------------------------- #
# prepare_model_dir
# --------------------------------------------------------------------------- #
def test_prepare_wipes_a_killed_download(tmp_path, capsys):
    model = tmp_path / "m"
    model.mkdir()
    (model / ".download").write_text("{}")
    (model / "partial.bin").write_bytes(b"\0")

    assert prepare_model_dir(str(model), lambda _p: True, "m") == FRESH
    assert model.is_dir() and not any(model.iterdir())
    assert events(capsys)[-1]["description"] == "Removing incomplete previous download: m"


def test_prepare_wipes_a_bookkeeping_only_leftover(tmp_path):
    model = tmp_path / "m"
    model.mkdir()
    (model / ".arduino_metadata.yaml").write_text("models: []\n")

    assert prepare_model_dir(str(model), lambda _p: True, "m") == FRESH
    assert not any(model.iterdir())


def test_prepare_reports_an_installed_model(tmp_path, capsys):
    model = tmp_path / "m"
    model.mkdir()
    (model / "model.bin").write_bytes(b"\0" * (1024 * 1024))

    assert prepare_model_dir(str(model), lambda _p: True, "m") == EXISTS
    assert events(capsys)[-1] == {"event": "info", "description": "Model exists: m", "size_mb": 1.0}
    assert (model / "model.bin").exists()


def test_prepare_keeps_content_that_is_not_the_model(tmp_path):
    model = tmp_path / "m"
    model.mkdir()
    (model / "other.bin").write_bytes(b"\0")

    assert prepare_model_dir(str(model), lambda path: os.path.isfile(os.path.join(path, "m.eim")), "m.eim") == FRESH
    assert (model / "other.bin").exists()


def test_prepare_creates_a_missing_directory(tmp_path):
    model = tmp_path / "a" / "m"
    assert prepare_model_dir(str(model), lambda _p: True, "m") == FRESH
    assert model.is_dir()
