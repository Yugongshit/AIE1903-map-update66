"""Publish two independent language routes from one verified scene-studio build."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import os

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from map_update.website_languages import localize_html, localized_issues


def english_report(issues, destination):
    from docx import Document
    from docx.shared import Inches, Pt
    doc=Document();section=doc.sections[0]
    section.top_margin=section.bottom_margin=Inches(.65)
    doc.styles['Normal'].font.name='Arial';doc.styles['Normal'].font.size=Pt(10)
    doc.add_heading('AIE1903 | Runtime Issues and Next Steps',0)
    doc.add_paragraph('2026-11-04 · nuScenes / scene-0980 · Argoverse pending review')
    doc.add_heading('Current evidence',1)
    doc.add_paragraph('41 frames; one visit of about 19.85 seconds; 29 change candidates, including 23 occupancy candidates and six marking discrepancies; seven M3 review units. Real base-map updates: 0. All seven M3 units remain Insufficient Evidence.')
    doc.add_paragraph('Source object labels, dimensions and poses are reused from delivered nuScenes annotations. The English website displays original category strings; the Chinese website provides translated display names. This is a visualization change, not a new detection or annotation run.')
    doc.add_paragraph('Camera metadata and calibration are available, but no matching camera JPGs are currently readable. The front view is a geometry reconstruction. The supplied perception video is preserved and uses an independent timeline. Recorded ego trajectories are not planned routes.')
    doc.add_heading('Issues, owners and acceptance criteria',1)
    for issue in issues:
        doc.add_heading(issue['id']+' · '+issue['title'],2)
        doc.add_paragraph(issue['priority']+' | '+issue['status']+' | '+issue['owner'])
        for label,key in [('Finding','finding'),('Impact','impact'),('Next action','next'),('Acceptance','acceptance'),('Source','source')]:
            p=doc.add_paragraph();p.add_run(label+': ').bold=True;p.add_run(issue[key])
    doc.add_heading('Scope of the evaluation',1)
    doc.add_paragraph('Controlled edits operate on an isolated source-map copy. Offsets of 0.6 / 0.8 / 1.2 m, visits at 0 / 12 / 26 hours and 95% valid coverage are simulated. They verify engine writes, versions and rollback, not actual long-term road changes.')
    doc.add_paragraph('Conditional F1 is 94.1%, decision coverage 28.6%, and recall over all positives 22.2% on 126 same-scene variants. The 217 real-scene label rows lack independent ground truth; real accuracy remains unknown. Planning and cross-vehicle sharing are not demonstrated.')
    doc.add_heading('Decisions for the next team meeting',1)
    for text in ['Review LD003 / LD004 and retain false-detection or alignment findings.', 'Assign owners and dates for camera media, valid coverage, target matching and independent labels.', 'Confirm independent revisits; otherwise limit real-scene conclusions to candidates.', 'Agree on planner inputs, decision time, map version, route geometry and explicit failure outputs.']:
        doc.add_paragraph(text,style='List Bullet')
    doc.save(destination)


def build(source,output):
    source=Path(source).resolve();output=Path(output).resolve()
    if output.exists():raise ValueError('Choose a fresh output directory')
    if not (source/'object_label_checks.json').exists():raise ValueError('A checked semantic studio build is required')
    output.parent.mkdir(parents=True,exist_ok=True)
    staging=Path(tempfile.mkdtemp(prefix='.bilingual-',dir=output.parent))
    try:
        shutil.copytree(source/'assets',staging/'assets')
        original=(source/'index.html').read_text()
        original_review=(source/'review/index.html').read_text()
        payload=json.loads(re.search(r'<script type="application/json" id="studio-data">(.*?)</script>',original,re.S)[1])
        for language in ('en','zh'):
            folder=staging/language;folder.mkdir();(folder/'review').mkdir();(folder/'assets').mkdir()
            (folder/'index.html').write_text(localize_html(original,language))
            (folder/'review/index.html').write_text(localize_html(original_review,language,review=True))
            issues=localized_issues(payload['issues'],language)
            (folder/'assets/issues.json').write_text(json.dumps(issues,ensure_ascii=False,indent=2)+'\n')
            if language=='en':english_report(issues,folder/'assets/AIE1903_issues_and_next_steps.docx')
            else:shutil.copy2(source/'assets/AIE1903_issues_and_next_steps.docx',folder/'assets/AIE1903_issues_and_next_steps.docx')
            (folder/'assets/issues.md').write_text('\n\n'.join(issue['id']+' '+issue['title']+'\n\n'+issue['finding'] for issue in issues))
        # Original shared evidence URLs still work; the default entry is English.
        (staging/'index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>AIE1903 Evidence Studio</title><p><a href="en/index.html">English website</a> · <a href="zh/index.html">Chinese website</a></p><script>location.replace("en/index.html"+location.search+location.hash)</script></html>')
        shutil.copytree(source/'review',staging/'review')
        (staging/'review/index.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>AIE1903 Evidence Review</title><p><a href="../en/review/index.html">Open evidence review</a></p><script>location.replace("../en/review/index.html"+location.search+location.hash)</script></html>')
        manifest=json.loads((source/'version.json').read_text())
        manifest.update(schema='bilingual-evidence-studio-1.0',default_language='en',languages=['en','zh'],routes={'en':'en/index.html','zh':'zh/index.html'},source_category_policy='English: exact original category; Chinese: translated display name; source identity and geometry unchanged')
        manifest['shared_source_sha256']=sha256(json.dumps({key:payload[key] for key in ('real','camera','write_experiment')},sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        (staging/'version.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n');(staging/'.nojekyll').touch()
        os.rename(staging,output)
    finally:
        if staging.exists():shutil.rmtree(staging)
    print(json.dumps({'output':str(output),'languages':['en','zh'],'source_data':'shared and unchanged'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--source',required=True,type=Path);parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args();build(args.source,args.out)
