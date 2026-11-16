"""Build a portable offline 3D review of trusted local ScenarioNet recordings.

Embeds geometry and valid per-frame states; no server, CDN, physics or Planning.
Optional checker reports overlay measured counterfactual traces, never guessed paths.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import pickle
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def native(value):
    if hasattr(value, 'tolist'):
        return native(value.tolist())
    if isinstance(value, dict):
        return {str(k):native(v) for k,v in value.items()}
    if isinstance(value, (list, tuple)):
        return [native(v) for v in value]
    return value


def scene_payload(scene):
    tracks = []
    for key, track in scene['tracks'].items():
        state = track['state']
        frames = []
        for i in range(scene['length']):
            if not bool(state['valid'][i]):
                frames.append(None)
                continue
            def scalar(name):
                if name not in state:
                    return None
                value = native(state[name][i])
                while isinstance(value, list): value = value[0]
                return float(value)
            frames.append(dict(position=native(state['position'][i]),
                               velocity=native(state['velocity'][i]),heading=scalar('heading'),
                               length=scalar('length'),width=scalar('width'),height=scalar('height')))
        tracks.append(dict(id=str(key),type=track['type'],frames=frames))
    features = []
    for key, feature in scene['map_features'].items():
        for kind in ('polyline', 'polygon'):
            if kind in feature:
                features.append(dict(id=str(key),type=feature.get('type','UNKNOWN'),kind=kind,
                                     points=native(feature[kind])))
                break
    signals=[]
    for key, signal in scene['dynamic_map_states'].items():
        signals.append(dict(id=str(key),states=native(signal['state']['object_state'])))
    return dict(id=scene['id'],dataset=scene['metadata'].get('dataset','unknown'),
                sdc=str(scene['metadata']['sdc_id']),times=native(scene['metadata']['ts']),
                tracks=tracks,map=features,signals=signals,branches=[])


def attach_reports(scenes, roots):
    by_id={scene['id']:scene for scene in scenes}
    for root in roots:
        for path in sorted(root.rglob('report.json')):
            report=json.loads(path.read_text())
            scene=by_id.get(report.get('scene_id'))
            if scene is None: continue
            if report.get('physical_map_sha256') != scene['physical_map_sha256']:
                raise ValueError('Report map differs from selected recording: '+str(path))
            for b in report.get('branches',[]):
                if not b.get('trace') or not b.get('initial_objects'): continue
                # Native runtime is centered by ScenarioNet around initial SDC XY.
                # Infer the translation from the same tracked object's recorded pose.
                first=b['controlled'][0]
                track=next((t for t in scene['tracks'] if t['id']==first),None)
                step=b['takeover_step']
                if track is None or not 0 <= step < len(track['frames']) or track['frames'][step] is None:
                    continue
                raw=track['frames'][step]['position']
                actual=b['initial_objects'][first]['position_m']
                offset=[raw[i]-actual[i] for i in (0,1)]
                # Check every controlled actor shares the same source-bound origin.
                for key in b['controlled']:
                    t=next(t for t in scene['tracks'] if t['id']==key)
                    for axis in (0,1):
                        if abs(t['frames'][step]['position'][axis] - b['initial_objects'][key]['position_m'][axis] - offset[axis]) > .02:
                            raise ValueError('Report initial states do not match recording: '+str(path))
                scene['branches'].append(dict(controlled=b['controlled'],start=step,end=b['final_step'],
                    actions=b.get('actions',{}),completed=b.get('completed',False),
                    passed=report.get('passed',False),offset=offset,initial=b['initial_objects'],
                    trace=b['trace'],contacts=b.get('contacts',[]),report_sha256=sha256(path.read_bytes()).hexdigest()))


HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>WOMD · 原记录与接管审阅</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0b1320;color:#e1e9f3;font:14px system-ui,-apple-system,sans-serif}header{padding:18px 24px;border-bottom:1px solid #29364a;display:flex;justify-content:space-between;gap:16px}h1{font-size:21px;margin:0 0 6px}small,.muted{color:#9fb0c4}main{display:grid;grid-template-columns:1fr 320px;height:calc(100vh - 96px)}#viewport{position:relative;min-width:0;background:radial-gradient(ellipse,#1c2c3e,#0b1320)}canvas{display:block;touch-action:none}aside{padding:20px;background:#111e2f;overflow:auto;border-left:1px solid #29364a}label{display:block;margin:15px 0 7px;color:#b5c5d7}select,button{border:1px solid #3b4e65;border-radius:7px;background:#1d3046;color:#fff;padding:9px;font:inherit}select{width:100%}button{cursor:pointer}button:hover{background:#304a65}.row{display:flex;gap:8px;flex-wrap:wrap}.controls{position:absolute;left:18px;right:18px;bottom:18px;background:#0c192beb;padding:14px;border:1px solid #3b4e65;border-radius:12px}input[type=range]{width:100%;margin:12px 0}#stats{position:absolute;top:18px;left:18px;background:#0b1624df;padding:12px;border-radius:8px;pointer-events:none}#tag{position:absolute;display:none;background:#ffaa3a;color:#102030;padding:4px 8px;border-radius:4px;pointer-events:none;font-weight:bold}#error{position:absolute;top:80px;left:20px;right:20px;color:#ff9999;background:#311;padding:12px;display:none}#details{line-height:1.9;overflow-wrap:anywhere}.badge{display:inline-block;color:#5de2cd;background:#153833;padding:4px 8px;border-radius:5px}.legend{line-height:1.9;margin-top:18px}.notice{font-size:12px;line-height:1.7;color:#aebed0;border-top:1px solid #304153;padding-top:16px;margin-top:18px}@media(max-width:800px){main{grid-template-columns:1fr;height:auto}#viewport{height:65vh}aside{border-left:0}header{display:block}}
</style>
<header><div><h1>WOMD · 真实记录场景审阅</h1><span class="muted">原地图与标注轨迹 · 车辆框示意 · 无虚构建筑</span></div><div><span class="badge">离线可视化</span><br><small id="build"></small></div></header>
<main><div id="viewport"><div id="stats"></div><div id="tag"></div><div id="error"></div><div class="controls"><div class="row"><button id="play">播放</button><button id="reset">回到开头</button><button id="fit">全景</button><button id="top">俯视</button><button id="focus">聚焦所选车辆</button><select id="speed" style="width:auto"><option value="1">1×</option><option value="0.5">0.5×</option><option value="2">2×</option></select></div><input id="timeline" type="range" min="0" value="0"><div id="clock"></div></div></div>
<aside><label>真实数据场景</label><select id="scene"></select><label>查看模式</label><select id="branch"></select><p id="mode" class="muted"></p><label>当前有效车辆 · 源对象ID</label><select id="actor"></select><div id="details"></div><div class="legend"><span style="color:#66e0cc">■</span> 原记录车辆　<span style="color:#f6d065">■</span> 采集车<br><span style="color:#ff9f43">■</span> 选中车辆　<span style="color:#df83ff">■</span> 接管后的实测仿真轨迹<br><span style="color:#a0aab8">□</span> 接管车原记录对照位置<br><span style="color:#6bb4d1">—</span> 车道中心线　<span style="color:#d5c9a0">—</span> 道路边界/标线</div><div class="notice">拖动旋转，滚轮缩放，Shift＋拖动平移；点击车辆可选中。道路使用源几何，车辆用标注尺寸方框表示，不是真实相机画面。<br><br>接管模式仅回看已执行测试的逐帧物理状态，测试动作为刹车或小油门，不是 Planning。此页面不执行新动作，不连接实时仿真服务，也不是 Planning 观测接口。未记录的接管帧不会推测补齐。</div></aside></main>
<script>__THREE__</script><script>const DATA=__DATA__;</script>
<script>
'use strict';
const $=id=>document.getElementById(id);let scene,frame=0,selected='',branch=null,playing=false,last=0,elapsed=0;
const viewport=$('viewport'), world=new THREE.Scene(), camera=new THREE.PerspectiveCamera(45,1,.1,10000);
let renderer;
try{renderer=new THREE.WebGLRenderer({antialias:true});renderer.setClearColor(0x0b1320);renderer.setPixelRatio(Math.min(devicePixelRatio,2));viewport.prepend(renderer.domElement);}catch(e){$('error').style.display='block';$('error').textContent='浏览器无法启动 WebGL：'+e.message;throw e;}
world.add(new THREE.AmbientLight(0xffffff,.85));const sun=new THREE.DirectionalLight(0xffffff,.8);sun.position.set(20,-30,100);world.add(sun);
const roads=new THREE.Group(),actors=new THREE.Group(),ghosts=new THREE.Group();world.add(roads,actors,ghosts);
let origin=[0,0,0],center=new THREE.Vector3(),distance=120,yaw=-.8,tilt=.85,meshes=new Map();
function clear(group){while(group.children.length){const o=group.children.pop();o.parent=null;o.geometry?.dispose();if(Array.isArray(o.material))o.material.forEach(m=>m.dispose());else o.material?.dispose();}}
function local(p){return new THREE.Vector3(p[0]-origin[0],p[1]-origin[1],(p[2]??origin[2])-origin[2]);}
function line(points,color,opacity=1){const g=new THREE.BufferGeometry().setFromPoints(points);return new THREE.Line(g,new THREE.LineBasicMaterial({color,transparent:opacity<1,opacity}));}
function cameraUpdate(){camera.up.set(0,0,1);camera.position.set(center.x+distance*Math.sin(tilt)*Math.cos(yaw),center.y+distance*Math.sin(tilt)*Math.sin(yaw),center.z+distance*Math.cos(tilt));camera.lookAt(center);}
function fit(){const pts=scene.tracks.flatMap(t=>t.frames[frame]?[local(t.frames[frame].position)]:[]);const box=new THREE.Box3().setFromPoints(pts.length?pts:roads.children.flatMap(o=>o.geometry.attributes.position?Array.from({length:o.geometry.attributes.position.count},(_,i)=>new THREE.Vector3().fromBufferAttribute(o.geometry.attributes.position,i)):[]));if(box.isEmpty())return;box.getCenter(center);const size=box.getSize(new THREE.Vector3());distance=Math.max(45,Math.max(size.x,size.y)*1.45);cameraUpdate();}
function stateFor(t){const original=t.frames[frame];if(!branch||!branch.controlled.includes(t.id))return original;if(frame<branch.start||frame>branch.end)return null;const row=frame===branch.start?branch.initial[t.id]:branch.trace.find(r=>r.step===frame)?.objects[t.id];if(!row)return null;return {...original,position:[row.position_m[0]+branch.offset[0],row.position_m[1]+branch.offset[1],original?.position[2]??origin[2]],heading:row.heading_rad,velocity:row.velocity_m_s};}
function makeBox(t,s,color,wire=false){const dims=[s.length,s.width,s.height];const known=dims.every(x=>Number.isFinite(x)&&x>0);const g=new THREE.BoxGeometry(known?dims[0]:1,known?dims[1]:1,known?dims[2]:1);const mat=new THREE.MeshStandardMaterial({color,transparent:true,opacity:wire?.35:.9,wireframe:wire||!known,roughness:.8});const mesh=new THREE.Mesh(g,mat);mesh.position.copy(local(s.position));mesh.rotation.z=s.heading;mesh.userData={id:t.id,known};return mesh;}
function update(){clear(actors);clear(ghosts);meshes.clear();const eligible=[];let visible=0;
for(const t of scene.tracks){const s=stateFor(t);if(!s)continue;visible++;const controlled=!!branch?.controlled.includes(t.id),color=controlled?0xdf83ff:t.id===selected?0xff9f43:t.id===scene.sdc?0xf6d065:t.type==='VEHICLE'?0x66e0cc:0xb3c3d9;const mesh=makeBox(t,s,color);actors.add(mesh);meshes.set(t.id,mesh);if(t.type==='VEHICLE')eligible.push(t.id);if(controlled&&t.frames[frame])ghosts.add(makeBox(t,t.frames[frame],0xb6bfcd,true));}
const old=selected;$('actor').replaceChildren(new Option('请选择车辆',''),...eligible.map(id=>new Option(id+(id===scene.sdc?' · 采集车':''),id)));$('actor').value=eligible.includes(old)?old:'';
const track=scene.tracks.find(t=>t.id===selected),s=track&&stateFor(track);$('details').textContent=s?`车辆 ${selected}${selected===scene.sdc?'（采集车）':''} · ${branch?.controlled.includes(selected)?'反事实仿真测试':'原记录回放'}\n速度 ${Math.hypot(...s.velocity).toFixed(2)} m/s\n尺寸 ${s.length?.toFixed(2)??'未知'} × ${s.width?.toFixed(2)??'未知'} × ${s.height?.toFixed(2)??'未知'} m`:(selected?'该对象在此帧没有有效状态':'选择车辆查看状态');$('details').style.whiteSpace='pre-line';
$('timeline').value=frame;const t0=scene.times[0];$('clock').textContent=`第 ${frame} / ${scene.times.length-1} 帧 · ${(scene.times[frame]-t0).toFixed(2)} / ${(scene.times.at(-1)-t0).toFixed(2)} 秒`;
const signals=scene.signals.reduce((out,s)=>{const state=String(s.states[frame]);out[state]=(out[state]||0)+1;return out;},{});
$('stats').textContent=`${scene.id} · 有效对象 ${visible} · 信号状态：${Object.entries(signals).map(([k,v])=>k+' ×'+v).join('，')||'无记录'}`;
window.RECORDED_VIEW_STATE={scene_id:scene.id,frame,visible,selected,mode:branch?'recorded_counterfactual_trace':'original_recording',controlled:branch?.controlled||[]};}
function loadScene(index){playing=false;$('play').textContent='播放';scene=DATA.scenes[index];frame=0;branch=null;selected=scene.sdc;const sdc=scene.tracks.find(t=>t.id===scene.sdc);origin=sdc.frames.find(Boolean).position.slice();if(origin.length<3)origin.push(0);clear(roads);
for(const f of scene.map){if(f.points.length<2)continue;let pts=f.points.map(local);if(f.kind==='polygon')pts=pts.concat([pts[0]]);const lane=f.type.includes('LANE'),edge=f.type.includes('EDGE');roads.add(line(pts,lane?0x4d91ad:edge?0xd5c9a0:0x82949d,lane?.45:.8));}
$('branch').replaceChildren(new Option('原记录完整回放',''),...scene.branches.map((b,i)=>new Option(`测试分支 ${i+1} · 车辆 ${b.controlled.join(' + ')}`,String(i))));$('timeline').min=0;$('timeline').max=scene.times.length-1;$('mode').textContent=scene.branches.length?'可选择已记录接管测试，与灰色原记录位置对照。':'当前只有原记录；没有带逐帧状态的接管报告。';update();fit();}
$('scene').replaceChildren(...DATA.scenes.map((s,i)=>new Option(`${i+1} · ${s.id}`,String(i))));$('scene').onchange=()=>loadScene(Number($('scene').value));
$('branch').onchange=()=>{playing=false;$('play').textContent='播放';branch=$('branch').value===''?null:scene.branches[Number($('branch').value)];frame=branch?branch.start:0;selected=branch?branch.controlled[0]:scene.sdc;$('timeline').min=branch?branch.start:0;$('timeline').max=branch?branch.end:scene.times.length-1;$('mode').textContent=branch?`已记录测试分支 · 第${branch.start}–${branch.end}帧 · 动作${JSON.stringify(branch.actions)} · 报告${branch.passed?'通过':'未通过'} · 接触记录${branch.contacts.length}行（非独立碰撞次数）。显示高度沿用原记录，不代表完整反事实3D姿态。`:'原记录完整回放；所有对象遵循记录，不响应新动作。';elapsed=0;update();fit();};
$('actor').onchange=()=>{selected=$('actor').value;update();};$('timeline').oninput=()=>{frame=Number($('timeline').value);elapsed=0;update();};$('play').onclick=()=>{const end=Number($('timeline').max);if(frame>=end){frame=Number($('timeline').min);update();}playing=!playing;elapsed=0;$('play').textContent=playing?'暂停':'播放';};$('reset').onclick=()=>{playing=false;$('play').textContent='播放';frame=Number($('timeline').min);elapsed=0;update();};$('fit').onclick=fit;$('top').onclick=()=>{tilt=.001;cameraUpdate();};$('focus').onclick=()=>{const m=meshes.get(selected);if(m){center.copy(m.position);distance=35;cameraUpdate();}};
let down=null;renderer.domElement.onpointerdown=e=>{down={x:e.clientX,y:e.clientY,px:e.clientX,py:e.clientY};renderer.domElement.setPointerCapture(e.pointerId);};renderer.domElement.onpointermove=e=>{if(!down)return;const dx=e.clientX-down.px,dy=e.clientY-down.py;if(e.shiftKey){const right=new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld,0),up=new THREE.Vector3().setFromMatrixColumn(camera.matrixWorld,1);center.addScaledVector(right,-dx*distance/700).addScaledVector(up,dy*distance/700);}else{yaw-=dx*.006;tilt=Math.max(.001,Math.min(1.48,tilt+dy*.006));}down.px=e.clientX;down.py=e.clientY;cameraUpdate();};renderer.domElement.onpointerup=e=>{if(down&&Math.hypot(e.clientX-down.x,e.clientY-down.y)<5){const r=renderer.domElement.getBoundingClientRect(),ray=new THREE.Raycaster();ray.setFromCamera(new THREE.Vector2((e.clientX-r.left)/r.width*2-1,-(e.clientY-r.top)/r.height*2+1),camera);const hit=ray.intersectObjects(actors.children).find(h=>scene.tracks.find(t=>t.id===h.object.userData.id)?.type==='VEHICLE');if(hit){selected=hit.object.userData.id;update();}}down=null;};renderer.domElement.onwheel=e=>{e.preventDefault();distance=Math.max(8,Math.min(4000,distance*Math.exp(e.deltaY*.001)));cameraUpdate();};
new ResizeObserver(()=>{const w=viewport.clientWidth,h=viewport.clientHeight;renderer.setSize(w,h);camera.aspect=w/h;camera.updateProjectionMatrix();}).observe(viewport);
$('build').textContent='生成：'+new Date(DATA.generated_at).toLocaleString();loadScene(0);
function animate(now){requestAnimationFrame(animate);const dt=last?Math.min((now-last)/1000,.25):0;last=now;if(playing){elapsed+=dt*Number($('speed').value);let changed=false;const end=Number($('timeline').max);while(frame<end&&elapsed>=scene.times[frame+1]-scene.times[frame]){elapsed-=scene.times[frame+1]-scene.times[frame];frame++;changed=true;}if(frame>=end){playing=false;$('play').textContent='播放';}if(changed)update();}cameraUpdate();renderer.render(world,camera);const mesh=meshes.get(selected),tag=$('tag');if(mesh){const p=mesh.position.clone();p.z+=3;p.project(camera);tag.style.display=Math.abs(p.x)<1&&Math.abs(p.y)<1&&p.z>-1&&p.z<1?'block':'none';tag.style.left=((p.x+1)*viewport.clientWidth/2)+'px';tag.style.top=((-p.y+1)*viewport.clientHeight/2)+'px';tag.textContent=selected;}else tag.style.display='none';}
requestAnimationFrame(animate);
</script></html>'''


def build(database, output, report_roots=()):
    from metadrive.scenario.utils import read_dataset_summary, read_scenario_data
    from map_update.simulation_bridge import feature_sha256
    _, lookup, mapping=read_dataset_summary(str(database))
    scenes=[]
    for name in lookup:
        path=database/mapping[name]/name
        with path.open('rb') as f: scene=pickle.load(f)
        payload=scene_payload(scene)
        payload['physical_map_sha256']=feature_sha256(read_scenario_data(str(path),centralize=True)['map_features'])
        payload['source_sha256']=sha256(path.read_bytes()).hexdigest()
        scenes.append(payload)
    if not scenes: raise ValueError('Database has no scenes')
    attach_reports(scenes, report_roots)
    data=dict(generated_at=datetime.now(timezone.utc).isoformat(),scenes=scenes)
    encoded=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':')).replace('<','\\u003c')
    three=('/* '+(ROOT/'map_update/vendor/three.LICENSE').read_text()+' */\n'+(ROOT/'map_update/vendor/three.min.js').read_text()).replace('</script','<\\/script')
    page=HTML.replace('__THREE__',three).replace('__DATA__',encoded)
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as f:f.write(page)
    print(json.dumps(dict(output=str(output),bytes=output.stat().st_size,
        scenes=[dict(id=s['id'],frames=len(s['times']),branches=len(s['branches'])) for s in scenes]),ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--report-root',type=Path,action='append',default=[])
    a=p.parse_args();build(a.database,a.out,a.report_root)
