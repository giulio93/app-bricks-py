#!/bin/bash

# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

model_folder="${model_name%.*}"
model_path="/models/${model_folder}"

# A held lock is a download or delete running right now (common/model_lock.py).
if python /app/common/model_lock.py busy "${model_folder}"; then
    echo "{\"event\": \"info\", \"description\": \"Model downloading: ${model_name}\", \"downloading\": true, \"status\": \"in_progress\"}"
    exit 0
# A ".download" marker with the lock free is a killed run, which the next download wipes.
elif [ -f "${model_path}/.download" ]; then
    echo "{\"event\": \"info\", \"description\": \"Model downloading: ${model_name}\", \"downloading\": true, \"status\": \"not_installed\"}"
    exit 0
elif [ -f "${model_path}/${model_name}" ]; then
    python /app/common/model_size.py --description "Model exists: ${model_name}" --downloading false --status installed "${model_path}" \
        || echo "{\"event\": \"info\", \"description\": \"Model exists: ${model_name}\", \"downloading\": false, \"size_mb\": null, \"status\": \"installed\"}"
    exit 0
else
    echo "{\"event\": \"error\", \"description\": \"Model does not exist: ${model_name}\", \"downloading\": false, \"status\": \"not_installed\"}"
    exit 1
fi
