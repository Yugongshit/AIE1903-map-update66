#!/usr/bin/env python3
"""Measure browser render/poll costs with real road geometry and synthetic 10Hz poses.

No MetaDrive physics is executed. Use --revision COMMIT for a repeatable baseline.
Request latency is intentionally fixed; these numbers are not device/native FPS.
"""
import json,time,threading,statistics,argparse,subprocess,hashlib
from pathlib import Path
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--name',default='current');ap.add_argument('--revision');ap.add_argument('--input-delay-ms',type=float,default=40);ap.add_argument('--data-python',default='.venv-sim/bin/python');args=ap.parse_args()
root=Path(__file__).resolve().parents[1]
data=subprocess.run([str(root/args.data_python),'-c','import json;from pathlib import Path;from map_update.simulation_platform_data import load_scene,build_snapshot,catalog;r=Path.cwd();s=load_scene(r,"nuscenes-scene0980");print(json.dumps({"scene":s,"snapshots":{"0":build_snapshot(s,0)},"catalog":catalog(r)}))'],cwd=root,text=True,capture_output=True,check=True)
f=json.loads(data.stdout);scene=f['scene'];key=scene['key'];runtime={'active':False,'start':0};requests=[]
assets={name:(subprocess.check_output(['git','show',args.revision+':map_update/'+name],cwd=root).decode() if args.revision else (root/'map_update'/name).read_text()) for name in ['simulation_platform.js','simulation_platform.html','simulation_platform.css']}
source_hash=hashlib.sha256(assets['simulation_platform.js'].encode()).hexdigest()
class Files(SimpleHTTPRequestHandler):
 def __init__(self,*a,**kw):super().__init__(*a,directory=str(root/'map_update'),**kw)
 def log_message(self,*a):pass
server=ThreadingHTTPServer(('127.0.0.1',0),Files);threading.Thread(target=server.serve_forever,daemon=True).start()
script=assets['simulation_platform.js'].replace('viewer=new ReviewRenderer()','viewer=window.__reviewViewer=new ReviewRenderer()',1)
script=script.replace('this.renderer.render(this.world,this.camera);','{const start=performance.now();this.renderer.render(this.world,this.camera);window.__draws.push({t:performance.now(),ms:performance.now()-start,calls:this.renderer.info.render.calls});}',1)
static=[dict(a,position_city_m=a['position'],heading_rad=a['heading'],length_m=a['length'],width_m=a['width'],height_m=a['height']) for a in scene['static_overview_actors']]
def frame():
 step=max(0,int((time.monotonic()-runtime['start'])*10));rows={}
 for n,id in enumerate(['ego','P1','P2','P3','P4','P5']):rows[id]={'vehicle_id':id,'mode':'manual' if id=='P1' else 'external' if id=='ego' else 'ai','position_city_m':[440+n*3+step*.1,1257+n*2],'position_scenario_m':[440+n*3+step*.1,1257+n*2],'heading_rad':0,'speed_m_s':1,'length_m':4.5,'width_m':1.8,'height_m':1.5,'collision':{'vehicle':False}}
 snapshot=dict(f['snapshots']['0'],step=step)
 return {'step':step,'state_id':f'state-{step}','frame_id':None,'image_available':False,'sequence':step+1,'scene_id':scene['scene_id'],'dataset':scene['dataset'],'map_features_sha256':scene['versions']['updated']['map_sha256'],'live_engine':True,'snapshot':snapshot,'interactive':{'enabled':True,'step':step,'sim_time_s':step*.1,'vehicles':rows,'static_actors':static}}
def intercept(route):
 path=urlparse(route.request.url).path
 if path.endswith('/simulation_platform.html') or path.endswith('/simulation_platform.css'):
  name=path.rsplit('/',1)[-1];route.fulfill(body=assets[name],content_type='text/html' if name.endswith('.html') else 'text/css');return
 if path.endswith('/simulation_platform.js'):route.fulfill(body=script,content_type='application/javascript');return
 if path=='/api/scenes':data=f['catalog']
 elif path==f'/api/scenes/{key}':data=scene
 elif path.endswith('/snapshot'):data=f['snapshots']['0']
 elif path=='/api/interactive/open':runtime.update(active=True,start=time.monotonic());data={'room':{'session_id':'benchmark','epoch':1,'host_token':'fixture-host','join_token':'fixture-join'},'phase':'starting'}
 elif path=='/api/interactive/state':
  requests.append(time.monotonic());time.sleep(args.input_delay_ms/1000);result=frame();data={'active':True,'playing':True,'phase':'running','room':{'session_id':'benchmark','epoch':1,'scene_id':key,'version':'updated','step':result['step'],'vehicles':{id:{'mode':r['mode'],'claimed':False} for id,r in result['interactive']['vehicles'].items()}},'engine':{'last_result':result}}
 else:raise AssertionError(path)
 route.fulfill(body=json.dumps(data),content_type='application/json')
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox','--enable-webgl','--use-gl=angle','--use-angle=swiftshader'])
 page=browser.new_page(viewport={'width':1440,'height':1000});page.add_init_script('window.__draws=[];window.__samples=[];');page.route('**/api/**',intercept);page.route('**/simulation_platform.js',intercept);page.route('**/simulation_platform.html',intercept);page.route('**/simulation_platform.css',intercept);page.goto(f'http://127.0.0.1:{server.server_port}/simulation_platform.html');page.wait_for_function('MAP_PLATFORM.state.snapshot != null');page.wait_for_timeout(1000);closed=page.locator('#viewport').bounding_box();page.evaluate('window.__draws=[]');page.wait_for_timeout(2200);idle=page.evaluate('window.__draws');
 if page.locator('#open-driving').count():page.locator('#open-driving').click()
 elif not page.locator('#interactive-panel').evaluate('(n)=>n.open'):page.locator('#interactive-panel > summary').click()
 opened=page.locator('#viewport').bounding_box();page.locator('#start-interactive').click();page.wait_for_function('MAP_PLATFORM.interactive.frame != null');page.wait_for_timeout(700);page.evaluate('window.__draws=[];window.__sampleActive=true;function s(t){if(!window.__sampleActive)return;const a=__reviewViewer.actorMeshes.get("P1");window.__samples.push({t,x:a?.position.x,authoritative:MAP_PLATFORM.interactive.frame?.interactive.vehicles.P1.position_city_m[0]-MAP_PLATFORM.state.scene.origin[0],step:MAP_PLATFORM.state.step});requestAnimationFrame(s)};requestAnimationFrame(s)');requests.clear();page.wait_for_timeout(4200);page.evaluate('window.__sampleActive=false');active=page.evaluate('window.__draws');samples=page.evaluate('window.__samples');
 intervals=[(b-a)*1000 for a,b in zip(requests,requests[1:])];xs=[round(r['x'],5) for r in samples if r.get('x') is not None];result={'name':args.name,'fixture':'real nuScenes geometry and synthetic completed poses; no native physics benchmark','browser_backend':'Chromium SwiftShader software WebGL','state_frequency_hz':10,'response_delay_ms':args.input_delay_ms,'ui_source_sha256':source_hash,'revision':args.revision,'map_closed':closed,'map_controls_open':opened,'idle_draws_in_2_2s':len(idle),'active_draws_in_4_2s':len(active),'mean_draw_calls':statistics.mean(r['calls'] for r in active) if active else 0,'render_cpu_p50_ms':statistics.median(r['ms'] for r in active) if active else 0,'render_cpu_p95_ms':sorted(r['ms'] for r in active)[int(len(active)*.95)] if active else 0,'poll_interval_p50_ms':statistics.median(intervals) if intervals else None,'visual_sample_count':len(samples),'visual_distinct_positions':len(set(xs)),'max_visual_lead_m':max((r['x']-r['authoritative'] for r in samples if r.get('x') is not None),default=0)}
 out=root/'outputs/driving-ui-performance';out.mkdir(exist_ok=True,parents=True);(out/(args.name+'.json')).write_text(json.dumps(result,indent=2)+'\n');page.screenshot(path=str(out/(args.name+'.png')));print(json.dumps(result,indent=2));browser.close()
server.shutdown()
