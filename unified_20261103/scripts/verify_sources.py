"""Verify protected originals and distinguish approved integration edits."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPROVED_COPY_EDITS = {'rtl/kv260_haptic_pipeline.v'}

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def verify():
    manifest = json.loads((ROOT/'manifests/SOURCE_SNAPSHOT.json').read_text(encoding='utf8'))
    items = []
    errors = []
    for row in manifest['files']:
        dst = ROOT/row['copy'].replace('\\', '/')
        old_ok = None  # Historical originals are provenance, not build dependencies.
        copy_ok = dst.is_file() and digest(dst) == row['sha256']
        approved = row['copy'].replace('\\', '/') in APPROVED_COPY_EDITS
        items.append(dict(file=row['copy'],historical_original_rechecked=False,
                          copy_byte_identical=copy_ok,approved_integration_copy=approved))
        if not copy_ok:
            errors.append(row['copy'])
    return dict(result='PASS' if not errors else 'FAIL',
                protected_sources=len(items), errors=errors, files=items,
                hardware_access=False)

if __name__ == '__main__':
    result=verify()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(bool(result['errors']))
