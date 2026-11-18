"""Experimental resumable file transport (not CFDP).

Manifest identity, fsynced contiguous offsets, per-chunk CRC, final SHA-256.
The sender charges all application frame bytes, replies and injected losses to
one monotonic pacer. OS TCP/IP overhead is outside this simulator's rate model.
"""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import random
import time
import uuid
import zlib

from interfaces.framing import encode_frame, read_frame, send_frame

CHUNK = 4096
MAX_FILE = 16 * 1024 * 1024


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(65536), b''):
            value.update(block)
    return value.hexdigest()


def sync_dir(root):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_manifest(message):
    image_id = message['id']
    if not isinstance(image_id, str) or str(uuid.UUID(image_id)) != image_id:
        raise ValueError('canonical UUID required')
    size, sha = message['size'], message['sha256']
    if type(size) is not int or not 1 <= size <= MAX_FILE:
        raise ValueError('invalid file size')
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha):
        raise ValueError('invalid digest')
    return {'id': image_id, 'size': size, 'sha256': sha}


class Link:
    def __init__(self, rate_mbps=1000, loss=0, seed=42, window=600, latency_ms=0,
                 corrupt_once=False, interrupt_after=None):
        if not all(math.isfinite(x) for x in (rate_mbps, loss, window, latency_ms)):
            raise ValueError('nonfinite link setting')
        if not 0 < rate_mbps <= 1000 or not 0 <= loss <= 0.5 or not 0 < window <= 3600 or not 0 <= latency_ms <= 1000:
            raise ValueError('invalid link setting')
        self.rate = rate_mbps * 1_000_000 / 8
        self.loss, self.random, self.window, self.latency = loss, random.Random(seed), window, latency_ms / 1000
        self.corrupt_once, self.interrupt_after = corrupt_once, interrupt_after
        self.started = self.next_send = time.monotonic()
        self.wire_bytes = self.lost_packets = self.retransmissions = self.chunks = 0
        self.resumed_bytes = self.payload_bytes = 0
        self.cancelled = None

    def charge(self, size):
        if self.cancelled is not None and self.cancelled.is_set():
            raise ConnectionAbortedError('transfer cancelled during shutdown')
        now = time.monotonic()
        due = max(now, self.next_send) + size / self.rate
        if due > self.started + self.window:
            raise TimeoutError('pass window closed; resume next pass')
        self.next_send = due
        delay = max(0, due - now)
        if self.cancelled is None:
            time.sleep(delay)
        elif self.cancelled.wait(delay):
            raise ConnectionAbortedError('transfer cancelled during shutdown')
        self.wire_bytes += size

    def send(self, sock, message):
        raw = encode_frame(message)
        self.charge(len(raw))
        sock.sendall(raw)

    def receive(self, sock):
        value, size = read_frame(sock)
        self.charge(size)
        if self.latency:
            time.sleep(self.latency)
        return value

    def metrics(self):
        return {'attempted_wire_bytes': self.wire_bytes, 'lost_packets': self.lost_packets,
                'retransmissions': self.retransmissions, 'resumed_bytes': self.resumed_bytes,
                'payload_delivered_bytes': self.payload_bytes,
                'wall_seconds': time.monotonic() - self.started}


def send_file(sock, path, manifest, link):
    manifest = validate_manifest(manifest)
    if Path(path).stat().st_size != manifest['size'] or digest(path) != manifest['sha256']:
        raise ValueError('source manifest mismatch')
    link.send(sock, {'type': 'MANIFEST', **manifest})
    response = link.receive(sock)
    offset = response.get('offset')
    if response.get('type') != 'READY' or type(offset) is not int or not 0 <= offset <= manifest['size']:
        raise ValueError('invalid resume acknowledgement')
    link.resumed_bytes += offset
    with Path(path).open('rb') as handle:
        handle.seek(offset)
        while offset < manifest['size']:
            data = handle.read(min(CHUNK, manifest['size'] - offset))
            crc = zlib.crc32(data)
            for attempt in range(100):
                if attempt:
                    link.retransmissions += 1
                message = {'type': 'CHUNK', 'offset': offset, 'crc32': crc,
                           'data': base64.b64encode(data).decode('ascii')}
                if link.random.random() < link.loss:
                    # Deterministically lose a radio frame before the TCP adapter.
                    link.charge(len(encode_frame(message)))
                    link.lost_packets += 1
                    continue
                if link.corrupt_once:
                    link.corrupt_once = False
                    message['crc32'] ^= 1
                link.send(sock, message)
                response = link.receive(sock)
                if response.get('type') == 'NAK' and response.get('offset') == offset:
                    continue
                if response.get('type') != 'ACK' or response.get('offset') != offset + len(data):
                    raise ValueError('invalid chunk acknowledgement')
                break
            else:
                raise TimeoutError('chunk retry budget exhausted')
            offset += len(data)
            link.payload_bytes += len(data)
            link.chunks += 1
            if link.interrupt_after is not None and link.chunks >= link.interrupt_after:
                raise ConnectionAbortedError('injected link interruption after durable ACK')
    link.send(sock, {'type': 'FINISH'})
    receipt = link.receive(sock)
    if receipt != {'type': 'RECEIPT', **manifest}:
        raise ValueError('invalid final receipt')
    return receipt


def receive_file(sock, directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    message, _ = read_frame(sock)
    if message.get('type') != 'MANIFEST':
        raise ValueError('manifest required')
    manifest = validate_manifest(message)
    stem = root / manifest['id']
    final, part, state = (stem.with_suffix(suffix) for suffix in ('.jpg', '.part', '.json'))
    if state.exists():
        if json.loads(state.read_text()) != manifest:
            raise ValueError('transfer identity conflict')
    else:
        if part.exists() or final.exists():
            raise ValueError('unidentified existing transfer')
        temporary = stem.with_suffix('.manifest.tmp')
        with temporary.open('w') as handle:
            json.dump(manifest, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, state)
        sync_dir(root)
    if final.exists():
        if final.stat().st_size != manifest['size'] or digest(final) != manifest['sha256']:
            raise ValueError('existing destination corrupt')
        offset = manifest['size']
    else:
        offset = part.stat().st_size if part.exists() else 0
    if offset > manifest['size']:
        raise ValueError('partial file exceeds manifest')
    send_frame(sock, {'type': 'READY', 'offset': offset})
    handle = None if final.exists() else part.open('ab')
    if handle:
        sync_dir(root)
    try:
        while True:
            frame, _ = read_frame(sock)
            if frame.get('type') == 'FINISH':
                if offset != manifest['size']:
                    raise ValueError('premature finish')
                if handle:
                    handle.close()
                    handle = None
                candidate = final if final.exists() else part
                if digest(candidate) != manifest['sha256']:
                    # Retain corrupt bytes as evidence; explicit repair required.
                    raise ValueError('final checksum mismatch; quarantine required')
                if candidate == part:
                    os.replace(part, final)
                    sync_dir(root)
                receipt = {'type': 'RECEIPT', **manifest}
                send_frame(sock, receipt)
                return receipt
            if frame.get('type') != 'CHUNK' or handle is None:
                raise ValueError('unexpected frame')
            data = base64.b64decode(frame['data'], validate=True)
            if not 0 < len(data) <= CHUNK or frame['offset'] != offset or offset + len(data) > manifest['size']:
                raise ValueError('invalid chunk bounds or offset')
            if zlib.crc32(data) != frame['crc32']:
                send_frame(sock, {'type': 'NAK', 'offset': offset})
                continue
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            offset += len(data)
            send_frame(sock, {'type': 'ACK', 'offset': offset})
    finally:
        if handle:
            handle.close()
