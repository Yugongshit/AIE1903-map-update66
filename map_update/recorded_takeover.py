"""Experimental synchronous takeover of recorded ScenarioNet actors.

Native state only: no camera claims, no future trajectories in observations.
One process owns one MetaDrive world. The data directory must be trusted:
ScenarioNet databases contain Python pickle files.
"""
from copy import deepcopy
import math

from .simulation_interactive import checked_action


def action_batch(step, observation_step, controlled, actions):
    if type(observation_step) is not int or observation_step != step:
        raise ValueError('Actions must refer to the current observation step')
    if not isinstance(actions, dict) or set(actions) != set(controlled):
        raise ValueError('Submit exactly one action per controlled actor, atomically')
    return {key: checked_action(value) for key, value in actions.items()}


class RecordedTakeoverSession:
    """All actors replay until explicitly taken over; no live return-to-log."""

    def __init__(self, database, index=0):
        from metadrive.envs.scenario_env import ScenarioEnv
        from metadrive.policy.replay_policy import ReplayEgoCarPolicy
        from metadrive.policy.env_input_policy import EnvInputPolicy
        from .simulation_interactive import native_environment
        from .simulation_runtime import environment_config
        cls, observation = native_environment(ScenarioEnv)
        config = environment_config(database, mode='replay', render='none', steps=10000,
                                    num_scenarios=1, input_policy=EnvInputPolicy,
                                    replay_policy=ReplayEgoCarPolicy)
        config.update(start_scenario_index=index, physics_world_step_size=.02, decision_repeat=5,
                      no_static_vehicles=False,
                      filter_overlapping_car=False, reactive_traffic=False,
                      agent_observation=observation, horizon=None)
        self.env = cls(config)
        self.controlled = set()
        self.stopped = None
        self.index = index
        self.database = str(database)
        self.branch = None
        try:
            self.env.reset(seed=index)
            self.scenario = self.env.engine.data_manager.current_scenario
            self.sdc = str(self.scenario['metadata']['sdc_id'])
            points = [p for f in self.scenario['map_features'].values()
                      for p in f.get('polyline', f.get('polygon', []))]
            if not points:
                raise ValueError('No mapped coordinates')
            self.bounds = [(min(float(p[i]) for p in points), max(float(p[i]) for p in points)) for i in (0, 1)]
            timestamps = self.scenario['metadata']['ts']
            if any(abs(float(b-a)-.1) > 1e-3 for a,b in zip(timestamps, timestamps[1:])):
                raise ValueError('Initial adapter requires 10 Hz records')
        except BaseException:
            self.close()
            raise

    @property
    def step(self):
        return int(self.env.episode_step)

    def _objects(self):
        manager = self.env.engine.traffic_manager
        objects = {str(k): self.env.engine.get_objects([v])[v]
                   for k,v in manager._scenario_id_to_obj_id.items()}
        objects[self.sdc] = self.env.agent
        return objects

    def _valid(self, key, step):
        track = self.scenario['tracks'].get(key)
        return track is not None and 0 <= step < self.scenario['length'] and bool(track['state']['valid'][step])

    def observe(self):
        # Explicit whitelist: never serialize the scenario, routes or policies.
        rows = {}
        for key, obj in self._objects().items():
            if not self._valid(key, self.step):
                continue
            rows[key] = dict(track_id=key, native_id=str(obj.id),
                             type=self.scenario['tracks'][key]['type'],
                             position_m=[float(v) for v in obj.position[:2]],
                             velocity_m_s=[float(v) for v in obj.velocity[:2]],
                             heading_rad=float(obj.heading_theta), valid=True,
                             dimensions_m={name: float(getattr(obj, name.upper()))
                                           if getattr(obj, name.upper(), None) is not None else None
                                           for name in ('length', 'width', 'height')},
                             mode='external' if key in self.controlled else 'recorded_replay')
        signals = {}
        for key, light in self.scenario['dynamic_map_states'].items():
            value = light['state']['object_state'][self.step]
            signals[str(key)] = str(value)
        return dict(schema='recorded-takeover-observation-1', step=self.step,
                    recorded_time_s=float(self.scenario['metadata']['ts'][self.step]),
                    simulation_time_s=self.step * .1,
                    coordinate_frame='MetaDrive centralized scenario XY, metres',
                    observation_kind='ideal_current_state_not_real_sensors',
                    objects=rows, signals=signals, stopped=self.stopped)

    def map_input(self):
        # Static map is separate from dynamic signals and recorded trajectories.
        return deepcopy(self.scenario['map_features'])

    def takeover(self, ids):
        from metadrive.policy.base_policy import BasePolicy
        from metadrive.policy.env_input_policy import EnvInputPolicy
        keys = list(ids)
        if not keys or len(keys) != len(set(keys)) or self.stopped:
            raise ValueError('Need unique active actor IDs in a running session')
        objects = self._objects()
        for key in keys:
            if (key in self.controlled or key not in objects or not self._valid(key, self.step)
                    or self.scenario['tracks'][key]['type'] != 'VEHICLE'):
                raise ValueError('Takeover requires a currently valid recorded vehicle')
            obj = objects[key]
            values = [*obj.position[:2], *obj.velocity[:2], obj.heading_theta]
            if not all(math.isfinite(float(v)) for v in values):
                raise ValueError('Non-finite vehicle state')
            if not all(lo <= float(obj.position[i]) <= hi for i, (lo, hi) in enumerate(self.bounds)):
                raise ValueError('Vehicle is outside the recorded map envelope')
        # Preserve original snapshot for a separate replay comparison, never teleport back.
        if self.branch is None:
            self.branch = dict(scene_id=self.scenario['id'], index=self.index,
                               step=self.step, initial_observation=deepcopy(self.observe()))
        for key in keys:
            obj = objects[key]
            old = self.env.engine.get_policy(obj.id)
            self.env.engine.add_policy(obj.id, EnvInputPolicy if key == self.sdc else BasePolicy, obj, 0)
            old.destroy()
            obj.set_static(False)
            self.controlled.add(key)
        return self.observe()

    def return_to_replay(self, ids):
        raise ValueError('No live return to replay: open an independent original-record branch')

    def advance(self, actions, *, observation_step):
        if self.stopped:
            raise ValueError(self.stopped)
        checked = action_batch(self.step, observation_step, self.controlled, actions)
        if self.step + 1 >= self.scenario['length']:
            self.stopped = 'recording_end'
            raise ValueError(self.stopped)
        # Stop the whole branch before a controlled actor loses source validity.
        if any(not self._valid(key, self.step+1) for key in self.controlled | {self.sdc}):
            self.stopped = 'controlled_or_sdc_track_end_or_gap'
            raise ValueError(self.stopped)
        objects = self._objects()
        for key, action in checked.items():
            if key != self.sdc:
                objects[key].before_step(action)
        self.env.step(checked.get(self.sdc, [0., 0.]))
        for key in self.controlled - {self.sdc}:
            self._objects()[key].after_step()
        for key in self.controlled:
            obj = self._objects()[key]
            if not all(lo <= float(obj.position[i]) <= hi for i,(lo,hi) in enumerate(self.bounds)):
                self.stopped = 'outside_recorded_map_envelope'
        return self.observe()

    def contacts(self):
        """Evaluator-only solved actor contacts; replay collisions are not planner blame."""
        from .simulation_interactive import physical_contacts
        objects = self._objects()
        actors = {obj.body: dict(object_id=key, native_object_id=str(obj.id),
                                mode='external' if key in self.controlled else 'recorded_replay')
                  for key, obj in objects.items() if hasattr(obj, 'body')}
        return physical_contacts(self.env.engine.physics_world.dynamic_world,
                                 {obj.body: key for key, obj in objects.items() if hasattr(obj, 'body')},
                                 actors, step=self.step)

    def replay_error(self):
        """Evaluator only. Never pass this ground-truth diagnostic to Planning."""
        from metadrive.scenario.parse_object_state import parse_object_state
        errors = {}
        for key, obj in self._objects().items():
            if key not in self.controlled and self._valid(key, self.step):
                expected = parse_object_state(self.scenario['tracks'][key], self.step)
                errors[key] = math.dist(obj.position[:2], expected['position'][:2])
        return max(errors.values(), default=0.)

    def close(self):
        self.env.close()
