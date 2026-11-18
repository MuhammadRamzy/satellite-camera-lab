# Engineering progress report — 2026-11-18, Codex

Outcome: genuine Space ROS execution and the Phase 2 synthetic camera pipeline
are demonstrated with saved evidence. This is not the complete satellite mission.

Phase 1: the official July 2026 Jazzy development image was matched to the
registry, pinned by digest and full source commit, and its exact component
manifest/installed versions captured. C++ DDS publisher/receiver, build and
node/topic discovery passed. October is newer but two IPv6 connection resets
blocked its download; that upgrade is explicitly open.

Phase 2: independently executable C++ camera/processing nodes and Python storage
node communicate through typed ROS interfaces. The camera publishes rgb8 images
and uncalibrated CameraInfo. Processing validates buffers, optionally crops,
nearest-neighbor resizes and converts to grayscale, compresses JPEG at configured
quality, and measures statistics/latency. One in-flight capture, released after
storage result, bounds normal acquisition. SQLite storage preserves capture UUID,
UTC/spacecraft time, dimensions, encoding, quality, size, checksum, status and
processing/storage duration; file-ready follows durable commit.

Acceptance: five RGB 640×480 JPEGs plus one grayscale crop/resize 160×120,
independent Pillow decode and six SHA-256 matches; same entries/files after all
three processes restart. Fifteen host tests and five C++ cases pass. Bundled
ament_cppcheck and Cppcheck pass the processing core. See test-results.md and
phase2-result.json for actual values/paths. Timing samples are acceptance-run
observations, not the requested controlled benchmark campaign.

Found/fixed: duplicate SIGINT from the test harness interrupted storage cleanup;
signal only launch and require clean launcher/child exits. Added a shared build
lock and streaming pull logs. No user/system configurations or host ROS changed.

Files changed: source/interface packages, launch/config, container/bootstrap and
acceptance scripts, catalogue identity/timestamp support, tests and documentation.
All changes are uncommitted. No full mission acceptance claim.

Phase 3 now passes nine runtime control/fault/restart cases; see
`evidence/phase3/result.json` and `test-results.md`. Lifecycle, spacecraft authority,
burst feedback/cancel and 1 Hz health are implemented. Next is the live gateway.
Directory/V4L2 inputs and original preservation also remain.
Gateway runtime, behavioral bus, experimental file transport, radio, ground CLI,
full fault/benchmark campaigns, actual cFS/CFDP and ARM64 execution are open.

Independent review follow-up: Claude quota prevented review; AGY did not return
a review within more than three minutes. Neither result is an approval. See
docs/reviews/2026-11-18-phase2-review-status.md.
