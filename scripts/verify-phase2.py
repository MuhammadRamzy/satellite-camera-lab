#!/usr/bin/env python3
"""Actual ROS camera/storage integration and independent image verification."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time

from PIL import Image
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from rclpy.parameter import Parameter
from rclpy.parameter_client import AsyncParameterClient
from payload_interfaces.msg import FileReady, CameraStatus
from payload_interfaces.srv import Capture, SetMode, SpacecraftControl
from sensor_msgs.msg import Image as RawImage, CameraInfo

ROOT = Path('/workspace')
EVIDENCE = ROOT / 'docs/evidence'


class Probe(Node):
    def __init__(self):
        super().__init__('phase2_verifier')
        self.results = {}
        self.raw_count = 0
        self.info_count = 0
        self.raw_sub = self.create_subscription(RawImage, '/payload/image_raw', self.raw, 1)
        self.info_sub = self.create_subscription(CameraInfo, '/payload/camera_info', self.info, 1)
        self.result_sub = self.create_subscription(FileReady, '/payload/file_ready', self.result, 16)
        self.client = self.create_client(Capture, '/payload/capture_single')
        self.mode_client = self.create_client(SetMode, '/payload/set_mode')
        self.spacecraft_client = self.create_client(SpacecraftControl, '/payload/spacecraft_control')
        self.state = None
        self.state_sub = self.create_subscription(CameraStatus, '/payload/state', self.state_received,
            QoSProfile(depth=32, reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL))

    def state_received(self, message):
        self.state = message

    def raw(self, msg):
        assert msg.encoding == 'rgb8' and len(msg.data) == msg.width * msg.height * 3
        self.raw_count += 1

    def info(self, msg):
        assert msg.width > 0 and msg.height > 0 and msg.k[0] == 0
        self.info_count += 1

    def result(self, msg):
        self.results[msg.metadata.capture_id] = msg

    def wait(self, condition, timeout=25):
        deadline = time.monotonic() + timeout
        while not condition():
            if time.monotonic() > deadline:
                raise TimeoutError('ROS integration condition timed out')
            rclpy.spin_once(self, timeout_sec=0.05)

    def ready(self):
        self.wait(lambda: self.client.service_is_ready()
                  and self.count_subscribers('/payload/compressed') >= 1
                  and self.count_subscribers('/payload/capture') >= 1
                  and self.count_publishers('/payload/file_ready') >= 2)
        self.wait(lambda: self.mode_client.service_is_ready() and self.spacecraft_client.service_is_ready()
                  and self.state is not None and self.state.mode == CameraStatus.STANDBY
                  and self.state.lifecycle_state == 2)

    def enable_imaging(self):
        command = SpacecraftControl.Request()
        command.spacecraft_mode, command.imaging_permitted = 1, True
        future = self.spacecraft_client.call_async(command)
        self.wait(future.done)
        assert future.result().accepted, future.result().reason
        command = SetMode.Request()
        command.mode = CameraStatus.READY
        future = self.mode_client.call_async(command)
        self.wait(future.done)
        assert future.result().accepted, future.result().reason

    def call(self):
        future = self.client.call_async(Capture.Request())
        self.wait(future.done, 10)
        response = future.result()
        assert response is not None
        return response

    def capture(self):
        # The file result can reach this probe just before the camera itself.
        # A busy rejection has no side effect, so a bounded retry is safe here.
        deadline = time.monotonic() + 5
        while True:
            response = self.call()
            if response.accepted:
                break
            if 'busy' not in response.error or time.monotonic() > deadline:
                raise AssertionError(response.error)
            rclpy.spin_once(self, timeout_sec=0.1)
        self.wait(lambda: response.capture_id in self.results, 20)
        result = self.results[response.capture_id]
        assert result.success, result.error
        return result

    def configure(self, name, values):
        client = AsyncParameterClient(self, name)
        self.wait(lambda: client.services_are_ready())
        future = client.set_parameters([Parameter(key, value=value) for key, value in values.items()])
        self.wait(future.done)
        assert all(result.successful for result in future.result().results)


def start_pipeline(directory, log_name):
    log = (EVIDENCE / log_name).open('w')
    process = subprocess.Popen(['ros2', 'launch', 'payload_camera', 'pipeline.launch.py',
                                f'data_dir:={directory}'], cwd=ROOT, stdout=log,
                               stderr=subprocess.STDOUT, start_new_session=True)
    return process, log


def stop_pipeline(process, log):
    if process.poll() is None:
        # Launch forwards this signal to its children. Sending to the whole
        # group here would interrupt rclpy cleanup a second time.
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                raise RuntimeError('pipeline required SIGKILL to stop')
    log.close()
    if process.returncode != 0:
        raise RuntimeError(f'launch did not stop cleanly: {process.returncode}')
    if 'process has died' in Path(log.name).read_text():
        raise RuntimeError(f'payload child did not stop cleanly; see {log.name}')


def verify_files(directory, results):
    connection = sqlite3.connect(f'file:{directory}/catalogue.db?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute('SELECT * FROM images ORDER BY id').fetchall()
    connection.close()
    assert len(rows) == len(results), (len(rows), len(results))
    assert not list(directory.glob('*.part'))
    verified = []
    for row in rows:
        result = results[row['id']]
        path = directory / (row['id'] + '.jpg')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == row['sha256'] == result.sha256
        assert path.stat().st_size == row['size'] == result.metadata.compressed_bytes
        assert row['status'] == 'COMPLETE' and row['downlink_status'] == 'PENDING'
        metadata = json.loads(row['metadata'])
        assert metadata['capture_stamp_ns'] > 0 and metadata['spacecraft_time_us'] > 0
        assert metadata['quality'] == 75 and metadata['processing_duration_ms'] > 0
        assert metadata['storage_duration_ms'] > 0
        with Image.open(path) as image:
            image.load()  # Independent decoder, not merely JPEG marker checks.
            assert image.format == 'JPEG'
            assert image.size == (result.metadata.width, result.metadata.height)
            expected_mode = 'L' if result.metadata.encoding.startswith('mono8') else 'RGB'
            assert image.mode == expected_mode
            extrema = [image.getextrema()] if expected_mode == 'L' else image.getextrema()
            assert max(high - low for low, high in extrema) > 20
            verified.append({'id': row['id'], 'path': str(path.relative_to(ROOT)),
                             'sha256': digest, 'bytes': row['size'], 'width': image.width,
                             'height': image.height, 'mode': image.mode,
                             'capture_ms': result.metadata.capture_ms,
                             'processing_ms': result.metadata.processing_ms,
                             'storage_ms': result.storage_ms})
    return verified


def main():
    (EVIDENCE / 'phase2-result.json').unlink(missing_ok=True)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory = ROOT / 'data' / ('phase2_' + run_id)
    directory.mkdir(parents=True)
    rclpy.init()
    probe = Probe()
    process = log = None
    results = {}
    try:
        process, log = start_pipeline(directory, 'phase2-pipeline.log')
        probe.ready()
        probe.enable_imaging()
        assert process.poll() is None
        # Save actual ROS graph/interface inspection with the pipeline running.
        inspections = {
            'phase2-nodes.log': ['ros2', 'node', 'list', '--no-daemon'],
            'phase2-topics.log': ['ros2', 'topic', 'list', '-t'],
            'phase2-services.log': ['ros2', 'service', 'list', '-t'],
            'phase2-capture-interface.log': ['ros2', 'interface', 'show', 'payload_interfaces/srv/Capture'],
        }
        for name, command in inspections.items():
            with (EVIDENCE / name).open('w') as output:
                subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=15)
        for _ in range(5):
            begin = time.monotonic()
            result = probe.capture()
            results[result.metadata.capture_id] = result
            while time.monotonic() - begin < 1:
                rclpy.spin_once(probe, timeout_sec=0.05)
        probe.configure('/payload_camera', {'quality': 0})
        rejected = probe.call()
        assert not rejected.accepted and 'bounds' in rejected.error
        probe.configure('/payload_camera', {'quality': 75})
        probe.configure('/payload_processing', {'grayscale': True, 'crop_x': 10, 'crop_y': 20,
                        'crop_width': 320, 'crop_height': 240, 'resize_width': 160, 'resize_height': 120})
        transformed = probe.capture()
        assert (transformed.metadata.width, transformed.metadata.height) == (160, 120)
        results[transformed.metadata.capture_id] = transformed
        probe.wait(lambda: probe.raw_count >= 6 and probe.info_count >= 6)
        before = verify_files(directory, results)
        stop_pipeline(process, log)
        process = log = None
        # Wait for old DDS endpoints to leave before checking the restarted graph.
        probe.wait(lambda: not probe.client.service_is_ready())
        restart_started = time.monotonic()
        process, log = start_pipeline(directory, 'phase2-restart.log')
        probe.ready()
        restart_ms = (time.monotonic() - restart_started) * 1000
        assert process.poll() is None
        after = verify_files(directory, results)
        assert before == after
        report = {'result': 'PASS', 'run_id': run_id, 'directory': str(directory.relative_to(ROOT)),
                  'five_image_capture': True, 'transformed_capture': True,
                  'invalid_capture_rejected': True, 'restart_preserved': True,
                  'restart_ready_ms': restart_ms, 'raw_messages_observed': probe.raw_count,
                  'camera_info_observed': probe.info_count, 'images': after}
        (EVIDENCE / 'phase2-result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
    finally:
        if process:
            stop_pipeline(process, log)
        probe.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
