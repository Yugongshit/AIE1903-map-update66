"""Exercise the real HTTP client and version receipt; no actual planner is installed."""
import argparse
import json
from pathlib import Path
import time

from interactive_planner_client import RoomClient, run


class RecordingClient(RoomClient):
    def __init__(self, url):
        super().__init__(url, timeout_s=10)
        self.receipts = []

    def request(self, path, **kwargs):
        response = super().request(path, **kwargs)
        if path == 'input':
            assert response['accepted_planning_versions'] == kwargs['body']['planning_state_versions']
            self.receipts.append(response['accepted_planning_versions'])
        return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8092')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    client = RecordingClient(args.url)
    opened = client.request('open', body={'scene_id':'nuscenes-scene0980','version':'updated',
        'traffic':{'vehicle_count':8,'speed_spread':.3}})
    room = opened['room']
    identity = {k:room[k] for k in ('session_id','epoch')}
    host = {**identity,'host_token':room['host_token']}
    query = {'session_id':room['session_id'],'vehicle_id':'P8'}
    try:
        deadline = time.monotonic()+120
        while client.request('state',query=query)['phase']=='starting':
            if time.monotonic()>deadline:
                raise RuntimeError('Native room did not become ready')
            time.sleep(.2)
        observed = client.request('planning-input',query={**query,'map_policy':'observed'})
        baseline = client.request('planning-input',query={**query,'map_policy':'baseline'})
        assert observed['step']==baseline['step']
        assert observed['own_vehicle']==baseline['own_vehicle']
        assert observed['current_objects']==baseline['current_objects']
        assert observed['road_version']==baseline['road_version']
        run(client,room['session_id'],['P8'],room['join_token'],duration_s=.5,live_map=True)
        assert client.receipts
        result = {'passed':True,'accepted_action_receipts':len(client.receipts),
            'map_policy_preserves_physical_observation':True,
            'scope':'Real HTTP + native paused room + brake-only client; not Planning performance or execution proof'}
        args.out.parent.mkdir(parents=True,exist_ok=True)
        args.out.write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result),flush=True)
    finally:
        client.request('control',body={**host,'action':'close'})


if __name__=='__main__':
    main()
