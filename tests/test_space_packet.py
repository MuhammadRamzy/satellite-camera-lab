import random
import struct
import unittest
from interfaces.space_packet import (Command, COMMAND_APID, Packet, ProtocolError,
                                     SequenceTracker, StreamDecoder, validate_command)


def ping(seq=0, cid=1):
    return Packet(COMMAND_APID, seq, True, Command.PL_PING, cid, 0)


class PacketTests(unittest.TestCase):
    def test_known_wire_vector(self):
        # Independent expected primary header: TC=1, secondary=1, APID=256;
        # unsegmented sequence=0; data length 14 minus 1 = 13.
        expected = bytes.fromhex("1900c000000d0101000000010000000000000000")
        self.assertEqual(ping().encode(), expected)
        self.assertEqual(Packet.decode(expected), ping())

    def test_roundtrip_sequence_wrap(self):
        tracker = SequenceTracker()
        for seq in (16383, 0, 1):
            p = ping(seq)
            tracker.accept(Packet.decode(p.encode()))
        with self.assertRaises(ProtocolError):
            tracker.accept(ping(1))
        with self.assertRaises(ProtocolError):
            tracker.accept(ping(4))
        tracker.accept(ping(2))  # rejection must not move sequence state

    def test_bad_primary_and_mission_headers(self):
        raw = ping().encode()
        variants = [raw[:5], raw[:-1], raw + b"x"]
        for index, value in ((0, 0x39), (0, 0x11), (2, 0), (6, 2), (5, 12)):
            bad = bytearray(raw)
            bad[index] = value
            variants.append(bytes(bad))
        for bad in variants:
            with self.subTest(raw=bad), self.assertRaises(ProtocolError):
                Packet.decode(bad)

    def test_stream_fragmentation_and_coalescence(self):
        wire = b"".join(ping(i).encode() for i in range(100))
        rng, decoder, decoded = random.Random(42), StreamDecoder(), []
        while wire:
            n = rng.randrange(1, 90)
            decoded.extend(decoder.feed(wire[:n]))
            wire = wire[n:]
        self.assertEqual([p.sequence for p in decoded], list(range(100)))
        self.assertFalse(decoder.buffer)

    def test_stream_size_rejected_at_header(self):
        decoder = StreamDecoder()
        with self.assertRaises(ProtocolError):
            decoder.feed(bytes.fromhex("1900c000ffff"))
        self.assertFalse(decoder.buffer)

    def test_every_command_has_exact_length(self):
        bodies = {3: b"\x02", 5: struct.pack("!HI", 5, 1000),
                  6: struct.pack("!HHBBIBB", 640, 480, 5, 75, 10000, 3, 0),
                  7: struct.pack("!IH", 0, 32), 8: bytes(16), 9: bytes(16)}
        for cmd in Command:
            p = Packet(COMMAND_APID, 0, True, cmd, 1, 0, bodies.get(cmd, b""))
            self.assertEqual(validate_command(p), cmd)
            with self.assertRaises(ProtocolError):
                validate_command(Packet(COMMAND_APID, 0, True, cmd, 1, 0, p.payload + b"x"))

    def test_bad_command_values(self):
        for op, body in [(99, b""), (3, b"\xff"), (5, struct.pack("!HI", 0, 1000)),
                         (5, struct.pack("!HI", 5, 0)), (7, struct.pack("!IH", 0, 33)),
                         (6, struct.pack("!HHBBIBB", 640, 480, 0, 75, 10000, 0, 0))]:
            with self.subTest(op=op), self.assertRaises(ProtocolError):
                validate_command(Packet(COMMAND_APID, 0, True, op, 1, 0, body))
        for p in [ping(cid=0), Packet(1, 0, True, 1, 1, 0), Packet(256, 0, False, 1, 1, 0)]:
            with self.assertRaises(ProtocolError):
                validate_command(p)

    def test_encoder_limits(self):
        for p in [ping(-1), ping(16384), Packet(2047, 0, True, 1, 1, 0),
                  Packet(256, 0, True, 1, 1, 0, bytes(4096))]:
            with self.assertRaises(ProtocolError):
                p.encode()
