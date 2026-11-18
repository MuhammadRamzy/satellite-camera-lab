# Space ROS verification — PASS for Phases 1–2

Installed official July 2026 Jazzy development image, pinned in
config/image-lock.json. Local repository digest matches the official registry;
source VCS label is the full July tag commit. October is the newer verified
registry release but remains an open upgrade due to repeated download resets.

Evidence chain:

- image-inspect.json and official-july-registry-tag.json: image identity, vendor,
  source URL/commit, amd64 architecture and official repository digest.
- spaceros-exact.repos: upstream embedded exact revisions of all components.
- container-environment.log and payload-packages.tsv: Noble/Jazzy environment,
  installed versions, /opt/ros/spaceros prefixes and actual compiler/tooling.
- colcon-build.log and smoke-receiver.log: C++ smoke build and five DDS messages
  across distinct processes; nodes.log and topics.log record discovery/QoS.
- phase2-build.log: camera, processing, storage and generated typed interfaces.
- phase2-nodes/topics/services/capture-interface logs: live graph and service.
- phase2-unit-tests.log and phase2-result.json: C++ tests and actual six-image
  capture/processing/persistence with independent checksums/decoding/restart.
- phase2-ament-cppcheck.log/.xml/.sarif: bundled ament_cppcheck passes processing
  core. phase2-static-analysis.log: Cppcheck using the compile database passes.

Observed: rclcpp 28.1.21, ament_cmake 2.5.6, RMW Cyclone DDS, Python 3.12.3,
GCC 13.3.0, Cppcheck 2.13.0 and IKOS 3.5. IKOS version was inspected; IKOS
analysis has not run. Cppcheck covers the processing core, not the whole stack
or a flight-certification claim. Initial `ros2 run ament_cppcheck` failed because
this linter is a shell executable; verified `ament_cppcheck --help` and the
successful actual invocation are saved. No success was inferred from the
failed invocation.

The smoke package itself has zero unit tests; later processing tests supply
five real C++ cases. Actions and lifecycle are not implemented/inspected yet.
The host's ROS Lyrical is not used; ROS_DISTRO alone is not provenance proof.
