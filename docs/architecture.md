# Architecture and implementation boundary

Target data path: ground CLI → cFS behavioral bus simulator → CCSDS gateway →
Space ROS camera → processing → persistent storage → reliable experimental
TCP file link → bus spool → rate-limited S-band simulator → ground verifier.

Current executable payload: separate C++ camera/processing and Python storage
ROS nodes, a typed single-capture service, image/CameraInfo topics and file-ready
results, running inside genuine pinned Space ROS. Real JPEGs and SQLite entries
survive process restart. Host-testable packet codec and catalogue libraries also
pass their tests. The bus/gateway/transport/radio/ground rows below remain design
targets; they are not running services. Payload control is the next phase.

| Boundary | Planned execution | Interface |
|---|---|---|
| Payload | Dedicated official Space ROS container | DDS only within container network namespace |
| Bus | Independent Python process/container, no payload imports | SPP v1 mission ICD on a dedicated TCP socket |
| Bulk transfer | Independent TCP endpoint in payload | Custom experimental reliable file transport; separate port |
| Downlink | Bus spool reader + throttled link process | Segmented transfer with pass scheduling, loss and acknowledgements |
| Ground | Independent CLI and receiver process | Bus operator endpoint and received files |

Implemented packages: payload_interfaces (typed image/metadata/result and capture
service), payload_camera (C++ synthetic node), payload_processing (C++ bounded
transform/JPEG node), payload_storage (Python single-owner catalogue wrapper), and
payload_smoke. No bus internals are imported. Lifecycle, additional source adapters,
burst action, payload_gateway, payload_modes and payload_health are next work.

Depth-one reliable volatile image queues plus camera single-in-flight gating
bound the normal pipeline. The camera releases its gate on a matching file result;
a 30-second uncertain timeout keeps imaging blocked and logs an error. Processing
statistics describe pixels before JPEG compression. Fixed seed/scene makes images
reproducible. Camera exposure is metadata only; CameraInfo is explicitly uncalibrated.
Simulated spacecraft time is monotonic elapsed time since camera node boot;
it resets on restart until the later bus-controlled epoch is implemented. UTC
is preserved independently in every stored record. Acquisition is operator-triggered; the Phase 2 acceptance schedules captures at
1 FPS, not a claim of continuous 10 FPS camera operation.

Use depth-one image QoS with explicit reliability; command acceptance must report
queue-full rather than overwrite a commanded capture. A burst action generates
feedback only after a capture reaches the defined completion stage, supports
cancel between frames, and releases all buffers. Measure queue wait separately
from processing time. A mode change to SAFE prevents new exposure immediately
and cancels the action; finish or recover any already-started atomic write.

Bus spacecraft modes govern permission to image. Payload internal progress
(CAPTURING/PROCESSING/STORING) is not a spacecraft mode. The bus must be able to
disable acquisition regardless of previously accepted local commands.

Each durable image has a UUID and ground-confirmed checksum acknowledgement.
Bus reception alone does not confirm ground delivery. Never delete on start of
transfer. A file-ready event is emitted only after catalogue commit. Ground
confirmation propagates through the bus to payload before deletion is allowed.

Use loopback-bound operator endpoints on the PC. SPP does not authenticate a
command. A physical spacecraft deployment needs a separately designed trusted
link/security boundary; this prototype does not claim flight qualification.
