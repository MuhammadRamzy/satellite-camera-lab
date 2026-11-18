"""Independent cFS behavioral simulator. No ROS or payload package imports.

Models command dispatch, a 1 Hz scheduler/health service, persistent command IDs,
spacecraft authority and a durable downlink queue. It is not a cFS installation.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import socket
import sqlite3
import struct
import threading
import time
import uuid

from interfaces.file_link import Link, digest, receive_file, send_file
from interfaces.framing import read_frame, read_packet, send_frame
from interfaces.space_packet import ACK_APID, COMMAND_APID, Command, Packet, SequenceTracker, validate_command


class Bus:
    def __init__(self, root, gateway_port=47001, file_port=47002, ground_port=47004):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'bus.db', check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS counter (singleton INTEGER PRIMARY KEY, value INTEGER)')
        self.db.execute('INSERT OR IGNORE INTO counter VALUES(1,100)')
        self.db.execute('CREATE TABLE IF NOT EXISTS queue (id TEXT PRIMARY KEY, manifest TEXT, state TEXT)')
        self.db.commit()
        self.gateway_port, self.file_port, self.ground_port = gateway_port, file_port, ground_port
        self.lock = threading.RLock()
        self.transfer_lock = threading.Lock()
        self.peer = None
        self.sequence = 0
        self.tracker = SequenceTracker()
        self.latest = {}
        self.last_health = 0.0
        self.operational = False
        self.stop = threading.Event()
        self.scheduler = threading.Thread(target=self.schedule, daemon=True)
        self.scheduler.start()

    def allocate(self, requested=None):
        value = self.db.execute('SELECT value FROM counter WHERE singleton=1').fetchone()[0]
        command_id = value + 1 if requested is None else int(requested)
        if not 1 <= command_id <= 0xFFFFFFFF:
            raise ValueError('command ID space exhausted or invalid')
        with self.db:
            self.db.execute('UPDATE counter SET value=? WHERE singleton=1', (max(value, command_id),))
        return command_id

    def connect(self):
        if self.peer is None:
            self.peer = socket.create_connection(('127.0.0.1', self.gateway_port), timeout=6)
            self.peer.settimeout(6)
            self.sequence, self.tracker = 0, SequenceTracker()

    def command(self, opcode, body=b'', command_id=None):
        with self.lock:
            cid = self.allocate(command_id)
            try:
                self.connect()
                packet = Packet(COMMAND_APID, self.sequence, True, int(opcode), cid, time.time_ns() // 1000, body)
                validate_command(packet)
                self.peer.sendall(packet.encode())
                response = read_packet(self.peer)
                if response.apid != ACK_APID or response.is_command or response.command_id != cid or response.opcode != opcode:
                    raise ValueError('uncorrelated ACK')
                self.tracker.accept(response)
                self.sequence = (self.sequence + 1) % 16384
                return json.loads(response.payload) | {'command_id': cid}
            except (OSError, EOFError, ValueError):
                if self.peer is not None:
                    self.peer.close()
                self.peer, self.operational = None, False
                # No automatic retry: an exposure may have happened. The same ID
                # can be queried again explicitly after reconnect.
                raise

    def schedule(self):
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.latest = self.command(Command.PL_GET_STATUS)
                if self.latest.get('healthy') and self.latest.get('state_fresh'):
                    self.last_health = started
                elif self.operational and started - self.last_health > 3:
                    self.command(Command.PL_SAFE)
                    self.operational = False
                    print(json.dumps({'event': 'bus_HS_SAFE', 'reason': 'payload health stale/unhealthy'}), flush=True)
            except (OSError, EOFError, ValueError) as error:
                self.latest = {'status': 'DISCONNECTED', 'reason': str(error)[:256]}
            self.stop.wait(max(0.05, 1 - (time.monotonic() - started)))

    def queued(self):
        with self.lock:
            return [dict(id=row[0], manifest=json.loads(row[1]), state=row[2])
                    for row in self.db.execute('SELECT * FROM queue ORDER BY id')]

    def fetch(self, image_id):
        result = self.command(Command.PL_REQUEST_FILE, uuid.UUID(image_id).bytes)
        if result['status'] != 'COMPLETED':
            return result
        with socket.create_connection(('127.0.0.1', self.file_port), timeout=6) as peer:
            peer.settimeout(6)
            send_frame(peer, {'type': 'GET', 'id': image_id})
            receipt = receive_file(peer, self.root / 'spool')
        if any(receipt[key] != result[key] for key in ('id', 'size', 'sha256')):
            raise ValueError('payload file receipt mismatch')
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO queue VALUES(?,?,?)',
                            (image_id, json.dumps({k: receipt[k] for k in ('id', 'size', 'sha256')}), 'PENDING'))
        return {'status': 'COMPLETED', 'spooled': image_id, 'sha256': receipt['sha256']}

    def downlink(self, settings):
        report = []
        # One pass-wide Link: windows/rate accounting do not reset per file.
        link = Link(**settings)
        try:
            for entry in self.queued():
                if entry['state'] == 'DELIVERED':
                    continue
                manifest = entry['manifest']
                path = self.root / 'spool' / (entry['id'] + '.jpg')
                if digest(path) != manifest['sha256']:
                    raise ValueError('bus spool checksum mismatch')
                with socket.create_connection(('127.0.0.1', self.ground_port), timeout=6) as peer:
                    peer.settimeout(10)
                    # Ground receives MANIFEST first; manifest/chunks/ACKs and
                    # receipt are all accounted by send_file's pass-wide pacer.
                    receipt = send_file(peer, path, manifest, link)
                confirmed = self.command(Command.PL_CONFIRM_FILE,
                                         uuid.UUID(entry['id']).bytes + bytes.fromhex(receipt['sha256']))
                if confirmed['status'] != 'COMPLETED':
                    raise ValueError('payload did not accept ground receipt')
                with self.lock, self.db:
                    self.db.execute("UPDATE queue SET state='DELIVERED' WHERE id=?", (entry['id'],))
                report.append(receipt)
            return {'status': 'COMPLETED', 'receipts': report, 'metrics': link.metrics()}
        except (OSError, EOFError, ValueError) as error:
            return {'status': 'INTERRUPTED', 'reason': str(error), 'receipts': report, 'metrics': link.metrics()}

    def handle(self, request):
        op = request['op']
        command_id = request.get('command_id')
        def command(cmd, body=b''):
            return self.command(cmd, body, command_id)
        if op == 'status':
            return command(Command.PL_GET_STATUS) | {'queue': self.queued(), 'spacecraft_operational': self.operational}
        if op == 'ping':
            return command(Command.PL_PING)
        if op == 'spacecraft':
            mode, permission = int(request['mode']), request.get('imaging_permitted', False)
            if type(permission) is not bool:
                raise ValueError('boolean permission required')
            result = command(Command.PL_SPACECRAFT_CONTROL, bytes([mode, int(permission)]))
            if result['status'] == 'COMPLETED':
                self.operational = mode == 1 and permission
                self.last_health = time.monotonic()
            return result
        if op == 'mode':
            return command(Command.PL_SET_MODE, bytes([int(request['mode'])]))
        if op == 'recover':
            return command(Command.PL_RECOVER)
        if op == 'safe':
            self.operational = False
            return command(Command.PL_SAFE)
        if op == 'capture':
            return command(Command.PL_CAPTURE)
        if op == 'burst':
            return command(Command.PL_CAPTURE_BURST, struct.pack('!HI', int(request['count']), int(request['interval_ms'])))
        if op == 'abort':
            return command(Command.PL_ABORT_CAPTURE)
        if op == 'configure':
            body = struct.pack('!HHBBIBB', int(request.get('width', 640)), int(request.get('height', 480)),
                int(request.get('fps', 1)), int(request.get('quality', 75)), int(request.get('exposure_us', 10000)),
                int(request.get('noise', 2)), int(request.get('source', 0)))
            return command(Command.PL_SET_CAMERA_CONFIG, body)
        if op == 'list':
            return command(Command.PL_LIST_FILES, struct.pack('!IH', int(request.get('offset', 0)), int(request.get('limit', 16))))
        if op in ('delete', 'confirm'):
            body = uuid.UUID(request['id']).bytes
            if op == 'confirm':
                body += bytes.fromhex(request['sha256'])
            return command(Command.PL_DELETE_FILE if op == 'delete' else Command.PL_CONFIRM_FILE, body)
        if op == 'fetch':
            if not self.transfer_lock.acquire(blocking=False):
                return {'status': 'ERROR', 'reason': 'file operation busy'}
            try:
                return self.fetch(request['id'])
            finally:
                self.transfer_lock.release()
        if op == 'downlink':
            if not self.transfer_lock.acquire(blocking=False):
                return {'status': 'ERROR', 'reason': 'file operation busy'}
            try:
                return self.downlink(request.get('link', {}))
            finally:
                self.transfer_lock.release()
        if op == 'reconnect':
            with self.lock:
                if self.peer:
                    self.peer.close()
                    self.peer = None
                self.operational = False
            return {'status': 'COMPLETED', 'reason': 'session closed; explicit permission/recovery required'}
        raise ValueError('unknown operator command')

    def close(self):
        self.stop.set()
        self.scheduler.join(timeout=8)
        if self.peer:
            self.peer.close()
        self.db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--port', type=int, default=47003)
    parser.add_argument('--gateway-port', type=int, default=47001)
    parser.add_argument('--file-port', type=int, default=47002)
    parser.add_argument('--ground-port', type=int, default=47004)
    args = parser.parse_args()
    bus = Bus(args.root, args.gateway_port, args.file_port, args.ground_port)
    slots = threading.BoundedSemaphore(4)

    def handle_peer(peer):
        try:
            with peer:
                peer.settimeout(10)
                try:
                    request, _ = read_frame(peer)
                    result = bus.handle(request)
                except (OSError, EOFError, ValueError, KeyError, TypeError, AttributeError, OverflowError, struct.error) as error:
                    result = {'status': 'ERROR', 'reason': str(error)[:256]}
                try:
                    send_frame(peer, result)
                except OSError:
                    pass
        finally:
            slots.release()

    workers = ThreadPoolExecutor(max_workers=4)
    try:
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(('127.0.0.1', args.port))
            listener.listen(4)
            listener.settimeout(0.5)
            while True:
                try:
                    peer, _ = listener.accept()
                except socket.timeout:
                    continue
                if slots.acquire(blocking=False):
                    workers.submit(handle_peer, peer)
                else:
                    peer.close()
    except KeyboardInterrupt:
        pass
    finally:
        workers.shutdown(wait=True)
        bus.close()


if __name__ == '__main__':
    main()
