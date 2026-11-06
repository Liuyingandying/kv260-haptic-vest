"""C++-validated compact native observations -> unchanged inhibited OBS2.

 Each actual producer ELF/package is separately hash-bound by the launcher;
 CRC and same-UID IPC do not authenticate a malicious publisher. Full258-region
 witnesses and raw hashes remain in independent native frames.jsonl, identified
 by session/sequence/raw_sha. This is not an arbitrary-JSON schema bypass.
"""
import copy,hashlib,time
from compact_codec import decode,identity_digest,CompactFrame
from protocol_prepare.obs2_codec import ObservationPacket,GeometryQuality,MAX_RECEIPT_AGE_US
from protocol_prepare.obs2_producer import ProducedObservation

class CompactOwnedObservation:
    __slots__=('_frame','_observation','_source','_packet','_consumed')
    def __init__(self,frame,observation,source,wire):
        self._frame=frame;self._observation=copy.deepcopy(observation)
        self._source=copy.deepcopy(source);self._packet=bytes(wire);self._consumed=False
    @property
    def frame(self):return self._frame
    @property
    def observation(self):return copy.deepcopy(self._observation)
    @property
    def source(self):return copy.deepcopy(self._source)
    @property
    def packet(self):return self._packet
    @property
    def consumed(self):return self._consumed
    @property
    def matrix(self):return self._frame.matrix
    @property
    def unknown(self):return self._frame.unknown
    def summary(self):
        return {'session':self.frame.session,'sequence':self.frame.sequence,'sdk_frame_index':self.frame.sdk_index,
          'source_kind':self.source['source_kind'],'actual_live':self.source['actual_live'],
          'geometry_state':self.observation['geometry_state'],'unknown_cells':self.frame.unknown_cells,
          'physical_output_permitted':False,'capture_time_verified':False,'capture_age_ns':None}
    def public_snapshot(self):
        raise RuntimeError('COMPACT_PATH_USE_INDEPENDENT_FULL_NATIVE_JSON_EVIDENCE_NOT_FAKE_FULL_SNAPSHOT')
    def discard(self):
        if self.consumed:raise RuntimeError('OWNED_FRAME_ALREADY_CONSUMED')
        self._consumed=True
    def submit_once(self,backend):
        if self.consumed:raise RuntimeError('OWNED_FRAME_ALREADY_CONSUMED')
        if self._source['source_kind']!='LIVE_NATIVE_PIPE' or not self._source['actual_live']:
            raise RuntimeError('REPLAY_CANNOT_HANDOFF_TO_REAL_BACKEND')
        self._consumed=True
        return backend.submit(ProducedObservation(self._packet,copy.deepcopy(self._observation),copy.deepcopy(self._source),
            self._source['receipt_age_ns'],{'schema':'G6C1_CPP_FULL_QUALITY_VALIDATED_VIEW'}))

class CompactProducer:
    @property
    def source_kind(self):return self._source_kind
    @property
    def identity(self):return copy.deepcopy(self._identity)
    def __init__(self,identity,*,source_kind,session_started_monotonic_ns=None,native_binary_sha256,clock_ns=time.monotonic_ns):
        if source_kind not in ('LIVE_NATIVE_PIPE','SAVED_REAL_REPLAY'):raise ValueError('EXPLICIT_SOURCE_KIND')
        if source_kind=='LIVE_NATIVE_PIPE' and (type(session_started_monotonic_ns) is not int or session_started_monotonic_ns<=0):
            raise ValueError('LIVE_SESSION_CLOCK_REQUIRED')
        if not isinstance(native_binary_sha256,str) or len(native_binary_sha256)!=64 or any(c not in '0123456789abcdef' for c in native_binary_sha256):
            raise ValueError('HASH_BOUND_COMPACT_NATIVE_ELF_REQUIRED')
        self._identity=copy.deepcopy(identity);self.identity_sha=identity_digest(identity);self._source_kind=source_kind
        self.started=session_started_monotonic_ns;self.native_sha=native_binary_sha256;self.clock_ns=clock_ns
        self.last_session=None;self.last_sequence=0;self.last_sdk=-1;self.last_receipt=-1;self.last_processing=-1
    def produce_owned(self,blob,*,receipt_monotonic_ns,now_monotonic_ns=None):
        # Do not accept an arbitrary caller-constructed dataclass as proof that
        # bytes passed the structural/CRC checks.
        frame=decode(blob.raw if isinstance(blob,CompactFrame) else blob)
        now=self.clock_ns() if now_monotonic_ns is None else now_monotonic_ns
        if type(receipt_monotonic_ns) is not int or type(now) is not int or receipt_monotonic_ns<=0 or now<receipt_monotonic_ns:
            raise ValueError('HOST_RECEIPT_CLOCK_ORDER')
        if receipt_monotonic_ns<=self.last_receipt:raise ValueError('HOST_RECEIPT_CLOCK_REPLAY')
        if now-receipt_monotonic_ns>=250_000_000:raise ValueError('HOST_RECEIPT_TOO_OLD_NOT_CAPTURE_AGE')
        if frame.identity_sha256!=self.identity_sha:raise ValueError('COMPACT_DEVICE_BINDING_MISMATCH')
        if self.last_session is not None and frame.session!=self.last_session:raise ValueError('NEW_CAPTURE_SESSION_REQUIRES_NEW_PRODUCER_INSTANCE')
        if frame.sequence<=self.last_sequence:raise ValueError('DUPLICATE_OR_OUT_OF_ORDER_SEQUENCE')
        if frame.sdk_index<=self.last_sdk:raise ValueError('SDK_INDEX_REPLAY_OR_MISSING')
        live=self.source_kind=='LIVE_NATIVE_PIPE'
        if live:
            if self.started>frame.session_started_ns or frame.encoding_begin_ns>receipt_monotonic_ns:
                raise ValueError('LIVE_CLOCK_ORDER_OR_PRE_SESSION_FRAME')
            if frame.processing_end_ns<=self.last_processing:raise ValueError('LIVE_PROCESSING_CLOCK_REPLAY')
            if now-frame.wait_end_ns>=250_000_000:raise ValueError('SDK_DELIVERY_TO_RESULT_TOO_OLD_NOT_CAPTURE_AGE')
        ended=self.clock_ns();age=(ended-receipt_monotonic_ns+999)//1000
        if ended<now:raise ValueError('OBS2_HOST_MONOTONIC_ORDER')
        if live and ended-frame.wait_end_ns>=250_000_000:
            raise ValueError('SDK_DELIVERY_TO_RESULT_TOO_OLD_NOT_CAPTURE_AGE')
        if age>=MAX_RECEIPT_AGE_US:raise ValueError('OBS2_RECEIPT_EXPIRED_DURING_PRODUCER')
        packet=ObservationPacket(frame.session,frame.sequence,receipt_monotonic_ns,age,frame.geometry_quality,
          frame.matrix,frame.unknown,frame.left,frame.right)
        wire=packet.encode()
        geometry={0:'COMPLETE',1:'PARTIAL',3:'UNUSABLE'}[frame.geometry_quality]
        obs={'session':frame.session,'sequence':frame.sequence,'matrix_flat':list(frame.matrix),
          'unknown_mask_bytes':list(frame.unknown),'unknown_cells':frame.unknown_cells,'geometry_state':geometry,
          'full_region_evidence':'INDEPENDENT_NATIVE_FRAMES_JSONL; NOT_OMITTED_VALIDATION',
          'component_support_semantics':'VALIDATED_CPP_GFREGION_REAL_COUNTS; NO_FAKE_SUPPORT_FIELDS'}
        source={'session':frame.session,'sequence':frame.sequence,'sdk_frame_index':frame.sdk_index,
          'device_identity':self.identity,'source_kind':self.source_kind,'actual_live':live,
          'timestamp_reference':'HOST_MONOTONIC_IPC_RECEIPT_NOT_CAPTURE','receipt_monotonic_ns':receipt_monotonic_ns,
          'ipc_receipt_monotonic_ns':receipt_monotonic_ns,'encoded_monotonic_ns':ended,'receipt_age_ns':ended-receipt_monotonic_ns,
          'capture_time_verified':False,'capture_monotonic_ns':None,'capture_age_ns':None,'capture_clock_mapping':'UNVERIFIED',
          'raw_sha256':frame.raw_sha256.hex(),'native_binary_sha256':self.native_sha,
          'compact_packet_sha256':hashlib.sha256(frame.raw).hexdigest(),
          'cpp_full_region_validator_ns':frame.validation_ns,
          'timing_evidence':{'host_wait_begin_ns':frame.wait_begin_ns,'host_wait_end_ns':frame.wait_end_ns,
            'processing_begin_ns':frame.processing_begin_ns,'processing_end_ns':frame.processing_end_ns,
            'sdk_device_timestamp_us':frame.sdk_device_us,'sdk_system_timestamp_us':frame.sdk_system_us}}
        self.last_session=frame.session;self.last_sequence=frame.sequence;self.last_sdk=frame.sdk_index
        self.last_receipt=receipt_monotonic_ns;self.last_processing=frame.processing_end_ns
        return CompactOwnedObservation(frame,obs,source,wire)
