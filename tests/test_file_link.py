import hashlib
from pathlib import Path
import socket
import tempfile
import threading
import unittest
import uuid

from interfaces.file_link import Link, digest, receive_file, send_file, validate_manifest


class FileLinkTests(unittest.TestCase):
    def transfer(self, source, destination, manifest, link, fails=False):
        sender, receiver = socket.socketpair()
        failures = []
        def receive():
            with receiver:
                try:
                    receive_file(receiver, destination)
                except (EOFError, OSError, ValueError) as error:
                    failures.append(error)
        thread = threading.Thread(target=receive)
        thread.start()
        try:
            with sender:
                sender.settimeout(2)
                if fails:
                    with self.assertRaises((ConnectionAbortedError, TimeoutError, EOFError)):
                        send_file(sender, source, manifest, link)
                else:
                    self.assertEqual(send_file(sender, source, manifest, link)['sha256'], manifest['sha256'])
        finally:
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        if not fails:
            self.assertFalse(failures, failures)

    def test_interrupt_resume_crc_loss_rate_and_receipt_replay(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'source.jpg'
            data = bytes(range(256)) * 160
            source.write_bytes(data)
            manifest = {'id': str(uuid.uuid4()), 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            destination = root / 'ground'
            first = Link(interrupt_after=2)
            self.transfer(source, destination, manifest, first, fails=True)
            self.assertEqual((destination / (manifest['id'] + '.part')).stat().st_size, 8192)
            second = Link(rate_mbps=4, loss=0.1, seed=1, corrupt_once=True)
            self.transfer(source, destination, manifest, second)
            self.assertEqual(second.resumed_bytes, 8192)
            self.assertGreater(second.retransmissions, 0)
            self.assertGreater(second.lost_packets, 0)
            self.assertGreaterEqual(second.metrics()['wall_seconds'], second.wire_bytes / second.rate)
            self.assertEqual(digest(destination / (manifest['id'] + '.jpg')), manifest['sha256'])
            replay = Link()
            self.transfer(source, destination, manifest, replay)
            self.assertEqual(replay.payload_bytes, 0)

    def test_pass_budget_and_identity_guards(self):
        with self.assertRaises(TimeoutError):
            Link(rate_mbps=0.5, window=0.001).charge(1000)
        for manifest in ({'id': '../x', 'size': 1, 'sha256': '0' * 64},
                         {'id': str(uuid.uuid4()), 'size': 0, 'sha256': '0' * 64}):
            with self.assertRaises(ValueError):
                validate_manifest(manifest)
