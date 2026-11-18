"""Durable at-most-once dispatch fence; uncertain effects remain IN_DOUBT."""
import hashlib
import json
import sqlite3


class Journal:
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS commands (id INTEGER PRIMARY KEY, fingerprint TEXT, response TEXT)')
        self.db.commit()

    def execute(self, packet, dispatch):
        # UTC and session sequence are transport metadata, not command identity.
        fingerprint = hashlib.sha256(bytes([packet.opcode]) + packet.payload).hexdigest()
        row = self.db.execute('SELECT fingerprint,response FROM commands WHERE id=?', (packet.command_id,)).fetchone()
        if row:
            if row[0] != fingerprint:
                return {'status': 'ERROR', 'reason': 'COMMAND_ID_CONFLICT'}
            if row[1] is None:
                return {'status': 'IN_DOUBT', 'reason': 'dispatch outcome uncertain; reconcile before new command'}
            return json.loads(row[1])
        with self.db:
            self.db.execute('INSERT INTO commands VALUES(?,?,NULL)', (packet.command_id, fingerprint))
        # A process crash/timeout here MUST NOT cause redispatch on reconnect.
        response = dispatch(packet)
        encoded = json.dumps(response, allow_nan=False, separators=(',', ':'))
        with self.db:
            self.db.execute('UPDATE commands SET response=? WHERE id=?', (encoded, packet.command_id))
        return response

    def close(self):
        self.db.close()
