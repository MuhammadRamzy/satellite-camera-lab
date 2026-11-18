#!/usr/bin/env bash
set -eo pipefail
cd /workspace
test "${ROS_DISTRO:-}" = jazzy
test "${SPACEROS_DIR:-}" = /opt/ros/spaceros
test -f "$SPACEROS_DIR/setup.bash"
# shellcheck source=/dev/null
source "$SPACEROS_DIR/setup.bash"
mkdir -p docs/evidence
{
  date -u
  cat /etc/os-release
  printenv | sort | grep -E '^(ROS|SPACEROS|AMENT|CMAKE_PREFIX)'
  ros2 pkg prefix rclcpp
  ros2 pkg prefix ament_cmake
  dpkg-query -W
  command -v ikos || true
  command -v clang-tidy || true
  command -v cppcheck || true
} > docs/evidence/container-environment.log
colcon build --packages-select payload_smoke --parallel-workers 2 \
  --event-handlers console_direct+ 2>&1 | tee docs/evidence/colcon-build.log
# shellcheck source=/dev/null
source install/setup.bash
export ROS_LOCALHOST_ONLY=1
export ROS_DOMAIN_ID=121
ros2 run payload_smoke smoke > docs/evidence/smoke-sender.log 2>&1 &
sender_pid=$!
trap 'kill "$sender_pid" 2>/dev/null || true; wait "$sender_pid" 2>/dev/null || true' EXIT
timeout 25 ros2 run payload_smoke smoke receive 2>&1 | tee docs/evidence/smoke-receiver.log
grep -q SMOKE_PASS docs/evidence/smoke-receiver.log
ros2 node list --no-daemon > docs/evidence/nodes.log
ros2 topic info /payload/smoke --verbose > docs/evidence/topics.log
colcon test --packages-select payload_smoke --event-handlers console_direct+ \
  2>&1 | tee docs/evidence/colcon-test.log
colcon test-result --verbose 2>&1 | tee docs/evidence/colcon-test-result.log
echo 'Phase 1 DDS probe passed; this smoke package has no unit tests.'
