"""Serial JSON-line worker; MetaDrive runs on this process's main thread."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--width', type=int, default=1000)
    parser.add_argument('--height', type=int, default=650)
    args = parser.parse_args()
    # Reserve the parent's stdout pipe for protocol messages. This also routes
    # native-library stdout diagnostics to stderr, not only Python print calls.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), 'w', encoding='utf-8', buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    from map_update.simulation_session import SimulationSession
    session = SimulationSession(args.output_dir, width=args.width, height=args.height)
    try:
        for line in sys.stdin:
            identifier = None
            message = None
            try:
                message = json.loads(line)
                identifier = message.get('id') if isinstance(message, dict) else None
                # HTTP controls remain capped at 32 KiB. The trusted broker
                # adds both verified maps to a fleet launch after validation.
                # Keep the job ID on errors so the broker never waits for a
                # response that it cannot associate with its request.
                fleet_open = isinstance(message, dict) and message.get('command') == 'open' and message.get('fleet') is not None
                limit = 32 * 1024 * 1024 if fleet_open else 65536
                if len(line.encode('utf-8')) > limit:
                    raise ValueError('Session command is too large')
                result = session.command(message)
                response = {'id': identifier, 'ok': True, 'result': result}
            except Exception as error:
                response = {'id': identifier, 'ok': False, 'error': str(error), 'error_type': type(error).__name__}
            protocol.write(json.dumps(response, ensure_ascii=False, allow_nan=False) + '\n')
            protocol.flush()
            if isinstance(message, dict) and message.get('command') == 'close':
                break
    finally:
        session.close()
        protocol.close()


if __name__ == '__main__':
    main()
