#!/usr/bin/env python3
from datetime import datetime, timezone
import fcntl
import json
import math
import shutil
from pathlib import Path
import time

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy
from payload_interfaces.msg import CompressedCapture, FileReady, NodeHealth, StageEvent
from rclpy.clock import Clock, ClockType
from foundations.catalogue import Catalogue
from payload_interfaces.srv import CatalogueCommand


class Storage(Node):
    def __init__(self):
        super().__init__('payload_storage')
        directory = self.declare_parameter('data_dir', 'data/payload').value
        quota = self.declare_parameter('quota_bytes', 268435456).value
        self.declare_parameter('simulated_write_delay_s', 0.0)
        root = Path(directory).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if len(str(root)) > 200 or quota < 1:
            raise ValueError('storage path too long or quota invalid')
        self.owner_lock = (root / '.owner.lock').open('a')
        fcntl.flock(self.owner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.catalogue = Catalogue(root, quota)
        self.root = root
        qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.RELIABLE,
                         durability=DurabilityPolicy.VOLATILE)
        self.results = self.create_publisher(FileReady, '/payload/file_ready', 16)
        self.stages = self.create_publisher(StageEvent, '/payload/stage', 16)
        self.health = self.create_publisher(NodeHealth, '/payload/node_health', 16)
        self.health_sequence = 0
        self.last_error = ''
        self.health_timer = self.create_timer(1.0, self.heartbeat,
                                              clock=Clock(clock_type=ClockType.STEADY_TIME))
        self.subscription = self.create_subscription(CompressedCapture, '/payload/compressed', self.store, qos)
        self.commands = self.create_service(CatalogueCommand, '/payload/catalogue', self.command)
        self.get_logger().info(json.dumps({'event': 'storage_recovered', 'entries': len(self.catalogue.list())}))

    def command(self, request, response):
        try:
            if request.operation == CatalogueCommand.Request.LIST:
                if not 1 <= request.limit <= 32:
                    raise ValueError('invalid page limit')
                # Compact entries keep worst-case pages below SPP's 4096 bytes.
                rows = self.catalogue.db.execute(
                    'SELECT id,size,status,downlink_status FROM images ORDER BY id LIMIT ? OFFSET ?',
                    (request.limit, request.offset)).fetchall()
                value = {'files': [dict(row) for row in rows], 'next_offset': request.offset + len(rows)}
                # Status strings and UUIDs bound a page to at most 3500 bytes.
                while len(json.dumps(value, separators=(',', ':'))) > 3500:
                    value['files'].pop()
                    value['next_offset'] -= 1
            elif request.operation == CatalogueCommand.Request.REQUEST:
                path = self.catalogue._path(request.image_id)
                row = self.catalogue.db.execute('SELECT * FROM images WHERE id=?', (request.image_id,)).fetchone()
                from foundations.catalogue import sha256_file
                if not row or row['status'] != 'COMPLETE' or sha256_file(path) != row['sha256']:
                    raise ValueError('file unavailable or checksum invalid')
                value = {'id': request.image_id, 'size': row['size'], 'sha256': row['sha256']}
            elif request.operation == CatalogueCommand.Request.CONFIRM:
                self.catalogue.mark_delivered(request.image_id, request.sha256)
                value = {'delivered': request.image_id}
            elif request.operation == CatalogueCommand.Request.DELETE:
                self.catalogue.delete(request.image_id)
                value = {'deleted': request.image_id}
            else:
                raise ValueError('unknown catalogue operation')
            response.result_json = json.dumps(value, separators=(',', ':'))
            response.accepted = True
        except Exception as error:
            response.reason = str(error)[:256]
        return response

    def store(self, message):
        started = time.monotonic()
        event = FileReady()
        event.metadata = message.metadata
        meta = message.metadata
        stage = StageEvent()
        stage.capture_id = meta.capture_id
        stage.stage = StageEvent.STORING
        self.stages.publish(stage)
        try:
            delay = self.get_parameter('simulated_write_delay_s').value
            if not math.isfinite(delay) or not 0 <= delay <= 0.5:
                raise ValueError('simulated write delay must be within 0..0.5 seconds')
            if delay:
                time.sleep(delay)
            data = bytes(message.image.data)
            if len(data) > 16777216 or len(data) != meta.compressed_bytes:
                raise ValueError('compressed image size mismatch or limit exceeded')
            fields = ('width', 'height', 'source_width', 'source_height', 'quality',
                      'spacecraft_time_us', 'exposure_us', 'noise_sigma', 'raw_bytes',
                      'compressed_bytes', 'capture_ms', 'mean', 'minimum', 'maximum')
            metadata = {key: getattr(meta, key) for key in fields}
            metadata['processing_duration_ms'] = meta.processing_ms
            metadata['capture_stamp_ns'] = meta.utc.sec * 1000000000 + meta.utc.nanosec
            metadata['utc'] = datetime.fromtimestamp(meta.utc.sec, timezone.utc).replace(
                microsecond=meta.utc.nanosec // 1000).isoformat()
            metadata['pixel_encoding'] = meta.encoding
            image_id = self.catalogue.store(data, metadata, capture_id=meta.capture_id)
            event.path = str(self.root / (image_id + '.jpg'))
            # Read the checksum from the committed catalogue, never announce
            # delivery or file-ready from an uncommitted write.
            row = self.catalogue.db.execute('SELECT sha256 FROM images WHERE id=?', (image_id,)).fetchone()
            event.sha256 = row['sha256']
            event.success = True
            self.last_error = ''
            event.storage_ms = (time.monotonic() - started) * 1000
            self.get_logger().info(json.dumps({'event': 'file_ready', 'id': image_id,
                                               'bytes': len(data), 'sha256': event.sha256,
                                               'storage_ms': event.storage_ms}))
        except Exception as error:
            self.last_error = str(error)
            event.error = str(error)[:256]
            self.get_logger().error(json.dumps({'event': 'storage_failed', 'id': meta.capture_id,
                                                'error': str(error)}))
        self.results.publish(event)

    def heartbeat(self):
        self.health_sequence += 1
        msg = NodeHealth()
        msg.node_name = 'payload_storage'
        msg.sequence = self.health_sequence
        try:
            quota = self.get_parameter('quota_bytes').value
            if quota < 1:
                raise ValueError('quota must be positive')
            self.catalogue.quota = quota
            row = self.catalogue.db.execute("""SELECT
                coalesce(sum(status='COMPLETE'),0),
                coalesce(sum(status='COMPLETE' AND downlink_status!='DELIVERED'),0),
                coalesce(sum(CASE WHEN status!='MISSING' THEN size ELSE 0 END),0)
                FROM images""").fetchone()
            msg.stored_images, msg.pending_files = row[0], row[1]
            msg.counts_valid = True
            msg.healthy = row[2] < quota and shutil.disk_usage(self.root).free > 4194304
            msg.detail = self.last_error[:256] if msg.healthy else 'storage quota exhausted or disk headroom low'
        except Exception as error:
            msg.detail = str(error)[:256]
            self.get_logger().error(json.dumps({'event': 'storage_health_failed', 'error': str(error)}))
        self.health.publish(msg)

    def destroy_node(self):
        self.catalogue.close()
        self.owner_lock.close()
        return super().destroy_node()


def main():
    rclpy.init()
    node = None
    try:
        node = Storage()
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass  # Operator shutdown; not a storage exception.
    finally:
        if node:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
