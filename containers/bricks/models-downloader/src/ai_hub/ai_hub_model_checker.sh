#!/bin/bash

# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

model_path="/models/${model_directory}"
lock_key="${model_directory:-${model_name}}"

# A held lock is a download or delete running right now (common/model_lock.py).
if python /app/common/model_lock.py busy "${lock_key}"; then
    echo "{\"event\": \"info\", \"description\": \"Model downloading: ${model_directory}\", \"downloading\": true, \"status\": \"in_progress\"}"
    exit 0
# A ".download" marker with the lock free is a killed run, which the next download wipes.
elif [ -f "${model_path}/.download" ]; then
    echo "{\"event\": \"info\", \"description\": \"Model downloading: ${model_directory}\", \"downloading\": true, \"status\": \"not_installed\"}"
    exit 0
# A directory holding only the ".arduino_metadata.yaml" record and no model content
# is a leftover, not an installed model.
elif [ -d "${model_path}" ] && [ -n "$(find "${model_path}" -mindepth 1 ! -name '.arduino_metadata.yaml*' -print -quit 2>/dev/null)" ]; then
    python /app/common/model_size.py --description "Model exists: ${model_directory}" --downloading false --status installed "${model_path}" \
        || echo "{\"event\": \"info\", \"description\": \"Model exists: ${model_directory}\", \"downloading\": false, \"size_mb\": null, \"status\": \"installed\"}"
    exit 0
else
    echo "{\"event\": \"error\", \"description\": \"Model does not exist: ${model_directory}\", \"downloading\": false, \"status\": \"not_installed\"}"
    exit 1
fi
