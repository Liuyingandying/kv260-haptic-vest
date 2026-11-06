"""Cross-build native32 helper without executing ARM code or touching devices."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import struct
import shutil
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compiler',type=Path,default=Path(os.environ.get('R6_AARCH64_GCC','aarch64-linux-gnu-gcc')))
    parser.add_argument('--out',type=Path,required=True,help='new output directory; existing directories refused')
    args=parser.parse_args()
    compiler=shutil.which(str(args.compiler))
    if compiler is None:raise RuntimeError('AARCH64_COMPILER_NOT_FOUND: use --compiler or R6_AARCH64_GCC')
    args.compiler=Path(compiler).resolve()
    out=args.out.resolve();out.mkdir(parents=True,exist_ok=False)
    source=Path(__file__).resolve().parent/'control_mmio32.c'
    output=out/'control_mmio32.aarch64.so';commands=[]
    flags=['-std=c11','-Os','-Wall','-Wextra','-Werror','-fPIC','-shared','-nostdlib','-fvisibility=hidden','-Wl,--build-id=none']
    def run(name,command):
        result=subprocess.run(list(map(str,command)),capture_output=True,timeout=60)
        (out/(name+'.stdout')).write_bytes(result.stdout);(out/(name+'.stderr')).write_bytes(result.stderr)
        commands.append({'name':name,'command':list(map(str,command)),'returncode':result.returncode})
        if result.returncode:raise RuntimeError('TOOL_FAILED:'+name)
        return result.stdout.decode('utf-8',errors='replace')
    run('compile',[args.compiler,*flags,source,'-o',output])
    suffix='.exe' if os.name=='nt' else ''
    header=run('readelf',[args.compiler.with_name('aarch64-linux-gnu-readelf'+suffix),'-W','-h','-l','-d','-s',output])
    dis=run('objdump',[args.compiler.with_name('aarch64-linux-gnu-objdump'+suffix),'-d',output])
    raw=output.read_bytes()
    if raw[:6]!=b'\x7fELF\x02\x01' or struct.unpack_from('<H',raw,18)[0]!=183:
        raise RuntimeError('NOT_AARCH64_ELF64_LE')
    if any(x in header for x in ('(NEEDED)','INTERP','(INIT)','(FINI)','(INIT_ARRAY)','(FINI_ARRAY)')):
        raise RuntimeError('UNEXPECTED_RUNTIME_OR_INITIALIZER')
    counts={}
    for name in ('control_read32','control_gpio_read32','control_status_read32','control_write32'):
        part=dis.split('<'+name+'>:',1)[1].split('\n\n',1)[0]
        count=len(re.findall(r'\b'+('str' if name=='control_write32' else 'ldr')+r'\s+w\d+,\s*\[',part))
        if count!=1 or part.count('dmb\tsy')!=2:
            raise RuntimeError('NATIVE32_SINGLE_ACCESS_DISASSEMBLY_MISMATCH:'+name)
        counts[name]={'single_32bit_MMIO_instruction':count,'dmb_sy':2}
    sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
    result={'schema':'R6_D1_NATIVE32_LOCAL_BUILD_V1','status':'PASS','commands':commands,
            'source_sha256':sha(source),'compiler_sha256':sha(args.compiler),'helper_sha256':sha(output),
            'helper_bytes':len(raw),'instruction_checks':counts,'AArch64_binary_executed':False,
            'hardware_access':False,'physical_output_permitted':False}
    (out/'BUILD_RESULT.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
