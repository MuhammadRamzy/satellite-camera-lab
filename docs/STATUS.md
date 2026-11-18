# Status — 2026-11-18, Codex

| Phase | State | Evidence / remaining gate |
|---|---|---|
| 0: environment | PASS | Official container route verified; October download failure documented |
| 1: Space ROS | PASS, official July Jazzy release | image-lock.json, exact manifest, successful C++ build/DDS/discovery |
| 2: camera pipeline | PASS for phase scope | Five RGB + one transformed JPEG, checksums and restart; phase2-result.json |
| 3: payload control | PASS | Nine runtime cases: lifecycle/modes, cancel/SAFE, watchdogs, 1 Hz health and restart |
| 4: gateway/bus | PASS | Live SPP ACKs, command journal, reconnect and gateway/bus SIGKILL tests |
| 5: downlink | PASS | Ground SIGKILL/resume, CRC/loss retry, pass window/rate cap, receipt/delete guards, concurrent SAFE |
| 6: mission | NEXT | Five-image delivery all the way to ground |
| 7: complete verification | PARTIAL | 15 host + 5 C++ tests and Phase 2 integration; full fault/benchmark campaigns open |
| 8: migration | PLANNED | ARM64/cFS plans; no ARM execution |

Working: genuine Space ROS nodes, synthetic sensor_msgs/Image and CameraInfo,
typed single-capture service, JPEG processing, durable catalogue/file-ready
results and restart persistence. All nodes stop cleanly after integration tests.

Open functionality: additional camera sources, original preservation,
live file/downlink acceptance,
full fault coverage, benchmark exports, ARM execution. No claim of complete
mission acceptance. The fixed scene/noise seed deliberately yields identical
RGB file hashes across captures; UUIDs/timestamps and capture operations differ.

The October release is newer, but two pulls reset over IPv6. July's digest was
matched directly to the official registry and its source label to its Git tag.
An upgrade remains open; do not silently change config/image-lock.json.
