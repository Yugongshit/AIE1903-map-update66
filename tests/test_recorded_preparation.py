from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

from map_update.map_versions import MapVersions
from map_update.map_coverage import MapCoverage
from map_update.planning_contract import validate_plan, road_task, scoped_observation
from map_update.recorded_service import RecordedService
from map_update.recorded_recovery import binding, recover_commands
from map_update.experiment_records import freeze_manifest, verify_manifest, read_journal, summarize_journal
from map_update.planner_process import PlannerProcess


def lane(x=0, entry=None, exits=None):
    return dict(type='LANE_SURFACE_STREET', polyline=[[x,0,0],[x+10,0,0]],
                polygon=[[x,-2,0],[x+10,-2,0],[x+10,2,0],[x,2,0],[x,-2,0]],
                entry_lanes=entry or [], exit_lanes=exits or [])


class World:
    def __init__(self):
        self.step = 0
        self.controlled = set()
        self.stopped = None
        self.scenario = {'id':'test', 'length':20}
        self.database = '/trusted/test'
        self.index = 0

    def map_input(self):
        return {'one':lane(exits=['two']), 'two':lane(10,entry=['one'])}

    def observe(self):
        return dict(step=self.step, simulation_time_s=self.step*.1, signals={'light':'GREEN'},
                    coordinate_frame='XY', stopped=self.stopped,
                    objects={key:dict(position_m=[x,0], velocity_m_s=[0,0], heading_rad=0,
                        dimensions_m={'length':2,'width':1,'height':1}, type='VEHICLE', native_id=key,
                        mode='external' if key in self.controlled else 'recorded_replay')
                             for key,x in [('a',2),('b',12),('c',15),('d',18)]})

    def takeover(self, ids):
        self.controlled.update(ids)

    def advance(self, actions, observation_step):
        self.step += 1

    def close(self):
        pass


def patch():
    return dict(provenance={'source':'synthetic','reason':'test','synthetic':True},
                operations=[dict(operation='add',feature_id='line',source_event_id='test-event',
                    feature={'type':'ROAD_LINE_SOLID_SINGLE_WHITE','polyline':[[0,1,0],[2,1,0]]})])


def plan(observation, actor='a', plan_id='p1'):
    current = observation['objects'][actor]
    return dict(plan_id=plan_id, actor_id=actor, coordinate_frame='XY',
                points=[dict(position_m=deepcopy(current['position_m']),heading_rad=0,speed_m_s=0,
                             time_offset_s=t) for t in (0.,.1)])


class PreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.journal = (self.root/'events.jsonl').open('x+')
        self.addCleanup(self.journal.close)
        self.now = 0.
        self.world = World()
        self.service = RecordedService(self.world,self.journal,clock=lambda:self.now,action_timeout=5.)

    def claim(self, ids=None, scope=None):
        return self.service.takeover(dict(binding(self.service),actor_ids=ids or ['a'],
            observation_scope=scope or {'kind':'global'}))['control_tokens']

    def test_map_transaction_and_rollback_preserve_physical_world(self):
        original = self.world.map_input()
        initial = self.service.map_version
        result = self.service.map_patch(dict(binding(self.service),request_id='m',**patch()))
        self.assertIn('line', self.service.map)
        self.assertEqual(self.world.map_input(), original)
        self.assertEqual(self.service.physical_map_version, initial)
        reverted = self.service.map_rollback(dict(binding(self.service),request_id='r',
            target_version=initial, provenance=patch()['provenance']))
        self.assertEqual(self.service.map, original)
        self.assertNotEqual(reverted['revision']['version'], initial)
        self.assertEqual(len(self.service.maps.revisions), 3)

    def test_real_structural_publication_requires_supported_matching_evidence(self):
        source=patch()
        source['provenance']['synthetic']=False
        with self.assertRaisesRegex(ValueError,'supported upstream'):
            self.service.map_patch(dict(binding(self.service),request_id='missing',**source))
        evidence=dict(evidence_id='test-event',source_object_id='source-object',
            change_type='add_marking',observed_time_ns='0',source_ids=['source-frame'],
            score=.8,score_semantics='uncalibrated quality',unknown_fields=[],
            decision='supported',coordinate_frame='XY',operations=source['operations'])
        self.service.structure_evidence(dict(binding(self.service),request_id='e',records=[evidence]))
        self.service.map_patch(dict(binding(self.service),request_id='supported',**source))
        self.assertIn('line',self.service.map)

    def test_structural_evidence_batch_validation_is_atomic(self):
        source=patch()
        evidence=dict(evidence_id='test-event',source_object_id='source-object',
            change_type='add_marking',observed_time_ns='1',source_ids=['source-frame'],
            score=.8,score_semantics='uncalibrated quality',unknown_fields=[],
            decision='supported',coordinate_frame='XY',operations=source['operations'])
        with self.assertRaises(ValueError):
            self.service.structure_evidence(dict(binding(self.service),request_id='future',records=[evidence]))
        self.assertEqual(self.service.structure_records,{})

    def test_map_retry_has_no_extra_revision(self):
        body = dict(binding(self.service),request_id='m',**patch())
        with self.assertRaisesRegex(ValueError,'Unknown transaction'):
            self.service.map_patch(dict(body,control_token='must-not-be-logged'))
        first = self.service.map_patch(body)
        self.assertEqual(self.service.map_patch(body), first)
        self.assertEqual(len(self.service.maps.revisions),2)
        event=json.loads((Path(__file__).resolve().parents[1]/'examples/road-state-intake/synthetic-event.json').read_text())
        event['coordinate_reference']['frame']='XY'
        self.service.evidence_update(dict(binding(self.service),scene_id='test',
            submission_id='map:m',time_basis='simulation_time_ns',events=[event]))
        self.assertEqual(self.service.map_patch(body),first)
        body['provenance']['reason'] = 'different'
        with self.assertRaises(ValueError): self.service.map_patch(body)

    def test_invalid_map_batch_and_topology_are_atomic(self):
        body = patch()
        body['operations'].append(dict(operation='delete',feature_id='missing',source_event_id='e',before_sha256='bad'))
        with self.assertRaises(ValueError):
            self.service.map_patch(dict(binding(self.service),request_id='m',**body))
        self.assertNotIn('line',self.service.map)
        ledger = MapVersions(self.world.map_input())
        from map_update.simulation_bridge import feature_sha256
        with self.assertRaisesRegex(ValueError,'topology'):
            ledger.prepare([dict(operation='delete',feature_id='two',source_event_id='e',
                                 before_sha256=feature_sha256(ledger.current['two']))])

    def test_plan_is_intent_and_requires_current_control(self):
        tokens = self.claim()
        state = self.service.snapshot()
        trajectory = plan(state)
        body = dict(binding(self.service),plan=trajectory,control_token=tokens['a'])
        self.service.plan_submit(body)
        self.service.plan_submit(body)
        self.assertEqual(self.world.step,0)
        self.assertEqual(self.service.snapshot()['objects'],state['objects'])
        bad = deepcopy(body)
        bad['control_token']='bad'
        with self.assertRaises(PermissionError): self.service.plan_submit(bad)
        self.service.map_patch(dict(binding(self.service),request_id='m',**patch()))
        with self.assertRaises(ValueError):
            self.service.advance(dict(binding(self.service),actions={'a':[0,0]},
                control_tokens=tokens,plan_ids={'a':'p1'}))

    def test_trajectory_contract_rejects_future_and_nonfinite_and_wrong_start(self):
        self.claim()
        state = self.service.snapshot()
        for field,value in [('coordinate_frame','wrong'),('actor_id','b')]:
            trajectory=plan(state);trajectory[field]=value
            with self.assertRaises(ValueError): validate_plan(trajectory,state,{'a'},.2)
        for field,value in [('time_offset_s',.3),('position_m',[float('nan'),0]),('speed_m_s',-1)]:
            trajectory=plan(state);trajectory['points'][1][field]=value
            with self.assertRaises(ValueError): validate_plan(trajectory,state,{'a'},.2)
        trajectory=plan(state);trajectory['points'][0]['position_m']=[9,9]
        with self.assertRaises(ValueError): validate_plan(trajectory,state,{'a'},.2)

    def test_four_actor_barrier_advances_once_and_hides_other_actions(self):
        tokens = self.claim(['a','b','c','d'])
        original = binding(self.service)
        for index,actor in enumerate(tokens):
            result = self.service.actor_action(dict(original,actor_id=actor,control_token=tokens[actor],action=[0,0]))
            self.assertEqual(result['advanced'], index==3)
            self.assertNotIn('pending',result['observation'])
        self.assertEqual(self.world.step,1)
        with self.assertRaises(ValueError):
            self.service.actor_action(dict(original,actor_id='a',control_token=tokens['a'],action=[0,0]))

    def test_partial_mailbox_cleared_when_map_changes(self):
        tokens = self.claim(['a','b'])
        self.service.actor_action(dict(binding(self.service),actor_id='a',control_token=tokens['a'],action=[0,0]))
        self.service.map_patch(dict(binding(self.service),request_id='m',**patch()))
        self.assertEqual(self.service.pending,{})

    def test_actor_scope_and_task_reachability(self):
        tokens = self.claim(scope={'kind':'radius','radius_m':3})
        state = self.service.actor_observation('a',tokens['a'])
        self.assertEqual(set(state['objects']),{'a'})
        self.assertEqual(state['signals'],{})
        self.assertIsNone(state['road_states'])
        task = self.service.task_set(dict(binding(self.service),actor_id='a',control_token=tokens['a'],goal_m=[18,0]))
        self.assertEqual(task['task']['task']['status'],'reachable')
        self.assertEqual(road_task(self.service.map,[18,0],[2,0])['status'],'unreachable')
        self.assertEqual(road_task(self.service.map,[2,0],[100,100])['status'],'outside_available_lane_geometry')

    def test_explicit_polygon_coverage_and_unavailable_geometry(self):
        actor=self.world.observe()['objects']['a']
        self.assertTrue(MapCoverage(self.world.map_input()).actor(actor)['footprint_covered'])
        actor['position_m']=[0,0]
        self.assertFalse(MapCoverage(self.world.map_input()).actor(actor)['footprint_covered'])
        self.assertEqual(MapCoverage({}).actor(actor)['status'],'unavailable')

    def test_timeout_resume_keeps_control_and_invalidates_old_actions(self):
        tokens=self.claim()
        old=binding(self.service)
        self.now=6
        self.assertEqual(self.service.snapshot()['service_stopped'],'action_timeout')
        current=self.service.resume(binding(self.service))
        self.assertEqual(current['objects']['a']['mode'],'external')
        self.assertEqual(self.world.step,0)
        with self.assertRaises(ValueError):
            self.service.advance(dict(old,actions={'a':[0,0]},control_tokens=tokens))

    def test_recovery_replays_commands_with_new_session_identity(self):
        tokens=self.claim()
        self.service.map_patch(dict(binding(self.service),request_id='m',**patch()))
        self.service.advance(dict(binding(self.service),actions={'a':[0,0]},control_tokens=tokens))
        records=read_journal(self.root/'events.jsonl')
        with (self.root/'recovered.jsonl').open('x') as journal:
            restored=RecordedService(World(),journal)
            recover_commands(restored,records)
            self.assertNotEqual(restored.session_id,self.service.session_id)
            self.assertEqual(restored.session.step,1)
            self.assertEqual(restored.map,self.service.map)
            self.assertNotEqual(restored.leases['a'],tokens['a'])
        summary=summarize_journal(self.root/'events.jsonl')
        self.assertEqual(summary['runs'][0]['frames'],1)
        self.assertEqual(summary['runs'][0]['map_changes'],1)

    def test_recovery_refuses_unresolved_mutation(self):
        records=read_journal(self.root/'events.jsonl')
        records.append({'event':'advance_intent','data':{'actions':{}}})
        with self.assertRaisesRegex(ValueError,'unresolved'):
            recover_commands(self.service,records)

    def test_manifest_group_separation_and_checksum(self):
        (self.root/'a').write_text('one')
        (self.root/'b').write_text('two')
        rows=[dict(path='a',scene_id='a',group_id='source1',split='development',source='synthetic')]
        manifest=freeze_manifest(rows,self.root)
        verify_manifest(manifest,self.root)
        with self.assertRaises(ValueError):
            freeze_manifest(rows+[dict(path='b',scene_id='b',group_id='source1',split='test',source='synthetic')],self.root)
        (self.root/'a').write_text('tampered')
        with self.assertRaises(ValueError): verify_manifest(manifest,self.root)

    def test_explicit_evidence_transform_preserves_source_and_exact_time(self):
        from map_update.evidence_adapter import adapt_events
        event = dict(coordinate_reference={'frame':'source','units':'m'},samples=[
            dict(timestamp_ns='1000000000000000001',source_ids=['frame1'],
                 geometry_city={'type':'LineString','coordinates':[[1,0,3],[2,0,4]]})])
        transform = dict(source_frame='source',target_frame='XY',rotation_rad=0,
                         translation_m=[10,20],scale=2,source_time_origin_ns='1000000000000000000',
                         target_time_origin_ns='0',drop_z=True)
        result=adapt_events([event],transform,'a'*64)
        self.assertEqual(result[0]['samples'][0]['timestamp_ns'],'1')
        self.assertEqual(result[0]['samples'][0]['geometry_city']['coordinates'],[[12.,20.],[14.,20.]])
        self.assertEqual(event['samples'][0]['geometry_city']['coordinates'][0],[1,0,3])
        with self.assertRaises(ValueError):
            adapt_events([event],dict(transform,drop_z=False),'a'*64)
        with self.assertRaises(ValueError):
            adapt_events([event],dict(transform,source_frame='wrong'),'a'*64)

    def test_strict_source_coverage_is_checked_before_and_after_control(self):
        token=self.service.takeover(dict(binding(self.service),actor_ids=['a'],
            coverage_policy='source_lane_polygons'))['control_tokens']
        original_observe=self.world.observe
        def outside():
            result=original_observe()
            if self.world.step:
                result['objects']['a']['position_m']=[100,100]
            return result
        self.world.observe=outside
        result=self.service.advance(dict(binding(self.service),actions={'a':[0,0]},control_tokens=token))
        self.assertEqual(result['service_stopped'],'outside_explicit_source_lane_polygons')
        self.assertFalse(result['objects']['a']['source_lane_coverage']['position_covered'])

    def test_worker_timeout_and_oversized_output_are_bounded(self):
        with (self.root/'worker.log').open('w') as errors:
            worker=PlannerProcess([sys.executable,'-c','import time;time.sleep(5)'],errors,.05)
            try:
                with self.assertRaises(TimeoutError): worker.compute({})
            finally: worker.close()
            worker=PlannerProcess([sys.executable,'-c','print("x"*1100000)'],errors,2.)
            try:
                with self.assertRaises(ValueError): worker.compute({})
            finally: worker.close()
            code = 'import json; print(json.dumps(dict(plans=[], actions={}, implementation="test", state_version="old")))'
            worker=PlannerProcess([sys.executable,'-c',code],errors,2.)
            try:
                with self.assertRaisesRegex(ValueError, 'different observation'):
                    worker.compute({'observation': {'state_version': 'current'}})
            finally: worker.close()


if __name__ == '__main__':
    unittest.main()
