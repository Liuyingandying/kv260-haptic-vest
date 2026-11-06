"""DGD1 digital bring-up, explicitly separate from production KPF1/FRESH.

Allows unknown capture age, >250 ms delivery age, PARTIAL and a subset of
online PCA blocks. Only one VALID/non-UNKNOWN nonzero source cell is projected;
other projected cells are DISABLED, never claimed observed-safe. V+ must remain
disconnected under the owning experiment procedure. Physical permission=false.
"""
from dataclasses import dataclass
import hashlib
import math
import struct
import time
import zlib
from .producer import ControlRejected

MAGIC, VERSION, FLAGS, DESIGN_ID = 0x31444744, 1, 3, 0x52364431
ALLOWED_ONLINE_MASK = 0xf0f0
PCA_ADDRESSES = (0x4d,0x4e,0x4f,0x44,0x49,0x4a,0x4b,0x4c,0x40,0x41,0x42,0x43,0x45,0x46,0x47,0x48)


def mapping(index):
    if type(index) is not int or not 0 <= index < 256:
        raise ValueError('SOURCE_INDEX_0_TO_255_REQUIRED')
    row, col = divmod(index, 16)
    if col < 8:
        block, channel = row//2, (row%2)*8+col
    else:
        block = 8+row//2
        channel = (row%2)*8+((15-col) if block <= 11 else (col-8))
    return {'source_index': index, 'row': row, 'column': col, 'block': block,
            'channel': channel, 'tca_channel': block//4, 'tca_mask': 1<<(block//4),
            'pca_address': PCA_ADDRESSES[block]}


def crc32_packet(data):
    return zlib.crc32(data[:44]+bytes(4)+data[48:])&0xffffffff


def decode_digital(data):
    if type(data) is not bytes or len(data) != 384:
        raise ValueError('DGD1_EXACT_384_BYTES_REQUIRED')
    w = struct.unpack('<96I', data)
    if w[:3] != (MAGIC, VERSION, 384) or w[6:8] != (0,0) or w[10] != FLAGS:
        raise ValueError('DGD1_MAGIC_VERSION_CAPTURE_UNKNOWN_OR_FLAGS')
    if w[11] != crc32_packet(data):
        raise ValueError('DGD1_CRC_MISMATCH')
    if not w[3] or not (w[4] | w[5]<<32) or w[9] not in (0,1):
        raise ValueError('DGD1_IDENTITY_OR_GEOMETRY_QUALITY')
    if (w[15:18] != (0,0,0) or w[18] != 900000 or w[19] not in (1,2)
            or not 16 <= (w[20]&0xffff) <= 4096 or not 16 <= w[20]>>16 <= 4096
            or w[21:24] != (96,32,256)):
        raise ValueError('DGD1_RESERVED_OR_LAYOUT')
    target = mapping(w[12])
    if (w[13],w[14]) != (target['block'],target['channel']):
        raise ValueError('DGD1_SELECTED_MAP_MISMATCH')
    matrix, unknown = data[96:352], data[352:]
    if any(x>4 for x in matrix) or not 1 <= matrix[w[12]] <= 4 or sum(x!=0 for x in matrix)!=1:
        raise ValueError('DGD1_EXACT_ONE_NONZERO_PROJECTION_REQUIRED')
    if unknown[w[12]//8] & (1<<(w[12]%8)):
        raise ValueError('DGD1_SELECTED_CELL_IS_UNKNOWN')
    return {'session': w[3], 'sequence': w[4]|w[5]<<32, 'receipt_age_us':w[8],
            'geometry_quality':w[9], 'matrix':matrix, 'unknown':unknown,
            'selected':{**target,'level':matrix[w[12]],'region_quality':'VALID'},
            'CRC32':w[11], 'flags':w[10]}


def _compact(data, identity):
    if type(data) is not bytes or len(data)!=512:
        raise ValueError('G6C1_EXACT_512_BYTES_REQUIRED')
    if struct.unpack_from('<4sHHI',data)!=(b'G6C1',1,224,512):
        raise ValueError('G6C1_HEADER')
    if struct.unpack_from('<I',data,12)[0] != zlib.crc32(data[:12]+bytes(4)+data[16:])&0xffffffff:
        raise ValueError('G6C1_CRC_MISMATCH')
    flags, session = struct.unpack_from('<II',data,16)
    if flags!=15 or not session or data[199] or any(data[216:224]):
        raise ValueError('G6C1_FULL_CPP_VALIDATION_FLAGS_OR_RESERVED')
    seq,sdk,start,wb,we,pb,pe,enc,dev,system = struct.unpack_from('<10Q',data,24)
    if not 0 < seq <= 0xffffffff or not 0 < start <= wb <= we <= pb <= pe <= enc:
        raise ValueError('G6C1_SOURCE_TIME_ORDER')
    if identity.get('vid')!=0x2bc5 or identity.get('pid')!=0x0614 or not identity.get('uid'):
        raise ValueError('ACTUAL_GEMINI_DEVICE_IDENTITY_REQUIRED')
    identity_parts = ['2bc5:0614',identity['uid'],identity.get('serial') or '',identity.get('firmware') or '']
    if any(not isinstance(x,str) or '\x00' in x for x in identity_parts):
        raise ValueError('DEVICE_IDENTITY_STRINGS_REQUIRED')
    if data[136:168] != hashlib.sha256('\x00'.join(identity_parts).encode()).digest():
        raise ValueError('G6C1_DEVICE_IDENTITY_DIGEST_MISMATCH')
    if struct.unpack_from('<HHI',data,168)!=(640,400,1280):
        raise ValueError('G6C1_DIMENSIONS_OR_STRIDE')
    scale=struct.unpack_from('<d',data,176)[0]
    if not math.isfinite(scale) or scale<=0:
        raise ValueError('G6C1_POSITIVE_FINITE_SCALE_REQUIRED')
    counts = struct.unpack_from('<III',data,184)
    if sum(counts)!=256000 or not any(data[104:136]):
        raise ValueError('G6C1_RAW_COUNT_OR_SHA')
    matrix,unknown=data[224:480],data[480:]
    if any(x>4 for x in matrix) or struct.unpack_from('<H',data,196)[0] != sum(x.bit_count() for x in unknown):
        raise ValueError('G6C1_MATRIX_OR_UNKNOWN_COUNT')
    quality=data[198]
    if quality not in (0,1,3) or (quality==0 and any(unknown)) or (quality==3 and (any(matrix) or unknown!=b'\xff'*32)):
        raise ValueError('G6C1_QUALITY_INCONSISTENT')
    for offset in (200,204):
        kind,flags,distance=struct.unpack_from('<BBH',data,offset)
        if (kind not in range(6) or flags not in (2,3,5,6) or
                (kind==0 and (flags not in (2,6) or distance!=0xffff)) or
                (kind!=0 and (flags not in (3,5) or distance in (0,0xffff))) or
                (kind==1 and flags!=5) or (kind==5 and flags!=3) or (quality==0 and flags!=5)):
            raise ValueError('G6C1_SIDE_OBSERVATION_INVALID')
    return {'session':session,'sequence':seq,'sdk_frame_index':sdk,'host_wait_end_ns':we,
            'encoding_begin_ns':enc,'raw_sha256':data[104:136].hex(),'matrix':matrix,
            'unknown':unknown,'geometry_quality':quality,'sdk_device_us':dev,'sdk_system_us':system}


@dataclass(frozen=True)
class DigitalFrame:
    packet: bytes
    original_matrix: bytes
    original_unknown: bytes
    compact_packet_sha256: str
    raw_sha256: str
    native_binary_sha256: str
    sdk_frame_index: int
    receipt_monotonic_ns: int
    evaluated_monotonic_ns: int
    host_wait_end_ns: int
    source_kind: str
    selected: dict
    # Set only for an explicitly supplied D1 producer epoch. G6C1 keeps its
    # original acquisition identity; this does not establish capture freshness.
    acquisition_session: int | None = None

    @property
    def actual_live(self):
        return self.source_kind == 'LIVE_NATIVE_PIPE'

    @property
    def session(self):
        return decode_digital(self.packet)['session']

    @property
    def sequence(self):
        return decode_digital(self.packet)['sequence']

    def as_record(self):
        d=decode_digital(self.packet)
        record={'schema':'R6_D1_SINGLE_CELL_PROJECTION_V1','session':d['session'],
                'sequence':d['sequence'],'sdk_frame_index':self.sdk_frame_index,
                'source_kind':self.source_kind,'actual_live':self.actual_live,
                'raw_sha256':self.raw_sha256,'G6C1_packet_sha256':self.compact_packet_sha256,
                'native_binary_sha256':self.native_binary_sha256,
                'original_matrix':list(self.original_matrix),'original_unknown_hex':self.original_unknown.hex(),
                'original_matrix_sha256':hashlib.sha256(self.original_matrix).hexdigest(),
                'original_unknown_sha256':hashlib.sha256(self.original_unknown).hexdigest(),
                'projection_matrix':list(d['matrix']),'projection_unknown_hex':d['unknown'].hex(),
                'projection_packet_hex':self.packet.hex(),'projection_packet_sha256':hashlib.sha256(self.packet).hexdigest(),
                'projection_crc32':d['CRC32'],'selected_source_trace':dict(self.selected),
                'other_projection_cells':'EXPLICIT_DISABLED_NOT_OBSERVED_SAFE',
                'capture_time_verified':False,'capture_age_ns':None,'capture_status':'UNKNOWN',
                'SDK_delivery_age_ns':self.evaluated_monotonic_ns-self.host_wait_end_ns,
                'IPC_receipt_age_ns':self.evaluated_monotonic_ns-self.receipt_monotonic_ns,
                'FRESH':False,'physical_output_permitted':False,'mode':'DIGITAL_BRINGUP_MODE',
                'VPLUS_procedure':'MUST_REMAIN_DISCONNECTED;NOT_INFERRED_FROM_REGISTER_STATE'}
        if self.acquisition_session is not None:
            record.update(schema='R6_D1_SINGLE_CELL_PROJECTION_V2',
                          source_acquisition_session=self.acquisition_session,
                          producer_session=d['session'])
        return record


class DigitalBringupProducer:
    def __init__(self, identity, *, native_binary_sha256, source_kind,
                 clock_ns=time.monotonic_ns, emit=None):
        if source_kind not in ('LIVE_NATIVE_PIPE','SAVED_REAL_REPLAY'):
            raise ValueError('EXPLICIT_LIVE_OR_RECORDED_SOURCE_REQUIRED')
        if (not isinstance(native_binary_sha256,str) or len(native_binary_sha256)!=64
                or any(x not in '0123456789abcdef' for x in native_binary_sha256)):
            raise ValueError('HASH_BOUND_NATIVE_ELF_REQUIRED')
        self.identity=dict(identity);self.native_sha=native_binary_sha256
        self.source_kind,self.clock_ns,self.emit=source_kind,clock_ns,emit
        self.last_session=None;self.last_sequence=0;self.last_sdk=-1;self.last_receipt=-1
        self.last_control_session=None

    def prepare_owned(self, owned, *, online_mask, now_monotonic_ns=None, required_source_index=None, control_session=None):
        return self.prepare_compact(owned.frame.raw, online_mask=online_mask,
                                    receipt_monotonic_ns=owned.source['receipt_monotonic_ns'],
                                    now_monotonic_ns=now_monotonic_ns,required_source_index=required_source_index,
                                    control_session=control_session)

    def prepare_compact(self, blob, *, online_mask, receipt_monotonic_ns, now_monotonic_ns=None, required_source_index=None, control_session=None):
        """Project source bytes; an explicit epoch changes only DGD1 word3/CRC.

        Acquisition replay protection always uses the unchanged G6C1 identity.
        The owner reserves/renews producer epochs after its safe close/rearm.
        """
        f=_compact(blob,self.identity)
        if control_session is not None and (type(control_session) is not int or not 0<control_session<=0xffffffff):
            raise ValueError('D1_CONTROL_SESSION_POSITIVE_U32_REQUIRED')
        producer_session=f['session'] if control_session is None else control_session
        if self.last_control_session is not None and producer_session<self.last_control_session:
            raise ValueError('D1_CONTROL_SESSION_DECREASE_OR_WRAP')
        now=self.clock_ns() if now_monotonic_ns is None else now_monotonic_ns
        if (type(now) is not int or type(receipt_monotonic_ns) is not int or receipt_monotonic_ns<=0
                or now<receipt_monotonic_ns or f['encoding_begin_ns']>receipt_monotonic_ns):
            raise ValueError('D1_HOST_MONOTONIC_SOURCE_ORDER')
        if type(online_mask) is not int or not 0<=online_mask<=0xffff:
            raise ValueError('D1_ONLINE_MASK_REQUIRED')
        if (self.last_session is not None and f['session']!=self.last_session
                or f['sequence']<=self.last_sequence or f['sdk_frame_index']<=self.last_sdk
                or receipt_monotonic_ns<=self.last_receipt):
            raise ValueError('D1_SOURCE_SESSION_OR_SEQUENCE_REPLAY')
        if f['geometry_quality'] not in (0,1):
            raise ControlRejected(('D1_NO_VALID_GEOMETRY_CANDIDATE',),session=f['session'],sequence=f['sequence'])
        candidates=[mapping(i) for i,v in enumerate(f['matrix']) if v>0 and
                    not f['unknown'][i//8]&(1<<(i%8)) and (online_mask & ALLOWED_ONLINE_MASK)&(1<<mapping(i)['block'])]
        if required_source_index is not None:
            if type(required_source_index) is not int or not 0<=required_source_index<256:
                raise ValueError('D1_LOCKED_SOURCE_INDEX_0_TO_255_REQUIRED')
            candidates=[x for x in candidates if x['source_index']==required_source_index]
            if not candidates:
                raise ControlRejected(('D1_LOCKED_SOURCE_CELL_NOT_VALID_NONUNKNOWN_NONZERO',),
                                      session=f['session'],sequence=f['sequence'])
        if not candidates:
            raise ControlRejected(('D1_NO_VALID_NONUNKNOWN_NONZERO_CELL_ON_ONLINE_BLOCK',),
                                  session=f['session'],sequence=f['sequence'])
        # Prefer known cell128 (block4/ch0) when available, then strongest
        # level and lowest source index. Never invent a source level.
        selected=min(candidates,key=lambda x:(x['source_index']!=128,-f['matrix'][x['source_index']],x['source_index']))
        selected={**selected,'level':f['matrix'][selected['source_index']],
                  'region_quality':'VALID','quality_evidence':'G6C1_UNKNOWN_BIT_ZERO_IFF_CPP_GFREGION_QUALITY_EQ_1',
                  'online_mask_at_selection':online_mask}
        projection=bytearray(256);projection[selected['source_index']]=selected['level']
        words=[MAGIC,VERSION,384,producer_session,f['sequence']&0xffffffff,f['sequence']>>32,
               0,0,min(0xffffffff,(now-receipt_monotonic_ns+999)//1000),f['geometry_quality'],FLAGS,0,
               selected['source_index'],selected['block'],selected['channel'],0,0,0,
               900000,1,640|(400<<16),96,32,256]
        wire=struct.pack('<24I',*words)+bytes(projection)+f['unknown']
        wire=wire[:44]+struct.pack('<I',crc32_packet(wire))+wire[48:]
        decode_digital(wire)
        result=DigitalFrame(wire,f['matrix'],f['unknown'],hashlib.sha256(blob).hexdigest(),f['raw_sha256'],
                            self.native_sha,f['sdk_frame_index'],receipt_monotonic_ns,now,f['host_wait_end_ns'],
                            self.source_kind,selected,f['session'] if control_session is not None else None)
        self.last_session=f['session'];self.last_sequence=f['sequence'];self.last_sdk=f['sdk_frame_index'];self.last_receipt=receipt_monotonic_ns
        self.last_control_session=producer_session
        if self.emit is not None:self.emit(result.as_record())
        return result
