# Requirements and test traceability

PASS=verified phase acceptance; P=passing partial/foundation check; N=not implemented/not run. Partial checks do not close the full mission requirement.

| ID | Requirement | Evidence / planned test | State |
|---|---|---|---|
| ENV-01 | Inspect host and existing ROS | evidence/environment.log | P |
| ENV-02 | Genuine official Space ROS with pinned provenance | Official July digest/commit/manifest, registry match and runtime | PASS |
| ROS-01 | C++ colcon build and DDS communication | payload_smoke build, five messages, discovery logs | PASS |
| CAM-01 | Synthetic/directory/V4L2 capture | Six real synthetic captures decoded independently; other adapters open | P |
| CAM-02 | Cancellable burst action and explicit bounded QoS | Phase 3 feedback/cancel and busy rejection | PASS |
| IMG-01 | Validate/grayscale/crop/resize/JPEG/statistics | Five C++ cases plus color/grayscale Pillow decode and metadata | PASS for phase scope |
| STO-01 | Atomic durable storage and restart | Six actual JPEGs/catalogue persist through process restart; library crash hooks | PASS for phase scope |
| STO-02 | Quota, integrity, safe deletion | test_catalogue quota/corrupted/missing/confirmation | P |
| CTL-01 | Mode/lifecycle and bus SAFE authority | Phase 3 modes, spacecraft authority service and watchdogs; live bus next | P |
| HK-01 | 1 Hz CPU/RAM/disk/state/heartbeat | Phase 3 six consecutive timed samples | PASS |
| ICD-01 | SPP header, bounds, TC validation | test_space_packet known vector/bad headers/every command | P |
| ICD-02 | Sequence validation | test_space_packet roundtrip_sequence_wrap | P |
| ICD-03 | Duplicate journal, ACKs, reconnect dispatch | Phase 4 real capture replay across SIGKILL + uncertainty library test | PASS |
| BUS-01 | Independent cFS behavioral simulator | Separate process, scheduler, persistent IDs, spacecraft authority; full watchdog campaign next | P |
| FIL-01 | Reliable experimental file transport | Phase 5 live resume, CRC, retransmit, final digest | PASS |
| RF-01 | Enforced data rates/windows/loss | Phase 5 timed application-wire cap and interrupted pass | PASS for simulated scope |
| GND-01 | CLI and five-image ground receipt | Complete mission with five independent SHA-256 checks | N |
| VER-01 | Full fault campaign | fault-injection.md | N |
| BEN-01 | Measured factorial benchmark exports | benchmarks.md | N |
| PORT-01 | Pi 5 ARM64 plan/build/runtime | raspberry-pi-migration.md; hardware validation pending | N |

Mission acceptance now has actual Space ROS/payload node execution, synthetic images, processing/compression, mode/lifecycle control, health and process-restart persistence evidence. Transfer, ground delivery, full faults and benchmark measurements remain open.
