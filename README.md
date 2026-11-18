# Satellite Camera Lab

Phases 1–4 are working in a genuine, pinned Space ROS Jazzy environment.
This is an engineering prototype in progress, not the complete satellite mission.

Implemented: C++17 synthetic camera and image processing nodes; typed ROS
messages/service; real JPEG files; grayscale/crop/resize/statistics; Python ROS
storage node with SQLite, atomic writes, quota, checksums and restart recovery.
A single in-flight capture provides backpressure across the bounded pipeline.
Lifecycle/mode control, spacecraft SAFE authority, cancellable burst actions,
watchdogs and 1 Hz housekeeping pass nine runtime acceptance cases.

Verified: five 640×480 RGB images plus a 160×120 grayscale transformation,
independent Pillow decoding, six SHA-256 matches, preserved catalogue/files
through process restart, 15 host tests and five C++ processing tests. Bundled
ament_cppcheck passes for the processing core. See [evidence](docs/test-results.md).

The installed base is official `osrf/space-ros:jazzy-2026.07.0-dev`, pinned in
[config/image-lock.json](config/image-lock.json). The newer October release was
verified in the registry but its download failed twice with IPv6 connection
resets. No ordinary ROS replacement or host ROS installation was used.

From this directory:

```sh
bash scripts/test-host.sh
python3 scripts/bootstrap-spaceros.py
bash scripts/run-phase2.sh
bash scripts/run-phase3.sh
```

The bootstrap reuses the locked official image and tests DDS. The Phase 2 runner
builds a small derivative container, builds/tests the workspace, performs real
capture/restart acceptance, and saves new images under `data/phase2_<UTC>/`.
It needs working Docker and registry/Ubuntu mirror access for uncached builds;
no camera hardware or privileged container is required.

For interactive capture see [the operator guide](docs/START-HERE.md). Next gate is
Phase 5: live experimental file transfer and simulated radio acceptance.
The live SPP gateway and independent behavioral bus now pass duplicate/reconnect
and process-restart tests. This bus is a cFS behavioral simulator, not real cFS.
Directory/V4L2 camera sources, original-image preservation, CCSDS gateway runtime,
bus, file transport, radio, ground CLI, full faults and benchmarks remain open.
The host-testable packet codec is implemented; it is not a live gateway.

Start with [status](docs/STATUS.md), [report](docs/engineering-report.md), and
[handoff](docs/HANDOFF.md). No real cFS or CFDP is installed.
