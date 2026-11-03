/* User-operated review workspace. All review drafts stay separate from M3. */
(()=>{
'use strict';
document.body.classList.add('workbench');
document.title='AIE1903 · 地图证据复核工作台';
document.querySelector('h1').textContent='地图证据复核工作台';
document.querySelector('.topbar .sub').textContent='nuScenes · scene-0980 · Singapore one-north · 选择、对照、测量与复核';
const wb={family:'all',query:'',sort:'priority',visibleOnly:false,compare:false,tool:'browse',measure:[],
  reviews:new Map(),storage:true,inspector:'decision',vehicle:null,toast:null,dragStart:null};
window.WB=wb;
const storageKey='aie1903-review-v2:'+R.dataset_id+':'+R.scene_id+':'+R.map.source_sha256;
const legacyStorageKey='aie1903-review-v1:'+R.scene_id+':'+R.map.source_sha256;
const legacyNuScenes=R.dataset_id==='nuscenes'&&R.scene_id==='scene-0980'&&R.map.source_sha256==='17939122b940d7faff50fa7ff623ded7f739549a781c32dff1ecd1ac7bebca02';
const allowed=new Set(['unreviewed','needs_evidence','suspected_change','suspected_perception_issue']);
const statuses={unreviewed:'未复核',needs_evidence:'需补证据',suspected_change:'疑似地图变化',suspected_perception_issue:'疑似感知 / 对齐问题'};
const items=[...R.candidates.map(c=>({id:c.candidate_id,c,u:units.get(c.candidate_id)})),
 ...R.m3.review_units.filter(u=>!u.candidate_id).map(u=>({id:u.record_id,c:null,u}))];
const itemMap=new Map(items.map(i=>[i.id,i]));
const div=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
function html(parent,markup){parent.insertAdjacentHTML('beforeend',markup);}
function toast(message){let n=$('wb-toast');if(!n){n=div('div','wb-toast');n.id='wb-toast';n.setAttribute('role','status');document.body.append(n);}n.textContent=message;n.hidden=false;clearTimeout(wb.toast);wb.toast=setTimeout(()=>n.hidden=true,3800);}
const sceneGrid=document.querySelector('.scene-grid'),mapPanel=sceneGrid.children[0],inspector=sceneGrid.children[1];
mapPanel.classList.add('wb-map-panel');inspector.classList.add('wb-inspector');
mapPanel.querySelector('h2').textContent='场景工作区';mapPanel.querySelector('.panel-head .sub').textContent='滚轮缩放 · 拖动平移 · 点击曲线或占用轮廓选中';
const browser=div('section','panel wb-cases');
browser.innerHTML=`<div class="wb-cases-head"><h2>证据对象 <span class="wb-count" id="wb-count"></span></h2><input id="wb-search" type="search" placeholder="搜索 ID、轨迹或类型…" aria-label="搜索证据对象"><div class="wb-filters"><button data-family="all" class="active">全部 30</button><button data-family="lane">标线 / M3 7</button><button data-family="occupancy">占用 23</button><button data-family="priority">优先 2</button></div><div class="wb-case-options"><label><input id="wb-visible" type="checkbox">只看本帧</label><select id="wb-sort" aria-label="候选排序"><option value="priority">优先复核</option><option value="score">证据分数</option><option value="start">起始时间</option></select></div></div><div id="wb-case-list" role="listbox" aria-label="证据对象列表"></div><div class="wb-cases-footer">29 个候选 + 1 个独立局部匹配窗口</div>`;
sceneGrid.prepend(browser);
const wrap=mapPanel.querySelector('.map-wrap'),stage=div('div','wb-map-stage');stage.id='wb-map-stage';wrap.insertBefore(stage,$('map'));
const reference=elem('svg',{id:'wb-reference-map',role:'img','aria-label':'仅显示参考地图的同步对照视图'});reference.setAttribute('hidden','');stage.append(reference,$('map'));
html(wrap,`<span class="wb-view-label" id="wb-reference-label" hidden>参考地图</span><span class="wb-view-label" id="wb-observation-label" hidden>地图 + 当前帧观测</span><div class="wb-map-nav"><button id="wb-zoom-in" aria-label="放大">+</button><button id="wb-zoom-out" aria-label="缩小">−</button><button id="wb-fullscreen" aria-label="地图全屏">⛶</button></div><div class="wb-map-help" id="wb-measure-help" hidden>在地图上点击两点，量取平面距离</div>`);
html(mapPanel.querySelector('.map-tools'),'<button id="wb-compare">对照视图</button><button id="wb-measure">量距</button>');
const mapStatus=div('div','wb-map-status');html(mapStatus,'<span id="wb-pointer">城市坐标 · 米</span><strong id="wb-selection-status">点击对象查看证据</strong>');wrap.after(mapStatus);
html(mapPanel.querySelector('.player-buttons'),'<select id="wb-speed" class="wb-speed" aria-label="播放速度"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select><input id="wb-frame-number" class="wb-frame-input" type="number" min="0" max="40" value="30" aria-label="跳转到帧">');
const timeline=div('div','wb-timeline');timeline.innerHTML='<div class="wb-timeline-head"><b id="wb-chart-title">逐帧差异</b><span id="wb-chart-current"></span></div><svg id="wb-evidence-chart" viewBox="0 0 700 65" role="img" aria-label="点击图表跳转到对应帧"></svg><div class="wb-chart-foot"><span>点击图表跳帧 · 空格播放 / 暂停 · ← → 单帧</span><span id="wb-chart-unit"></span></div>';mapPanel.append(timeline);
const tabs=div('div','wb-inspector-tabs');tabs.innerHTML='<button data-inspector="decision" class="active">系统结论</button><button data-inspector="observations">本帧观测</button><button data-inspector="review">我的复核</button>';inspector.querySelector('.detail-top').after(tabs);
const observationPane=div('div','wb-observation');observationPane.id='wb-observation-pane';observationPane.hidden=true;inspector.querySelector('.detail-bottom').before(observationPane);
const reviewPane=div('div');reviewPane.id='wb-review-pane';reviewPane.hidden=true;
reviewPane.innerHTML=`<div class="wb-review-note">这是你的复核草稿。它单独保存在当前浏览器，不改动 M3 结论，也不作为独立真值计算准确率。</div><label class="wb-review-label" for="wb-review-status">复核意见</label><select id="wb-review-status"><option value="unreviewed">未复核</option><option value="needs_evidence">需补证据</option><option value="suspected_change">疑似地图变化</option><option value="suspected_perception_issue">疑似感知 / 对齐问题</option></select><label class="wb-review-label" for="wb-review-author">复核人（可选）</label><input class="wb-review-field" id="wb-review-author" maxlength="80" placeholder="你的名字或缩写"><label class="wb-review-label" for="wb-review-note">判断依据与待办</label><textarea class="wb-review-field" id="wb-review-note" maxlength="3000" placeholder="例如：第30帧的投影曲线与地图有偏差，需要核对标定和遮挡…"></textarea><label class="wb-review-label" for="wb-review-ref">补充证据来源（可选）</label><input class="wb-review-field" id="wb-review-ref" maxlength="500" placeholder="截图编号、记录 ID 或资料位置"><div class="wb-review-save" id="wb-review-saved">还没有复核记录</div><div class="wb-review-actions"><button id="wb-export-reviews" class="primary">导出全部复核</button><button id="wb-import-reviews">导入记录</button></div><input id="wb-import-file" type="file" accept=".json,application/json" hidden><div class="wb-review-info">导出文件可供小组汇总。浏览器清理本地数据前，请先导出。</div>`;
inspector.querySelector('.detail-bottom').before(reviewPane);
const progress=div('span','wb-review-progress');progress.id='wb-review-progress';document.querySelector('.actions').prepend(progress);
const share=div('button','', '复制当前视图');share.id='wb-share';document.querySelector('.actions').append(share);
const help=div('button','','操作说明');help.id='wb-help';document.querySelector('.actions').append(help);
function validatedReviewFile(payload){
 const legacy=payload?.schema==='aie1903-human-review-draft-1.0'&&legacyNuScenes;
 if(!payload||(!legacy&&payload.schema!=='aie1903-human-review-draft-1.1')||payload.role!=='personal_review_not_ground_truth'||payload.scene_id!==R.scene_id||payload.map_sha256!==R.map.source_sha256)throw Error('记录的场景或地图版本不匹配');
 if(payload.dataset_id!==R.dataset_id&&!(legacy&&payload.dataset_id===undefined))throw Error('记录的数据集不匹配；nuScenes 与 Argoverse 分开复核');
 if(!Array.isArray(payload.records)||payload.records.length>items.length)throw Error('复核记录数量不正确');
 const result=new Map();for(const r of payload.records){if(!r||!itemMap.has(r.record_id)||result.has(r.record_id)||!allowed.has(r.status))throw Error('存在未知或重复的对象 / 意见类型');
  for(const[k,max]of [['note',3000],['author',80],['evidence_reference',500]])if(typeof r[k]!=='string'||r[k].length>max)throw Error('复核字段格式不正确');
  result.set(r.record_id,{record_id:r.record_id,status:r.status,note:r.note,author:r.author,evidence_reference:r.evidence_reference,updated_at:typeof r.updated_at==='string'?r.updated_at:''});}
 return result;
}
function reviewFile(){return {schema:'aie1903-human-review-draft-1.1',role:'personal_review_not_ground_truth',dataset_id:R.dataset_id,scene_id:R.scene_id,map_sha256:R.map.source_sha256,map_version:R.map.version,records:[...wb.reviews.values()]};}
try{let raw=localStorage.getItem(storageKey);if(!raw&&legacyNuScenes){raw=localStorage.getItem(legacyStorageKey);if(raw){wb.reviews=validatedReviewFile(JSON.parse(raw));localStorage.setItem(storageKey,JSON.stringify(reviewFile()));}}else if(raw){wb.reviews=validatedReviewFile(JSON.parse(raw));}}catch(e){wb.storage=false;toast('本地记录无法读取；可以继续操作并导出复核草稿。');}
function meaningful(r){return r&&(r.status!=='unreviewed'||r.note.trim()||r.author.trim()||r.evidence_reference.trim());}
function saveReview(){const r={record_id:state.selected,status:$('wb-review-status').value,note:$('wb-review-note').value,author:$('wb-review-author').value,evidence_reference:$('wb-review-ref').value,updated_at:new Date().toISOString()};
 if(meaningful(r))wb.reviews.set(r.record_id,r);else wb.reviews.delete(r.record_id);
 try{localStorage.setItem(storageKey,JSON.stringify(reviewFile()));wb.storage=true;}catch(e){wb.storage=false;}
 $('wb-review-saved').textContent=wb.storage?'已自动保存在当前浏览器':'当前浏览器无法持久保存，请导出记录';updateProgress();renderList();
}
function updateProgress(){$('wb-review-progress').textContent=`我的复核 ${wb.reviews.size} / ${items.length}`;}
function renderReview(){const r=wb.reviews.get(state.selected);$('wb-review-status').value=r?.status||'unreviewed';$('wb-review-author').value=r?.author||'';$('wb-review-note').value=r?.note||'';$('wb-review-ref').value=r?.evidence_reference||'';$('wb-review-saved').textContent=r?(wb.storage?'已保存在当前浏览器':'暂存于本次会话，请导出'):'还没有复核记录';}
for(const id of ['wb-review-status','wb-review-author','wb-review-note','wb-review-ref'])$(id).addEventListener('input',saveReview);
$('wb-export-reviews').onclick=()=>download(R.dataset_id+'-'+R.scene_id+'-my-review-drafts.json',reviewFile());
$('wb-import-reviews').onclick=()=>$('wb-import-file').click();
$('wb-import-file').onchange=async e=>{const f=e.target.files[0];if(!f)return;try{if(f.size>1000000)throw Error('文件超过1MB，请选择复核 JSON 文件');const incoming=validatedReviewFile(JSON.parse(await f.text()));const conflicts=[...incoming.keys()].filter(k=>wb.reviews.has(k));if(conflicts.length&&!confirm(`导入将覆盖 ${conflicts.length} 条已有复核草稿。继续导入？`))return;for(const[k,v]of incoming)wb.reviews.set(k,v);try{localStorage.setItem(storageKey,JSON.stringify(reviewFile()));wb.storage=true;}catch{wb.storage=false;}renderReview();updateProgress();renderList();toast(`已导入 ${incoming.size} 条复核草稿；系统结论保持原样。`);}catch(err){toast('导入未执行：'+err.message);}finally{e.target.value='';}};
function setInspector(tab){wb.inspector=tab;inspector.querySelector('.detail-body').hidden=tab!=='decision';observationPane.hidden=tab!=='observations';reviewPane.hidden=tab!=='review';tabs.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.inspector===tab));renderReview();renderObservations();}
tabs.querySelectorAll('button').forEach(b=>b.onclick=()=>setInspector(b.dataset.inspector));
function present(item){return item.c?item.c.frame_indices.includes(current().frame_index):state.frame>=item.u.frame_window[0]&&state.frame<=item.u.frame_window[1];}
function visibleItems(){const q=wb.query.toLowerCase();return items.filter(i=>(wb.family==='all'||wb.family==='lane'&&i.c?.change_type!=='occupancy'||wb.family==='occupancy'&&i.c?.change_type==='occupancy'||wb.family==='priority'&&i.u?.review_priority==='PRIORITY')&&(!wb.visibleOnly||present(i))&&(!q||[i.id,shortId(i.id),i.c?.track_id||i.u?.track_id||'',i.c?.change_type==='occupancy'?'占用 道路':'标线 匹配'].join(' ').toLowerCase().includes(q))).sort((a,b)=>{if(wb.sort==='start')return (a.c?.frame_window||a.u.frame_window)[0]-(b.c?.frame_window||b.u.frame_window)[0];if(wb.sort==='priority'){const d=Number(b.u?.review_priority==='PRIORITY')-Number(a.u?.review_priority==='PRIORITY');if(d)return d;}return (b.c?.evidence_score??-1)-(a.c?.evidence_score??-1)||a.id.localeCompare(b.id);});}
function renderList(){const list=$('wb-case-list'),scroll=list.scrollTop,rows=visibleItems();list.replaceChildren();$('wb-count').textContent=rows.length+' / '+items.length;
 for(const item of rows){const {c,u,id}=item,b=div('button','wb-case-row'+(id===state.selected?' selected':''));b.dataset.id=id;b.setAttribute('role','option');b.setAttribute('aria-selected',String(id===state.selected));const top=div('div','wb-row-top'),score=div('span','score',c?c.evidence_score.toFixed(3):'局部诊断');top.append(div('span','',shortId(id)),score);const w=c?.frame_window||u.frame_window,sub=div('div','wb-row-sub',`帧 ${w[0]}–${w[1]} · ${c?c.support_frames+' 个支持帧':'15 个局部匹配帧'}`),tags=div('div','wb-row-tags');tags.append(div('span','',c?.change_type==='occupancy'?'占用事件':'标线复核'));if(u?.review_priority==='PRIORITY')tags.append(div('span','priority','优先'));if(present(item))tags.append(div('span','present','本帧'));if(meaningful(wb.reviews.get(id)))tags.append(div('span','reviewed','已记录'));b.append(top,sub,tags);b.onclick=()=>{stopPlay();choose(id);};list.append(b);}
 if(!rows.length)list.append(div('div','wb-empty','没有符合筛选条件的对象。可以清空搜索或取消“只看本帧”。'));list.scrollTop=scroll;
}
$('wb-search').oninput=e=>{wb.query=e.target.value;renderList();};$('wb-sort').onchange=e=>{wb.sort=e.target.value;renderList();};$('wb-visible').onchange=e=>{wb.visibleOnly=e.target.checked;renderList();};browser.querySelectorAll('[data-family]').forEach(b=>b.onclick=()=>{wb.family=b.dataset.family;browser.querySelectorAll('[data-family]').forEach(x=>x.classList.toggle('active',x===b));renderList();});
function renderObservations(){observationPane.replaceChildren();const f=current(),item=itemMap.get(state.selected),c=item.c,u=item.u;const lines=f.observed_lines.filter(l=>l.candidate_ids.includes(state.selected)||l.m3_record_id===state.selected);const occ=f.occupancy.find(o=>o.candidate_id===state.selected);const kv=(label,value)=>{const n=div('div','kv');n.append(div('span','',label),div('span','',value));observationPane.append(n);};
 observationPane.append(div('h3','',`当前帧 ${f.frame_index} · ${f.elapsed_s.toFixed(3)} s`));kv('本帧观测',present(item)?'存在':'缺测，不代表恢复');kv('独立访问','1 次');kv('地图版本','nuScenes '+R.map.source_version);
 if(occ){kv('占用面积',occ.lane_overlap_area_m2.toFixed(3)+' m²');kv('占用宽度比例',(occ.occupied_width_fraction*100).toFixed(1)+'%');kv('物体轮廓',occ.objects.length+' 个');kv('宽度几何',occ.width_geometry_valid?'通过上游几何检查':'未通过');}
 for(const l of lines){observationPane.append(div('h3','',`感知曲线 ${l.track_id} / ${l.line_id}`));kv('点数',l.geometry_city.coordinates.length);kv('最近地图线中位距离',l.nearest_map_median_m.toFixed(4)+' m');const code=div('code','',l.source_file);observationPane.append(code);}
 const mr=R.m3.matched_frame_evidence.find(r=>u?.record_type==='MATCHED_WINDOW'&&r.frame===f.frame_index&&r.track===u.track_id);if(mr){kv('横向残差',mr.lateral_residual_m.toFixed(4)+' m');kv('局部异常分数',mr.anomaly_score.toFixed(4));kv('有效覆盖率','未知');}
 observationPane.append(div('h3','','绝对时间与来源'));observationPane.append(div('code','',f.timestamp_ns+' ns'));observationPane.append(div('p','insight','感知曲线和车辆轨迹来自已交付结果；车辆轮廓来自该帧的 nuScenes 原始标注。'));
 if(c?.map_element_id)observationPane.append(div('code','',c.map_element_id));else if(u?.reference_element_id)observationPane.append(div('code','',u.reference_element_id));
}
const oldApply=applyView;applyView=function(){oldApply();reference.setAttribute('viewBox',state.view.join(' '));drawMeasurement();};
const oldDraw=drawMap;drawMap=function(){oldDraw();
 const actors=$('map-actors');[...actors.children].filter(n=>n.tagName.toLowerCase()==='circle').forEach(n=>n.remove());
 const vehicleGroup=elem('g',{'data-kind':'vehicle-shapes'});actors.prepend(vehicleGroup);
 for(const v of current().vehicles){if(!state.layers.vehicles)break;if(v.geometry_city){const active=wb.vehicle===v.track_id;const p=path(vehicleGroup,v.geometry_city,active?'#235fa9':'#69889f',active?2:1.3,active?'#c9def8':'#e4ecf3');p.dataset.vehicle=v.track_id;p.onclick=e=>{if(wb.tool==='measure')return;e.stopPropagation();wb.vehicle=wb.vehicle===v.track_id?null:v.track_id;drawMap();updatePointerStatus();};const t=elem('title');t.textContent=`${v.category} · ${v.track_id.slice(0,8)} · ${v.speed_mps?.toFixed(1)??'未知'} m/s`;p.append(t);}}
 if(wb.vehicle&&state.layers.vehicles){const history=R.frames.filter(f=>f.frame_index<=current().frame_index).map(f=>f.vehicles.find(v=>v.track_id===wb.vehicle)?.xy).filter(Boolean);if(history.length>1)path(vehicleGroup,{type:'LineString',coordinates:history},'#6095d0',1.8,'none','4 3');}
 renderComparison();drawMeasurement();
};
function renderComparison(){if(!wb.compare)return;reference.replaceChildren();reference.append(elem('rect',{x:state.view[0],y:state.view[1],width:state.view[2],height:state.view[3],fill:'#f4f7fa'}));for(const e of R.map.elements){const line=e.geometry_city.type==='LineString';path(reference,e.geometry_city,line?'#749bb7':'#cbd9e6',line?1.7:.8,line?'none':e.layer==='road_segment'?'#e8eef4':'#f7fafc',e.layer==='lane_connector'?'4 4':'');}}
function updatePointerStatus(){const v=current().vehicles.find(v=>v.track_id===wb.vehicle);$('wb-selection-status').textContent=v?`车辆 ${v.track_id.slice(0,8)} · ${v.speed_mps?.toFixed(1)??'未知'} m/s · 显示历史轨迹`:wb.measure.length===2?'量距 '+Math.hypot(wb.measure[0][0]-wb.measure[1][0],wb.measure[0][1]-wb.measure[1][1]).toFixed(2)+' m':'选择 '+shortId(state.selected);}
function drawMeasurement(){let g=$('wb-measure-layer');if(!g){g=elem('g',{id:'wb-measure-layer','pointer-events':'none'});$('map').append(g);}g.replaceChildren();const r=state.view?state.view[2]*.006:.5;for(const [i,p]of wb.measure.entries()){g.append(elem('circle',{cx:p[0],cy:p[1],r,fill:'#fff',stroke:'#2475cd','stroke-width':2,'vector-effect':'non-scaling-stroke'}));textNode(g,i?'B':'A',{x:p[0]+r*1.5,y:p[1]-r*1.5,fill:'#2868ad','font-size':r*2.5});}
 if(wb.measure.length===2){const[a,b]=wb.measure,d=Math.hypot(a[0]-b[0],a[1]-b[1]);g.prepend(elem('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:'#2475cd','stroke-width':2,'stroke-dasharray':'5 4','vector-effect':'non-scaling-stroke'}));const t=textNode(g,d.toFixed(2)+' m',{x:(a[0]+b[0])/2,y:(a[1]+b[1])/2-r*2,fill:'#235b96','font-size':r*2.6,'text-anchor':'middle','paint-order':'stroke',stroke:'white','stroke-width':r*.7});t.dataset.distance=d;}
 updatePointerStatus();
}
function zoom(factor){const v=state.view;if(v[2]*factor<6||v[2]*factor>2000)return;state.view=[v[0]+v[2]*(1-factor)/2,v[1]+v[3]*(1-factor)/2,v[2]*factor,v[3]*factor];applyView();renderComparison();}
$('wb-zoom-in').onclick=()=>zoom(.75);$('wb-zoom-out').onclick=()=>zoom(1.33);
$('wb-compare').onclick=()=>{wb.compare=!wb.compare;reference.toggleAttribute('hidden',!wb.compare);stage.classList.toggle('comparing',wb.compare);$('wb-reference-label').hidden=$('wb-observation-label').hidden=!wb.compare;$('wb-compare').classList.toggle('active',wb.compare);focus();renderComparison();};
$('wb-fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await mapPanel.requestFullscreen();}catch{toast('浏览器没有开启全屏；地图仍可缩放和拖动。');}};
function setMeasure(on){wb.tool=on?'measure':'browse';stage.classList.toggle('measuring',on);$('wb-measure').classList.toggle('active',on);$('wb-measure-help').hidden=!on;wb.dragStart=null;}
$('wb-measure').onclick=()=>{setMeasure(wb.tool!=='measure');if(wb.tool==='measure'){wb.measure=[];drawMeasurement();}};
function worldPoint(event,svg){return new DOMPoint(event.clientX,event.clientY).matrixTransform(svg.getScreenCTM().inverse());}
for(const svg of [$('map'),reference]){
 svg.onpointerdown=e=>{wb.suppressClick=false;wb.dragStart={point:worldPoint(e,svg),client:[e.clientX,e.clientY],view:[...state.view]};if(wb.tool==='browse'&&!(e.target.closest('#map-observations')||e.target.dataset.vehicle))svg.setPointerCapture(e.pointerId);};
 svg.onpointermove=e=>{const p=worldPoint(e,svg);$('wb-pointer').textContent=`X ${p.x.toFixed(2)} · Y ${(-p.y).toFixed(2)} m`;if(wb.dragStart&&wb.tool==='browse'){state.view[0]+=wb.dragStart.point.x-p.x;state.view[1]+=wb.dragStart.point.y-p.y;applyView();renderComparison();}};
 svg.onpointerup=e=>{wb.suppressClick=!!wb.dragStart&&Math.hypot(e.clientX-wb.dragStart.client[0],e.clientY-wb.dragStart.client[1])>5;if(wb.tool==='measure'&&wb.dragStart&&Math.hypot(e.clientX-wb.dragStart.client[0],e.clientY-wb.dragStart.client[1])<5){const p=worldPoint(e,svg);if(wb.measure.length===2)wb.measure=[];wb.measure.push([p.x,p.y]);drawMeasurement();}wb.dragStart=null;};
 svg.onpointercancel=()=>wb.dragStart=null;
 svg.addEventListener('click',e=>{if(wb.tool==='measure'||wb.suppressClick){wb.suppressClick=false;e.preventDefault();e.stopImmediatePropagation();}},true);
 svg.onwheel=e=>{e.preventDefault();const p=worldPoint(e,svg),s=e.deltaY>0?1.12:.89,v=state.view;if(v[2]*s<6||v[2]*s>2000)return;state.view=[p.x+(v[0]-p.x)*s,p.y+(v[1]-p.y)*s,v[2]*s,v[3]*s];applyView();renderComparison();};
}
function evidenceValues(){const u=selectedUnit();if(!selected())return R.m3.matched_frame_evidence.filter(r=>r.track===u.track_id).map(r=>({frame:r.frame,value:r.anomaly_score,support:false}));return R.frame_evidence.filter(r=>r.candidate_id===state.selected).map(r=>({frame:r.frame,value:r.value,support:r.supports}));}
function renderChart(){const svg=$('wb-evidence-chart'),rows=evidenceValues(),c=selected(),w=700,h=65,pad=8;svg.replaceChildren();const values=rows.map(r=>r.value).filter(v=>v!==null),max=Math.max(...values,1)*1.15,x=f=>pad+f/40*(w-pad*2),y=v=>h-13-v/max*(h-22);$('wb-chart-title').textContent=c?.change_type==='occupancy'?'逐帧占用面积':c?'逐帧地图距离':'逐帧局部异常分数';$('wb-chart-unit').textContent=c?.change_type==='occupancy'?'单位 m²':c?'单位 m':'标准化分数 · 非概率';const now=rows.find(r=>r.frame===state.frame);$('wb-chart-current').textContent=now&&now.value!==null?`当前帧 ${state.frame} · ${now.value.toFixed(3)}`:`当前帧 ${state.frame} · 无该对象观测`;
 svg.append(elem('line',{x1:pad,y1:h-13,x2:w-pad,y2:h-13,stroke:'#dce5f1'}));svg.append(elem('line',{x1:x(state.frame),y1:3,x2:x(state.frame),y2:h-5,stroke:'#78a6e1','stroke-dasharray':'3 3'}));
 let previous=null;for(const r of rows){if(r.value===null){previous=null;continue;}if(previous&&r.frame===previous.frame+1)svg.append(elem('line',{x1:x(previous.frame),y1:y(previous.value),x2:x(r.frame),y2:y(r.value),stroke:'#719ad0','stroke-width':1.6}));const p=elem('circle',{cx:x(r.frame),cy:y(r.value),r:r.frame===state.frame?4:2.8,fill:r.support?'#618ac6':'#d7ac61',stroke:'#fff','stroke-width':1});const t=elem('title');t.textContent=`第${r.frame}帧：${r.value.toFixed(4)}`;p.append(t);svg.append(p);previous=r;}
 for(const f of [0,10,20,30,40])textNode(svg,String(f),{x:x(f),y:h-1,'text-anchor':'middle','font-size':8,fill:'#9baabd'});
}
$('wb-evidence-chart').onclick=e=>{const p=worldPoint(e,$('wb-evidence-chart'));stopPlay();state.frame=Math.max(0,Math.min(40,Math.round((p.x-8)/684*40)));render();};
function startPlay(){if(state.play){stopPlay();return;}if(state.frame===40)state.frame=0;$('play').textContent='Ⅱ';$('play').setAttribute('aria-label','暂停');state.play=setInterval(()=>{if(state.frame===40){stopPlay();return;}move(1);},500/Number($('wb-speed').value));}
$('play').onclick=startPlay;$('wb-speed').onchange=()=>{if(state.play){stopPlay();startPlay();}};$('wb-frame-number').onchange=e=>{stopPlay();const n=Number(e.target.value);state.frame=Number.isFinite(n)?Math.max(0,Math.min(40,Math.round(n))):state.frame;render();};
function route(){try{const p=new URLSearchParams({case:state.selected,frame:String(state.frame),tab:state.tab});history.replaceState(null,'','#'+p.toString());}catch{}}
const baseRender=render;render=function(){baseRender();$('wb-frame-number').value=state.frame;renderList();renderChart();renderReview();renderObservations();updatePointerStatus();route();};
const baseChoose=choose;choose=function(id,jump=true){wb.vehicle=null;wb.measure=[];baseChoose(id,jump);const row=[...$('wb-case-list').children].find(n=>n.dataset.id===id);row?.scrollIntoView({block:'nearest'});};
const baseTab=showTab;showTab=function(name){baseTab(name);route();};
$('wb-share').onclick=async()=>{route();try{await navigator.clipboard.writeText(location.href);toast('已复制当前对象、帧和页面链接；不包含个人复核草稿。');}catch{prompt('复制当前视图链接',location.href);}};
$('wb-help').onclick=()=>{let modal=$('wb-help-dialog');if(!modal){modal=div('div','wb-details-dialog');modal.id='wb-help-dialog';modal.innerHTML='<section class="wb-details-card" role="dialog" aria-modal="true" aria-label="操作说明"><h2>你可以这样操作</h2><p>① 左侧筛选对象，点击一条候选定位。<br>② 滚轮缩放，按住地图拖动；点击车辆可查看轨迹。<br>③ 拖时间轴或按 ← → 查看前后帧，空格控制播放。<br>④ 点击“对照视图”，同步比较参考地图与观测。<br>⑤ 点击“量距”，在地图上点两点；Esc 退出。<br>⑥ 右侧“我的复核”可写意见，自动保存在浏览器，并可导出汇总。</p><button id="wb-help-close">开始操作</button></section>';document.body.append(modal);$('wb-help-close').onclick=()=>modal.hidden=true;modal.onclick=e=>{if(e.target===modal)modal.hidden=true;};}modal.hidden=false;$('wb-help-close').focus();};
document.addEventListener('keydown',e=>{if(e.key==='Escape'){setMeasure(false);if($('wb-help-dialog'))$('wb-help-dialog').hidden=true;return;}if(e.target.closest('input,textarea,select,[contenteditable=true]')||state.tab!=='scene'||!$('drawer-back').hidden||($('wb-help-dialog')&&!$('wb-help-dialog').hidden))return;if(e.code==='Space'){e.preventDefault();startPlay();}else if(e.key==='ArrowLeft'){e.preventDefault();stopPlay();move(-1);}else if(e.key==='ArrowRight'){e.preventDefault();stopPlay();move(1);}});
function restoreRoute(){try{const p=new URLSearchParams(location.hash.slice(1));if(itemMap.has(p.get('case')))state.selected=p.get('case');const f=Number(p.get('frame'));if(p.has('frame')&&Number.isInteger(f)&&f>=0&&f<=40)state.frame=f;if(['scene','evaluation','evidence'].includes(p.get('tab')))state.tab=p.get('tab');}catch{}showTab(state.tab);render();if(state.tab==='scene')focus();}
updateProgress();restoreRoute();window.addEventListener('hashchange',restoreRoute);
window.addEventListener('resize',()=>{if(state.tab==='scene'){applyView();renderComparison();}});
})();
