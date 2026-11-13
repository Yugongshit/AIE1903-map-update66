'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const COLORS = {cyan:0x65e2cb,amber:0xf3bc65,yellow:0xf3df75,purple:0xbe9beb,red:0xf39393};
  const S = {catalog:[],scene:null,key:null,step:0,snapshot:null,mode:'review',version:'updated',tab:'states',selected:null,playing:false,speed:1,request:0,sceneRequest:0,snapshotAbort:null,validation:null,engine:{busy:false,ready:false,last_result:null},engineCommandBusy:false,engineRequest:0,engineCamera:null,fleet:null,fleetConfig:null,fleetControlPending:false};
  const text = (id, value) => { $(id).textContent = value; };
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const clamp = (v,a,b) => Math.max(a,Math.min(b,v));
  const keyOf = item => item.key || item.id;
  const version = () => S.version === 'original' ? 'original' : 'updated';
  const timeText = value => { const n=Math.max(0,Number(value)||0); return String(Math.floor(n/60)).padStart(2,'0')+':'+(n%60).toFixed(1).padStart(4,'0'); };
  const percent = value => value == null ? '—' : (100*value).toFixed(1)+'%';
  const esc = value => String(value ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const short = (value,n=33) => String(value||'').length>n ? String(value).slice(0,n-1)+'…' : String(value||'');
  const opName = op => ({add:'新增',replace:'修改',delete:'移除'}[op]||op);
  const statusName = value => ({scene_overview:'全场景固定',observed_active:'观测活动',unknown:'当前未知',stale:'已过期',cleared:'已清除'}[value]||value||'未观测');
  const uncertainState = value => !['observed_active','scene_overview'].includes(value);
  const categoryName = value => { const s=String(value||'').toLowerCase();return s.includes('barrier')?'路障':s.includes('cone')?'锥桶':s.includes('debris')?'杂物':s.includes('pedestrian')?'行人':s.includes('cycl')?'骑行者':s.includes('construct')?'施工':s.includes('vehicle')?'车辆':'对象'; };
  const fieldName = key => ({source_lane_type:'车道用途',vehicle_access:'通行权限',source_marking_type:'标线类型',source_left_lane_mark_type:'左标线',source_right_lane_mark_type:'右标线',is_intersection:'路口属性',type:'类型',entry_lanes:'前驱连接',exit_lanes:'后继连接',left_neighbor:'左侧连接',right_neighbor:'右侧连接'}[key]||key);
  const eventId = event => event.properties?.event_id || event.id;
  const eventProps = event => event.properties || event;
  const events = () => (S.scene?.state_overview||S.snapshot?.states)?.events || [];
  const objects = () => (S.scene?.state_overview||S.snapshot?.states)?.objects || [];
  function toast(message) { text('toast',message);$('toast').hidden=false;clearTimeout(S.toastTimer);S.toastTimer=setTimeout(()=>$('toast').hidden=true,5500); }
  function element(tag,cls,content) {const node=document.createElement(tag);if(cls)node.className=cls;if(content!=null)node.textContent=content;return node;}
  async function api(path, options={}) {const response=await fetch(path,{...options,headers:{...(options.body?{'Content-Type':'application/json'}:{}),...options.headers}});let data;try{data=await response.json();}catch{throw new Error('服务未返回有效数据（HTTP '+response.status+'）');}if(!response.ok)throw new Error(data.error || data.message || '请求失败：'+response.status);return data;}
  function setLoading(on,message='正在加载场景数据') {$('viewport-loading').hidden=!on;text('loading-text',message);}
  function showError(error) {setLoading(false);$('viewport-error').hidden=false;text('viewport-error-text',error.message||error);}
  function pathsOf(feature) {if(!feature)return[];const paths=[];if(feature.polyline?.length>1)paths.push(feature.polyline);if(feature.polygon?.length>2){const ring=feature.polygon.slice();if(ring[0][0]!==ring.at(-1)[0]||ring[0][1]!==ring.at(-1)[1])ring.push(ring[0]);paths.push(ring);}return paths;}
  function boundsOf(paths) {const p=paths.flat().filter(v=>Array.isArray(v)&&finite(v[0])&&finite(v[1]));if(!p.length)return null;return [Math.min(...p.map(p=>p[0])),Math.min(...p.map(p=>p[1])),Math.max(...p.map(p=>p[0])),Math.max(...p.map(p=>p[1]))];}
  function pointsCenter(paths) {const b=boundsOf(paths);return b?[(b[0]+b[2])/2,(b[1]+b[3])/2]:null;}
  function currentActors(step=S.step) {
    let actors=S.snapshot?.step===step?S.snapshot.actors:null;
    if(!actors) {
      actors=[];
      for(const track of Object.values(S.scene?.tracks||{})) {
        if(!track.valid[step])continue;
        const item={};
        for(const k of ['id','type','category','is_ego','persistent'])item[k]=track[k];
        for(const k of ['position','heading','length','width','height','velocity'])item[k]=track[k][step];
        actors.push(item);
      }
    }
    // The preview has a fixed static world; the current snapshot remains the
    // time-bounded source of truth for Planning and moving traffic.
    if(fleetActive())actors=[...actors,...fleetVehicles().filter(row=>fleetPosition(row)).map(row=>({id:'fleet-'+row.vehicle_id,type:'VEHICLE',category:'vehicle',is_ego:false,persistent:true,fleet_vehicle:row.vehicle_id,position:fleetPosition(row),heading:row.heading_rad||0,length:row.length_m||4.5,width:row.width_m||1.8,height:row.height_m||1.5}))];
    const fixed=S.scene?.static_overview_actors;
    if(!fixed)return actors;
    const ids=new Set(fixed.map(item=>item.id));
    return [...actors.filter(item=>!ids.has(item.id)),...fixed];
  }
  function currentEgo(){return currentActors().find(a=>a.is_ego)||null;}

  function fleetVehicles() {
    const fleet=S.fleet;if(!fleet)return [];
    const profiles=fleet.service?.per_vehicle||{};
    const rows=Array.isArray(fleet.vehicles)?fleet.vehicles:Object.entries(fleet.vehicles||{}).map(([id,row])=>({vehicle_id:id,...row}));
    return rows.map(row=>({...profiles[row.vehicle_id||row.id],...row,vehicle_id:row.vehicle_id||row.id}));
  }
  function fleetActive(){return Boolean(S.fleet&&S.engine.scene_id===S.key&&S.engine.version===version());}
  function fleetPoints(row,kind) {
    if(kind==='trace')return (row.actual_trace||[]).map(p=>p.position_city_m||p.position_scenario_m||p).filter(p=>Array.isArray(p)&&finite(p[0])&&finite(p[1]));
    const route=row[kind+'_route_points_city']||row[kind+'_route_points']||row[kind+'_route'];
    const points=Array.isArray(route)?route:route?.points||route?.polyline||[];
    return points.filter(p=>Array.isArray(p)&&finite(p[0])&&finite(p[1]));
  }
  function fleetPosition(row){return row.position_city_m||row.position_scenario_m||row.position;}
  function cameraTargetHeading(camera){if(camera.target==='ego')return currentEgo()?.heading||0;const id=String(camera.target||'').replace(/^recipient-/, '');return fleetVehicles().find(row=>row.vehicle_id===id)?.heading_rad||0;}
  function fleetReason(reason){return ({initial_delivery:'首次收到共享地图',map_structure_received:'收到道路结构更新',attached_state_received:'收到附着状态更新',goal_changed:'目标已更改',vehicle_geometry_changed:'车辆尺寸已改变',no_topology_path:'道路连接不通，无法到达',blocked:'道路受阻',shared_state_blocks_endpoint_lane:'已知障碍阻断起点或终点车道',shared_state_would_reassign_endpoint:'障碍阻断当前车道，停止规划',shared_state_no_directed_detour:'障碍封路，源地图没有可行绕路',shared_map_braking_horizon:'为共享地图中的障碍制动',local_actor_braking_horizon:'为近处交通参与者制动',no_accessible_lane_centerlines:'没有允许此车通行的车道',no_supported_lane_centerlines:'没有可规划的车道中心线',no_forward_start_lane:'没有同向起始车道',start_or_goal_too_far_from_lane:'起点或目标离车道过远',goal_behind_on_same_lane:'目标在当前车道后方',no_forward_path_into_goal_radius:'没有到达目标的前向路径',route_tracking_error:'偏离规划路线，已停车',goal_missed_without_reverse_route:'驶过目标且无倒车路线，已停车',initial_plan:'初始规划',map_update:'收到地图更新',state_update:'收到状态更新',route_blocked:'原路线受阻',no_path:'当前地图无可达路线',goal_reached:'已到目标',subscription_disabled:'已暂停订阅',no_route:'没有可行路线',obstacle_ahead:'前方障碍',dynamic_obstacle:'等待动态障碍',collision:'发生碰撞'}[reason]||reason||'等待规划');}
  function setNodeText(node,value){const next=String(value??'—');if(node.textContent!==next)node.textContent=next;}
  function renderFleet() {
    const active=fleetActive(),rows=active?fleetVehicles():[];
    $('fleet-runtime').hidden=!active;setNodeText($('export-planning-label'),active?'导出 B 的 Planning 输入':'导出 Planning 数据');setNodeText($('export-planning-dialog'),active?'导出 B 的 Planning 输入':'导出当前帧 Planning JSON');setNodeText($('planning-description'),active?'按车辆导出它实际收到的道路与状态、独立目标及当前物理状态。B / C 卡片可分别导出；未收到的共享更新不会混入。':'导出当前道路图与所选时刻已观测的状态。画面中的全场景预览包含后续观测，预览信息不混入当前帧导出。');
    setNodeText($('fleet-summary'),active?`${S.fleet.mode==='online'?'在线订阅':'离线先验'} · ${{old:'原始地图',structure:'道路结构',full:'结构 + 状态'}[S.fleet.variant]} · B / C 独立规划 · ${S.mode==='review'?'审阅引擎当前帧':'真实引擎运行'}`:'让 B / C 使用共享地图独立规划');
    if(!active){$('fleet-vehicles').replaceChildren();delete $('fleet-vehicles').dataset.vehicles;return;}
    setNodeText($('fleet-clock'),`当前引擎帧 ${S.step} · ${S.fleet.mode==='online'?'仅截至当前时刻的观测':S.fleet.variant==='full'?'全场景先验，包含后续观测':S.fleet.variant==='structure'?'仅道路结构先验，不含附着状态':'原始地图，不含共享更新'}`);
    $('fleet-publish').hidden=S.fleet.mode!=='online';
    const host=$('fleet-vehicles'),ids=rows.map(r=>r.vehicle_id).join('|');
    if(host.dataset.vehicles!==ids){
      host.replaceChildren();host.dataset.vehicles=ids;
      for(const row of rows){const id=row.vehicle_id,card=element('article','fleet-vehicle');card.dataset.vehicle=id;
        const heading=element('div','fleet-vehicle-heading');heading.append(element('strong','',`${id} · 受控规划车辆`));
        const label=element('label'),check=element('input');check.type='checkbox';check.dataset.subscription=id;check.onchange=()=>fleetCommand({action:'subscription',vehicle_id:id,enabled:check.checked});label.append(check,document.createTextNode('订阅'));heading.append(label);card.append(heading);
        for(const name of ['version','motion','reason']){const p=element('p');p.dataset.field=name;card.append(p);}
        const actions=element('div','fleet-vehicle-actions'),follow=element('button','button outline fleet-follow',`跟随 ${id}`);follow.dataset.followFleet=id;follow.onclick=()=>followFleetVehicle(id);actions.append(follow);const button=element('button','text-button',`导出 ${id} 的 Planning 输入 ↗`);button.onclick=()=>exportFleetPlanning(id);actions.append(button);card.append(actions);host.append(card);
      }
    }
    for(const row of rows){const card=[...host.children].find(c=>c.dataset.vehicle===row.vehicle_id);if(!card)continue;
      const subscription=card.querySelector('input'),enabled=row.subscription_enabled??row.subscribed??row.subscription??true;
      subscription.checked=S.pendingSubscription?.vehicle_id===row.vehicle_id?S.pendingSubscription.enabled:Boolean(enabled);subscription.parentElement.hidden=S.fleet.mode!=='online';
      const received=row.received_version??row.map_version??'未接收',step=row.received_step;
      setNodeText(card.querySelector('[data-field="version"]'),`已收版本 ${short(typeof received==='object'?JSON.stringify(received):received,20)} · 接收帧 ${step??'—'} · 已知状态 ${row.known_state_objects??0}`);
      const pos=fleetPosition(row),speed=Number(row.speed_m_s)||0;
      setNodeText(card.querySelector('[data-field="motion"]'),`${(speed*3.6).toFixed(1)} km/h${pos?` · XY ${pos[0].toFixed(1)}, ${pos[1].toFixed(1)}`:''} · 实际轨迹 ${fleetPoints(row,'trace').length} 点`);
      const reason=row.stopped_reason||row.stop_reason||row.braking?.reason||row.last_replan_reason||row.replan_reason;
      setNodeText(card.querySelector('[data-field="reason"]'),`${fleetReason(reason)} · 重规划 ${row.replan_count??0} 次`);
      card.querySelector('[data-field="version"]').title=String(received);
      const follow=card.querySelector('[data-follow-fleet]'),following=S.engineDisplayedFrame?.result?.camera?.target==='recipient-'+row.vehicle_id;follow.classList.toggle('active',following);follow.setAttribute('aria-pressed',String(following));
    }
    syncFleetControls();
  }
  function syncFleetControls() {
    const replaying=(S.mode==='engine'||fleetActive())&&S.playing,locked=Boolean(S.engineCommandBusy)||replaying;
    for(const id of ['start-fleet','fleet-mode','fleet-variant','fleet-goal-B-x','fleet-goal-B-y','fleet-goal-C-x','fleet-goal-C-y']){const disabled=locked||!S.scene;if($(id).disabled!==disabled)$(id).disabled=disabled;}
    // Publication/subscription can interrupt playback; they do not flicker on
    // every internal render command. Their explicit operation owns this lock.
    const disabled=Boolean(S.fleetControlPending)||!fleetActive()||Boolean(S.engineFrameError);
    $('fleet-publish').disabled=disabled;
    document.querySelectorAll('[data-subscription]').forEach(input=>{input.disabled=disabled;});
    document.querySelectorAll('[data-follow-fleet]').forEach(button=>{button.disabled=!fleetActive()||Boolean(S.engineFrameError);});
  }
  async function loadFleetMissions(key) {
    for(const id of ['B','C'])for(const axis of ['x','y']){$(`fleet-goal-${id}-${axis}`).value='';$(`fleet-goal-${id}-${axis}`).placeholder='默认';}
    try{const data=await api(`/api/scenes/${encodeURIComponent(key)}/fleet`);if(key!==S.key)return;S.fleetMissions=data;
      const missions=Array.isArray(data.missions)?data.missions:Object.entries(data.missions||{}).map(([id,m])=>({vehicle_id:id,...m}));
      for(const mission of missions){const id=mission.vehicle_id||mission.id,goal=mission.goal_city_m||mission.goal_scenario_m||mission.goal;if(!['B','C'].includes(id)||!Array.isArray(goal))continue;for(const [index,axis]of ['x','y'].entries())$(`fleet-goal-${id}-${axis}`).placeholder=Number(goal[index]).toFixed(1);}
    }catch(error){if(key===S.key)S.fleetMissionError=error.message;}
  }
  async function launchFleet(){
    if(!S.scene||S.engineCommandBusy)return;
    const config={mode:$('fleet-mode').value,variant:$('fleet-variant').value},goals={};
    for(const id of ['B','C']){const values=['x','y'].map(axis=>$(`fleet-goal-${id}-${axis}`).value.trim());if(values.every(v=>!v))continue;if(values.some(v=>!v)||values.some(v=>!Number.isFinite(Number(v)))){toast(`${id} 的目标需要完整、有效的 X 和 Y。`);return;}goals[id]=values.map(Number);}
    if(Object.keys(goals).length)config.goals=goals;
    S.version='updated';$('map-version').value='updated';S.validation=null;$('fleet-panel').open=true;
    await launchEngine(config);
  }
  async function followFleetVehicle(vehicleId){
    if(!fleetActive())return;
    stopPlayback();setMode('engine');$('engine-placeholder').hidden=true;$('engine-frame').hidden=!hasDisplayedFrame()||S.engineDisplayedFrame.step!==S.step;
    updateCameraButtons(null);await engineCommand({command:'camera',preset:'follow',target:'recipient-'+vehicleId});
  }
  async function fleetCommand(action){
    if(!fleetActive()||S.fleetControlPending)return;
    S.pendingSubscription=action.action==='subscription'?action:null;
    stopPlayback();S.fleetControlPending=true;syncFleetControls();
    try{const key=S.key;while(S.engineCommandBusy&&key===S.key)await new Promise(resolve=>setTimeout(resolve,40));if(key===S.key&&fleetActive())await engineCommand({command:'fleet',...action});}
    finally{S.fleetControlPending=false;S.pendingSubscription=null;renderFleet();}
  }

  class ReviewRenderer {
    constructor() {
      this.host=$('review-view');this.renderer=null;this.labels=[];this.selectionNodes=[];this.frame=0;this.drag=null;
      this.cameraConfig={yaw:25,pitch:49,distance:57,pan:[0,0],target:'ego',fixed:[0,0]};
      if(!window.THREE)throw new Error('本地三维组件未载入，请检查 vendor/three.min.js。');
      try{this.renderer=new THREE.WebGLRenderer({antialias:true,alpha:false,powerPreference:'high-performance'});}catch{throw new Error('浏览器无法创建 WebGL 三维视图。请启用硬件加速，或使用 MetaDrive 引擎视图。');}
      this.renderer.setPixelRatio(Math.min(devicePixelRatio||1,2));this.renderer.setClearColor(0x142431,1);this.renderer.outputColorSpace=THREE.SRGBColorSpace;
      this.host.appendChild(this.renderer.domElement);this.world=new THREE.Scene();this.world.fog=new THREE.Fog(0x142431,550,1400);
      this.camera=new THREE.PerspectiveCamera(48,1,.2,5000);this.camera.up.set(0,0,1);
      this.world.add(new THREE.HemisphereLight(0xc8e2ef,0x243843,2.5));const sun=new THREE.DirectionalLight(0xf8e9d0,2);sun.position.set(-30,-80,160);this.world.add(sun);
      this.groups={};this.actorMeshes=new Map();this.origin=[0,0];this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(this.host);
      const surface=$('viewport');surface.addEventListener('contextmenu',e=>e.preventDefault());surface.addEventListener('pointerdown',e=>this.pointerDown(e));surface.addEventListener('pointermove',e=>this.pointerMove(e));surface.addEventListener('pointerup',e=>this.pointerUp(e));surface.addEventListener('pointercancel',e=>this.pointerUp(e));surface.addEventListener('wheel',e=>this.wheel(e),{passive:false});
      this.animate=()=>{this.render();this.frame=requestAnimationFrame(this.animate);};this.animate();
    }
    disposeGroup(group){if(!group)return;this.world.remove(group);group.traverse(node=>{node.geometry?.dispose();if(Array.isArray(node.material))node.material.forEach(m=>m.dispose());else node.material?.dispose();});}
    clear(){Object.values(this.groups).forEach(g=>this.disposeGroup(g));this.groups={};this.actorMeshes.clear();this.labels=[];$('object-labels').replaceChildren();}
    group(name){const g=new THREE.Group();this.world.add(g);this.groups[name]=g;return g;}
    xyz(point,z=.12){return new THREE.Vector3(point[0]-this.origin[0],point[1]-this.origin[1],z);}
    line(group,points,color,z=.1,dashed=false,opacity=1){if(!points||points.length<2)return;const valid=points.filter(p=>finite(p[0])&&finite(p[1]));if(valid.length<2)return;const geometry=new THREE.BufferGeometry().setFromPoints(valid.map(p=>this.xyz(p,z)));const material=dashed?new THREE.LineDashedMaterial({color,dashSize:.65,gapSize:.45,transparent:opacity<1,opacity}):new THREE.LineBasicMaterial({color,transparent:opacity<1,opacity});const line=new THREE.Line(geometry,material);if(dashed)line.computeLineDistances();group.add(line);return line;}
    polygon(group,points,color,z=.02,opacity=1,holes=[]){if(!points||points.length<3)return;const valid=points.filter(p=>finite(p[0])&&finite(p[1]));if(valid.length<3)return;const shape=new THREE.Shape(valid.map(p=>new THREE.Vector2(p[0]-this.origin[0],p[1]-this.origin[1])));for(const ring of holes){shape.holes.push(new THREE.Path(ring.map(p=>new THREE.Vector2(p[0]-this.origin[0],p[1]-this.origin[1]))));}let geometry;try{geometry=new THREE.ShapeGeometry(shape);}catch{return;}const material=new THREE.MeshBasicMaterial({color,side:THREE.DoubleSide,transparent:opacity<1,opacity,depthWrite:opacity>=1});const mesh=new THREE.Mesh(geometry,material);mesh.position.z=z;group.add(mesh);return mesh;}
    load(payload){this.clear();this.origin=payload.origin.slice(0,2);for(const variant of ['original','updated']){const base=this.group('base-'+variant);for(const [id,f] of Object.entries(payload.maps[variant])){let polygon=f.polygon,holes=[];const extra=payload.display_polygons?.[variant]?.[id];if(!polygon&&extra){polygon=extra.polygon;holes=extra.holes||[];}const type=f.type||'';if(polygon){const color=type.includes('CROSSWALK')?0x66808a:type.includes('SIDEWALK')?0x3d5460:0x2d4856;this.polygon(base,polygon,color,type.includes('CROSSWALK')?.05:.015,1,holes);}if(f.polyline){const isLine=type.includes('ROAD_LINE')||type.includes('ROAD_EDGE');if(type==='UNKNOWN_LINE')continue;this.line(base,f.polyline,isLine?(type.includes('YELLOW')?0xc1af77:0x8297a0):0x3d6777,.065,isLine&&type.includes('BROKEN'),isLine?.85:.42);}}const changes=this.group('changes-'+variant);for(const patch of payload.changes){const f=patch[variant==='original'?'before':'after'];const color=variant==='original'?(patch.operation==='delete'?COLORS.red:COLORS.amber):COLORS.cyan;for(const path of pathsOf(f))this.line(changes,path,color,.22,variant==='original',.95);if(f?.polygon)this.polygon(changes,f.polygon,color,.045,.10);}}
      this.group('actors');this.group('fleet');this.group('states');this.group('selected');this.group('selection-original');this.cameraConfig={yaw:25,pitch:49,distance:57,pan:[0,0],target:'ego',fixed:payload.origin.slice()};this.updateActors();this.render();}
    resize(){if(!this.renderer)return;const r=this.host.getBoundingClientRect();if(r.width<1||r.height<1)return;this.renderer.setSize(r.width,r.height,false);this.width=r.width;this.height=r.height;this.render();}
    updateActors(){if(!this.groups.actors)return;const visible=new Set();for(const a of currentActors()){visible.add(a.id);let mesh=this.actorMeshes.get(a.id);if(!mesh){const color=a.fleet_vehicle?(a.fleet_vehicle==='B'?0x69b6ff:0xc9a0ff):a.is_ego?0x59d9be:categoryName(a.type)==='路障'?0xb78260:categoryName(a.type)==='锥桶'?0xcf9460:categoryName(a.type)==='行人'?0x879aa9:0x647f92;const geometry=new THREE.BoxGeometry(1,1,1);const material=new THREE.MeshStandardMaterial({color,roughness:.78,metalness:.05,transparent:!a.is_ego,opacity:a.is_ego?1:.86});mesh=new THREE.Mesh(geometry,material);const outline=new THREE.LineSegments(new THREE.EdgesGeometry(geometry),new THREE.LineBasicMaterial({color:a.is_ego?0xc5fff0:0xa9bcc6,transparent:true,opacity:a.is_ego?.7:.3}));mesh.add(outline);mesh.userData=a;this.groups.actors.add(mesh);this.actorMeshes.set(a.id,mesh);}mesh.visible=a.is_ego||a.persistent||$('show-traffic').checked;mesh.userData=a;mesh.scale.set(a.length,a.width,a.height);mesh.position.copy(this.xyz(a.position,a.height/2+.06));mesh.rotation.z=a.heading||0;}for(const[id,mesh]of this.actorMeshes)if(!visible.has(id))mesh.visible=false;this.groups.actors.visible=true;}
    updateFleet(){
      if(!S.fleet&&!this.groups.fleet?.children.length&&!this.labels.some(l=>l.kind==='fleet'))return;
      this.disposeGroup(this.groups.fleet);const group=this.group('fleet');this.labels=this.labels.filter(l=>l.kind!=='fleet');
      if(fleetActive())for(const row of fleetVehicles()){
        const color=row.vehicle_id==='B'?0x69b6ff:0xc9a0ff;
        this.line(group,fleetPoints(row,'previous'),color,.48,true,.45);
        this.line(group,fleetPoints(row,'current'),color,.5,false,.9);
        const trace=fleetPoints(row,'trace'),vertices=[];
        for(let i=1;i<trace.length;i++){const a=trace[i-1],b=trace[i],dx=b[0]-a[0],dy=b[1]-a[1],d=Math.hypot(dx,dy);if(d<.001)continue;const nx=-dy/d*.18,ny=dx/d*.18;const p=[[a[0]+nx,a[1]+ny],[a[0]-nx,a[1]-ny],[b[0]+nx,b[1]+ny],[b[0]-nx,b[1]-ny]];for(const j of [0,1,2,2,1,3])vertices.push(p[j][0]-this.origin[0],p[j][1]-this.origin[1],.56);}
        if(vertices.length){const geometry=new THREE.BufferGeometry();geometry.setAttribute('position',new THREE.Float32BufferAttribute(vertices,3));group.add(new THREE.Mesh(geometry,new THREE.MeshBasicMaterial({color,side:THREE.DoubleSide})));}
        const position=fleetPosition(row);if(position)this.labels.push({kind:'fleet',color:'#'+color.toString(16),point:[position[0],position[1],3],text:`${row.vehicle_id} · 实际引擎位置`});
      }
      this.redrawLabels();this.render();
    }
    clearStates(){this.stateOverview=null;this.disposeGroup(this.groups.states);this.group('states');this.labels=this.labels.filter(l=>l.kind!=='state');this.redrawLabels();}
    updateStates(){const overview=S.scene?.state_overview,selection=JSON.stringify(S.selected);if(overview&&this.stateOverview===overview&&this.stateSelection===selection){this.groups.states.visible=$('show-states').checked;return;}this.clearStates();if(!overview&&(!S.snapshot||S.snapshot.step!==S.step))return;this.stateOverview=overview;this.stateSelection=selection;const group=this.groups.states;const eventMap=new Map(events().map(e=>[eventId(e),e]));for(const obj of objects()){const color=uncertainState(obj.status)?COLORS.purple:COLORS.yellow;const paths=obj.runtime_paths||obj.geometry_city?.coordinates||[];const h=finite(obj.height_m)&&obj.height_m>0?obj.height_m:0;const chosen=S.selected?.kind==='state'&&obj.event_ids.includes(S.selected.id);for(const path of paths){this.line(group,path,color,.25,uncertainState(obj.status),chosen?1:.8);if(h>0)this.line(group,path,color,h+.2,uncertainState(obj.status),chosen?1:.7);for(let i=0;h>0&&i<path.length-1;i++){const p=path[i],arr=[this.xyz(p,.25),this.xyz(p,h+.2)];const g=new THREE.BufferGeometry().setFromPoints(arr);group.add(new THREE.Line(g,new THREE.LineBasicMaterial({color,transparent:true,opacity:chosen?1:.55})));}if(chosen)this.polygon(group,path,color,.24,.14);} }
      const ego=currentEgo();const candidates=events().map(e=>{const id=eventId(e),rows=objects().filter(o=>o.event_ids.includes(id)),center=pointsCenter(rows.flatMap(o=>o.runtime_paths||[]));return{e,id,rows,center,d:center&&ego?Math.hypot(center[0]-ego.position[0],center[1]-ego.position[1]):1e6};}).filter(x=>x.center).sort((a,b)=>a.id.localeCompare(b.id));
      const chosen=S.selected?.kind==='state'?candidates.find(c=>c.id===S.selected.id):null;const labels=[...(chosen?[chosen]:[]),...candidates.filter(c=>c!==chosen).slice(0,chosen?2:3)];for(const item of labels){const p=eventProps(item.e);const cats=[...new Set(item.rows.map(o=>categoryName(o.category||o.type)))].join(' / ');this.labels.push({kind:'state',point:[...item.center,Math.max(1.8,...item.rows.map(o=>o.height_m||1))+1],text:`${cats} · ${item.rows.length} 个 · ${statusName(p.state)}`,unknown:uncertainState(p.state)});}this.redrawLabels();group.visible=$('show-states').checked;}
    redrawLabels(){$('object-labels').replaceChildren();for(const label of this.labels){const node=element('div','world-label '+(label.kind==='structure'?'structure':label.unknown?'unknown':''),label.text);if(label.kind==='fleet'){node.style.color=label.color;node.style.borderColor=label.color;}$('object-labels').append(node);label.node=node;}}
    select(){this.disposeGroup(this.groups.selected);this.disposeGroup(this.groups['selection-original']);const after=this.group('selected'),before=this.group('selection-original');this.labels=this.labels.filter(l=>l.kind!=='structure');if(S.selected?.kind==='structure'){const p=S.scene.changes.find(c=>c.feature_id===S.selected.id);if(p){for(const[feature,group,color]of[[p.before,before,COLORS.amber],[p.after,after,COLORS.cyan]])for(const path of pathsOf(feature)){this.line(group,path,color,.4,false,1);if(feature.polygon)this.polygon(group,feature.polygon,color,.25,.28);}const center=pointsCenter(pathsOf(p.after||p.before));if(center)this.labels.push({kind:'structure',point:[...center,3],text:(p.label||'')+' · '+opName(p.operation)});}}this.updateStates();this.redrawLabels();this.render();}
    focus(){if(!S.selected)return;let paths=[];if(S.selected.kind==='structure'){const p=S.scene.changes.find(c=>c.feature_id===S.selected.id);if(p)paths=[...pathsOf(p.before),...pathsOf(p.after)];}else paths=objects().filter(o=>o.event_ids.includes(S.selected.id)).flatMap(o=>o.runtime_paths||[]);const b=boundsOf(paths);if(!b)return;this.cameraConfig.target='selection';this.cameraConfig.fixed=[(b[0]+b[2])/2,(b[1]+b[3])/2];this.cameraConfig.pan=[0,0];this.cameraConfig.distance=Math.max(18,Math.max(b[2]-b[0],b[3]-b[1])*1.7);this.cameraConfig.pitch=62;this.render();}
    preset(name){const c=this.cameraConfig;const e=currentEgo();c.pan=[0,0];if(name==='overview'){const b=S.scene.bounds;c.target='map';c.fixed=[(b[0]+b[2])/2,(b[1]+b[3])/2];c.distance=Math.max(b[2]-b[0],b[3]-b[1])*1.1;c.pitch=72;c.yaw=0;}else{c.target='ego';c.yaw=name==='rear'?180:0;c.pitch=name==='top'?89:name==='rear'?24:35;c.distance=name==='top'?75:38;if(e)c.fixed=e.position.slice(0,2);}this.render();}
    updateCamera(aspect){const c=this.cameraConfig,ego=currentEgo();let target=c.target==='ego'&&ego?ego.position:c.fixed;const heading=c.target==='ego'&&ego?ego.heading||0:0;const yaw=heading+c.yaw*Math.PI/180,pitch=c.pitch*Math.PI/180;const tx=target[0]-this.origin[0]+c.pan[0],ty=target[1]-this.origin[1]+c.pan[1];this.camera.aspect=aspect;this.camera.position.set(tx-Math.cos(yaw)*c.distance*Math.cos(pitch),ty-Math.sin(yaw)*c.distance*Math.cos(pitch),Math.sin(pitch)*c.distance+1);this.camera.lookAt(tx,ty,.5);this.camera.updateProjectionMatrix();}
    setMapVisibility(variant,compare=false){this.groups['base-original'].visible=variant==='original';this.groups['base-updated'].visible=variant==='updated';this.groups['changes-original'].visible=$('show-structure').checked&&(variant==='original'||!compare);this.groups['changes-updated'].visible=$('show-structure').checked&&variant==='updated';this.groups.selected.visible=$('show-structure').checked&&variant==='updated';this.groups['selection-original'].visible=$('show-structure').checked&&variant==='original';this.groups.states.visible=$('show-states').checked;this.groups.actors.visible=true;}
    render(){if(!this.renderer||!S.scene||!this.width||S.mode!=='review')return;const w=this.width,h=this.height,compare=S.version==='compare';this.renderer.setScissorTest(true);for(let i=0;i<(compare?2:1);i++){const left=compare?i*w/2:0,vw=compare?w/2:w;this.renderer.setViewport(left,0,vw,h);this.renderer.setScissor(left,0,vw,h);this.updateCamera(vw/h);this.setMapVisibility(compare?(i===0?'original':'updated'):version(),compare);this.renderer.render(this.world,this.camera);}this.renderer.setScissorTest(false);for(const label of this.labels){if(!label.node)continue;const allowed=!compare&&(label.kind==='fleet'?fleetActive():label.kind==='state'?$('show-states').checked:$('show-structure').checked);const p=this.xyz(label.point,label.point[2]);p.project(this.camera);const show=allowed&&p.z>-1&&p.z<1&&Math.abs(p.x)<.92&&Math.abs(p.y)<.88;label.node.hidden=!show;if(show){label.node.style.left=((p.x+1)/2*w)+'px';label.node.style.top=((1-p.y)/2*h)+'px';}}}
    pointerDown(e){if(e.target.closest('button,select,input,.viewport-loading,.viewport-error'))return;if(e.button!==0&&e.button!==2)return;this.drag={x:e.clientX,y:e.clientY,button:e.button};$('viewport').setPointerCapture(e.pointerId);e.preventDefault();}
    pointerMove(e){if(!this.drag)return;const dx=e.clientX-this.drag.x,dy=e.clientY-this.drag.y;this.drag.x=e.clientX;this.drag.y=e.clientY;const c=S.mode==='engine'?(S.engineCamera||{yaw:0,pitch:35,distance:38,pan:[0,0],target:'ego'}):this.cameraConfig;if(this.drag.button===2||e.shiftKey){const scale=c.distance*.0018,yaw=(c.yaw||0)*Math.PI/180+cameraTargetHeading(c);const pan=c.pan||[0,0];c.pan=[pan[0]+(-dx*Math.sin(yaw)+dy*Math.cos(yaw))*scale,pan[1]+(dx*Math.cos(yaw)+dy*Math.sin(yaw))*scale];}else{c.yaw=(c.yaw-dx*.4)%360;c.pitch=clamp(c.pitch+dy*.3,4,89);}if(S.mode==='engine'){S.engineCamera=c;scheduleEngineCamera();}else this.render();updateCameraButtons(null);}
    pointerUp(e){if(this.drag&&S.mode==='engine')scheduleEngineCamera(0);this.drag=null;if($('viewport').hasPointerCapture(e.pointerId))$('viewport').releasePointerCapture(e.pointerId);}
    wheel(e){if(!S.scene||e.target.closest('select,input'))return;e.preventDefault();const c=S.mode==='engine'?(S.engineCamera||{yaw:0,pitch:35,distance:38,pan:[0,0],target:'ego'}):this.cameraConfig;c.distance=clamp(c.distance*Math.exp(e.deltaY*.001),5,1800);if(S.mode==='engine'){S.engineCamera=c;scheduleEngineCamera();}else this.render();}
  }
  let viewer=null;
  try{viewer=new ReviewRenderer();}catch(error){S.rendererError=error;showError(error);}

  function renderCatalog(){const host=$('scene-list');host.replaceChildren();for(const item of S.catalog){const card=element('button','scene-card'+(keyOf(item)===S.key?' active':''));card.dataset.scene=keyOf(item);card.setAttribute('aria-pressed',String(keyOf(item)===S.key));const dataset=item.dataset==='argoverse'?'ARGOVERSE':'NUSCENES';card.innerHTML=`<div class="scene-card-kind"><span>${dataset}</span>${item.synthetic?'<span class="demo-label">DEMO</span>':'<span>●</span>'}</div><h3>${esc(item.title.replace(/^nuScenes · |^Argoverse · /,''))}</h3><p>${esc(item.source_label)}</p><div class="scene-card-counts"><span><b>${item.structure_updates??item.summary?.structure_updates??0}</b> 结构</span><span><b>${item.state_events??item.summary?.state_events??0}</b> 状态</span></div>`;card.onclick=()=>selectScene(keyOf(item));host.append(card);}}
  async function selectScene(key){if(S.key===key&&S.scene)return;stopPlayback();const token=++S.sceneRequest;S.snapshotAbort?.abort();S.request++;cancelFramePresentation();clearFrameError();S.key=key;S.pendingEngineAction=null;S.pendingCamera=null;S.pendingSeek=null;S.pendingFrameRetry=false;clearTimeout(S.cameraTimer);S.cameraTimer=null;S.scene=null;S.snapshot=null;S.fleet=null;S.fleetConfig=null;S.fleetControlPending=false;S.fleetMissions=null;renderFleet();S.engineAdvanceMs=0;S.selected=null;S.validation=null;S.inspectorSignature=null;S.step=0;S.version='updated';$('map-version').value='updated';setMode('review');renderCatalog();setLoading(true);$('viewport-error').hidden=true;$('start-engine').disabled=true;$('export-planning').disabled=true;$('play-pause').disabled=true;$('timeline').disabled=true;try{const payload=await api('/api/scenes/'+encodeURIComponent(key));if(token!==S.sceneRequest)return;S.scene=payload;loadFleetMissions(key);S.tab=payload.summary.state_events?'states':'structure';text('dataset-badge',payload.dataset==='argoverse'?'ARGOVERSE':'NUSCENES');text('scene-title',payload.title);text('scene-kind',payload.source.synthetic?'受控联调 · 独立标记':payload.source.kind==='reference_update'?'已交付参考更新':'真实算法状态');text('source-note',payload.source.label+'。'+(payload.dataset==='argoverse'?'当前源数据没有可关联的三维状态轨迹。':'变化在开场完整显示，静态障碍保持固定；车辆与行人按轨迹回放。'));text('source-badge',payload.source.synthetic?'DEMO + 真实状态 · 全场景固定':payload.source.kind==='reference_update'?'参考更新 · 全场景固定':'真实状态 · 全场景固定预览');$('source-badge').classList.toggle('synthetic',payload.source.synthetic);text('data-status','本地数据已校验');text('structure-count',payload.changes.length);text('state-count','0 / '+payload.summary.state_events);text('layer-description',payload.changes.length?payload.summary.structure_updates+' 项源结构修改 · '+payload.changes.length+' 个仿真要素':'结构修改 0 项 · 状态事件仍独立显示');$('timeline').max=payload.frame_count-1;$('timeline').disabled=false;$('play-pause').disabled=false;syncEngineActivity();$('export-planning').disabled=false;text('total-time',timeText(payload.timeline.times_s.at(-1)));renderTimeMarkers();viewer?.load(payload);updateCameraButtons('follow');setLoading(false);if(S.rendererError)showError(S.rendererError);setTab(S.tab);await seek(0);if(payload.summary.state_events&&events().length){S.selected={kind:'state',id:eventId(events()[0])};viewer?.select();renderInspector();}else if(payload.changes.length){S.selected={kind:'structure',id:payload.changes[0].feature_id};viewer?.select();renderInspector();}updateTimeline();}catch(error){if(token===S.sceneRequest){showError(error);text('data-status','场景载入失败');}}}
  function setMode(mode){if(mode!==S.mode)cancelFramePresentation();S.mode=mode;$('engine-frame-error').hidden=!(S.engineFrameError&&(mode==='engine'||fleetActive()));if(mode==='review')$('pending-state').hidden=true;if(mode==='engine')$('viewport-error').hidden=true;else if(S.rendererError)showError(S.rendererError);$('show-traffic').disabled=mode==='engine';$('show-traffic').parentElement.title=mode==='engine'?'引擎参与者保持原场景数据；此开关仅用于地图审阅':'';document.querySelectorAll('[data-mode]').forEach(b=>b.classList.toggle('active',b.dataset.mode===mode));$('review-view').hidden=mode!=='review';$('engine-view').hidden=mode!=='engine';$('object-labels').hidden=mode!=='review';$('split-labels').hidden=mode!=='review'||S.version!=='compare';text('renderer-badge',mode==='review'?'三维源几何审阅 · 按源尺寸简化物体':'MetaDrive 引擎画面');$('map-version').querySelector('[value="compare"]').disabled=mode==='engine';if(mode==='engine'&&S.version==='compare'){S.version='updated';$('map-version').value='updated';}if(mode==='review'){if(S.scene&&S.snapshot){viewer?.updateActors();viewer?.updateFleet();viewer?.select();}viewer?.resize();}text('camera-hint',mode==='engine'?'拖动旋转引擎相机 · 右键平移 · 滚轮缩放':'左键旋转 · 右键平移 · 滚轮缩放');syncEngineActivity();renderFleet();}
  function renderTimeMarkers(){const host=$('time-markers');host.replaceChildren();const source=S.scene.state_source;if(!source)return;const stamps=S.scene.timeline.source_timestamps_ns;for(const event of source.events||[]){const sample=(event.samples||[]).filter(s=>s.observation==='supports_change'&&s.quality>0).sort((a,b)=>a.timestamp_ns.localeCompare(b.timestamp_ns))[0];if(!sample)continue;const index=stamps.findIndex(t=>t!=null&&BigInt(t)>=BigInt(sample.timestamp_ns));if(index<0)continue;const mark=element('i','time-marker');mark.style.left=(index/(S.scene.frame_count-1)*100)+'%';mark.title='源记录首次观测（变化已全程展示）';host.append(mark);}}
  function updateTimeline(){if(!S.scene)return;const t=S.scene.timeline.times_s[S.step];text('current-time',timeText(t));text('frame-readout',`帧 ${S.step} / ${S.scene.frame_count-1}`);$('timeline').value=S.step;$('timeline').style.setProperty('--progress',(100*S.step/(S.scene.frame_count-1))+'%');const ego=currentEgo();text('coordinates',ego?`X ${ego.position[0].toFixed(1)} · Y ${ego.position[1].toFixed(1)} m`:'源城市 XY · 米');text('time-source',fleetActive()?'A 记录回放 · B / C 独立规划':S.mode==='engine'?'真实引擎 · 变化固定展示':'轨迹回放 · 变化固定展示');}
  async function seek(raw,{fromEngine=false}={}){if(!S.scene)return;const step=clamp(Math.round(raw),0,S.scene.frame_count-1);if((S.mode==='engine'||fleetActive())&&!fromEngine){if(!S.engine.ready||S.engine.scene_id!==S.key||S.engine.version!==version()){toast('请先启动当前场景和道路版本的 MetaDrive。');return;}if(S.engineCommandBusy){S.pendingSeek=step;return;}await engineCommand({command:'seek',step});return;}S.step=step;S.snapshot=null;if(!S.scene.state_overview)viewer?.clearStates();viewer?.updateActors();updateTimeline();const token=++S.request,key=S.key,ver=version();S.snapshotAbort?.abort();const abort=new AbortController();S.snapshotAbort=abort;text('pending-state','正在对齐状态…');$('pending-state').hidden=false;try{const snapshot=await api(`/api/scenes/${encodeURIComponent(key)}/snapshot?step=${step}&version=${ver}`,{signal:abort.signal});if(token!==S.request||key!==S.key||step!==S.step||ver!==version())return;S.snapshot=snapshot;if(S.selected?.kind==='state'&&!events().some(e=>eventId(e)===S.selected.id))S.selected=events().length?{kind:'state',id:eventId(events()[0])}:null;viewer?.updateActors();viewer?.updateStates();text('state-count',`${events().length} / ${S.scene.summary.state_events}`);renderInspector();if(S.mode==='review')$('pending-state').hidden=true;}catch(error){if(error.name!=='AbortError'&&token===S.request){if(S.mode==='review')$('pending-state').hidden=true;toast('状态读取失败：'+error.message);}}}
  function setTab(tab){if(tab!==S.tab)S.inspectorSignature=null;S.tab=tab;document.querySelectorAll('[data-tab]').forEach(b=>{b.classList.toggle('active',b.dataset.tab===tab);b.setAttribute('aria-selected',String(b.dataset.tab===tab));});$('change-search-wrap').hidden=tab==='validation';$('change-list').hidden=tab==='validation';$('validation-panel').hidden=tab!=='validation';if(tab==='validation'){ $('selection-details').hidden=true;loadValidation(); }else renderInspector();}
  function changeTitle(change){const type=(change.after||change.before)?.type||'';const base=type.includes('CROSSWALK')?'人行横道':type.includes('ROAD_LINE')||type==='UNKNOWN_LINE'?'车道标线':'车道结构';const attrs=change.attribute_changes||[];if(attrs.some(a=>a.field==='source_lane_type'))return '车道用途 · '+opName(change.operation);if(attrs.some(a=>a.field==='exit_lanes'||a.field==='entry_lanes'))return base+' / 连接关系';return base+' · '+opName(change.operation);}
  function stateTitle(event){const rows=objects().filter(o=>o.event_ids.includes(eventId(event)));const cats=[...new Set(rows.map(o=>categoryName(o.category||o.type)))];return (cats.join(' / ')||'状态事件')+' · '+rows.length+' 个实例';}
  function renderInspector(){if(!S.scene||S.tab==='validation')return;const list=$('change-list');const query=$('change-search').value.trim().toLowerCase();const rows=S.tab==='structure'?S.scene.changes:events();const signature=JSON.stringify([S.key,S.tab,query,S.selected?.kind,S.selected?.id,S.tab==='states'?rows.map(row=>[eventId(row),eventProps(row).state,stateTitle(row)]):null]);if(signature===S.inspectorSignature){if(S.tab==='states'&&!S.scene.state_overview)renderDetails();return;}S.inspectorSignature=signature;list.replaceChildren();const matches=rows.filter(row=>{const id=S.tab==='structure'?row.feature_id:eventId(row);const title=S.tab==='structure'?changeTitle(row):stateTitle(row);return (id+' '+title+' '+(row.label||'')).toLowerCase().includes(query);});if(!matches.length){const e=element('div','change-empty');const state=S.tab==='states';e.innerHTML='<svg><use href="#i-layers"/></svg>';e.append(element('strong','',query?'没有匹配项':state?(S.scene.summary.state_events?'当前场景没有可显示的状态概览':'这个场景未提供三维状态事件'):'当前场景没有已执行的道路结构修改'));e.append(element('p','',query?'可搜索完整或部分要素 ID。':state?(S.scene.summary.state_events?'当前源材料无法构建变化概览。':'道路与状态仍在同一场景界面。这里不借用其他数据集的障碍或行人。'):'附着状态可在“状态”页查看。没有结构更新时，原始道路与更新后道路一致。'));list.append(e);}else for(const row of matches){const state=S.tab==='states',id=state?eventId(row):row.feature_id;const active=S.selected?.kind===(state?'state':'structure')&&S.selected.id===id;const status=state?eventProps(row).state:null;const button=element('button','change-item'+(state?' state':'')+(status&&uncertainState(status)?' unknown':'')+(active?' active':''));button.dataset.change=id;button.innerHTML=`<div class="change-item-title"><span class="change-symbol">${state?'◇':row.operation==='add'?'+':row.operation==='delete'?'−':'↔'}</span><span>${esc(state?stateTitle(row):changeTitle(row))}</span><span class="change-op">${esc(state?statusName(status):row.label||'')}</span></div><small title="${esc(id)}">${esc(id)}</small>`;button.onclick=()=>selectItem(state?'state':'structure',id);list.append(button);}renderDetails();}
  function selectItem(kind,id,{focus=true}={}){S.selected={kind,id};if(S.mode==='review')viewer?.select();renderInspector();if(focus)focusSelection();}
  function focusSelection(){if(!S.selected){toast('先从右侧选择一处道路修改或状态事件。');return;}if(S.mode==='engine'){if(!S.engine.ready)return;if(version()==='original'&&S.selected.kind==='structure'){const patch=S.scene.changes.find(c=>c.feature_id===S.selected.id),b=patch?boundsOf(pathsOf(patch.before||patch.after)):null,center=S.engine.last_result?.map_center_scenario_m;if(!b||!center){toast('引擎尚未返回原地图定位信息，请重新启动当前场景。');return;}engineCommand({command:'camera',target:'map',pan:[(b[0]+b[2])/2-center[0],(b[1]+b[3])/2-center[1]],pitch:65,distance:clamp(Math.max(20,Math.hypot(b[2]-b[0],b[3]-b[1])*1.8),3,2000)});}else engineCommand({command:'select',...(S.selected.kind==='structure'?{feature_id:S.selected.id}:{event_id:S.selected.id}),focus:true});}else viewer?.focus();updateCameraButtons(null);}
  function detailRow(label,value){const row=element('div','detail-row');row.append(element('span','',label),element('span','',value));return row;}
  function miniViews(before,after){const all=[...pathsOf(before),...pathsOf(after)],b=boundsOf(all);if(!b)return element('div');const dx=Math.max(2,b[2]-b[0]),dy=Math.max(2,b[3]-b[1]),scale=Math.min(100/(dx+2),72/(dy+2));const cx=(b[0]+b[2])/2,cy=(b[1]+b[3])/2;const host=element('div','before-after');for(const[f,side,color]of[[before,'BEFORE', '#f3bc65'],[after,'AFTER','#65e2cb']]){const box=element('div','mini-view');box.append(element('strong','',side));if(!f)box.append(element('div','absent',side==='BEFORE'?'原图不存在':'更新后已移除'));else{const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 112 82');for(const path of pathsOf(f)){const line=document.createElementNS(svg.namespaceURI,'polyline');line.setAttribute('points',path.map(p=>`${56+(p[0]-cx)*scale},${41-(p[1]-cy)*scale}`).join(' '));line.setAttribute('fill','none');line.setAttribute('stroke',color);line.setAttribute('stroke-width','2');if(side==='BEFORE')line.setAttribute('stroke-dasharray','4 3');svg.append(line);}box.append(svg);}host.append(box);}return host;}
  function renderDetails(){const host=$('selection-details');host.replaceChildren();const kind=S.tab==='states'?'state':'structure';if(!S.selected||S.selected.kind!==kind){host.hidden=true;return;}host.hidden=false;if(kind==='structure'){const c=S.scene.changes.find(c=>c.feature_id===S.selected.id);if(!c){host.hidden=true;return;}host.append(element('h3','',(c.label||'')+' · '+changeTitle(c)),element('div','detail-id',c.feature_id),miniViews(c.before,c.after),element('p','detail-caption','两格使用相同范围与比例，显示真实前后几何。'));for(const attr of(c.attribute_changes||[]).slice(0,4)){let a=attr.before,b=attr.after;if(Array.isArray(a))a=a.length+' 项';if(Array.isArray(b))b=b.length+' 项';host.append(detailRow(fieldName(attr.field),(a??'无')+' → '+(b??'无')));}host.append(detailRow('来源',S.scene.source.synthetic?'受控演示':S.scene.source.kind==='reference_update'?'已交付参考更新':'算法结构输出'));}else{const e=events().find(e=>eventId(e)===S.selected.id);if(!e){host.hidden=true;return;}const p=eventProps(e),rows=objects().filter(o=>o.event_ids.includes(S.selected.id));host.append(element('h3','',stateTitle(e)),element('div','detail-id',S.selected.id),detailRow('展示方式',statusName(p.state)),detailRow('关联实例',rows.length+' 个'),detailRow('仅源轮廓',rows.filter(o=>o.missing_model).length+' 个'));host.append(detailRow('变化范围','整段场景'),detailRow('代表位置','首次可靠观测'),detailRow('修改道路','否 · 独立附着层'));host.append(element('p','detail-caption','黄色轮廓汇总整段记录中的变化，从开场固定展示。普通交通参与者继续回放；Planning 导出仍按选定时刻提供已观测信息。'));}const focus=element('button','detail-focus','聚焦这处变化');focus.onclick=focusSelection;host.append(focus);}
  async function loadValidation(){const host=$('validation-panel');if(!S.scene)return;const key=S.key,ver=version();if(!S.validation){host.replaceChildren(element('p','validation-note','正在核对地图、状态和评估依据…'));try{const data=await api('/api/scenes/'+encodeURIComponent(key)+'/validation?version='+ver);if(key!==S.key||ver!==version())return;S.validation=data;}catch(error){if(key===S.key&&ver===version())host.replaceChildren(element('p','validation-note','无法读取验证结果：'+error.message));return;}}renderValidation();}
  function renderValidation(){const host=$('validation-panel'),data=S.validation;host.replaceChildren();if(!data)return;const integrity=data.integrity||{};const card=element('section','validation-card');card.append(element('h3','', '地图写入与数据一致性'),element('p',integrity.passed?'pass':'fail',integrity.passed?`写入一致性通过 · ${integrity.summary?.warnings||0} 类源数据提示`:'存在需要查看的检查结果'));for(const check of integrity.checks||[]){const row=element('div','validation-check'+(check.status==='fail'?' fail':''));row.append(element('span','',check.status==='pass'?'✓':check.status==='fail'?'×':'!'));row.append(document.createTextNode(check.label||check.name));if(check.status==='warning'||check.status==='fail'){const details=element('details','validation-issue');const summary=element('summary');summary.append(row);details.append(summary);const d=check.details||{};const mappings={preserved_source_centerlines_outside_polygon:'源中心线超出车道面',source_lanes_without_polygon:'源转换中缺道路面的车道',invalid_source_polygons:'源多边形问题',preserved_source_issues:'保留的源连接缺口',excluded_external_references:'已排除的区域外连接'};for(const[k,label]of Object.entries(mappings)){if(d[k]!=null){const count=Array.isArray(d[k])?d[k].length:d[k];if(count)details.append(element('p','validation-note',`${label}：${count}`));}}if(d.note||d.error)details.append(element('p','validation-note',d.note||d.error));card.append(details);}else card.append(row);}host.append(card);const d=data.detection||{},detect=element('section','validation-card');detect.append(element('h3','', '算法检出评估'),element('p','',d.label||d.scope_description||d.scope||'独立标注评估'));if(d.scope_description)detect.append(element('p','',d.scope_description));if(d.available&&d.metrics){const g=element('div','metric-grid');for(const[k,label]of[['precision','精确率'],['recall','召回率'],['f1','F1']]){const m=element('div');m.append(element('b','',k==='f1'?(d.metrics[k]==null?'—':Number(d.metrics[k]).toFixed(2)):percent(d.metrics[k])),element('small','',label));g.append(m);}detect.append(g);if(d.final_model_structural_updates!=null)detect.append(element('p','',`最终算法结构写入：${d.final_model_structural_updates} 项`));if(d.reference_updates_available!=null)detect.append(element('p','',`已交付参考更新：${d.reference_updates_available} 项（单独记账）`));if(d.geometry_accuracy){const a=d.geometry_accuracy;detect.append(element('p','',`局部几何：${a.passed} / ${a.total} 在 ${a.tolerance_m} m 容差内`));}detect.append(element('p','', '指标按上述评估范围计算，不等同于整张地图的正确率。'));}else detect.append(element('p','', '缺少独立真值，当前不能报告可信的检测正确率。'));for(const line of d.limitations||[])detect.append(element('p','',line));host.append(detect);for(const line of data.limitations||[])host.append(element('p','validation-note',line));}

  function cancelFramePresentation() {
    S.engineFrameGeneration=(S.engineFrameGeneration||0)+1;
    S.engineFrameAbort?.abort();
    S.engineFrameAbort=null;
  }
  function frameContextMatches(key,ver) {
    return (S.mode==='engine'||fleetActive())&&key===S.key&&ver===version();
  }
  function hasDisplayedFrame(key=S.key,ver=version()) {
    const shown=S.engineDisplayedFrame,image=$('engine-frame');
    return Boolean(shown&&shown.key===key&&shown.version===ver&&image.complete&&image.naturalWidth>0);
  }
  function restoreDisplayedFrameState() {
    if(!hasDisplayedFrame())return false;
    const shown=S.engineDisplayedFrame;
    S.snapshotAbort?.abort();S.request++;
    S.step=shown.step;S.snapshot=shown.snapshot;S.fleet=shown.result.fleet||null;renderFleet();if(S.mode==='review')viewer?.updateFleet();
    $('engine-frame').hidden=S.mode!=='engine';if(S.mode==='review')viewer?.updateActors();
    updateTimeline();
    text('state-count',`${events().length} / ${S.scene.summary.state_events}`);renderInspector();
    return true;
  }
  function clearFrameError() {
    S.engineFrameError=null;
    $('engine-frame-error').hidden=true;
  }
  function reportFrameError(error,result,key,ver) {
    if(!frameContextMatches(key,ver)||error.name==='AbortError')return;
    stopPlayback();
    S.pendingSeek=S.pendingCamera=S.pendingEngineAction=null;S.pendingFrameRetry=false;
    clearTimeout(S.cameraTimer);S.cameraTimer=null;
    S.engineFrameError={key,version:ver,result,message:error.message||String(error)};
    const displayed=hasDisplayedFrame(key,ver);
    if(displayed)restoreDisplayedFrameState();
    const retained=displayed?`仍显示第 ${S.engineDisplayedFrame.step} 帧，时间轴和导出也保持在这一帧。`:'尚未收到可显示的引擎画面。';
    text('engine-frame-error-text',`第 ${result.step} 帧未能显示，回放已暂停。${retained} ${S.engineFrameError.message}`);
    $('engine-frame-error').hidden=false;
    $('engine-frame').hidden=!displayed||S.mode!=='engine';
    $('engine-placeholder').hidden=true;
    setLoading(false);
    text('playback-note','画面读取失败 · 重试成功后可继续播放');
  }
  function abortableDelay(ms,signal) {
    return new Promise((resolve,reject)=>{
      if(signal.aborted){reject(new DOMException('Frame request cancelled','AbortError'));return;}
      const onAbort=()=>{clearTimeout(timer);reject(new DOMException('Frame request cancelled','AbortError'));};
      const timer=setTimeout(()=>{signal.removeEventListener('abort',onAbort);resolve();},ms);
      signal.addEventListener('abort',onAbort,{once:true});
    });
  }
  async function decodeEngineFrame(result,key,ver,signal) {
    if(typeof result.frame_id!=='string'||!result.frame_id||!Number.isInteger(result.sequence))
      throw new Error('服务器未提供有效画面标识，请重启本地平台服务并刷新页面。');
    const query=new URLSearchParams({frame_id:result.frame_id,scene_id:key,version:ver,sequence:String(result.sequence)});
    const url='/api/engine/frame.png?'+query.toString();
    let lastError;
    for(let attempt=0;attempt<3;attempt++) {
      let blobUrl=null;
      const requestAbort=new AbortController();
      const onAbort=()=>requestAbort.abort();
      if(signal.aborted)throw new DOMException('Frame request cancelled','AbortError');
      signal.addEventListener('abort',onAbort,{once:true});
      const timer=setTimeout(()=>requestAbort.abort(),20000);
      try {
        if(frameContextMatches(key,ver)) {
          S.engineFrameAttempt=attempt;
          S.engineActivity='正在接收引擎画面…';
          syncEngineActivity();
        }
        const response=await fetch(url,{cache:'no-store',signal:requestAbort.signal});
        if(!response.ok) {
          let detail='';
          try {const body=await response.json();detail=body.error||body.message||'';}catch{}
          throw new Error(`画面请求失败（HTTP ${response.status}）${detail?'：'+detail:''}`);
        }
        if(!response.headers.get('Content-Type')?.toLowerCase().startsWith('image/png'))
          throw new Error('服务器返回的内容不是 PNG 画面。');
        const blob=await response.blob();
        if(!blob.size)throw new Error('服务器返回了空画面。');
        blobUrl=URL.createObjectURL(blob);
        const image=new Image();
        image.id='engine-frame';image.alt='MetaDrive 引擎实时画面';image.draggable=false;
        image.dataset.frameId=result.frame_id;image.dataset.sequence=String(result.sequence);image.dataset.step=String(result.step);
        image.src=blobUrl;
        await image.decode();
        if(signal.aborted)throw new DOMException('Frame request cancelled','AbortError');
        if(!image.naturalWidth||!image.naturalHeight)throw new Error('引擎画面无法解码。');
        return {image,blobUrl};
      } catch(error) {
        if(blobUrl)URL.revokeObjectURL(blobUrl);
        if(signal.aborted)throw new DOMException('Frame request cancelled','AbortError');
        lastError=error.name==='AbortError'?new Error('画面读取超时。'):error;
      } finally {
        clearTimeout(timer);signal.removeEventListener('abort',onAbort);
      }
      if(attempt<2)await abortableDelay(250*(attempt+1),signal);
    }
    throw lastError;
  }
  async function pollEngine(jobId,sceneKey,sceneVersion,completedStatus=null) {
    let status=completedStatus;
    for(;;) {
      if(!status) {
        const start=performance.now();
        const query=new URLSearchParams({wait_ms:'1000',job_id:jobId||''});
        status=await api('/api/engine/status?'+query.toString());
        // Older servers ignore wait_ms. Keep their fallback polling bounded.
        if(status.busy&&performance.now()-start<50)
          await new Promise(resolve=>setTimeout(resolve,50));
      }
      if(sceneKey!==S.key||sceneVersion!==version())return null;
      if(status.scene_id!==sceneKey||status.version!==sceneVersion)
        throw new Error('引擎已切换到其他场景或道路版本，请重新启动当前场景。');
      if(status.job_id&&jobId&&status.job_id!==jobId)
        throw new Error('引擎任务已被其他操作替换，请重新启动当前场景。');
      S.engine={...S.engine,...status};
      if(!status.busy) {
        if(status.error)throw new Error(typeof status.error==='string'?status.error:JSON.stringify(status.error));
        return status.last_result||{};
      }
      text('loading-text',status.progress?.message||'MetaDrive 正在载入或定位场景');
      status=null;
    }
  }
  async function applyEngineResult(result,key,ver) {
    if(!result||key!==S.key||ver!==version())return false;
    if(!S.scene||result.scene_id!==S.scene.scene_id||result.dataset!==S.scene.dataset||result.map_features_sha256!==S.scene.versions[ver].map_sha256||result.live_engine!==true)
      throw new Error('引擎场景或地图版本与当前选择不一致，已拒绝显示。');
    S.engine.ready=true;S.engine.scene_id=key;S.engine.version=ver;S.engine.last_result=result;
    if(S.mode!=='engine'&&!fleetActive()){setLoading(false);return false;}
    cancelFramePresentation();
    const generation=S.engineFrameGeneration,abort=new AbortController();
    S.engineFrameAbort=abort;
    let decoded=null;
    try {
      const framePromise=decodeEngineFrame(result,key,ver,abort.signal).then(frame=>{
        if(abort.signal.aborted){URL.revokeObjectURL(frame.blobUrl);throw new DOMException('Frame request cancelled','AbortError');}
        decoded=frame;return frame;
      });
      const snapshotPromise=result.snapshot?Promise.resolve(result.snapshot):
        api(`/api/scenes/${encodeURIComponent(key)}/snapshot?step=${result.step}&version=${ver}`,{signal:abort.signal});
      const [,snapshot]=await Promise.all([framePromise,snapshotPromise]);
      if(snapshot.key!==key||snapshot.step!==result.step||snapshot.version!==ver||snapshot.map_sha256!==result.map_features_sha256)
        throw new Error('画面与状态的帧号或地图版本不一致，已拒绝显示。');
      if(!frameContextMatches(key,ver)||generation!==S.engineFrameGeneration||abort.signal.aborted)
        return false;
      // Publish an already decoded image and its matching snapshot together.
      // Never replace the visible image with a network URL that can fail later.
      const previous=S.engineDisplayedFrame;
      const oldImage=$('engine-frame');
      decoded.image.hidden=S.mode!=='engine';
      oldImage.replaceWith(decoded.image);
      S.engineDisplayedFrame={key,version:ver,frame_id:result.frame_id,sequence:result.sequence,step:result.step,result,snapshot,blobUrl:decoded.blobUrl};
      decoded=null;
      if(previous?.blobUrl)URL.revokeObjectURL(previous.blobUrl);
      S.snapshotAbort?.abort();S.request++;
      S.step=result.step;S.snapshot=snapshot;S.fleet=result.fleet||null;S.fleetConfig=S.fleet?{mode:S.fleet.mode,variant:S.fleet.variant,goals:Object.fromEntries(fleetVehicles().filter(r=>r.goal_city_m||r.goal_scenario_m).map(r=>[r.vehicle_id,r.goal_city_m||r.goal_scenario_m]))}:null;renderFleet();if(S.mode==='review'){viewer?.updateFleet();viewer?.updateActors();}
      if(result.camera&&!S.pendingCamera)S.engineCamera=JSON.parse(JSON.stringify(result.camera));
      if(S.selected?.kind==='state'&&!events().some(e=>eventId(e)===S.selected.id))S.selected=events().length?{kind:'state',id:eventId(events()[0])}:null;
      updateTimeline();
      text('state-count',`${events().length} / ${S.scene.summary.state_events}`);renderInspector();
      clearFrameError();$('engine-placeholder').hidden=true;setLoading(false);
      text('data-status','MetaDrive 已连接 · 画面与状态已对齐');
      return true;
    } catch(error) {
      if(error.name==='AbortError'||!frameContextMatches(key,ver)||generation!==S.engineFrameGeneration)return false;
      reportFrameError(error,result,key,ver);
      return false;
    } finally {
      abort.abort();
      if(decoded?.blobUrl)URL.revokeObjectURL(decoded.blobUrl);
      if(S.engineFrameAbort===abort)S.engineFrameAbort=null;
    }
  }
  async function launchEngine(fleet=null){if(!S.scene)return;if(S.engineCommandBusy){toast('引擎正在完成当前操作，请稍候。');return;}stopPlayback();setMode('engine');const key=S.key,ver=version(),step=fleet?0:S.step;setEngineBusy(true);S.engine.ready=false;S.pendingSeek=null;S.pendingCamera=null;S.pendingEngineAction=null;S.pendingFrameRetry=false;cancelFramePresentation();clearFrameError();$('engine-frame').hidden=!hasDisplayedFrame(key,ver)||S.engineDisplayedFrame.step!==step;$('engine-placeholder').hidden=true;$('viewport-error').hidden=true;setLoading(true,'正在启动真实 MetaDrive 引擎');try{const job=await api('/api/engine/open',{method:'POST',body:JSON.stringify({scene_id:key,version:ver,step,wait_ms:1000,...(fleet?{fleet}:{})})});const result=await pollEngine(job.job_id,key,ver,job.status);const shown=await applyEngineResult(result,key,ver);if(shown&&key===S.key&&ver===version()&&(!$('show-structure').checked||!$('show-states').checked))S.pendingEngineAction={command:'overlays',structure:$('show-structure').checked,state:$('show-states').checked};}catch(error){if(frameContextMatches(key,ver)&&error.name!=='AbortError'){setLoading(false);const restored=restoreDisplayedFrameState();$('engine-placeholder').hidden=restored;$('engine-frame').hidden=!restored;toast('MetaDrive 启动失败：'+error.message);}}finally{setEngineBusy(false);flushEngineQueue();}}
  function syncEngineActivity() {
    // A command's serialization lock changes every frame. The visible controls
    // belong to the whole playback session, including gaps between commands.
    const replaying=(S.mode==='engine'||fleetActive())&&S.playing;
    const busy=Boolean(S.engineCommandBusy),locked=busy||replaying;
    for(const id of ['map-version','start-engine','engine-launch-inline']) {
      const disabled=locked||!S.scene||(id==='map-version'&&fleetActive());
      if($(id).disabled!==disabled)$(id).disabled=disabled;
    }
    for(const [id,title] of [['map-version','暂停回放后可切换道路版本'],
                             ['start-engine','暂停回放后可重新启动 MetaDrive']]) {
      const hint=replaying?title:'';
      if($(id).title!==hint)$(id).title=hint;
    }
    for(const id of ['retry-engine-frame','regenerate-engine-frame'])
      if($(id).disabled!==busy)$(id).disabled=busy;
    syncFleetControls();
    // Review mode owns its own snapshot-loading indicator.
    if(S.mode!=='engine')return;
    const message=busy&&S.engineFrameAttempt>0?`正在重试画面 (${S.engineFrameAttempt+1}/3)…`:
      replaying?'正在回放':busy?(S.engineActivity||'引擎处理中…'):'';
    const badge=$('pending-state');
    if(badge.textContent!==message)badge.textContent=message;
    if(badge.hidden!==!message)badge.hidden=!message;
  }
  function setEngineBusy(busy) {
    S.engineCommandBusy=busy;
    S.engineActivity=null;S.engineFrameAttempt=0;
    syncEngineActivity();
  }
  async function engineCommand(command){if(!S.scene||!S.engine.ready)return;if(S.engineFrameError&&command.command!=='frame'){updateTimeline();toast('画面尚未恢复，请先重试画面或重新生成当前帧。');return;}if(S.engineCommandBusy){if(command.command==='seek')S.pendingSeek=command.step;else if(command.command==='camera')S.pendingCamera=command;else S.pendingEngineAction=command;return;}const key=S.key,ver=version(),started=performance.now();S.pendingFrameRetry=false;setEngineBusy(true);try{const job=await api('/api/engine/command',{method:'POST',body:JSON.stringify({...command,expected_scene_id:key,expected_version:ver,wait_ms:1000})});const result=await pollEngine(job.job_id,key,ver,job.status);const shown=await applyEngineResult(result,key,ver);if(shown&&command.command==='advance'){const elapsed=performance.now()-started;S.engineAdvanceMs=S.engineAdvanceMs?S.engineAdvanceMs*.7+elapsed*.3:elapsed;}}catch(error){if(frameContextMatches(key,ver)&&error.name!=='AbortError'){stopPlayback();toast('引擎操作失败：'+error.message);}}finally{setEngineBusy(false);flushEngineQueue();}}
  function flushEngineQueue(){if(S.engineCommandBusy||(S.mode!=='engine'&&!fleetActive())||!S.engine.ready||S.engineFrameError)return;if(S.pendingSeek!=null){const step=S.pendingSeek;S.pendingSeek=null;engineCommand({command:'seek',step});return;}if(S.pendingFrameRetry){S.pendingFrameRetry=false;retryEngineFrame();return;}if(S.pendingCamera){const command=S.pendingCamera;S.pendingCamera=null;engineCommand(command);return;}if(S.pendingEngineAction){const command=S.pendingEngineAction;S.pendingEngineAction=null;engineCommand(command);}}
  async function retryEngineFrame(){if(S.engineCommandBusy){S.pendingFrameRetry=true;return;}S.pendingFrameRetry=false;const candidate=S.engineFrameError;const key=candidate?.key||S.key,ver=candidate?.version||version(),result=candidate?.result||S.engine.last_result;if(!result||!frameContextMatches(key,ver))return;setEngineBusy(true);try{await applyEngineResult(result,key,ver);}catch(error){reportFrameError(error,result,key,ver);}finally{setEngineBusy(false);flushEngineQueue();}}
  function scheduleEngineCamera(delay=40) {
    const c=S.engineCamera;
    if(!c||!S.engine.ready||S.engineFrameError)return;
    S.pendingCamera={command:'camera',yaw:c.yaw,pitch:c.pitch,distance:c.distance,pan:[...(c.pan||[0,0])],target:c.target||'ego'};
    // Send the latest drag position periodically, even while the pointer keeps
    // moving. At most one command and one pending camera position are kept.
    if(delay===0){clearTimeout(S.cameraTimer);S.cameraTimer=null;flushEngineQueue();return;}
    if(S.cameraTimer)return;
    S.cameraTimer=setTimeout(()=>{S.cameraTimer=null;flushEngineQueue();},delay);
  }
  function updateCameraButtons(preset){document.querySelectorAll('[data-camera]').forEach(button=>button.classList.toggle('active',button.dataset.camera===preset));}
  function setCameraPreset(name){if(!S.scene)return;if(S.mode==='engine'){if(!S.engine.ready){toast('先启动 MetaDrive，再调整引擎视角。');return;}engineCommand({command:'camera',preset:name});}else viewer?.preset(name);updateCameraButtons(name);}
  function stopPlayback(){S.playing=false;S.playGeneration=(S.playGeneration||0)+1;clearTimeout(S.playTimer);$('play-pause').setAttribute('aria-label','播放');$('play-pause').innerHTML='<svg><use href="#i-play"/></svg>';text('playback-note','暂停中 · 拖动进度条查看任意时刻');syncEngineActivity();}
  function play(){
    if(!S.scene)return;
    if(S.playing){stopPlayback();return;}
    if((S.mode==='engine'||fleetActive())&&S.engineFrameError){toast('画面读取失败，先重试画面后再播放。');return;}
    if((S.mode==='engine'||fleetActive())&&(!S.engine.ready||S.engine.scene_id!==S.key||S.engine.version!==version())){toast('请先启动当前场景的 MetaDrive。');return;}
    S.playing=true;
    syncEngineActivity();
    const generation=S.playGeneration=(S.playGeneration||0)+1;
    $('play-pause').setAttribute('aria-label','暂停');
    $('play-pause').innerHTML='<svg><use href="#i-pause"/></svg>';
    text('playback-note',(S.mode==='engine'||fleetActive())?'引擎帧推进 · 受本机渲染速度影响':'正在回放 · 变化从开场固定保留');
    const restartAtStart=S.step>=S.scene.frame_count-1;
    const tick=async()=>{
      if(!S.playing||generation!==S.playGeneration)return;
      if(S.step>=S.scene.frame_count-1){stopPlayback();text('playback-note','本段回放结束 · 可拖动返回任意时刻');return;}
      const start=performance.now(),engineMode=S.mode==='engine'||fleetActive();
      // Display each source frame when rendering keeps up. On slower machines
      // combine at most two source ticks per picture at 1x; physics still runs
      // every tick and the displayed clock always reports the actual source time.
      const base=(S.engineAdvanceMs||0)>2000*S.scene.timestep_s?2:1;
      const batch=Math.max(1,Math.round(base*S.speed));
      const interval=1000*S.scene.timestep_s*(engineMode?batch:1)/S.speed;
      if(engineMode){
        if(!S.engineCommandBusy)await engineCommand({command:'advance',steps:Math.min(20,batch,S.scene.frame_count-1-S.step)});
      }else await seek(S.step+1);
      // A paused in-flight tick must not start another loop after resuming.
      if(!S.playing||generation!==S.playGeneration)return;
      S.playTimer=setTimeout(tick,Math.max(5,interval-(performance.now()-start)));
    };
    const begin=async()=>{
      if(restartAtStart){
        while(S.engineCommandBusy&&S.playing&&generation===S.playGeneration)
          await new Promise(resolve=>setTimeout(resolve,50));
        if(!S.playing||generation!==S.playGeneration)return;
        await seek(0);
        if(!S.playing||generation!==S.playGeneration||S.step!==0)return;
      }
      tick();
    };
    begin();
  }
  async function changeVersion(value){if(!S.scene)return;if(fleetActive()){$('map-version').value='updated';toast('多车实验使用更新后的真实道路。比较车辆所知信息，请调整多车 Planning 中的选项并重新启动。');return;}if(S.mode==='engine'&&S.engineCommandBusy){$('map-version').value=S.version;toast('请等当前引擎操作完成后切换道路版本。');return;}stopPlayback();S.version=value;S.validation=null;$('split-labels').hidden=S.mode!=='review'||value!=='compare';if(S.mode==='engine'){await launchEngine();}else{viewer?.render();await seek(S.step);}renderDetails();if(S.tab==='validation')loadValidation();}
  async function exportFleetPlanning(vehicleId){
    const shown=S.engineDisplayedFrame;
    if(!fleetActive()||!shown?.result?.fleet||shown.key!==S.key){toast('请先启动多车 Planning 并等待引擎画面。');return;}
    try{const data=await api(`/api/engine/fleet/planning?vehicle_id=${encodeURIComponent(vehicleId)}&frame_id=${encodeURIComponent(shown.frame_id)}`);
      const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=element('a');a.href=url;a.download=`planning-${shown.key}-${vehicleId}-frame${shown.step}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast(`已导出 ${vehicleId} 第 ${shown.step} 帧实际接收的地图与引擎状态。`);
    }catch(error){toast('车辆 Planning 输入导出失败：'+error.message);}
  }
  async function exportPlanning(){if(!S.scene)return;if(fleetActive()){await exportFleetPlanning('B');return;}const key=S.key,step=S.step,ver=version();try{const data=await api(`/api/scenes/${encodeURIComponent(key)}/planning?step=${step}&version=${ver}`);const blob=new Blob([JSON.stringify(data,null,2)],{type:'application/json'}),url=URL.createObjectURL(blob),a=element('a');a.href=url;a.download=`planning-${key}-${ver}-frame${step}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);toast(`已导出 ${key} 第 ${step} 帧：道路图、当前状态及来源，不包含未来轨迹。`);}catch(error){toast('导出失败：'+error.message);}}
  function showContract(){if(!S.scene)return;text('contract-description',fleetActive()?'多车实验按车辆隔离已接收地图、附着状态和目标。导出绑定当前已显示的引擎帧，使用该车实际物理位置；B / C 可在各自卡片导出。':'导出当前选中的场景、道路版本和回放时刻。固定预览汇总整段记录；导出仅使用所选时刻已知的观测。结构与状态分层，坐标保持源城市 XY 米制。');if(fleetActive()){text('contract-json',JSON.stringify({scene:S.scene.scene_id,step:S.step,frame_id:S.engineDisplayedFrame?.frame_id,mode:S.fleet.mode,variant:S.fleet.variant,vehicles:fleetVehicles().map(r=>({vehicle_id:r.vehicle_id,received_version:r.received_version,received_step:r.received_step,goal_city_m:r.goal_city_m||r.goal_scenario_m,position_city_m:fleetPosition(r)}))},null,2));$('contract-dialog').showModal();return;}text('contract-json',JSON.stringify({scene:S.scene.scene_id,dataset:S.scene.dataset,map_selection:version(),step:S.step,source_timestamp_ns:S.snapshot?.source_timestamp_ns||null,coordinate_reference:S.scene.identity,map_version:S.scene.versions[version()].map_sha256,planning_contract:S.scene.planning_contract},null,2));$('contract-dialog').showModal();}
  async function setOverlay(){viewer?.render();if(S.mode==='engine'&&S.engine.ready)await engineCommand({command:'overlays',structure:$('show-structure').checked,state:$('show-states').checked});}
  document.querySelectorAll('[data-mode]').forEach(button=>button.onclick=()=>{stopPlayback();setMode(button.dataset.mode);$('show-traffic').disabled=S.mode==='engine';$('show-traffic').parentElement.title=S.mode==='engine'?'引擎参与者保持原场景数据；此开关仅用于地图审阅':'';if(S.mode==='engine'){const ready=S.engine.ready&&S.engine.scene_id===S.key&&S.engine.version===version();$('engine-placeholder').hidden=ready;$('engine-frame').hidden=!ready||!hasDisplayedFrame()||S.engineDisplayedFrame.step!==S.step;if(ready&&S.engineFrameError){restoreDisplayedFrameState();$('engine-frame-error').hidden=false;setLoading(false);}else if(ready&&S.engine.last_result?.step!==S.step){setLoading(true,'正在将引擎定位到选中帧');engineCommand({command:'seek',step:S.step});}else if(ready)retryEngineFrame();}else setLoading(false);updateTimeline();});
  document.querySelectorAll('[data-camera]').forEach(button=>button.onclick=()=>setCameraPreset(button.dataset.camera));
  document.querySelectorAll('[data-tab]').forEach(button=>button.onclick=()=>setTab(button.dataset.tab));
  $('start-engine').onclick=()=>launchEngine();$('engine-launch-inline').onclick=()=>launchEngine();$('start-fleet').onclick=launchFleet;$('fleet-publish').onclick=()=>fleetCommand({action:'publish'});$('map-version').onchange=e=>changeVersion(e.target.value);$('change-search').oninput=renderInspector;
  $('focus-selection').onclick=focusSelection;$('show-structure').onchange=setOverlay;$('show-states').onchange=setOverlay;$('show-traffic').onchange=()=>{viewer?.updateActors();viewer?.render();};
  $('play-pause').onclick=play;$('restart').onclick=()=>{stopPlayback();seek(0);};$('previous-frame').onclick=()=>{stopPlayback();seek(S.step-1);};$('next-frame').onclick=()=>{stopPlayback();seek(S.step+1);};$('play-speed').onchange=e=>{S.speed=Number(e.target.value);};
  $('timeline').addEventListener('input',e=>{stopPlayback();const next=Number(e.target.value);clearTimeout(S.seekTimer);S.seekTimer=setTimeout(()=>seek(next),60);});$('timeline').addEventListener('change',e=>{clearTimeout(S.seekTimer);seek(Number(e.target.value));});
  $('export-planning').onclick=exportPlanning;$('export-planning-dialog').onclick=exportPlanning;$('planning-contract').onclick=showContract;$('close-contract').onclick=()=>$('contract-dialog').close();$('retry-scene').onclick=()=>{const key=S.key;S.key=null;selectScene(key);};
  document.addEventListener('keydown',e=>{if(['INPUT','SELECT','TEXTAREA','BUTTON'].includes(e.target.tagName)||$('contract-dialog').open)return;if(e.code==='Space'){e.preventDefault();play();}if(e.key==='ArrowLeft'){e.preventDefault();stopPlayback();seek(S.step-1);}if(e.key==='ArrowRight'){e.preventDefault();stopPlayback();seek(S.step+1);}});
  $('retry-engine-frame').onclick=retryEngineFrame;
  $('regenerate-engine-frame').onclick=()=>engineCommand({command:'frame'});
  window.MAP_PLATFORM={get state(){return S;},get camera(){return viewer?.cameraConfig;},selectScene,seek,setMode,setCameraPreset,selectItem,launchEngine,launchFleet,fleetCommand,followFleetVehicle,stopPlayback,play,retryEngineFrame};
  (async()=>{try{const data=await api('/api/scenes');S.catalog=Array.isArray(data)?data:data.scenes||[];if(!S.catalog.length)throw new Error('当前仓库没有可运行的场景数据。');renderCatalog();const requested=new URLSearchParams(location.search).get('scene');const first=S.catalog.find(item=>keyOf(item)===requested)||S.catalog.find(item=>keyOf(item)==='nuscenes-scene0980')||S.catalog[0];await selectScene(keyOf(first));}catch(error){showError(error);text('data-status','本地服务连接失败');}})();
})();
