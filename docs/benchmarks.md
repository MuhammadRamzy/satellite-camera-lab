# Benchmark protocol — not executed

No controlled camera or downlink benchmark campaign has executed. Phase 2 acceptance records real capture/processing/storage latency samples in phase2-result.json, but these are not the requested benchmark matrix. No synthetic numbers are
exported as measurements. Hardware utilization snapshots in environment.log
are inspection data, not experiment results.

After the five-image mission gate, run the full 3×3×3 camera matrix:
640×480, 1280×720, 1920×1080 × JPEG quality 50/75/90 × 1/5/10 FPS.
Use at least five images per combination, a documented warmup, fixed noise seed,
identical scene and a recorded software/image-lock version. Measure both dropped
and accepted frames; command a bounded burst instead of assuming sustained FPS.
Add five repetitions if variability materially affects a comparison.

For each camera setting, replay a fixed file set at 0.5/1/2/4 Mbps under
0/1/5/10% injected loss, fixed latency and a 60-second window. Separately test
600-second windows and interruption/resumption over multiple passes. Record
the PRNG seed and distinguish injected loss from actual OS/network failures.

Proposed CSV columns: run_id, image_digest, source_commit, resolution, quality,
requested_fps, delivered_fps, width, height, raw_bytes, jpeg_bytes,
capture_ms, queue_ms, processing_ms, storage_ms, end_to_end_ms, cpu_percent,
peak_rss_bytes, rate_mbps, window_seconds, loss_probability, seed,
attempted_wire_bytes, payload_delivered_bytes, protocol_bytes, retransmissions,
lost_packets, active_transfer_seconds, wall_seconds, completed_images,
remaining_images, recovery_ms, measurement_kind.

Use monotonic timestamps around actual operations and process/cgroup counters
for CPU and peak RAM. Define CPU normalization (single core vs whole machine).
Report active-pass goodput and total mission wall-clock goodput separately.
Include all attempted frame headers, ACKs and retransmitted bytes in wire-rate
accounting; distinguish transport framing overhead from IP/TCP overhead unless
packet capture measures the latter. Exclude reused partial bytes from newly
delivered payload goodput. Keep timeout/failed runs in CSV.

Calculated comparison only: ideal transmission seconds = file_bytes×8 /
(rate_mbps×1,000,000); ideal pass budget = rate_mbps×1,000,000×window/8.
These equations are not observed throughput. Produce Markdown summaries and
standalone comparison plots only after measurements exist.
