# Engineering trade-offs

| Choice | Decision / implication |
|---|---|
| Space ROS vs standard ROS 2 | Official Space ROS is mandatory; provenance/tooling and mission assurance work matter. Jazzy compatibility alone does not establish Space ROS identity or flight qualification. |
| DDS | Keep it local to payload; discovery/QoS/buffer overhead must be measured. One-slot image queues reduce RAM and impose backpressure. |
| Lifecycle | Configure devices and storage before activation; SAFE deactivates acquisition while health/gateway remain responsive. Orchestration and failed transitions need explicit tests. |
| JPEG | Natural scene size reduction at lossy quality; quality 50/75/90 must be compared with actual images. Preserve originals only under a configured quota. The verified libjpeg dependency is installed only in the application container; nearest-neighbor resizing avoids another image-library dependency. |
| Storage | SQLite plus fsynced rename gives inspectable catalogue and recovery intent. It cannot make the filesystem rename and SQL commit a single atomic transaction; recovery reconciles them. |
| Quota | Current foundation bounds image data, not SQLite/WAL/filesystem overhead. Keep separate headroom and fail explicitly on actual I/O errors. Single writer ownership is required. |
| CCSDS gateway | Use the standard primary header and separately documented mission fields. Small bounded messages ease validation; authentication is outside SPP and not provided here. |
| TCP vs UART | TCP is convenient for PC development; UART needs framing, corruption handling, bandwidth budgeting and electrical integration. SPP length alone cannot safely resync a noisy UART stream. |
| Bus-mediated vs direct radio | Bus spool keeps radio policy and spacecraft authority centralized, at cost of another storage/copy stage. A direct link would require a new ownership/security contract. |
| Custom transfer vs CFDP | Experimental transport allows controlled PC loss/resume tests; it is not interoperable CFDP. Verified NASA CF plus compatible payload peer is a later integration. |
| Recovery | Durable intents/receipts preserve images; command side effects across crashes require reconciliation, not a promise of exactly-once delivery. |
| CPU/RAM | Cap container to two CPU cores and 4 GiB during smoke/build. Phase 2 latency samples exist, but controlled camera rates, peak RAM and DDS allocations remain unmeasured; do not promise 1080p/10 FPS on Pi. |

Implemented custom functionality: typed ROS camera/processing/storage pipeline,
synthetic source, command service and backpressure, packet codec/stream parser,
sequence tracker, and durable catalogue. Storage uses Python/rclpy to share the
same tested single-owner catalogue code; C++17/rclcpp handles acquisition and
processing. A future flight-oriented storage port would need equivalent fault
coverage rather than a mechanical rewrite.

Planned custom functionality: lifecycle/modes/action/health, gateway, file transport
and ground tools. Simulations: synthetic camera is running; behavioral bus and
radio models are still unimplemented. Genuine Space ROS supplies the pinned
middleware/toolchain used for all payload ROS execution and bundled analysis;
application capture/storage logic is custom, not a Space ROS camera feature.
