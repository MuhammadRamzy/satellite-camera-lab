# Raspberry Pi 5 deployment plan

Target: Ubuntu 24.04 ARM64, Pi 5 with 4 GB RAM. The PC now runs pinned official July amd64 Space ROS with verified JPEG dependency; this does not establish ARM availability for that dev image. No Pi hardware is required for
the PC prototype, and no ARM execution/build has been performed yet.

Official Docker Hub lists an arm64 variant for the October Space ROS runtime;
the dev variant was only listed for amd64 at inspection. Verify registry
manifests directly before claiming multiarch development support. An amd64 dev
digest must never be used as an ARM runtime base. A manifest-list digest and
each resolved architecture-specific digest must both be recorded for release.

Migration sequence:

1. Complete all PC gates and establish measured memory/CPU/storage budgets.
2. Inspect the pinned official runtime manifest for linux/arm64. If tooling or
   application dependencies are missing, build from the pinned official source
   using its verified build.sh instructions on native ARM64, or an isolated
   multiarch builder. Record toolchain and exact source manifest. Emulation is
   acceptable for build/smoke only, not Pi performance claims.
3. Build application packages against the ARM64 Space ROS installation, then
   copy only application installation and verified runtime libraries into the
   runtime image. Remove x86 assumptions; use C++17, explicit wire widths and
   SQLite/OpenSSL/JPEG only after availability is confirmed on both architectures.
4. Run the same synthetic mission/fault suite on Pi with 4 GB limit and low build
   parallelism (1–2 workers). Start at 640×480/1 FPS, depth-one queue. Measure
   actual peak RSS, thermal throttling, swap activity and SD/eMMC write latency.
5. Replace source adapter with USB V4L2 after testing permissions/formats/exposure.
   CSI requires a driver/libcamera integration verified for the selected Ubuntu
   kernel; a CSI camera must not be assumed to expose a usable V4L2 capture stream.
6. Bind only the required video device and persistent data directory. Avoid
   privileged containers. Verify restart, disk quota and power-loss recovery.

Unsupported/unverified: native Space ROS on host Resolute, ARM64 dev image,
candidate camera/codec dependencies and CSI driver. Valid fallbacks are a
reproducible official Space ROS source build and the synthetic/USB source, not
silently replacing Space ROS with ordinary ROS. Multiarch application Dockerfiles
will be added only after the actual required dependencies and image manifests
are verified; no untested build is advertised as working.
