"""Add a lazy native Rerun tab to the verified bilingual evidence studio."""
import argparse
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.fetch_rerun_viewer import URL,INTEGRITY

TEXT={
 'en':{
  'title':'Inspect the scene. Follow the evidence.',
  'intro':'Explore the team’s nuScenes data with the real Rerun viewer: map geometry, original object annotations and frame evidence.',
  'reference':'Reference scene: 41 frames · 2,278 annotations · 0 real base-map writes',
  'source':'Delivered geometry + original nuScenes annotations · repackaged RRD · no camera images',
  'default':'Load scene-0980','overlay':'Original overlay','open':'Open local RRD','unload':'Unload / cancel','inspect':'Show object properties','changes':'Map changes ↗','drive':'Labels & source video ↗',
  'loading':'Starting Rerun… First load downloads a 28 MB viewer, then the scene recording.',
  'ready':'Scene loaded. Drag, zoom, select objects or move the native timeline below.',
  'overlaySource':'Original supplied incremental overlay · camera images and base recording are absent',
  'overlayReady':'Original overlay loaded. It contains evidence overlays, not the full camera scene; its timeline is independent.',
  'local':'Local file', 'localReady':'Local file loaded. Its scene and timeline have not been matched to this evaluation; it is independent.',
  'reading':'Reading local recording…','unloaded':'Recording unloaded. Load the scene or choose a local file to continue.','none':'No recording is loaded.',
  'failed':'Unable to open the recording. Existing scene, video and evidence pages remain available.',
  'timeout':'No complete recording was decoded within 60 seconds. Check the file, network or browser memory, then retry.',
  'badFile':'This file is not a readable RRF2 recording. Choose an intact .rrd file.',
  'version':'This viewer requires Rerun 0.22.1. The selected recording header reports',
  'frame':'Frame','synced':'Scene frame ↔ website evidence','independent':'Frame linking is off for other or unloaded recordings.',
  'review':'Open this frame’s evidence ↗','candidate':'Candidates in this frame',
  'helpTitle':'Use the viewer',
  'help':['Drag / scroll to pan and zoom; drag in the 3D view to orbit.','Click an object, then “Show object properties” for its source fields.','Use Scene / Projection / Frame evidence inside the viewer. The website slider synchronizes the known scene with the evidence page.'],
  'limitsTitle':'What is being shown',
  'limits':'Default: actual delivered geometry, re-exported for browser inspection. Purple outlines are occupancy candidates, not confirmed lane closures. The projection is not a photograph; the ego path records what happened.',
  'localNote':'Open the full scene-0980 RRD from your Mac without uploading it. Local files use an independent timeline. Very large files may exceed browser memory. Rerun’s native controls are in English.',
  'legend':['Road objects','Vehicles','People','Other classes','Occupancy candidates','Recorded ego path'],
  'auditTitle':'Runtime audit · 2026-11-05','auditIntro':'Four reproduced issues were fixed: two existing core / packaging bugs, and two integration issues found while adding Rerun. Data gaps remain separate below.',
  'report':'Download audit report (Word)','dataReport':'Audit data (JSON)','fixed':'Fixed',
 },
 'zh':{
  'title':'亲手查看场景，逐帧追溯证据。',
  'intro':'把成员交付的 nuScenes 数据放进真正的 Rerun 查看器：地图几何、原始物体标注与逐帧证据。',
  'reference':'参考场景：41 帧 · 2,278 条标注 · 真实基础地图修改 0 处',
  'source':'成员交付几何 + nuScenes 原标注 · 重新封装为 RRD · 没有相机照片',
  'default':'加载 scene-0980','overlay':'原始增量叠加','open':'打开本地 RRD','unload':'卸载 / 取消','inspect':'显示物体属性','changes':'查看地图修改 ↗','drive':'类别标注与原视频 ↗',
  'loading':'正在启动 Rerun… 首次需下载约 28 MB 查看器，再加载场景录制。',
  'ready':'场景已加载。可以拖动、缩放、选择物体，或操作下方原生时间轴。',
  'overlaySource':'用户原始交付的增量叠加录制 · 不含相机图片及完整基础录制',
  'overlayReady':'原始叠加已加载。这里只有证据叠加内容，不是完整相机场景；时间轴独立。',
  'local':'本地文件','localReady':'本地文件已加载。其场景和时间未与当前评估核对，使用独立时间轴。',
  'reading':'正在读取本地录制…','unloaded':'录制已卸载。可重新加载场景，或选择本地文件。','none':'当前没有加载录制。',
  'failed':'无法打开录制。已有场景、视频及证据页面仍可使用。',
  'timeout':'60 秒内没有完成录制解析。请检查文件、网络或浏览器内存后重试。',
  'badFile':'这不是可读取的 RRF2 录制，请选择完整的 .rrd 文件。',
  'version':'此查看器对应 Rerun 0.22.1；所选文件的头部版本为',
  'frame':'帧','synced':'场景帧 ↔ 网页证据同步','independent':'其他录制或未加载时，关闭帧联动。',
  'review':'打开此帧的证据 ↗','candidate':'本帧候选',
  'helpTitle':'怎样操作',
  'help':['拖动 / 滚轮平移与缩放；在三维视图中拖动可旋转观察。','点击物体，再点“显示物体属性”，查看来源字段。','查看器内可切换 Scene / Projection / Frame evidence。网页滑块联动已核对场景的帧与证据页。'],
  'limitsTitle':'这里展示的是什么',
  'limits':'默认显示成员交付的真实几何，重新封装后供浏览器查看。紫色轮廓是占用候选，不代表已确认封路。前视投影不是实拍，实际轨迹仅记录曾经走过的路线。',
  'localNote':'可直接打开 Mac 上的完整 scene-0980 RRD，无需上传。本地文件使用独立时间轴，大文件可能超过浏览器内存。Rerun 内置菜单仍为英文，部分中文可能受其字体限制；中文类别可在“类别标注与原视频”中完整查看。',
  'legend':['道路物体','车辆','人员','其他类别','占用候选','实际行驶轨迹'],
  'auditTitle':'运行问题检查 · 2026-11-05','auditIntro':'已修复四项复现问题：两项已有核心 / 打包问题，以及本次接入 Rerun 时发现的两项集成问题。数据与证据缺口单独列在下方。',
  'report':'下载问题检查报告（Word）','dataReport':'检查数据（JSON）','fixed':'已修复',
 }
}


def page_fragment(t):
    def button(id,key):return f'<button id="{id}" type="button">{escape(t[key])}</button>'
    return f'''<section id="rerun-page" hidden>
 <div class="section-title"><div><p class="kicker">RERUN / NUScenes · SCENE-0980</p><h1>{escape(t['title'])}</h1><p class="description">{escape(t['intro'])}</p></div></div>
 <div class="rr-badges"><span>{escape(t['reference'])}</span><span>Rerun 0.22.1 · WebGL</span></div>
 <section class="panel"><div class="rr-toolbar">{button('rerun-default','default')}{button('rerun-overlay','overlay')}{button('rerun-open','open')}{button('rerun-inspect','inspect')}{button('rerun-unload','unload')}<input type="file" id="rerun-file" accept=".rrd" hidden></div>
 <div class="rr-status" id="rerun-status" role="status" aria-live="polite">{escape(t['none'])}</div>
 <div class="rr-native"><div id="rerun-host" class="rr-host" aria-label="Rerun"></div></div>
 <div class="rr-source" id="rerun-source">{escape(t['source'])}</div>
 <div class="rr-sync"><span id="rerun-frame-text"></span><input id="rerun-frame" aria-label="{escape(t['frame'])}" type="range" min="0" max="40" value="0" disabled><small id="rerun-sync-note"></small></div>
 <div id="rerun-context" class="rr-context"><small>{escape(t['candidate'])}</small><div id="rerun-candidates" class="evidence-chips"></div><a id="rerun-review" class="text-link" href="review/index.html">{escape(t['review'])}</a></div></section>
 <div class="rr-legend">{''.join(f'<span><i style="background:{color}"></i>{escape(label)}</span>' for color,label in zip(['#ff958c','#8fb9e7','#e3c986','#b9c2cd','#c7a6fb','#47cab2'],t['legend']))}</div>
 <div class="rr-help"><article class="panel"><h3>{escape(t['helpTitle'])}</h3><ul>{''.join('<li>'+escape(x)+'</li>' for x in t['help'])}</ul><div class="rr-toolbar">{button('rerun-to-drive','drive')}{button('rerun-to-changes','changes')}</div></article><article class="panel"><h3>{escape(t['limitsTitle'])}</h3><p>{escape(t['limits'])}</p><p>{escape(t['localNote'])}</p></article></div>
 </section>'''


def audit_fragment(t,audit,language):
    parts=[]
    for issue in audit['bugs']:
        x=issue[language]
        parts.append(f'<details><summary>{issue["id"]} · {escape(t["fixed"])} · {escape(x["title"])}</summary><p>{escape(x["finding"])}</p><p>{escape(x["fix"])}</p></details>')
    return f'<section class="panel rr-audit"><h2>{escape(t["auditTitle"])}</h2><p>{escape(t["auditIntro"])}</p>'+''.join(parts)+f'<a href="assets/AIE1903_runtime_audit.docx" download>{escape(t["report"])}</a><a href="assets/runtime_audit.json" download>{escape(t["dataReport"])}</a></section>'


def report(audit,language,path,recordings):
    from docx import Document
    from docx.shared import Inches,Pt
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    zh=language=='zh';d=Document();sec=d.sections[0];sec.top_margin=sec.bottom_margin=Inches(.65)
    normal=d.styles['Normal'];normal.font.name='Calibri';normal.font.size=Pt(10)
    normal.element.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'Microsoft YaHei')
    d.add_heading('AIE1903 · 运行检查与 Rerun 接入' if zh else 'AIE1903 · Runtime Audit and Rerun Integration',0)
    d.add_paragraph('2026-11-05 · nuScenes / scene-0980')
    d.add_paragraph('范围：地图维护引擎、安装包和网站接入。不是重跑成员模型后的全项目准确率认证。' if zh else audit['scope'])
    d.add_heading('1. 已复现并修复的问题' if zh else '1. Reproduced and fixed issues',1)
    for issue in audit['bugs']:
        x=issue[language];d.add_heading(issue['id']+' · '+x['title'],2)
        for label,key in ([('现象','finding'),('处理','fix'),('复现','reproduce')] if zh else [('Finding','finding'),('Fix','fix'),('Reproduction','reproduce')]):
            p=d.add_paragraph();p.add_run(label+': ').bold=True;p.add_run(x[key])
    d.add_heading('2. 尚未解决的数据与证据缺口' if zh else '2. Open data and evidence gaps',1)
    for x in audit['limits'][language]:d.add_paragraph(x,style='List Bullet')
    d.add_heading('3. 使用与来源核对' if zh else '3. Usage and provenance',1)
    d.add_paragraph('打开英文或中文网站的 Rerun 页签，可拖动、缩放、查看原始字段、切换逐帧证据；“地图修改”中保留真实结果与独立受控试验。打开本地文件不会上传，也不会据此改变现有评估。' if zh else 'Open the Rerun tab on either website to pan, zoom, inspect source fields and follow frame evidence. Map Changes retains real results and the separate controlled experiment. Opening a local file uploads nothing and does not alter evaluation results.')
    d.add_paragraph('https://yugongshit.github.io/AIE1903-map-update66/'+language+'/index.html#rerun')
    for lang,item in recordings['recordings'].items():
        d.add_paragraph(lang+' · '+item['file']+'\nSHA256: '+item['sha256'])
    d.add_paragraph('原始标注 SHA256: '+recordings['object_source_sha256'] if zh else 'Source annotation SHA256: '+recordings['object_source_sha256'])
    d.add_heading('4. 验证范围' if zh else '4. Validation scope',1)
    d.add_paragraph('检查包括：核心回归、安装包隔离导入、最终 RRD 逐项回读、英文 / 中文交互、真实 WebGL 加载、时间联动、本地文件错误与取消、原视频及受控回退。具体通过项与截图随发布保存；单一云端 Chromium 测试不能覆盖所有 Mac 和浏览器，也未做实车安全验证。' if zh else 'Checks cover core regressions, isolated wheel import, saved-RRD readback, bilingual interaction, real WebGL loading, timeline linking, local-file errors and cancellation, the original video and controlled rollback. Check results and screenshots accompany the release. One cloud Chromium environment does not cover every Mac or browser, and no real-vehicle safety validation is claimed.')
    d.save(path)


def build(source,recordings,vendor,overlay,output):
    if output.exists():raise ValueError('Choose a new output directory')
    if not (source/'bilingual_checks.json').exists():raise ValueError('Use a checked bilingual build')
    manifest=json.loads((recordings/'manifest.json').read_text())
    assert all(x['annotation_rows_verified_from_saved_file']==2278 for x in manifest['recordings'].values())
    shutil.copytree(source,output)
    assets=output/'assets';native=assets/'vendor/rerun-0.22.1';native.mkdir(parents=True)
    for name in ['index.js','index.js.map','re_viewer.js','re_viewer_bg.wasm','package.json','README.md','LICENSE-MIT','LICENSE-APACHE']:
        shutil.copy2(vendor/name,native/name)
    js=(native/'index.js').read_text();assert js.count('import("./re_viewer")')==1
    (native/'index.js').write_text(js.replace('import("./re_viewer")','import("./re_viewer.js")'))
    vendor_manifest={'package':'@rerun-io/web-viewer','version':'0.22.1','source':URL,'npm_sha512':INTEGRITY,'patch':'index.js: add .js to the re_viewer dynamic import for static HTTP hosting','files':{p.name:sha256(p.read_bytes()).hexdigest() for p in native.iterdir()}}
    (native/'provenance.json').write_text(json.dumps(vendor_manifest,indent=2)+'\n')
    rec=assets/'rerun';shutil.copytree(recordings,rec);shutil.copy2(overlay,rec/'original-overlay.rrd')
    for name in ['rerun_bridge.js','rerun_bridge.css']:shutil.copy2(ROOT/'map_update'/name,assets/name)
    audit=json.loads((ROOT/'docs/runtime-audit-20261105.json').read_text())
    for language,t in TEXT.items():
        folder=output/language;page=(folder/'index.html').read_text()
        page=page.replace('</head>','<link rel="stylesheet" href="../assets/rerun_bridge.css"><script>window.INITIAL_STUDIO_HASH=location.hash;</script></head>',1)
        page=page.replace('<main>','<main>'+page_fragment(t),1)
        page=page.replace('<button data-page="drive"','<button id="rerun-nav" data-page="rerun">Rerun</button><button data-page="drive"',1)
        page=page.replace('<section id="issues-page" hidden>','<section id="issues-page" hidden>'+audit_fragment(t,audit,language),1)
        config={'text':t,'viewer_module':'../assets/vendor/rerun-0.22.1/index.js','recording':'../assets/rerun/'+manifest['recordings'][language]['file'],'recording_id':manifest['recordings'][language]['recording_id'],'overlay':'../assets/rerun/original-overlay.rrd'}
        injection='<script type="application/json" id="rerun-config">'+json.dumps(config,ensure_ascii=False).replace('</',r'<\/')+'</script><script src="../assets/rerun_bridge.js"></script>'
        page=page.replace('</body>',injection+'</body>')
        (folder/'index.html').write_text(page)
        localized={'date':audit['date'],'bugs':[{'id':x['id'],'status':x['status'],**x[language]} for x in audit['bugs']],'limits':audit['limits'][language]}
        (folder/'assets/runtime_audit.json').write_text(json.dumps(localized,ensure_ascii=False,indent=2)+'\n')
        report(audit,language,folder/'assets/AIE1903_runtime_audit.docx',manifest)
    version=json.loads((output/'version.json').read_text());version.update(schema='rerun-evidence-studio-1.0',release_date='2026-11-05',rerun_version='0.22.1',real_base_map_writes=0,rerun_recordings=manifest['recordings'])
    (output/'version.json').write_text(json.dumps(version,ensure_ascii=False,indent=2)+'\n')
    # The old checks remain labelled as the input baseline until this release is checked.
    (output/'baseline_bilingual_checks.json').write_bytes((output/'bilingual_checks.json').read_bytes())
    (output/'bilingual_checks.json').unlink()
    print(json.dumps({'output':str(output),'recordings':manifest['recordings']},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for k in ['source','recordings','vendor','overlay','out']:p.add_argument('--'+k,type=Path,required=True)
    a=p.parse_args();build(a.source,a.recordings,a.vendor,a.overlay,a.out)
