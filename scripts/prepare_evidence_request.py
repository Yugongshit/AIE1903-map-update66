"""Build a validated submission and retain its explicit coordinate/time transform."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from map_update.evidence_adapter import adapt_events
from map_update.evidence_intake import prepare_evidence, require_identity

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--input',type=Path,required=True,help='Source events JSON array')
parser.add_argument('--transform',type=Path,required=True)
parser.add_argument('--observation',type=Path,required=True)
parser.add_argument('--submission-id',required=True)
parser.add_argument('--out',type=Path,required=True)
args=parser.parse_args()
raw=args.input.read_bytes()
source_hash=sha256(raw).hexdigest()
transform=json.loads(args.transform.read_text())
observation=json.loads(args.observation.read_text())
require_identity(args.submission_id,'submission_id')
events=adapt_events(json.loads(raw),transform,source_hash)
prepare_evidence({},events,cutoff_ns=round(observation['simulation_time_s']*1_000_000_000),
                 coordinate_frame=observation['coordinate_frame'])
request=dict(session_id=observation['session_id'],scene_id=observation['scene_id'],
             map_version=observation['map_version'],state_version=observation['state_version'],
             observation_step=observation['step'],time_basis='simulation_time_ns',
             submission_id=args.submission_id,events=events)
args.out.mkdir(parents=True,exist_ok=False)
(args.out/'request.json').write_text(json.dumps(request,ensure_ascii=False,indent=2)+'\n')
(args.out/'adaptation.json').write_text(json.dumps(dict(source_sha256=source_hash,transform=transform),
                                                    ensure_ascii=False,indent=2)+'\n')
print(args.out/'request.json')
