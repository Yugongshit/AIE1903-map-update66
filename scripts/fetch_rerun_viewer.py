"""Download the exact, integrity-checked official viewer used by delivered RRDs."""
import argparse
import base64
import hashlib
import io
from pathlib import Path
import tarfile
import urllib.request

VERSION='0.22.1'
URL=f'https://registry.npmjs.org/@rerun-io/web-viewer/-/web-viewer-{VERSION}.tgz'
INTEGRITY='SnonQpLmkukZ3IqfrLYpU1SupI+Z/YRz5IhWYjtpuUTmev//V8VKVUjFh+Xi/lkQwxKzqMmGlgNSyrxZcPMqJg=='


def fetch(output):
    raw=urllib.request.urlopen(URL,timeout=90).read()
    if base64.b64encode(hashlib.sha512(raw).digest()).decode()!=INTEGRITY:
        raise ValueError('npm package integrity mismatch')
    output.mkdir(parents=True,exist_ok=False)
    names=['index.js','index.js.map','re_viewer.js','re_viewer_bg.wasm','package.json','README.md']
    with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
        for name in names:(output/name).write_bytes(archive.extractfile('package/'+name).read())
    for name in ['LICENSE-MIT','LICENSE-APACHE']:
        (output/name).write_bytes(urllib.request.urlopen(f'https://raw.githubusercontent.com/rerun-io/rerun/{VERSION}/{name}',timeout=30).read())
    print('Verified @rerun-io/web-viewer '+VERSION+' -> '+str(output))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);fetch(p.parse_args().output)
