# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

"""Decide what a download does with the model directory it is about to fill.

Shared by the downloaders whose directory holds exactly one model (AI Hub, Edge
Impulse); Hugging Face shares a directory between quantizations and decides per file.
It must run while the model's lock is held (``common/model_lock.py``): a ``.download``
marker only means "killed mid-download" because no other run can be writing it.
"""

import json
import os
import shutil

from common.model_size import MARKER_NAME, exists_event, is_bookkeeping_name

EXISTS = "exists"
FRESH = "fresh"


def has_model_content(path):
    """True when *path* is a directory holding more than bookkeeping files."""
    try:
        with os.scandir(path) as it:
            return any(not is_bookkeeping_name(entry.name) for entry in it)
    except OSError:
        return False


def prepare_model_dir(model_path, installed, label):
    """Make *model_path* ready for a download, or say the model is already there.

    A ``.download`` marker, or a directory holding only bookkeeping files, is what a
    killed run leaves: it is wiped. Otherwise ``installed(model_path)`` decides whether
    the model is there, and if so its "Model exists" event is printed. *label* names the
    model in the events.

    Returns EXISTS, or FRESH once the (empty) directory exists.
    """
    leftover = os.path.isfile(os.path.join(model_path, MARKER_NAME)) or (os.path.isdir(model_path) and not has_model_content(model_path))
    if leftover:
        print(json.dumps({"event": "info", "description": f"Removing incomplete previous download: {label}"}), flush=True)
        shutil.rmtree(model_path, ignore_errors=True)
    elif os.path.isdir(model_path) and installed(model_path):
        print(json.dumps(exists_event(f"Model exists: {label}", model_path)), flush=True)
        return EXISTS
    os.makedirs(model_path, exist_ok=True)
    return FRESH
