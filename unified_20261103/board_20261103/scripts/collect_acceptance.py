"""Summarize actual local Vivado evidence without claiming board operation."""
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    build=json.loads((ROOT/'LATEST_BUILD.json').read_text(encoding='utf8'))
    run=Path(build['result_directory'])
    proof_path=run/'postroute_connectivity.txt'
    if not proof_path.exists():proof_path=run/'synth_connectivity.txt'
    proof=proof_path.read_text(encoding='utf8',errors='replace') if proof_path.exists() else ''
    timing=(run/'timing_result.txt').read_text() if (run/'timing_result.txt').exists() else ''
    ports=[line for line in proof.splitlines() if line.startswith('PORT=')]
    pin_ok=len(ports)==2 and all(any(f'PORT={name} ' in line and f'PIN={pin} ' in line and 'IO=LVCMOS33 ' in line and 'DRIVE=4 ' in line and 'SLEW=SLOW ' in line for line in ports) for name,pin in [('i2c_scl_io','H12'),('i2c_sda_io','B10')])
    usb=[line for line in proof.splitlines() if line.startswith('USB=')]
    usb_ok=len(usb)==4 and all('DRIVER_REFS=GND' in line for line in usb)
    ps=(ROOT/'evidence/PS_CONFIG_OBJECT_COMPARISON.tsv').read_text(encoding='utf8')
    ps_ok='\tDIFF\t' not in ps and sum('\tMATCH\t' in x or '\tNEW_INSTANCE_NAME\t' in x for x in ps.splitlines())==1692
    inputs_match=all((ROOT/name).is_file() and sha(ROOT/name)==digest for name,digest in build['input_sha256'].items())
    match=re.search(r'WNS=([^ ]+) WHS=([^\s]+)',timing)
    io_drc=(run/'physical_io_drc.rpt').read_text(errors='replace') if (run/'physical_io_drc.rpt').exists() else ''
    io_drc_ok=bool(re.search(r'Checks found:\s+0',io_drc))
    shells=sorted((ROOT/'tests').glob('result_*/RESULT.json'))
    shell=json.loads(shells[-1].read_text(encoding='utf8')) if shells else {}
    state=dict(BOARD_PROJECT='PASS' if build.get('exit_code')==0 else 'FAIL',
        CONSTRAINTS_ATTACHED='PASS' if pin_ok else 'NOT_VERIFIED',
        PHYSICAL_PORTS=ports,PS_1692_PARAMETER_PARITY='PASS' if ps_ok else 'FAIL',
        NSTD_UCIO_DRC='PASS' if io_drc_ok else 'NOT_VERIFIED',
        USB_OVERCURRENT_FOUR_GND='PASS' if usb_ok else 'NOT_VERIFIED',
        CONNECTIVITY_QUERY_SCOPE='POST_ROUTE' if proof_path.name.startswith('postroute') else 'SYNTHESIS',
        INTERNAL_ROUTED_TIMING='PASS' if match and min(float(match[1]),float(match[2]))>=0 else 'NOT_VERIFIED',
        WNS_NS=float(match[1]) if match else None,WHS_NS=float(match[2]) if match else None,
        BOARD_SHELL_SIMULATION=shell.get('status','NOT_VERIFIED'),
        BOARD_SHELL_CHECKS=shell.get('checks'),INPUTS_UNCHANGED=inputs_match,
        project=build['project'],result_directory=str(run),
        bitstreams=[dict(path=p,sha256=sha(Path(p))) for p in build.get('bitstreams',[])],
        BOARD_LOAD='NOT_PERFORMED',CLOCK_RUNTIME='NOT_VERIFIED',
        EXTERNAL_IIC_ELECTRICAL_TIMING='NOT_VERIFIED',REAL_TCA_PCA_ACK='NOT_VERIFIED',
        MOTOR_VIBRATION='NOT_VERIFIED',PHYSICAL_OUTPUT_ENABLED=False,
        HARDWARE_ACTION_TAKEN=False)
    state['LOCAL_BUILD_ACCEPTANCE']='PASS' if all([build.get('exit_code')==0,pin_ok,usb_ok,ps_ok,io_drc_ok,inputs_match,match and min(float(match[1]),float(match[2]))>=0]) else 'FAIL'
    (ROOT/'BOARD_BUILD_ACCEPTANCE.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(state,ensure_ascii=False,indent=2))
    return 0 if state['LOCAL_BUILD_ACCEPTANCE']=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
