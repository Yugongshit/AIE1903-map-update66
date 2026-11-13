"""Keep explicitly persisted static scene objects in the running simulator.

Only exported barriers/cones are affected. This is a selected simulation policy,
not a claim that an observed object remained present outside the recording.
MetaDrive already retains spawned static objects; this adapter also admits late
observations, uses their measured dimensions and keeps them with moving traffic
disabled. The platform can explicitly preload all static objects for a fixed
offline overview; ordinary simulation remains gated by first observation.
The third-party source files and global bindings are restored on exit.
"""
from contextlib import contextmanager
import math


STATIC_TYPES = {'TRAFFIC_BARRIER', 'TRAFFIC_CONE'}


def persistent_objects(manifest):
    if manifest is None or manifest.get('mode') == 'recorded':
        return {}
    if manifest.get('schema') != 'simulation-obstacle-policy-1.0' or manifest.get('mode') != 'persistent':
        raise ValueError('Unsupported persistent obstacle manifest')
    objects = manifest.get('objects')
    if not isinstance(objects, list):
        raise ValueError('Persistent obstacle manifest needs an objects list')
    indexed = {}
    for row in objects:
        key = row.get('track_id')
        if not isinstance(key, str) or not key or key in indexed:
            raise ValueError('Persistent obstacles need unique track IDs')
        if row.get('type') not in STATIC_TYPES or row.get('persisted') is not True:
            raise ValueError('Only explicitly persisted barriers and cones are supported')
        if type(row.get('first_seen_step')) is not int or row['first_seen_step'] < 0:
            raise ValueError('Invalid persistent object first-seen step')
        for field in ('length_m', 'width_m', 'height_m'):
            if type(row.get(field)) not in (int, float) or not math.isfinite(row[field]) or row[field] <= 0:
                raise ValueError('Persistent object dimensions must be finite and positive')
        indexed[key] = row
    return indexed


def _scalar(value):
    return float(value.item() if hasattr(value, 'item') else value)


def persistent_spawn_spec(track_id, track, definition, step, *, preload_all=False):
    """Use the loaded first observation; early visibility is preview-only."""
    if type(preload_all) is not bool:
        raise ValueError('Full-scene preload must be boolean')
    first = definition['first_seen_step']
    if step < first and not preload_all:
        return None
    if track.get('type') != definition['type']:
        raise ValueError('Persisted object type differs from its track')
    state = track['state']
    if first >= len(state['valid']) or not state['valid'][first]:
        raise ValueError('Persisted first observation is absent from its track')
    position = [float(x) for x in state['position'][first][:2]]
    heading = _scalar(state['heading'][first])
    if not all(math.isfinite(x) for x in position + [heading]):
        raise ValueError('Persistent object pose must be finite')
    dims = {}
    for field in ('length', 'width', 'height'):
        value = _scalar(state[field][first])
        if not math.isclose(value, definition[field + '_m'], rel_tol=1e-6, abs_tol=1e-7):
            raise ValueError('Persistent object dimensions differ from its loaded track')
        dims[field] = value
    return dict(position=position, heading_theta=heading, static=True, **dims)


def traffic_manager_class(base, definitions, *, no_moving_traffic=False, object_classes=None, preload_all=False):
    """Create an adapter class; object_classes is injected for engine-free tests."""
    definitions = dict(definitions)
    if type(preload_all) is not bool:
        raise ValueError('Full-scene preload must be boolean')

    class PersistentScenarioTrafficManager(base):
        persistence_definitions = definitions
        moving_traffic_disabled = no_moving_traffic
        full_scene_preview = preload_all

        @property
        def current_traffic_data(self):
            tracks = super().current_traffic_data
            if no_moving_traffic:
                return {key: track for key, track in tracks.items() if key in definitions}
            return tracks

        def spawn_static_object(self, cls, scenario_id, track):
            if scenario_id not in definitions:
                return super().spawn_static_object(cls, scenario_id, track)
            # Do not rely on the vendor's minimum 20 valid frames: a barrier
            # first observed on the last frame is still an explicit object.
            if scenario_id in self._scenario_id_to_obj_id:
                return
            spec = persistent_spawn_spec(scenario_id, track, definitions[scenario_id], self.episode_step,
                                         preload_all=preload_all)
            if spec is None:
                return
            name = scenario_id if self.engine.global_config['force_reuse_object_name'] else None
            measured_cls = object_classes[track['type']]
            obj = self.spawn_object(measured_cls, name=name, force_spawn=True, **spec)
            self._scenario_id_to_obj_id[scenario_id] = obj.name
            self._obj_id_to_scenario_id[obj.name] = scenario_id

    return PersistentScenarioTrafficManager


def _measured_classes():
    """Fit generic vendor models to source L/W/H and use the same collision box."""
    from panda3d.bullet import BulletBoxShape
    from metadrive.component.static_object.traffic_object import TrafficBarrier, TrafficCone

    def measured(base, name):
        class MeasuredObject(base):
            # Bullet's setStatic(True) only changes collision flags. Retaining
            # the vendor cone/barrier mass leaves nonzero inverse mass in the
            # solver: it assigns an impulse to an object that cannot move,
            # allowing cars to penetrate it. A fixed source obstacle must have
            # zero mass (infinite resistance), as well as the static flag.
            MASS = 0.

            def __init__(self, position, heading_theta, length, width, height, static=True,
                         random_seed=None, name=None):
                self._source_length, self._source_width = float(length), float(width)
                self.HEIGHT = float(height)
                super().__init__(position=position, heading_theta=heading_theta, static=True,
                                 random_seed=random_seed, name=name)
                # Source heading follows the object's length X axis. The vendor
                # barrier box swaps X/Y, so replace it with source-oriented LWH.
                for shape in list(self.body.getShapes()):
                    self.body.removeShape(shape)
                self.body.addShape(BulletBoxShape((length / 2, width / 2, height / 2)))
                self.set_static(True)
                if self.render and self.origin.getNumChildren():
                    children = list(self.origin.getChildren())
                    visual = self.origin.attachNewNode('source-dimensioned-model')
                    for child in children:
                        child.reparentTo(visual)
                    bounds = visual.getTightBounds()
                    if bounds:
                        lower, upper = bounds
                        extents = [upper[i] - lower[i] for i in range(3)]
                        if min(extents) > 1e-8:
                            scale = [size / extent for size, extent in zip((length, width, height), extents)]
                            visual.setScale(*scale)
                            visual.setPos(*[-(lower[i] + upper[i]) * .5 * scale[i] for i in range(3)])

            @property
            def LENGTH(self):
                return self._source_length

            @property
            def WIDTH(self):
                return self._source_width

            @property
            def top_down_length(self):
                return self._source_length

            @property
            def top_down_width(self):
                return self._source_width

        MeasuredObject.__name__ = name
        return MeasuredObject

    return {'TRAFFIC_BARRIER': measured(TrafficBarrier, 'SourceDimensionedBarrier'),
            'TRAFFIC_CONE': measured(TrafficCone, 'SourceDimensionedCone')}


@contextmanager
def persistent_obstacle_adapter(scenario_env_module, manifest, *, no_moving_traffic=False, preload_all=False):
    """Scope the ScenarioEnv traffic-manager binding to one live engine."""
    definitions = persistent_objects(manifest)
    if not definitions:
        yield {'active': False, 'objects': 0}
        return
    original = scenario_env_module.ScenarioTrafficManager
    scenario_env_module.ScenarioTrafficManager = traffic_manager_class(
        original, definitions, no_moving_traffic=no_moving_traffic, object_classes=_measured_classes(),
        preload_all=preload_all)
    try:
        yield {'active': True, 'objects': len(definitions), 'mode': 'persistent',
               'moving_traffic_disabled': no_moving_traffic,
               'collision_geometry': 'first-observed oriented source bounding box',
               'visual_geometry': 'generic model fitted to first-observed dimensions',
               'display_scope': 'full_scene_overview' if preload_all else 'current_observations',
               'future_objects_visible_early': preload_all,
               'observed_permanence': False}
    finally:
        scenario_env_module.ScenarioTrafficManager = original


def runtime_obstacle_report(env):
    manager = getattr(env.engine, 'traffic_manager', None)
    definitions = getattr(manager, 'persistence_definitions', {})
    preload_all = getattr(manager, 'full_scene_preview', False)
    mapping = getattr(manager, '_scenario_id_to_obj_id', {}) or {}
    expected = sorted(key for key, row in definitions.items() if preload_all or row['first_seen_step'] <= env.episode_step)
    active = sorted(key for key in mapping if key in definitions)
    poses = {}
    for key in active:
        obj = manager.spawned_objects[mapping[key]]
        poses[key] = {'position_runtime_m': [float(v) for v in obj.position[:2]],
                      'heading_rad': float(obj.heading_theta),
                      'length_m': float(obj.LENGTH), 'width_m': float(obj.WIDTH),
                      'height_m': float(obj.HEIGHT), 'static': bool(obj.body.isStatic())}
    return {'mode': 'persistent' if definitions else 'recorded', 'step': int(env.episode_step),
            'display_scope': 'full_scene_overview' if preload_all else 'current_observations',
            'future_objects_visible_early': bool(preload_all),
            'expected_ids': expected, 'active_ids': active, 'active_count': len(active),
            'missing_expected_ids': sorted(set(expected) - set(active)), 'objects': poses}
