"""Offline test entry: Host mocks by default; RTL simulation is explicit.
Never imports a native ARM helper, starts the SDK, opens a device, or runs a
board stage. These checks do not grant physical or production acceptance.
"""
from pathlib import Path
import argparse,datetime,hashlib,json,subprocess,sys,unittest
TESTS=Path(__file__).resolve().parent;ROOT=TESTS.parent
sys.dont_write_bytecode=True
sys.path[:0]=[str(ROOT),str(ROOT/'runtime_compact'),str(TESTS)]
HOST_MODULES=(
 'host.test_control_paths','host.test_control_session','host.test_digital_matrix',
 'host.test_matrix_source_recovery_host','host.test_window_telemetry_host','host.test_window_renewal_host')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--suite',choices=('host','rtl','all'),default='host')
    parser.add_argument('--iverilog');parser.add_argument('--vvp')
    args=parser.parse_args();ok=True
    if args.suite in ('host','all'):
        suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(n) for n in HOST_MODULES)
        result=unittest.TextTestRunner(verbosity=2).run(suite);ok=result.wasSuccessful()
        out=TESTS/'results'/('host_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        out.mkdir(parents=True)
        report={'status':'PASS' if ok else 'FAIL','tests_run':result.testsRun,
                'failures':len(result.failures),'errors':len(result.errors),
                'hardware_action':False,'SDK_started':False,'fixture_kind':'SYNTHETIC_HOST_ONLY',
                'runtime_source_sha256':hashlib.sha256((ROOT/'runtime_night/run_d1_live.py').read_bytes()).hexdigest(),
                'production_FRESH':'BLOCKED','physical_acceptance':'NOT_PROVEN_BY_HOST_TESTS'}
        (out/'RESULT.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,indent=2))
    if args.suite in ('rtl','all') and ok:
        cmd=[sys.executable,'-B',str(TESTS/'pl_c2/run_dgm2_tests.py')]
        if args.iverilog:cmd+=['--iverilog',args.iverilog]
        if args.vvp:cmd+=['--vvp',args.vvp]
        ok=subprocess.run(cmd,cwd=ROOT).returncode==0
    return 0 if ok else 1

if __name__=='__main__':raise SystemExit(main())
