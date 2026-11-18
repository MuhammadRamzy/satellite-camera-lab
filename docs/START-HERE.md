# Start here: the satellite camera experiment, explained from zero

This guide explains what we are building, why it matters, how to run it, and how
to judge the results. You do not need previous ROS or spacecraft experience.

## 1. The end goal

We want to prove that a satellite camera computer can work separately from the
computer that controls the rest of the spacecraft.

Imagine two computers on a satellite:

- **The bus computer is the spacecraft's manager.** It decides when the camera
  may operate, sends commands, watches for problems, and controls communication
  with Earth. The eventual flight software choice here is NASA cFS.
- **The payload computer is the camera's worker.** It takes pictures, processes
  and compresses them, saves them safely, and provides them to the bus. This
  experiment actually uses Space ROS for that computer's software environment.

The final demonstration should let you command five pictures, save them, move
them through the simulated bus and radio, receive them on the ground, and prove
that the received files match the originals. It should also show what happens
when communication is interrupted or a component fails.

For now, your Ubuntu PC plays all these roles. No camera, Raspberry Pi, radio,
or satellite is required. Eventually the payload can move to a Raspberry Pi 5.

## 2. What is real, and what is simulated?

| Part | What it means here |
|---|---|
| Space ROS | A real official Space ROS installation inside a container; its identity is recorded, not just a renamed ordinary ROS installation. |
| Camera pictures | Real JPEG files generated from a synthetic scene. They are test images, not photographs from a physical camera. |
| Image processing | Real software changes pixels and compresses them as JPEG. |
| Storage | Real files and a real SQLite database on your PC. |
| Payload processes | Real programs communicating through ROS. |
| Satellite bus | A behavioral simulator to be implemented in Phase 4; actual NASA cFS is not installed. |
| Radio and ground delivery | Planned for later phases; no downlink has been demonstrated yet. |
| File transfer | Will first be a custom experimental protocol. That must not be called CFDP. A real CFDP implementation is a separate later experiment. |

The current picture is deliberately repeatable: the scene and noise seed stay
fixed. Several captures can therefore have the same image checksum while still
having different capture IDs and timestamps. That is useful for controlled tests.

## 3. The few terms you need

| Term | Plain meaning |
|---|---|
| ROS 2 | Software that helps separate programs exchange data and commands. |
| Space ROS | A ROS-derived environment developed for space robotics, with additional engineering and analysis tooling. Using it does not by itself prove a complete flight system. |
| Node | One program doing a particular job, such as making an image or saving a file. |
| Topic | A named stream of messages. A camera publishes images; another node receives them. |
| Service | A request and a reply, such as “change to READY” and “accepted.” |
| Action | A longer job with progress and cancellation, such as “capture five images.” |
| Lifecycle | The camera's software readiness: unconfigured, inactive, or active. |
| Payload mode | What the camera system is allowed to do: STANDBY, READY, SAFE, and so on. |
| DDS | The communication layer ROS uses. We keep it inside the payload container. |
| Docker/container | An isolated software environment. Our container runs the supported Space ROS environment while your PC keeps its own Ubuntu installation. |
| SQLite catalogue | A small database listing the pictures and their metadata. |
| SHA-256/checksum | A fingerprint of a file's bytes. Matching fingerprints are our integrity check during storage and later transfer. |
| Telemetry/housekeeping | Regular reports of state, resource usage, stored files, and health. |
| CCSDS packet | A standard spacecraft packet header. Our mission commands will be carried inside it. |
| S-band | A radio-band choice. Our later simulator models its limited data rate and contact windows, not actual radio waves. |

Two kinds of state matter. **Lifecycle ACTIVE** means the camera software is
activated. **Payload READY plus spacecraft permission** means taking a picture
is authorized. One of these alone must not bypass the other.

## 4. What we have built so far

The project has nine phases, numbered 0 to 8:

| Phase | Purpose |
|---|---|
| 0 | Inspect the PC and verify the installation route. |
| 1 | Run genuine Space ROS and prove separate programs can communicate. |
| 2 | Capture synthetic images, process/compress them, save them, and survive restart. |
| 3 | Add modes, lifecycle, burst actions, SAFE behavior, and health monitoring. |
| 4 | Connect a separate simulated bus through the binary CCSDS gateway. |
| 5 | Add file transfer, radio simulation, and the ground station. |
| 6 | Run the complete five-image mission from command to ground reception. |
| 7 | Run the full fault campaign and controlled benchmark matrix. |
| 8 | Prepare and verify the Raspberry Pi migration path. |

Phases 0–2 have passed their scoped checks. **Phase 3 is being implemented and
tested now.** See [STATUS.md](STATUS.md) for the current acceptance status; the
presence of source files alone does not mean that a phase passed.

The running payload consists of these jobs:

```text
Mode manager: checks permission and handles mode requests
        |
Camera: generates an RGB picture
        |
Processing: validates, optionally transforms, and compresses it
        |
Storage: writes the JPEG and database entry
        |
File-ready result: confirms that the picture is safely stored

Health monitor: reports on this system once per second
```

The camera allows one image through this chain at a time. A burst's progress
counts pictures that reached durable storage, not just pictures requested.
If SAFE is commanded, new exposures stop; an already-started write can finish.

## 5. Which terminal and folder do I use?

Open your normal Ubuntu terminal. Start here:

```sh
cd /home/mhdramzy/Hex20/satellite-camera-lab
```

Commands beginning with `bash scripts/...` or `python3 scripts/...` below are
run in that normal terminal. Our scripts start the required container for you.
You do not need to install or source another ROS version on your PC.

The project folder appears inside the container as `/workspace`. For example,
`/workspace/data/example.jpg` inside it is `data/example.jpg` in this project
folder on your PC. The files remain after the container exits.

The installed Space ROS release is the official **July 2026 Jazzy development
image**. The newer October image was verified in the registry, but downloading
its large layers repeatedly failed. We used the verified July Space ROS image
and pinned its exact identity. The recorded identity is in
`config/image-lock.json`; it is not the host's separate ROS Lyrical installation.

## 6. First run: test the camera pipeline

From the project folder:

```sh
bash scripts/run-phase2.sh
```

The first uncached build can take longer and needs internet access. Later runs
reuse the downloaded dependencies. Let the command finish. It builds the code,
runs processing tests, starts the payload, captures images, checks them, restarts
the payload, and checks that the files are still there. It stops its test nodes
when finished.

You should see build/test output and a final report containing `"result": "PASS"`.
The report is saved here:

```sh
cat docs/evidence/phase2-result.json
```

That test produces **five 640×480 color images and a sixth 160×120 grayscale
crop/resize check**. Six is intentional; it is not a five-image downlink mission.

Print the image paths and open one:

```sh
python3 - <<'PY'
import json
report = json.load(open('docs/evidence/phase2-result.json'))
for image in report['images']:
    print(image['path'])
PY
```

Copy one printed path and use `xdg-open` before it. For example:

```text
xdg-open data/phase2_<run-time>/<capture-id>.jpg
```

The angle-bracket parts are placeholders: use an actual printed path.
You should see a synthetic colored planet and stars, or the smaller grayscale
image. A real decoded JPEG and a matching database checksum demonstrate the
local capture/processing/storage chain. **They do not demonstrate a radio link
or successful ground reception.**

## 7. Phase 3: run the automated control experiment

While Phase 3 is under development, this command is a development check. Treat
it as passed only when its latest report says PASS and [STATUS.md](STATUS.md)
marks the phase complete:

```sh
bash scripts/run-phase3.sh
```

It is designed to check these behaviors:

1. Startup is STANDBY/inactive, with imaging inhibited.
2. Permission alone does not take pictures; an explicit READY request is needed.
3. A five-image action reports progress as files are committed.
4. Cancellation during a simulated slow write stops further exposures and drains
   the write already in progress.
5. A spacecraft SAFE command stops imaging and cannot be undone by a local READY
   command. Returning permission still requires explicit recovery.
6. Pausing the controller causes its lease to expire and inhibits the camera.
7. Pausing processing causes a missing-heartbeat fault; recovery is explicit.
8. Health reports arrive at roughly one-second intervals with correct file counts.
9. A restart preserves pictures and comes back inhibited.

The pause tests send signals only to programs started by that test. They are
not intended to pause your desktop or other projects. This exercise does not
yet test an actual radio interruption or a real camera crash.

Read the results:

```sh
cat docs/evidence/phase3/result.json
cat docs/evidence/phase3/cases.csv
```

`result.json` is written on successful acceptance. If it is absent, or the
command exits with an error, inspect `cases.json` and the logs; do not infer
success from an earlier screenshot or from source files existing.

## 8. Watch a payload running continuously

Automated tests exit when done. To keep a payload running so you can inspect it,
first build successfully, then use a normal terminal in the project folder:

```sh
docker run --rm --name satellite-camera-payload --network none \
  --memory 4g --cpus 2 --cap-drop ALL --security-opt no-new-privileges \
  -e ROS_DOMAIN_ID=121 -e ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
  -v "$PWD:/workspace" satellite-camera-lab:phase2 \
  bash -c 'source install/setup.bash && exec ros2 launch payload_camera pipeline.launch.py'
```

Leave this terminal running. It will show logs. The container has no external
network; ROS communication stays inside it. In a **second normal terminal**:

```sh
docker exec -it satellite-camera-payload bash
source /opt/ros/spaceros/setup.bash
source /workspace/install/setup.bash
```

This second shell is now **inside the running payload container**. The ROS
commands in the next sections run there. You do not need to source the PC's ROS.

Start with discovery:

```sh
ros2 node list
ros2 topic list -t
ros2 service list -t
ros2 action list -t
ros2 lifecycle get /payload_camera
```

You should find camera, processing, storage, modes and health nodes. The camera
should be `inactive` after startup, not automatically capturing.

**What this tells you:** programs and interfaces exist and can be discovered.
**What it does not tell you:** that pictures have been captured or saved.

## 9. Authorize imaging, then capture a picture

In the container shell from the previous section:

```sh
ros2 service call /payload/spacecraft_control payload_interfaces/srv/SpacecraftControl \
  '{spacecraft_mode: 1, imaging_permitted: true}'
ros2 service call /payload/set_mode payload_interfaces/srv/SetMode \
  '{mode: 2, recover: false}'
ros2 lifecycle get /payload_camera
ros2 service call /payload/capture_single payload_interfaces/srv/Capture '{}'
```

`spacecraft_mode: 1` means OPERATIONAL; payload `mode: 2` means READY. These are
two different mode systems, despite both using numbers. The camera lifecycle
should now be `active`.

The capture service's `accepted: true` means the picture request was accepted.
It is **not** yet the final storage confirmation. Watch the first terminal for
`file_ready`, or run this in the container shell:

```sh
ros2 topic echo /payload/file_ready
```

That topic is a live stream, so start watching before another capture if you
want to see its event. A successful result includes the capture ID, file path
and checksum. It means the storage node committed the image and catalogue entry.
If you request another image while one is in flight, a busy rejection is expected.

## 10. Try a five-image action

Inside the same container shell, after READY:

```sh
ros2 action send_goal /payload/capture_burst payload_interfaces/action/CaptureBurst \
  '{count: 5, interval_ms: 1000}' --feedback
```

You should see feedback counting to five, then a successful result. The interval
requests one capture per second. If processing/storage takes longer, the camera
waits; we do not claim a rate that the pipeline cannot sustain.

The automated Phase 3 test checks actual action cancellation. Closing a CLI or
pressing Ctrl+C in an action client's terminal alone should not be treated as
proof that the server cancelled the job.

## 11. Try SAFE and recover deliberately

Inside the container shell:

```sh
ros2 service call /payload/spacecraft_control payload_interfaces/srv/SpacecraftControl \
  '{spacecraft_mode: 2, imaging_permitted: false}'
ros2 lifecycle get /payload_camera
ros2 service call /payload/capture_single payload_interfaces/srv/Capture '{}'
```

Here **spacecraft mode 2 means SAFE**. The camera should be inactive, and the
capture request should be rejected. Existing files stay on disk. A burst should
abort, while a write already started can finish.

To recover, return spacecraft permission, explicitly recover to STANDBY, then
command READY:

```sh
ros2 service call /payload/spacecraft_control payload_interfaces/srv/SpacecraftControl \
  '{spacecraft_mode: 1, imaging_permitted: true}'
ros2 service call /payload/set_mode payload_interfaces/srv/SetMode \
  '{mode: 1, recover: true}'
ros2 service call /payload/set_mode payload_interfaces/srv/SetMode \
  '{mode: 2, recover: false}'
```

If a capture is still draining, wait for its file result before recovery.
If a node is missing or unhealthy, restore it first. Repeated READY requests
should not bypass these conditions.

## 12. Read health reports

Inside the container shell:

```sh
ros2 topic echo /payload/housekeeping
```

| Field | What to look for | What you can infer |
|---|---|---|
| heartbeat_sequence | Increases every report | The health node is still making new reports. A sequence reset with a new boot ID means it restarted. |
| payload_mode / payload_state | STANDBY, READY, processing stages, SAFE or FAULT | What the payload is doing or allowed to do. |
| camera_lifecycle_state | Usually 2 inactive or 3 active | Whether the acquisition component is activated. |
| cpu_percent | Finite number from 0 to 100 | Recent CPU use, normalized to the container CPU allowance when resource_scope is cgroup. |
| memory_used_bytes / memory_total_bytes | Used is below total | Current memory use and available limit; this is not the full benchmark's measured peak RAM. |
| disk_used_bytes / disk_total_bytes | Sufficient free space | Usage of the underlying disk volume. The separate image quota also applies. |
| stored_images / pending_files | Increase after captures | Durable files and files not confirmed delivered. Pending is expected: downlink is not implemented yet. |
| nodes_healthy / stale_nodes | Healthy true; no stale required nodes | Recent heartbeats are arriving. This is a heartbeat/resource check, not proof of image scientific quality. |
| gateway_connected | False at this phase | Correct: the bus/gateway connection has not been implemented. |

One report is a snapshot. Watch a sequence over time, and compare it to actual
file results before drawing conclusions. The acceptance test checks report
timing and counts automatically.

## 13. Where pictures and evidence live

On your PC, relative to the project folder:

| Location | Contents |
|---|---|
| data/phase2_<UTC>/ | Images/database from a camera acceptance run. |
| data/phase3_<UTC>/ | Images/database from a Phase 3 control/fault exercise. |
| data/payload/ | Default storage for your continuously running operator payload. |
| docs/evidence/phase2-result.json | Image paths, hashes and acceptance observations. |
| docs/evidence/phase3/result.json | Successful Phase 3 acceptance summary. |
| docs/evidence/phase3/cases.csv | Expected/observed behavior and recovery measurements per test. |
| docs/evidence/phase3/housekeeping.csv | Recorded health reports, including fault periods. |
| docs/test-results.md | Human-readable verification summary and limitations. |
| docs/HANDOFF.md | What the last coding session changed and what remains. |

Do not edit or remove the live catalogue while the storage node owns it. For
this experiment, simply leaving the captured images in place is fine. Future
deletion will be guarded by confirmed delivery; starting a transfer is not enough.

## 14. How to judge whether the experiment is succeeding

Think of the proof in layers:

1. **Environment proof:** official image identity and real Space ROS execution.
2. **Local payload proof:** commands cause real images, processing and durable files.
3. **Control proof:** unauthorized imaging is rejected; cancellation, SAFE and
   watchdog faults stop new captures; explicit recovery restores operation.
4. **Link proof, still later:** commands cross the separate CCSDS gateway and
   files move reliably through the bus and limited simulated radio.
5. **Mission proof, still later:** the ground receives complete images with
   matching checksums, including after interruptions and simulated packet loss.
6. **Performance proof, still later:** controlled runs establish actual CPU,
   memory, latency, compression and downlink limits at the requested settings.

A green test for an earlier layer does not prove a later one. In particular,
seeing a picture on your PC is exciting evidence of the local pipeline, but
the full satellite-to-ground experiment needs the remaining phases.

## 15. Common surprises and what to do

- **“Capture rejected” immediately after startup:** expected. Grant spacecraft
  imaging permission and command READY, then check the lifecycle.
- **“Busy”:** one picture or a burst is still active. Wait for completion, cancel
  the action through a client, or command SAFE.
- **“Recovery required”:** returning permission does not clear a latched SAFE or
  FAULT. Restore healthy nodes and explicitly recover via STANDBY.
- **No file-ready event in `topic echo`:** it is a live topic, not a history
  browser. Start echo first, then request a new capture, or inspect the catalogue.
- **Several pictures have matching hashes:** expected with the fixed synthetic
  scene and noise seed. Their capture IDs/timestamps should still differ.
- **“Another bootstrap/build owns this workspace”:** a build/test is already
  running. Let it finish; do not start a competing run.
- **Image download fails:** read the pull log. It does not mean your camera code
  failed. The existing locked official image is the known working environment.
- **An automated command fails:** inspect its latest logs and failed case. Do
  not treat a missing new success report as a pass.

To stop the continuously running payload, press Ctrl+C in its first terminal.
To leave the second container shell, type `exit`. The image files remain on disk.
