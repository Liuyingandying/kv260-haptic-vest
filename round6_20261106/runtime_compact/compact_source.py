"""Fixed binary frames through existing same-UID bounded UDS lifecycle.

 No SDK or socket is opened on import. LatestSlot remains exactly one record.
"""
from pathlib import Path
import hashlib,json
from bridge_r4.source import SeqpacketFrameSource,ReceivedNative,SourceProtocolError,SourceClosed
from compact_codec import decode,identity_digest
from compact_producer import CompactProducer

class CompactFrameSource(SeqpacketFrameSource):
    def _ingest(self,data,flags=0,*,source_origin='G6C1_SAME_UID_NATIVE_BINARY_NOT_CAPTURE_TIME'):
        if not data:raise SourceClosed('IPC_PEER_EOF')
        if flags&32 or len(data)!=512:raise SourceProtocolError('G6C1_ATOMIC_LENGTH_OR_TRUNCATION')
        receipt=self.clock_ns()
        try:frame=decode(data)
        except ValueError as exc:self.stats['protocol_errors']+=1;raise SourceProtocolError(str(exc)) from exc
        ended=self.clock_ns()
        if self._last_session is not None and frame.session!=self._last_session:
            raise SourceProtocolError('CAPTURE_SESSION_CHANGED_NEW_SOURCE_REQUIRED')
        if self._last_sequence is not None:
            if frame.sequence==self._last_sequence or frame.sdk_index==self._last_sdk_index:
                self.stats['rejected_duplicate']+=1;return False
            if frame.sequence<self._last_sequence or frame.sdk_index<self._last_sdk_index:
                self.stats['rejected_out_of_order']+=1;return False
            self.stats['sequence_gaps']+=max(0,frame.sequence-self._last_sequence-1)
            self.stats['sdk_index_gaps']+=max(0,frame.sdk_index-self._last_sdk_index-1)
        native={'schema':'G6C1_PRIVATE_DECODED_V1','compact_packet':data,
          'session':frame.session,'sequence':frame.sequence,'sdk_frame_index':frame.sdk_index,
          'sdk_device_timestamp_us':frame.sdk_device_us,'sdk_system_timestamp_us':frame.sdk_system_us,
          'host_wait_begin_ns':frame.wait_begin_ns,'host_wait_end_ns':frame.wait_end_ns,
          'processing_begin_ns':frame.processing_begin_ns,'processing_end_ns':frame.processing_end_ns,
          'raw_sha256':frame.raw_sha256.hex(),'raw_valid_pixels':frame.raw_valid_pixels,
          'raw_valid_ratio':frame.raw_valid_pixels/256000,'raw_zero_pixels':frame.raw_zero_pixels,
          'raw_nonzero_out_of_measurement_range_pixels':frame.raw_out_of_range_pixels,
          'native_stage_ns':{}, # Core-only duration is not sent; full JSON retains it.
          'offline_equivalent_core_pipeline_ns':frame.processing_end_ns-frame.processing_begin_ns,
          'cpp_full_validator_ns':frame.validation_ns}
        self.slot.offer(ReceivedNative(native,receipt,source_origin,ended))
        self._last_session=frame.session;self._last_sequence=frame.sequence;self._last_sdk_index=frame.sdk_index
        self.stats['messages']+=1;self.stats['bytes']+=len(data)
        self.stats['compact_decode_ns_total']=self.stats.get('compact_decode_ns_total',0)+ended-receipt
        self.stats['compact_decode_ns_max']=max(self.stats.get('compact_decode_ns_max',0),ended-receipt)
        self.stats['JSON_parser']='NOT_USED_G6C1_FIXED_BINARY'
        return True

class BoundCompactProducer:
    """Binds the new native session's separately saved SDK device identity once.

    The launcher verifies the native ELF; identity file alone is not hardware
    authentication. No stream is opened here and old output directories cannot
    be reused by the launcher.
    """
    def __init__(self,binding_path,*,session_started_monotonic_ns,native_binary_sha256,clock_ns=None):
        self.path=Path(binding_path);self.started=session_started_monotonic_ns;self.native_sha=native_binary_sha256
        self.clock_ns=clock_ns;self.producer=None;self.identity=None
    def produce_owned(self,native,**times):
        if not isinstance(native,dict) or native.get('schema')!='G6C1_PRIVATE_DECODED_V1':
            raise ValueError('G6C1_DECODED_NATIVE_REQUIRED')
        if self.producer is None:
            if self.path.is_symlink():raise ValueError('COMPACT_BINDING_SYMLINK_FORBIDDEN')
            value=json.loads(self.path.read_text(encoding='utf8'))
            if value.get('schema')!='G6C1_DEVICE_BINDING_V1' or identity_digest(value).hex()!=value.get('sha256'):
                raise ValueError('COMPACT_BINDING_DIGEST_MISMATCH')
            self.identity={k:value.get(k) for k in ('vid','pid','uid','serial','firmware')}
            kw={} if self.clock_ns is None else {'clock_ns':self.clock_ns}
            self.producer=CompactProducer(self.identity,source_kind='LIVE_NATIVE_PIPE',session_started_monotonic_ns=self.started,
              native_binary_sha256=self.native_sha,**kw)
        result=self.producer.produce_owned(native['compact_packet'],**times)
        native['device_identity']=self.identity
        return result
