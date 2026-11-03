"""Independent local-only Icarus integration test, never opens board devices."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from source_list import RTL_FILES
from tool_paths import iverilog, vvp, child_env

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ['tests/tb_unified.sv'] + [str(path.relative_to(ROOT)).replace('\\','/') for path in RTL_FILES]

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', type=int, choices=(0, 1, 2, 3), action='append')
    args = parser.parse_args()
    cases = args.case if args.case is not None else [0, 1, 2, 3]
    out = ROOT / 'results' / ('rtl_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ'))
    out.mkdir(parents=True, exist_ok=False)
    inputs = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
    fixture_names = ['fixtures/saved_00_expected.mem', 'fixtures/saved_00_expected.kpf'] if 3 in cases else []
    fixture_hashes = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in fixture_names}
    records = []
    for case in cases:
        binary = out / f'case{case}.vvp'
        commands = [
            [iverilog(), '-g2012', '-Wall', '-s', 'tb_unified',
             f'-Ptb_unified.CASE_ID={case}', '-o', str(binary)] + [str(ROOT/name) for name in SOURCES],
            [vvp(), '-i', '-N', str(binary)],
        ]
        record = {'case': case, 'steps': []}
        for index, command in enumerate(commands):
            start = time.perf_counter()
            timed_out = False
            prefix = f'case{case}_' + ('compile' if index == 0 else 'run')
            # vvp -i disables stdio buffering; stage evidence is available on
            # disk while the bounded simulation is still executing.
            print(f'RUN case={case} stage={prefix} stdout={out/(prefix+".stdout.log")}', flush=True)
            with (out/(prefix+'.stdout.log')).open('wb') as stdout_file, (out/(prefix+'.stderr.log')).open('wb') as stderr_file:
                try:
                    proc = subprocess.run(command, cwd=ROOT, env=child_env(), stdout=stdout_file, stderr=stderr_file, timeout=180)
                    code = proc.returncode
                except subprocess.TimeoutExpired:
                    code = None
                    timed_out = True
            record['steps'].append(dict(command=command, exit_code=code,
                timed_out=timed_out, wall_seconds=time.perf_counter()-start))
            if code != 0:
                break
        text = (out/f'case{case}_run.stdout.log').read_text(errors='replace') if (out/f'case{case}_run.stdout.log').exists() else ''
        marker = re.search(r'UNIFIED_RESULT case=(\d+) scenarios=(\d+) checks=(\d+) failures=(\d+)', text)
        record['status'] = 'PASS' if (len(record['steps']) == 2 and
            all(step['exit_code'] == 0 for step in record['steps']) and marker and
            marker[4] == '0' and f'UNIFIED_SIM_PASS case={case}' in text and 'FAIL ' not in text) else 'FAIL'
        if marker:
            record.update(scenarios=int(marker[2]), checks=int(marker[3]), failures=int(marker[4]))
        replay = re.search(r'SAVED_REPLAY_RESULT unknown_cells=(\d+) nonzero_cells=(\d+) original_words=(\d+)', text)
        if replay:
            record['saved_replay'] = dict(unknown_cells=int(replay[1]), nonzero_cells=int(replay[2]), original_words=int(replay[3]), hardware_live=False)
        records.append(record)
        for line in text.splitlines():
            if line.startswith(('SCENARIO', 'FAIL', 'FATAL', 'UNIFIED_', 'SAVED_REPLAY_')):
                print(line, flush=True)
    after = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
    after_fixture = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in fixture_names}
    result = dict(status='PASS' if all(r['status']=='PASS' for r in records) and inputs==after and fixture_hashes==after_fixture else 'FAIL',
        cases=records, input_sha256=inputs, sources_unchanged_during_run=inputs==after,
        fixture_sha256=fixture_hashes, fixtures_unchanged_during_run=fixture_hashes==after_fixture,
        hardware_access=False, camera_access=False, board_load=False,
        test_clock_hz=1000000, configured_iic_hz=100000, freshness_us=250000,
        physical_pins_enable=0, simulation_authority_enabled_only_in_testbench=True,
        performance_scope='OFFLINE_SIMULATION_NOT_BOARD_TIMING_OR_ELECTRICAL_ACCEPTANCE',
        result_directory=str(out))
    (out/'RESULTS.json').write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(dict(status=result['status'], result_directory=str(out)), indent=2))
    return 0 if result['status']=='PASS' else 1

if __name__ == '__main__':
    raise SystemExit(main())
