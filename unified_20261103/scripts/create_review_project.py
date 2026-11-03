"""Create a new Vivado source/simulation project; never synthesize or access hardware."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from source_list import ROOT, RTL_FILES
from tool_paths import vivado as find_vivado, child_env

def main():
    vivado=Path(find_vivado())
    if not vivado.is_file():
        raise SystemExit('Vivado 2025.2 executable missing')
    missing=[str(p) for p in RTL_FILES if not p.is_file()]
    if missing:raise SystemExit('Missing inputs: '+repr(missing))
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run=ROOT/'results'/('vivado_review_'+stamp);run.mkdir(parents=True)
    project=ROOT/'vivado'/('review_'+stamp)
    tcl=run/'create.tcl'
    lines=[f'create_project kv260_unified {{{project.as_posix()}}} -part xck26-sfvc784-2LV-c']
    lines += [f'add_files -norecurse {{{p.as_posix()}}}' for p in RTL_FILES]
    lines += ['set_property top unified_perception_haptic [get_filesets sources_1]',
              'set_property target_language Verilog [current_project]',
              'update_compile_order -fileset sources_1',
              'puts "UNIFIED_REVIEW_TOP=[get_property top [get_filesets sources_1]]"',
              'foreach f [get_files -compile_order sources -used_in synthesis] {puts "UNIFIED_INPUT=$f"}',
              'puts "UNIFIED_NO_SYNTHESIS_NO_IMPLEMENTATION_NO_HARDWARE"',
              'close_project','exit 0']
    tcl.write_text('\n'.join(lines)+'\n',encoding='utf8')
    cmd=[str(vivado),'-mode','batch','-notrace','-source',str(tcl),
         '-log',str(run/'vivado.log'),'-journal',str(run/'vivado.jou')]
    # This command host omits the standard Windows architecture variable.
    # AMD loader.bat otherwise exits silently before starting Vivado. Limit
    # the correction to this child, on the verified 64-bit x86 Windows host.
    env=child_env()
    if not env.get('PROCESSOR_ARCHITECTURE'):
        env['PROCESSOR_ARCHITECTURE']='AMD64'
    with (run/'stdout.txt').open('wb') as out,(run/'stderr.txt').open('wb') as err:
        proc=subprocess.run(cmd,cwd=run,stdout=out,stderr=err,timeout=180,env=env)
    receipt=dict(exit_code=proc.returncode,command=cmd,
                 project=str(project/'kv260_unified.xpr'),
                 log=str(run/'vivado.log'),board_access=False,
                 synthesis=False,implementation=False,bitstream_generated=False,
                 pin_mapping='NOT_ASSIGNED_NO_BOARD_DEPLOYMENT')
    receipt['child_architecture']=env['PROCESSOR_ARCHITECTURE']
    receipt['rtl_sha256']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in RTL_FILES}
    (run/'RESULT.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    (ROOT/'vivado/LATEST_REVIEW.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
    print(json.dumps(receipt,indent=2))
    return proc.returncode

if __name__=='__main__':sys.exit(main())
