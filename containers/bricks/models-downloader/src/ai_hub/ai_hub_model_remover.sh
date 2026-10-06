#!/bin/bash

# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

cd /models

# Removed holding the model's lock, so a download in progress is never deleted under
# it; exit 75 means it was busy, and the install_in_progress event is already out.
python /app/common/model_lock.py run "${model_directory:-${model_name}}" -- rm -fr "$model_directory"
exit_code=$?
if [ "${exit_code}" -eq 75 ]; then
    exit 75
elif [ "${exit_code}" -ne 0 ]; then
    echo "{\"event\": \"error\", \"description\": \"Failed to remove model: ${model_directory}\"}"
    exit 1
fi

python /app/list_models.py --refresh-index
echo "{\"event\": \"info\", \"description\": \"Model removed: ${model_directory}\"}"
