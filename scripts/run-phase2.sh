#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
check_phase=${PAYLOAD_CHECK_PHASE:-2}
case "$check_phase" in 2|3|mission) ;; *) echo 'Supported check phases: 2, 3 or mission' >&2; exit 1;; esac
exec 9>.bootstrap.lock
flock -n 9 || { echo 'Another bootstrap/build owns this workspace' >&2; exit 1; }
mkdir -p docs/evidence
base_image=$(python3 - <<'PY'
import json, re
image = json.load(open('config/image-lock.json'))['image']
if not re.fullmatch(r'osrf/space-ros@sha256:[0-9a-f]{64}', image):
    raise ValueError('An official verified Space ROS lock is required')
print(image)
PY
)
docker build -f docker/Dockerfile.payload --build-arg "SPACE_ROS_BASE=$base_image" \
  -t satellite-camera-lab:phase2 . 2>&1 | tee docs/evidence/payload-image-build.log
docker image inspect satellite-camera-lab:phase2 > docs/evidence/payload-image-inspect.json
image_id=$(python3 - <<'PY'
import json, re
from pathlib import Path
base = json.load(open('config/image-lock.json'))
image = json.load(open('docs/evidence/payload-image-inspect.json'))[0]
if not re.fullmatch(r'sha256:[0-9a-f]{64}', image['Id']):
    raise ValueError('Invalid built image ID')
Path('config/payload-image-lock.json').write_text(json.dumps({
    'image_id': image['Id'], 'base': base['image'],
    'base_source_commit': base['source_commit'], 'architecture': image['Architecture'],
    'dockerfile': 'docker/Dockerfile.payload'}, indent=2) + '\n')
print(image['Id'])
PY
)
docker run --rm --network none --memory 4g --cpus 2 --cap-drop ALL \
  -e "MISSION_THROUGH=${MISSION_THROUGH:-6}" -e "MISSION_REGRESSION=${MISSION_REGRESSION:-1}" \
  --security-opt no-new-privileges -v "$PWD:/workspace" "$image_id" \
  bash "scripts/phase${check_phase}-container.sh" 2>&1 | tee "docs/evidence/phase${check_phase}-execution.log"
