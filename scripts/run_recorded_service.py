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
    parser.add_argument('--recover', type=Path, help='Verify and replay the latest session from a journal')
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
    from metadrive.scenario.utils import read_dataset_summary
    summary, lookup, mapping = read_dataset_summary(str(args.database))
    catalog = [dict(index=index, name=name, scene_id=summary[name].get('scenario_id'),
                    frames=int(summary[name].get('track_length', len(summary[name].get('ts', [])))))
               for index, name in enumerate(lookup)]
    recovery = None
    if args.recover:
        from map_update.recorded_recovery import latest_run
        recovery, source = latest_run(args.recover)
        if Path(source['database']).resolve() != args.database.resolve():
            parser.error('Recovery database path differs from the recorded source')
        args.index = source['index']
    if not 0 <= args.index < len(catalog):
        parser.error('Scene index is outside the trusted catalog')
    args.out.mkdir(parents=True, exist_ok=False)
    with (args.out / 'events.jsonl').open('x') as journal:
        session = RecordedTakeoverSession(args.database, args.index)
        try:
            if recovery:
                for _ in range(recovery[0]['data']['step']):
                    session.advance({}, observation_step=session.step)
            service = RecordedService(session, journal, history_frames=args.history_frames,
                                      action_timeout=args.action_timeout)
            if recovery:
                from map_update.recorded_recovery import recover_commands
                recover_commands(service, recovery)
            with RecordedHTTPServer((args.host, args.port), service, key,
                                    session_factory=lambda index: RecordedTakeoverSession(args.database, index),
                                    catalog=catalog) as server:
                print(f'Recorded service ready on port {server.server_port}; scene {session.scenario["id"]}', flush=True)
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
        finally:
            if 'server' in locals():
                server.service.session.close()
            else:
                session.close()


if __name__ == '__main__':
    main()
