"""Bounded 600s supervisor helpers. Inert on import; no SDK/MMIO interfaces.

The native acquisition is continuous, but this supervisor is explicitly timed.
Rotated evidence is a sampled audit trail, never a claim of full raw retention.
"""
from pathlib import Path
import hashlib,json,os,re,signal,threading

DURATION_S=600
NATIVE_SHA='6eb4aacce1a488b7aee6ac383030322ee2fdab2f42cb6eb828fe9c750c949cd9'
FILE_BYTES=4*1024*1024


class RotatingLog:
    """Keep current and previous segment; streaming hash/count cover omissions."""
    def __init__(self,path,limit=FILE_BYTES):
        self.path=Path(path);self.previous=Path(str(path)+'.previous');self.limit=limit
        self.fp=self.path.open('xb');self.segment_bytes=0;self.total_bytes=0
        self.rotations=0;self.digest=hashlib.sha256();self.closed=False
    def write(self,value):
        data=value.encode('utf8') if isinstance(value,str) else bytes(value)
        # Text calls rotate between complete JSON records; SDK chunks may split lines.
        if len(data)>self.limit:raise RuntimeError('LOG_RECORD_EXCEEDS_SEGMENT_BOUND')
        if self.segment_bytes and self.segment_bytes+len(data)>self.limit:
            self.fp.close();os.replace(self.path,self.previous);self.fp=self.path.open('xb')
            self.segment_bytes=0;self.rotations+=1
        self.fp.write(data);self.segment_bytes+=len(data);self.total_bytes+=len(data);self.digest.update(data)
        return len(value)
    def flush(self):self.fp.flush()
    def close(self):
        if not self.closed:self.fp.close();self.closed=True
    def as_record(self):
        return {'path':str(self.path),'previous_path':str(self.previous),'segment_limit_bytes':self.limit,
                'retained_max_bytes':2*self.limit,'stream_bytes':self.total_bytes,'rotations':self.rotations,
                'all_stream_sha256':self.digest.hexdigest(),'complete_raw_stream_retained':self.rotations<=1}
    def __enter__(self):return self
    def __exit__(self,*exc):self.close()


class PipeCapture:
    """Drain a subprocess pipe without backpressure from a full retained-log quota.

    Count warnings across the complete stream, including rotated-out segments.
    A >64KiB line is explicitly incomplete accounting, never hidden as CLEAN.
    """
    def __init__(self,pipe,path):
        self.pipe=pipe;self.log=RotatingLog(path);self.error=None;self.buffer=b''
        self.overlong_lines=0;self.overlong=False
        self.counts=dict(OB_EVENT_TIMEOUT=0,cancel_request_lines=0,warning_lines=0,error_lines=0)
        self.thread=threading.Thread(target=self._pump,name='bounded-log-drain',daemon=True);self.thread.start()
    def _line(self,data):
        text=data.decode('utf8',errors='replace')
        self.counts['OB_EVENT_TIMEOUT']+=len(re.findall('OB_EVENT_TIMEOUT',text))
        self.counts['cancel_request_lines']+=int('cancel' in text.lower())
        self.counts['warning_lines']+=int(bool(re.search(r'\bwarn(?:ing)?\b',text,re.I)))
        self.counts['error_lines']+=int(bool(re.search(r'\berror\b',text,re.I)))
    def _pump(self):
        try:
            while True:
                data=self.pipe.read(65536)
                if not data:break
                self.log.write(data);self.log.flush();self.buffer+=data
                while b'\n' in self.buffer:
                    line,self.buffer=self.buffer.split(b'\n',1);self._line(line)
                    if self.overlong:self.overlong=False
                if len(self.buffer)>65536:
                    if not self.overlong:self.overlong_lines+=1;self.overlong=True
                    self._line(self.buffer);self.buffer=b''
            if self.buffer:self._line(self.buffer);self.buffer=b''
        except BaseException as exc:self.error=repr(exc)
        finally:
            try:self.pipe.close()
            finally:self.log.close()
    def check(self):
        if self.error:raise RuntimeError('BOUNDED_LOG_DRAIN_FAILED:'+self.error)
    def finish(self,timeout=3):
        self.thread.join(timeout);self.check()
        if self.thread.is_alive():raise RuntimeError('BOUNDED_LOG_DRAIN_NOT_FINISHED_NO_CLEAN_CLAIM')
        return {**self.log.as_record(),'warning_counts':dict(self.counts),
                'warning_accounting':'COMPLETE_STREAM_LINES' if not self.overlong_lines else 'INCOMPLETE_OVERLONG_LINES',
                'overlong_lines':self.overlong_lines}


class StopRequest:
    """Signal handlers only set state/call the supplied *software* relay."""
    def __init__(self,relay=None):self.requested=False;self.signal=None;self.relay=relay;self.previous={}
    def request(self,number=None,frame=None):
        self.requested=True;self.signal=number
        if self.relay is not None:self.relay()
    def install(self):
        for number in (signal.SIGTERM,signal.SIGINT):
            self.previous[number]=signal.getsignal(number);signal.signal(number,self.request)
        return self
    def close(self):
        for number,handler in self.previous.items():signal.signal(number,handler)
        self.previous={}


def evidence_ledger(state,policy):
    required=('processed_frames','evidence_submitted','evidence_written','evidence_pending','raw_samples_saved')
    if not all(type(state.get(k)) is int and state[k]>=0 for k in required):raise RuntimeError('EXTENDED_EVIDENCE_COUNT_TYPES')
    n=state['processed_frames'];written=min(n,1800)
    if not (state.get('schema')=='GEMINI_NATIVE_CONTINUOUS_SESSION_V1' and n>0
            and state['evidence_submitted']==state['evidence_written']==written
            and state['evidence_pending']==0 and state.get('evidence_error')==''
            and state.get('raw_samples_requested')==64 and state['raw_samples_saved']==min(n,64)
            and state.get('full_raw_evidence') is (n<=64)):
        raise RuntimeError('EXTENDED_NATIVE_EVIDENCE_DRAIN_OR_QUOTA_MISMATCH')
    if not (policy.get('schema')=='GEMINI_CONTINUOUS_POLICY_V1' and policy.get('processed_frames')==n
            and policy.get('persisted_frame_records')==written and policy.get('omitted_frame_records')==n-written
            and policy.get('evidence_record_limit')==1800 and policy.get('raw_sample_limit')==64
            and policy.get('resource_recovery')=='UNKNOWN' and policy.get('stop_reason')=='SIGNAL_REQUESTED'):
        raise RuntimeError('EXTENDED_NATIVE_POLICY_OR_EXPECTED_STOP_MISMATCH')
    return {'mode':'EXPLICIT_600S_SINGLE_CONTINUOUS_NATIVE_WITH_BOUNDED_EVIDENCE','processed_frames':n,
            'persisted_complete_frame_records':written,'omitted_complete_frame_records':n-written,
            'raw_samples_saved':min(n,64),'raw_sample_limit':64,'full_run_frame_evidence':n<=1800,
            'SDK_restart':False,'resource_recovery':'UNKNOWN'}
