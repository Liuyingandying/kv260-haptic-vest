"""One bounded D1 live session; inert on import.

Default digital mode requires V+ disconnected. The explicit supervised single
motor bench instead requires an out/boot-bound external operator attestation;
it never asserts production readiness or a proven physical shutdown guarantee.
No image loading, permission changes, production FRESH flags, or retry. Parent
STOP_MMIO prohibits further accesses; an outstanding AXI load cannot be cancelled.
"""
from __future__ import annotations
import argparse, dataclasses, hashlib, json, mmap, os, platform, signal
from contextlib import ExitStack
from pathlib import Path
import re, subprocess, sys, tempfile, time

ROOT=Path(__file__).resolve().parents[1]
DESIGN_ID=0x52364431
MATRIX_DESIGN_ID=0x52364d32
HEALTHY=0xf0f0
NATIVE_CAPTURE_S=8
SUPPORTED_DURATIONS_S=(8,60)
FULL_OFF_READBACK_BUDGET_S=35
PARENT_ADDITIONAL_BUDGET_S=55
# Digital-only wall-clock budget for one returned AXI transaction, including
# durable forensic logging. The observed SD/logging stall was 1.634 s AFTER
# a READ32_RETURNED event. Keep this below the existing 3 s PL local lease;
# this is unrelated to, and does not relax, production capture freshness.
D1_TRANSACTION_BUDGET_NS=2_000_000_000
SESSION_LEDGER_PATH=Path('/home/ubuntu/kv260_r6_d1_session_ledger.jsonl')
SESSION_U32_MAX=0xffffffff
DIGITAL_WINDOW_RENEWAL_NS=30_000_000_000
HARDWARE_SIGNAL_WINDOW_NS=50_000_000_000
SIGNAL_WINDOW_CLOCK_HZ=100_000_000
SIGNAL_WINDOW_LIMIT_CYCLES=5_000_000_000
MATRIX_SOURCE_STALL_NS=750_000_000


def parent_deadline_s(capture_s):
    require(capture_s in (*SUPPORTED_DURATIONS_S,600),'CAPTURE_MUST_BE_EIGHT_OR_SIXTY_SECONDS')
    return capture_s+PARENT_ADDITIONAL_BUDGET_S

ARM,PERMIT,PAGES=1<<30,1<<23,15
ALLOWED=ARM|PERMIT|PAGES
GPIO_SHA='356122bb910cfe28f3f423108c5bbc000ef716e18e0c1c0b6ca76bccc7ecb365'
SDK_SHA='18c09b76a0f2237d127ebf9fa131ee2ef0f3ceb5fc06f2639f636e170b6731a1'
XML_SHA='3f46bc5ff10ef4bea78def33d0b01e105292c9de3a70f7d3198084a8584a5073'


def require(ok,reason):
    if not ok:raise RuntimeError(reason)


def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def require_no_protocol_fault(value):
    require(type(value) is int and 0<=value<=SESSION_U32_MAX and not value&0x80,'D1_PROTOCOL_FAULT_OR_STATUS_INVALID_STOP_BEFORE_CAMERA')


def session_ledger_floor(raw):
    if not raw:return 0
    require(raw.endswith(b'\n'),'SESSION_LEDGER_INCOMPLETE_LINE_STOP')
    def unique_object(pairs):
        require(len(pairs)==len({k for k,v in pairs}),'SESSION_LEDGER_DUPLICATE_FIELD_STOP')
        return dict(pairs)
    previous=0
    for line in raw.decode('utf8').splitlines():
        require(bool(line),'SESSION_LEDGER_EMPTY_LINE_STOP')
        row=json.loads(line,object_pairs_hook=unique_object)
        require(isinstance(row,dict) and set(row)=={'schema','reserved_session','pl_active_session','previous_reserved_session','boot_id','run_output','pid','reserved_monotonic_ns'}
                and row.get('schema')=='D1_SESSION_RESERVATION_V1','SESSION_LEDGER_SCHEMA_STOP')
        require(all(type(row[k]) is int and 0<=row[k]<=SESSION_U32_MAX for k in ('reserved_session','pl_active_session','previous_reserved_session')),
                'SESSION_LEDGER_U32_INVALID_STOP')
        require(row['previous_reserved_session']==previous and row['reserved_session']==max(previous,row['pl_active_session'])+1,
                'SESSION_LEDGER_NONMONOTONIC_STOP')
        require(isinstance(row['boot_id'],str) and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',row['boot_id']) is not None
                and isinstance(row['run_output'],str) and row['run_output'].startswith('/')
                and type(row['pid']) is int and row['pid']>0 and type(row['reserved_monotonic_ns']) is int and row['reserved_monotonic_ns']>=0,
                'SESSION_LEDGER_CONTEXT_INVALID_STOP')
        previous=row['reserved_session']
    return previous


class SessionLedger:
    """One held OS lock; reservation records only, never a hardware-health certificate."""
    def __init__(self,path,lock_api):
        import stat
        self.path=Path(path);self.lock_api=lock_api;self.fd=None
        require(self.path.is_absolute(),'SESSION_LEDGER_ABSOLUTE_PATH_REQUIRED')
        fd=os.open(self.path,os.O_RDWR|os.O_CREAT|os.O_APPEND|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0),0o600)
        try:
            require(stat.S_ISREG(os.fstat(fd).st_mode),'SESSION_LEDGER_REGULAR_FILE_REQUIRED')
            try:lock_api.flock(fd,lock_api.LOCK_EX|lock_api.LOCK_NB)
            except OSError as exc:raise RuntimeError('SESSION_LEDGER_LOCK_HELD_OR_UNAVAILABLE_STOP') from exc
            self.fd=fd
        except BaseException:os.close(fd);raise
    def __enter__(self):return self
    def __exit__(self,*exc):self.close()
    def close(self):
        if self.fd is not None:
            fd=self.fd;self.fd=None
            try:self.lock_api.flock(fd,self.lock_api.LOCK_UN)
            finally:os.close(fd)
    def _sync_parent(self):
        fd=os.open(self.path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    def reserve(self,active_session,boot_id,run_output):
        require(self.fd is not None,'SESSION_LEDGER_LOCK_NOT_HELD')
        require(type(active_session) is int and 0<=active_session<=SESSION_U32_MAX,'PL_ACTIVE_SESSION_U32_INVALID_STOP')
        os.lseek(self.fd,0,os.SEEK_SET);chunks=[]
        while True:
            chunk=os.read(self.fd,65536)
            if not chunk:break
            chunks.append(chunk)
        previous=session_ledger_floor(b''.join(chunks));session=max(active_session,previous)+1
        require(session<=SESSION_U32_MAX,'SESSION_U32_EXHAUSTED_STOP_NO_WRAP')
        row={'schema':'D1_SESSION_RESERVATION_V1','reserved_session':session,'pl_active_session':active_session,
             'previous_reserved_session':previous,'boot_id':boot_id,'run_output':str(run_output),
             'pid':os.getpid(),'reserved_monotonic_ns':time.monotonic_ns()}
        payload=(json.dumps(row,separators=(',',':'))+'\n').encode('utf8')
        # Validate our exact row and chain before append. Short writes stay invalid and stop later runs.
        session_ledger_floor(b''.join(chunks)+payload)
        require(os.write(self.fd,payload)==len(payload),'SESSION_LEDGER_SHORT_APPEND_STOP')
        os.fsync(self.fd);self._sync_parent()
        return row


def open_session_ledger():
    import fcntl
    return SessionLedger(SESSION_LEDGER_PATH,fcntl)


def source_index_arg(value):
    try:index=int(value)
    except (ValueError,TypeError) as exc:raise argparse.ArgumentTypeError('source-index must be 0..255') from exc
    if not 0<=index<=255:raise argparse.ArgumentTypeError('source-index must be 0..255')
    return index


def extended_mode(args):return getattr(args,'extended_session_s',None) is not None


def session_duration_s(args):return args.extended_session_s if extended_mode(args) else args.duration_s


def extended_module():
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from runtime_night import extended_session
    return extended_session


def validate_window_mode(args):
    if extended_mode(args):
        require(args.extended_session_s==600 and args.duration_s==8 and args.renew_digital_window,
                'EXTENDED600_REQUIRES_DEFAULT_DURATION8_AND_EXPLICIT_RENEWAL')
        require(args.matrix_mode and args.supervised_matrix and args.matrix_cell_limit==128,
                'EXTENDED600_ONLY_EXPLICIT_SUPERVISED128_MATRIX_AFTER_ACTUAL_VERIFICATION')
        require(args.native_sha256==extended_module().NATIVE_SHA,'EXTENDED600_EXACT_CONTINUOUS_NATIVE_SHA_REQUIRED')
        return
    require((args.duration_s==60)==args.renew_digital_window,'SIXTY_SECONDS_REQUIRES_EXPLICIT_SAFE_WINDOW_RENEWAL_AND_ONLY_SIXTY')


def window_renewal_due(started_ns,now_ns):
    age=now_ns-started_ns
    require(0<=age<HARDWARE_SIGNAL_WINDOW_NS,'HARDWARE_WINDOW_EXPIRED_NO_AUTOMATIC_REARM')
    return age>=DIGITAL_WINDOW_RENEWAL_NS


def native_evidence_ledger(state,duration_s,raw_limit=64):
    require(duration_s in SUPPORTED_DURATIONS_S and raw_limit==64 and state.get('raw_samples_requested')==raw_limit,'EXACT_DURATION_AND_RAW_BUDGET_REQUIRED')
    keys=('processed_frames','evidence_submitted','evidence_written','evidence_pending','raw_samples_saved')
    require(all(type(state.get(k)) is int and state[k]>=0 for k in keys),'NATIVE_EVIDENCE_COUNTS_INVALID')
    processed=state['processed_frames'];saved=state['raw_samples_saved']
    require(processed>0 and state['evidence_submitted']==state['evidence_written']==processed
            and state['evidence_pending']==0 and state.get('evidence_error')=='','NATIVE_EVIDENCE_DRAIN_INCOMPLETE')
    require(saved==min(processed,raw_limit),'NATIVE_BOUNDED_RAW_COUNT_MISMATCH')
    require(type(state.get('full_raw_evidence')) is bool and state['full_raw_evidence']==(saved==processed),'NATIVE_FULL_RAW_LEDGER_INCONSISTENT')
    if duration_s==8:require(state['full_raw_evidence'] and saved==processed,'EIGHT_SECOND_FULL_RAW_REQUIRED')
    return {'mode':'FULL_RAW_REQUIRED' if duration_s==8 else 'BOUNDED_RAW_SAMPLING',
            'processed_frames':processed,'evidence_submitted':state['evidence_submitted'],'evidence_written':state['evidence_written'],
            'evidence_pending':0,'evidence_error':'','raw_samples_saved':saved,'raw_sample_limit':raw_limit,
            'raw_sequence_scope':'ALL_PROCESSED' if duration_s==8 else 'FIRST_MIN_PROCESSED_64_ORDINALS',
            'full_raw_evidence':state['full_raw_evidence'],'full_JSON_compact_and_raw_SHA_expected_frames':processed,
            'raw_after_limit':'NOT_SAVED;PER_FRAME_RAW_SHA_AND_FULL_JSON_RETAINED' if duration_s==60 else 'BUDGET_EXHAUSTION_REJECTED'}


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profile',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--native',type=Path,required=True)
    p.add_argument('--native-sha256',required=True)
    p.add_argument('--sdk-library',type=Path,required=True)
    p.add_argument('--xml',type=Path,required=True)
    p.add_argument('--gpio-helper',type=Path,required=True)
    p.add_argument('--duration-s',type=int,default=NATIVE_CAPTURE_S,choices=list(SUPPORTED_DURATIONS_S))
    p.add_argument('--extended-session-s',type=int,choices=[600],help='Explicit600s supervised128 session using one continuous native; NOT unbounded. Requires new operator proof and30s renewal')
    p.add_argument('--source-index',type=source_index_arg,default=None,help='Optional fixed source cell 0..255, valid/nonUNKNOWN/nonzero required from first frame')
    p.add_argument('--renew-digital-window',action='store_true',help='Required for60s: safely FULL_OFF/disarm/reacquire every30s while the same camera/native session continues')
    p.add_argument('--raw-samples',type=int,default=64,choices=[64])
    p.add_argument('--run-once',action='store_true')
    power=p.add_mutually_exclusive_group()
    power.add_argument('--vplus-disconnected',action='store_true')
    power.add_argument('--supervised-single-motor',action='store_true',help='Explicit human-attested single motor bench, not production permission')
    power.add_argument('--supervised-matrix',action='store_true',help='Explicit V4 operator-supervised matrix bench; requires newR6M2 image')
    p.add_argument('--matrix-mode',action='store_true',help='Opt-in DGM2 ABI; rejects oldD1 before MMIO')
    p.add_argument('--matrix-cell-limit',type=int,choices=[8,32,64,128],default=128)
    p.add_argument('--output-mask',type=lambda x:int(x,0),default=0xf0f0)
    p.add_argument('--level-cap',type=int,choices=[1],default=1)
    p.add_argument('--operator-attestation',type=Path,help='Out/boot-bound external operator confirmation; never inferred from a GPIO flag')
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    return p


def supervised_module():
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from runtime_night import supervised_single
    return supervised_single


def matrix_module():
    if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
    from runtime_night import matrix_bench
    return matrix_bench


def load_operator_context(args,*,consume=False,boot_id=None):
    if getattr(args,'matrix_mode',False):
        require(args.source_index is None and not args.supervised_single_motor,'MATRIX_MODE_HAS_EXPLICIT_SCOPE_NOT_SINGLE_SOURCE')
        require(args.output_mask==0xf0f0 and args.level_cap==1,'MATRIX_CURRENT_F0F0_CAP1_REQUIRED')
        if not args.supervised_matrix:
            require(args.vplus_disconnected and args.operator_attestation is None,'MATRIX_EXPLICIT_POWER_PROCEDURE_REQUIRED')
            return None
        require(not args.vplus_disconnected and args.operator_attestation is not None,'MATRIX_OPERATOR_ATTESTATION_REQUIRED')
        if boot_id is None:boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        return matrix_module().load_attestation(args.operator_attestation,out=args.out,duration_s=session_duration_s(args),
            stage_cells=args.matrix_cell_limit,output_mask=args.output_mask,level_cap=args.level_cap,
            test_kind='LIVE_MATRIX_CONTINUOUS' if extended_mode(args) else 'LIVE_MATRIX',boot_id=boot_id,consume=consume)
    require(not getattr(args,'supervised_matrix',False),'SUPERVISED_MATRIX_REQUIRES_EXPLICIT_MATRIX_MODE')
    supervised=getattr(args,'supervised_single_motor',False)
    proof_path=getattr(args,'operator_attestation',None)
    if not supervised:
        require(proof_path is None,'OPERATOR_PROOF_REQUIRES_EXPLICIT_SUPERVISED_MODE')
        return None
    require(not args.vplus_disconnected and proof_path is not None,
            'EXPLICIT_SUPERVISED_MODE_AND_OPERATOR_ATTESTATION_REQUIRED')
    if boot_id is None:boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return supervised_module().load_attestation(proof_path,out=args.out,
        duration_s=args.duration_s,source_index=args.source_index,
        boot_id=boot_id,consume=consume)


def safe_value(value):
    if value is None or type(value) in (bool,int,float,str):return value
    if isinstance(value,Path):return str(value)
    if isinstance(value,(bytes,bytearray,memoryview)):return {'python_type':type(value).__name__,'bytes':len(value)}
    if isinstance(value,(list,tuple)):return [safe_value(x) for x in value]
    if isinstance(value,dict):return {str(k):safe_value(v) for k,v in value.items()}
    if dataclasses.is_dataclass(value):return {f.name:safe_value(getattr(value,f.name)) for f in dataclasses.fields(value)}
    result={'python_type':type(value).__name__}
    identity=getattr(value,'__dict__',{}).get('design_id')
    if type(identity) is int:result['design_id']=identity
    return result


def verify_package(root):
    root=Path(root);manifest=json.loads((root/'PACKAGE_MANIFEST.json').read_text(encoding='utf8'))
    require(manifest.get('schema')=='R6_D1_DEPLOY_PACKAGE_V1','EXACT_D1_PACKAGE_MANIFEST_REQUIRED')
    require(isinstance(manifest.get('files'),dict) and 1<=len(manifest['files'])<=64,'PACKAGE_FILE_COUNT_BOUND')
    for relative,expected in manifest['files'].items():
        rel=Path(relative);p=root/rel
        require(not rel.is_absolute() and '..' not in rel.parts and p.is_file() and not p.is_symlink(),'PACKAGE_FILE_PATH_INVALID:'+relative)
        require(p.stat().st_size==expected['bytes'] and sha(p)==expected['sha256'],'PACKAGE_FILE_SHA_OR_SIZE_MISMATCH:'+relative)
    return {'manifest_sha256':sha(root/'PACKAGE_MANIFEST.json'),'checked_files':len(manifest['files']),
            'supports_digital_window_renewal':manifest.get('supports_digital_window_renewal') is True,
            'digital_window_renewal_interval_s':manifest.get('digital_window_renewal_interval_s'),
            'hardware_signal_window_s':manifest.get('hardware_signal_window_s')}


class Journal:
    def __init__(self,out,bounded=False):
        self.out=out;self.stop=out/'STOP_MMIO';self.fp=extended_module().RotatingLog(out/'events.jsonl') if bounded else (out/'events.jsonl').open('x',encoding='utf8')
        self.pending=False;self.failed=False
    def event(self,stage,**fields):
        self.fp.write(json.dumps(safe_value({'monotonic_ns':time.monotonic_ns(),'stage':stage,**fields}),ensure_ascii=False)+'\n');self.fp.flush()
    def gate(self):require(not self.stop.exists() and not self.failed,'MMIO_STOP_LATCHED_NO_FURTHER_ACCESS')
    def backend(self,event):
        self.gate();stage=event.get('stage','BACKEND')
        if stage.endswith('_BEGIN') and ('READ32' in stage or 'WRITE32' in stage):self.pending=True
        self.event('BACKEND',event=event)
        if stage.endswith('_RETURNED') and ('READ32' in stage or 'WRITE32' in stage):self.pending=False
    def call(self,stage,fn,*args):
        self.gate();self.pending=True;self.event(stage+'_BEGIN',args=list(args),software_timeout_cannot_cancel_AXI=True)
        try:value=fn(*args)
        except BaseException:
            self.failed=True;raise
        self.pending=False;self.event(stage+'_RETURNED',value=value);return value


class LoggedNative:
    def __init__(self,inner,log):self.inner,self.log=inner,log
    def read(self,off):return self.log.call('GPIO_LDR',self.inner.read,off)
    def write(self,off,value):return self.log.call('GPIO_STR',self.inner.write,off,value)


def write_exclusive_json(path,record):
    # Not a rotating log: preserve the original error before any slow cleanup.
    with Path(path).open('x',encoding='utf8') as fp:
        json.dump(safe_value(record),fp,ensure_ascii=False,indent=2);fp.flush();os.fsync(fp.fileno())


def save_first_error(out,exc,log,gpio,backend,context):
    transaction=getattr(backend,'transaction',None)
    returned_safe=not log.pending and not log.failed and not log.stop.exists()
    record={'schema':'D1_FIRST_ERROR_V1','monotonic_ns':time.monotonic_ns(),
        'exception_type':type(exc).__name__,'error':str(exc),'code':getattr(exc,'code',type(exc).__name__),
        'details':safe_value(getattr(exc,'details',{})),
        'identity':safe_value(getattr(transaction,'identity',{})),
        'backend_first_error':safe_value(getattr(transaction,'failed',None)),
        'MMIO_pending':log.pending,'MMIO_failed':log.failed,'STOP_MMIO':log.stop.exists(),
        'context':safe_value(context),'last_window_acquire':safe_value(getattr(gpio,'last_window_acquire',None)),
        'diagnostics':'RETURNED_ONLY_GPIO_SNAPSHOT_PLANNED' if returned_safe and gpio is not None and gpio.owned else 'SKIPPED_NO_SAFE_RETURNED_MMIO_STATE'}
    target=Path(out)/'FIRST_ERROR.json'
    if target.exists():return {'status':'ORIGINAL_FIRST_ERROR_PRESERVED','path':str(target)}
    write_exclusive_json(target,record)
    # Primary evidence is already durable if any following diagnostic never returns.
    if returned_safe and gpio is not None and gpio.owned:
        diagnostic={'schema':'D1_FIRST_ERROR_RETURNED_DIAGNOSTICS_V1','first_error_monotonic_ns':record['monotonic_ns']}
        try:
            diagnostic['window']=gpio.window_snapshot('FIRST_RETURNED_ERROR')
            diagnostic['permit_page13']=gpio.page(13)
            diagnostic['guard_page14']=gpio.page(14)
            gpio.page(0);diagnostic['status']='RETURNED_GPIO_SNAPSHOT_ONLY'
        except BaseException as diagnostic_error:
            diagnostic.update(status='DIAGNOSTIC_FAILED_NO_FURTHER_DIAGNOSTIC_ACCESS',
                error=f'{type(diagnostic_error).__name__}:{diagnostic_error}')
        write_exclusive_json(Path(out)/'FIRST_ERROR_DIAGNOSTICS.json',diagnostic)
    return {'status':'SAVED','path':str(target)}


class D1Gpio:
    def __init__(self,io,log,expected_id=DESIGN_ID):
        require(expected_id in (DESIGN_ID,MATRIX_DESIGN_ID),'EXPLICIT_REVIEWED_RUNTIME_ID_REQUIRED')
        self.io,self.log,self.expected_id=io,log,expected_id;self.data=0;self.owned=False;self.armed=False
        self.last_window_acquire=None
    def wr(self,off,value):
        require((off==8 and not value&~ALLOWED) or (off==12 and value|ALLOWED==0xffffffff),'GPIO_WORD_OR_MASK_FORBIDDEN')
        self.io.write(off,value)
    def page(self,n):
        require(0<=n<=15,'PAGE_RANGE');self.data=(self.data&~PAGES)|n;self.wr(8,self.data)
        require(self.io.read(8)&15==n,'PAGE_SELECTOR_NOT_READ_BACK');return self.io.read(0)
    def window_snapshot(self,label):
        # Page selection changes only selector bits, never ARM/PERMIT. A failed
        # or outstanding access prohibits even these diagnostic reads.
        self.log.gate();require(not self.log.pending,'WINDOW_SNAPSHOT_PENDING_MMIO_FORBIDDEN')
        flags=self.page(5);high_before=self.page(7);low=self.page(6);high_after=self.page(7)
        require(high_before==high_after,'WINDOW_COUNTER_TORN_NO_RETRY')
        snapshot={'label':label,'monotonic_ns':time.monotonic_ns(),'page5_raw':flags,
            'window_active':bool(flags&1),'window_expired':bool(flags&2),'arm_synced':bool(flags&4),
            'arm_request':bool(flags&32),'elapsed_cycles':(high_after<<32)|low,
            'high_before':high_before,'low':low,'high_after':high_after,
            'nominal_clock_hz':SIGNAL_WINDOW_CLOCK_HZ,'window_limit_cycles':SIGNAL_WINDOW_LIMIT_CYCLES}
        self.page(0);self.log.event('D1_ACTUAL_WINDOW_SNAPSHOT',snapshot=snapshot)
        return snapshot
    def acquire(self):
        require(self.io.read(12)==0xffffffff,'FRESH_GPIO_ALL_INPUTS_REQUIRED')
        require(self.io.read(0)==self.expected_id,'D1_PAGE0_ID_REQUIRED')
        self.wr(8,0);self.wr(12,0xffffffff&~ALLOWED);self.owned=True
        require(self.page(14)&15==0,'GUARD_NOT_OFF')
        require(self.page(13)&3==0,'PERMIT_REQUEST_NOT_OFF')
        require(self.page(15)&1==0,'DIAGNOSTIC_OWNER_ALREADY_PRESENT')
        before=self.window_snapshot('BEFORE_ARM')
        self.last_window_acquire={'before':before,'after':None,'reset_verified':False}
        require(not before['page5_raw']&0x27,'WINDOW_NOT_INACTIVE_UNEXPIRED_AND_ARM_LOW_BEFORE_ACQUIRE')
        arm_started_ns=time.monotonic_ns()
        self.data=ARM;self.wr(8,self.data);self.armed=True
        end=time.monotonic()+1
        while time.monotonic()<end:
            state=self.page(14)
            if state&15 in (8,10):break
            time.sleep(.001)
        require(state&15==8 and state&64 and not state&128,'D1_PAD_GUARD_NOT_RUN')
        after=self.window_snapshot('AFTER_ARM')
        self.last_window_acquire['after']=after
        # At100MHz an observed fresh start must fit its actual host bracket
        # (+100ms read/log tolerance), and never exceed2s of the unchanged50s lease.
        host_elapsed_ns=after['monotonic_ns']-arm_started_ns
        maximum_cycles=min(2*SIGNAL_WINDOW_CLOCK_HZ,
            ((host_elapsed_ns+100_000_000)*SIGNAL_WINDOW_CLOCK_HZ)//1_000_000_000)
        require(host_elapsed_ns>=0 and after['page5_raw']&0x27==0x25
                and 0<=after['elapsed_cycles']<=maximum_cycles<SIGNAL_WINDOW_LIMIT_CYCLES,
                'ACTUAL_WINDOW_DID_NOT_RESET_AND_ACTIVATE_NO_RENEWAL')
        self.last_window_acquire.update(reset_verified=True,host_arm_elapsed_ns=host_elapsed_ns,
            maximum_fresh_elapsed_cycles=maximum_cycles)
        self.page(0);self.log.event('D1_PAD_RUN',guard=state)
    def permit(self,on):
        self.data=(self.data|PERMIT) if on else self.data&~PERMIT;self.wr(8,self.data)
        end=time.monotonic()+.2
        while time.monotonic()<end:
            value=self.page(13)
            if bool(value&2)==on:break
            time.sleep(.001)
        require(bool(value&2)==on,'D1_PERMIT_SYNCHRONIZER_DID_NOT_FOLLOW')
        if on:require(value&4,'D1_RUNTIME_PERMIT_NOT_GRANTED')
        self.page(0);self.log.event('D1_PERMIT',requested=on,page13=value)
    def disarm(self):
        self.data&=~(ARM|PERMIT);self.wr(8,self.data);self.armed=False
        end=time.monotonic()+.7
        while time.monotonic()<end:
            state=self.page(14)
            if state&15 in (0,10) and not state&0x70:break
            time.sleep(.001)
        clean=state&15==0 and not state&0xf0 and bool(state&4096)
        # Returned digital faults may leave HOLD; disable requests/directions without clearing that fault.
        self.data=0;self.wr(8,0);self.wr(12,0xffffffff)
        require(self.io.read(12)==0xffffffff,'TRI_NOT_RESTORED');self.owned=False
        self.log.event('D1_PAD_OFF_TRI_INPUT',guard=state,clean_off=clean)
        require(clean,'GUARD_NOT_CLEAN_OFF_REQUESTS_DISABLED_TRI_RESTORED')


def current_status(transport,log):
    names={'frame_count':0,'burst_count':4,'flags':8,'init_done':0x28,'online_mask':0x2c,
           'runtime_error_mask':0x34,'off_pending':0x38,'fault_latched':0x3c,
           'i2c_error_count':0x40,'timeout_count':0x50,'terminal_flags':0x60}
    return {k:log.call('STATUS_LDR',transport.read_status32,v) for k,v in names.items()}


def require_health(status,baseline=None):
    require(status['online_mask']&0xffff==HEALTHY,'D1_EXPECTED_EXACT_F0F0_ONLINE_MASK')
    require(status['init_done']&1 and not status['fault_latched'],'D1_INIT_OR_FAULT_GATE')
    require(not status['runtime_error_mask']&HEALTHY,'HEALTHY_BLOCK_RUNTIME_ERROR')
    if baseline is not None:
        require(status['i2c_error_count']==baseline['i2c_error_count'] and status['timeout_count']==baseline['timeout_count'],'NEW_I2C_ERROR_OR_TIMEOUT_NO_RETRY')


def acquire_digital_window(gpio,transport,log,require_zero_iic=False):
    started_ns=time.monotonic_ns() # Conservative: hardware authorization has not started yet.
    gpio.acquire();until=time.monotonic()+2
    while time.monotonic()<until:
        baseline=current_status(transport,log)
        if baseline['init_done']&1:break
        time.sleep(.005)
    require_health(baseline)
    if require_zero_iic:require(not baseline['i2c_error_count'] and not baseline['timeout_count'],'RENEW_INITIALIZATION_IIC_ERRORS_NO_RETRY')
    gpio.permit(True)
    return started_ns,baseline


def ordered_revoke_disarm(transport,native,gpio,log,baseline=None):
    gpio.permit(False);end=time.monotonic()+1.5;safe=False
    while time.monotonic()<end:
        status=current_status(transport,log)
        if baseline is not None:require_health(status,baseline)
        mail=log.call('MAILBOX_LDR',transport.read32,4);pins=native.read(8)
        if (not status['off_pending']&HEALTHY and not status['flags']&0x1e and pins&(1<<14)
                and mail&8 and not mail&4):safe=True;break
        time.sleep(.005)
    require(safe,'HEALTHY_FULL_OFF_QUEUE_OR_PIPELINE_NOT_IDLE')
    gpio.disarm();return status


def renew_digital_window(transport,native,gpio,log,baseline,ledger,out,started_ns,record):
    record.update(status='REVOKING',previous_window_started_ns=started_ns,renewal_started_ns=time.monotonic_ns())
    require(window_renewal_due(started_ns,record['renewal_started_ns']),'WINDOW_RENEWAL_NOT_DUE')
    record['actual_window_before_revoke']=gpio.window_snapshot('BEFORE_RENEWAL_REVOKE')
    before=record['actual_window_before_revoke']
    require(before['window_active'] and not before['window_expired'] and before['arm_synced']
            and before['elapsed_cycles']<SIGNAL_WINDOW_LIMIT_CYCLES,'ACTUAL_OLD_WINDOW_NOT_ACTIVE_NO_REARM')
    record['revoke_status']=ordered_revoke_disarm(transport,native,gpio,log,baseline)
    # Never rearm after a delayed/failed close crosses the old hardware window.
    window_renewal_due(started_ns,time.monotonic_ns());log.gate()
    mail=log.call('RENEW_MAILBOX_LDR',transport.read32,4);require_no_protocol_fault(mail)
    active=log.call('RENEW_ACTIVE_SESSION_LDR',transport.read32,0x10)
    reservation=ledger.reserve(active,Path('/proc/sys/kernel/random/boot_id').read_text().strip(),out)
    record['session_reservation']={'ledger_path':str(ledger.path),**reservation}
    log.event('D1_RENEW_SESSION_RESERVED_DURABLE',**record['session_reservation'])
    window_renewal_due(started_ns,time.monotonic_ns())
    new_start,new_baseline=acquire_digital_window(gpio,transport,log,require_zero_iic=True)
    record['actual_window_reacquire']=gpio.last_window_acquire
    require(gpio.last_window_acquire['reset_verified']
            and gpio.last_window_acquire['after']['elapsed_cycles']<before['elapsed_cycles'],
            'ACTUAL_RENEWAL_COUNTER_NOT_RESET_STOP_NO_RETRY')
    generations={name:log.call('RENEW_GENERATION_LDR',transport.read32,offset) for name,offset in (('accepted_generation',8),('actual_haptic_active_generation',0x28))}
    record.update(status='SAFE_WINDOW_REARMED_WITH_CONTROL_GAP',new_window_started_ns=new_start,
                  new_baseline=new_baseline,generations_before_next_fresh_frame=generations,
                  ready_monotonic_ns=time.monotonic_ns(),PCA_register_readback='NOT_RUN_DURING_RENEWAL',
                  FULL_OFF_scope='HARDWARE_QUEUE_IDLE_NO_NEW_IIC_ERROR;FINAL_EIGHT_REGISTER_READBACK_SEPARATE')
    log.event('D1_SAFE_WINDOW_RENEWED',record=record)
    return new_start,new_baseline,reservation,record['ready_monotonic_ns']


def matrix_source_watchdog_due(last_submit_ns,now_ns):
    if last_submit_ns is None:return False
    require(now_ns>=last_submit_ns,'MATRIX_SOURCE_WATCHDOG_CLOCK_REVERSED')
    return now_ns-last_submit_ns>=MATRIX_SOURCE_STALL_NS


def hold_matrix_source(transport,native,gpio,log,baseline,record):
    """Withdraw real output on source loss; keep the one native/SDK session."""
    log.gate();require(not log.pending and gpio.owned,'MATRIX_SOURCE_HOLD_REQUIRES_RETURNED_OWNED_GPIO')
    record.update(status='REVOKING_ON_SOURCE_LOSS',started_monotonic_ns=time.monotonic_ns(),
        actual_window_before_hold=gpio.window_snapshot('BEFORE_MATRIX_SOURCE_HOLD'))
    record['revoke_status']=ordered_revoke_disarm(transport,native,gpio,log,baseline)
    record.update(status='OFF_AWAITING_NEW_OBSERVABLE_SOURCE',off_monotonic_ns=time.monotonic_ns(),
        SDK_restart=False,retired_control_session_must_not_be_reused=True)
    log.event('DGM2_SOURCE_LOSS_OFF',record=record)


def resume_matrix_source(transport,gpio,log,ledger,out,record):
    """New control epoch, never a replay or a sticky-fault clear."""
    log.gate();require(not log.pending and not gpio.owned and not gpio.armed,
        'MATRIX_SOURCE_RESUME_REQUIRES_RETURNED_DISARMED_STATE')
    mail=log.call('SOURCE_RESUME_MAILBOX_LDR',transport.read32,4);require_no_protocol_fault(mail)
    active=log.call('SOURCE_RESUME_ACTIVE_SESSION_LDR',transport.read32,0x10)
    reservation=ledger.reserve(active,Path('/proc/sys/kernel/random/boot_id').read_text().strip(),out)
    record['session_reservation']={'ledger_path':str(ledger.path),**reservation}
    log.event('DGM2_SOURCE_RESUME_SESSION_RESERVED',**record['session_reservation'])
    started,baseline=acquire_digital_window(gpio,transport,log,require_zero_iic=True)
    record.update(status='REARMED_AWAITING_POST_ARM_FRESH_RECEIPT',
        new_window_started_ns=started,new_baseline=baseline,actual_window_reacquire=gpio.last_window_acquire,
        ready_monotonic_ns=time.monotonic_ns(),SDK_restart=False,
        trigger_frame_action='DISCARD_NO_SUBMISSION;ONLY_LATER_RECEIPTS_MAY_SUBMIT')
    log.event('DGM2_SOURCE_RECOVERY_REARMED',record=record)
    return started,baseline,reservation,record['ready_monotonic_ns']


def pwm_refresh_required(previous_projection,current_projection):
    require(type(current_projection) is bytes and len(current_projection)==256,'EXACT_PROJECTED_MATRIX_BYTES_REQUIRED')
    return previous_projection is None or current_projection!=previous_projection


def scheduler_progress(before,after,*,refresh_required):
    # Status counters are 16-bit: frame_done and burst_trig, not target IIC ACKs.
    frames=(after['frame_count']-before['frame_count'])&0xffff
    bursts=(after['burst_count']-before['burst_count'])&0xffff
    idle=not bool(after['flags']&0x1e)
    return {'frame_done_delta':frames,'global_burst_trigger_delta':bursts,'pipeline_idle':idle,
            'pwm_update_requirement':'PWM_REFRESH_REQUIRED' if refresh_required else 'NO_PWM_CHANGE_REQUIRED',
            'accepted':frames>0 and idle and (bursts>0 or not refresh_required),
            'counter_scope':'GLOBAL_FRAME_DONE_AND_BURST_TRIG_NOT_TARGET_IIC_ACK'}


def full_off_readback(native,log,expected_id=DESIGN_ID):
    # Existing low-level engine, retargeted by explicit constructor ID; never main()/run().
    from digital_iic_diag import GuardPins,BitBang,Inventory
    pins=GuardPins(native,expected_id,lambda stage,**kw:log.event('FULL_OFF_'+stage,**kw))
    rows=[];opened=False;result={'status':'NOT_VERIFIED','devices':rows,'physical_shutdown':'UNVERIFIED'}
    try:
        pins.open();opened=True;bus=BitBang(pins,lambda stage,**kw:log.event('FULL_OFF_'+stage,**kw),session_budget_s=FULL_OFF_READBACK_BUDGET_S);inventory=Inventory(bus,lambda stage,**kw:log.event('FULL_OFF_'+stage,**kw))
        for block in (4,5,6,7,12,13,14,15):
            row=inventory.device(block,initialize=False);rows.append(row)
            require(row['status']=='REGISTER_READBACK_COMPLETE' and row.get('full_off_register_bits') is True,'PCA_FULL_OFF_ACTUAL_READBACK_FAILED')
        # Mux deselection only; never PWM or initialization writes.
        bus.transfer(0x70,[0]);result['status']='VERIFIED_REGISTER_BITS_ONLY'
    finally:
        if (opened or pins.tri!=0xffffffff) and not log.failed and not log.pending and not log.stop.exists():
            try:result['terminal']=pins.close()
            finally:
                # Only disable requests after returned errors; never after unknown MMIO.
                if not log.failed and not log.pending and not log.stop.exists():
                    native.write(8,0);native.write(12,0xffffffff)
                    result['TRI_after']=native.read(12)
                    require(result['TRI_after']==0xffffffff,'DIAGNOSTIC_TRI_NOT_RESTORED')
    return result


def supervised_revoke_before_sdk_wait(proof,result,transport,native,gpio,log):
    """Supervised normal end: physical requests close before SDK stop can stall.

    This uses the existing revoke/drain/disarm and actual eight-PCA FULL_OFF
    readback. Unknown MMIO forbids every following hardware access.
    """
    if proof is None or gpio is None or not gpio.owned:return False
    require(not log.pending and not log.failed and not log.stop.exists(),
            'SUPERVISED_STOP_MMIO_UNKNOWN_NO_FURTHER_ACCESS')
    result['motor_revoke_monotonic_ns']=time.monotonic_ns()
    log.event('SUPERVISED_MOTOR_REVOKE_BEFORE_NATIVE_WAIT',
              motor_revoke_monotonic_ns=result['motor_revoke_monotonic_ns'])
    result['revoke_status']=ordered_revoke_disarm(transport,native,gpio,log)
    result['motor_requests_disarmed_monotonic_ns']=time.monotonic_ns()
    result['FULL_OFF_readback']=(full_off_readback(native,log,expected_id=gpio.expected_id)
        if getattr(gpio,'expected_id',DESIGN_ID)!=DESIGN_ID else full_off_readback(native,log))
    result['motor_full_off_verified_monotonic_ns']=time.monotonic_ns()
    return True


def _worker_locked(args,session_ledger):
    require(sys.platform=='linux' and platform.machine().lower()=='aarch64' and os.geteuid()==0,'EXISTING_ROOT_LINUX_AARCH64_REQUIRED')
    sys.path[:0]=[str(ROOT),str(ROOT/'runtime_compact'),str(ROOT/'iic_c1')]
    from runtime_control.uio import EnvironmentProfile,inspect_environment,open_confirmed_transport
    from runtime_control.digital_backend import LinuxDigitalBackend
    from runtime_control.digital_bringup import DigitalBringupProducer
    from runtime_control.producer import ControlRejected
    from compact_source import CompactFrameSource,SourceClosed
    from compact_codec import identity_digest
    from run_compact_observation import depth_device_access,require_elf,warning_counts
    from digital_iic_diag import Native32
    proof=load_operator_context(args,consume=True)
    matrix_mode=getattr(args,'matrix_mode',False)
    expected_id=MATRIX_DESIGN_ID if matrix_mode else DESIGN_ID
    bench=(matrix_module() if matrix_mode else supervised_module()) if proof is not None else None
    if matrix_mode:
        from runtime_control.digital_matrix import DigitalMatrixProducer,matrix_stage_indices
        from runtime_control.digital_matrix_backend import LinuxDigitalMatrixBackend
        from runtime_control.digital_bringup import _compact
    extended=extended_mode(args);duration=session_duration_s(args)
    out=args.out;log=Journal(out,bounded=True) if extended else Journal(out);began=time.monotonic_ns();session=None
    stop_request=extended_module().StopRequest().install() if extended else None
    captures=[]
    result={'schema':'R6_D1_LIVE_DIGITAL_V1','mode':'DIGITAL_BRINGUP_MODE','FRESH':False,'capture_time_verified':False,
            'physical_output_permitted':False,'SDK_cleanup':'UNKNOWN','requested_duration_s':duration,'requested_source_index':args.source_index,'transactions':0,'errors':[],'MMIO_unknown':False,
            'run_kind':'CONTINUOUS_ACQUISITION_WITH_SAFE_DIGITAL_CONTROL_GAPS' if args.renew_digital_window else 'BOUNDED_DIGITAL_SESSION',
            'window_renewals':[],'session_reservations':[],'pre_renewal_receipt_frames_discarded':0}
    result['source_eligibility']={'rejected_no_eligible_count':0,'last_rejected':None,
        'last_successful_submit_monotonic_ns':None,'last_completed_transaction_monotonic_ns':None}
    if extended:
        result.update(run_kind='EXPLICIT_600_SECOND_SUPERVISOR_SINGLE_CONTINUOUS_NATIVE',
            acquisition_mode='0_FRAME_LIMIT_0_DURATION_CONTINUOUS_UNTIL_SIGNAL_OR_ERROR',
            runtime_unbounded=False,stop_requested=False,raw_sample_limit=64,
            evidence_limit=1800,log_segment_limit_bytes=extended_module().FILE_BYTES,
            physical_control_continuity='NOT_CLAIMED_SAFE_FULL_OFF_REARM_GAPS')
    if matrix_mode:
        result.update(schema='R6_DGM2_LIVE_MATRIX_BENCH_V1',mode='DIGITAL_MATRIX_BENCH_MODE',
            expected_design_id=expected_id,matrix_stage_cells=args.matrix_cell_limit,
            allowed_source_indices=list(matrix_stage_indices(args.matrix_cell_limit)),
            output_enabled_block_mask=args.output_mask,level_cap=args.level_cap,
            source_recoveries=[],source_watchdog_ns=MATRIX_SOURCE_STALL_NS)
    if proof is not None:
        result.update(mode=bench.MODE,operator_bench_permission=True,production_output_permitted=False,
                      production_ready=False,VPLUS_actual_state='ON_USER_CONFIRMED',
                      operator_attestation=proof.as_record(),actual_vibration='NOT_VERIFIED')
    child=source=backend=transport=rawio=memory=None;fd=None;gpio=None;temporary=None;error=None;baseline=None;renewal_failed=False
    matrix_source_held=False;matrix_last_submit_ns=None;matrix_hold_record=None
    try:
        result['package']=verify_package(ROOT)
        if extended:
            manifest=json.loads((ROOT/'PACKAGE_MANIFEST.json').read_text())
            require(manifest.get('supports_extended_matrix_session') is True
                and manifest.get('extended_matrix_session_s')==600
                and manifest.get('continuous_native_sha256')==extended_module().NATIVE_SHA,
                'EXPLICIT_EXTENDED600_SEALED_PACKAGE_REQUIRED')
        if matrix_mode:
            manifest=json.loads((ROOT/'PACKAGE_MANIFEST.json').read_text())
            require(manifest.get('supports_digital_matrix_mode') is True
                and manifest.get('matrix_design_id')==MATRIX_DESIGN_ID
                and manifest.get('matrix_allowed_target_mask')==0xf0f0
                and manifest.get('matrix_supported_stage_cells')==[8,32,64,128]
                and manifest.get('matrix_level_cap')==1,'EXACT_DGM2_MATRIX_PACKAGE_REQUIRED')
            if proof is not None:require(manifest.get('supports_supervised_matrix') is True,'EXACT_SUPERVISED_MATRIX_PACKAGE_REQUIRED')
        if proof is not None and not matrix_mode:
            manifest=json.loads((ROOT/'PACKAGE_MANIFEST.json').read_text())
            require(manifest.get('supports_supervised_single_motor') is True
                    and manifest.get('supervised_source_index')==131
                    and manifest.get('supervised_level_cap')==1,
                    'EXACT_SUPERVISED_SINGLE_MOTOR_PACKAGE_REQUIRED')
            if proof.source_index!=131:
                require(manifest.get('supports_supervised_target_selection') is True
                        and manifest.get('supervised_allowed_target_mask')==0xf0f0,
                        'EXACT_SUPERVISED_SELECTED_TARGET_F0F0_PACKAGE_REQUIRED')
        if args.renew_digital_window:
            require(result['package']['supports_digital_window_renewal'] and result['package']['digital_window_renewal_interval_s']==30
                    and result['package']['hardware_signal_window_s']==50,'EXACT_SAFE_WINDOW_RENEWAL_PACKAGE_REQUIRED')
        raw=json.loads(args.profile.read_text());profile=EnvironmentProfile(**{f.name:raw[f.name] for f in dataclasses.fields(EnvironmentProfile)})
        require(profile.expected_design_id==expected_id,'EXACT_RUNTIME_MODE_PROFILE_ID_REQUIRED_BEFORE_MMIO');env=inspect_environment(profile)
        require_elf(args.native,args.native_sha256);require(sha(args.gpio_helper)==GPIO_SHA,'GPIO_HELPER_SHA_MISMATCH')
        require(sha(args.sdk_library)==SDK_SHA and sha(args.xml)==XML_SHA,'SDK_OR_XML_SHA_MISMATCH')
        clock=next((s for s in Path('/sys/kernel/debug/clk/clk_summary').read_text().splitlines() if s.split() and s.split()[0]=='pl0_ref'),None)
        require(clock and 90_000_000<=int(clock.split()[4])<=110_000_000,'PL0_CLOCK_NOT_90_110MHZ')
        result.update(environment=env,clock=clock,depth_device=depth_device_access(),native_sha256=args.native_sha256)
        log.event('ENVIRONMENT_GATE_PASS',**result['environment'])
        # GPIO ID is read by the transport while all selectors remain inputs/page0.
        transport=log.call('CONTROL_TRANSPORT_OPEN',open_confirmed_transport,profile)
        mailbox_status=log.call('D1_PRE_CAPTURE_STATUS_LDR',transport.read32,4);require_no_protocol_fault(mailbox_status)
        result['pre_capture_mailbox_status']=mailbox_status
        active_session=log.call('D1_ACTIVE_SESSION_LDR',transport.read32,0x10)
        reservation=session_ledger.reserve(active_session,Path('/proc/sys/kernel/random/boot_id').read_text().strip(),out)
        session=reservation['reserved_session'];result['session_reservation']={'ledger_path':str(session_ledger.path),**reservation}
        result['session_reservations'].append(result['session_reservation']);control_session=session
        log.event('D1_SESSION_RESERVED_DURABLE',**result['session_reservation'])
        fd=os.open(env['devices']['gpio'],os.O_RDWR|os.O_SYNC);memory=mmap.mmap(fd,0x10000,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE)
        rawio=Native32(memory,args.gpio_helper);native=LoggedNative(rawio,log);gpio=D1Gpio(native,log,expected_id=expected_id)
        window_started_ns,baseline=acquire_digital_window(gpio,transport,log,require_zero_iic=args.renew_digital_window);result['initial_status']=baseline
        result['initial_actual_window_acquire']=gpio.last_window_acquire
        result['initial_window_started_ns']=window_started_ns;receipt_after_renewal_ns=0
        temporary=tempfile.TemporaryDirectory(prefix='d1_');source=CompactFrameSource(Path(temporary.name)/'f.sock').start()
        native_out=out/'native';native_out.mkdir(mode=0o700)
        cmd=[str(args.native),str(args.xml),str(native_out),'1800',str(session),str(args.raw_samples),str(source.path),str(args.duration_s*1000)]
        if extended:cmd[3]='0';cmd[-1]='0';cmd.append('--continuous')
        sdk_env=os.environ.copy();sdk_env['LD_LIBRARY_PATH']=str(args.sdk_library.parent)+':'+sdk_env.get('LD_LIBRARY_PATH','')
        if extended:stdout=stderr=subprocess.PIPE
        else:stdout=(out/'sdk.stdout.log').open('xb');stderr=(out/'sdk.stderr.log').open('xb')
        child=subprocess.Popen(cmd,cwd=native_out,env=sdk_env,stdout=stdout,stderr=stderr,start_new_session=True)
        if extended:captures=[extended_module().PipeCapture(child.stdout,out/'sdk.stdout.log'),extended_module().PipeCapture(child.stderr,out/'sdk.stderr.log')]
        else:stdout.close();stderr.close()
        (out/'NATIVE_PID.json').write_text(json.dumps({'pid':child.pid,'native':str(args.native),'proc_starttime':Path(f'/proc/{child.pid}/stat').read_text().split()[21]}))
        log.event('NATIVE_CHILD_STARTED',pid=child.pid,command=cmd)
        result['digital_transaction_budget_ns']=D1_TRANSACTION_BUDGET_NS
        backend=(LinuxDigitalMatrixBackend(profile,target_mask=0xf0f0,emit=log.backend,timeout_ns=D1_TRANSACTION_BUDGET_NS) if matrix_mode else LinuxDigitalBackend(profile,emit=log.backend,timeout_ns=D1_TRANSACTION_BUDGET_NS));producer=None;locked_target=None;previous_projection=None;end=time.monotonic()+duration+(0 if extended else 7)
        with (extended_module().RotatingLog(out/'transactions.jsonl') if extended else (out/'transactions.jsonl').open('x',encoding='utf8')) as records:
            while time.monotonic()<end:
                log.gate()
                if extended:
                    for capture in captures:capture.check()
                    if stop_request.requested or (out/'STOP_REQUESTED').exists():
                        result.update(stop_requested=True,stop_signal=stop_request.signal);break
                if matrix_mode and not matrix_source_held and matrix_source_watchdog_due(matrix_last_submit_ns,time.monotonic_ns()):
                    matrix_hold_record={'cause':'NO_RETURNED_MATRIX_SUBMISSION_WITHIN_BOUND',
                        'last_successful_submit_monotonic_ns':matrix_last_submit_ns,'previous_control_session':control_session}
                    result['source_recoveries'].append(matrix_hold_record)
                    hold_matrix_source(transport,native,gpio,log,baseline,matrix_hold_record)
                    matrix_source_held=True;matrix_last_submit_ns=None
                if args.renew_digital_window and not matrix_source_held and child.poll() is None and window_renewal_due(window_started_ns,time.monotonic_ns()):
                    renewal={'native_pid':child.pid,'native_acquisition_session':session,'previous_control_session':control_session}
                    result['window_renewals'].append(renewal)
                    try:
                        window_started_ns,baseline,reserved,receipt_after_renewal_ns=renew_digital_window(transport,native,gpio,log,baseline,session_ledger,out,window_started_ns,renewal)
                    except BaseException:
                        renewal['status']='FAILED_NO_RENEWAL_RETRY';renewal_failed=True;raise
                    control_session=reserved['reserved_session'];result['session_reservations'].append(renewal['session_reservation'])
                    if matrix_mode:matrix_last_submit_ns=None
                    previous_projection=None # The reinitialized pipeline starts FULL_OFF; first new frame must refresh.
                try:item=source.next_frame(timeout_s=.25)
                except TimeoutError:
                    if child.poll() is not None:break
                    continue
                except SourceClosed:
                    require(source.error is None or str(source.error).endswith('IPC_PEER_EOF'),'IPC_SOURCE_PROTOCOL_FAILURE:'+str(source.error))
                    # The native IPC worker closes before SDK stop/destruct; wait below for its bounded exit.
                    log.event('IPC_NORMAL_PEER_EOF',native_exit_pending=child.poll() is None)
                    break
                if (args.renew_digital_window or matrix_mode) and item.receipt_monotonic_ns<=receipt_after_renewal_ns:
                    result['pre_renewal_receipt_frames_discarded']+=1
                    log.event('D1_PRE_RENEWAL_RECEIPT_FRAME_DISCARDED_NO_SUBMISSION',session=item.native['session'],sequence=item.native['sequence'],receipt_monotonic_ns=item.receipt_monotonic_ns,renewal_ready_ns=receipt_after_renewal_ns,capture_time_verified=False)
                    continue
                if producer is None:
                    binding=json.loads((native_out/'compact_identity.json').read_text())
                    require(binding.get('sha256')==identity_digest(binding).hex(),'SDK_IDENTITY_BINDING_INVALID')
                    producer_options={'native_binary_sha256':args.native_sha256,'source_kind':'LIVE_NATIVE_PIPE',
                        'emit':None if proof is not None else lambda e:log.event('PROJECTION',record=e)}
                    producer=(DigitalMatrixProducer(binding,allowed_target_mask=0xf0f0,**producer_options)
                        if matrix_mode else DigitalBringupProducer(binding,**producer_options))
                if matrix_mode:
                    observed=_compact(item.native['compact_packet'],producer.identity)
                    observable=observed['geometry_quality'] in (0,1) and observed['unknown']!=b'\xff'*32
                    if not observable:
                        eligibility=result['source_eligibility'];eligibility['rejected_no_eligible_count']+=1
                        eligibility['last_rejected']={'monotonic_ns':time.monotonic_ns(),
                            'session':observed['session'],'sequence':observed['sequence'],
                            'sdk_frame_index':observed['sdk_frame_index'],'reasons':['DGM2_NO_OBSERVABLE_GEOMETRY']}
                        if not matrix_source_held:
                            matrix_hold_record={'cause':'REAL_SOURCE_UNUSABLE_OR_ALL_UNKNOWN',
                                'source':dict(eligibility['last_rejected']),'previous_control_session':control_session}
                            result['source_recoveries'].append(matrix_hold_record)
                            hold_matrix_source(transport,native,gpio,log,baseline,matrix_hold_record)
                            matrix_source_held=True;matrix_last_submit_ns=None
                        log.event('DGM2_UNUSABLE_SOURCE_OFF_NO_SUBMISSION',**eligibility['last_rejected'])
                        continue
                    if matrix_source_held:
                        matrix_hold_record['recovery_trigger_source']={'session':observed['session'],
                            'sequence':observed['sequence'],'sdk_frame_index':observed['sdk_frame_index'],
                            'receipt_monotonic_ns':item.receipt_monotonic_ns,'raw_sha256':observed['raw_sha256']}
                        window_started_ns,baseline,reserved,receipt_after_renewal_ns=resume_matrix_source(
                            transport,gpio,log,session_ledger,out,matrix_hold_record)
                        control_session=reserved['reserved_session']
                        result['session_reservations'].append(matrix_hold_record['session_reservation'])
                        matrix_source_held=False;matrix_last_submit_ns=None;previous_projection=None
                        # The observation triggered rearm; do not replay it after that slow operation.
                        continue
                now=current_status(transport,log);require_health(now,baseline)
                prepare_options={}
                if args.renew_digital_window or matrix_mode:prepare_options['control_session']=control_session
                if matrix_mode:matrix_prepare=producer.prepare_compact
                try:
                    if matrix_mode:
                        frame=matrix_prepare(item.native['compact_packet'],online_mask=now['online_mask']&0xffff,
                            receipt_monotonic_ns=item.receipt_monotonic_ns,output_mask=args.output_mask,level_cap=args.level_cap,
                            allowed_source_indices=matrix_stage_indices(args.matrix_cell_limit),**prepare_options)
                    else:frame=producer.prepare_compact(item.native['compact_packet'],online_mask=now['online_mask']&0xffff,receipt_monotonic_ns=item.receipt_monotonic_ns,required_source_index=args.source_index if locked_target is None else locked_target['source_index'],**prepare_options)
                except ControlRejected as exc:
                    if any(str(reason).startswith(('D1_NO_VALID','D1_LOCKED_SOURCE','DGM2_NO_OBSERVABLE')) for reason in exc.reasons):
                        eligibility=result['source_eligibility'];eligibility['rejected_no_eligible_count']+=1
                        eligibility['last_rejected']={'monotonic_ns':time.monotonic_ns(),
                            'session':item.native['session'],'sequence':item.native['sequence'],
                            'sdk_frame_index':item.native['sdk_frame_index'],'reasons':list(exc.reasons)}
                        log.event('NO_ELIGIBLE_LOCKED_CELL' if locked_target is not None else 'NO_ELIGIBLE_CELL',
                                  session=item.native['session'],sequence=item.native['sequence'],sdk_frame_index=item.native['sdk_frame_index'],
                                  raw_sha256=item.native['raw_sha256'],locked_target=locked_target,requested_source_index=args.source_index,reasons=list(exc.reasons),
                                  compact_packet_hex=item.native['compact_packet'].hex(),FRESH=False,
                                  action='NO_SUBMISSION_NO_TARGET_CHANGE;LOCAL_D1_LIFETIME_MAY_EXPIRE')
                        continue
                    raise
                if proof is not None:
                    frame=bench.wrap_live_frame(frame,proof) if matrix_mode else bench.cap_single_frame(frame,proof)
                    log.event('SUPERVISED_CAPPED_CANDIDATE_NOT_YET_SUBMITTED',record=frame.as_record())
                if not matrix_mode and locked_target is None:
                    locked_target={k:v for k,v in frame.selected.items() if k!='level'}
                    selection={'schema':'D1_FIRST_LIVE_SELECTED_TARGET_V1','selection_stage':'PREPARED_NOT_YET_SUBMITTED',
                               'logical_target':locked_target,'requested_source_index':args.source_index,'first_observed_level':frame.selected['level'],
                               'session':frame.session,'first_sequence':frame.sequence,'sdk_frame_index':frame.sdk_frame_index,
                               'raw_sha256':frame.raw_sha256,'G6C1_packet_sha256':frame.compact_packet_sha256,
                               'physical_location':'NOT_INFERRED_FROM_LOGICAL_MAP','subsequent_rule':'SAME_SOURCE_INDEX_ONLY;UNKNOWN_ZERO_INVALID_REJECTED_NO_FALLBACK',
                               'capture_time_verified':False,'FRESH':False,'physical_output_permitted':False}
                    if proof is not None:
                        selection.update(schema='SUPERVISED_SINGLE_MOTOR_SELECTED_TARGET_V1',mode=bench.MODE,
                            first_observed_level=frame.source_level,first_commanded_level=1,
                            operator_bench_permission=True,production_output_permitted=False,
                            VPLUS_actual_state='ON_USER_CONFIRMED')
                    with (out/'SELECTED_TARGET.json').open('x') as selected_file:json.dump(selection,selected_file,indent=2)
                    result['selected_target']=selection;log.event('SELECTED_TARGET_LOCKED',**selection)
                if not matrix_mode:
                    require(frame.selected['source_index']==locked_target['source_index'] and frame.selected['block']==locked_target['block']
                            and frame.selected['channel']==locked_target['channel'],'LOCKED_D1_TARGET_CHANGED_NO_SUBMISSION')
                # All source values/quality are retained; only the named D1 diagnostic projection is submitted.
                gpio.page(0)
                # Covers the backend's first transport/GPIO-ID open before its per-word event stream.
                log.gate();log.pending=True;log.event('D1_BACKEND_SUBMIT_BEGIN',sequence=frame.sequence)
                submitted=backend.submit(frame);log.pending=False
                if proof is not None:submitted=bench.wrap_submission(submitted,frame)
                log.event('D1_BACKEND_SUBMIT_RETURNED',sequence=frame.sequence)
                require(submitted['packet_read_words']==96 and submitted['haptic_active_read_words']==64,'D1_PACKET_OR_TRUE_BUFFER_READBACK_INCOMPLETE')
                require(submitted['session']==frame.session and submitted['sequence']==frame.sequence,'D1_READBACK_SOURCE_IDENTITY_CHANGED')
                result['source_eligibility']['last_successful_submit_monotonic_ns']=time.monotonic_ns()
                if matrix_mode:matrix_last_submit_ns=result['source_eligibility']['last_successful_submit_monotonic_ns']
                result['transactions']+=1
                # Dirty=0 produces frame_done with no channel scan/burst; it is valid for an unchanged projection.
                source_record=frame.as_record();projection=bytes(source_record['projection_matrix'])
                refresh_required=pwm_refresh_required(previous_projection,projection)
                target_end=time.monotonic()+.7;seen=[];progress=None
                while time.monotonic()<target_end:
                    post=current_status(transport,log);require_health(post,baseline);seen.append(post)
                    progress=scheduler_progress(submitted['diagnostics_before'],post,refresh_required=refresh_required)
                    if progress['accepted']:break
                    time.sleep(.003)
                row={'source':source_record,'submit_readback':submitted,'scheduler_observations':seen,
                     'scheduler_acceptance':progress,'projection_matrix_changed_or_first':refresh_required,
                     'scheduler_burst_changed':bool(progress['global_burst_trigger_delta']),
                     'target_specific_IIC_trace':'NOT_SAVED','target_PWM_register_readback':'NOT_RUN',
                     'physical_output_verified':False}
                if args.renew_digital_window:row.update(digital_window_index=len(result['window_renewals']),source_acquisition_session=item.native['session'],producer_session=control_session,native_pid=child.pid)
                records.write(json.dumps(row)+'\n');records.flush()
                require(progress['accepted'],'NO_COMPLETED_SCHEDULER_FRAME_OR_REQUIRED_REFRESH_WITHIN_BOUND')
                previous_projection=projection
                result['source_eligibility']['last_completed_transaction_monotonic_ns']=time.monotonic_ns()
                log.event('D1_LIVE_TRANSACTION_COMPLETE',sequence=frame.sequence,selected=frame.as_record().get('selected_source_trace') if not matrix_mode else {'matrix_stage_cells':args.matrix_cell_limit,'nonzero_cells':sum(bool(x) for x in projection)},status=post,scheduler_acceptance=progress)
            # Supervised V+ ON requires requests revoked and eight actual FULL_OFF
            # readbacks before the potentially slow SDK stop/destruct process.
            # Legacy V+ disconnected ordering is intentionally unchanged.
            if extended:result['extended_pre_sdk_revoke_attempted']=True
            if matrix_mode and matrix_source_held:
                # Already disarmed on source loss; verify the existing eight actual FULL_OFF registers once at end.
                log.gate();require(not log.pending,'MATRIX_HELD_FINAL_MMIO_UNKNOWN')
                result['FULL_OFF_readback']=full_off_readback(native,log,expected_id=expected_id)
                result['motor_full_off_verified_monotonic_ns']=time.monotonic_ns()
            supervised_revoke_before_sdk_wait(proof,result,transport,native,gpio,log)
            if extended:
                result['stop_reason']='OPERATOR_SOFTWARE_STOP_REQUEST' if result['stop_requested'] else 'EXPLICIT_600S_SUPERVISOR_END'
                if child.poll() is None:os.killpg(child.pid,signal.SIGTERM)
                child.wait(timeout=10)
                result['transaction_log_retention']=records.as_record()
            elif child.poll() is None:child.wait(timeout=max(.001,end-time.monotonic()))
        require(child.returncode==0,'NATIVE_CAPTURE_EXIT_NONZERO')
        state=json.loads((native_out/'session_result.json').read_text());result['native_session']=state
        result['native_evidence_ledger']=(extended_module().evidence_ledger(state,json.loads((native_out/'continuous_policy_result.json').read_text()))
            if extended else native_evidence_ledger(state,args.duration_s,args.raw_samples))
        log.event('NATIVE_EVIDENCE_LEDGER',**result['native_evidence_ledger'])
        require(result['transactions']>0,'NO_LIVE_D1_TRANSACTION')
        if args.renew_digital_window:result['native_single_PID_session']={'pid':child.pid,'acquisition_session':session,'SDK_restart':False,'physical_control_continuity':'NOT_CLAIMED_SAFE_FULL_OFF_REARM_GAPS'}
    except BaseException as exc:
        error=f'{type(exc).__name__}:{exc}';result['errors'].append(error)
        try:
            result['first_error_evidence']=save_first_error(out,exc,log,gpio,backend,
                {'transactions':result['transactions'],'source_eligibility':result['source_eligibility'],
                 'initial_window_started_ns':result.get('initial_window_started_ns'),
                 'initial_actual_window_acquire':result.get('initial_actual_window_acquire'),
                 'window_renewals':result['window_renewals'],
                 'source_hold_active':matrix_source_held,'source_recoveries':result.get('source_recoveries',[])})
        except BaseException as evidence_error:
            result['first_error_evidence']={'status':'EVIDENCE_WRITE_FAILED','original_error':error,
                'evidence_error':f'{type(evidence_error).__name__}:{evidence_error}'}
        log.event('SESSION_ERROR',error=error,details=getattr(exc,'details',{}),first_error_evidence=result['first_error_evidence'])
    finally:
        # Never send another hardware access after an unreturned/failed native32 operation or parent stop.
        uncertain=log.pending or log.failed or log.stop.exists();result['MMIO_unknown']=uncertain
        if extended and gpio is not None and gpio.owned and not uncertain and not result.get('extended_pre_sdk_revoke_attempted'):
            result['extended_pre_sdk_revoke_attempted']=True
            try:supervised_revoke_before_sdk_wait(proof,result,transport,native,gpio,log)
            except BaseException as exc:
                result['errors'].append('EXTENDED_PRE_SDK_REVOKE:'+str(exc));log.event('EXTENDED_PRE_SDK_REVOKE_FAILED',error=str(exc))
        if child is not None and child.poll() is None:
            try:os.killpg(child.pid,signal.SIGTERM);child.wait(timeout=3)
            except BaseException as exc:result['errors'].append('NATIVE_STOP:'+str(exc))
        uncertain=log.pending or log.failed or log.stop.exists()
        if gpio is not None and gpio.owned and not uncertain and not extended:
            try:
                if renewal_failed:
                    gpio.disarm();raise RuntimeError('RENEWAL_FAILED_REQUESTS_DISABLED_NO_PHASE_RETRY')
                result['revoke_status']=ordered_revoke_disarm(transport,native,gpio,log)
                # Read actual PCA bytes after the separate guard diagnostic-owner transition.
                result['FULL_OFF_readback']=(full_off_readback(native,log,expected_id=expected_id) if matrix_mode else full_off_readback(native,log))
            except BaseException as exc:
                result['errors'].append('ORDERED_REVOKE:'+str(exc));log.event('ORDERED_REVOKE_FAILED',error=str(exc))
                if not renewal_failed and gpio.owned and not log.failed and not log.pending and not log.stop.exists():
                    try:gpio.disarm()
                    except BaseException as stop_error:result['errors'].append('DISARM_AFTER_RETURNED_ERROR:'+str(stop_error))
        elif gpio is not None and gpio.owned:result['required_action']='STOP_ALL_HARDWARE_ACCESS_NO_RETRY_OR_AUTOMATIC_CLEANUP_WRITE'
        # Close local mappings/descriptors only. These calls perform no register write.
        for obj in (source,backend,transport,rawio):
            if obj is not None:
                try:obj.close()
                except BaseException as exc:result['errors'].append('LOCAL_CLOSE:'+str(exc))
        if memory is not None:
            try:memory.close()
            except BaseException as exc:result['errors'].append('LOCAL_UNMAP:'+str(exc))
        if fd is not None:os.close(fd)
        if temporary is not None:
            try:temporary.cleanup()
            except BaseException as exc:result['errors'].append('SOCKET_CLEANUP:'+str(exc))
        if extended:
            stats=[]
            for capture in captures:
                try:stats.append(capture.finish())
                except BaseException as exc:result['errors'].append('SDK_LOG_CAPTURE:'+str(exc))
            result['SDK_log_retention']=stats
            result['SDK_warning_counts']={name:sum(row['warning_counts'][name] for row in stats) for name in ('OB_EVENT_TIMEOUT','cancel_request_lines','warning_lines','error_lines')}
            result['SDK_warning_accounting']='COMPLETE_STREAM_LINES' if len(stats)==2 and all(row['warning_accounting']=='COMPLETE_STREAM_LINES' for row in stats) else 'INCOMPLETE_NO_CLEAN_CLAIM'
        else:
            text=''.join((out/n).read_text(errors='replace') for n in ('sdk.stdout.log','sdk.stderr.log') if (out/n).exists())
            result['SDK_warning_counts']=warning_counts(text)
        result['SDK_cleanup']='UNKNOWN'
        result['MMIO_unknown']=log.pending or log.failed or log.stop.exists()
        if result['MMIO_unknown']:result['required_action']='STOP_ALL_HARDWARE_ACCESS_NO_RETRY_OR_AUTOMATIC_CLEANUP_WRITE'
        result['elapsed_ns']=time.monotonic_ns()-began
        result['status']='PASS_DIGITAL_ONLY_NOT_PHYSICAL' if not result['errors'] and result.get('FULL_OFF_readback',{}).get('status')=='VERIFIED_REGISTER_BITS_ONLY' else 'FAILED_OR_PARTIAL_STOP_NO_RETRY'
        log.event('SESSION_END',status=result['status'],errors=result['errors'])
        if extended:result['event_log_retention']=log.fp.as_record()
        (out/'WORKER_RESULT.json').write_text(json.dumps(result,indent=2))
        if stop_request is not None:stop_request.close()
        log.fp.close()
    return 0 if result['status']=='PASS_DIGITAL_ONLY_NOT_PHYSICAL' else 70


def worker(args):
    require(sys.platform=='linux' and platform.machine().lower()=='aarch64' and os.geteuid()==0,'EXISTING_ROOT_LINUX_AARCH64_REQUIRED')
    with open_session_ledger() as session_ledger:
        return _worker_locked(args,session_ledger)


def main(argv=None):
    args=parser().parse_args(argv);require(args.run_once and (args.vplus_disconnected or args.supervised_single_motor or args.supervised_matrix),'EXPLICIT_RUN_ONCE_AND_POWER_PROCEDURE_REQUIRED')
    validate_window_mode(args)
    proof=load_operator_context(args)
    for name in ('profile','out','native','sdk_library','xml','gpio_helper'):
        require(getattr(args,name).is_absolute(),'ABSOLUTE_ARGUMENT_PATH_REQUIRED:'+name)
    if args.worker:return worker(args)
    args.out.mkdir(parents=True,exist_ok=False)
    (args.out/'ATTEMPT.json').write_text(json.dumps({'schema':'D1_ONE_ATTEMPT','pid':os.getpid(),'mode':(matrix_module().MODE if args.matrix_mode else supervised_module().MODE) if proof is not None else 'DIGITAL_MATRIX_VPLUS_DISCONNECTED' if args.matrix_mode else 'DIGITAL_ONLY_VPLUS_DISCONNECTED','deadline_s':parent_deadline_s(session_duration_s(args)),'automatic_retry':False}))
    cmd=[sys.executable,'-B','-u',str(Path(__file__).resolve()),*(sys.argv[1:] if argv is None else argv),'--worker']
    worker_log_stats=[];worker_log_errors=[]
    with ExitStack() as files:
        if extended_mode(args):stdout=stderr=subprocess.PIPE
        else:
            stdout=files.enter_context((args.out/'worker.stdout.log').open('xb'))
            stderr=files.enter_context((args.out/'worker.stderr.log').open('xb'))
        child=subprocess.Popen(cmd,stdout=stdout,stderr=stderr,start_new_session=True)
        worker_captures=([extended_module().PipeCapture(child.stdout,args.out/'worker.stdout.log'),extended_module().PipeCapture(child.stderr,args.out/'worker.stderr.log')] if extended_mode(args) else [])
        stop_relay=None
        if extended_mode(args):
            def relay_stop():
                # This signal touches only the worker process. The worker orders FULL_OFF before SDK wait.
                if child.poll() is None:os.killpg(child.pid,signal.SIGTERM)
            stop_relay=extended_module().StopRequest(relay_stop).install()
        try:code=child.wait(timeout=parent_deadline_s(session_duration_s(args)));timeout=False
        except subprocess.TimeoutExpired:
            timeout=True;code=124;(args.out/'STOP_MMIO').write_text('PARENT_DEADLINE_NO_FURTHER_HARDWARE_ACCESS\n')
            # Do not kill/retry an unknown MMIO and do not perform cleanup register writes.
            # The isolated SDK owner may be asked to stop; identity/starttime prevents PID reuse.
            pidfile=args.out/'NATIVE_PID.json'
            if pidfile.exists():
                try:
                    info=json.loads(pidfile.read_text());proc=Path('/proc')/str(info['pid'])
                    if proc.joinpath('stat').read_text().split()[21]==info['proc_starttime'] and info['native'].encode() in proc.joinpath('cmdline').read_bytes():os.killpg(info['pid'],signal.SIGTERM)
                except (FileNotFoundError,ProcessLookupError):pass
        finally:
            if stop_relay is not None:stop_relay.close()
            for capture in worker_captures:
                try:worker_log_stats.append(capture.finish())
                except BaseException as exc:worker_log_errors.append(str(exc))
            if worker_log_errors and not timeout:code=70
    result={'schema':'D1_BOUNDED_PARENT_V1','worker_pid':child.pid,'returncode':code,'timeout':timeout,'automatic_retry':False,
            'required_action':'STOP_ALL_HARDWARE_ACCESS_NO_RETRY_NO_AUTOMATIC_CLEANUP_WRITE' if timeout else 'REVIEW_WORKER_RESULT','out':str(args.out)}
    if extended_mode(args):result.update(extended_session_s=600,runtime_unbounded=False,worker_log_retention=worker_log_stats,worker_log_errors=worker_log_errors)
    (args.out/'SUPERVISOR_RESULT.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True);return code

if __name__=='__main__':raise SystemExit(main())
