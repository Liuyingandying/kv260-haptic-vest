"""Bounded real Linux/AArch64 Gemini -> UDS -> strict Producer observation.

Only the verified native child owns the camera. Default: no hardware backend.
Explicit --real-obs2 --profile enables one capability gate before SDK and only
independent observation bank submissions; never control, IIC, FPGA loading,
USB permissions or RGB. Capture freshness remains BLOCKED.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parent
STALE_ERRORS = frozenset(("HOST_RECEIPT_TOO_OLD_NOT_CAPTURE_AGE",
                          "SDK_DELIVERY_TO_RESULT_TOO_OLD_NOT_CAPTURE_AGE",
                          "OBS2_RECEIPT_EXPIRED_DURING_PRODUCER"))


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def warning_counts(text):
    lines=text.splitlines()
    return {'OB_EVENT_TIMEOUT':len(re.findall('OB_EVENT_TIMEOUT',text)),
            'cancel_request_lines':sum('cancel' in line.lower() for line in lines),
            'warning_lines':sum(bool(re.search(r'\bwarn(?:ing)?\b',line,re.I)) for line in lines),
            'error_lines':sum(bool(re.search(r'\berror\b',line,re.I)) for line in lines)}


def distribution(values):
    if not values:return None
    s=sorted(values)
    return {'samples':len(s),'p50':s[len(s)//2],
            'p95':s[max(0,(95*len(s)+99)//100-1)],'max':s[-1],'min':s[0]}


def classify_producer_error(exc):
    return 'DROP_STALE_OBSERVATION' if str(exc) in STALE_ERRORS else 'FATAL_PRODUCER_ERROR'


def verify_runtime():
    manifest=json.loads((ROOT/'RUNTIME_MANIFEST.json').read_text(encoding='utf8'))
    checked=[]
    for rel, expected in manifest['files'].items():
        p=ROOT/rel
        if Path(rel).is_absolute() or '..' in Path(rel).parts or not p.is_file():
            raise ValueError('PACKAGE_PATH_OR_FILE_INVALID:'+rel)
        actual=sha(p)
        if actual!=expected['sha256']:raise ValueError('PACKAGE_SHA256_MISMATCH:'+rel)
        checked.append(rel)
    return {'package_identity':manifest['identity'],'checked_files':len(checked),'status':'PASS'}


def require_elf(path,expected_sha):
    if not path.is_file() or sha(path)!=expected_sha:raise ValueError('NATIVE_ELF_SHA256_MISMATCH')
    header=path.read_bytes()[:64]
    if header[:6]!=b'\x7fELF\x02\x01' or int.from_bytes(header[18:20],'little')!=183:
        raise ValueError('NATIVE_NOT_LITTLE_ENDIAN_ELF64_AARCH64')


def depth_device_access():
    devices=[]
    for entry in Path('/sys/bus/usb/devices').glob('*'):
        try:
            if (entry/'idVendor').read_text().strip().lower()!='2bc5' or (entry/'idProduct').read_text().strip().lower()!='0614':continue
            bus=int((entry/'busnum').read_text());number=int((entry/'devnum').read_text())
            node=Path('/dev/bus/usb')/f'{bus:03d}'/f'{number:03d}'
            st=node.stat()
            devices.append({'sysfs':str(entry),'node':str(node),'mode':oct(stat.S_IMODE(st.st_mode)),
                            'uid':st.st_uid,'gid':st.st_gid,
                            'access_rw':os.access(node,os.R_OK|os.W_OK)})
        except FileNotFoundError:continue
    if len(devices)!=1:raise ValueError('EXACTLY_ONE_CURRENT_GEMINI_DEPTH_DEVICE_REQUIRED:'+str(devices))
    dev=devices[0];owners=[];denied=[]
    for p in Path('/proc').glob('[0-9]*'):
        try:
            for fd in (p/'fd').iterdir():
                try:
                    if os.readlink(fd).removesuffix(' (deleted)')==dev['node']:
                        owners.append({'pid':int(p.name),'fd':fd.name})
                except FileNotFoundError:continue
        except FileNotFoundError:continue
        except PermissionError:denied.append(int(p.name))
    dev.update(owners=owners,occupancy_permission_denied_pids=denied)
    if owners:raise ValueError('GEMINI_USB_DEVICE_ALREADY_OPEN:'+str(owners))
    if denied:raise ValueError('DEVICE_OCCUPANCY_NOT_FULLY_INSPECTABLE')
    if not dev['access_rw']:raise PermissionError('CURRENT_USER_USB_RW_NOT_AVAILABLE_NO_PERMISSION_CHANGE')
    return dev


def kernel_snapshot(path):
    p=subprocess.run(['dmesg','--level=err,warn','--notime'],capture_output=True,timeout=5)
    path.write_bytes(p.stdout)
    path.with_suffix('.stderr.log').write_bytes(p.stderr)
    return {'exit_code':p.returncode,'text':p.stdout.decode(errors='replace')}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-snapshot-limit',type=int,default=0,help='Optional expensive fullpublic audits0..2; default never snapshots perframe')
    parser.add_argument('--raw-samples',type=int,default=1,help='Save first0..64 processed raw Y16 samples; default1; evidence only, not a freshness override')
    parser.add_argument('--real-obs2',action='store_true',help='Explicit one-shot OBS2 MMIO capability gate and observations only')
    parser.add_argument('--profile',type=Path,help='Parent-verified exact new R5 deployment identity JSON')
    parser.add_argument('--pause-forwarding-test',action='store_true',help='Once after5 commits, stop software forwarding400ms, inspect stale, then resume')
    parser.add_argument('--benchmark-saved-observations',type=Path,help='Pure offline saved native JSONL; no SDK/backend import')
    parser.add_argument('--benchmark-out',type=Path)
    parser.add_argument('--run-camera',action='store_true',help='Explicit native SDK ownership authorization for this invocation')
    parser.add_argument('--verify-package',action='store_true',help='Files only; no platform/device checks or SDK')
    parser.add_argument('--sdk-dir',type=Path,default=Path('/home/ubuntu/gemini_live_20261026T135635Z/sdk/lib'))
    parser.add_argument('--xml',type=Path,default=Path('/home/ubuntu/gemini_live_20261026T135635Z/sdk/config/OrbbecSDKConfig_v1.0.xml'))
    parser.add_argument('--out',type=Path)
    parser.add_argument('--duration-s',type=int,default=60)
    parser.add_argument('--session',type=int)
    args=parser.parse_args(argv)
    if args.public_snapshot_limit!=0:parser.error('compact path saves independent full native JSON; public snapshots must remain0')
    if not 0<=args.raw_samples<=64:parser.error('raw-samples must be0..64')
    package=verify_runtime()
    if args.verify_package:
        print(json.dumps(package));return 0
    if args.benchmark_saved_observations:
        parser.error('Compact saved-frame entry is benchmark_compact_saved.py; no legacy JSON replay through live entry')
    if args.real_obs2 and (not args.profile or args.session is not None):
        parser.error('REAL_OBS2_REQUIRES_LATE_BOUND_PROFILE_AND_SESSION_FROM_ACTUAL_ACTIVE_BANK')
    if args.profile and not args.real_obs2:parser.error('--profile only with explicit --real-obs2')
    if not args.run_camera:parser.error('No SDK start without explicit --run-camera')
    if args.out is None:parser.error('--out NEW_DIRECTORY is required')
    args.out=args.out.resolve();args.sdk_dir=args.sdk_dir.resolve();args.xml=args.xml.resolve()
    if not 1<=args.duration_s<=60:parser.error('duration-s must be1..60; total supervisor deadline duration+15<=75')
    session=args.session if args.session is not None else secrets.randbelow(0xfffffffe)+1
    if not 1<=session<=0xffffffff:parser.error('session must be nonzero uint32')
    args.out.mkdir(mode=0o700,parents=False,exist_ok=False)
    phase=(args.out/'supervisor.metadata.jsonl').open('x',encoding='utf8')
    def log(stage,result,**kw):
        phase.write(json.dumps({'monotonic_ns':time.monotonic_ns(),'stage':stage,'result':result,**kw},ensure_ascii=False)+'\n');phase.flush()
    # Main deadline leaves a bounded 3s TERM + 2s KILL/reap reserve.
    began=time.monotonic_ns();deadline=began+(args.duration_s+10)*1_000_000_000
    result={'schema':'GEMINI_R6_COMPACT_LINUX_OBSERVATION_V1','package':package,
            'started_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'session':session,'requested_capture_duration_s':args.duration_s,'requested_raw_samples':args.raw_samples,
            'main_deadline_s':args.duration_s+10,'final_reap_reserve_s':5,
            'SDK_started':False,'child_exit_code':None,'supervisor_timeout':False,
            'MMIO_access':False,'IIC_access':False,'PL_load':False,'RGB_started':False,
            'physical_output_permitted':False,'capture_clock_mapping':'BLOCKED','public_snapshot_limit':args.public_snapshot_limit,'producer_ownership':'PRIVATE_SINGLE_OWNER_FULL258_VALIDATED',
            'live_capture_freshness':'BLOCKED','SDK_cleanup':'UNKNOWN',
            'resource_recovery':'UNKNOWN','capture_test':'NOT_RUN','producer_test':'NOT_RUN',
            'BOARD_HEALTH':'UNKNOWN',
            'source_matrix_PL_submission':'NOT_RUN','observation_protocol':'OBS2_CAPTURE_UNVERIFIED','real_obs2_requested':args.real_obs2,'errors':[]}
    child=None;source=None;backend=None;temp=None;native=None;before=None;consumer_error=None
    metrics={'received_records':0,'produced_observations':0,'stale_observations_dropped':0,
             'fatal_producer_errors':0,'observation_records_written':0,'PL_commits':0,'eligible_observations':0,'pre_submit_stale_drops':0,'public_snapshots':0}
    timing={'producer_ns':[],'receipt_to_producer_end_ns':[],'sdk_delivery_to_producer_begin_ns':[],
            'raw_valid_ratio':[],'unknown_cells':[],'public_snapshot_ns':[]}
    first_received=last_received=None;normal_eof=False;pause_done=False;real_backend_opened=False
    try:
        log('PACKAGE','PASS',checked_files=package['checked_files'])
        if os.name!='posix' or os.uname().machine!='aarch64':raise ValueError('LINUX_AARCH64_TARGET_REQUIRED')
        expected=json.loads((ROOT/'SDK_EXPECTATIONS.json').read_text(encoding='utf8'))
        binary=ROOT/'native/gemini_live_compact.aarch64';require_elf(binary,expected['native_sha256'])
        sdk=args.sdk_dir/'libOrbbecSDK.so.1.10'
        if not sdk.is_file() or sha(sdk)!=expected['sdk_sha256']:raise ValueError('EXISTING_SDK_LIBRARY_HASH_MISMATCH')
        if not args.xml.is_file() or sha(args.xml)!=expected['config_sha256']:raise ValueError('EXISTING_SDK_CONFIG_HASH_MISMATCH')
        result['inputs']={'binary_sha256':sha(binary),'sdk_sha256':sha(sdk),'xml_sha256':sha(args.xml),
                          'sdk_dir':str(args.sdk_dir),'xml':str(args.xml),'euid':os.geteuid(),
                          'uname':list(os.uname())}
        result['depth_device']=depth_device_access();log('DEPTH_DEVICE_PREFLIGHT','PASS',device=result['depth_device'])
        before=kernel_snapshot(args.out/'kernel_before.log')
        if before['exit_code']!=0:raise ValueError('KERNEL_HEALTH_BASELINE_UNAVAILABLE')
        sys.path.insert(0,str(ROOT))
        from compact_source import CompactFrameSource,BoundCompactProducer
        
        if args.real_obs2:
            from real_backend_prepare.obs2_uio_backend import EnvironmentProfile,LinuxUIOBackend
            profile=EnvironmentProfile(**json.loads(args.profile.read_text(encoding='utf8')))
            result['profile_sha256']=sha(args.profile)
            backend=LinuxUIOBackend(profile,access_markers=False,
                emit=lambda event:log('OBS2_MMIO','EVENT',event=event))
            result['backend_environment']=backend.validate_environment()
            # Parent must pass actual loaded-image/global-inhibit/clock/health Gate.
            # One probe, this same open instance, before starting any camera SDK.
            log('OBS2_CAPABILITY_GATE','BEGIN_ONCE_NO_AUTOMATIC_RETRY')
            backend.open_confirmed_uio();real_backend_opened=True;result['MMIO_access']=True
            result['OBS2_capability']=backend.probe_once()
            state=backend.observe_status();result['OBS2_initial_status']=state
            if state['active_session']==0xffffffff:raise ValueError('OBS2_SESSION_WRAP_REQUIRES_MANUAL_ROUTE')
            session=state['active_session']+1;result['session']=session
            log('OBS2_CAPABILITY_GATE','PASS',session=session)
        else:
            from bridge_r4.backend import MockMatrixBackend
            backend=MockMatrixBackend();result['mock_policy_initial']=backend.get_status()
        temp=tempfile.TemporaryDirectory(prefix='gr5_',dir='/tmp')
        ipc_path=Path(temp.name)/'f.sock'
        source=CompactFrameSource(ipc_path,max_frame_bytes=1024,socket_buffer_bytes=4096).start()
        producer=None
        log('IPC_LISTENER','PASS',socket=str(ipc_path),euid=os.geteuid())
        native=args.out/'native';native.mkdir(mode=0o700)
        producer=BoundCompactProducer(native/'compact_identity.json',session_started_monotonic_ns=began,native_binary_sha256=expected['native_sha256'])
        cmd=[str(binary),str(args.xml),str(native),'1800',str(session),str(args.raw_samples),str(ipc_path),str(args.duration_s*1000)]
        result['command']=cmd
        env=dict(os.environ);env['LD_LIBRARY_PATH']=str(args.sdk_dir.resolve())+':'+env.get('LD_LIBRARY_PATH','')
        env['PYTHONDONTWRITEBYTECODE']='1'
        with (args.out/'sdk.stdout.log').open('xb') as stdout,(args.out/'sdk.stderr.log').open('xb') as stderr,(args.out/'observations.jsonl').open('x',encoding='utf8') as records:
            child=subprocess.Popen(cmd,stdout=stdout,stderr=stderr,env=env,cwd=native,start_new_session=True)
            result.update(SDK_started=True,pid=child.pid);log('NATIVE_CHILD_STARTED','PASS',pid=child.pid)
            while time.monotonic_ns()<deadline:
                if metrics['received_records']>=1800:consumer_error='OBSERVATION_FRAME_BUDGET';break
                try:item=source.next_frame(timeout_s=.25)
                except TimeoutError:
                    if child.poll() is not None:break
                    if first_received is None and time.monotonic_ns()-began>15_000_000_000:
                        consumer_error='FIRST_NATIVE_FRAME_TIMEOUT_15S';break
                    continue
                except EOFError as exc:
                    normal_eof=source.error is None or 'IPC_PEER_EOF' in source.error
                    log('IPC_EOF','OBSERVED',normal_peer_eof=normal_eof,error=str(exc));break
                received=time.monotonic_ns();first_received=received if first_received is None else first_received;last_received=received
                metrics['received_records']+=1
                start=time.monotonic_ns()
                try:
                    produced=producer.produce_owned(item.native,receipt_monotonic_ns=item.receipt_monotonic_ns,now_monotonic_ns=start)
                except Exception as exc:
                    if classify_producer_error(exc)=='DROP_STALE_OBSERVATION':
                        metrics['stale_observations_dropped']+=1
                        log('PRODUCER','REJECT_STALE_NOT_PUBLISHED',sequence=item.native.get('sequence'),error=str(exc));continue
                    metrics['fatal_producer_errors']+=1;consumer_error='PRODUCER:'+str(exc)
                    log('PRODUCER','FAIL',error=str(exc));break
                end=time.monotonic_ns();metrics['produced_observations']+=1
                # The full258region tree remains private, validated and owned.
                # Only immutable payloads/tiny metadata escape the fast path.
                matrix=produced.matrix;unknown=produced.unknown;summary=produced.summary()
                if (len(matrix)!=256 or len(unknown)!=32 or summary['physical_output_permitted'] is not False
                        or summary['actual_live'] is not True or summary['source_kind']!='LIVE_NATIVE_PIPE'):
                    produced.discard();consumer_error='UNEXPECTED_PRODUCER_AUTHORIZATION_OR_SHAPE';break
                if metrics['public_snapshots']<args.public_snapshot_limit:
                    audit_began=time.monotonic_ns();snapshot=produced.public_snapshot()
                    audit_path=args.out/('audit_snapshot_'+str(item.native['sequence']).zfill(6)+'.json')
                    with audit_path.open('x',encoding='utf8') as f:json.dump(snapshot.envelope,f,ensure_ascii=False,allow_nan=False)
                    del snapshot
                    audit_ns=time.monotonic_ns()-audit_began;metrics['public_snapshots']+=1
                    timing['public_snapshot_ns'].append(audit_ns)
                    log('OPTIONAL_PUBLIC_SNAPSHOT','SAVED_EXTRA_AUDIT_NOT_QUALITY_CHECK',sequence=item.native['sequence'],elapsed_ns=audit_ns)
                submit_check=time.monotonic_ns()
                # Must re-evaluate after the complete producer+envelope work.
                # This is IPC RECEIPT age only, never exposure/capture proof.
                # Match backend's conservative microsecond ceiling exactly.
                final_receipt_age_us=(submit_check-item.receipt_monotonic_ns+999)//1000
                if final_receipt_age_us>=250_000:
                    metrics['pre_submit_stale_drops']+=1
                    log('OBS2_PRE_SUBMIT','DROP_RECEIPT_EXPIRED_AFTER_FULL_PRODUCER',sequence=item.native.get('sequence'),receipt_age_ns=submit_check-item.receipt_monotonic_ns)
                    produced.discard()
                    continue
                metrics['eligible_observations']+=1
                committed=None
                if args.real_obs2:
                    try:
                        committed=produced.submit_once(backend)
                        metrics['PL_commits']+=1
                    except BaseException as exc:
                        consumer_error='OBS2_BACKEND:'+str(exc)
                        log('OBS2_BACKEND','FAIL_STOP_NO_RETRY',error=str(exc));break
                    log('OBS2_FRAME','READBACK_PASS',sequence=item.native['sequence'],generation=committed['generation'],matrix_sha256=committed['matrix_sha256'],compared_words=committed['compared_words'])
                entry={'schema':'GEMINI_R6_COMPACT_REAL_OBSERVATION_V1','source_kind':'REAL_SDK_UDS_SAME_HOST',
                       'device_identity':item.native.get('device_identity'),
                       'raw_sha256':item.native['raw_sha256'],'G6C1_packet_sha256':hashlib.sha256(item.native['compact_packet']).hexdigest(),
                       'cpp_full_validator_ns':item.native['cpp_full_validator_ns'],
                       'session':item.native['session'],'sequence':item.native['sequence'],
                       'sdk_frame_index':item.native['sdk_frame_index'],
                       'sdk_device_timestamp_us':item.native.get('sdk_device_timestamp_us'),
                       'sdk_system_timestamp_us':item.native.get('sdk_system_timestamp_us'),
                       'host_wait_begin_ns':item.native.get('host_wait_begin_ns'),
                       'host_wait_end_ns':item.native.get('host_wait_end_ns'),
                       'native_processing_begin_ns':item.native.get('processing_begin_ns'),
                       'native_processing_end_ns':item.native.get('processing_end_ns'),
                       'ipc_receipt_monotonic_ns':item.receipt_monotonic_ns,
                       'ipc_json_decode_end_ns':item.decode_end_monotonic_ns,
                       'producer_begin_ns':start,'producer_end_ns':end,'submit_check_monotonic_ns':submit_check,'receipt_age_ns_at_submit_check':submit_check-item.receipt_monotonic_ns,'receipt_age_us_ceil_at_submit_check':final_receipt_age_us,
                       'clock_domain':'CLOCK_MONOTONIC_SAME_LINUX_HOST',
                       'capture_age_ns':None,'capture_clock_mapping':'BLOCKED',
                       'matrix':list(matrix),'matrix_sha256':hashlib.sha256(matrix).hexdigest(),
                       'unknown_hex':unknown.hex(),'unknown_cells':sum(v.bit_count() for v in unknown),
                       'geometry_quality':summary['geometry_state'],
                       'raw_valid_pixels':item.native.get('raw_valid_pixels'),'raw_valid_ratio':item.native.get('raw_valid_ratio'),
                       'raw_zero_pixels':item.native.get('raw_zero_pixels'),
                       'raw_nonzero_out_of_measurement_range_pixels':item.native.get('raw_nonzero_out_of_measurement_range_pixels'),
                       'native_core_ns':item.native.get('native_stage_ns',{}).get('total_ns'),
                       'native_decode_geometry_ns':item.native.get('offline_equivalent_core_pipeline_ns'),
                       'observation_audit_scope':'FULL258_CPP_VALIDATED;FULL_NATIVE_JSON_SAVED_SEPARATELY;G6C1_CRC_AND_IDENTITY_BOUND',
                       'unknown_sha256':hashlib.sha256(unknown).hexdigest(),
                       'producer_reason':'OBSERVATION_ONLY_CAPTURE_TIME_UNVERIFIED','KPF1_quality':'UNUSABLE','KPF1_fresh':False,
                       'OBS2_packet_sha256':hashlib.sha256(produced.packet).hexdigest(),
                       'producer_packet_timestamp_scope':'ENCODED_RECEIPT_AGE_BEFORE_BEGIN_RECHECK',
                       'OBS2_readback':committed,
                       'source_matrix_submission':'OBS2_OBSERVATION_ONLY' if committed else 'NOT_SUBMITTED',
                       'MMIO_submitted':committed is not None,
                       'force_off_policy':1,'control_valid_policy':0,'physical_output_permitted':False}
                records.write(json.dumps(entry,ensure_ascii=False,allow_nan=False)+'\n');records.flush();metrics['observation_records_written']+=1
                if not args.real_obs2:produced.discard()
                timing['producer_ns'].append(end-start);timing['receipt_to_producer_end_ns'].append(end-item.receipt_monotonic_ns)
                timing['sdk_delivery_to_producer_begin_ns'].append(start-item.native['host_wait_end_ns'])
                for name in ('raw_valid_ratio','unknown_cells'):
                    if entry.get(name) is not None:timing[name].append(entry[name])
                if args.real_obs2 and args.pause_forwarding_test and not pause_done and metrics['PL_commits']>=5:
                    pause_done=True
                    before_pause=backend.observe_status();log('SOFTWARE_FORWARDING_PAUSE','BEGIN',duration_ms=400,status=before_pause)
                    time.sleep(.4)
                    expired=backend.observe_status()
                    if expired['receipt_recent'] or expired['generation']!=before_pause['generation']:
                        consumer_error='OBS2_SOFTWARE_STREAM_LOSS_NOT_OBSERVED';break
                    result['software_stream_loss_status']=expired
                    log('SOFTWARE_FORWARDING_PAUSE','STALE_CONFIRMED_RESUME_LATEST',status=expired)
                size=sum((args.out/name).stat().st_size for name in ('sdk.stdout.log','sdk.stderr.log','observations.jsonl'))
                if size>16*1024*1024:consumer_error='BOUNDED_LOG_BUDGET_16MIB';break
            if time.monotonic_ns()>=deadline:
                result['supervisor_timeout']=True;consumer_error=consumer_error or 'SUPERVISOR_DEADLINE'
            if consumer_error and child.poll() is None:
                log('NATIVE_STOP_SIGNAL','SIGTERM',reason=consumer_error);os.killpg(child.pid,signal.SIGTERM)
            remaining=max(.001,(deadline-time.monotonic_ns())/1e9)
            try:result['child_exit_code']=child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                result['supervisor_timeout']=True;log('NATIVE_STOP_SIGNAL','SIGTERM',reason='75S_TOTAL_DEADLINE')
                os.killpg(child.pid,signal.SIGTERM)
                try:result['child_exit_code']=child.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    log('NATIVE_STOP_SIGNAL','SIGKILL',reason='SIGTERM_GRACE_EXPIRED_NOT_CLEAN')
                    os.killpg(child.pid,signal.SIGKILL);result['child_exit_code']=child.wait(timeout=2)
        if args.real_obs2 and not consumer_error and metrics['PL_commits']>0:
            # No new forwarding after native ended. Inspect the same probed bank.
            time.sleep(.35)
            final_status=backend.observe_status();result['OBS2_final_after_stop']=final_status
            if final_status['receipt_recent']:consumer_error='OBS2_RECEIPT_RECENT_NOT_EXPIRED_AFTER_STOP'
            result['source_matrix_PL_submission']='PASS_OBSERVATION_ONLY' if not consumer_error else 'FAIL'
        if consumer_error:result['errors'].append(consumer_error)
    except Exception as exc:
        result['errors'].append(f'{type(exc).__name__}:{exc}');log('SUPERVISOR','FAIL',error=result['errors'][-1])
    finally:
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid,signal.SIGTERM);result['child_exit_code']=child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL);result['child_exit_code']=child.wait(timeout=2)
                result['errors'].append('FORCED_PROCESS_KILL_RESOURCE_RECOVERY_UNKNOWN')
            except Exception as exc:result['errors'].append('CHILD_FINALIZE:'+str(exc))
        if source is not None:
            result['source_stats']=dict(source.stats,slot_offered=source.slot.offered,slot_replaced=source.slot.replaced,slot_popped=source.slot.popped)
            result['source_events']=list(source.events)
            try:source.close();log('SOURCE_CLOSE','RETURNED')
            except Exception as exc:result['errors'].append('SOURCE_CLOSE:'+str(exc))
        if backend is not None:
            try:
                if args.real_obs2:
                    backend.close();log('OBS2_CLOSE','LOCAL_UNMAP_ONLY_NO_ABORT')
                else:result['mock_abort']=backend.abort('OBSERVATION_SESSION_ENDED')
            except Exception as exc:result['errors'].append('BACKEND_CLOSE:'+str(exc))
        if temp is not None:
            try:temp.cleanup()
            except Exception as exc:result['errors'].append('PRIVATE_IPC_DIR_CLEANUP:'+str(exc))
        raw=''
        for name in ('sdk.stdout.log','sdk.stderr.log'):
            p=args.out/name
            if p.is_file():raw+=p.read_text(errors='replace')+'\n'
        result['warning_counts']=warning_counts(raw);result['SDK_warning_observed']=any(result['warning_counts'].values())
        native_state=native/'session_result.json' if native else None
        if native_state is not None and native_state.is_file():
            result['native_session']=json.loads(native_state.read_text())
        result['metrics']=metrics;result['distribution']={k:distribution(v) for k,v in timing.items()}
        result['wall_ns']=time.monotonic_ns()-began
        result['consumer_observation_span_ns']=None if first_received is None else last_received-first_received
        if native is not None and (native/'metadata.jsonl').is_file():
            native_phases=[json.loads(line) for line in (native/'metadata.jsonl').read_text().splitlines() if line.strip()]
            starts=[p['host_monotonic_ns'] for p in native_phases if p['phase']=='DEPTH_START_END' and p['result']=='PASS']
            ends=[p['host_monotonic_ns'] for p in native_phases if p['phase']=='APPLICATION_CAPTURE_THREAD_JOIN']
            result['capture_phase_ns']=ends[-1]-starts[0] if starts and ends else None
        else:result['capture_phase_ns']=None
        state=result.get('native_session',{})
        phase_ns=result['capture_phase_ns']
        result['camera_fps']=state.get('captured_frames',0)*1e9/phase_ns if phase_ns else None
        result['camera_fps_denominator']='SDK_DEPTH_START_RETURN_TO_APPLICATION_CAPTURE_THREAD_JOIN'
        result['native_processing_fps']=state.get('processed_frames',0)*1e9/phase_ns if phase_ns else None
        result['producer_output_fps']=metrics['produced_observations']*1e9/phase_ns if phase_ns else None
        result['eligible_observation_fps']=metrics['eligible_observations']*1e9/phase_ns if phase_ns else None
        result['PL_commit_fps']=metrics['PL_commits']*1e9/phase_ns if phase_ns else None
        result['capture_test']='PASS' if state.get('captured_frames',0)>0 else ('FAIL' if result['SDK_started'] else 'NOT_RUN')
        result['producer_test']='PASS' if metrics['produced_observations']>0 else ('FAIL' if result['SDK_started'] else 'NOT_RUN')
        result['continuous_duration_pass']=bool(phase_ns is not None and phase_ns>=args.duration_s*1_000_000_000)
        result['normal_peer_eof_observed']=normal_eof
        if before is not None:
            try:
                after=kernel_snapshot(args.out/'kernel_after.log')
                result['new_kernel_warnings_errors']=[x for x in after['text'].splitlines() if x and x not in set(before['text'].splitlines())]
                result['kernel_after_exit_code']=after['exit_code']
                critical=re.compile(r'(?:mmc|spi).*tim(?:e|ed).*out|I/O error|external abort|SError|hung task|blocked for more|BUG:|Oops:',re.I)
                result['critical_new_kernel_errors']=[x for x in result['new_kernel_warnings_errors'] if critical.search(x)]
                result['BOARD_HEALTH']='FAIL' if result['critical_new_kernel_errors'] or after['exit_code'] else 'PASS'
                if result['BOARD_HEALTH']=='FAIL':result['errors'].append('NEW_CRITICAL_KERNEL_ERROR_OR_HEALTH_UNAVAILABLE')
            except Exception as exc:result['errors'].append('KERNEL_AFTER:'+str(exc))
        dataflow_ok=(result['capture_test']=='PASS' and result['producer_test']=='PASS' and result['continuous_duration_pass']
                     and result['child_exit_code']==0 and not result['supervisor_timeout'] and not result['errors']
                     and (not args.real_obs2 or (metrics['PL_commits']>0 and result['source_matrix_PL_submission']=='PASS_OBSERVATION_ONLY')))
        result['LIVE_GEMINI_TO_PRODUCER']='PASS' if dataflow_ok else ('FAIL' if result['SDK_started'] else 'BLOCKED')
        result['acceptance_scope']='MINIMAL_TRANSACTION_PATH_ONLY_NOT_CONTINUOUS_SERVICE_OR_PHYSICAL_OUTPUT'
        result['LIVE_GEMINI_TO_PL_OBSERVATION']=('PASS' if dataflow_ok and metrics['PL_commits']>0 else 'FAIL') if args.real_obs2 else 'NOT_RUN'
        code=0 if dataflow_ok else 70 if result['SDK_started'] else 73
        if dataflow_ok and result['SDK_warning_observed']:code=71
        result['supervisor_exit_code']=code
        log('SESSION_END',result['LIVE_GEMINI_TO_PRODUCER'],exit_code=code,SDK_cleanup='UNKNOWN')
        phase.close()
        result['archive_policy']='GZIP_LEVEL1_AFTER_SDK_EXIT_NOT_IN_FRAME_LATENCY'
        result['output_sha256']={str(p.relative_to(args.out)):sha(p) for p in args.out.rglob('*') if p.is_file()}
        (args.out/'SUPERVISOR_RESULT.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
        archive=args.out.parent/(args.out.name+'.tgz')
        archive_begin_ns=time.monotonic_ns()
        with archive.open('xb') as archive_file:
            with tarfile.open(fileobj=archive_file,mode='w:gz',compresslevel=1) as tar:
                for p in sorted(args.out.rglob('*')):
                    if p.is_file():tar.add(p,arcname=str(p.relative_to(args.out)),recursive=False)
        output={'result_file':str(args.out/'SUPERVISOR_RESULT.json'),'archive':str(archive),'archive_bytes':archive.stat().st_size,
                'archive_sha256':sha(archive),'archive_elapsed_ns':time.monotonic_ns()-archive_begin_ns,'LIVE_GEMINI_TO_PRODUCER':result['LIVE_GEMINI_TO_PRODUCER'],
                'captured':state.get('captured_frames',0),'processed':state.get('processed_frames',0),
                'produced':metrics['produced_observations'],'stale_dropped':metrics['stale_observations_dropped'],
                'exit_code':code,'SDK_cleanup':'UNKNOWN','MMIO_access':result['MMIO_access'],'PL_commits':metrics['PL_commits'],'IIC_access':False,'physical_output_permitted':False}
        (args.out/'RETURN_SUMMARY.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf8')
        print(json.dumps(output,ensure_ascii=False),flush=True)
    return code


if __name__=='__main__':
    raise SystemExit(main())
