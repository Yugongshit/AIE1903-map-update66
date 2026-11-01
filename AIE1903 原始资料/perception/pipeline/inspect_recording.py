"""Inspect the existing Rerun recording before adding any visualization layers."""
import rerun_env
import json
from pathlib import Path
import rerun as rr
import rerun.dataframe as rdf

if __name__ == '__main__':
    p = rerun_env.ROOT / 'Nuscnenes已处理/processed/processed/scene-0980_discrepancy_full_v2.rrd'
    rec = rdf.load_recording(str(p))
    print('rerun', rr.__version__)
    print('recording', rec)
    print('recording_methods', [n for n in dir(rec) if not n.startswith('_')])
    schema = rec.schema()
    schema_text = '\n'.join([repr(c) for c in schema.index_columns()] + [repr(c) for c in schema.component_columns()])
    print('schema', schema_text)
    out = rerun_env.ROOT / 'outputs/visualization'
    out.mkdir(exist_ok=True)
    (out / 'source_schema.txt').write_text(schema_text, encoding='utf-8')
