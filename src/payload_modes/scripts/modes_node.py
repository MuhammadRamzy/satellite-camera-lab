#!/usr/bin/env python3
"""Spacecraft authority boundary and asynchronous lifecycle/mode orchestration."""
import copy
import json
import time
import uuid

import rclpy
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.clock import Clock, ClockType
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from rclpy.task import Future
from payload_interfaces.msg import Authority, CameraStatus, NodeHealth
from payload_interfaces.srv import ApplyControl, SetMode, SpacecraftControl
from foundations.control import SpacecraftAuthority, BOOT, STANDBY, READY, SAFE, FAULT, SC_SAFE

PIPELINE_NODES = ('payload_processing', 'payload_storage', 'payload_health')


class Modes(Node):
    def __init__(self):
        super().__init__('payload_modes')
        self.io = ReentrantCallbackGroup()
        self.wall = Clock(clock_type=ClockType.STEADY_TIME)
        self.boot_id = str(uuid.uuid4())
        self.authority = SpacecraftAuthority()
        self.sequence = self.heartbeat_sequence = 0
        self.camera = CameraStatus()
        self.camera.operational_state = 'BOOT'
        self.last_camera = None
        self.started = time.monotonic()
        self.health = {}
        self.bootstrapped = self.bootstrap_pending = self.fault_latched = self.recovering = False
        self.fault_reason = ''
        self.node_timeout = self.declare_parameter('node_timeout_s', 2.5).value
        self.bus_required = self.declare_parameter('bus_required', False).value
        self.camera_timeout = self.declare_parameter('camera_timeout_s', 1.2).value
        if self.node_timeout < 1.5 or self.camera_timeout < 0.5:
            raise ValueError('supervisor timeout too short')
        self.authority_pub = self.create_publisher(Authority, '/payload/authority', 1)
        state_qos = QoSProfile(depth=32, reliability=ReliabilityPolicy.RELIABLE,
                               durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.state_pub = self.create_publisher(CameraStatus, '/payload/state', state_qos)
        self.health_pub = self.create_publisher(NodeHealth, '/payload/node_health', 16)
        self.camera_sub = self.create_subscription(CameraStatus, '/payload/camera_status',
                                                   self.camera_status, state_qos, callback_group=self.io)
        self.health_sub = self.create_subscription(NodeHealth, '/payload/node_health',
                                                   self.node_health, 16, callback_group=self.io)
        self.apply = self.create_client(ApplyControl, '/payload/apply_control', callback_group=self.io)
        # Default mutually-exclusive group serializes operator/bus changes;
        # IO callbacks remain runnable while these async handlers await replies.
        self.mode_service = self.create_service(SetMode, '/payload/set_mode', self.set_mode)
        self.sc_service = self.create_service(SpacecraftControl, '/payload/spacecraft_control', self.spacecraft)
        self.timer = self.create_timer(0.25, self.tick, callback_group=self.io, clock=self.wall)
        self.health_timer = self.create_timer(1.0, self.heartbeat, callback_group=self.io, clock=self.wall)

    def camera_status(self, message):
        if message.boot_id == self.camera.boot_id and message.sequence <= self.camera.sequence:
            return
        self.camera = message
        self.last_camera = time.monotonic()
        if message.mode == FAULT and not self.recovering:
            self.latch_fault(message.reason or 'camera fault')
        self.publish_state()

    def node_health(self, message):
        if message.node_name in PIPELINE_NODES or message.node_name == 'payload_gateway':
            self.health[message.node_name] = (time.monotonic(), message)

    def pipeline_healthy(self):
        now = time.monotonic()
        nodes = PIPELINE_NODES + (('payload_gateway',) if self.bus_required else ())
        return (all(name in self.health and now - self.health[name][0] < self.node_timeout
                    and self.health[name][1].healthy for name in nodes)
                and (not self.bus_required or self.health['payload_gateway'][1].gateway_connected))

    def latch_fault(self, reason):
        if not self.fault_latched:
            self.get_logger().error(json.dumps({'event': 'supervisor_fault', 'reason': reason}))
        self.fault_latched = True
        self.fault_reason = reason

    def publish_authority(self):
        self.sequence += 1
        message = Authority()
        message.boot_id = self.boot_id
        message.sequence = self.sequence
        message.imaging_permitted = self.authority.may_image and not self.fault_latched and not self.recovering
        message.force_safe = self.authority.mode == SC_SAFE
        message.fault = self.fault_latched and not self.recovering
        self.authority_pub.publish(message)
        return self.sequence

    def publish_state(self):
        message = copy.deepcopy(self.camera)
        if self.fault_latched:
            message.mode = FAULT
            message.operational_state = 'FAULT'
            message.imaging_enabled = False
            message.reason = self.fault_reason[:256]
        self.state_pub.publish(message)
        return message

    def request(self, mode, recover=False):
        if not self.apply.service_is_ready():
            raise RuntimeError('camera control service unavailable')
        request = ApplyControl.Request()
        request.authority_id = self.boot_id
        request.authority_sequence = self.publish_authority()
        request.mode = mode
        request.recover = recover
        inner = self.apply.call_async(request)
        outer = Future(executor=self.executor)

        def expired():
            if not outer.done():
                outer.set_exception(TimeoutError('camera control acknowledgement timed out'))
                inner.cancel()
            timer.cancel()

        timer = self.create_timer(2.0, expired, callback_group=self.io, clock=self.wall)

        def complete(future):
            timer.cancel()
            self.destroy_timer(timer)
            if outer.done():
                return
            try:
                outer.set_result(future.result())
            except Exception as error:
                outer.set_exception(error)

        inner.add_done_callback(complete)
        return outer

    async def command(self, mode, recover=False):
        # A service request can overtake its DDS lease message. Retry only that
        # explicit no-side-effect rejection, never an unknown command outcome.
        for _ in range(4):
            response = await self.request(mode, recover)
            if 'sequence boundary' not in response.reason and 'fresh authority lease' not in response.reason:
                return response
        return response

    async def set_mode(self, request, response):
        recovering = False
        try:
            self.authority.guard_payload(request.mode)
            if request.mode == READY and (self.fault_latched or not self.pipeline_healthy()):
                raise ValueError('recover fault and restore healthy processing/storage before READY')
            if request.recover:
                if request.mode != STANDBY or not self.pipeline_healthy():
                    raise ValueError('recovery requires STANDBY and fresh healthy processing/storage')
                self.recovering = recovering = True
            result = await self.command(request.mode, request.recover)
            response.accepted, response.reason = result.accepted, result.reason
            if result.accepted:
                if recovering:
                    self.fault_latched = False
                    self.fault_reason = ''
                self.bootstrapped = True
                self.camera_status(result.status)
        except Exception as error:
            response.reason = str(error)[:256]
        finally:
            self.recovering = False
            self.publish_authority()
        response.status = self.publish_state()
        return response

    async def spacecraft(self, request, response):
        try:
            self.authority.update(request.spacecraft_mode, request.imaging_permitted)
            self.publish_authority()  # Revoke exposure permission before awaiting an ACK.
            if request.spacecraft_mode == SC_SAFE or (not self.authority.may_image and self.camera.mode == READY):
                result = await self.command(SAFE)
                response.accepted, response.reason = result.accepted, result.reason
                if result.accepted:
                    self.camera_status(result.status)
            else:
                response.accepted = True  # Enabling permission never commands READY.
        except Exception as error:
            response.reason = str(error)[:256]
        response.status = self.publish_state()
        return response

    def tick(self):
        now = time.monotonic()
        if not self.recovering:
            if self.last_camera is not None and now - self.last_camera > self.camera_timeout:
                self.latch_fault('camera heartbeat missed')
            elif self.last_camera is None and now - self.started > 5:
                self.latch_fault('camera startup timed out')
            if self.camera.mode == READY and not self.pipeline_healthy():
                self.latch_fault('processing/storage heartbeat missed or unhealthy')
        self.publish_authority()
        self.publish_state()
        if (not self.bootstrapped and not self.bootstrap_pending and not self.fault_latched
                and self.pipeline_healthy() and self.apply.service_is_ready()):
            self.bootstrap_pending = True
            try:
                future = self.request(STANDBY)
            except Exception as error:
                self.bootstrap_pending = False
                self.latch_fault(str(error))
                return

            def boot_finished(done):
                self.bootstrap_pending = False
                try:
                    result = done.result()
                    if result.accepted:
                        self.bootstrapped = True
                        self.camera_status(result.status)
                    elif 'authority' not in result.reason:
                        self.latch_fault(result.reason)
                except Exception as error:
                    self.latch_fault(str(error))

            future.add_done_callback(boot_finished)

    def heartbeat(self):
        self.heartbeat_sequence += 1
        msg = NodeHealth()
        msg.node_name = 'payload_modes'
        msg.sequence = self.heartbeat_sequence
        msg.healthy = not self.fault_latched
        msg.detail = self.fault_reason[:256]
        self.health_pub.publish(msg)


def main():
    rclpy.init()
    node = Modes()
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
