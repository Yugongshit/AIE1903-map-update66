"""Reproduce the supplied M3 scripts from the five real CSV inputs they read.

The staging ZIP is an input transport, not a reconstruction of the original
delivery archive (which also included images). No delivered source is changed.
"""

import argparse
import csv
from hashlib import sha256
import json
from math import isclose
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from zipfile import ZipFile, ZIP_DEFLATED


def equal(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if a == b:
        return True
    if isinstance(a, bool) or isinstance(b, bool) or a is None or b is None:
        return False
    try:
        return isclose(float(a), float(b), rel_tol=1e-10, abs_tol=1e-10)
    except (TypeError, ValueError):
        return False


def content(path):
    if path.suffix == '.json':
        return json.loads(path.read_text(encoding='utf-8'))
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-root', required=True, type=Path)
    parser.add_argument('--m3-root', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    raw, m3, output = (p.resolve() for p in (args.raw_root, args.m3_root, args.out))
    if output.exists():
        parser.error('output exists; choose a new directory')
    names = ['frames.csv', 'scene0980_hdmap_divider_vertices.csv',
             'scene0980_sensor_hdmap_line_metrics.csv', 'scene0980_sensor_hdmap_point_distances.csv',
             'scene0980_discrepancy_candidates_v1.csv']
    inputs = []
    for name in names:
        matches = list((raw/'processed').rglob(name))
        if len(matches) != 1:
            raise ValueError(f'Expected exactly one source {name}')
        inputs.append(matches[0])
    map_file = raw/'maps/expansion/singapore-onenorth.json'
    scores = raw/'perception/outputs/stage3/scored_candidates.csv'
    source_files = inputs + [map_file, scores] + sorted(m3.glob('*.py'))
    before = {str(p): sha256(p.read_bytes()).hexdigest() for p in source_files}
    with tempfile.TemporaryDirectory(prefix='m3-real-reproduction-') as temp:
        archive = Path(temp)/'five_real_input_tables.zip'
        with ZipFile(archive, 'w', ZIP_DEFLATED) as handle:
            for path in inputs:
                handle.write(path, path.relative_to(raw).as_posix())
        completed = subprocess.run(
            [sys.executable, str(m3/'run_supplement.py'), str(archive), str(map_file), str(scores),
             '--output-dir', str(output)], cwd=temp,
            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}, capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError(completed.stderr)
    comparisons = []
    for source in sorted((m3/'results').iterdir()):
        if source.suffix not in {'.csv', '.json'}:
            continue
        generated = output/source.name
        comparisons.append({'file': source.name, 'exists': generated.exists(),
                            'content_equal': generated.exists() and equal(content(source), content(generated)),
                            'byte_identical': generated.exists() and source.read_bytes() == generated.read_bytes()})
    unchanged = all(sha256(Path(p).read_bytes()).hexdigest() == digest for p, digest in before.items())
    report = {'scope': 'Re-execution of delivered M3 scripts using five real CSVs; no camera inference rerun.',
              'staging_archive': 'Only the five required CSV inputs; not the original delivery ZIP.',
              'numeric_tolerance': {'relative': 1e-10, 'absolute': 1e-10},
              'input_sha256': before, 'sources_unchanged': unchanged,
              'comparisons': comparisons,
              'passed': unchanged and bool(comparisons) and all(c['content_equal'] for c in comparisons)}
    (output/'reproduction_report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (output/'run.log').write_text(completed.stdout+completed.stderr, encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'compared': len(comparisons),
                      'different': [c['file'] for c in comparisons if not c['content_equal']]}, ensure_ascii=False))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
