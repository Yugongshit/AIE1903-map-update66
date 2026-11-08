"""List, verify or restore the archived conversation's existing project files.

Uses only Python's standard library. Existing different files are never replaced.
"""
import argparse
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def relative_path(value):
    path = PurePosixPath(value)
    if not value or path.is_absolute() or '..' in path.parts or '\\' in value:
        raise ValueError('Unsafe archived path: ' + value)
    if path.as_posix() != value:
        raise ValueError('Non-canonical archived path: ' + value)
    return Path(*path.parts)


def digest_file(path):
    digest = sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def run(args):
    archive_root = args.archive_root.resolve()
    manifest = json.loads((archive_root / 'manifest.json').read_text())
    if manifest['schema'] != 'aie1903-session-archive-1.0':
        raise ValueError('Unsupported manifest schema')
    files = manifest['files']
    if len(files) != manifest['file_count'] or len({f['path'] for f in files}) != len(files):
        raise ValueError('Manifest count or duplicate path error')
    groups = defaultdict(lambda: [0, 0])
    for item in files:
        relative_path(item['path'])
        relative_path(item['archive'])
        groups[item['group']][0] += 1
        groups[item['group']][1] += item['bytes']
    if args.list:
        for group, (count, size) in sorted(groups.items()):
            print(f'{group}\t{count} files\t{size / 1e6:.2f} MB restored')
        return
    selected = files if not args.group else [
        f for f in files if any(f['group'] == group or f['group'].startswith(group.rstrip('/') + '/') for group in args.group)
    ]
    if not selected:
        raise ValueError('No matching group. Use --list to inspect group names.')
    archives = {a['file']: a for a in manifest['archives']}
    if len(archives) != manifest['archive_count']:
        raise ValueError('Archive count mismatch')
    wanted = defaultdict(list)
    for item in selected:
        wanted[item['archive']].append(item)
    all_members = defaultdict(set)
    for item in files:
        all_members[item['archive']].add(item['path'])

    # Validate every selected member before writing anything.
    checked = 0
    for name, items in sorted(wanted.items()):
        path = archive_root / relative_path(name)
        meta = archives[name]
        if path.stat().st_size != meta['bytes'] or digest_file(path) != meta['sha256']:
            raise ValueError('Archive checksum mismatch: ' + name)
        with zipfile.ZipFile(path) as archive:
            if len(archive.namelist()) != len(set(archive.namelist())) or set(archive.namelist()) != all_members[name]:
                raise ValueError('Unexpected archive members: ' + name)
            for item in items:
                info = archive.getinfo(item['path'])
                if info.file_size != item['bytes']:
                    raise ValueError('Member size mismatch: ' + item['path'])
                content = archive.read(info)
                if sha256(content).hexdigest() != item['sha256']:
                    raise ValueError('Member checksum mismatch: ' + item['path'])
                checked += 1
    if args.verify_only:
        print(json.dumps({'status': 'PASS', 'verified_files': checked, 'archives': len(wanted)}))
        return

    destination = args.destination.resolve()
    for item in selected:
        path = destination / relative_path(item['path'])
        if destination not in path.resolve().parents:
            raise ValueError('Destination escapes restore directory: ' + item['path'])
        for ancestor in [path, *path.parents]:
            if ancestor == destination:
                break
            if ancestor.is_symlink():
                raise ValueError('Refusing destination symlink: ' + str(ancestor))
        if path.exists() and (not path.is_file() or digest_file(path) != item['sha256']):
            raise ValueError('Existing different file, left unchanged: ' + str(path))
    restored = skipped = 0
    for name, items in sorted(wanted.items()):
        with zipfile.ZipFile(archive_root / relative_path(name)) as archive:
            for item in items:
                path = destination / relative_path(item['path'])
                if path.exists():
                    skipped += 1
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open('xb') as target:
                    target.write(archive.read(item['path']))
                path.chmod(item['mode'] & 0o777)
                restored += 1
    print(json.dumps({'status': 'PASS', 'verified_files': checked, 'restored_files': restored,
                      'identical_files_skipped': skipped, 'destination': str(destination)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive-root', type=Path, default=ROOT / 'archives/session-20261108')
    parser.add_argument('--list', action='store_true', help='List all available groups')
    parser.add_argument('--group', action='append', help='Restore an exact group or group prefix; repeatable')
    parser.add_argument('--verify-only', action='store_true', help='Check all selected hashes without restoring')
    parser.add_argument('--destination', type=Path, default=Path('restored_session'))
    run(parser.parse_args())
