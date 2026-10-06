# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""Tests for the models index (``<models root>/.models-index.yaml``) and for the
downloaders' side of it: the lock they take and the index they rewrite."""

import fcntl
import json
import os
import sys
import threading

import pytest
import yaml

import list_models
from common import model_lock
from common.model_metadata import write_metadata

CATALOG = """\
models:
 - "ai-hub:yolo":
    name: "YOLO"
    description: "Object detection"
    supported_boards: ["ventunoq"]
    bricks:
      - id: "arduino:object_detection"
    model_labels: ["vision"]
    deployment:
      handler: "ai-hub-handler"
      platforms:
        - ventunoq:
            variables:
              models_repository: "ai-hub"
              model_directory: "yolo-dir"
              model_name: "yolo"
    metadata:
      model_size_mb: 2
 - "ei:keyword":
    name: "Keyword"
    deployment:
      handler: "ei-handler"
      platforms:
        - ventunoq:
            variables:
              models_repository: "edge-impulse"
              model_name: "keyword.eim"
    metadata:
      model_size_mb: 1.5
 - "builtin:tiny":
    name: "Tiny"
    deployment:
      handler: "ai-hub-handler"
      pre-loaded: true
 - "other-board:model":
    name: "Elsewhere"
    supported_boards: ["unoq"]
    deployment:
      handler: "ei-handler"
      platforms:
        - unoq:
            variables:
              models_repository: "edge-impulse"
              model_name: "elsewhere.eim"
"""


@pytest.fixture
def catalog(tmp_path):
    path = tmp_path / "models-list.yaml"
    path.write_text(CATALOG)
    return str(path)


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"\0" * size)


def _index(root):
    with open(os.path.join(str(root), list_models.INDEX_NAME)) as f:
        document = yaml.safe_load(f)
    return document, {model_id: model for item in document["models"] for model_id, model in item.items()}


def _user_gguf(root, repo, filename, model_id, mmproj=False, record=True):
    repo_dir = os.path.join(str(root), "llamacpp", repo)
    _write(os.path.join(repo_dir, filename), 1000)
    if mmproj:
        _write(os.path.join(repo_dir, "mmproj-BF16.gguf"), 24)
    if record:
        write_metadata(
            repo_dir,
            "hf-handler",
            env={"models_repository": "llamacpp", "model_url": f"{repo}:Q4_0"},
            models_list_path="",
            identity={"model_id": model_id, "model_origin": "user"},
            files=[filename],
        )


def test_index_keeps_the_catalog_entry_and_adds_the_state(models_root, catalog):
    _write(os.path.join(str(models_root), "ai-hub", "yolo-dir", "yolo.bin"), 3000)

    list_models.write_index(str(models_root), str(models_root), catalog, "ventunoq")

    document, models = _index(models_root)
    assert document["board"] == "ventunoq"
    yolo = models["ai-hub:yolo"]
    assert yolo["name"] == "YOLO"
    assert yolo["description"] == "Object detection"
    assert yolo["bricks"] == [{"id": "arduino:object_detection"}]
    assert yolo["model_labels"] == ["vision"]
    assert yolo["deployment"]["platforms"][0]["ventunoq"]["variables"]["model_directory"] == "yolo-dir"
    assert yolo["handler"] == "ai-hub-handler"
    assert yolo["status"] == "installed"
    # Measured in bytes, not the 2 MB declared.
    assert yolo["size_bytes"] == 3000
    assert yolo["folder"] == "ai-hub/yolo-dir"
    assert yolo["origin"] == "curated"
    assert yolo["metadata"]["model_size_mb"] == 2
    assert yolo["metadata"]["runtime"]


def test_index_sizes_a_missing_model_by_its_declaration(models_root, catalog):
    list_models.write_index(str(models_root), str(models_root), catalog, "ventunoq")

    _, models = _index(models_root)
    keyword = models["ei:keyword"]
    assert keyword["status"] == "not-installed"
    assert keyword["size_bytes"] == int(1.5 * 1024 * 1024)
    assert "folder" not in keyword
    assert models["builtin:tiny"]["status"] == "installed"


def test_index_reports_a_download_in_progress(models_root, catalog):
    folder = os.path.join(str(models_root), "edge-impulse", "keyword")
    _write(os.path.join(folder, "keyword.eim"), 10)
    with open(os.path.join(folder, ".download"), "w") as f:
        f.write('{"status": "downloading"}')

    list_models.write_index(str(models_root), str(models_root), catalog, "ventunoq")

    _, models = _index(models_root)
    assert models["ei:keyword"]["status"] == "downloading"


def test_index_lists_only_the_board(models_root, catalog):
    list_models.write_index(str(models_root), str(models_root), catalog, "ventunoq")
    _, models = _index(models_root)
    assert "other-board:model" not in models


def test_index_describes_a_user_model_by_its_record(models_root, catalog):
    model_id = "llamacpp:org/repo-GGUF/model-Q4_0"
    _user_gguf(models_root, "org/repo-GGUF", "model-Q4_0.gguf", model_id)

    list_models.write_index(str(models_root), str(models_root), catalog, "ventunoq")

    _, models = _index(models_root)
    user = models[model_id]
    assert user["origin"] == "user"
    assert user["status"] == "installed"
    assert user["handler"] == "hf-handler"
    assert user["bricks"] == [{"id": "arduino:llm"}]
    assert user["deployment"] == {
        "handler": "hf-handler",
        "platforms": [{"ventunoq": {"variables": {"models_repository": "llamacpp", "model_url": "org/repo-GGUF:Q4_0"}}}],
    }
    assert user["metadata"]["source-model-url"] == "org/repo-GGUF:Q4_0"
    assert user["size_bytes"] == 1000
    assert user["folder"] == "llamacpp/org/repo-GGUF"


def test_index_runs_a_user_model_with_a_projector_on_the_vlm_brick(models_root, catalog):
    model_id = "llamacpp:org/vision-GGUF/model-Q4_0"
    _user_gguf(models_root, "org/vision-GGUF", "model-Q4_0.gguf", model_id, mmproj=True)

    list_models.write_index(str(models_root), str(models_root), catalog, "ventunoq")

    _, models = _index(models_root)
    assert models[model_id]["bricks"] == [{"id": "arduino:vlm"}]
    assert models[model_id]["size_bytes"] == 1024


def test_index_leaves_out_a_user_model_it_cannot_drive(models_root, catalog, capsys):
    _user_gguf(models_root, "org/legacy-GGUF", "legacy-Q4_0.gguf", "unused", record=False)
    _user_gguf(models_root, "org/sibling-GGUF", "a-Q4_0.gguf", "llamacpp:someone-else")

    list_models.write_index(str(models_root), str(models_root), catalog, "ventunoq")

    _, models = _index(models_root)
    assert not [m for m in models if "legacy" in m or "sibling" in m]
    assert "skipping" in capsys.readouterr().err


def test_index_write_is_atomic_and_ignores_its_own_files(models_root, catalog):
    os.makedirs(os.path.join(str(models_root), ".locks"))
    list_models.write_index(str(models_root), str(models_root), catalog, "ventunoq")
    list_models.write_index(str(models_root), str(models_root), catalog, "ventunoq")

    names = sorted(os.listdir(str(models_root)))
    assert names == [".listing.lock", ".locks", ".models-index.yaml"]
    _, models = _index(models_root)
    assert set(models) == {"ai-hub:yolo", "ei:keyword", "builtin:tiny"}


def test_index_waits_for_another_listing(models_root, catalog):
    """A listing holding the lock is waited for, not raced."""
    fd = os.open(os.path.join(str(models_root), list_models.LISTING_LOCK_NAME), os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    writer = threading.Thread(target=list_models.write_index, args=(str(models_root), str(models_root), catalog, "ventunoq"))
    writer.start()
    writer.join(timeout=0.5)
    assert writer.is_alive()
    assert not os.path.exists(os.path.join(str(models_root), list_models.INDEX_NAME))
    os.close(fd)
    writer.join(timeout=10)
    assert not writer.is_alive()
    _index(models_root)


def test_refresh_index_never_raises(models_root, tmp_path, capsys):
    assert list_models.refresh_index(yaml_path=str(tmp_path / "missing.yaml")) is False
    assert "Could not write" in capsys.readouterr().err
    assert not os.path.exists(os.path.join(str(models_root), list_models.INDEX_NAME))


def test_refresh_index_lists_the_mounted_root_for_the_board(models_root, catalog, monkeypatch):
    monkeypatch.setenv("BOARD_NAME", "unoq")
    assert list_models.refresh_index(yaml_path=catalog) is True
    document, models = _index(models_root)
    assert document["board"] == "unoq"
    assert "other-board:model" in models


def test_cli_write_index_prints_nothing_without_json(models_root, catalog, monkeypatch, capsys):
    argv = ["list_models.py", "--models-dir", str(models_root), "--model-list", catalog, "--write-index", str(models_root)]
    monkeypatch.setattr(sys, "argv", argv)
    list_models.main()
    assert capsys.readouterr().out == ""
    _index(models_root)

    monkeypatch.setattr(sys, "argv", [*argv, "--json"])
    list_models.main()
    listed = json.loads(capsys.readouterr().out)["models"]
    assert all(not key.startswith("_") for entry in listed for key in entry)


# --------------------------------------------------------------------------- #
# Downloaders: busy lock, and the index they rewrite
# --------------------------------------------------------------------------- #
def hold(root, key, models_repository=""):
    path = model_lock.lock_path(key, models_repository, str(root))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def _events(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def _in_progress_dir(path):
    """What a running download looks like on disk: its marker and a partial file."""
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, ".download"), "w") as f:
        f.write('{"status": "downloading"}')
    _write(os.path.join(path, "partial.bin"), 10)


EI_ARGV = ["download_ei_build.py", "--ei-project-id", "1", "--impulse-id", "2", "--target", "runner-linux-aarch64", "--output-name", "m.eim"]


def test_ei_download_leaves_a_busy_model_alone(models_root, tmp_path, monkeypatch, capsys):
    from edge_impulse import download_ei_build

    folder = tmp_path / "m"
    _in_progress_dir(str(folder))
    fd = hold(models_root, "m")
    monkeypatch.setattr(sys, "argv", [*EI_ARGV, "--output-dir", str(folder)])
    with pytest.raises(SystemExit) as exc:
        download_ei_build.main()
    os.close(fd)

    assert exc.value.code == model_lock.EXIT_BUSY
    assert _events(capsys)[-1]["code"] == "install_in_progress"
    assert (folder / ".download").exists() and (folder / "partial.bin").exists()


def test_ei_download_wipes_a_killed_run_and_rewrites_the_index(models_root, tmp_path, monkeypatch, capsys):
    from edge_impulse import download_ei_build

    folder = tmp_path / "m"
    _in_progress_dir(str(folder))
    refreshed = []
    monkeypatch.setattr(download_ei_build, "refresh_index", lambda: refreshed.append(True))

    def _download(_url, output_dir, _json_progress, output_name=None):
        assert not os.path.exists(os.path.join(output_dir, "partial.bin"))
        path = os.path.join(output_dir, output_name)
        _write(path, 10)
        return path

    monkeypatch.setattr(download_ei_build, "download", _download)
    monkeypatch.setattr(sys, "argv", [*EI_ARGV, "--output-dir", str(folder)])
    download_ei_build.main()

    assert refreshed == [True]
    assert not model_lock.is_busy("m")
    assert _events(capsys)[0]["description"] == "Removing incomplete previous download: m.eim"


def test_ei_download_reports_an_installed_model(models_root, tmp_path, monkeypatch, capsys):
    from edge_impulse import download_ei_build

    folder = tmp_path / "m"
    _write(str(folder / "m.eim"), 10)
    monkeypatch.setattr(download_ei_build, "download", lambda *a, **k: pytest.fail("downloaded again"))
    monkeypatch.setattr(sys, "argv", [*EI_ARGV, "--output-dir", str(folder)])
    download_ei_build.main()
    assert _events(capsys)[-1]["description"] == "Model exists: m.eim"


AI_HUB_ARGV = ["download_ai_hub_model.py", "--model_type", "t", "--model_name", "n", "--quantization", "q", "--chipset", "c"]


def test_ai_hub_download_leaves_a_busy_model_alone(models_root, tmp_path, monkeypatch, capsys):
    from ai_hub import download_ai_hub_model

    _in_progress_dir(str(tmp_path / "dir"))
    monkeypatch.setenv("model_directory", "dir")
    monkeypatch.setenv("models_repository", "ai-hub")
    fd = hold(models_root, "dir", "ai-hub")
    monkeypatch.setattr(download_ai_hub_model.subprocess, "run", lambda *a, **k: pytest.fail("fetched"))
    monkeypatch.setattr(sys, "argv", [*AI_HUB_ARGV, "--output-dir", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        download_ai_hub_model.main()
    os.close(fd)

    assert exc.value.code == model_lock.EXIT_BUSY
    assert (tmp_path / "dir" / "partial.bin").exists()


def _qwen(tmp_path):
    repo = tmp_path / "llamacpp" / "unsloth" / "Qwen3-0.6B-GGUF"
    repo.mkdir(parents=True)
    return tmp_path / "llamacpp", repo


def _hf_main(monkeypatch, *argv):
    from hugging_face import hf_downloader

    monkeypatch.setattr(sys, "argv", ["hf_downloader.py", *argv])
    hf_downloader.main()


def test_hf_check_reports_a_busy_repository_in_progress(models_root, tmp_path, monkeypatch, capsys):
    models_dir, repo = _qwen(tmp_path)
    (repo / "Qwen3-0.6B-Q4_0.gguf").write_bytes(b"\0")
    fd = hold(models_root, "unsloth/Qwen3-0.6B-GGUF")
    _hf_main(monkeypatch, "--check", "--model-url", "unsloth/Qwen3-0.6B-GGUF:Q4_0", "--output-dir", str(models_dir))
    os.close(fd)
    assert _events(capsys)[-1]["status"] == "in_progress"


def test_hf_download_leaves_a_busy_repository_alone(models_root, tmp_path, monkeypatch, capsys):
    models_dir, repo = _qwen(tmp_path)
    _in_progress_dir(str(repo))
    fd = hold(models_root, "unsloth/Qwen3-0.6B-GGUF")
    with pytest.raises(SystemExit) as exc:
        _hf_main(monkeypatch, "--model-url", "unsloth/Qwen3-0.6B-GGUF:Q4_0", "--output-dir", str(models_dir))
    os.close(fd)
    assert exc.value.code == model_lock.EXIT_BUSY
    assert _events(capsys)[-1]["code"] == "install_in_progress"
    assert (repo / ".download").exists() and (repo / "partial.bin").exists()


def test_hf_delete_leaves_a_busy_repository_alone(models_root, tmp_path, monkeypatch, capsys):
    models_dir, repo = _qwen(tmp_path)
    (repo / "Qwen3-0.6B-Q4_0.gguf").write_bytes(b"\0")
    fd = hold(models_root, "unsloth/Qwen3-0.6B-GGUF")
    with pytest.raises(SystemExit) as exc:
        _hf_main(monkeypatch, "--delete", "--model-url", "unsloth/Qwen3-0.6B-GGUF:Q4_0", "--output-dir", str(models_dir))
    os.close(fd)
    assert exc.value.code == model_lock.EXIT_BUSY
    assert (repo / "Qwen3-0.6B-Q4_0.gguf").exists()


def test_hf_delete_rewrites_the_index(models_root, tmp_path, monkeypatch):
    from hugging_face import hf_downloader

    models_dir, repo = _qwen(tmp_path)
    (repo / "Qwen3-0.6B-Q4_0.gguf").write_bytes(b"\0")
    refreshed = []
    monkeypatch.setattr(hf_downloader, "refresh_index", lambda: refreshed.append(True))
    _hf_main(monkeypatch, "--delete", "--model-url", "unsloth/Qwen3-0.6B-GGUF:Q4_0", "--output-dir", str(models_dir))
    assert refreshed == [True]
    assert not (repo / "Qwen3-0.6B-Q4_0.gguf").exists()
    assert not model_lock.is_busy("unsloth/Qwen3-0.6B-GGUF")
