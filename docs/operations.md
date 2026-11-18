# Operations

Working now: Space ROS DDS smoke, the command-triggered camera pipeline and
Phase 2 capture/restart acceptance. The ground mission CLI is a later phase.
No bus, ground reception or downlink is implied by these local ROS commands.

Rebuild and execute acceptance from the repository root:

```sh
bash scripts/run-phase2.sh
```

This saves real images in a new data/phase2_<UTC> directory; inspect
`docs/evidence/phase2-result.json` for paths, checksums and timings. It completes
five RGB captures, rejects invalid quality zero, performs a sixth grayscale
crop/resize, and verifies all files after restart. Fixed scene/noise seed makes
color images identical by design, with distinct IDs/timestamps.

Start an operator-controlled payload (after building successfully):

```sh
docker run --rm --name satellite-camera-payload --network none \
  --memory 4g --cpus 2 --cap-drop ALL --security-opt no-new-privileges \
  -e ROS_DOMAIN_ID=121 -e ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST \
  -v "$PWD:/workspace" satellite-camera-lab:phase2 \
  bash -c 'source install/setup.bash && exec ros2 launch payload_camera pipeline.launch.py'
```

In another terminal, capture once inside the same payload namespace:

```sh
docker exec satellite-camera-payload bash -c \
  'source /opt/ros/spaceros/setup.bash && source /workspace/install/setup.bash && ros2 service call /payload/capture_single payload_interfaces/srv/Capture "{}"'
```

Use the same `docker exec` shell setup with `ros2 param set /payload_camera
quality 90` or width/height/noise_sigma, and inspect `/payload/file_ready`.
A service response accepts acquisition; file-ready success confirms durable
storage. Busy requests are rejected while one capture is in flight. Files go
to data/payload by default; override launch argument data_dir as needed. Ctrl+C
on the launch terminal stops all three nodes. Starting with the same directory
recovers existing catalogue entries. No image deletion or ground delivery occurs.

The camera is synthetic only at this phase. Burst actions, modes, lifecycle,
other image sources and original-image preservation remain open.

## Planned complete mission (not yet executable)

Planned five-image acceptance scenario, after phases 1–5 pass:

1. Start payload and independently start bus; observe health and gateway connection.
2. Bus permits imaging; lifecycle configure/activate and command READY.
3. Configure 640×480, quality 75, synthetic source; request burst count 5 at 1 FPS.
4. Observe progress, five committed catalogue entries, five file-ready events.
5. Request each image over the separate file link; verify bus spool SHA-256.
6. Schedule a 60-second pass at 1 Mbps, then initiate ground delivery.
7. Observe actual paced chunks, queue depth, ACKs and eventual ground checksums.
8. Propagate five ground receipts to the catalogue. Keep originals unless deletion
   is explicitly commanded after confirmation.
9. Command STANDBY; retain telemetry, timestamps, IDs and checksums as evidence.

SAFE must prevent new captures; abort reports whether an in-flight frame has
already committed. Restart should recover complete fsynced files and flag
corrupt/missing files without announcing them as ready. Quota exhaustion must
fail capture cleanly without deleting pending images.

Log only structured command/result identifiers and bounded error descriptions.
Separate spacecraft elapsed timestamps from UTC; use monotonic time for duration
and rate enforcement so wall-clock changes cannot accelerate the simulated link.
