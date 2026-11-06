"""G6C1 observation IPC decoder. No SDK, device, socket or MMIO on import."""
from dataclasses import dataclass
import hashlib,math,struct,zlib
from protocol_prepare.obs2_codec import SideObservation

BYTES,HEADER,MATRIX,UNKNOWN=512,224,224,480

def identity_digest(identity):
    if (not isinstance(identity,dict) or identity.get('vid')!=0x2bc5 or identity.get('pid')!=0x0614 or
        not isinstance(identity.get('uid'),str) or not identity['uid']):
        raise ValueError('ACTUAL_GEMINI_DEVICE_IDENTITY_REQUIRED')
    values=['2bc5:0614',identity['uid'],identity.get('serial') or '',identity.get('firmware') or '']
    if any(not isinstance(x,str) or '\x00' in x for x in values):raise ValueError('DEVICE_IDENTITY_STRING')
    return hashlib.sha256('\x00'.join(values).encode()).digest()

@dataclass(frozen=True)
class CompactFrame:
    raw:bytes
    session:int
    sequence:int
    sdk_index:int
    session_started_ns:int
    wait_begin_ns:int
    wait_end_ns:int
    processing_begin_ns:int
    processing_end_ns:int
    encoding_begin_ns:int
    sdk_device_us:int
    sdk_system_us:int
    raw_sha256:bytes
    identity_sha256:bytes
    scale_m:float
    raw_valid_pixels:int
    raw_zero_pixels:int
    raw_out_of_range_pixels:int
    unknown_cells:int
    geometry_quality:int
    left:SideObservation
    right:SideObservation
    validation_ns:int
    matrix:bytes
    unknown:bytes

def decode(data):
    if type(data) is not bytes or len(data)!=BYTES:raise ValueError('G6C1_EXACT_512_BYTES')
    if struct.unpack_from('<4sHHI',data)!=(b'G6C1',1,HEADER,BYTES):raise ValueError('G6C1_HEADER_VERSION_LENGTH')
    expected=struct.unpack_from('<I',data,12)[0]
    checked=data[:12]+b'\0'*4+data[16:]
    if zlib.crc32(checked)&0xffffffff!=expected:raise ValueError('G6C1_CRC_MISMATCH')
    flags,session=struct.unpack_from('<II',data,16)
    if flags!=15 or session==0 or data[199] or any(data[216:224]):raise ValueError('G6C1_FLAGS_OR_RESERVED')
    times=struct.unpack_from('<10Q',data,24)
    sequence,sdk_index,started,wait_begin,wait_end,processing_begin,processing_end,encoding_begin,device_us,system_us=times
    if not 0<sequence<=0xffffffff or not 0<started<=wait_begin<=wait_end<=processing_begin<=processing_end<=encoding_begin:
        raise ValueError('G6C1_SOURCE_CLOCK_ORDER')
    raw_sha=data[104:136];identity_sha=data[136:168]
    if not any(raw_sha) or not any(identity_sha):raise ValueError('G6C1_RAW_AND_IDENTITY_DIGEST_REQUIRED')
    if struct.unpack_from('<HHI',data,168)!=(640,400,1280):raise ValueError('G6C1_FIXED_NATIVE_GEOMETRY')
    scale=struct.unpack_from('<d',data,176)[0]
    if not math.isfinite(scale) or scale<=0:raise ValueError('G6C1_SCALE')
    valid,zero,out=struct.unpack_from('<III',data,184)
    if valid+zero+out!=256000:raise ValueError('G6C1_RAW_PIXEL_PARTITION')
    unknown_cells=struct.unpack_from('<H',data,196)[0];quality=data[198]
    matrix=data[MATRIX:UNKNOWN];unknown=data[UNKNOWN:]
    if any(v>4 for v in matrix) or unknown_cells!=sum(v.bit_count() for v in unknown):raise ValueError('G6C1_MATRIX_UNKNOWN')
    def side(offset):
        kind,flags,distance=struct.unpack_from('<BBH',data,offset)
        return SideObservation(kind,0xffffffff if distance==0xffff else distance,flags).validate()
    left,right=side(200),side(204)
    if quality not in (0,1,3):raise ValueError('G6C1_GEOMETRY_QUALITY')
    if quality==0 and (unknown_cells or left.flags!=5 or right.flags!=5):raise ValueError('G6C1_COMPLETE_WITH_UNKNOWN')
    if quality==3 and (any(matrix) or unknown_cells!=256 or left.kind or right.kind):raise ValueError('G6C1_UNUSABLE_INCONSISTENT')
    return CompactFrame(data,session,sequence,sdk_index,started,wait_begin,wait_end,processing_begin,processing_end,encoding_begin,
      device_us,system_us,raw_sha,identity_sha,scale,valid,zero,out,unknown_cells,quality,left,right,
      struct.unpack_from('<Q',data,208)[0],matrix,unknown)
