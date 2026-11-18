# Test plan

Phase gates are sequential. Host library tests may run independently during the
installation block. They exercise filesystem operations on disposable temporary
directories and need no hardware or installed ROS.

1. Environment: record kernel/resources/tools/ROS conflicts, inspect official
   sources, resolve official immutable image and embedded source hashes.
2. Space ROS: build payload_smoke, deliver five distinct messages across two
   processes, inspect discovery and environment. A timeout is a failed gate.
3. Camera: decode generated pixel patterns independently; check noise seed,
   timestamps, source errors, image count, typed image/CameraInfo topics and burst
   cancel. Test 640×480 before larger resolutions.
4. Processing/storage: assert transformations against known pixel coordinates;
   independently decode JPEG, verify SHA-256 and metadata after killed process
   restart. Test unavailable disk and quota without filling the host disk.
5. Control: exhaustive transition table and denied guards; lifecycle activation,
   SAFE during each pipeline stage, node-crash and watchdog behavior.
6. Gateway/bus: known binary vectors and independent parser; fragmented TCP,
   malformed/oversized frames, duplicate journal and reconnect. Confirm no bus
   import in payload and no DDS outside payload namespace.
7. File/downlink: independently compute receiver hash, inject chunk corruption,
   disconnect receiver and resume pass. Assert wall duration is at least
   attempted_wire_bits/configured_rate within measured clock tolerance.
8. Mission: execute all steps in operations.md, then fault campaign and benchmarks.

Every fault result must record initial state, injection, expected and observed
behavior, recovery action/time, and pass/fail. Capture logs directly; NOT RUN is
neither PASS nor FAIL. Re-run only checks impacted by changed code or concerns.
