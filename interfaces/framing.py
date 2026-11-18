"""Bounded experimental JSON frames and exact SPP reads. No ROS dependency."""
import json
import struct
import time
from interfaces.space_packet import MAX_PACKET, PRIMARY, Packet, ProtocolError

MAX_FRAME = 32768


def exact(sock, size, deadline=None):
    data = bytearray()
    original_timeout = sock.gettimeout()
    try:
        while len(data) < size:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('absolute frame deadline exceeded')
                sock.settimeout(remaining)
            block = sock.recv(size - len(data))
            if not block:
                raise EOFError('peer disconnected')
            data.extend(block)
    finally:
        if deadline is not None:
            sock.settimeout(original_timeout)
    return bytes(data)


def read_packet(sock):
    deadline = time.monotonic() + (sock.gettimeout() or 3)
    header = exact(sock, 6, deadline)
    total = PRIMARY.unpack(header)[2] + 7
    if not 20 <= total <= MAX_PACKET:
        raise ProtocolError('invalid declared packet length')
    return Packet.decode(header + exact(sock, total - 6, deadline))


def encode_frame(message):
    raw = json.dumps(message, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_FRAME:
        raise ProtocolError('frame exceeds maximum')
    return struct.pack('!I', len(raw)) + raw


def send_frame(sock, message):
    raw = encode_frame(message)
    sock.sendall(raw)
    return len(raw)


def read_frame(sock):
    deadline = time.monotonic() + (sock.gettimeout() or 15)
    size = struct.unpack('!I', exact(sock, 4, deadline))[0]
    if not 2 <= size <= MAX_FRAME:
        raise ProtocolError('invalid frame length')
    try:
        value = json.loads(exact(sock, size, deadline))
    except (ValueError, RecursionError) as error:
        raise ProtocolError('invalid JSON frame') from error
    if not isinstance(value, dict):
        raise ProtocolError('object frame required')
    return value, size + 4
