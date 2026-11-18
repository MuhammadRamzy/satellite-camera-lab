#!/usr/bin/env python3
"""ROS stays inside payload; bus sees only SPP and a separate file socket."""
import json
from pathlib import Path
import socket
import struct
import threading
import time
import uuid

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.parameter import Parameter
from rcl_interfaces.srv import SetParametersAtomically
from payload_interfaces.action import CaptureBurst
from payload_interfaces.msg import CameraStatus, Housekeeping, NodeHealth
from payload_interfaces.srv import Capture, CatalogueCommand, SetMode, SpacecraftControl
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy

from foundations.command_journal import Journal
from interfaces.framing import read_packet, read_frame
from interfaces.file_link import Link, send_file
from interfaces.space_packet import ACK_APID, Command, Packet, SequenceTracker, validate_command


class Gateway(Node):
    def __init__(self):
        super().__init__('payload_gateway')
        self.root = Path(self.declare_parameter('data_dir', 'data/payload').value).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.port = self.declare_parameter('command_port', 47001).value
        self.file_port = self.declare_parameter('file_port', 47002).value
        self.connected = False
        self.stopping = threading.Event()
        self.state = self.hk = None
        self.state_received = self.hk_received = 0.0
        self.jobs, self.goal, self.heartbeat_sequence = {}, None, 0
        qos = QoSProfile(depth=32, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.state_sub = self.create_subscription(CameraStatus, '/payload/state', self.got_state, qos)
        self.hk_sub = self.create_subscription(Housekeeping, '/payload/housekeeping', self.got_hk, 8)
        self.health = self.create_publisher(NodeHealth, '/payload/node_health', 16)
        self.timer = self.create_timer(1.0, self.heartbeat)
        self.mode = self.create_client(SetMode, '/payload/set_mode')
        self.spacecraft = self.create_client(SpacecraftControl, '/payload/spacecraft_control')
        self.capture = self.create_client(Capture, '/payload/capture_single')
        self.catalogue = self.create_client(CatalogueCommand, '/payload/catalogue')
        self.parameters = self.create_client(SetParametersAtomically, '/payload_camera/set_parameters_atomically')
        self.bursts = ActionClient(self, CaptureBurst, '/payload/capture_burst')
        self.threads = [threading.Thread(target=self.serve_commands, daemon=True),
                        threading.Thread(target=self.serve_files, daemon=True)]
        for thread in self.threads:
            thread.start()

    def got_state(self, message):
        self.state, self.state_received = message, time.monotonic()

    def got_hk(self, message):
        self.hk, self.hk_received = message, time.monotonic()

    def heartbeat(self):
        self.heartbeat_sequence += 1
        message = NodeHealth()
        message.node_name, message.sequence = 'payload_gateway', self.heartbeat_sequence
        message.healthy = True
        message.gateway_connected = self.connected
        self.health.publish(message)

    def await_future(self, future, timeout=4):
        event = threading.Event()
        future.add_done_callback(lambda _: event.set())
        if not event.wait(timeout):
            raise TimeoutError('ROS outcome uncertain; reconcile command journal')
        return future.result()

    def rpc(self, client, request):
        if not client.wait_for_service(timeout_sec=2):
            raise TimeoutError('ROS service unavailable')
        return self.await_future(client.call_async(request))

    @staticmethod
    def response(result):
        return {'status': 'COMPLETED' if result.accepted else 'ERROR', 'reason': result.reason}

    def cat(self, operation, image_id='', sha='', offset=0, limit=16):
        request = CatalogueCommand.Request()
        request.operation, request.image_id, request.sha256 = operation, image_id, sha
        request.offset, request.limit = offset, limit
        result = self.rpc(self.catalogue, request)
        return self.response(result) | (json.loads(result.result_json) if result.accepted else {})

    def status(self):
        now = time.monotonic()
        state, hk = self.state, self.hk
        return {'status': 'COMPLETED', 'mode': state.mode if state else 0,
                'state': state.operational_state if state else 'BOOT',
                'state_fresh': now - self.state_received < 1.5,
                'healthy': bool(hk and hk.nodes_healthy and now - self.hk_received < 2.5),
                'stored': hk.stored_images if hk else 0, 'pending': hk.pending_files if hk else 0,
                'heartbeat': hk.heartbeat_sequence if hk else 0,
                'gateway_connected': bool(hk and hk.gateway_connected),
                'cpu_percent': hk.cpu_percent if hk else 0,
                'memory_used_bytes': hk.memory_used_bytes if hk else 0,
                'jobs': dict(self.jobs)}

    def set_mode(self, mode, recover=False):
        request = SetMode.Request()
        request.mode, request.recover = mode, recover
        result = self.rpc(self.mode, request)
        if result.accepted:
            self.got_state(result.status)
        return self.response(result)

    def safe(self):
        request = SpacecraftControl.Request()
        request.spacecraft_mode, request.imaging_permitted = 2, False
        return self.response(self.rpc(self.spacecraft, request))

    def dispatch(self, packet):
        cmd, body = Command(packet.opcode), packet.payload
        if cmd in (Command.PL_PING, Command.PL_GET_STATUS):
            return self.status()
        if cmd == Command.PL_SPACECRAFT_CONTROL:
            request = SpacecraftControl.Request()
            request.spacecraft_mode, permitted = body
            request.imaging_permitted = bool(permitted)
            return self.response(self.rpc(self.spacecraft, request))
        if cmd == Command.PL_SET_MODE:
            return self.set_mode(body[0])
        if cmd == Command.PL_RECOVER:
            return self.set_mode(1, True)
        if cmd == Command.PL_SAFE:
            return self.safe()
        if cmd == Command.PL_CAPTURE:
            result = self.rpc(self.capture, Capture.Request())
            return {'status': 'ACCEPTED' if result.accepted else 'ERROR',
                    'capture_id': result.capture_id, 'reason': result.error}
        if cmd == Command.PL_CAPTURE_BURST:
            if not self.bursts.wait_for_server(timeout_sec=2):
                return {'status': 'ERROR', 'reason': 'action server unavailable'}
            goal = CaptureBurst.Goal()
            goal.count, goal.interval_ms = struct.unpack('!HI', body)
            key = str(packet.command_id)
            # Bound telemetry even during a long-running operator session.
            if len(self.jobs) >= 8:
                oldest = next((k for k, v in self.jobs.items() if v['status'] != 'ACCEPTED'), None)
                if oldest is None:
                    return {'status': 'ERROR', 'reason': 'job telemetry full'}
                del self.jobs[oldest]
            self.jobs[key] = {'completed': 0, 'requested': goal.count, 'status': 'ACCEPTED'}

            def feedback(message):
                self.jobs[key] = self.jobs[key] | {'completed': message.feedback.completed,
                    'last_capture_id': message.feedback.last_capture_id}

            handle = self.await_future(self.bursts.send_goal_async(goal, feedback_callback=feedback))
            if not handle.accepted:
                self.jobs[key] = self.jobs[key] | {'status': 'ERROR'}
                return {'status': 'ERROR', 'reason': 'burst rejected'}
            self.goal = handle

            def finished(future):
                result = future.result()
                self.jobs[key] = self.jobs[key] | {'completed': result.result.completed,
                    'status': 'COMPLETED' if result.result.success else 'ABORTED',
                    'action_status': result.status, 'reason': result.result.reason}

            handle.get_result_async().add_done_callback(finished)
            return {'status': 'ACCEPTED', 'job': key}
        if cmd == Command.PL_ABORT_CAPTURE:
            if self.goal is None:
                return {'status': 'ERROR', 'reason': 'no burst goal'}
            result = self.await_future(self.goal.cancel_goal_async())
            return {'status': 'ACCEPTED' if result.goals_canceling else 'ERROR', 'reason': 'cancel requested'}
        if cmd == Command.PL_SET_CAMERA_CONFIG:
            width, height, fps, quality, exposure, noise, source = struct.unpack('!HHBBIBB', body)
            if source != 0:
                return {'status': 'ERROR', 'reason': 'only synthetic camera source is supported'}
            if self.state is None or self.state.mode != 1 or time.monotonic() - self.state_received > 1.5:
                return {'status': 'ERROR', 'reason': 'configuration requires fresh STANDBY'}
            request = SetParametersAtomically.Request()
            values = {'width': width, 'height': height, 'quality': quality,
                      'exposure_us': exposure, 'noise_sigma': float(noise)}
            request.parameters = [Parameter(k, value=v).to_parameter_msg() for k, v in values.items()]
            result = self.rpc(self.parameters, request).result
            return {'status': 'COMPLETED' if result.successful else 'ERROR', 'reason': result.reason,
                    'requested_fps': fps, 'acquisition': 'commanded burst interval'}
        if cmd == Command.PL_LIST_FILES:
            offset, limit = struct.unpack('!IH', body)
            return self.cat(1, offset=offset, limit=limit)
        image_id = str(uuid.UUID(bytes=body[:16]))
        if cmd == Command.PL_REQUEST_FILE:
            return self.cat(2, image_id) | {'file_port': self.file_port}
        if cmd == Command.PL_DELETE_FILE:
            return self.cat(4, image_id)
        if cmd == Command.PL_CONFIRM_FILE:
            return self.cat(3, image_id, body[16:].hex())
        return {'status': 'ERROR', 'reason': 'unsupported command'}

    def listener(self, port):
        listener = socket.socket()
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))
        listener.listen(4)
        listener.settimeout(0.5)
        return listener

    def serve_commands(self):
        journal = Journal(self.root / 'commands.db')
        try:
            with self.listener(self.port) as listener:
                while not self.stopping.is_set():
                    try:
                        peer, _ = listener.accept()
                    except socket.timeout:
                        continue
                    self.connected = True
                    tracker, sequence = SequenceTracker(), 0
                    with peer:
                        peer.settimeout(3)
                        try:
                            while not self.stopping.is_set():
                                packet = read_packet(peer)
                                validate_command(packet)
                                tracker.accept(packet)
                                try:
                                    response = journal.execute(packet, self.dispatch)
                                except TimeoutError as error:
                                    response = {'status': 'IN_DOUBT', 'reason': str(error)}
                                raw = Packet(ACK_APID, sequence, False, packet.opcode, packet.command_id,
                                             time.time_ns() // 1000,
                                             json.dumps(response, separators=(',', ':')).encode()).encode()
                                peer.sendall(raw)
                                sequence = (sequence + 1) % 16384
                        except (OSError, EOFError, ValueError) as error:
                            self.get_logger().info(json.dumps({'event': 'command_session_closed', 'reason': str(error)[:256]}))
                        finally:
                            self.connected = False
                            try:
                                self.safe()  # Loss of bus authority revokes imaging.
                            except Exception as error:
                                self.get_logger().error(f'disconnect SAFE failed: {error}')
        finally:
            journal.close()

    def serve_files(self):
        with self.listener(self.file_port) as listener:
            while not self.stopping.is_set():
                try:
                    peer, _ = listener.accept()
                except socket.timeout:
                    continue
                with peer:
                    peer.settimeout(5)
                    try:
                        request, _ = read_frame(peer)
                        if request.get('type') != 'GET':
                            raise ValueError('GET required')
                        manifest = self.cat(2, request['id'])
                        if manifest['status'] != 'COMPLETED':
                            raise ValueError(manifest['reason'])
                        send_file(peer, self.root / (manifest['id'] + '.jpg'), manifest, Link())
                    except (OSError, EOFError, ValueError, KeyError, TypeError, AttributeError) as error:
                        self.get_logger().info(f'file session closed: {error}')

    def destroy_node(self):
        self.stopping.set()
        for thread in self.threads:
            thread.join(timeout=0.7)
        return super().destroy_node()


def main():
    rclpy.init()
    node = Gateway()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
