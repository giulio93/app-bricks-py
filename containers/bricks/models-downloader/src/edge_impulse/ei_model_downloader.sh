#!/bin/bash

# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

quantization_arg=()
if [ -n "${quantization}" ]; then
    quantization_arg=(--quantization "${quantization}")
fi

# Models pinned to an entry of the project's deployment history are fetched from
# the history endpoint, which addresses the build by id alone (target and
# quantization are already baked into that past build).
history_arg=()
if [ -n "${history_id}" ]; then
    history_arg=(--history-id "${history_id}")
fi

# Each model lives in its own folder named after model_name without its
# extension (e.g. efficientnet-b4-qnn.eim -> efficientnet-b4-qnn). The .eim file
# and the in-progress ".download" marker both live inside this folder, mirroring
# the AI Hub / HF layout.
model_folder="${model_name%.*}"
model_path="/models/${model_folder}"

# The python downloader takes the model's lock, then wipes what a killed run left
# or reports the model as already there (common/model_dir.py), then downloads.
# Use exec so python replaces this shell as PID 1 and receives SIGINT/SIGTERM
# directly, allowing it to clean up partial downloads before exiting.
exec python /app/edge_impulse/download_ei_build.py \
    --ei-project-id "${ei_project_id}" \
    --impulse-id "${ei_impulse_id}" \
    --output-name "${model_name}" \
    --output-dir "${model_path}" \
    "${quantization_arg[@]}" \
    "${history_arg[@]}" \
    --target "${target}"
