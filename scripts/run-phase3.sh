#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
bash scripts/test-host.sh
PAYLOAD_CHECK_PHASE=3 bash scripts/run-phase2.sh
