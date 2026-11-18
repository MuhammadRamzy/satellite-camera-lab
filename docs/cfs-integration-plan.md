# cFS integration and CFDP evaluation plan

No real cFS has been installed. The standalone behavioral simulator is planned,
not implemented. It must identify itself as simulation in startup, logs and CLI.
Map SCH to timeline ticks, SC to validated stored command sequences, HK to
telemetry collection, HS to heartbeat timeout and SAFE policy, CI/TO to operator
commands and telemetry, and payload/file applications to the ICD and bus spool.

After the simulated mission and fault campaign pass, pin an official
[NASA cFS bundle](https://github.com/nasa/cFS) and its submodule hashes in an
isolated workspace. Record toolchain, configuration, build/tests and actual app
versions. Do not treat the public demo bundle as a flight-ready distribution.

Write a payload-interface app with explicit message IDs, software bus
subscriptions, scheduled read/write budgets, bounded queues and lifecycle
configuration tables. It replaces the simulator by implementing the same
mission ICD; it does not import payload internals or expose DDS to the bus.
Use cFE/OSAL APIs appropriate to the pinned release, verified against its headers.
Test command acknowledgements, reconnect, watchdog, time conversion, queue
saturation and preserved delivery receipts with an unchanged payload.

CFDP is a separate experiment after custom reliable transfer works. The official
bundle links [NASA CF](https://github.com/nasa/CF), a candidate cFS CFDP app. Its
existence does not establish a standalone payload peer compatible with Python
or ARM64. Verify release, dependencies, license, supported CFDP classes/options,
entity IDs, transport adapter, fault modes and a compatible independent peer.
Then test interrupted transfers, metadata, checksum algorithms, transaction
persistence and file directives. Record wire evidence of actual CFDP PDUs.
Do not call the custom transport CFDP or wrap it with a new label. Selection and
integration of a payload CFDP peer remain unresolved.
