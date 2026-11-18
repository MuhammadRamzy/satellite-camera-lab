"""Ground receiver and operator CLI; uses only mission sockets, never DDS."""
import argparse
import json
from pathlib import Path
import socket

from interfaces.file_link import receive_file
import uuid
from interfaces.framing import read_frame, send_frame


def call(request, port=47003, timeout=660):
    with socket.create_connection(('127.0.0.1', port), timeout=5) as peer:
        peer.settimeout(timeout)
        send_frame(peer, request)
        return read_frame(peer)[0]


def receive(root, port, sessions=False):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(('127.0.0.1', port))
        listener.listen(4)
        while True:
            peer, _ = listener.accept()
            with peer:
                peer.settimeout(15)
                try:
                    destination = root
                    if sessions:
                        request, _ = read_frame(peer)
                        run_id = request['id']
                        if request.get('type') != 'SESSION' or str(uuid.UUID(run_id)) != run_id:
                            raise ValueError('canonical benchmark session UUID required')
                        destination = root / run_id
                        send_frame(peer, {'type': 'SESSION_READY', 'id': run_id})
                    receipt = receive_file(peer, destination)
                    print(json.dumps({'event': 'ground_verified', **receipt}), flush=True)
                except (OSError, EOFError, ValueError, KeyError, TypeError, AttributeError) as error:
                    print(json.dumps({'event': 'transfer_interrupted', 'reason': str(error)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    receiver = sub.add_parser('receive')
    receiver.add_argument('--root', required=True)
    receiver.add_argument('--port', type=int, default=47004)
    receiver.add_argument('--sessions', action='store_true', help='isolated benchmark destinations')
    operator = sub.add_parser('command')
    operator.add_argument('json', help='JSON object, e.g. {"op":"status"}')
    operator.add_argument('--port', type=int, default=47003)
    args = parser.parse_args()
    try:
        if args.action == 'receive':
            receive(args.root, args.port, args.sessions)
        else:
            print(json.dumps(call(json.loads(args.json), args.port), indent=2))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
