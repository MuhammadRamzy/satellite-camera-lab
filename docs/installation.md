# Installation and reproducibility

Host inspection: Ubuntu 26.04.1 x86-64, kernel 7.0.0-43-generic, i7-14650HX,
24 logical CPUs, 14 GiB usable RAM and 43 GiB free disk at initial inspection.
Docker 29.9.3, Compose 5.7.0, Git 2.53.0, Python 3.14.4, colcon-core 0.21.3.
Existing host ROS Lyrical was not sourced or modified. Full initial output is
in evidence/environment.log.

Selected environment: official Space ROS Jazzy on Ubuntu Noble 24.04, using
`osrf/space-ros:jazzy-2026.07.0-dev` at digest
`sha256:411c4810a3bc234071d8fa32d4d7daeaf7528a51c17ff4f88b97d1ab8edba85e`.
Upstream source commit: `749aabd8820dceee6fad7347e014002774906d89`.
Registry response matches the installed image. Complete component revisions are
saved in evidence/spaceros-exact.repos; versions/prefixes in
container-environment.log. Observed rclcpp 28.1.21, ament_cmake 2.5.6, Python
3.12.3, GCC 13.3.0 and Cyclone DDS RMW.

The latest official release/tag verified remotely is October 2026, commit
73db1cd98c6962cb1a35efb03a346cf68dbb56e4. Its dev digest is saved in
config/environment.json and official-registry-tag.json. Two IPv6 download resets
prevented installation. We explicitly selected the existing official July
release, not ordinary ROS. The initial Docker/DNS restrictions are resolved.

Reproduce:

```sh
python3 scripts/bootstrap-spaceros.py
bash scripts/run-phase2.sh
```

With no existing lock, the bootstrap's default candidate is October. To explicitly
select the verified July release on a fresh checkout:

```sh
python3 scripts/bootstrap-spaceros.py --image-tag jazzy-2026.07.0-dev
```

A different explicit release cannot replace an existing lock automatically.
Review and archive the old lock/evidence before upgrading. Both bootstrap and
Phase 2 runner take the workspace lock to prevent overlapping builds/log writes.
Image pulls stream progress to evidence logs.

The derivative application Dockerfile adds only `libjpeg-dev=8c-2ubuntu11`,
verified against Noble apt metadata before installation. Its ID and upstream
base relation are saved in config/payload-image-lock.json. Full application
package versions are in evidence/payload-packages.tsv. Dependencies resolved at
build time are recorded; a long-term rebuild also requires those apt versions
to remain available, or a package mirror/snapshot.

Build/runtime are limited to two CPUs and 4 GiB. DDS test containers have no
external network, dropped capabilities and no-new-privileges. Only the project
is mounted at /workspace. Storage uses Python/rclpy to reuse the same tested
SQLite durability code; camera and processing use C++17/rclcpp. No host packages,
privileged containers, sudo or system configuration changes were needed.
