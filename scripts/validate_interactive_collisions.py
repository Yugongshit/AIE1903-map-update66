"""Controlled native impacts against an unchanged source barrier.

Only the test vehicle's initial mission is placed on a clear approach. During
the run every pose comes from Bullet, never from a stop/teleport correction.
Run in the simulation interpreter without a concurrent native renderer.
"""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def choose_approach(source, missions, distance):
    from shapely.geometry import LineString
    from map_update.simulation_interactive import _footprint
    actors = source['static_overview_actors']
    footprints = {row['id']: _footprint({'position_city_m': row['position'][:2],
        'heading_rad': row['heading'], 'length_m': row['length'], 'width_m': row['width']}) for row in actors}
    for barrier in sorted((row for row in actors if row['type'] == 'TRAFFIC_BARRIER'), key=lambda row: row['length']):
        for side in (1, -1):
            heading = barrier['heading'] + (math.pi if side < 0 else 0.)
            direction = [math.cos(heading), math.sin(heading)]
            point = barrier['position'][:2]
            start = [point[i]-direction[i]*distance for i in (0, 1)]
            corridor = LineString([start, point]).buffer(1.05, cap_style=2)
            if any(key != barrier['id'] and corridor.intersects(footprint)
                   for key, footprint in footprints.items()):
                continue
            if any(math.dist(start, mission['position_scenario_m']) < 8 or
                   math.dist(point, mission['position_scenario_m']) < 8 for mission in missions[1:]):
                continue
            return barrier, start, heading, direction
    raise ValueError('No isolated source barrier has a clear test approach')


def validate(out, speed, replay_actions=None):
    from map_update.simulation_bridge import feature_sha256
    from map_update.simulation_interactive import IDS, physical_contacts
    from map_update.simulation_interactive_scenarios import choose_interactive_missions
    from map_update.simulation_platform_data import load_scene, scene_database
    from map_update.simulation_session import SimulationSession

    source = load_scene(ROOT, 'nuscenes-scene0980')
    missions = choose_interactive_missions(source)
    barrier, start, heading, direction = choose_approach(source, missions, 8. if speed <= 3 else 25.)
    mission = next(row for row in missions if row['vehicle_id'] == 'P1')
    mission.update(position_scenario_m=start, heading_rad=heading,
                   goal_scenario_m=[barrier['position'][i]+direction[i]*8 for i in (0, 1)])
    mission['patrol_goals_scenario_m'] = [mission['goal_scenario_m']]
    mission['patrol_loop'] = False
    out.mkdir(parents=True, exist_ok=True)
    session = SimulationSession(out/'native', width=640, height=420)
    report = {'source_barrier': barrier, 'test_initial_pose_city_m': start, 'target_speed_m_s': speed,
              'pose_assignments_after_spawn': 0, 'frames': [], 'checks': {},
              'collision_model': 'unchanged source-dimensioned fixed box; rigid vehicles, no damage model',
              'sampling': 'production fields at decision-end; test-only observer additionally reads each native substep',
              'substep_contacts': []}
    recorded_actions = json.loads(replay_actions.read_text())['frames'] if replay_actions else None
    if recorded_actions:
        report['replayed_action_report'] = str(replay_actions)
    controls = {key: {'mode': 'external', 'action': [0., -1.]} for key in IDS}
    physics_step = None
    try:
        initial = session.open(scene_database(ROOT, 'nuscenes-scene0980'), interactive={'missions': missions})
        before_hash = feature_sha256(session.env.engine.data_manager.current_scenario['map_features'])
        session.set_camera({'preset': 'follow', 'target': 'P1'})
        barrier_native = next(row for row in initial['interactive']['static_actors'] if row['id'] == barrier['id'])
        native_object = session.env.engine.get_objects()[barrier_native['native_object_id']]
        report['barrier_native_static'] = bool(native_object.body.isStatic())
        report['barrier_native_mass_kg'] = float(native_object.body.getMass())
        report['barrier_native_inverse_mass'] = float(native_object.body.getInvMass())
        report['barrier_native_collision_shape'] = type(native_object.body.getShape(0)).__name__
        physics_step = session.env.engine.step_physics_world
        substep_index = 0
        def observe_physics_step():
            # Observe only: call the original integrator exactly once with its
            # original dt. Never change a transform, action, velocity or mass.
            nonlocal substep_index
            result = physics_step()
            substep_index += 1
            vehicle = session.interactive.vehicles['P1']
            contacts = physical_contacts(session.env.engine.physics_world.dynamic_world,
                {vehicle.body: 'P1'}, {native_object.body: {'object_id': barrier['id'],
                'native_object_id': str(native_object.id), 'type': barrier['type']}}, step=session.env.episode_step)['P1']
            if contacts:
                report['substep_contacts'].append({'physics_substep': substep_index,
                    'speed_m_s': float(vehicle.speed), 'contacts': contacts})
            return result
        session.env.engine.step_physics_world = observe_physics_step
        contact_step, saved_contact = None, False
        for index in range(len(recorded_actions) if recorded_actions else 140):
            vehicle = session.interactive.vehicles['P1']
            throttle = max(-.4, min(.5, (speed-float(vehicle.speed))*.4))
            if contact_step is not None and session.env.episode_step >= contact_step+20:
                throttle = -1.
            controls['P1']['action'] = [0., throttle]
            if recorded_actions:
                controls['P1']['action'] = list(recorded_actions[index]['action'])
            state = session.interactive_tick(controls, 1, capture=False)
            row = deepcopy(state['interactive']['vehicles']['P1'])
            row['step'] = state['step']
            row['wall_normal_position_m'] = sum((row['position_city_m'][i]-barrier['position'][i])*direction[i] for i in (0, 1))
            report['frames'].append(row)
            if any(item['object_id'] == barrier['id'] for item in row['current_contacts']):
                contact_step = contact_step if contact_step is not None else state['step']
                if not saved_contact:
                    captured = session.frame()
                    shutil.copyfile(out/'native'/captured['frame_file'], out/'contact.png')
                    saved_contact = True
            if not recorded_actions and contact_step is not None and state['step'] >= contact_step+40:
                break
        frames = report['frames']
        contact_frames = [row for row in frames if any(item['object_id'] == barrier['id'] for item in row['current_contacts'])]
        report['first_contact_step'] = contact_step
        report['max_speed_m_s'] = max(row['speed_m_s'] for row in frames)
        report['max_chassis_center_z_m'] = max(row['chassis_center_city_m'][2] for row in frames)
        report['max_abs_pitch_rad'] = max(abs(row['pitch_rad']) for row in frames)
        report['max_abs_roll_rad'] = max(abs(row['roll_rad']) for row in frames)
        report['max_wall_normal_position_m'] = max(row['wall_normal_position_m'] for row in frames)
        report['checks'] = {
            'real_source_barrier_is_static_box': report['barrier_native_static'] and report['barrier_native_collision_shape'] == 'BulletBoxShape',
            'fixed_body_has_zero_inverse_mass': report['barrier_native_inverse_mass'] == 0.,
            'actual_barrier_contact_reported': bool(contact_frames),
            'nonzero_solver_impulse': any(item['normal_impulse_ns'] > 0 for row in report['substep_contacts'] for item in row['contacts']),
            'impact_history_retained': frames[-1]['last_collision'] is not None,
            'car_center_does_not_pass_through_wall': report['max_wall_normal_position_m'] < 0.,
            'car_decelerates_after_contact': bool(contact_frames) and frames[-1]['speed_m_s'] < .5,
            'physical_deceleration_while_commanding_forward': any(row['action'][1] > 0 and
                row['last_collision'] and row['last_collision']['speed_before_m_s'] > 1 and
                row['speed_m_s'] < .5 for row in contact_frames),
            'no_ground_contact_reported_as_collision': not any(row['collision_active'] for row in frames[:max(0, (contact_step or 1)-2)]),
            'source_map_hash_unchanged': before_hash == feature_sha256(session.env.engine.data_manager.current_scenario['map_features']),
        }
        if recorded_actions:
            report['checks']['exact_same_action_sequence_as_baseline'] = (
                [row['action'] for row in frames] == [row['action'] for row in recorded_actions])
        report['passed'] = all(report['checks'].values())
        captured = session.frame()
        shutil.copyfile(out/'native'/captured['frame_file'], out/'final.png')
    finally:
        if physics_step is not None and session.env is not None:
            session.env.engine.step_physics_world = physics_step
        session.close()
        (out/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in {'frames', 'source_barrier', 'substep_contacts'}}, indent=2))
    return report['passed']


def validate_replay(out):
    from map_update.simulation_bridge import feature_sha256
    from map_update.simulation_obstacle_runtime import runtime_obstacle_report
    from map_update.simulation_platform_data import load_scene, build_snapshot, scene_database
    from map_update.simulation_session import SimulationSession
    source = load_scene(ROOT, 'nuscenes-scene0980')
    out.mkdir(parents=True, exist_ok=True)
    session = SimulationSession(out/'native', width=480, height=320)
    report = {'checks': {}, 'samples': []}
    try:
        session.open(scene_database(ROOT, 'nuscenes-scene0980'))
        initial = runtime_obstacle_report(session.env)
        original = deepcopy(session.env.engine.data_manager.current_scenario)
        initial_hash = feature_sha256(original['map_features'])
        for step in (10, 50, 100, 150, 199):
            current = session.seek(step)
            expected = build_snapshot(source, step)['ego']['position'][:2]
            obstacles = runtime_obstacle_report(session.env)
            report['samples'].append({'step': step, 'actual': current['position_scenario_m'], 'recorded': expected,
                'ego_error_m': math.dist(current['position_scenario_m'], expected),
                'persistent_objects_unchanged': initial['objects'] == obstacles['objects'],
                'physical_static_inverse_masses': sorted(set(float(obj.body.getInvMass())
                    for obj in session.env.engine.traffic_manager.spawned_objects.values()
                    if obj.id in session.env.engine.traffic_manager._obj_id_to_scenario_id
                    and session.env.engine.traffic_manager._obj_id_to_scenario_id[obj.id]
                    in session.env.engine.traffic_manager.persistence_definitions))})
        report['checks'] = {
            'source_replay_pose_matches_at_all_samples': all(row['ego_error_m'] < .05 for row in report['samples']),
            'all_persistent_poses_and_dimensions_unchanged': all(row['persistent_objects_unchanged'] for row in report['samples']),
            'fixed_source_bodies_keep_zero_inverse_mass': all(row['physical_static_inverse_masses'] == [0.] for row in report['samples']),
            'map_hash_unchanged': feature_sha256(session.env.engine.data_manager.current_scenario['map_features']) == initial_hash,
        }
        # Source track arrays, including the recorded ego, remain byte-for-byte
        # equal; the adapter changes physical fixed-body parameters only.
        import pickle
        report['checks']['source_track_arrays_unchanged'] = (pickle.dumps(original['tracks']) ==
            pickle.dumps(session.env.engine.data_manager.current_scenario['tracks']))
        report['passed'] = all(report['checks'].values())
    finally:
        session.close()
        (out/'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return report['passed']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--speed', type=float, default=3.)
    parser.add_argument('--replay-only', action='store_true')
    parser.add_argument('--replay-actions', type=Path, help='Replay the exact P1 actions from a previous impact report')
    args = parser.parse_args()
    passed = validate_replay(args.out) if args.replay_only else validate(args.out, args.speed, args.replay_actions)
    raise SystemExit(0 if passed else 1)
