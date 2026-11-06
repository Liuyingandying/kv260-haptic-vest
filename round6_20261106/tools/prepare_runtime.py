"""Host only: reproduce the exact sealed 33-file runtime in a NEW directory."""
from pathlib import Path, PurePosixPath
import argparse
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    manifest_path = ROOT / 'PACKAGE_MANIFEST.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf8'))
    expected = dict(manifest['files'])
    expected['PACKAGE_MANIFEST.json'] = {
        'bytes': manifest_path.stat().st_size,
        'sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest()}
    payloads = {}
    for rel, row in expected.items():
        safe = PurePosixPath(rel)
        if safe.is_absolute() or '..' in safe.parts or '\\' in rel:
            raise ValueError('UNSAFE_RELATIVE_PATH')
        data = (ROOT / rel).read_bytes()
        if len(data) != row['bytes'] or hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('SEALED_FILE_MISMATCH:' + rel)
        payloads[rel] = data
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    tree = out / 'tree'
    tree.mkdir()
    for rel, data in payloads.items():
        dest = tree / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open('xb') as stream:
            stream.write(data)
    ready = json.loads((ROOT / 'manifests/RUNTIME_READY_TEMPLATE.json').read_text(encoding='utf8'))
    ready['tree'] = str(tree)
    with (out / 'READY.json').open('x', encoding='utf8') as stream:
        json.dump(ready, stream, indent=2)
    print(json.dumps({'status': 'PASS_LOCAL_PACKAGE_ONLY', 'files': len(payloads),
                      'ready': str(out / 'READY.json'), 'board_access': False}))


if __name__ == '__main__':
    main()
