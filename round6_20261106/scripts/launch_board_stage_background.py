"""Launch one already-rendered, SHA-bound stage; release serial immediately.

The launcher contains no SDK/MMIO logic. A launched PID is not stage acceptance.
Import and --check-only do not open serial or execute the supplied stage.
"""
from pathlib import Path,PurePosixPath
import argparse,ast,base64,datetime,hashlib,json,re,secrets,zlib

MAX_SOURCE_BYTES=2*1024*1024

def require(ok,reason):
    if not ok:raise ValueError(reason)

def render_launcher_source(source,expected_sha,remote_directory):
    require(type(source) is bytes and 0<len(source)<=MAX_SOURCE_BYTES,'BOUNDED_STAGE_SOURCE_BYTES_REQUIRED')
    require(isinstance(expected_sha,str) and re.fullmatch('[0-9a-f]{64}',expected_sha) and
            hashlib.sha256(source).hexdigest()==expected_sha,'EXACT_RENDERED_STAGE_SHA256_REQUIRED')
    tree=ast.parse(source);compile(tree,'<HOST_CHECK_ONLY_RENDERED_STAGE>','exec')
    paths=[ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and
        len(n.targets)==1 and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='STAGE_DIRECTORY']
    require(len(paths)==1 and isinstance(paths[0],str) and
            re.fullmatch('/home/ubuntu/kv260_r6_d1_stage_[A-Za-z0-9_]+',paths[0]),'EXACT_RENDERED_STAGE_DIRECTORY_REQUIRED')
    require(isinstance(remote_directory,str) and
            re.fullmatch('/home/ubuntu/kv260_r6_[A-Za-z0-9_]+',remote_directory),'NEW_PRIVATE_RUNNER_DIRECTORY_REQUIRED')
    runner,stage=PurePosixPath(remote_directory),PurePosixPath(paths[0])
    require(not runner.is_relative_to(stage) and not stage.is_relative_to(runner),'RUNNER_MUST_BE_SEPARATE_FROM_STAGE_OUTPUT')
    constants={'REMOTE_DIRECTORY':remote_directory,'STAGE_DIRECTORY':paths[0],'EXPECTED_SHA256':expected_sha,
        'EXPECTED_BYTES':len(source),'SOURCE_Z_B64':base64.b64encode(zlib.compress(source,9)).decode('ascii'),
        'LAUNCH_NONCE':secrets.token_hex(16)}
    text=''.join(k+'='+repr(v)+'\n' for k,v in constants.items())+BOARD_SOURCE
    compile(text,'<HOST_CHECK_ONLY_BACKGROUND_LAUNCHER>','exec')
    return text

BOARD_SOURCE=r'''
from pathlib import Path
import base64,hashlib,json,os,platform,subprocess,time,zlib
runner=Path(REMOTE_DIRECTORY);child=None
result={'schema':'R6_ONE_BACKGROUND_STAGE_LAUNCH_V1','status':'PREFLIGHT','child_started':False,
 'automatic_retry':False,'nonce':LAUNCH_NONCE,'source_sha256':EXPECTED_SHA256,
 'runner_directory':str(runner),'stage_directory':STAGE_DIRECTORY,'run_directory':STAGE_DIRECTORY+'/run',
 'stage_result_path':STAGE_DIRECTORY+'/STAGE_RESULT.json',
 'launcher_own_MMIO':False,'launcher_own_SDK':False,'hardware_result':'NOT_VERIFIED',
 'stage_acceptance':'NOT_VERIFIED','actual_vibration':'NOT_VERIFIED'}
def need(ok,reason):
 if not ok:raise ValueError(reason)
def write_new(path,obj):
 with path.open('x',encoding='utf8') as stream:
  json.dump(obj,stream,indent=2);stream.flush();os.fsync(stream.fileno())
try:
 need(os.geteuid()==0 and platform.machine()=='aarch64','EXISTING_ROOT_AARCH64_REQUIRED')
 # Atomic mkdir refuses both existing directories and pre-existing symlinks.
 runner.mkdir(mode=0o700,parents=False,exist_ok=False)
 need(not runner.is_symlink() and runner.resolve()==runner,'RUNNER_CANONICAL_DIRECTORY_REQUIRED')
 write_new(runner/'STARTUP_MARKER.json',{'nonce':LAUNCH_NONCE,'source_sha256':EXPECTED_SHA256,
  'automatic_retry':False,'stage_directory':STAGE_DIRECTORY,'admitted_monotonic_ns':time.monotonic_ns()})
 raw=zlib.decompress(base64.b64decode(SOURCE_Z_B64,validate=True))
 need(len(raw)==EXPECTED_BYTES and hashlib.sha256(raw).hexdigest()==EXPECTED_SHA256,'TRANSFERRED_SOURCE_SHA_MISMATCH')
 source_path=runner/'stage.py'
 with source_path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
 source_path.chmod(0o400)
 need(hashlib.sha256(source_path.read_bytes()).hexdigest()==EXPECTED_SHA256,'SAVED_SOURCE_SHA_MISMATCH')
 stdout_path=runner/'stage.stdout.log';stderr_path=runner/'stage.stderr.log'
 argv=['python3','-B',str(source_path)]
 result.update(source_path=str(source_path),stdout_path=str(stdout_path),stderr_path=str(stderr_path),argv=argv)
 with stdout_path.open('xb') as stdout,stderr_path.open('xb') as stderr:
  child=subprocess.Popen(argv,cwd=runner,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,start_new_session=True)
  result.update(child_started=True,pid=child.pid,launched_monotonic_ns=time.monotonic_ns())
 # No wait, poll, signal, retry, MMIO or SDK call. Child logs never occupy UART.
 result['status']='LAUNCHED_NOT_ACCEPTED'
 write_new(runner/'LAUNCH_RESULT.json',result)
except BaseException as exc:
 result.update(status='LAUNCHER_FAILED_NO_RETRY',error=repr(exc),required_action='REVIEW_PID_AND_LOGS_NO_AUTOMATIC_RETRY')
 if child is not None:result.update(child_started=True,pid=child.pid)
print('FAST_RESULT '+json.dumps(result),flush=True)
'''

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--source-sha256',required=True)
    parser.add_argument('--remote-directory',required=True)
    parser.add_argument('--out',type=Path,required=True)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--run-once',action='store_true');mode.add_argument('--check-only',action='store_true')
    args=parser.parse_args(argv)
    require(args.source.is_absolute() and args.source.is_file() and not args.source.is_symlink() and
            args.source.stat().st_size<=MAX_SOURCE_BYTES,'LOCAL_REGULAR_ABSOLUTE_STAGE_REQUIRED')
    source=render_launcher_source(args.source.read_bytes(),args.source_sha256,args.remote_directory)
    if args.check_only:
        print(json.dumps({'status':'PASS_HOST_SOURCE_CHECK_ONLY','launcher_source_sha256':hashlib.sha256(source.encode()).hexdigest(),
            'stage_source_sha256':args.source_sha256,'board_access':False,'stage_executed':False},indent=2));return 0
    require(args.out.is_absolute() and not args.out.exists() and not args.out.is_symlink(),'NEW_ABSOLUTE_HOST_RESULT_REQUIRED')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    marker=args.out.with_name(args.out.name+'.started.json')
    with marker.open('x',encoding='utf8') as stream:json.dump({'stage_source_sha256':args.source_sha256,
        'launcher_source_sha256':hashlib.sha256(source.encode()).hexdigest(),'remote_directory':args.remote_directory,
        'automatic_retry':False},stream,indent=2)
    import board_console
    console=board_console.Console('stage_background_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    try:
        result=console.run(source,'one_detached_stage_launch_no_hardware_logic',timeout=30)
        with args.out.open('x',encoding='utf8') as stream:json.dump(result,stream,indent=2)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        return 0 if result.get('status')=='LAUNCHED_NOT_ACCEPTED' and result.get('child_started') is True else 2
    finally:console.close()

if __name__=='__main__':raise SystemExit(main())
