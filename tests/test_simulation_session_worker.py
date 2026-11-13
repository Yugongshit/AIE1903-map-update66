"""Real worker framing: large verified map launches and correlated failures."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class WorkerProtocolTests(unittest.TestCase):
    def test_map_payload_is_accepted_and_oversize_control_errors_keep_job_id(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            messages = [
                {'id': 'large-map', 'command': 'open', 'database_path': directory + '/missing',
                 'fleet': {'maps': {'original': {'payload': 'x' * 70000}}}},
                {'id': 'large-control', 'command': 'frame', 'payload': 'x' * 70000},
                {'id': 'close', 'command': 'close'},
            ]
            process = subprocess.run([sys.executable, str(root/'scripts/simulation_session_worker.py'),
                                      '--output-dir', directory],
                                     input=''.join(json.dumps(row)+'\n' for row in messages),
                                     capture_output=True, text=True, timeout=20, cwd=root)
            self.assertEqual(process.returncode, 0, process.stderr[-2000:])
            responses = [json.loads(line) for line in process.stdout.splitlines()]
            self.assertEqual([row['id'] for row in responses], ['large-map', 'large-control', 'close'])
            self.assertFalse(responses[0]['ok'])
            self.assertNotEqual(responses[0]['error'], 'Session command is too large')
            self.assertEqual(responses[1]['error'], 'Session command is too large')
            self.assertTrue(responses[2]['ok'])


if __name__ == '__main__':
    unittest.main()
