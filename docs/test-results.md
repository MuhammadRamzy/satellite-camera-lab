# Test results — 2026-11-18

PASS: Phases 1–3, including the synthetic pipeline and payload control.

Phase 4: three live gateway/bus cases passed; see
`evidence/mission/phase4-result.json` and its per-run commands/cases/logs.
The current host suite has 21 tests, including real socket-pair transfer faults
and persistent journal uncertainty/replay tests. Phase 2 and Phase 3 regressions
passed after adding the storage catalogue service. Failed attempts are retained.

Phase 3 rerun: nine runtime cases passed in the pinned official container;
16 independently decoded/checksummed JPEGs survived restart. Evidence:
`evidence/phase3/result.json`, `cases.csv`, `housekeeping.csv`, and
`evidence/phase3-execution.log`. Six consecutive healthy telemetry samples
arrived at 0.999–1.001 s intervals. SAFE and cancellation drain existing writes;
paused mode/processing nodes cause a latched FAULT and stop new exposures.
There are 18 host tests and nine C++ cases (colcon reports 11 records including
two CTest wrappers). Earlier failed telemetry runs remain preserved; the current
verifier waits for healthy count-consistent recovery before timing steady state.

| Check | Actual result | Evidence |
|---|---|---|
| Host tests | 15 passed | evidence/host-tests.log, host-checks.log |
| C++ processing | Five GTest cases passed | evidence/phase2-unit-tests.log |
| Colcon aggregate | Six records, zero failures (five cases plus CTest wrapper; not six independent cases) | evidence/phase2-test-result.log |
| DDS smoke | Separate receiver got five messages | evidence/smoke-receiver.log |
| Build/discovery | Five workspace packages build; nodes/topics/service inspected | evidence/phase2-build.log, phase2-nodes.log, phase2-topics.log, phase2-services.log |
| Real capture | Five 640×480 RGB JPEGs, one 160×120 grayscale crop/resize | evidence/phase2-result.json |
| Integrity | All six files independently decoded by Pillow and matched SHA-256/catalogue/receipt | evidence/phase2-result.json |
| Invalid camera config | Quality zero rejected; no capture accepted | evidence/phase2-pipeline.log |
| Persistence | Same six files/catalogue entries after all payload processes restarted | evidence/phase2-restart.log, phase2-result.json |
| Shutdown | All three payload nodes finished cleanly | evidence/phase2-pipeline.log, phase2-restart.log |
| Bundled static analysis | Processing core passes ament_cppcheck and compilation-database Cppcheck | evidence/phase2-ament-cppcheck.log, phase2-static-analysis.log |
| Syntax/config | Python compilation, ShellCheck, Bash syntax, Compose configuration pass | evidence/host-checks.log |
| Bootstrap concurrency | Second launch rejected without touching active build | evidence/bootstrap-concurrency.log |

The catalogue library tests additionally cover interrupted-write exception hooks,
quota ENOSPC, corruption/missing detection, delivery-protected deletion, UUID
identity and UTC preservation. These are not real node crash/disk-full tests.

Failures found and resolved: the first integration invocation preceded creation
of the verifier script; rerun succeeded after adding it. The first shutdown
method sent SIGINT to the group and launch then forwarded another SIGINT,
interrupting Python cleanup. Sending once to launch fixed it; the verifier now
requires clean launcher/child exits. All final evidence reflects the fixed run.

October image pulls failed twice with connection resets; preserved in
space-ros-pull-network-reset*.log. The installed/tested official July image is
fully pinned with source provenance. Initial Docker/DNS session restrictions
were resolved when the user enabled access.

NOT RUN: live file/radio
transport, ground mission, full fault campaign, benchmark matrix, ARM hardware.
No downlinked or ground-received images exist yet. No real cFS or CFDP claim.

Cross-review attempted with the shared agent-review tool in an isolated snapshot.
Claude quota was exhausted; Antigravity returned no review after more than three
minutes and was stopped. No independent review approval is claimed. See
reviews/2026-11-18-phase2-review-status.md.
