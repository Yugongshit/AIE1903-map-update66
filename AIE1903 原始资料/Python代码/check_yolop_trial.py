"""Validate trial artifacts, not model prediction accuracy."""
import json
from pathlib import Path
import numpy as np
from PIL import Image
root=Path(__file__).resolve().parent;out=root/'m2_yolop_trial'
rows=json.loads((out/'results.json').read_text(encoding='utf-8'));count=0
assert len(rows)==33 and len(list((out/'comparisons').glob('*.jpg')))==33
for row in rows:
    with Image.open(root/row['image_path']) as image:size=image.size
    for suffix in ['road','lane_raw','lane_filtered']:
        path=out/'masks'/f"{row['frame_index']:04d}_{row['camera']}_{suffix}.png"
        with Image.open(path) as mask:
            assert mask.size==size
            assert set(np.unique(np.asarray(mask)))<={0,255}
        count+=1
    assert row['inference_seconds']>0
report={'status':'PASS','images':len(rows),'masks_checked':count,'checks':['original_image_dimensions','binary_masks','comparison_count','positive_timing'],'note':'Artifact consistency only; not model precision/recall.'}
(out/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
