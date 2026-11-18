#!/usr/bin/env bash
set -eo pipefail
cd /workspace
# shellcheck source=/dev/null
source /opt/ros/spaceros/setup.bash
mkdir -p docs/evidence/mission
colcon build --parallel-workers 2 --event-handlers console_direct+ \
  --cmake-args -DCMAKE_BUILD_TYPE=RelWithDebInfo -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
  2>&1 | tee docs/evidence/mission/build.log
# shellcheck source=/dev/null
source install/setup.bash
export ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST
export ROS_DOMAIN_ID=121
colcon test --packages-select payload_camera payload_processing --event-handlers console_direct+ \
  2>&1 | tee docs/evidence/mission/unit-tests.log
colcon test-result --verbose 2>&1 | tee docs/evidence/mission/unit-test-result.log
if [[ "${MISSION_REGRESSION:-1}" == 1 ]]; then
  python3 scripts/verify-phase2.py 2>&1 | tee docs/evidence/mission/phase2-regression.log
  python3 scripts/verify-phase3.py 2>&1 | tee docs/evidence/mission/phase3-regression.log
fi
python3 scripts/verify-mission.py --through "${MISSION_THROUGH:-6}" 2>&1 | tee docs/evidence/mission/integration.log
