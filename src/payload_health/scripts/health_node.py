#!/usr/bin/env python3
"""1 Hz payload housekeeping; cgroup resources when available, host fallback."""
import math
from pathlib import Path
import shutil
import time
import uuid

import rclpy
from rclpy.clock import Clock, ClockType
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy, ReliabilityPolicy
from payload_interfaces.msg import CameraStatus, Housekeeping, NodeHealth


class Resources:
    def __init__(self):
        self.group = Path('/sys/fs/cgroup')
        self.cgroup = all((self.group / name).exists() for name in ('cpu.stat', 'cpu.max', 'memory.current', 'memory.max'))
        self.previous = None

    def sample(self):
        memory = {parts[0].rstrip(':'): int(parts[1]) * 1024
                  for line in Path('/proc/meminfo').read_text().splitlines()
                  if len(parts := line.split()) >= 2}
        total = memory['MemTotal']
        now = time.monotonic()
        if self.cgroup:
            usage = dict(line.split() for line in (self.group / 'cpu.stat').read_text().splitlines())
            cpu = int(usage['usage_usec']) / 1000000
            quota, period = (self.group / 'cpu.max').read_text().split()
            if quota == 'max':
                cores = sum(line.startswith('cpu') and not line.startswith('cpu ')
                            for line in Path('/proc/stat').read_text().splitlines())
            else:
                cores = int(quota) / int(period)
            limit = (self.group / 'memory.max').read_text().strip()
            if limit != 'max':
                total = min(total, int(limit))
            used = int((self.group / 'memory.current').read_text())
            percent = 0.0 if self.previous is None else (
                (cpu - self.previous[1]) / max(now - self.previous[0], 1e-6) / max(cores, 0.01) * 100)
            self.previous = (now, cpu)
        else:
            ticks = [int(value) for value in Path('/proc/stat').read_text().splitlines()[0].split()[1:]]
            # guest counters are already included in user/nice; do not double count.
            current = (sum(ticks[:8]), ticks[3] + ticks[4])
            percent = 0.0 if self.previous is None else (
                100 * (1 - (current[1] - self.previous[1]) / max(current[0] - self.previous[0], 1)))
            self.previous = current
            used = total - memory['MemAvailable']
        if not math.isfinite(percent) or used < 0 or total <= 0:
            raise ValueError('invalid resource counters')
        return max(0.0, min(100.0, percent)), used, total, 'cgroup' if self.cgroup else 'host'


class Health(Node):
    def __init__(self):
        super().__init__('payload_health')
        self.root = Path(self.declare_parameter('data_dir', 'data/payload').value)
        self.root.mkdir(parents=True, exist_ok=True)
        self.timeout = self.declare_parameter('node_timeout_s', 2.5).value
        self.boot_id = str(uuid.uuid4())
        self.sequence = 0
        self.nodes = {}
        self.camera = self.state = None
        self.last_camera = self.last_state = 0.0
        self.resources = Resources()
        if not self.resources.cgroup:
            self.get_logger().warning('cgroup counters unavailable; housekeeping resource_scope=host')
        self.housekeeping = self.create_publisher(Housekeeping, '/payload/housekeeping', 8)
        self.heartbeat = self.create_publisher(NodeHealth, '/payload/node_health', 16)
        self.nodes_sub = self.create_subscription(NodeHealth, '/payload/node_health', self.node_health, 16)
        qos = QoSProfile(depth=32, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.camera_sub = self.create_subscription(CameraStatus, '/payload/camera_status', self.camera_status, qos)
        self.state_sub = self.create_subscription(CameraStatus, '/payload/state', self.payload_status, qos)
        self.timer = self.create_timer(1.0, self.tick, clock=Clock(clock_type=ClockType.STEADY_TIME))

    def node_health(self, message):
        allowed = ('payload_modes', 'payload_processing', 'payload_storage', 'payload_gateway')
        if message.node_name in allowed:
            self.nodes[message.node_name] = (time.monotonic(), message)

    def camera_status(self, message):
        self.camera, self.last_camera = message, time.monotonic()

    def payload_status(self, message):
        self.state, self.last_state = message, time.monotonic()

    def fresh(self, name):
        return name in self.nodes and time.monotonic() - self.nodes[name][0] < self.timeout

    def tick(self):
        now = time.monotonic()
        self.sequence += 1
        msg = Housekeeping()
        msg.utc = self.get_clock().now().to_msg()
        msg.boot_id, msg.heartbeat_sequence = self.boot_id, self.sequence
        resource_ok = True
        try:
            msg.cpu_percent, msg.memory_used_bytes, msg.memory_total_bytes, msg.resource_scope = self.resources.sample()
            disk = shutil.disk_usage(self.root)
            msg.disk_total_bytes, msg.disk_used_bytes = disk.total, disk.used
        except Exception as error:
            resource_ok = False
            msg.resource_scope = 'unavailable'
            msg.detail = str(error)[:256]
            self.get_logger().error(f'housekeeping resource sample failed: {error}')
        stale = [name for name in ('payload_modes', 'payload_processing', 'payload_storage') if not self.fresh(name)]
        if self.camera is None or now - self.last_camera > 1.2:
            stale.append('payload_camera')
        else:
            msg.camera_lifecycle_state = self.camera.lifecycle_state
            msg.camera_healthy = self.camera.mode != CameraStatus.FAULT and self.camera.lifecycle_state != 4
        if self.state is None or now - self.last_state > 1.2:
            stale.append('payload_state')
            status = self.camera
        else:
            status = self.state
        if status is not None:
            msg.payload_mode, msg.payload_state = status.mode, status.operational_state
        else:
            msg.payload_mode, msg.payload_state = CameraStatus.BOOT, 'BOOT'
        if self.fresh('payload_storage'):
            storage = self.nodes['payload_storage'][1]
            if storage.counts_valid:
                msg.stored_images, msg.pending_files = storage.stored_images, storage.pending_files
        if self.fresh('payload_gateway'):
            gateway = self.nodes['payload_gateway'][1]
            msg.gateway_connected = gateway.healthy and gateway.gateway_connected
        msg.stale_nodes = stale
        msg.nodes_healthy = resource_ok and not stale and msg.camera_healthy and all(
            self.nodes[name][1].healthy for name in ('payload_modes', 'payload_processing', 'payload_storage')
            if name in self.nodes)
        if stale and not msg.detail:
            msg.detail = ('missing heartbeat: ' + ', '.join(stale))[:256]
        self.housekeeping.publish(msg)
        heartbeat = NodeHealth()
        heartbeat.node_name = 'payload_health'
        heartbeat.sequence = self.sequence
        heartbeat.healthy = resource_ok
        heartbeat.detail = '' if resource_ok else msg.detail
        self.heartbeat.publish(heartbeat)


def main():
    rclpy.init()
    node = Health()
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
