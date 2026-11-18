#!/usr/bin/env python3
"""Pull official candidate, pin its identity, capture provenance, run Phase 1."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/evidence"


def run(args, log):
    print(f"Running {' '.join(args)}; log: docs/evidence/{log}", flush=True)
    # Stream to disk so an operator can inspect progress during large pulls.
    with (EVIDENCE / log).open("w") as output:
        result = subprocess.run(args, cwd=ROOT, text=True, stdout=output,
                                stderr=subprocess.STDOUT, check=False)
    if result.returncode:
        raise RuntimeError(f"{' '.join(args)} failed; see docs/evidence/{log}")
    return (EVIDENCE / log).read_text()


def main(image_tag=None):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "config/environment.json").read_text())
    run(["docker", "info"], "docker-info.log")
    lock_path = ROOT / "config/image-lock.json"
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        if image_tag and lock["candidate"] != f"osrf/space-ros:{image_tag}":
            raise RuntimeError("Existing lock selects another release; review it before changing releases")
        pinned = lock["image"]
        if not re.fullmatch(r"osrf/space-ros@sha256:[0-9a-f]{64}", pinned):
            raise RuntimeError("Invalid official image digest in lock")
    else:
        candidate = f"osrf/space-ros:{image_tag}" if image_tag else config["candidate_image"]
        run(["docker", "pull", candidate], "space-ros-pull.log")
        info = json.loads(run(["docker", "image", "inspect", candidate],
                              "image-inspect.json"))[0]
        digests = [d for d in info["RepoDigests"] if d.startswith("osrf/space-ros@sha256:")]
        if not digests:
            raise RuntimeError("Official repository digest absent")
        labels = info["Config"].get("Labels") or {}
        commit = labels.get("org.label-schema.vcs-ref", "")
        if not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise RuntimeError("Full upstream source hash absent; inspect provenance before proceeding")
        if labels.get("org.label-schema.vcs-url") != "https://github.com/space-ros/space-ros":
            raise RuntimeError("Unexpected upstream provenance URL")
        pinned = digests[0]
        lock = {"image": pinned, "image_id": info["Id"], "source_commit": commit,
                "architecture": info["Architecture"], "candidate": candidate}
        lock_path.write_text(json.dumps(lock, indent=2) + "\n")
    run(["docker", "pull", pinned], "space-ros-digest-pull.log")
    # This is the exact workspace source manifest embedded by the upstream image.
    run(["docker", "run", "--rm", "--network", "none", pinned, "cat",
         "/opt/ros/spaceros/scripts/spaceros.repos"], "spaceros-exact.repos")
    env = dict(os.environ, SPACE_ROS_IMAGE=pinned)
    result = subprocess.run(["docker", "compose", "run", "--rm", "smoke"],
                            cwd=ROOT, env=env, check=False)
    if result.returncode:
        raise RuntimeError("Space ROS smoke failed; do not advance to Phase 2")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-tag", help="Explicit official Jazzy release tag when a candidate is unavailable")
    args = parser.parse_args()
    if args.image_tag and not re.fullmatch(r"jazzy-\d{4}\.\d{2}\.\d+-dev", args.image_tag):
        parser.error("Expected an official Jazzy development release tag")
    try:
        with (ROOT / ".bootstrap.lock").open("a") as bootstrap_lock:
            try:
                fcntl.flock(bootstrap_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeError("Another bootstrap owns this workspace; wait for it to finish") from error
            main(args.image_tag)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"BOOTSTRAP BLOCKED: {error}", file=sys.stderr)
        sys.exit(1)
