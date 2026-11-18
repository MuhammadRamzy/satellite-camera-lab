# Official source investigation — 2026-11-18

## Follow-up with network access

Terminal access now works. Direct GitHub API reports the latest release as
`jazzy-2026.10.0`, published 2026-10-31. `git ls-remote` resolves that tag to
`73db1cd98c6962cb1a35efb03a346cf68dbb56e4`. Official Docker Hub API reports the
amd64 development image digest as
`sha256:0cebc6222c3d7af18438c6cedb10213bb0c5ffb9f898e459069550c27d9344bf`.
Saved raw registry response, manifest, release tag and official usage/Dockerfile
are under docs/evidence. These resolve the earlier cached-page discrepancy.
Local image provenance and execution are still a separate acceptance check.

## Initial restricted-session findings (historical)

Sources were inspected with the browsing tool; terminal Git/curl could not resolve
hosts. Browser results can be cached and are not registry attestation.

| Source | Finding |
|---|---|
| https://github.com/space-ros | Official organization inspected |
| https://github.com/space-ros/space-ros | Current official source/build repository |
| https://github.com/space-ros/space-ros/blob/main/Dockerfile | Noble/Jazzy base; separate main and dev images; provenance label and embedded exact source manifest |
| https://hub.docker.com/r/osrf/space-ros/tags | Lists jazzy-2026.10.0 and jazzy-2026.10.0-dev; runtime amd64/arm64, dev amd64 shown |
| https://github.com/space-ros/space-ros/releases | Returned a cached July 2026 latest release; conflicts with newer image listing, so no claim that this independently verifies October source |
| https://space.ros.org/pages/getting_started.html | Legacy instructions mention Humble and old docker repository; prefer current official source over these stale instructions |
| https://space-ros.github.io/docs/rolling/Introduction.html | Space ROS is derived from ROS 2; matching ROS_DISTRO alone cannot prove identity |
| https://docs.ros.org/en/jazzy/Installation/Ubuntu-Install-Debs.html | Browser blocked by Anubis; official raw source below inspected instead |
| https://raw.githubusercontent.com/ros2/ros2_documentation/jazzy/source/Installation/Ubuntu-Install-Debs.rst | Jazzy deb target is Ubuntu Noble 24.04, amd64 and arm64 |
| https://github.com/nasa/cFS | Actual cFS bundle; deferred installation; links NASA CF as CFDP application |
| https://public.ccsds.org/Pubs/133x0b2e1.pdf | Redirect failed in browser |
| https://ccsds.org/Pubs/133x0b2e2.pdf | Official SPP primary header/length definition found; used to check packet length rule |

The newest *listed* Jazzy image is the October candidate. There is no *locally
verified* image yet. Full digests/hashes/versions must come from the actual pull,
image labels, embedded exact.repos, and dpkg/ROS manifests. They remain null in
config/environment.json, deliberately. An abbreviated search-result digest is
insufficient for pinning.
