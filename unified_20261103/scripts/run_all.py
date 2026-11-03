"""One offline entry: package hashes, local C++ build, tests, replay and RTL."""
import datetime
import json
from pathlib import Path
import subprocess
import sys
from tool_paths import child_env

ROOT=Path(__file__).resolve().parents[1]

def main():
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out=ROOT/'results'/('acceptance_'+stamp);out.mkdir(parents=True,exist_ok=False)
    core_report=out/'software_core.json'
    admission_report=out/'software_admission.json'
    stages=[('package_integrity',[sys.executable,'-B',str(ROOT/'scripts/verify_sources.py')]),
            ('host_core_build',[sys.executable,'-B',str(ROOT/'scripts/build_host_core.py')]),
            ('software_core',[sys.executable,'-B',str(ROOT/'tests/test_unified_app.py'),'--json-report',str(core_report)]),
            ('software_admission',[sys.executable,'-B',str(ROOT/'tests/test_frame_admission.py'),'--json-report',str(admission_report)]),
            ('saved_depth_replay',[sys.executable,'-B',str(ROOT/'scripts/run_saved_frame.py')]),
            ('rtl',[sys.executable,'-B',str(ROOT/'scripts/run_rtl_tests.py')]),
            ('status',[sys.executable,'-B',str(ROOT/'scripts/finalize_status.py'),
                       '--core-report',str(core_report),'--admission-report',str(admission_report)])]
    results=[]
    for name,cmd in stages:
        with (out/(name+'.stdout.txt')).open('wb') as so,(out/(name+'.stderr.txt')).open('wb') as se:
            try:
                p=subprocess.run(cmd,cwd=ROOT,stdout=so,stderr=se,env=child_env(),timeout=240)
                code=p.returncode
            except subprocess.TimeoutExpired:code=124
        results.append(dict(stage=name,command=cmd,exit_code=code))
        print(name,code,flush=True)
        if code:break
    result=dict(result='PASS' if len(results)==len(stages) and all(r['exit_code']==0 for r in results) else 'FAIL',
                stages=results,hardware_test='NOT_RUN',physical_output_enabled=False)
    (out/'RESULT.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(str(out/'RESULT.json'))
    return 0 if result['result']=='PASS' else 1

if __name__=='__main__':sys.exit(main())
