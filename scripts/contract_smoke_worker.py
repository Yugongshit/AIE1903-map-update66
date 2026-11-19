"""Synthetic contract fixture with fixed braking actions; no planning algorithm."""
import json
import math
import sys

for line in sys.stdin:
    request = json.loads(line)
    observation = request['observation']
    plans = []
    for actor_id in request['controlled_actor_ids']:
        actor = observation['objects'][actor_id]
        speed = math.hypot(*actor['velocity_m_s'])
        plans.append(dict(
            plan_id=f"smoke-{actor_id}-{observation['step']}", actor_id=actor_id,
            coordinate_frame=observation['coordinate_frame'],
            points=[dict(time_offset_s=offset,
                         position_m=[p+v*offset for p,v in zip(actor['position_m'], actor['velocity_m_s'])],
                         speed_m_s=speed, heading_rad=actor['heading_rad']) for offset in (0., .1)]))
    print(json.dumps(dict(plans=plans, actions={key:[0., -1.] for key in request['controlled_actor_ids']},
                          implementation='synthetic_contract_smoke_fixed_brake', state_version=observation['state_version'])), flush=True)
