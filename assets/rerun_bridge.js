/* Rerun 0.22.1, same-origin assets. User files stay in this browser. */
(()=>{
 const C=JSON.parse(document.getElementById('rerun-config').textContent),T=C.text;
 const byId=id=>document.getElementById(id), delay=ms=>new Promise(r=>setTimeout(r,ms));
 let viewer=null,channel=null,reader=null,generation=0,mode='idle',activeId=null,busy=false,syncing=false,hasOpened=false;
 const host=byId('rerun-host'),status=byId('rerun-status'),slider=byId('rerun-frame');
 function message(text,error=false){status.textContent=text;status.classList.toggle('error',error);status.setAttribute('role',error?'alert':'status');}
 function linkState(){
  const enabled=!!(viewer?.ready&&mode==='scene'&&activeId===C.recording_id&&!busy);
  slider.disabled=!enabled;byId('rerun-review').hidden=!enabled;
  byId('rerun-sync-note').textContent=enabled?T.synced:T.independent;
 }
 function context(){
  slider.value=S.frame;byId('rerun-frame-text').textContent=`${T.frame} ${S.frame} / 40 · ${R.frames[S.frame].elapsed_s.toFixed(2)} s`;
  const box=byId('rerun-candidates');box.replaceChildren();
  for(const id of R.frames[S.frame].candidate_ids){const b=document.createElement('button');b.textContent=id.split('-').at(-1);b.onclick=()=>{selectCandidate(id);window.DECISION_STUDIO?.pickCase(id,S.frame);context();};b.classList.toggle('active',S.selected===id);box.append(b);}
  byId('rerun-review').href='review/index.html#'+new URLSearchParams({case:S.selected||R.frames[S.frame].candidate_ids[0]||'',frame:S.frame,tab:'scene'});
  byId('rerun-context').hidden=mode!=='scene';
 }
 function release(){
  generation++;busy=false;activeId=null;mode='idle';
  if(reader){reader.cancel().catch(()=>{});reader=null;}
  if(channel){try{channel.close();}catch{}channel=null;}
  if(viewer){try{viewer.stop();}catch{}viewer=null;}
  host.replaceChildren();linkState();context();
 }
 function pause(){if(viewer?.ready){try{const id=viewer.get_active_recording_id();if(id)viewer.set_playing(id,false);}catch{}}}
 function fromViewer(){
  if(!viewer?.ready||mode!=='scene'||busy||syncing)return;
  const id=viewer.get_active_recording_id();if(id!==C.recording_id)return;
  const timeline=viewer.get_active_timeline(id),time=viewer.get_current_time(id,timeline);
  if(time===null||!Number.isFinite(time))return;
  let f;
  if(timeline==='frame')f=Math.max(0,Math.min(40,Math.round(time)));
  else if(timeline==='elapsed_s')f=R.frames.reduce((best,item)=>Math.abs(item.elapsed_s-time/1e9)<Math.abs(best.elapsed_s-time/1e9)?item:best).frame_index;
  else return;
  if(S.frame!==f){syncing=true;try{setFrame(f);}finally{syncing=false;}}
  context();
 }
 async function readyForData(v,token,expected=null){
  const start=performance.now();
  while(token===generation&&performance.now()-start<60000){
   if(v.ready){const id=v.get_active_recording_id();if(id&&(!expected||id===expected)){
    if(!expected||v.get_time_range(id,'frame')?.max>=40)return id;
   }}
   await delay(100);
  }
  if(token===generation)throw new Error(T.timeout);
  return null;
 }
 async function load(kind,file=null){
  // Validate before disposing of a currently usable recording.
  if(file){
   const h=new Uint8Array(await file.slice(0,8).arrayBuffer());
   if(file.size<32||String.fromCharCode(...h.slice(0,4))!=='RRF2'){message(T.badFile,true);return;}
   if(h[4]!==0||h[5]!==22||h[6]!==1){message(T.version+' '+[h[4],h[5],h[6]].join('.'),true);return;}
  }
  release();const token=generation;busy=true;mode=kind;hasOpened=true;context();linkState();
  message(T.loading);byId('rerun-source').textContent=file?T.local+': '+file.name:kind==='scene'?T.source:T.overlaySource;
  try{
   const {WebViewer}=await import(new URL(C.viewer_module,location.href).href);if(token!==generation)return;
   const v=new WebViewer();viewer=v;
   v.on('timeupdate',fromViewer);v.on('timelinechange',fromViewer);
   await v.start(file?null:kind==='scene'?C.recording:C.overlay,host,{width:'100%',height:'100%',render_backend:'webgl',hide_welcome_screen:true,allow_fullscreen:false});
   if(token!==generation){v.stop();return;}
   if(file){
    channel=v.open_channel(file.name);reader=file.stream().getReader();let count=0;
    while(token===generation){const part=await reader.read();if(part.done)break;channel.send_rrd(part.value);count+=part.value.byteLength;message(T.reading+' '+Math.round(count/file.size*100)+'%');await delay(0);}
    if(token!==generation)return;reader=null;channel.close();channel=null;
   }
   activeId=await readyForData(v,token,kind==='scene'?C.recording_id:null);if(token!==generation)return;
   busy=false;
   if(activeId){v.set_playing(activeId,false);if(kind==='scene'){v.set_active_timeline(activeId,'elapsed_s');v.set_current_time(activeId,'elapsed_s',Math.round(R.frames[S.frame].elapsed_s*1e9));}}
   message(kind==='scene'?T.ready:kind==='overlay'?T.overlayReady:T.localReady);
   linkState();context();if(S.page!=='rerun')pause();
  }catch(error){if(token!==generation)return;release();message(T.failed+' '+(error?.message||String(error)),true);}
 }
 const originalFrame=setFrame;
 setFrame=function(value){originalFrame(value);context();if(!syncing&&viewer?.ready&&!busy&&mode==='scene'&&activeId===C.recording_id){viewer.set_active_timeline(activeId,'elapsed_s');viewer.set_current_time(activeId,'elapsed_s',Math.round(R.frames[S.frame].elapsed_s*1e9));}};
 const originalPage=showPage;
 showPage=function(name){
  if(name==='rerun'){
   stopPlayback();byId('perception-video').pause();S.page='rerun';
   for(const p of ['drive','changes','issues'])byId(p+'-page').hidden=true;
   byId('rerun-page').hidden=false;document.querySelectorAll('[data-page]').forEach(b=>b.classList.toggle('active',b.dataset.page==='rerun'));
   history.replaceState(null,'','#rerun');context();if(!hasOpened)load('scene');
  }else{pause();byId('rerun-page').hidden=true;originalPage(name);}
 };
 byId('rerun-nav').onclick=()=>showPage('rerun');
 byId('rerun-default').onclick=()=>load('scene');
 byId('rerun-overlay').onclick=()=>load('overlay');
 byId('rerun-open').onclick=()=>byId('rerun-file').click();
 byId('rerun-file').onchange=event=>{const file=event.target.files[0];event.target.value='';if(file)load('local',file);};
 byId('rerun-unload').onclick=()=>{release();message(T.unloaded);byId('rerun-source').textContent=T.none;};
 byId('rerun-to-changes').onclick=()=>showPage('changes');
 byId('rerun-to-drive').onclick=()=>showPage('drive');
 byId('rerun-inspect').onclick=()=>{if(viewer?.ready)viewer.override_panel_state('selection','expanded');};
 slider.oninput=()=>{pause();setFrame(Number(slider.value));};
 document.addEventListener('visibilitychange',()=>{if(document.hidden)pause();});
 window.addEventListener('pagehide',release);
 window.addEventListener('hashchange',()=>{if(location.hash==='#rerun'&&S.page!=='rerun')showPage('rerun');});
 window.AIE_RERUN={get viewer(){return viewer;},get mode(){return mode;},get busy(){return busy;},get recordingId(){return activeId;},load,release};
 context();linkState();
 if(window.INITIAL_STUDIO_HASH==='#rerun')showPage('rerun');
})();
