"""Standard-library example loader. Place in delivery root as read_delivery.py."""
import json
from pathlib import Path

def read_table(package,name):
    with (Path(package)/'tables'/f'{name}.jsonl').open(encoding='utf-8') as f:
        for line in f:yield json.loads(line)

def load(package):
    package=Path(package)
    views={v['view_id']:v for v in read_table(package,'views')}
    matches={m['observation_id']:m for m in read_table(package,'matches')}
    for observation in read_table(package,'observations'):
        yield observation,views[observation['view_id']],matches[observation['observation_id']]

def resolve_source(reference,source_root):
    """source_root is the transferred Argoverse workspace root, not package root."""
    path=Path(source_root)/Path(reference)
    if not path.is_file():raise FileNotFoundError(f'External source file not available: {path}')
    return path

if __name__=='__main__':
    package=Path(__file__).resolve().parent
    count=0;projected=0
    for observation,view,match in load(package):
        count+=1
        if observation['geometry_city'] is not None:projected+=1
    print({'observations':count,'projected':projected})
