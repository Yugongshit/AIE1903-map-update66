"""IDM background traffic baseline, using only current local actor states.

Parameters are explicit simulation assumptions, not fitted real-driver models.
Lateral control and emergency collision protection remain separate.
"""
import math
import random

from .simulation_planner import _angle, _length, _project


DRIVER_PARAMETERS = {
    'cautious': dict(time_headway_s=1.8, minimum_gap_m=3., acceleration_m_s2=.7, comfortable_brake_m_s2=1.4),
    'normal': dict(time_headway_s=1.3, minimum_gap_m=2., acceleration_m_s2=1., comfortable_brake_m_s2=1.8),
    'assertive': dict(time_headway_s=.9, minimum_gap_m=1.5, acceleration_m_s2=1.3, comfortable_brake_m_s2=2.2),
}


def driver_parameters(seed, vehicle_id, style):
    # Separate stream: changing behaviour must not move initial actors.
    if style == 'mixed':
        style = random.Random(f'{seed}:driver:{vehicle_id}').choice(sorted(DRIVER_PARAMETERS))
    return dict(style=style, **DRIVER_PARAMETERS[style], calibrated=False)


def project_actors(path, actors):
    return {a['vehicle_id']: _project(a['position_city_m'], path) for a in actors}


def current_leader(own, actors, path, projections=None, radius=50.):
    """Nearest forward vehicle along the cycle, with current geometry gating.

    Arc distance wraps at the seam. Adjacent/opposing traffic is not a leader;
    crossing obstacles are still handled by the independent emergency guard.
    """
    projections = projections if projections is not None else project_actors(path, actors)
    own_projection = projections.get(own['vehicle_id']) or _project(own['position_city_m'], path)
    length, choices = _length(path), []
    for other in actors:
        if other['vehicle_id'] == own['vehicle_id'] or other.get('type') != 'VEHICLE':
            continue
        if math.dist(own['position_city_m'][:2], other['position_city_m'][:2]) > radius:
            continue
        lateral, progress, _, tangent = projections[other['vehicle_id']]
        if lateral > 1.5 or abs(_angle(other['heading_rad']-tangent)) > math.pi/3:
            continue
        ahead = (progress-own_projection[1]) % length
        if not 0 < ahead <= radius:
            continue
        gap = ahead - (own['length_m']+other['length_m'])/2
        choices.append((ahead, other['vehicle_id'], gap, max(0., other['speed_m_s'])))
    if not choices:
        return None
    _, identifier, gap, speed = min(choices)
    return dict(vehicle_id=identifier, gap_m=gap, speed_m_s=speed)


def idm_acceleration(speed, desired_speed, leader, parameters):
    """Bounded IDM command in m/s²; no truth about future driver actions."""
    speed, desired_speed = max(0., speed), max(.1, desired_speed)
    a, b = parameters['acceleration_m_s2'], parameters['comfortable_brake_m_s2']
    interaction = 0.
    if leader:
        desired_gap = parameters['minimum_gap_m'] + max(0.,
            speed*parameters['time_headway_s'] + speed*(speed-leader['speed_m_s'])/(2*math.sqrt(a*b)))
        interaction = (desired_gap/max(.1, leader['gap_m']))**2
    return max(-4., min(a, a*(1-(speed/desired_speed)**4-interaction)))


def acceleration_action(acceleration, vehicle):
    """Approximate force feedforward to Bullet pedals, not a velocity assignment.

    Engine force is applied at four wheels; Bullet braking is an approximation
    here. The recorded acceleration remains the actual result of physics.
    """
    config = getattr(vehicle, 'config', {})
    mass = config.get('mass') or getattr(vehicle, 'MASS', 1100.)
    # Full braking is treated as a 4 m/s² command scale, not an assertion
    # about Bullet's achieved deceleration or tyre friction.
    scale = 4*config.get('max_engine_force', 1000.)/mass if acceleration >= 0 else 4.
    return max(-1., min(1., acceleration/max(.1, scale)))
