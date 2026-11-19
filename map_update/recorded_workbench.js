'use strict';

const byId = id => document.getElementById(id);
let accessKey = '';
let observation = null;
let map = null;
let tokens = {};
let selected = new Set();
let running = false;
let busy = false;
let renderedMap = null;
let yaw = -.8;
let tilt = .8;
let distance = 140;
const center = new THREE.Vector3();
const world = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(45, 1, .1, 20000);
camera.up.set(0, 0, 1);
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setClearColor(0x0e1921);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const viewport = byId('viewport');
viewport.prepend(renderer.domElement);
world.add(new THREE.AmbientLight(0xffffff, 1.5));
const roads = new THREE.Group();
const actors = new THREE.Group();
const states = new THREE.Group();
const intentions = new THREE.Group();
world.add(roads, actors, states, intentions);

function clear(group) {
    for (const child of [...group.children]) {
        group.remove(child);
        child.geometry.dispose();
        child.material.dispose();
    }
}

function line(group, points, color, elevation=0) {
    if (!points || points.length < 2) return;
    const vertices = points.map(p => new THREE.Vector3(p[0], p[1], elevation));
    group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(vertices),
        new THREE.LineBasicMaterial({color})));
}

function geometryLines(geometry) {
    if (!geometry) return [];
    if (geometry.type === 'GeometryCollection') return geometry.geometries.flatMap(geometryLines);
    if (geometry.type === 'LineString') return [geometry.coordinates];
    if (geometry.type === 'Polygon') return geometry.coordinates;
    return [];
}

async function api(path, body) {
    const response = await fetch(path, {
        method:body ? 'POST' : 'GET',
        headers:{Authorization:'Bearer '+accessKey, 'Content-Type':'application/json'},
        body:body ? JSON.stringify(body) : undefined,
        cache:'no-store'
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || '请求失败');
    return data;
}

function binding() {
    if (!observation) throw new Error('请先连接服务');
    return {
        session_id:observation.session_id,
        map_version:observation.map_version,
        state_version:observation.state_version,
        observation_step:observation.step
    };
}

function showError(error) {
    running = false;
    byId('play').textContent = '连续步进';
    byId('error').textContent = error.message;
}

async function guarded(operation) {
    if (busy) return;
    busy = true;
    document.body.dataset.busy = 'true';
    for (const button of document.querySelectorAll('button')) {
        if (!['play','fit','top'].includes(button.id)) button.disabled = true;
    }
    byId('error').textContent = '';
    try { await operation(); }
    catch (error) { showError(error); }
    finally {
        busy = false;
        document.body.dataset.busy = 'false';
        for (const button of document.querySelectorAll('button')) button.disabled = false;
    }
}

function fit() {
    const box = new THREE.Box3().setFromObject(roads);
    if (box.isEmpty()) return;
    box.getCenter(center);
    const size = box.getSize(new THREE.Vector3());
    distance = Math.max(40, Math.max(size.x, size.y)*1.1);
}

function draw() {
    if (!observation) return;
    if (renderedMap !== map.map_version) {
        clear(roads);
        for (const feature of Object.values(map.features)) {
            line(roads, feature.polyline || feature.polygon,
                feature.type.includes('LANE') ? 0x567b91 : 0xa4b1b8);
        }
        if (renderedMap === null) fit();
        renderedMap = map.map_version;
    }
    clear(actors);
    for (const [id, actor] of Object.entries(observation.objects)) {
        const dims = actor.dimensions_m || {};
        const known = [dims.length, dims.width, dims.height].every(v => Number.isFinite(v) && v > 0);
        const color = selected.has(id) ? 0xffb85a : actor.mode === 'external' ? 0xcc8fea : 0x59cdb7;
        const mesh = new THREE.Mesh(new THREE.BoxGeometry(dims.length || 1, dims.width || 1, dims.height || 1),
            new THREE.MeshStandardMaterial({color, wireframe:!known}));
        mesh.position.set(actor.position_m[0], actor.position_m[1], (dims.height || 1)/2);
        mesh.rotation.z = actor.heading_rad;
        mesh.userData.actorId = id;
        actors.add(mesh);
    }
    clear(states);
    for (const feature of observation.road_states.features) {
        for (const points of geometryLines(feature.geometry)) line(states, points, 0xf1ca65, .15);
    }
    byId('frame-info').textContent = observation.scene_id+' · 第 '+observation.step+' 帧 · '+observation.simulation_time_s.toFixed(1)+' 秒';
    byId('status').textContent = observation.service_stopped || observation.stopped || '可推进';
    byId('road-summary').textContent = JSON.stringify(observation.road_states.summary, null, 2);
    byId('versions').replaceChildren();
    for (const [label, key] of [['规划地图','map_version'], ['物理地图','physical_map_version'], ['道路状态','road_state_version']]) {
        const term = document.createElement('dt');
        const value = document.createElement('dd');
        term.textContent = label;
        value.textContent = observation[key];
        byId('versions').append(term, value);
    }
    renderActors();
    window.RECORDED_WORKBENCH_STATE = {
        session_id:observation.session_id, step:observation.step,
        controlled:Object.keys(tokens), map_version:observation.map_version,
        road_state_count:observation.road_states.features.length
    };
}

function renderActors() {
    const query = byId('actor-search').value.trim();
    byId('actors').replaceChildren();
    for (const [id, actor] of Object.entries(observation.objects)) {
        if (actor.type !== 'VEHICLE' || !id.includes(query)) continue;
        const label = document.createElement('label');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.checked = selected.has(id);
        checkbox.onchange = () => {
            if (checkbox.checked) selected.add(id); else selected.delete(id);
            draw();
        };
        const mode = document.createElement('span');
        mode.textContent = actor.mode === 'external' ? '外部控制' : '原记录';
        label.append(checkbox, document.createTextNode(id), mode);
        byId('actors').append(label);
    }
}

async function refresh() {
    const current = await api('/observation');
    if (observation && current.session_id !== observation.session_id) {
        running = false;
        selected.clear();
        renderedMap = null;
    }
    observation = current;
    map = await api('/map');
    tokens = (await api('/controls')).control_tokens;
    const previousActor = byId('plan-actor').value;
    byId('plan-actor').replaceChildren(...Object.keys(tokens).map(id => new Option(id, id)));
    if (tokens[previousActor]) byId('plan-actor').value = previousActor;
    const versions = await api('/map-versions');
    byId('map-version').replaceChildren(...versions.revisions.map(r => new Option(r.version, r.version)));
    const plans = await api('/plans');
    clear(intentions);
    for (const value of Object.values(plans)) {
        if (value.state_version === observation.state_version)
            line(intentions, value.plan.points.map(p => p.position_m), 0xffa456, .3);
    }
    draw();
}

async function advance() {
    const action = [Number(byId('steering').value), Number(byId('throttle').value)];
    const actions = Object.fromEntries(Object.keys(tokens).map(id => [id, action]));
    await api('/advance', {...binding(), actions, control_tokens:tokens});
    await refresh();
}

byId('connect-form').onsubmit = event => {
    event.preventDefault();
    guarded(async () => {
        accessKey = byId('access-key').value;
        const catalog = await api('/catalog');
        byId('scene').replaceChildren(...catalog.scenes.map(s => new Option(s.name, s.index)));
        await refresh();
        byId('connection').textContent = '已连接 · 状态级回放与物理接管';
    });
};
byId('refresh').onclick = () => guarded(refresh);
byId('step').onclick = () => guarded(advance);
byId('open').onclick = () => guarded(async () => {
    running = false;
    await api('/open', {session_id:observation.session_id,
        index:Number(byId('scene').value), step:Number(byId('start-step').value)});
    await refresh();
});
byId('takeover').onclick = () => guarded(async () => {
    const scope = byId('scope').value === 'radius' ?
        {kind:'radius', radius_m:Number(byId('radius').value)} : {kind:'global'};
    await api('/takeover', {...binding(), actor_ids:[...selected].filter(id => !tokens[id]), observation_scope:scope, coverage_policy:byId('coverage-policy').value});
    await refresh();
});
byId('select-all').onclick = () => {
    if (!observation) return;
    selected = new Set(Object.entries(observation.objects).filter(([,a]) => a.type === 'VEHICLE').map(([id]) => id));
    draw();
};
byId('actor-search').oninput = () => { if (observation) renderActors(); };
byId('play').onclick = () => {
    running = !running;
    byId('play').textContent = running ? '暂停' : '连续步进';
};
setInterval(() => {
    if (running && !busy && observation) guarded(advance);
}, 100);
setInterval(() => {
    if (accessKey && observation && !running && !busy && !document.hidden) guarded(refresh);
}, 1500);
byId('resume').onclick = () => guarded(async () => {
    await api('/resume', binding());
    await refresh();
});
byId('payload-file').onchange = async event => {
    const file = event.target.files[0];
    if (file) byId('payload').value = await file.text();
};
byId('evidence').onclick = () => guarded(async () => {
    const payload = JSON.parse(byId('payload').value);
    await api('/evidence', {...binding(), submission_id:crypto.randomUUID(),
        scene_id:observation.scene_id, time_basis:'simulation_time_ns',
        events:Array.isArray(payload) ? payload : payload.events || [payload]});
    await refresh();
});
byId('structure-evidence').onclick = () => guarded(async () => {
    const payload = JSON.parse(byId('payload').value);
    const result = await api('/structure-evidence', {...binding(), request_id:crypto.randomUUID(),
        records:payload.records || [payload]});
    byId('task-result').textContent = '已接收结构证据 '+result.accepted.join(', ');
});
byId('patch').onclick = () => guarded(async () => {
    const payload = JSON.parse(byId('payload').value);
    await api('/map-patch', {...binding(), request_id:crypto.randomUUID(),
        operations:payload.operations, provenance:payload.provenance});
    await refresh();
});
byId('rollback').onclick = () => guarded(async () => {
    await api('/map-rollback', {...binding(), request_id:crypto.randomUUID(),
        target_version:byId('map-version').value,
        provenance:{source:'workbench_operator', reason:'operator_requested_rollback', synthetic:false}});
    await refresh();
});
byId('submit-plan').onclick = () => guarded(async () => {
    const payload = JSON.parse(byId('payload').value);
    const plan = payload.plan || payload;
    await api('/plans', {...binding(), plan, control_token:tokens[plan.actor_id]});
    await refresh();
});
byId('goal').onclick = () => guarded(async () => {
    const id = byId('plan-actor').value;
    if (!byId('goal-x').value || !byId('goal-y').value) throw new Error('请填写目标坐标');
    const result = await api('/tasks', {...binding(), actor_id:id, control_token:tokens[id],
        goal_m:[Number(byId('goal-x').value), Number(byId('goal-y').value)]});
    byId('task-result').textContent = JSON.stringify(result.task, null, 2);
    await refresh();
});
byId('report').onclick = () => guarded(async () => {
    const result = await api('/report');
    const url = URL.createObjectURL(new Blob([JSON.stringify(result,null,2)], {type:'application/json'}));
    const link = document.createElement('a');
    link.href = url;
    link.download = 'recorded-experiment-report.json';
    link.click();
    URL.revokeObjectURL(url);
});
byId('fit').onclick = fit;
byId('top').onclick = () => { tilt = .001; };
let pointer = null;
renderer.domElement.onpointerdown = event => {
    pointer = {x:event.clientX, y:event.clientY};
    renderer.domElement.setPointerCapture(event.pointerId);
};
renderer.domElement.onpointermove = event => {
    if (!pointer) return;
    const dx = event.clientX-pointer.x;
    const dy = event.clientY-pointer.y;
    if (event.shiftKey) {
        center.x -= dx*distance/600;
        center.y += dy*distance/600;
    } else {
        yaw -= dx*.006;
        tilt = Math.max(.001, Math.min(1.48, tilt+dy*.006));
    }
    pointer = {x:event.clientX, y:event.clientY};
};
renderer.domElement.onpointerup = () => { pointer = null; };
renderer.domElement.onwheel = event => {
    event.preventDefault();
    distance = Math.max(8, Math.min(10000, distance*Math.exp(event.deltaY*.001)));
};
new ResizeObserver(() => {
    renderer.setSize(viewport.clientWidth, viewport.clientHeight);
    camera.aspect = viewport.clientWidth/viewport.clientHeight;
    camera.updateProjectionMatrix();
}).observe(viewport);
function animate() {
    requestAnimationFrame(animate);
    camera.position.set(center.x+distance*Math.sin(tilt)*Math.cos(yaw),
        center.y+distance*Math.sin(tilt)*Math.sin(yaw), center.z+distance*Math.cos(tilt));
    camera.lookAt(center);
    renderer.render(world,camera);
}
animate();
