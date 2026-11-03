"""Calculate exact coverage ratios from independently assessed target lengths."""

import argparse
import csv
import math
from pathlib import Path


LENGTH_FIELDS = ('target_length_m', 'in_fov_length_m', 'occluded_length_m', 'effective_length_m')


def add_coverage_rates(row):
    lengths = [float(row[key]) if str(row[key]).strip() else None for key in LENGTH_FIELDS]
    target, in_fov, occluded, effective = lengths
    if any(value is not None and (not math.isfinite(value) or value < 0) for value in lengths):
        raise ValueError('Coverage lengths must be finite and nonnegative.')
    if all(value is None for value in lengths):
        return dict(row, field_of_view_ratio=None, occlusion_ratio=None, effective_coverage=None,
                    coverage_status='UNKNOWN')
    if target is None or target <= 0 or not row['evidence_reference'].strip():
        raise ValueError('Provide a positive target length and evidence reference for measured lengths.')
    if any(value is not None and value > target for value in (in_fov, occluded, effective)):
        raise ValueError('A component length cannot exceed the target length.')
    if in_fov is not None:
        if occluded is not None and occluded > in_fov:
            raise ValueError('Occluded length cannot exceed in-view length.')
        if effective is not None and effective + (occluded or 0) > in_fov + 1e-9:
            raise ValueError('Effective length cannot exceed the in-view unoccluded length.')
    return dict(row, field_of_view_ratio=None if in_fov is None else in_fov / target,
                occlusion_ratio=occluded / in_fov if in_fov and occluded is not None else None,
                effective_coverage=None if effective is None else effective / target,
                coverage_status='PARTIAL' if effective is None else 'EFFECTIVE_COVERAGE_MEASURED')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_csv', type=Path)
    parser.add_argument('output_csv', type=Path)
    args = parser.parse_args()
    with args.input_csv.open(encoding='utf-8-sig', newline='') as handle:
        rows = [add_coverage_rates(row) for row in csv.DictReader(handle)]
    if not rows:
        raise ValueError('The input CSV must contain at least one target frame.')
    keys = {(row['record_id'], row['frame'], row['map_version']) for row in rows}
    if len(keys) != len(rows):
        raise ValueError('Expected one row per record, frame and map version.')
    with args.output_csv.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == '__main__':
    main()
