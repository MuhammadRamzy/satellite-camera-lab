#!/usr/bin/env python3
"""Executable Phase 3 control, lifecycle, action, health and recovery acceptance."""
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import signal
import sqlite3
import subprocess
import time

from PIL import Image
import rclpy
from rclpy.action import ActionClient
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from action_msgs.msg import GoalStatus
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState, GetState
from payload_interfaces.action import CaptureBurst
from payload_interfaces.msg import Authority, CameraStatus, Housekeeping, NodeHealth
from payload_interfaces.srv import SetMode, SpacecraftControl
from verify_phase3_helpers import load_phase2

phase2 = load_phase2()
EVIDENCE = Path('/workspace/docs/evidence/phase3')
ROOT = Path('/workspace')


class Probe(phase2.Probe):
    def __init__(self):
        super().__init__()
        self.camera = None
        self.states = []
        self.telemetry = []
        self.health = {}
        self.raw_stamps = []
        self.feedback = []
        qos = QoSProfile(depth=32, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.camera_sub = self.create_subscription(CameraStatus, '/payload/camera_status', self.camera_received, qos)
        self.hk_sub = self.create_subscription(Housekeeping, '/payload/housekeeping', self.hk_received, 8)
        self.health_sub = self.create_subscription(NodeHealth, '/payload/node_health', self.health_received, 16)
        self.bursts = ActionClient(self, CaptureBurst, '/payload/capture_burst')
        self.change_client = self.create_client(ChangeState, '/payload_camera/change_state')
        self.get_client = self.create_client(GetState, '/payload_camera/get_state')

    def camera_received(self, msg):
        self.camera = msg
        self.states.append({'received_mono': time.monotonic(), 'boot_id': msg.boot_id,
                            'sequence': msg.sequence, 'mode': msg.mode, 'state': msg.operational_state,
                            'in_flight': msg.in_flight, 'burst_active': msg.burst_active,
                            'completed': msg.burst_completed, 'lifecycle': msg.lifecycle_state,
                            'imaging_enabled': msg.imaging_enabled, 'reason': msg.reason})

    def hk_received(self, msg):
        self.telemetry.append((time.monotonic(), msg))

    def health_received(self, msg):
        self.health[msg.node_name] = (time.monotonic(), msg)

    def raw(self, msg):
        super().raw(msg)
        self.raw_stamps.append(msg.header.stamp.sec * 1000000000 + msg.header.stamp.nanosec)

    def rpc(self, client, request, timeout=8):
        self.wait(lambda: client.service_is_ready())
        future = client.call_async(request)
        self.wait(future.done, timeout)
        result = future.result()
        assert result is not None
        return result

    def mode(self, target, recover=False, accepted=True):
        request = SetMode.Request()
        request.mode, request.recover = target, recover
        response = self.rpc(self.mode_client, request)
        assert response.accepted == accepted, response.reason
        return response

    def spacecraft(self, mode, imaging, accepted=True):
        request = SpacecraftControl.Request()
        request.spacecraft_mode, request.imaging_permitted = mode, imaging
        response = self.rpc(self.spacecraft_client, request)
        assert response.accepted == accepted, response.reason
        return response

    def recover(self):
        self.spacecraft(1, True)
        self.wait(lambda: self.camera is not None and not self.camera.burst_active and not self.camera.in_flight)
        self.wait(lambda: all(name in self.health and self.health[name][1].healthy and
                  time.monotonic() - self.health[name][0] < 1.5
                  for name in ('payload_processing', 'payload_storage', 'payload_health')))
        response = self.mode(CameraStatus.STANDBY, recover=True)
        assert response.status.mode == CameraStatus.STANDBY
        response = self.mode(CameraStatus.READY)
        assert response.status.mode == CameraStatus.READY and response.status.lifecycle_state == 3
        self.wait(lambda: self.camera.mode == CameraStatus.READY and self.camera.imaging_enabled)

    def spin_for(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until:
            rclpy.spin_once(self, timeout_sec=0.05)

    def burst(self, count, interval_ms, accepted=True):
        self.wait(lambda: self.bursts.server_is_ready())
        goal = CaptureBurst.Goal()
        goal.count, goal.interval_ms = count, interval_ms
        feedback = []

        def progress(message):
            item = {'completed': message.feedback.completed, 'requested': message.feedback.requested,
                    'last_capture_id': message.feedback.last_capture_id, 'received_mono': time.monotonic()}
            feedback.append(item)
            self.feedback.append(item)

        future = self.bursts.send_goal_async(goal, feedback_callback=progress)
        self.wait(future.done)
        handle = future.result()
        assert handle.accepted == accepted
        return handle, feedback

    def burst_result(self, handle, status, timeout=15):
        future = handle.get_result_async()
        self.wait(future.done, timeout)
        response = future.result()
        assert response.status == status, (response.status, response.result.reason)
        self.wait(lambda: self.camera is not None and not self.camera.burst_active)
        return response.result

    def lifecycle(self, transition, success=True):
        request = ChangeState.Request()
        request.transition.id = transition
        response = self.rpc(self.change_client, request)
        assert response.success == success
        return self.rpc(self.get_client, GetState.Request()).current_state.id


def write_cases(cases):
    (EVIDENCE / 'cases.json').write_text(json.dumps(cases, indent=2) + '\n')
    fields = ['test_id', 'initial_state', 'injected_fault', 'expected', 'observed',
              'recovery_action', 'recovery_ms', 'duration_ms', 'result']
    with (EVIDENCE / 'cases.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fields)
        writer.writeheader()
        writer.writerows(cases)


def owned_node_pid(launch_process, log, executable):
    text = Path(log.name).read_text()
    matches = re.findall(r'\[' + re.escape(executable) + r'-\d+\]: process started with pid \[(\d+)\]', text)
    assert len(matches) == 1, (executable, matches)
    pid = int(matches[0])
    package = {'modes_node': 'payload_modes', 'processing_node': 'payload_processing'}[executable]
    expected = f'/workspace/install/{package}/lib/{package}/{executable}'
    assert expected in Path(f'/proc/{pid}/cmdline').read_bytes().decode().split('\0')
    ancestor = pid
    for _ in range(16):
        if ancestor == launch_process.pid:
            return pid
        status = Path(f'/proc/{ancestor}/status').read_text()
        ancestor = int(next(line.split()[1] for line in status.splitlines() if line.startswith('PPid:')))
        assert ancestor > 1
    raise AssertionError('refusing to signal a process outside this test launch')


def verify_images(directory, probe):
    connection = sqlite3.connect(f'file:{directory}/catalogue.db?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute('SELECT * FROM images ORDER BY id').fetchall()
    connection.close()
    assert rows and not list(directory.glob('*.part'))
    result = []
    for row in rows:
        assert row['status'] == 'COMPLETE' and row['downlink_status'] == 'PENDING'
        path = directory / (row['id'] + '.jpg')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == row['sha256']
        if row['id'] in probe.results:
            assert probe.results[row['id']].success and probe.results[row['id']].sha256 == digest
        with Image.open(path) as image:
            image.load()
            assert image.size == (640, 480) and image.mode == 'RGB'
        result.append({'id': row['id'], 'sha256': digest, 'bytes': path.stat().st_size,
                       'path': str(path.relative_to(ROOT))})
    return result


def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / 'result.json').unlink(missing_ok=True)
    directory = ROOT / 'data' / ('phase3_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    directory.mkdir(parents=True)
    cases, paused = [], set()
    process = log = None
    rclpy.init()
    probe = Probe()

    def case(test_id, initial, fault, expected, recovery, function):
        started = time.monotonic()
        record = dict(test_id=test_id, initial_state=initial, injected_fault=fault, expected=expected,
                      recovery_action=recovery, recovery_ms=None)
        try:
            observed = function()
            record['observed'] = observed.pop('observed')
            record['recovery_ms'] = observed.get('recovery_ms')
            record['result'] = 'PASS'
        except Exception as error:
            record.update(observed=f'{type(error).__name__}: {error}', result='FAIL')
            raise
        finally:
            record['duration_ms'] = (time.monotonic() - started) * 1000
            cases.append(record)
            write_cases(cases)
            print(json.dumps(record), flush=True)

    try:
        process, log = phase2.start_pipeline(directory, 'phase3/pipeline.log')
        probe.ready()

        def startup():
            assert probe.state.mode == CameraStatus.STANDBY and probe.state.lifecycle_state == 2
            assert any(entry['state'] == 'BOOT' for entry in probe.states)
            assert not probe.call().accepted
            probe.mode(CameraStatus.READY, accepted=False)
            probe.spacecraft(2, True, accepted=False)
            probe.mode(CameraStatus.FAULT, accepted=False)
            probe.burst(5, 100, accepted=False)
            probe.spacecraft(1, True)
            assert probe.state.mode == CameraStatus.STANDBY
            # Direct lifecycle activation cannot bypass the payload READY guard.
            assert probe.lifecycle(Transition.TRANSITION_ACTIVATE, success=False) == 2
            probe.mode(CameraStatus.READY)
            probe.burst(0, 100, accepted=False)
            probe.burst(101, 100, accepted=False)
            probe.burst(1, 99, accepted=False)
            return {'observed': 'STANDBY/inactive boot; inhibited/invalid requests rejected; explicit permission and READY activate camera'}

        case('P3-01', 'BOOT -> STANDBY', 'invalid requests and premature imaging',
             'no imaging before spacecraft permission and READY', 'enable permission, command READY', startup)

        for name, command in {
            'nodes.log': ['ros2', 'node', 'list', '--no-daemon'],
            'topics.log': ['ros2', 'topic', 'list', '-t'],
            'services.log': ['ros2', 'service', 'list', '-t'],
            'actions.log': ['ros2', 'action', 'list', '-t'],
            'lifecycle.log': ['ros2', 'lifecycle', 'get', '/payload_camera'],
            'burst-interface.log': ['ros2', 'interface', 'show', 'payload_interfaces/action/CaptureBurst'],
        }.items():
            with (EVIDENCE / name).open('w') as output:
                subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=15)

        def burst_complete():
            before = len(probe.results)
            goal, feedback = probe.burst(5, 1000)
            # Reject competing operations during the action, including between frames.
            probe.mode(CameraStatus.DOWNLINK, accepted=False)
            assert not probe.call().accepted
            result = probe.burst_result(goal, GoalStatus.STATUS_SUCCEEDED)
            probe.wait(lambda: len(feedback) == 5)
            assert result.success and result.completed == 5 and len(probe.results) == before + 5
            assert [item['completed'] for item in feedback] == [1, 2, 3, 4, 5]
            assert all(probe.results[item['last_capture_id']].success for item in feedback)
            assert {'CAPTURING', 'PROCESSING', 'STORING', 'READY'} <= {entry['state'] for entry in probe.states}
            return {'observed': '5/5 durable completions, ordered feedback 1..5, all operational stages and busy guards observed'}

        case('P3-02', 'READY', 'concurrent capture/downlink during five-image burst',
             'five stored images with progress; conflicting operations rejected', 'burst succeeds to READY', burst_complete)
        probe.configure('/payload_storage', {'simulated_write_delay_s': 0.3})

        def cancel():
            before = len(probe.results)
            goal, feedback = probe.burst(20, 100)
            probe.wait(lambda: probe.camera.operational_state == 'STORING' and bool(probe.camera.in_flight))
            future = goal.cancel_goal_async()
            probe.wait(future.done)
            assert future.result().goals_canceling
            cancel_ack = time.time_ns()
            result = probe.burst_result(goal, GoalStatus.STATUS_CANCELED)
            probe.spin_for(0.4)
            assert not result.success and 1 <= result.completed < 20
            assert len(probe.results) - before == result.completed
            assert not any(stamp > cancel_ack for stamp in probe.raw_stamps)
            return {'observed': f'cancelled with {result.completed} durable image(s); in-flight write drained; no post-ACK exposures'}

        case('P3-03', 'burst STORING', 'operator cancellation during simulated 0.3 s write',
             'stop new exposures, finish existing write, terminal CANCELED', 'cancel action; remain READY', cancel)

        def safe():
            goal, _ = probe.burst(20, 100)
            probe.wait(lambda: probe.camera.operational_state == 'STORING' and bool(probe.camera.in_flight))
            response = probe.spacecraft(2, False)
            safe_ack = time.time_ns()
            assert response.status.mode == CameraStatus.SAFE and response.status.lifecycle_state == 2
            result = probe.burst_result(goal, GoalStatus.STATUS_ABORTED)
            probe.spin_for(0.4)
            assert not result.success and 1 <= result.completed < 20
            assert not probe.call().accepted
            assert not any(stamp > safe_ack for stamp in probe.raw_stamps)
            probe.mode(CameraStatus.STANDBY, recover=True, accepted=False)
            probe.mode(CameraStatus.READY, accepted=False)
            resumed = time.monotonic()
            probe.spacecraft(1, True)
            probe.mode(CameraStatus.READY, accepted=False)  # SAFE is latched after bus permission returns.
            probe.recover()
            return {'observed': f'SAFE/inactive ACK, burst aborted after {result.completed} durable image(s), local override rejected',
                    'recovery_ms': (time.monotonic() - resumed) * 1000}

        case('P3-04', 'burst STORING', 'spacecraft SAFE command',
             'immediate exposure inhibit, action ABORTED, explicit recovery required', 'spacecraft OPERATIONAL then recover STANDBY then READY', safe)
        probe.configure('/payload_storage', {'simulated_write_delay_s': 0.0})

        def paused_node(executable, expected_reason, stale_name):
            goal, feedback = probe.burst(30, 200)
            probe.wait(lambda: len(feedback) >= 1)
            pid = owned_node_pid(process, log, executable)
            os.kill(pid, signal.SIGSTOP)
            paused.add(pid)
            fault_started = time.monotonic()
            try:
                probe.wait(lambda: probe.camera.mode == CameraStatus.FAULT, timeout=6)
                detected_ms = (time.monotonic() - fault_started) * 1000
                if expected_reason:
                    assert expected_reason in probe.camera.reason, probe.camera.reason
                probe.wait(lambda: bool(probe.telemetry) and stale_name in probe.telemetry[-1][1].stale_nodes, timeout=6)
                raw_at_fault = len(probe.raw_stamps)
                probe.spin_for(0.4)
                assert len(probe.raw_stamps) == raw_at_fault
            finally:
                os.kill(pid, signal.SIGCONT)
                paused.remove(pid)
            recovered = time.monotonic()
            result = probe.burst_result(goal, GoalStatus.STATUS_ABORTED)
            assert not result.success and 1 <= result.completed < 30
            probe.recover()
            return {'observed': f'{executable} paused; FAULT in {detected_ms:.1f} ms, {stale_name} stale, burst aborted at {result.completed}/30; no exposures after fault',
                    'recovery_ms': (time.monotonic() - recovered) * 1000}

        case('P3-05', 'READY/burst', 'SIGSTOP mode manager',
             'camera lease expires independently; FAULT and no new imaging', 'SIGCONT then explicit recovery',
             lambda: paused_node('modes_node', 'authority lease expired', 'payload_modes'))
        case('P3-06', 'READY/burst', 'SIGSTOP processing node',
             'supervisor detects missing heartbeat, inhibits camera and drains after resume', 'SIGCONT then explicit recovery',
             lambda: paused_node('processing_node', None, 'payload_processing'))

        def lifecycle():
            probe.mode(CameraStatus.DOWNLINK)
            assert not probe.call().accepted
            assert probe.rpc(probe.get_client, GetState.Request()).current_state.id == 2
            probe.mode(CameraStatus.STANDBY)
            assert probe.lifecycle(Transition.TRANSITION_CLEANUP) == 1
            probe.wait(lambda: probe.camera.mode == CameraStatus.BOOT)
            probe.mode(CameraStatus.READY, accepted=False)
            probe.mode(CameraStatus.STANDBY)
            probe.mode(CameraStatus.READY)
            assert probe.lifecycle(Transition.TRANSITION_DEACTIVATE) == 2
            probe.wait(lambda: probe.camera.mode == CameraStatus.SAFE)
            probe.recover()
            return {'observed': 'DOWNLINK inhibited; cleanup->unconfigured; configure->inactive; activate->active; direct deactivation->SAFE; recovered'}

        case('P3-07', 'READY', 'downlink, cleanup and external lifecycle deactivation',
             'lifecycle guards hold and imaging never bypasses READY', 'configure and recover READY', lifecycle)

        def housekeeping():
            expected = len(verify_images(directory, probe))
            # Recovery is asynchronous and heartbeat counters refresh at 1 Hz.
            # First establish a healthy, count-consistent steady-state baseline;
            # retain earlier fault/recovery samples in the exported CSV.
            probe.wait(lambda: bool(probe.telemetry) and probe.telemetry[-1][1].nodes_healthy
                       and not probe.telemetry[-1][1].stale_nodes
                       and probe.telemetry[-1][1].payload_mode == CameraStatus.READY
                       and probe.telemetry[-1][1].stored_images == expected
                       and probe.telemetry[-1][1].pending_files == expected, timeout=5)
            initial = len(probe.telemetry)
            probe.wait(lambda: len(probe.telemetry) >= initial + 6, timeout=8)
            recent = probe.telemetry[initial:]
            intervals = [b[0] - a[0] for a, b in zip(recent, recent[1:])]
            assert all(0.7 < value < 1.3 for value in intervals), intervals
            assert all(b[1].heartbeat_sequence == a[1].heartbeat_sequence + 1 for a, b in zip(recent, recent[1:]))
            assert len({item[1].boot_id for item in recent}) == 1
            for _, msg in recent:
                assert msg.nodes_healthy and msg.camera_healthy and not msg.stale_nodes
                assert msg.payload_mode == CameraStatus.READY
                assert math.isfinite(msg.cpu_percent) and 0 <= msg.cpu_percent <= 100
                assert 0 < msg.memory_used_bytes <= msg.memory_total_bytes <= 4294967296
                assert 0 <= msg.disk_used_bytes <= msg.disk_total_bytes
                assert msg.resource_scope == 'cgroup'
                assert not msg.gateway_connected
                assert msg.stored_images == expected and msg.pending_files == expected
            return {'observed': f'{len(recent)} consecutive healthy 1 Hz samples; interval range {min(intervals):.3f}..{max(intervals):.3f} s; {expected} stored/pending; gateway correctly disconnected'}

        case('P3-08', 'healthy READY', 'none; timed telemetry validation',
             '1 Hz finite resources, contiguous sequence, accurate counts and node/gateway status', 'none', housekeeping)
        before = verify_images(directory, probe)

        def restart():
            nonlocal process, log
            probe.mode(CameraStatus.STANDBY)
            phase2.stop_pipeline(process, log)
            process = log = None
            probe.wait(lambda: not probe.client.service_is_ready())
            restarted = time.monotonic()
            process, log = phase2.start_pipeline(directory, 'phase3/restart.log')
            probe.ready()
            assert not probe.call().accepted
            probe.mode(CameraStatus.READY, accepted=False)
            assert before == verify_images(directory, probe)
            return {'observed': f'{len(before)} verified JPEGs/catalogue entries persisted; restart STANDBY/inactive and imaging inhibited',
                    'recovery_ms': (time.monotonic() - restarted) * 1000}

        case('P3-09', 'STANDBY with completed files', 'restart all five payload processes',
             'preserve files and restart inhibited, not READY', 'relaunch same storage', restart)
        report = {'result': 'PASS', 'directory': str(directory.relative_to(ROOT)), 'cases': cases,
                  'images': before, 'housekeeping_samples': len(probe.telemetry),
                  'base_image': json.loads((ROOT / 'config/image-lock.json').read_text())}
        (EVIDENCE / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'result': 'PASS', 'cases': len(cases), 'images': len(before), 'directory': report['directory']}), flush=True)
    finally:
        for pid in paused:
            os.kill(pid, signal.SIGCONT)
        if process:
            phase2.stop_pipeline(process, log)
        fields = ['received_mono', 'utc_ns', 'boot_id', 'sequence', 'cpu_percent', 'memory_used_bytes',
                  'memory_total_bytes', 'disk_used_bytes', 'disk_total_bytes', 'scope', 'mode', 'state',
                  'stored_images', 'pending_files', 'gateway_connected', 'nodes_healthy', 'stale_nodes']
        with (EVIDENCE / 'housekeeping.csv').open('w', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(fields)
            for received, msg in probe.telemetry:
                writer.writerow([received, msg.utc.sec * 1000000000 + msg.utc.nanosec, msg.boot_id,
                    msg.heartbeat_sequence, msg.cpu_percent, msg.memory_used_bytes, msg.memory_total_bytes,
                    msg.disk_used_bytes, msg.disk_total_bytes, msg.resource_scope, msg.payload_mode,
                    msg.payload_state, msg.stored_images, msg.pending_files, msg.gateway_connected,
                    msg.nodes_healthy, '|'.join(msg.stale_nodes)])
        (EVIDENCE / 'states.json').write_text(json.dumps(probe.states, indent=2) + '\n')
        (EVIDENCE / 'feedback.json').write_text(json.dumps(probe.feedback, indent=2) + '\n')
        probe.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
