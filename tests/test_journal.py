from pathlib import Path
import tempfile
import unittest
from foundations.command_journal import Journal
from interfaces.space_packet import Packet


class JournalTests(unittest.TestCase):
    def test_replay_conflict_restart_and_uncertain_effect(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'commands.db'
            journal = Journal(path)
            effects = []
            packet = Packet(256, 0, True, 4, 1, 0)
            def dispatch(_):
                effects.append('exposure')
                return {'status': 'ACCEPTED', 'capture_id': 'one'}
            self.assertEqual(journal.execute(packet, dispatch)['capture_id'], 'one')
            journal.close()
            journal = Journal(path)
            self.assertEqual(journal.execute(packet, dispatch)['capture_id'], 'one')
            self.assertEqual(len(effects), 1)
            conflict = Packet(256, 1, True, 11, 1, 5)
            self.assertEqual(journal.execute(conflict, dispatch)['reason'], 'COMMAND_ID_CONFLICT')
            def crash(_):
                effects.append('uncertain exposure')
                raise RuntimeError('crash after side effect')
            uncertain = Packet(256, 2, True, 4, 2, 10)
            with self.assertRaises(RuntimeError):
                journal.execute(uncertain, crash)
            journal.close()
            journal = Journal(path)
            self.assertEqual(journal.execute(uncertain, dispatch)['status'], 'IN_DOUBT')
            self.assertEqual(len(effects), 2)
            journal.close()
