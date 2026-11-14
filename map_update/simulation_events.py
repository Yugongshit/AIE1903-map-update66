"""Explicit synthetic initial construction and simulation-clock clearance.

Only a fixed collision box is modelled; no workers, deployment animation or
vehicle damage are claimed. Event schedules belong to the generator, not sensors.
"""
from copy import deepcopy
import math
import random

from .simulation_interactive_scenarios import vehicle_footprint
from .simulation_planner import _length, _point_at, _project, _angle


def sample_clearance_time(options, event_id="construction-1"):
    """Independent reproducible event stream; timing is not a sensor field.

    Triangular simulation prior: shorter temporary blockages are more frequent.
    These bounds are demo assumptions, not calibrated real construction times.
    """
    low, high = options['construction_min_s'], options['construction_max_s']
    rng = random.Random(f"{options['seed']}:{event_id}:clearance")
    value = rng.triangular(low, high, low+(high-low)/3)
    return min(high, math.ceil(value*10)/10)


def construction_specs(path, missions, occupied, options):
    """Seeded safe initial sites on the source cycle, with explicit capacity.

    The first site prefers the lead car's neighbourhood for inspectability;
    remaining sites sample the whole cycle. No road or actors are moved to fit.
    """
    count_rng = random.Random(f"{options['seed']}:construction:count")
    requested = count_rng.randint(options['construction_count_min'], options['construction_count_max'])
    rng = random.Random(f"{options['seed']}:construction:positions")
    length = _length(path)
    first = _project(missions[0]['position_scenario_m'], path)[1]
    nearby = [(first+d) % length for d in range(10, 21, 2)]
    candidates = [i*2. for i in range(math.ceil(length/2))]
    rng.shuffle(nearby)
    rng.shuffle(candidates)
    specs, progresses, shapes = [], [], []
    for progress in nearby+candidates:
        position = list(_point_at(path, progress))
        heading = _project(position, path)[3]
        # Avoid corners where the box may cut across the source lane.
        tangents = [_project(_point_at(path,(progress+d)%length),path)[3] for d in (-4.,4.)]
        if any(abs(_angle(t-heading)) > .35 for t in tangents):
            continue
        shape = vehicle_footprint(position, heading, 1., 2.5, margin=.5)
        if any(shape.distance(other) < 4. for other in occupied):
            continue
        if any(min(abs(progress-s),length-abs(progress-s)) < 25. for s in progresses):
            continue
        if any(shape.distance(other) < 20. for other in shapes):
            continue
        index = len(specs)+1
        specs.append(dict(object_id=f'construction-C{index}', event_id=f'construction-{index}',
            type='TRAFFIC_BARRIER', position_city_m=position, heading_rad=heading,
            length_m=1., width_m=2.5, height_m=1., synthetic=True))
        progresses.append(progress)
        shapes.append(shape)
        if len(specs) == requested:
            break
    return specs, dict(requested_count=requested, placed_count=len(specs),
        capacity_limited=len(specs)<requested, candidate_scope='existing_source_cycle',
        reason='insufficient_safe_initial_sites' if len(specs)<requested else None,
        synthetic=True)


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
        return dict(event_id=self.spec.get('event_id', 'construction-1'), kind='synthetic_construction',
            status='active' if self.obj is not None else 'removed', object_id=self.spec['object_id'],
            started_at_s=0., removed_at_s=self.removed_at_s, synthetic=True,
            execution_model='initial_fixed_box_then_discrete_clearance_no_worker_animation')
