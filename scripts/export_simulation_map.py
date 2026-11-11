"""Export the completed map-update result into a separate MetaDrive database."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.simulation_bridge import export_simulation_map


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-path', type=Path, required=True)
    parser.add_argument('--pipeline-result', type=Path, required=True)
    parser.add_argument('--patch', type=Path, help='Explicit scene-bound feature patch JSON')
    parser.add_argument('--state-source-root', type=Path,
                        help='Source data root containing processed/frames.csv and objects.csv; exports time-aligned state observations')
    parser.add_argument('--obstacle-mode', choices=['persistent', 'recorded'], default='persistent',
                        help='Keep barriers/cones at their first observed pose (default), or preserve recorded visibility')
    parser.add_argument('--out', type=Path, required=True, help='New directory; source data is preserved')
    args = parser.parse_args()
    try:
        print(export_simulation_map(args.database_path, args.pipeline_result, args.out, args.patch,
                                    obstacle_mode=args.obstacle_mode, state_source_root=args.state_source_root))
    except (ValueError, FileExistsError, FileNotFoundError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
