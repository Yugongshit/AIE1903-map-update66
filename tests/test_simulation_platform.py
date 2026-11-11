"""HTTP boundaries and async engine ownership, without running a renderer."""
from http.client import HTTPConnection
import json
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from map_update.simulation_platform import PlatformServer, EngineBroker, BusyError


def png_pixel(red=0):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('!2I5B', 1, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes([0, red, 0, 0]))) + chunk(b'IEND', b''))


class PlatformHttpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'map_update').mkdir()
        (self.root / 'map_update/simulation_platform.html').write_text('<title>Map review</title>')
        self.server = PlatformServer(('127.0.0.1', 0), self.root)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, method='GET', body=None, headers=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        data = json.dumps(body) if body is not None else None
        conn.request(method, path, body=data, headers={'Content-Type': 'application/json', **(headers or {})})
        response = conn.getresponse()
        status, headers, content = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return status, headers, content

    def test_only_platform_assets_are_served(self):
        self.assertEqual(self.request('/')[0], 200)
        (self.root / 'private.txt').write_text('private')
        for path in ('/private.txt', '/../private.txt', '/%2e%2e/private.txt', '/.git/config'):
            self.assertEqual(self.request(path)[0], 404)

    def test_current_planning_download_uses_requested_version_and_step(self):
        with patch('map_update.simulation_platform_data.planning_bundle', return_value={'step': 7, 'route': None}) as build:
            status, headers, content = self.request('/api/scenes/example/planning?step=7&version=original')
        self.assertEqual(status, 200)
        build.assert_called_once_with(self.root, 'example', 7, 'original')
        self.assertEqual(json.loads(content)['route'], None)
        self.assertIn('frame7.json', headers['Content-Disposition'])
        for query in ('step=-1', 'step=1.5', 'step=1&step=2', 'version=anything'):
            self.assertEqual(self.request('/api/scenes/example/planning?' + query)[0], 400)

    def test_engine_paths_are_selected_from_catalog_not_http(self):
        with patch('map_update.simulation_platform_data.scene_database', return_value=self.root/'allowed') as resolve:
            with patch.object(self.server.engine, 'submit', return_value={'job_id': 'job'}) as submit:
                status, _, body = self.request('/api/engine/open', 'POST',
                    {'scene_id': 'example', 'version': 'updated', 'database_path': '/untrusted'})
        self.assertEqual(status, 202)
        resolve.assert_called_once_with(self.root, 'example', 'updated')
        self.assertEqual(submit.call_args.args[0]['database_path'], str(self.root/'allowed'))
        self.assertEqual(json.loads(body)['job_id'], 'job')

    def test_cross_origin_and_arbitrary_commands_cannot_control_engine(self):
        with patch.object(self.server.engine, 'submit') as submit:
            for command in ({'command': 'exec', 'code': 'anything'},
                            {'command': 'seek', 'step': 4, 'database_path': '/untrusted'}):
                self.assertEqual(self.request('/api/engine/command', 'POST', command)[0], 400)
            self.assertEqual(self.request('/api/engine/open', 'POST', {'scene_id': 'x'},
                                          {'Origin': 'https://other.example'})[0], 400)
            self.assertEqual(self.request('/api/engine/open', 'POST', {'scene_id': 'x'},
                                          {'Content-Type': 'text/plain'})[0], 400)
            submit.assert_not_called()

    def test_busy_seek_does_not_accept_an_overlapping_mutation(self):
        with patch.object(self.server.engine, 'status', return_value={'ready': True}):
            with patch.object(self.server.engine, 'submit', side_effect=BusyError('busy')):
                self.assertEqual(self.request('/api/engine/command', 'POST', {'command': 'seek', 'step': 10})[0], 409)

    def test_http_serves_the_requested_immutable_image_after_playback_advances(self):
        broker = self.server.engine
        broker.state.update(scene_id='example', version='updated')
        (broker.root/'frame-1.png').write_bytes(png_pixel(10))
        broker._publish_result({'sequence': 1, 'frame_file': 'frame-1.png'})
        first = broker.status()['last_result']['frame_id']
        (broker.root/'frame-2.png').write_bytes(png_pixel(20))
        broker._publish_result({'sequence': 2, 'frame_file': 'frame-2.png'})
        (broker.root/'frame-1.png').unlink()
        query = '?scene_id=example&version=updated&sequence=1&frame_id=' + first
        status, headers, body = self.request('/api/engine/frame.png' + query)
        self.assertEqual(status, 200)
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(body, png_pixel(10))
        self.assertEqual(self.request('/api/engine/frame.png' + query.replace('sequence=1', 'sequence=2'))[0], 400)
        missing_id = self.request('/api/engine/frame.png?scene_id=example&version=updated&sequence=1')
        self.assertEqual(missing_id[0], 400)
        self.assertIn('刷新', json.loads(missing_id[2])['error'])


class EngineBrokerTests(unittest.TestCase):
    def test_frame_cache_is_bounded_and_rejects_expired_ids_without_substitution(self):
        with tempfile.TemporaryDirectory() as root:
            broker = EngineBroker(root)
            try:
                broker.state.update(scene_id='a', version='updated')
                ids = []
                for seq in range(1, 11):
                    (broker.root/'frame.png').write_bytes(png_pixel(seq))
                    broker._publish_result({'sequence': seq, 'frame_file': 'frame.png'})
                    ids.append(broker.status()['last_result']['frame_id'])
                self.assertEqual(len(broker.frames), broker.FRAME_CACHE_LIMIT)
                self.assertEqual(broker.frame_bytes, sum(len(row['data']) for row in broker.frames.values()))
                with self.assertRaisesRegex(ValueError, 'no longer'):
                    broker.frame(scene_id='a', version='updated', sequence=1, frame_id=ids[0])
                self.assertEqual(broker.frame(scene_id='a', version='updated', sequence=3, frame_id=ids[2]), png_pixel(3))
                with self.assertRaisesRegex(ValueError, 'different scene'):
                    broker.frame(scene_id='b', version='updated', frame_id=ids[-1])
            finally:
                broker.close()

    def test_invalid_or_incomplete_capture_is_not_published_as_ready(self):
        with tempfile.TemporaryDirectory() as root:
            broker = EngineBroker(root)
            try:
                for data in (b'', b'not an image', png_pixel()[:-12]):
                    (broker.root/'frame.png').write_bytes(data)
                    with self.assertRaisesRegex(ValueError, 'PNG'):
                        broker._publish_result({'sequence': 1, 'frame_file': 'frame.png'})
                    self.assertFalse(broker.status()['ready'])
                    self.assertFalse(broker.frames)
            finally:
                broker.close()

    def test_restart_removes_old_numbered_files_and_never_reuses_frame_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'scripts').mkdir()
            # Match the real worker's sequence reset and eight-file cleanup.
            (root/'scripts/simulation_session_worker.py').write_text(
                "import json,sys\nfrom pathlib import Path\n"
                "root=Path(sys.argv[sys.argv.index('--output-dir')+1])\n"
                "for line in sys.stdin:\n"
                " message=json.loads(line)\n"
                " path=root/'frame-00000001.png'\n"
                f" path.write_bytes({png_pixel()!r})\n"
                " for old in sorted(root.glob('frame-*.png'))[:-8]:old.unlink()\n"
                " print(json.dumps({'id':message['id'],'ok':True,'result':{'sequence':1,'frame_file':path.name}}),flush=True)\n")
            broker = EngineBroker(root)
            try:
                broker.submit({'command': 'open'}, scene_id='a', version='updated')
                self.wait_job(broker)
                old_id = broker.status()['last_result']['frame_id']
                broker._stop_process()
                for seq in range(45, 53):
                    (broker.root/f'frame-{seq:08}.png').write_bytes(png_pixel(seq))
                broker.submit({'command': 'open'}, scene_id='a', version='updated')
                self.wait_job(broker)
                new_id = broker.status()['last_result']['frame_id']
                self.assertNotEqual(new_id, old_id)
                self.assertTrue((broker.root/'frame-00000001.png').is_file())
                self.assertEqual(broker.frame(scene_id='a', version='updated', sequence=1, frame_id=new_id), png_pixel())
                with self.assertRaisesRegex(ValueError, 'no longer'):
                    broker.frame(scene_id='a', version='updated', sequence=1, frame_id=old_id)
            finally:
                broker.close()

    def test_other_tab_cannot_relabel_a_different_scene_or_map(self):
        with tempfile.TemporaryDirectory() as root:
            broker = EngineBroker(root)
            try:
                broker.state.update(scene_id='scene-b', version='updated')
                with self.assertRaisesRegex(BusyError, '另一页面'):
                    broker.submit({'command': 'seek', 'step': 2},
                                  expected_scene_id='scene-a', expected_version='updated')
                with self.assertRaisesRegex(ValueError, 'different scene'):
                    broker.frame(scene_id='scene-b', version='original')
                broker.state['last_result'] = {'sequence': 5, 'frame_file': 'frame-00000005.png'}
                with self.assertRaisesRegex(ValueError, 'no longer'):
                    broker.frame(scene_id='scene-b', version='updated', sequence=4)
            finally:
                broker.close()

    def test_subprocess_protocol_serializes_jobs_and_reopens_after_close(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'scripts').mkdir()
            (root/'scripts/simulation_session_worker.py').write_text(
                "import json,sys,time\n"
                "for line in sys.stdin:\n"
                " message=json.loads(line)\n"
                " time.sleep(.1)\n"
                " print(json.dumps({'id':message['id'],'ok':True,'result':{'step':message.get('step',0)}}),flush=True)\n"
                " if message['command']=='close':break\n")
            broker = EngineBroker(root)
            try:
                broker.submit({'command': 'open'}, scene_id='example', version='updated')
                with self.assertRaises(BusyError):
                    broker.submit({'command': 'seek', 'step': 9})
                self.wait_job(broker)
                self.assertTrue(broker.status()['ready'])
                broker.submit({'command': 'seek', 'step': 7})
                self.wait_job(broker)
                self.assertEqual(broker.status()['last_result']['step'], 7)
                broker.submit({'command': 'close'})
                self.wait_job(broker)
                self.assertFalse(broker.status()['ready'])
                broker.submit({'command': 'open'}, scene_id='example', version='original')
                self.wait_job(broker)
                self.assertTrue(broker.status()['ready'])
            finally:
                broker.close()

    def wait_job(self, broker):
        deadline = time.monotonic()+5
        while broker.status()['busy'] and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertFalse(broker.status()['busy'])
        self.assertIsNone(broker.status()['error'])


if __name__ == '__main__':
    unittest.main()
