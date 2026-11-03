"""Offline board shell check with PS and IOBUF models, no hardware access."""
from __future__ import annotations
import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

BOARD = Path(__file__).resolve().parents[1]
ROOT = BOARD.parent
sys.path.insert(0, str(ROOT/'scripts'))
from source_list import RTL_FILES
from tool_paths import iverilog, vvp, child_env


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = BOARD/'tests'/('result_'+stamp)
    out.mkdir(exist_ok=False)
    sources = [BOARD/'tests/tb_board_shell.sv',
               BOARD/'rtl/kv260_unified_board_top.sv', *RTL_FILES]
    before = {str(p): digest(p) for p in sources}
    commands = [
        [iverilog(), '-g2012', '-Wall', '-s', 'tb_board_shell',
         '-o', str(out/'shell.vvp'), *map(str, sources)],
        [vvp(), '-i', str(out/'shell.vvp')],
    ]
    records = []
    for name, cmd in zip(('compile', 'simulation'), commands):
        start = time.perf_counter()
        try:
            proc = subprocess.run(cmd, cwd=ROOT, env=child_env(), capture_output=True, timeout=30)
            code, stdout, stderr, timed_out = proc.returncode, proc.stdout, proc.stderr, False
        except subprocess.TimeoutExpired as exc:
            code, stdout, stderr, timed_out = 124, exc.stdout or b'', exc.stderr or b'', True
        (out/(name+'.stdout.log')).write_bytes(stdout)
        (out/(name+'.stderr.log')).write_bytes(stderr)
        records.append(dict(stage=name, command=cmd, exit_code=code,
                            timed_out=timed_out, host_wall_seconds=time.perf_counter()-start))
        if code:
            break
    text = (out/'simulation.stdout.log').read_text(errors='replace') if (out/'simulation.stdout.log').exists() else ''
    marker = re.search(r'BOARD_SHELL_RESULT checks=(\d+) failures=(\d+) PS_MODEL_ONLY=1 HARDWARE_ACTION=0', text)
    after = {str(p): digest(p) for p in sources}
    passed = len(records) == 2 and all(r['exit_code'] == 0 for r in records) and bool(marker) and marker[2] == '0' and 'BOARD_SHELL_PASS' in text and before == after
    result = dict(status='PASS' if passed else 'FAIL',
                  checks=int(marker[1]) if marker else None,
                  failures=int(marker[2]) if marker else None,
                  source_sha256=before, sources_unchanged=before == after,
                  stages=records, test_clock_hz=100000000,
                  PS='SIMULATION_STUB_NOT_REAL_PS_VERIFICATION',
                  IOBUF='IDEAL_SIMULATION_MODEL_NOT_ELECTRICAL_ACCEPTANCE',
                  hardware_action_taken=False, camera_action_taken=False,
                  bitstream_generated=False, output_directory=str(out))
    (out/'RESULT.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf8')
    print(text)
    print(json.dumps(dict(status=result['status'], checks=result['checks'],
                          failures=result['failures'], result_file=str(out/'RESULT.json')), indent=2))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(main())
