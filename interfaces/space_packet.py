"""CCSDS primary header subset plus mission-specific v1 command encoding.

No ROS dependency. This module does not claim complete CCSDS conformance.
Only unsegmented packets, version 0, secondary header present are supported.
"""
from dataclasses import dataclass
from enum import IntEnum
import struct

PRIMARY = struct.Struct("!HHH")
SECONDARY = struct.Struct("!BBIQ")  # ICD version, opcode, command id, UTC microseconds
MAX_PACKET = 4096
COMMAND_APID = 0x100
ACK_APID = 0x101
HEALTH_APID = 0x102
EVENT_APID = 0x103


class ProtocolError(ValueError):
    pass


class Command(IntEnum):
    PL_PING = 1
    PL_GET_STATUS = 2
    PL_SET_MODE = 3
    PL_CAPTURE = 4
    PL_CAPTURE_BURST = 5
    PL_SET_CAMERA_CONFIG = 6
    PL_LIST_FILES = 7
    PL_REQUEST_FILE = 8
    PL_DELETE_FILE = 9
    PL_ABORT_CAPTURE = 10
    PL_SAFE = 11


@dataclass(frozen=True)
class Packet:
    apid: int
    sequence: int
    is_command: bool
    opcode: int
    command_id: int
    utc_us: int
    payload: bytes = b""

    def encode(self):
        if not 0 <= self.apid < 0x7FF or not 0 <= self.sequence < 16384:
            raise ProtocolError("APID or sequence out of range (idle APID excluded)")
        if not isinstance(self.is_command, bool):
            raise ProtocolError("packet type must be boolean")
        try:
            data = SECONDARY.pack(1, self.opcode, self.command_id, self.utc_us) + self.payload
        except (struct.error, TypeError) as exc:
            raise ProtocolError("mission field out of range") from exc
        if len(data) + PRIMARY.size > MAX_PACKET:
            raise ProtocolError("packet exceeds mission maximum")
        return PRIMARY.pack((int(self.is_command) << 12) | 0x800 | self.apid,
                            0xC000 | self.sequence, len(data) - 1) + data

    @classmethod
    def decode(cls, raw):
        if not PRIMARY.size + SECONDARY.size <= len(raw) <= MAX_PACKET:
            raise ProtocolError("invalid packet size")
        first, seq, length = PRIMARY.unpack_from(raw)
        if first >> 13 != 0:
            raise ProtocolError("unsupported CCSDS packet version")
        if not first & 0x800 or seq >> 14 != 3:
            raise ProtocolError("secondary header required; segmentation unsupported")
        if length + 7 != len(raw):
            raise ProtocolError("packet data length mismatch")
        if first & 0x7FF == 0x7FF:
            raise ProtocolError("idle packet not valid for mission interface")
        version, op, cid, utc = SECONDARY.unpack_from(raw, PRIMARY.size)
        if version != 1:
            raise ProtocolError("unsupported mission ICD version")
        return cls(first & 0x7FF, seq & 0x3FFF, bool(first & 0x1000), op, cid, utc,
                   bytes(raw[PRIMARY.size + SECONDARY.size:]))


# Body lengths are exact. No trailing bytes, implicit native widths or JSON.
EMPTY = {Command.PL_PING, Command.PL_GET_STATUS, Command.PL_ABORT_CAPTURE, Command.PL_SAFE}
LENGTHS = {Command.PL_SET_MODE: 1, Command.PL_CAPTURE: 0, Command.PL_CAPTURE_BURST: 6,
           Command.PL_SET_CAMERA_CONFIG: 12, Command.PL_LIST_FILES: 6,
           Command.PL_REQUEST_FILE: 16, Command.PL_DELETE_FILE: 16}


def validate_command(packet):
    if packet.apid != COMMAND_APID or not packet.is_command or packet.command_id == 0:
        raise ProtocolError("wrong command APID/type or zero command id")
    try:
        cmd = Command(packet.opcode)
    except ValueError as exc:
        raise ProtocolError("unknown command") from exc
    expected = 0 if cmd in EMPTY else LENGTHS[cmd]
    if len(packet.payload) != expected:
        raise ProtocolError("wrong command body length")
    if cmd == Command.PL_SET_MODE and packet.payload[0] not in (1, 2, 3, 4):
        raise ProtocolError("unknown commanded mode")
    if cmd == Command.PL_CAPTURE_BURST:
        count, interval_ms = struct.unpack("!HI", packet.payload)
        if not 1 <= count <= 100 or not 100 <= interval_ms <= 60000:
            raise ProtocolError("burst out of bounds")
    if cmd == Command.PL_SET_CAMERA_CONFIG:
        width, height, fps, quality, exposure, noise, source = struct.unpack("!HHBBIBB", packet.payload)
        if not (16 <= width <= 1920 and 16 <= height <= 1080 and 1 <= fps <= 10
                and 1 <= quality <= 100 and 1 <= exposure <= 1000000
                and noise <= 100 and source <= 2):
            raise ProtocolError("camera configuration out of bounds")
    if cmd == Command.PL_LIST_FILES:
        _, limit = struct.unpack("!IH", packet.payload)
        if not 1 <= limit <= 32:
            raise ProtocolError("catalogue page size out of bounds")
    return cmd


class SequenceTracker:
    """One peer session, state per APID. Reconnect requires explicit reset.

    Commands must be in order (TCP preserves order). Duplicate command IDs must
    be handled by a persistent execution journal before consulting this tracker.
    """
    def __init__(self):
        self.last = {}

    def accept(self, packet):
        previous = self.last.get(packet.apid)
        if previous is not None and packet.sequence != (previous + 1) % 16384:
            raise ProtocolError("sequence gap, replay or out of order packet")
        self.last[packet.apid] = packet.sequence


class StreamDecoder:
    """Bounded TCP reassembly using primary-header length; reject, then close.

    No magic bytes exist in SPP: attempting to resync malicious streams by
    guessing a header is unsafe. Caller must close on ProtocolError.
    """
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        packets = []
        # Feed incrementally to avoid retaining a large attacker-supplied buffer.
        for value in data:
            self.buffer.append(value)
            if len(self.buffer) >= PRIMARY.size:
                length = PRIMARY.unpack_from(self.buffer)[2] + 7
                if not PRIMARY.size + SECONDARY.size <= length <= MAX_PACKET:
                    self.buffer.clear()
                    raise ProtocolError("invalid stream frame length")
                if len(self.buffer) == length:
                    raw = bytes(self.buffer)
                    self.buffer.clear()
                    packets.append(Packet.decode(raw))
        return packets
