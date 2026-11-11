"""Export supplied Argoverse reference updates and same-scene measured ego poses."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from map_update.simulation_argoverse import export_argoverse_simulation


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pipeline-result', type=Path, required=True)
    parser.add_argument('--m2-package', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True, help='New directory containing original/updated ScenarioNet databases')
    parser.add_argument('--ego-dimensions', type=float, nargs=3, default=(4.5, 1.9, 1.5),
                        metavar=('LENGTH', 'WIDTH', 'HEIGHT'), help='Simulation configuration in metres; not measured source dimensions')
    args = parser.parse_args()
    try:
        print(export_argoverse_simulation(args.pipeline_result, args.m2_package, args.out,
                                         ego_dimensions=args.ego_dimensions))
    except (ValueError, FileExistsError, FileNotFoundError) as error:
        parser.exit(2, str(error) + '\n')


if __name__ == '__main__':
    main()
