#!/usr/bin/env bash
set -eo pipefail
cd /workspace
# shellcheck source=/dev/null
source /opt/ros/spaceros/setup.bash
mkdir -p docs/evidence/phase3
dpkg-query -W > docs/evidence/phase3/packages.tsv
colcon build --parallel-workers 2 --event-handlers console_direct+ \
  --cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  2>&1 | tee docs/evidence/phase3/build.log
# shellcheck source=/dev/null
source install/setup.bash
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_DOMAIN_ID=121
colcon test --packages-select payload_camera payload_processing --event-handlers console_direct+ \
  2>&1 | tee docs/evidence/phase3/unit-tests.log
colcon test-result --verbose 2>&1 | tee docs/evidence/phase3/unit-test-result.log
python3 scripts/verify-phase3.py 2>&1 | tee docs/evidence/phase3/integration.log
