"""Replay immutable saved depth through the unchanged core and compare full KPF1."""
import datetime
from pathlib import Path
import subprocess
import sys
from build_host_core import build

ROOT=Path(__file__).resolve().parents[1]
def main():
    core=ROOT/'results/host_core/basic_depth.host.exe'
    core = build(core)
    out=ROOT/'results'/('saved_replay_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    command=[sys.executable,'-B',str(ROOT/'runtime/unified_app.py'),
             '--input',str(ROOT/'fixtures/saved_00.bdp'),
             '--core-executable',str(core),'--output-dir',str(out)]
    p=subprocess.run(command,cwd=ROOT,timeout=60)
    if p.returncode:return p.returncode
    if (out/'frame_0001.kpf').read_bytes()!=(ROOT/'fixtures/saved_00_expected.kpf').read_bytes():
        raise SystemExit('FAIL_SAVED_KPF1_BYTE_PARITY')
    print('PASS_SAVED_KPF1_ALL_384_BYTES_MATCH_OFFLINE_ONLY',out)
    return 0
if __name__=='__main__':sys.exit(main())
