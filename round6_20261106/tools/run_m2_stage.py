"""Same reviewed stage, with the successful demo's current-boot admission key.

No board is accessed on import or --check-only. All original proof, prior-stage,
health, worker-lock, timeout and FULL_OFF checks stay in run_d1_board_stage.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import run_d1_board_stage as stage

OLD = "attempt=Path('/home/ubuntu')/('kv260_r6_D1_runtime_attempt_'+RECEIPT['target'].rsplit('_',1)[1]+'.json')"
NEW = "attempt=Path('/home/ubuntu')/('kv260_r6_D1_runtime_attempt_'+RECEIPT['target'].rsplit('_',1)[1]+'_boot_'+Path('/proc/sys/kernel/random/boot_id').read_text().strip()+'.json')"
original_make_source = stage.make_source


def boot_scoped_source(*args, **kwargs):
    source = original_make_source(*args, **kwargs)
    if source.count(OLD) != 1:
        raise ValueError('EXACT_REVIEWED_ADMISSION_LINE_REQUIRED')
    source = source.replace(OLD, NEW)
    compile(source, '<M2_CURRENT_BOOT_STAGE>', 'exec')
    return source


if __name__ == '__main__':
    stage.make_source = boot_scoped_source
    raise SystemExit(stage.main())
