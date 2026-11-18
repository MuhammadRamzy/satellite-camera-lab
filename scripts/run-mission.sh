#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export MISSION_THROUGH=${1:-6}
export MISSION_REGRESSION=${2:-1}
bash scripts/test-host.sh
PAYLOAD_CHECK_PHASE=mission bash scripts/run-phase2.sh
