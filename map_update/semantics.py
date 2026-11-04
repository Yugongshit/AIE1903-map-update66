"""Human-readable names for source nuScenes labels; no inferred object identity."""

FAMILIES = {
    'vehicle': {'label': '车辆', 'color': '#79bfff'},
    'person': {'label': '人员 / 行人相关', 'color': '#f6cc83'},
    'road_object': {'label': '道路物体', 'color': '#ff958c'},
    'other': {'label': '其他 / 未细分', 'color': '#b6c1cf'},
}

CATEGORIES = {
    'vehicle.car': ('小汽车', 'vehicle', 'car'),
    'vehicle.truck': ('卡车', 'vehicle', 'truck'),
    'vehicle.bus.rigid': ('公交车（单节）', 'vehicle', 'bus'),
    'vehicle.bus.bendy': ('公交车（铰接）', 'vehicle', 'bus'),
    'vehicle.construction': ('工程车辆', 'vehicle', 'truck'),
    'vehicle.motorcycle': ('摩托车', 'vehicle', 'bike'),
    'vehicle.bicycle': ('自行车', 'vehicle', 'bike'),
    'vehicle.trailer': ('挂车', 'vehicle', 'truck'),
    'vehicle.emergency.ambulance': ('救护车', 'vehicle', 'car'),
    'vehicle.emergency.police': ('警车', 'vehicle', 'car'),
    'human.pedestrian.adult': ('行人（成人）', 'person', 'person'),
    'human.pedestrian.child': ('行人（儿童）', 'person', 'person'),
    'human.pedestrian.construction_worker': ('施工人员', 'person', 'person'),
    'human.pedestrian.police_officer': ('警务人员', 'person', 'person'),
    'human.pedestrian.stroller': ('婴儿车', 'person', 'stroller'),
    'human.pedestrian.wheelchair': ('轮椅', 'person', 'stroller'),
    'human.pedestrian.personal_mobility': ('个人代步设备', 'person', 'bike'),
    'movable_object.barrier': ('路障 / 隔离栏', 'road_object', 'barrier'),
    'movable_object.trafficcone': ('交通锥', 'road_object', 'cone'),
    'movable_object.debris': ('杂物 / 碎片', 'road_object', 'box'),
    'movable_object.pushable_pullable': ('可推拉物体', 'road_object', 'box'),
    'static_object.bicycle_rack': ('自行车架', 'road_object', 'rack'),
    'animal': ('动物', 'other', 'box'),
}


def describe_category(category):
    name, family, icon = CATEGORIES.get(category, ('未细分类别', 'other', 'box'))
    return {'category': category, 'name_zh': name, 'family': family,
            'family_zh': FAMILIES[family]['label'], 'color': FAMILIES[family]['color'],
            'icon': icon, 'label_source': 'nuScenes dataset annotation; not a new detector prediction'}
