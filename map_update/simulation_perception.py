"""Stage-three preparation: deterministic object-level sensor degradation.

This is a test adapter, not a lidar/camera network or a calibrated sensor model.
It changes observations only; vehicle dynamics and event generation stay intact.
"""
from copy import deepcopy
import random
import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from .simulation_live_map import footprint


PROFILES=('ideal','occlusion_noise_v1')


def degrade_observation(measured, *, profile='ideal', seed=0, step=0, vehicle_id='ego'):
    if profile not in PROFILES:raise ValueError('Unknown perception profile')
    result=deepcopy(measured)
    if profile=='ideal':return result
    origin=result['own_vehicle']['position_city_m'][:2]
    objects=result['current_objects'];shapes=np.array([Polygon(footprint(a)) for a in objects],dtype=object)
    detections=[]
    for index,actor in enumerate(objects):
        key=actor['vehicle_id'];shape=shapes[index]
        points=[actor['position_city_m'][:2],*list(shape.exterior.coords)[:-1]]
        rays=np.array([LineString([origin,point]) for point in points],dtype=object)
        blocked=shapely.intersects(shapes[:,None],rays[None,:]);blocked[index,:]=False
        unblocked=int(np.count_nonzero(~blocked.any(axis=0)))
        # Ignore background silhouettes when most of the box is hidden.
        if unblocked<3:continue
        rng=random.Random(f'{seed}:sensor-v1:{vehicle_id}:{step}:{key}')
        if rng.random()<.05:continue
        row=deepcopy(actor)
        row['position_city_m']=[actor['position_city_m'][j]+rng.gauss(0.,.15) for j in (0,1)]+list(actor['position_city_m'][2:])
        row['measurement_model']='synthetic_object_level_v1'
        detections.append(row)
    result.update(current_objects=detections,observation_scope='synthetic_degraded_object_observations_v1',
                  occlusion_modelled=True,noise_modelled=True,
                  sensor_model=dict(position_sigma_m=.15,independent_dropout_probability=.05,
                                    occlusion='2d_current_actor_boxes_five_rays',calibrated=False,
                                    buildings_modelled=False,identity_association='ideal_actor_ids',
                                    own_pose_noise=False,velocity_noise=False),
                  clearance_coverage_available=False)
    # Dropping a detection never creates free-space evidence. A future beam or
    # occupancy adapter must certify coverage before this can clear a footprint.
    result['free_space_checks']=[]
    return result
