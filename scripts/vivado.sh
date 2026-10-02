#!/bin/bash
# SPDX-License-Identifier: CERN-OHL-S-2.0
# Copyright 2026 Romain Dolbeau
# Source location: https://github.com/MelkhiorVintageComputing/RD68884

# Kept because the Makefile names it; xilinx.sh does the work.
set -euo pipefail
exec "$(dirname "${BASH_SOURCE[0]}")/xilinx.sh" vivado "$@"
