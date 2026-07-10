#!/usr/bin/env bash
set -euo pipefail
# NON-CANONICAL / LEGACY WRAPPER — operator truth is ./stop.sh (v6).
# This entrypoint exists only for backward compatibility and forwards all args.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/stop.sh" "$@"
