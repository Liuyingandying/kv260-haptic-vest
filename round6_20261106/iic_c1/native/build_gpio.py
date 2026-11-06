"""Cross-build GPIO helper only; never executes ARM code or opens hardware.

Requires a new --out directory. Existing deployed helpers are never overwritten.
The source and compiler identity, command, logs and ELF inspection are retained.
"""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',default=os.environ.get('R6_AARCH64_GCC','aarch64-linux-gnu-gcc'))
    parser.add_argument('--out',type=Path,required=True,help='new output directory; existing directories refused')
    args=parser.parse_args()
    executable=shutil.which(str(args.compiler))
    if executable is None:raise RuntimeError('AARCH64_COMPILER_NOT_FOUND: use --compiler or R6_AARCH64_GCC')
    compiler=Path(executable).resolve()
    here=Path(__file__).resolve().parent
    source=here/'gpio_mmio32.c'
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    origin=json.loads((here/'SOURCE_ORIGIN.json').read_text(encoding='utf-8'))
    if sha(source)!=origin['source_sha256']:raise RuntimeError('GPIO_SOURCE_HASH_CHANGED')
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    output=out/'gpio_mmio32.aarch64.so'
    flags=['-std=c11','-O2','-Wall','-Wextra','-Werror','-fPIC','-shared','-nostdlib','-Wl,--build-id=none']
    commands=[]
    def run(name,command):
        command=list(map(str,command))
        done=subprocess.run(command,capture_output=True,timeout=60)
        (out/(name+'.stdout')).write_bytes(done.stdout)
        (out/(name+'.stderr')).write_bytes(done.stderr)
        commands.append({'name':name,'command':command,'returncode':done.returncode})
        if done.returncode:raise RuntimeError('TOOL_FAILED:'+name)
        return done.stdout
    run('compile',[compiler,*flags,source,'-o',output])
    raw=output.read_bytes()
    if raw[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',raw,18)[0]!=183:
        raise RuntimeError('NOT_AARCH64_ELF64_LITTLE_ENDIAN')
    readelf=compiler.with_name('aarch64-linux-gnu-readelf'+('.exe' if os.name=='nt' else ''))
    header=run('readelf',[readelf,'-W','-h','-l','-d','-Ws',output])
    if any(x in header for x in (b'(NEEDED)',b'INTERP',b'(INIT)',b'(FINI)',b'(INIT_ARRAY)',b'(FINI_ARRAY)')):
        raise RuntimeError('UNEXPECTED_RUNTIME_DEPENDENCY_OR_INITIALIZER')
    result={'schema':'R6_GPIO_MMIO32_SOURCE_BUILD_V1','status':'PASS',
            'source_sha256':sha(source),'compiler_sha256':sha(compiler),
            'output_sha256':sha(output),'output_bytes':len(raw),'commands':commands,
            'architecture':'AArch64 ELF64 little endian','DT_NEEDED':[],
            'AArch64_binary_executed':False,'hardware_access':False,
            'sealed_helper_replaced':False,'deployment_authorized':False,
            'note':'Rebuild identity is reported, not assumed. Do not deploy without updating helper hash contracts and relevant regression.'}
    (out/'BUILD_RESULT.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
