"""Extend a checked Rerun release with map decisions, provenance and evaluation."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from map_update.decision_data import build_decision_data


def extract(document):
    return json.loads(re.search(r'id="studio-data">(.*?)</script>',document,re.S)[1])


def build(source,output,cases,comparison):
    if output.exists():raise ValueError('Choose a new output directory')
    for name in ['rerun_checks.json','bilingual_checks.json']:
        if not (source/name).exists():raise ValueError('A checked Rerun release is required: '+name)
    en=extract((source/'en/index.html').read_text());zh=extract((source/'zh/index.html').read_text())
    for key in ['real','camera','write_experiment','controlled_evaluation']:
        if en[key]!=zh[key]:raise ValueError('Language source mismatch: '+key)
    data=build_decision_data(en,cases,comparison,ROOT/'external/perception_delivery')
    shutil.copytree(source,output)
    for name in ['decision_studio.css','decision_studio.js','rerun_bridge.js']:shutil.copy2(ROOT/'map_update'/name,output/'assets'/name)
    asset_revision=sha256(b''.join((output/'assets'/name).read_bytes()
                                  for name in ['decision_studio.css','decision_studio.js'])).hexdigest()[:12]
    assets=output/'assets/decisions';assets.mkdir()
    for path in [cases,comparison]:shutil.copy2(path,assets/path.name)
    (assets/'presentation_data.json').write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    automatic=data['automatic_decisions']
    for name,value in [('automatic_decisions',automatic),('recorded_event_layer',automatic['event_layer']),
                       ('observed_difference_layer',automatic['difference_layer'])]:
        (assets/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    template=(ROOT/'map_update/decision_studio.html').read_text()
    serialized=json.dumps(data,ensure_ascii=False,allow_nan=False).replace('<',r'\u003c').replace('>',r'\u003e').replace('&',r'\u0026')
    for language in ['en','zh']:
        path=output/language/'index.html';page=path.read_text()
        if 'id="decisions-page"' in page:raise ValueError('Source already contains decision studio')
        page=page.replace('</head>',f'<link rel="stylesheet" href="../assets/decision_studio.css?v={asset_revision}"></head>',1)
        page=page.replace('<main>','<main>'+template,1)
        labels=['01 Change decisions','02 Evaluation'] if language=='en' else ['01 地图决策','02 评估检查']
        nav=f'<button id="ds-nav" data-page="decisions">{labels[0]}</button><button id="de-nav" data-page="evaluation">{labels[1]}</button>'
        page=page.replace('<button id="rerun-nav"',nav+'<button id="rerun-nav"',1)
        page=page.replace('<button data-page="changes">','<button data-page="changes" class="ds-legacy-nav">',1)
        page=page.replace('<a href="review/index.html">','<a href="review/index.html" class="ds-legacy-nav">',1)
        page=page.replace('01 <span>Scene view</span>','<span>Source scene</span>').replace('01 <span>场景驾驶</span>','<span>原始场景</span>')
        page=page.replace('03 <span>Issues</span>','<span>Issues</span>').replace('03 <span>问题清单</span>','<span>问题清单</span>')
        page=page.replace('</body>','<script type="application/json" id="decision-data">'+serialized+f'</script><script src="../assets/decision_studio.js?v={asset_revision}"></script></body>')
        path.write_text(page)
        for key in ['real','camera','write_experiment','controlled_evaluation']:
            assert extract(page)[key]==en[key],key
    for name in ['rerun_checks.json','bilingual_checks.json','runtime_validation.json',
                 'release_validation.json','wheel_checks.json']:
        if (output/name).exists():(output/name).rename(output/('input_'+name))
    # The old deployment record belongs to the input release, not this build.
    if (output/'deployment_check.json').exists():(output/'deployment_check.json').rename(output/'input_deployment_check.json')
    manifest=json.loads((output/'version.json').read_text());manifest.update(schema='decision-evidence-studio-2.0',release_date='2026-11-08',default_workspace='decisions',new_workspaces=['decisions','evaluation'],controlled_case_rows=len(data['controlled_cases']),distance_preview_scope=data['semantics'],automatic_processing=automatic['summary'],manual_review_required=False)
    manifest.pop('source_commit',None)
    manifest['unchanged_evidence_sha256']=sha256(json.dumps({key:en[key] for key in ['real','camera','write_experiment','controlled_evaluation']},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    (output/'version.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'output':str(output),'candidates':len(en['real']['candidates']),'controlled_rows':len(data['controlled_cases']),'source_geometry':'unchanged'},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ['source','out','cases','comparison']:p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();build(a.source,a.out,a.cases,a.comparison)
