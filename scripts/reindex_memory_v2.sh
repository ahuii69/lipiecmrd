#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python -m aihub.scripts.reindex_memory_v2 "$@"
