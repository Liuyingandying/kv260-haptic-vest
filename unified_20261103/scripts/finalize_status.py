"""Consolidate observed offline results only; never promote hardware claims."""
import datetime
import argparse
import hashlib
import json
from pathlib import Path
from verify_sources import verify

ROOT=Path(__file__).resolve().parents[1]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text(encoding='utf8'))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--core-report',type=Path,default=ROOT/'results/unified_app_test_results.json')
    parser.add_argument('--admission-report',type=Path,default=ROOT/'results/frame_admission_test_results.json')
    args=parser.parse_args()
    protection=verify()
    candidates=[]
    for path in (ROOT/'results').glob('rtl_*/RESULTS.json'):
        r=load(path)
        matches=all((ROOT/name).is_file() and sha(ROOT/name)==value for name,value in r.get('input_sha256',{}).items())
        fixture_matches=all((ROOT/name).is_file() and sha(ROOT/name)==value for name,value in r.get('fixture_sha256',{}).items())
        if r.get('status')=='PASS' and matches and fixture_matches and {c['case'] for c in r['cases']}=={0,1,2,3}:
            candidates.append((path,r))
    candidates.sort(key=lambda pair:pair[0].stat().st_mtime_ns)
    rtl_path,rtl=(candidates[-1] if candidates else (None,None))
    app_path=args.core_report
    policy_path=args.admission_report
    software={str(p):load(p) for p in (app_path,policy_path) if p.is_file()}
    host_ok=len(software)==2 and all(r.get('status')=='PASS' and not r.get('failures') and not r.get('errors') for r in software.values())
    current_sources={str(p.relative_to(ROOT)):sha(p) for folder in ('rtl','runtime','core','scripts','tests','fixtures')
                     for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    status=dict(timestamp_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        SOURCE_PROTECTION=protection['result'],
        HOST_TESTS='PASS' if host_ok else 'FAIL',
        HOST_SOFTWARE_RESULTS=software,
        RTL_INTEGRATION='PASS' if rtl else 'NOT_VERIFIED_CURRENT_SOURCE',
        RTL_RESULT_FILE=str(rtl_path) if rtl_path else None,
        RTL_CASES=rtl['cases'] if rtl else [],
        VIVADO_PROJECT=(load(ROOT/'vivado/LATEST_REVIEW.json') if (ROOT/'vivado/LATEST_REVIEW.json').is_file() else dict(status='NOT_BUILT_IN_THIS_CHECKOUT')),
        BOARD_IMPLEMENTATION='NOT_RUN',BOARD_LOAD='NOT_RUN',
        LIVE_D455_TO_UNIFIED_CONTROLLER='NOT_VERIFIED',
        REAL_TCA_PCA_ACK='BLOCKED_EXISTING_FIRST_ADDRESS_NACK',
        MOTOR_VIBRATION='NOT_VERIFIED',HARDWARE_OUTPUT_SAFETY='NOT_VERIFIED',
        PHYSICAL_OUTPUT_ENABLED=False,HARDWARE_ACTION_TAKEN=False,
        current_source_sha256=current_sources)
    (ROOT/'INTEGRATION_STATUS.json').write_text(json.dumps(status,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in status.items() if k not in ('HOST_SOFTWARE_RESULTS','RTL_CASES','current_source_sha256','VIVADO_PROJECT')},ensure_ascii=False,indent=2))
    return 0 if rtl and host_ok and protection['result']=='PASS' else 1

if __name__=='__main__':raise SystemExit(main())
