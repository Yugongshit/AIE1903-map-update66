"""Build a navigable scene studio, honest write experiment and proposal issue report."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.datasets import require_active, run_paths
from map_update.delivery import read_csv
from map_update.evaluation import build_dashboard, dashboard_data, evaluate_controlled
from map_update.reliability import integrate_m3
from map_update.studio import build_studio_data


def write_report(payload, directory):
    from docx import Document
    from docx.shared import Inches, Pt, RGBColor
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    issues = payload['issues']
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(.68)
    section.left_margin = section.right_margin = Inches(.8)
    for style_name in ['Normal', 'Title', 'Heading 1', 'Heading 2', 'Heading 3']:
        style = doc.styles[style_name]
        style.font.name = 'Arial'
        style.element.rPr.rFonts.set(qn('w:eastAsia'), 'Microsoft YaHei')
        style.font.color.rgb = RGBColor.from_string('193249')
    doc.styles['Normal'].font.size = Pt(10)
    doc.styles['Normal'].paragraph_format.space_after = Pt(6)
    doc.styles['Normal'].paragraph_format.line_spacing = 1.2
    doc.add_heading('AIE1903｜运行问题与周末讨论清单', 0)
    doc.add_paragraph('2026-11-04 · nuScenes / scene-0980 · Argoverse 暂停，待审核', style='Subtitle')
    doc.add_paragraph('本清单区分“程序能跑通”与“证据足够支持改地图”。依据当前仓库、已交付结果、原 PPT 和这次可视化接入检查整理；不把未找到的交付等同于成员没有开展工作。')
    doc.add_heading('1. 当前实际完成到哪里', 1)
    for line in [
        '真实场景：41 帧、29 个变化候选、23 个占用事件候选、7 个 M3 复核对象。真实基础地图修改为 0。',
        '可靠性模块已接入；七个真实对象仍为证据不足。旧说明中的“可靠性尚未收到”已经过时。',
        '新版展示：可操作的前视几何重建、原始感知录屏、鸟瞰轨迹联动、真实事件层对照，以及真实底图独立副本上的受控写入、版本与回退。',
        '前视图使用相机时间的位姿和内外参。当前没有可读的车前照片；几何重建不是实景照片。实际行驶轨迹不是规划建议。',
        '受控写入试验的人为偏移、95% 有效覆盖率及 0 / 12 / 26 小时三次访问都明确标注；不增加真实访问数，不改写原数据。',
    ]:
        doc.add_paragraph(line, style='List Bullet')
    doc.add_heading('2. 必须先解决的六项问题', 1)
    table = doc.add_table(rows=1, cols=3);table.style = 'Light Shading Accent 1'
    for cell, text in zip(table.rows[0].cells, ['编号 / 问题', '为什么阻塞', '主要协作方']):cell.text=text
    for issue in issues:
        if issue['priority']!='P0':continue
        cells=table.add_row().cells
        for cell,text in zip(cells,[issue['id']+' '+issue['title'],issue['impact'],issue['owner']]):cell.text=text
    doc.add_heading('3. 问题、补交内容与验收标准', 1)
    for issue in issues:
        doc.add_heading(issue['id']+' · '+issue['title'], 2)
        doc.add_paragraph(issue['priority']+'｜'+issue['status']+'｜'+issue['owner'])
        for label,key in [('已核实', 'finding'),('影响', 'impact'),('下一步', 'next'),('验收', 'acceptance')]:
            p=doc.add_paragraph();p.add_run(label+'：').bold=True;p.add_run(issue[key])
        p=doc.add_paragraph('依据：'+issue['source']);p.runs[0].font.size=Pt(8)
    doc.add_heading('4. 跑通的部分与仍不能声称的结论', 1)
    doc.add_paragraph('已有感知下游规则和 M3 数值复现记录。原感知脚本的旧绝对路径由隔离运行器绕开；没有重跑 YOLOP 相机推理。当前构建的来源、模型 / 规则检查与浏览器测试另存 validation.json。测试通过不等于真实检测准确率。')
    doc.add_paragraph('条件 F1 94.1% 只针对质量门控已明确判定的样本；必须同时展示 28.6% 决策覆盖率和 22.2% 全正例召回。217 行真实标签仍为空，真实准确率保持未知。')
    doc.add_heading('5. PPT 承诺如何对齐', 1)
    for line in [
        '“前后地图、版本记录、回退”——本轮已补可操作的受控引擎展示；真实场景基础图保持不变。',
        '“长短期判断与分层写入”——真实数据目前只能保留占用事件候选与待确认；短片段不足以确认长期变化，也没有证明占用已解除。',
        '“实景叠加与建议路线”——照片接入和标定接口已准备，原图仍待可读交付；Planning 尚未接入，不能声称推荐路线或驾驶收益。',
        '“误更新、覆盖、几何误差与延迟评估”——代码机制可在受控试验检查；真实效果需独立标注与复访。',
    ]:doc.add_paragraph(line,style='List Bullet')
    doc.add_heading('6. 周末讨论应形成的决定', 1)
    for line in [
        '选一个最小真实案例，优先核查 LD003 / LD004；允许结论是误检或投影问题。',
        '分配相机素材、覆盖率、目标匹配和独立标签的补交责任及日期。',
        '确认是否有真实独立复访。没有就缩窄真实实验结论，并单列受控改图机制。',
        '与 Planning 对齐起终点、决策时刻、地图版本、路线坐标、可行性与失败原因。',
    ]:doc.add_paragraph(line,style='List Number')
    doc.add_paragraph('建议的 proposal 表述：我们将展示有来源的地图差异和事件候选，验证受控条件下的版本化更新与回退，并在获得独立复访、有效覆盖、目标匹配和参考标注后评估真实地图维护效果。当前不宣称已完成真实长期改图、实景路线推荐或安全收益。')
    footer=section.footer.paragraphs[0];footer.add_run('AIE1903 · nuScenes 运行复核　|　')
    field=OxmlElement('w:fldSimple');field.set(qn('w:instr'),'PAGE');footer._p.append(field)
    doc.save(directory/'AIE1903_issues_and_next_steps.docx')
    md=['# AIE1903 运行问题与周末讨论清单','', '2026-11-04；当前仅运行 nuScenes / scene-0980，Argoverse 待审核。','']
    for issue in issues:
        md += ['## '+issue['id']+' '+issue['title'], '', issue['priority']+' · '+issue['status']+' · '+issue['owner'], '']
        for label,key in [('现象','finding'),('影响','impact'),('下一步','next'),('验收','acceptance'),('依据','source')]:md += [label+'：'+issue[key], '']
    (directory/'issues.md').write_text('\n'.join(md),encoding='utf-8')
    (directory/'issues.json').write_text(json.dumps(issues,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def build(output, paths, video=None):
    require_active('nuscenes')
    output=Path(output).resolve()
    if output.exists():raise ValueError('Output exists; choose a new directory')
    output.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.studio-',dir=output.parent))
    try:
        raw,delivery,m3=[Path(paths[key]) for key in ['raw_root','delivery_root','m3_root']]
        scene=integrate_m3(raw,delivery,m3/'results/real_scene_handoff.json')
        evaluation=evaluate_controlled(read_csv(m3/'results/controlled_case_decisions.csv'),read_csv(m3/'results/controlled_comparison.csv'))
        dashboard=dashboard_data(scene,evaluation)
        dashboard['provenance']=[{'dataset_id':'nuscenes','scene_id':scene['scene_id'],
                                  'file':p.name,'sha256':sha256(p.read_bytes()).hexdigest()}
                                 for p in [m3/'results/real_scene_handoff.json',m3/'results/controlled_case_decisions.csv',m3/'results/controlled_comparison.csv']]
        payload=build_studio_data(raw,scene,dashboard)
        assets=staging/'assets';assets.mkdir()
        if video:
            video=Path(video)
            if video.suffix.lower()!='.mp4' or not video.is_file():raise ValueError('Expected an existing perception MP4')
            shutil.copy2(video,assets/'scene0980-perception.mp4')
            payload['perception_video']={'path':'assets/scene0980-perception.mp4','source_name':video.name,
                                        'sha256':sha256(video.read_bytes()).hexdigest(),
                                        'role':'original supplied screen recording, no inferred camera frames or automatic timeline synchronization'}
        write_report(payload,assets)
        build_dashboard(raw,delivery,m3,staging/'review',workbench=True,dataset_id='nuscenes')
        review=staging/'review/index.html'
        review.write_text(review.read_text(encoding='utf-8').replace(
            'AIE1903 · EVALUATION & EVIDENCE</div>',
            'AIE1903 · EVALUATION & EVIDENCE <a href="../index.html" style="margin-left:12px;color:#327b85;letter-spacing:0">← 场景与地图维护</a></div>'),encoding='utf-8')
        package=ROOT/'map_update'
        serialized=json.dumps(payload,ensure_ascii=False,allow_nan=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
        template=(package/'studio.html').read_text(encoding='utf-8').replace('__STUDIO_CSS__',(package/'studio.css').read_text(encoding='utf-8')).replace('__STUDIO_JS__',(package/'studio.js').read_text(encoding='utf-8')).replace('__STUDIO_JSON__',serialized)
        (staging/'index.html').write_text(template,encoding='utf-8')
        (assets/'write_experiment.json').write_text(json.dumps(payload['write_experiment'],ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        manifest={'schema':'visual-studio-release-1.0','dataset_id':'nuscenes','scene_id':scene['scene_id'],
                  'camera_frames':len(payload['camera']['views']),'camera_images_available':payload['camera']['available_images'],
                  'original_video_included':bool(video),'real_map_updates':0,'controlled_variants':3,
                  'argoverse':'pending_review_not_executed','planning':'not_integrated','issues':len(payload['issues']),
                  'source_checks':len(scene['raw_validation']['checks']),'m3_checks':len(scene['m3']['validation']['checks']),
                  'camera_source_files':payload['camera']['source_files'],'source_map_sha256':scene['reference_map']['source_sha256'],
                  'evaluation_source_files':payload['provenance'],
                  'perception_video':payload['perception_video']}
        (staging/'version.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (staging/'.nojekyll').touch()
        os.rename(staging,output)
    finally:
        if staging.exists():shutil.rmtree(staging)
    print(json.dumps({'output':str(output),'frames':41,'photos_available':payload['camera']['available_images'],
                      'real_map_updates':0,'controlled_write_and_rollback':'verified','issues':len(payload['issues'])},ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--perception-video',type=Path)
    args=parser.parse_args()
    build(args.out,run_paths(ROOT,'nuscenes','studio-build'),args.perception_video)
