#!/bin/bash

# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

cd /models

source /app/common/model_lock.sh
hold_model_lock "${model_directory}" "${model_directory}"

cmd=(rm -fr "$model_directory")

"${cmd[@]}"
if [ $? -ne 0 ]; then
    echo "{\"event\": \"error\", \"description\": \"Failed to remove model: ${model_directory}\"}"
    exit 1
fi

echo "{\"event\": \"info\", \"description\": \"Model removed: ${model_directory}\"}"