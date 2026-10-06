#!/bin/bash

# SPDX-FileCopyrightText: Copyright (C) Arduino s.r.l. and/or its affiliated companies
#
# SPDX-License-Identifier: MPL-2.0

# Writes the index the host reads (/models/.models-index.yaml), and prints the same
# listing as JSON for hosts that still parse it.
python /app/list_models.py --json --supported-board "${BOARD_NAME}" --write-index /models
