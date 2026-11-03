"""Compile the package's unchanged C++17 core; no camera, FPGA or ARM inference."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from tool_paths import child_env, cpp_compiler

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(output=None):
    compiler = cpp_compiler()
    output = Path(output) if output else ROOT/'results/host_core'/('basic_depth.host.exe' if os.name == 'nt' else 'basic_depth.host')
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    inputs = [ROOT/'core'/name for name in ('basic_depth.cpp', 'basic_depth_cli.cpp', 'basic_depth.hpp', 'phase_profile.hpp')]
    hashes = {str(p.relative_to(ROOT)).replace('\\','/'): sha(p) for p in inputs}
    latest = output.parent/'LATEST_HOST_BUILD.json'
    if latest.is_file() and output.is_file():
        old = json.loads(latest.read_text(encoding='utf8'))
        if old.get('compiler') == compiler and old.get('input_sha256') == hashes and old.get('output_sha256') == sha(output):
            print('HOST_CORE_REUSED', output)
            return output
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run = output.parent/('build_'+stamp)
    run.mkdir(exist_ok=False)
    temporary = run/output.name
    command = [compiler, '-std=c++17', '-O2', '-Wall', '-Wextra',
               str(inputs[0]), str(inputs[1]), '-o', str(temporary)]
    with (run/'stdout.log').open('wb') as so, (run/'stderr.log').open('wb') as se:
        proc = subprocess.run(command, cwd=ROOT, env=child_env(), stdout=so, stderr=se, timeout=180)
    receipt = dict(status='PASS_HOST_ONLY' if proc.returncode == 0 else 'FAIL',
                   compiler=compiler, command=command, exit_code=proc.returncode,
                   input_sha256=hashes, hardware_access=False, output=str(output))
    if proc.returncode == 0:
        # Only this package's generated output is replaced, never source data.
        os.replace(temporary, output)
        receipt['output_sha256'] = sha(output)
    (run/'RESULT.json').write_text(json.dumps(receipt, indent=2), encoding='utf8')
    if proc.returncode:
        raise RuntimeError(f'C++ compile failed: {run}')
    latest.write_text(json.dumps(receipt, indent=2), encoding='utf8')
    print('HOST_CORE_BUILT', output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    build(args.output)
