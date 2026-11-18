import socket
import threading
import time
import unittest
from interfaces.framing import read_packet


class FramingTests(unittest.TestCase):
    def test_trickle_cannot_extend_absolute_command_deadline(self):
        sender, receiver = socket.socketpair()
        receiver.settimeout(0.15)
        def trickle():
            with sender:
                try:
                    for value in b'\x19\x00\xc0\x00\x00\x0d':
                        sender.sendall(bytes([value]))
                        time.sleep(0.06)
                except OSError:
                    pass
        thread = threading.Thread(target=trickle)
        thread.start()
        started = time.monotonic()
        with receiver, self.assertRaises(TimeoutError):
            read_packet(receiver)
        self.assertLess(time.monotonic() - started, 0.3)
        thread.join(timeout=1)
