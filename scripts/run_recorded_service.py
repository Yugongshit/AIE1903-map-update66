"""Serve a trusted ScenarioNet recording to a synchronous Planning coordinator."""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from map_update.recorded_service import RecordedHTTPServer, RecordedService
from map_update.recorded_takeover import RecordedTakeoverSession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--index', type=int, default=0)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8094)
    parser.add_argument('--access-key-env', default='RECORDED_SERVICE_KEY')
    parser.add_argument('--history-frames', type=int, default=30)
    parser.add_argument('--action-timeout', type=float, default=30.)
    args = parser.parse_args()
    key = os.environ.get(args.access_key_env)
    if not key:
        parser.error('Set ' + args.access_key_env + ' in the server and trusted coordinator environment')
    args.out.mkdir(parents=True, exist_ok=False)
    with (args.out / 'events.jsonl').open('x') as journal:
        session = RecordedTakeoverSession(args.database, args.index)
        try:
            service = RecordedService(session, journal, history_frames=args.history_frames,
                                      action_timeout=args.action_timeout)
            with RecordedHTTPServer((args.host, args.port), service, key) as server:
                print(f'Recorded service ready on port {server.server_port}; scene {session.scenario["id"]}', flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
        finally:
            session.close()


if __name__ == '__main__':
    main()
