import unittest
from types import SimpleNamespace

from map_update.recorded_takeover import RecordedTakeoverSession, action_batch


class RecordedTakeoverTests(unittest.TestCase):
    def test_batch_requires_same_frame_and_complete_actor_set(self):
        for step, actions in [(9, {'a':[0,0]}), (True, {'a':[0,0]}), (10, {}),
                              (10, {'a':[0,0], 'unknown':[0,0]}), (10, {'a':[0,float('nan')]})]:
            with self.assertRaises(ValueError): action_batch(10, step, {'a'}, actions)
        self.assertEqual(action_batch(10,10,{'a'},{'a':[0,-1]}), {'a':[0.,-1.]})

    def session(self):
        s = RecordedTakeoverSession.__new__(RecordedTakeoverSession)
        s.env = SimpleNamespace(episode_step=0)
        s.sdc = 'a'; s.controlled = {'a'}; s.stopped = None
        s.scenario = {'length':2, 'metadata':{'ts':[0.,.1]}, 'map_features':{'lane':{'polyline':[[0,0],[1,0]]}},
                      'tracks':{'a':{'type':'VEHICLE','state':{'valid':[True,True]}}},
                      'dynamic_map_states':{'light':{'state':{'object_state':['RED','GREEN']}}}}
        obj = SimpleNamespace(id='native-a',position=[1.,2.],velocity=[3.,4.],heading_theta=.5)
        s._objects = lambda: {'a':obj}
        return s

    def test_observation_whitelist_excludes_future_and_is_detached(self):
        s = self.session(); observed = s.observe()
        self.assertEqual(observed['signals'], {'light':'RED'})
        self.assertNotIn('tracks', observed)
        s.scenario['tracks']['a']['state']['future_secret'] = ['answer']
        s.scenario['dynamic_map_states']['light']['state']['object_state'][1] = 'SECRET'
        self.assertEqual(observed,s.observe())
        observed['objects']['a']['position_m'][0] = 999
        self.assertEqual(s.observe()['objects']['a']['position_m'][0],1.)

    def test_missing_future_validity_stops_before_physics(self):
        s=self.session();s.scenario['tracks']['a']['state']['valid'][1]=False
        with self.assertRaisesRegex(ValueError,'track_end_or_gap'):
            s.advance({'a':[0,-1]},observation_step=0)
        self.assertEqual(s.step,0)

    def test_record_end_and_return_to_replay_are_explicit(self):
        s=self.session();s.env.episode_step=1
        with self.assertRaisesRegex(ValueError,'recording_end'):
            s.advance({'a':[0,-1]},observation_step=1)
        self.assertEqual(s.step,1)
        with self.assertRaisesRegex(ValueError,'independent'):
            s.return_to_replay(['a'])

    def test_map_is_copied_and_has_no_dynamic_or_track_schedule(self):
        s=self.session();result=s.map_input();result['lane']['polyline'][0][0]=999
        self.assertEqual(s.map_input()['lane']['polyline'][0][0],0)
        self.assertNotIn('dynamic_map_states',s.map_input())

if __name__ == '__main__': unittest.main()
