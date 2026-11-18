#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p docs/evidence
python3 -m unittest discover -s tests -v 2>&1 | tee docs/evidence/host-tests.log
python3 -m compileall -q foundations interfaces scripts tests src
shellcheck scripts/*.sh
for script in scripts/*.sh; do bash -n "$script"; done
SPACE_ROS_IMAGE=osrf/space-ros:jazzy-2026.10.0-dev docker compose config --quiet
