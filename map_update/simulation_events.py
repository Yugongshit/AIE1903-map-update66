"""Explicit synthetic initial construction and simulation-clock clearance.

Only a fixed collision box is modelled; no workers, deployment animation or
vehicle damage are claimed. Event schedules belong to the generator, not sensors.
"""
from copy import deepcopy
import math
import random

from .simulation_interactive_scenarios import vehicle_footprint
from .simulation_planner import _length, _point_at, _project


def sample_clearance_time(options):
    """Independent reproducible event stream; timing is not a sensor field.

    Triangular simulation prior: shorter temporary blockages are more frequent.
    These bounds are demo assumptions, not calibrated real construction times.
    """
    low, high = options['construction_min_s'], options['construction_max_s']
    rng = random.Random(f"{options['seed']}:construction-1:clearance")
    value = rng.triangular(low, high, low+(high-low)/3)
    return min(high, math.ceil(value*10)/10)


def construction_spec(path, missions, occupied):
    length = _length(path)
    first = _project(missions[0]['position_scenario_m'], path)[1]
    position = list(_point_at(path, (first+min(20., length/len(missions)*.55)) % length))
    heading = _project(position, path)[3]
    shape = vehicle_footprint(position, heading, 1., 2.5, margin=.5)
    if any(shape.intersects(other) for other in occupied):
        raise ValueError('Construction spawn overlaps a source actor or generated vehicle')
    return dict(object_id='construction-C1', type='TRAFFIC_BARRIER', position_city_m=position,
        heading_rad=heading, length_m=1., width_m=2.5, height_m=1., synthetic=True)


class ConstructionEvent:
    def __init__(self, engine, metadata, spec, duration_s):
        from .simulation_obstacle_runtime import _measured_classes
        self.engine, self.spec, self.duration_s = engine, deepcopy(spec), duration_s
        self.removed_at_s = None
        offset = metadata.get('old_origin_in_current_coordinate', [0.,0.])
        self.obj = engine.spawn_object(_measured_classes()['TRAFFIC_BARRIER'],
            name=spec['object_id'], random_seed=0,
            position=[spec['position_city_m'][i]+float(offset[i]) for i in (0,1)],
            heading_theta=spec['heading_rad'], length=spec['length_m'], width=spec['width_m'],
            height=spec['height_m'], static=True)
        self.native_id = self.obj.id

    def advance(self, time_s):
        if self.obj is not None and time_s >= self.duration_s:
            self.engine.clear_objects([self.obj.id], force_destroy=True)
            self.obj = None
            self.removed_at_s = time_s

    def close(self):
        if self.obj is not None:
            self.engine.clear_objects([self.obj.id], force_destroy=True)
            self.obj = None

    def report(self):
        return dict(event_id='construction-1', kind='synthetic_construction',
            status='active' if self.obj is not None else 'removed', object_id=self.spec['object_id'],
            started_at_s=0., removed_at_s=self.removed_at_s, synthetic=True,
            execution_model='initial_fixed_box_then_discrete_clearance_no_worker_animation')
