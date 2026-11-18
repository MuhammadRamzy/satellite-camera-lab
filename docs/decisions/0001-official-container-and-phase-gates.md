# ADR 0001: Official Space ROS container and honest phase gates

Date: 2026-11-18. Status: accepted for implementation, runtime verification pending.

Host Ubuntu 26.04 has a separate ROS Lyrical installation. Official current
Space ROS builds use Noble and Jazzy. Select the official Jazzy dev image;
resolve and save its immutable digest and complete source provenance before use.
Use dev on x86 initially to discover tools; use the smaller runtime on ARM64
only after package availability and build output are verified.

Do not use host Lyrical or substitute upstream ROS Jazzy. Do not integrate actual
cFS yet. Independently testable foundations may be developed while installation
is blocked, but do not advance sequential mission acceptance gates on that basis.
Record failures and missing evidence explicitly. Consequence: Phase 1 remains
open until Docker/network access and genuine Space ROS execution are available.


## 2026-11-18 implementation update

Session access was restored. October was verified as latest release, but its
large layers reset twice over IPv6. Explicitly select the existing official
July Jazzy dev image after matching its complete digest to registry and source
label to the July Git tag. Pin it in config/image-lock.json; do not describe it
as the latest release. Genuine Space ROS Phase 1 and synthetic Phase 2 gates
now pass with saved evidence. October upgrade and later mission gates remain
open. The small derivative image adds a verified pinned JPEG development package;
its image ID and package inventory are recorded separately.
