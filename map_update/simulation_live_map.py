"""Causal per-vehicle occupancy evidence, independent of generator event plans.

This baseline publishes observed stopped-vehicle footprints, never road closures
or permanent structure edits. Missing evidence becomes stale, not cleared.
"""
from copy import deepcopy
import hashlib
import json
import math

from shapely.geometry import Polygon
from .simulation_planner import _project, _angle


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def footprint(actor):
    x, y = actor['position_city_m'][:2]
    c, s = math.cos(actor['heading_rad']), math.sin(actor['heading_rad'])
    length, width = actor['length_m'], actor['width_m']
    return [[x+c*dx-s*dy, y+s*dx+c*dy] for dx, dy in
            [(-length/2,-width/2),(length/2,-width/2),(length/2,width/2),(-length/2,width/2)]]


class ObservedStateMap:
    def __init__(self, road_features, road_version, *, stationary_s=2., stale_s=3.):
        self.road_version = road_version
        self.lanes = {k: v['polyline'] for k,v in road_features.items()
                      if v.get('type') in {'LANE_SURFACE_STREET','LANE_SURFACE_UNSTRUCTURE','LANE_FREEWAY'}
                      and len(v.get('polyline', [])) >= 2}
        self.stationary_s, self.stale_s = stationary_s, stale_s
        self.tracks, self.events, self.lane_cache = {}, {}, {}
        self.step, self.time_s, self.revision = -1, 0., 0
        self.version = digest([road_version, []])
        self.known_versions = {self.version: -1}

    def _lane(self, actor):
        cached = self.lane_cache.get(actor['vehicle_id'])
        if cached and math.dist(cached[0], actor['position_city_m'][:2]) < .5 and abs(_angle(cached[1]-actor['heading_rad'])) < .1:
            return cached[2]
        matches=[]
        for key, points in self.lanes.items():
            distance, _, _, heading = _project(actor['position_city_m'], points)
            if distance <= 2. and abs(_angle(actor['heading_rad']-heading)) < math.pi/4:
                matches.append((distance, key))
        matches.sort()
        lane = None if not matches or (len(matches)>1 and matches[1][0]-matches[0][0]<.25) else matches[0][1]
        self.lane_cache[actor['vehicle_id']] = (actor['position_city_m'][:2], actor['heading_rad'], lane)
        return lane

    def update(self, step, time_s, perception):
        if step < self.step or time_s < self.time_s:
            raise ValueError('Observed-state clock cannot rewind; create a new session')
        if step == self.step:
            return  # Polling, cameras and duplicate packets cannot add evidence.
        self.step, self.time_s = step, time_s
        before = deepcopy(self.events)
        for actor in perception['current_objects']:
            if actor.get('type') != 'VEHICLE':
                continue
            identifier = actor['vehicle_id']
            previous = self.tracks.get(identifier)
            stable = (previous is not None and previous['hits']>0 and time_s-previous['seen'] <= .5
                      and math.dist(actor['position_city_m'][:2],previous['anchor'])<=.75)
            if actor['speed_m_s'] <= .25:
                first = previous['first'] if stable else time_s
                hits = previous['hits']+1 if stable else 1
                anchor = previous['anchor'] if stable else actor['position_city_m'][:2]
            else:
                first, hits, anchor = time_s, 0, actor['position_city_m'][:2]
            self.tracks[identifier] = dict(first=first,hits=hits,anchor=list(anchor),seen=time_s)
            event = self.events.get(identifier)
            if hits >= 5 and time_s-first >= self.stationary_s:
                # A stationary observation is evidence of occupancy, not of
                # an accident, intention, permanent change or an impassable lane.
                self.events[identifier] = dict(event_id='observed-stop:'+identifier, actor_id=identifier,
                    kind='stationary_vehicle_occupancy', status='active', geometry_city_m=footprint(actor),
                    lane_id=self._lane(actor), lane_closed=False, restriction='footprint_occupancy_only',
                    first_observed_stationary_s=first, last_observed_s=time_s, evidence_step=step,
                    supporting_samples=hits, source='ideal_simulated_vehicle_observations',
                    confidence_kind='rule_threshold_not_calibrated_probability', synthetic=True)
            elif event and event['status'] != 'cleared' and actor['speed_m_s'] >= .75:
                if Polygon(footprint(actor)).disjoint(Polygon(event['geometry_city_m'])):
                    self.events[identifier] = dict(event,status='cleared',evidence_step=step,
                        last_observed_s=time_s,clear_reason='observed_actor_moving_outside_previous_footprint')
        for identifier, event in self.events.items():
            if event['status']=='active' and time_s-event['last_observed_s']>self.stale_s:
                event.update(status='stale',evidence_step=step)
        # Unconfirmed, departed tracks are not permanent history. Published
        # events retain their explicit stale/cleared semantics.
        self.tracks = {k:v for k,v in self.tracks.items() if time_s-v['seen']<=self.stale_s or k in self.events}
        if before != self.events:
            self.revision += 1
            self.version = digest([self.road_version, self.revision, self.events])
            self.known_versions[self.version] = step
            while len(self.known_versions)>64:
                del self.known_versions[next(iter(self.known_versions))]

    def snapshot(self, policy='observed'):
        if policy not in {'observed','baseline'}:
            raise ValueError('Map policy must be observed or baseline')
        baseline = policy=='baseline'
        return dict(schema='observed-road-state-1.0',road_version=self.road_version,
            state_version=digest([self.road_version, []]) if baseline else self.version,
            state_revision=0 if baseline else self.revision, observed_through_step=self.step,
            observed_through_time_s=self.time_s, events=[] if baseline else deepcopy(list(self.events.values())),
            map_policy=policy, structural_writes=0, source='per_vehicle_causal_observations',
            no_observation_is_not_clearance=True)

    def accepts_version(self, version, observation_step):
        if version==digest([self.road_version, []]):
            return True
        return version in self.known_versions and self.known_versions[version]<=observation_step
