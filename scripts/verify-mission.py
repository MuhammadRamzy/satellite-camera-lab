#!/usr/bin/env python3
"""Real multi-process socket/ROS mission acceptance; no fabricated receipts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import time

from PIL import Image
from foundations.ground import call
from interfaces.file_link import digest
from interfaces.framing import read_packet
from interfaces.space_packet import Command, Packet

ROOT = Path('/workspace')
EVIDENCE = ROOT / 'docs/evidence/mission'


class Experiment:
    def __init__(self, through, clear_gates=True):
        self.through = through
        self.run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.directory = ROOT / 'data' / ('mission_' + self.run_id)
        self.logs = EVIDENCE / 'runs' / self.run_id
        self.directory.mkdir(parents=True)
        self.logs.mkdir(parents=True)
        self.processes, self.cases, self.commands = {}, [], []
        if clear_gates:
            for phase in range(4, through + 1):
                (EVIDENCE / f'phase{phase}-result.json').unlink(missing_ok=True)

    def start(self, name, args):
        handle = (self.logs / (name + '.log')).open('a')
        process = subprocess.Popen(args, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)
        self.processes[name] = (process, handle)
        return process

    def stop(self, name, kill=False):
        process, handle = self.processes.pop(name)
        if process.poll() is None:
            process.send_signal(signal.SIGKILL if kill else signal.SIGINT)
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
                raise AssertionError(f'{name} failed clean shutdown')
        handle.close()
        if not kill:
            assert process.returncode in (0, -signal.SIGINT), (name, process.returncode)

    def wait(self, condition, timeout=20):
        deadline = time.monotonic() + timeout
        while True:
            for name, (process, _) in self.processes.items():
                if process.poll() is not None:
                    raise AssertionError(f'{name} exited {process.returncode}; see {self.logs}')
            try:
                result = condition()
                if result:
                    return result
            except (ConnectionError, TimeoutError, EOFError):
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError('mission condition timed out')
            time.sleep(0.1)

    def cmd(self, op, expected='COMPLETED', **values):
        request = {'op': op, **values}
        started = time.monotonic()
        result = call(request)
        self.commands.append({'request': request, 'response': result,
                              'duration_ms': (time.monotonic() - started) * 1000})
        if expected is not None:
            assert result['status'] == expected, (request, result)
        return result

    def rows(self):
        path = self.directory / 'payload/catalogue.db'
        if not path.exists():
            return []
        connection = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        connection.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in connection.execute('SELECT * FROM images ORDER BY id')]
        finally:
            connection.close()

    def ready(self):
        self.wait(lambda: self.cmd('status', expected=None).get('gateway_connected'))
        self.cmd('spacecraft', mode=1, imaging_permitted=True)
        self.cmd('recover')
        self.cmd('mode', mode=2)
        self.wait(lambda: self.cmd('status')['mode'] == 2)

    def case(self, test_id, initial, injection, expected, recovery, function):
        record = dict(test_id=test_id, initial_state=initial, injected_fault=injection,
                      expected=expected, recovery_action=recovery, recovery_ms=None)
        started = time.monotonic()
        try:
            observed = function()
            record.update(observed=observed, result='PASS')
        except Exception as error:
            record.update(observed=f'{type(error).__name__}: {error}', result='FAIL')
            raise
        finally:
            record['duration_ms'] = (time.monotonic() - started) * 1000
            self.cases.append(record)
            (self.logs / 'cases.json').write_text(json.dumps(self.cases, indent=2) + '\n')
            print(json.dumps(record), flush=True)

    def checkpoint(self, phase):
        report = {'result': 'PENDING_SHUTDOWN', 'phase': phase, 'run_id': self.run_id,
                  'directory': str(self.directory.relative_to(ROOT)),
                  'logs': str(self.logs.relative_to(ROOT)), 'cases': self.cases.copy(),
                  'base_image': json.loads((ROOT / 'config/image-lock.json').read_text()),
                  'image_count': len(self.rows())}
        (EVIDENCE / f'phase{phase}-result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps({'phase': phase, 'result': 'PASS'}), flush=True)

    def run(self):
        self.start('payload', ['ros2', 'launch', 'payload_camera', 'pipeline.launch.py',
                              f'data_dir:={self.directory / "payload"}', 'bus_required:=true'])
        self.start_gateway()
        time.sleep(1)

        def wire():
            # The independent primary-header vector test is in host tests.
            # This checks actual TCP fragmentation/coalescence and close policy.
            with self.wait(lambda: socket.create_connection(('127.0.0.1', 47001), timeout=5)) as peer:
                peer.settimeout(5)
                ping = Packet(256, 16383, True, 1, 1, 0).encode()
                for byte in ping:
                    peer.sendall(bytes([byte]))
                assert json.loads(read_packet(peer).payload)['status'] == 'COMPLETED'
                peer.sendall(Packet(256, 0, True, 1, 2, 0).encode() + Packet(256, 1, True, 1, 3, 0).encode())
                assert read_packet(peer).command_id == 2
                assert read_packet(peer).command_id == 3
                peer.sendall(Packet(256, 1, True, 1, 4, 0).encode())
                assert peer.recv(1) == b''  # sequence replay closes session
            with socket.create_connection(('127.0.0.1', 47001), timeout=5) as peer:
                peer.settimeout(5)
                peer.sendall(bytes.fromhex('1900c000ffff'))
                assert peer.recv(1) == b''
            return 'fragmented/coalesced TCP, 16383->0 wrap, sequence replay and oversized-header close verified'

        self.case('P4-01', 'payload booting', 'fragmented/coalesced, replay/oversized frames',
                  'valid ACKs; invalid sessions close', 'fresh session', wire)
        self.start('bus', ['python3', '-m', 'foundations.bus', '--root', str(self.directory / 'bus')])
        self.wait(lambda: self.cmd('status').get('state_fresh') and self.cmd('status').get('gateway_connected'))

        def dispatch():
            # Raw wire tests use IDs 1..3; bus must not reuse them.
            self.cmd('ping', command_id=100)
            self.cmd('capture', expected='ERROR')
            self.cmd('spacecraft', mode=1, imaging_permitted=True)
            self.cmd('recover')
            self.cmd('configure', width=640, height=480, quality=75)
            self.ready()
            first = self.cmd('capture', expected='ACCEPTED')
            cid = first['command_id']
            self.wait(lambda: any(row['id'] == first['capture_id'] for row in self.rows()))
            again = self.cmd('capture', expected='ACCEPTED', command_id=cid)
            assert first == again
            self.cmd('safe', expected='ERROR', command_id=cid)
            count = len(self.rows())
            self.cmd('reconnect')
            self.wait(lambda: self.cmd('status')['mode'] == 4)
            self.cmd('capture', expected='ERROR')
            self.stop('gateway', kill=True)
            self.start_gateway()
            self.wait(lambda: self.cmd('status', expected=None).get('state_fresh'))
            replay = self.cmd('capture', expected='ACCEPTED', command_id=cid)
            assert replay == first and len(self.rows()) == count
            # Bus IDs/queue survive process restart and reconnect is inhibited.
            self.stop('bus', kill=True)
            self.start('bus', ['python3', '-m', 'foundations.bus', '--root', str(self.directory / 'bus')])
            self.wait(lambda: self.cmd('status', expected=None).get('mode') == 4)
            new_id = self.cmd('ping')['command_id']
            assert new_id > cid
            self.ready()
            return f'capture {first["capture_id"]} ACK replayed before/after gateway SIGKILL; conflict rejected; bus SIGKILL preserved increasing IDs'

        self.case('P4-02', 'STANDBY/inhibited', 'duplicate/conflicting IDs, reconnect, gateway/bus SIGKILL',
                  'one exposure; durable replay; SAFE and explicit recovery', 'restart/recover', dispatch)
        self.case('P4-03', 'READY', 'unsupported directory/V4L2 configuration',
                  'explicit rejection, no silent source fallback', 'keep synthetic source', self.unsupported_source)
        self.checkpoint(4)
        if self.through == 4:
            return
        self.start('ground', ['python3', '-m', 'foundations.ground', 'receive',
                              '--root', str(self.directory / 'ground')])
        time.sleep(0.2)
        image = self.rows()[0]

        def transfer_faults():
            self.cmd('delete', expected='ERROR', id=image['id'])
            self.cmd('confirm', expected='ERROR', id=image['id'], sha256='0' * 64)
            self.cmd('mode', mode=3)
            self.cmd('capture', expected='ERROR')
            self.cmd('fetch', id=image['id'])
            first = self.cmd('downlink', expected='INTERRUPTED', link={
                'rate_mbps': 0.5, 'window': 60, 'interrupt_after': 2})
            partial = self.directory / 'ground' / (image['id'] + '.part')
            assert partial.stat().st_size == 8192
            assert self.rows()[0]['downlink_status'] == 'PENDING'
            self.stop('ground', kill=True)
            self.start('ground', ['python3', '-m', 'foundations.ground', 'receive',
                                  '--root', str(self.directory / 'ground')])
            time.sleep(0.2)
            second = self.cmd('downlink', link={'rate_mbps': 0.5, 'window': 60,
                'loss': 0.1, 'seed': 1, 'corrupt_once': True})
            metrics = second['metrics']
            assert metrics['resumed_bytes'] == 8192 and metrics['retransmissions'] > 0
            assert metrics['wall_seconds'] >= metrics['attempted_wire_bytes'] * 8 / 500000
            assert digest(self.directory / 'ground' / (image['id'] + '.jpg')) == image['sha256']
            assert next(row for row in self.rows() if row['id'] == image['id'])['downlink_status'] == 'DELIVERED'
            self.cmd('delete', id=image['id'])
            assert not any(row['id'] == image['id'] for row in self.rows())
            return {'first_pass': first, 'resumed_pass': second,
                    'verified': 'ground SIGKILL/restart resumes; CRC corruption retried; wrong receipt and premature deletion rejected; deletion after ground confirmation succeeds'}

        self.case('P5-01', 'one pending JPEG', 'interrupted pass, ground SIGKILL, bad CRC and 10% loss',
                  'durable resume, rate cap, end-to-end checksum and guarded deletion', 'next pass/restart', transfer_faults)
        self.checkpoint(5)
        if self.through == 5:
            return

        def mission():
            self.cmd('mode', mode=1)
            self.cmd('configure', width=640, height=480, fps=1, quality=75)
            self.cmd('mode', mode=2)
            before = {row['id'] for row in self.rows()}
            burst = self.cmd('burst', expected='ACCEPTED', count=5, interval_ms=1000)
            self.wait(lambda: self.cmd('status')['jobs'].get(burst['job'], {}).get('status') == 'COMPLETED', timeout=15)
            new = [row for row in self.rows() if row['id'] not in before]
            assert len(new) == 5
            self.cmd('mode', mode=3)
            for row in new:
                self.cmd('fetch', id=row['id'])
            downlink = self.cmd('downlink', link={'rate_mbps': 1, 'window': 60, 'loss': 0, 'latency_ms': 5})
            assert len(downlink['receipts']) == 5
            metrics = downlink['metrics']
            assert metrics['wall_seconds'] >= metrics['attempted_wire_bytes'] * 8 / 1000000
            verified = []
            for row in new:
                source = self.directory / 'payload' / (row['id'] + '.jpg')
                ground = self.directory / 'ground' / (row['id'] + '.jpg')
                assert digest(source) == digest(ground) == row['sha256']
                with Image.open(ground) as decoded:
                    decoded.load()
                    assert decoded.size == (640, 480) and decoded.mode == 'RGB'
                verified.append({'id': row['id'], 'sha256': row['sha256'], 'bytes': row['size'],
                                 'ground': str(ground.relative_to(ROOT))})
            rows = self.rows()
            assert all(row['downlink_status'] == 'DELIVERED' for row in rows)
            self.cmd('mode', mode=1)
            self.wait(lambda: self.cmd('status')['pending'] == 0)
            return {'images': verified, 'link': downlink, 'final_mode': 'STANDBY', 'pending': 0}

        self.case('P6-01', 'ready synthetic payload and separate bus/ground', 'five-image nominal mission',
                  'five independent ground decodes/hashes; propagated receipts; retained payload originals', 'STANDBY', mission)
        self.checkpoint(6)

    def start_gateway(self):
        self.start('gateway', [str(ROOT / 'install/payload_gateway/lib/payload_gateway/gateway_node'), '--ros-args',
                              '-p', f'data_dir:={self.directory / "payload"}'])

    def unsupported_source(self):
        self.cmd('mode', mode=1)
        for source in (1, 2):
            result = self.cmd('configure', expected='ERROR', source=source)
            assert 'synthetic' in result['reason']
        self.cmd('mode', mode=2)
        return 'source selectors 1 and 2 explicitly rejected; no hardware adapter is claimed'

    def close(self):
        failures = []
        for name in ('ground', 'bus', 'gateway', 'payload'):
            if name in self.processes:
                try:
                    self.stop(name)
                except Exception as error:
                    failures.append(str(error))
        (self.logs / 'commands.json').write_text(json.dumps(self.commands, indent=2) + '\n')
        for phase in range(4, self.through + 1):
            path = EVIDENCE / f'phase{phase}-result.json'
            if path.exists():
                report = json.loads(path.read_text())
                if report.get('run_id') == self.run_id:
                    report['result'] = 'FAIL' if failures else 'PASS'
                    report['clean_shutdown'] = not failures
                    path.write_text(json.dumps(report, indent=2) + '\n')
        if failures:
            raise AssertionError(failures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--through', type=int, choices=(4, 5, 6), default=6)
    args = parser.parse_args()
    experiment = Experiment(args.through)
    try:
        experiment.run()
    finally:
        experiment.close()


if __name__ == '__main__':
    main()
