#!/bin/bash

# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

# The shell side of common/model_lock.py: the same lock files, taken with flock(1).
# Source it, then call hold_model_lock before touching a model directory, or
# model_lock_held to test it.

MODELS_DIR="${MODELS_DIR:-/models}"

# model_lock_file <key>: the lock file of the model directory <key>.
model_lock_file() {
    local key="${1#/}"
    key="${key%/}"
    echo "${MODELS_DIR}/.locks/${key//\//__}.lock"
}

# hold_model_lock <key> <label>: hold the lock on fd 9 until the process exits; an
# exec'd command inherits it. When another run holds it, print the busy event and exit.
hold_model_lock() {
    local file
    file="$(model_lock_file "$1")"
    if ! mkdir -p "$(dirname "${file}")" || ! exec 9>>"${file}"; then
        echo "{\"event\": \"error\", \"description\": \"Cannot lock model: $2\"}"
        exit 1
    fi
    # Waits out a check, which holds the lock for an instant, but not another download.
    if ! flock -w 2 9; then
        echo "{\"event\": \"error\", \"code\": \"download_in_progress\", \"description\": \"Another operation is in progress on model: $2\"}"
        exit 1
    fi
}

# model_lock_held <key>: succeeds while another process holds the lock. Creates nothing.
model_lock_held() {
    local file
    file="$(model_lock_file "$1")"
    [ -e "${file}" ] && ! flock -n -s "${file}" true
}
