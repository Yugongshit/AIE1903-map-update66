'use strict';
(() => {
  const build = window.SIMULATION_PUBLIC_BUILD || null;
  const scriptBase = new URL('.', document.currentScript?.src || location.href);
  const storageKey = 'aie1903-simulation-backend';
  const cache = new Map();
  let backend = '';

  function normalize(value) {
    if (!value || !String(value).trim()) return '';
    const parsed = new URL(String(value).trim());
    if (!['https:', 'http:'].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash)
      throw new Error('仿真服务器应为不带密码、参数或片段的 HTTP(S) 地址。');
    if (location.protocol === 'https:' && parsed.protocol !== 'https:')
      throw new Error('公网 HTTPS 页面需要连接 HTTPS 仿真服务器。');
    return parsed.href.replace(/\/+$/, '');
  }
  try {
    const shared = new URLSearchParams(location.search).get('backend');
    backend = normalize(shared != null ? shared : (build ? localStorage.getItem(storageKey) : ''));
  } catch { backend = ''; }

  function setBackend(value) {
    backend = normalize(value);
    if (backend) localStorage.setItem(storageKey, backend); else localStorage.removeItem(storageKey);
    window.dispatchEvent(new CustomEvent('simulation-backend-change', {detail: {backend}}));
    return backend;
  }
  function url(path) { return backend ? backend + '/' + path.replace(/^\/+/, '') : new URL(path, location.origin).href; }
  async function fetchJSON(path, options={}) {
    const response = await fetch(path, {...options, headers: {...(options.body ? {'Content-Type':'application/json'} : {}), ...options.headers}});
    let data;
    try { data = await response.json(); } catch { throw new Error('服务未返回有效数据（HTTP '+response.status+'）'); }
    if (!response.ok) {
      const error = new Error(data.error || data.message || '请求失败：'+response.status);
      error.status = response.status;
      error.code = data.code;
      throw error;
    }
    return data;
  }
  function clone(value) { return JSON.parse(JSON.stringify(value)); }
  async function dataFile(path, signal) {
    if (signal?.aborted) throw new DOMException('Aborted', 'AbortError');
    if (cache.has(path)) return cache.get(path);
    const response = await fetch(new URL('data/'+path, scriptBase), {signal});
    if (!response.ok) throw new Error('公网场景数据读取失败（HTTP '+response.status+'）');
    const bytes = new Uint8Array(await response.arrayBuffer());
    let value;
    if (bytes[0]===0x1f && bytes[1]===0x8b) {
      if (!('DecompressionStream' in window)) throw new Error('当前浏览器不支持场景解压，请使用新版 Chrome、Edge、Firefox 或 Safari。');
      const decoded = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
      value = await new Response(decoded).json();
    } else {
      // Some static hosts attach Content-Encoding:gzip to .gz files; Fetch
      // already decodes those. Never attempt to decompress them a second time.
      value = JSON.parse(new TextDecoder().decode(bytes));
    }
    cache.set(path, value);
    // Scene payloads are reusable; bounded frame chunks avoid retaining whole
    // recorded worlds when the user drags through several scenes.
    const chunks = [...cache.keys()].filter(key => key.includes('/frames-'));
    for (const key of chunks.slice(0, Math.max(0, chunks.length-4))) cache.delete(key);
    return value;
  }
  async function snapshot(key, step, version, signal) {
    const scene = await dataFile(key+'/scene.json.gz', signal);
    if (!Number.isInteger(step) || step<0 || step>=scene.frame_count) throw new Error('时间点超出该场景记录范围。');
    if (!['original', 'updated'].includes(version)) throw new Error('Unknown map version');
    const index = Math.floor(step/build.chunk_size);
    const rows = await dataFile(key+'/frames-'+String(index).padStart(4,'0')+'.json.gz', signal);
    const result = clone(rows[step-index*build.chunk_size]);
    result.version = version;
    result.map_sha256 = scene.versions[version].map_sha256;
    result.exported_scenario_sha256 = scene.versions[version].exported_scenario_sha256;
    return result;
  }
  async function planning(key, step, version, signal) {
    const scene = await dataFile(key+'/scene.json.gz', signal);
    const current = await snapshot(key, step, version, signal);
    const selected = scene.versions[version], provenance = selected.provenance;
    const features = clone(scene.maps[version]);
    const lanes = new Set(['LANE_SURFACE_STREET','LANE_SURFACE_UNSTRUCTURE','LANE_UNKNOWN','LANE_FREEWAY','LANE_BIKE_LANE']);
    const objects = current.actors.filter(row => !row.is_ego);
    return {schema:'simulation-planning-handoff-1.0',scene_key:key,identity:clone(scene.identity),
      map_version:current.map_sha256,map_selection:version,step,time_s:current.time_s,
      source_timestamp_ns:current.source_timestamp_ns,
      coordinate_reference:{frame:scene.coordinate,map:scene.map,units:'m'},map_features:features,
      lane_graph:Object.fromEntries(Object.entries(features).filter(([,f])=>lanes.has(f.type)).map(([id,f])=>[id,{
        entry_lanes:clone(f.entry_lanes||[]),exit_lanes:clone(f.exit_lanes||[]),
        source_lane_type:f.source_lane_type??null,vehicle_access:clone(f.vehicle_access??null)}])),
      map_state_snapshot:clone(current.states),current_objects:clone(objects),current_ego:clone(current.ego),
      persistent_obstacles:clone(objects.filter(row=>row.persistent)),route:null,requested_goal:null,
      planning_contract:clone(scene.planning_contract),provenance:{scene_export_sha256:selected.exported_scenario_sha256,
        source:clone(scene.source),pipeline_result_sha256:provenance.pipeline_result_sha256??null,
        state_source_sha256:provenance.state_source_sha256??null,source_scenario_sha256:provenance.source_scenario_sha256??null,
        data_scope:'current_snapshot_no_future_tracks_or_recorded_ego_route'}};
  }
  async function request(path, options={}) {
    if (!build || backend) return fetchJSON(url(path), options);
    if (options.method && options.method !== 'GET') throw new Error('请先连接运行 MetaDrive 的仿真服务器，再使用实时车辆控制。');
    const parsed = new URL(path, location.origin), pathname = parsed.pathname;
    if (pathname === '/api/config') return {public_static:true,engine_available:false,
      engine_label:'MetaDrive / Panda3D',review_label:'公网地图审阅',source_geometry_only:true,
      planning_schema:'simulation-planning-handoff-1.0'};
    if (pathname === '/api/scenes') return clone(await dataFile('catalog.json.gz', options.signal));
    const match = /^\/api\/scenes\/([^/]+)(?:\/(snapshot|planning|validation))?$/.exec(pathname);
    if (!match) throw new Error('公网地图审阅已可用；实时仿真需要连接 MetaDrive 服务器。');
    const key = decodeURIComponent(match[1]);
    if (!build.scene_keys.includes(key)) throw new Error('Unknown public scene');
    const version = parsed.searchParams.get('version') || 'updated';
    if (!['original','updated'].includes(version)) throw new Error('Unknown map version');
    const text = parsed.searchParams.get('step') || '0';
    if (!/^\d+$/.test(text)) throw new Error('step must be a nonnegative integer');
    const step = Number(text), action = match[2];
    if (!action) return clone(await dataFile(key+'/scene.json.gz', options.signal));
    if (action==='snapshot') return snapshot(key, step, version, options.signal);
    if (action==='planning') return planning(key, step, version, options.signal);
    return clone(await dataFile(key+'/validation-'+version+'.json.gz', options.signal));
  }
  window.SIMULATION_TRANSPORT = {request, url, setBackend, getBackend:()=>backend,
    isStatic:()=>Boolean(build), needsBackend:()=>Boolean(build&&!backend)};
})();
