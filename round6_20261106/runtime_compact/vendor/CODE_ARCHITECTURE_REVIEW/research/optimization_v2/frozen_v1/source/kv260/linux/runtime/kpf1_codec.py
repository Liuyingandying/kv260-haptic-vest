"""KPF1 wire codec. Local bytes/files only; no MMIO, devices or camera imports."""
from dataclasses import dataclass
from enum import IntEnum
import math
import struct

MAGIC = 0x3146504B
VERSION = 1
HEADER_BYTES = 96
MATRIX_BYTES = 256
UNKNOWN_BYTES = 32
TOTAL_BYTES = 384
COVERAGE_PPM = 900000
MAX_AGE_US = 250000
FUTURE_US = 40000
FRESH, GAP, RESUME = 1, 2, 4
SUPPORTED, UNKNOWN, COVERAGE_COMPLETE = 1, 2, 4
NO_DISTANCE = 0xFFFFFFFF
WORDS = struct.Struct('<96I')

class Quality(IntEnum):
    COMPLETE = 0
    PARTIAL = 1
    UNKNOWN = 2
    UNUSABLE = 3

class RiskKind(IntEnum):
    UNKNOWN = 0
    NO_NEAR = 1
    WITHIN_ATTENTION = 2
    CAUTION = 3
    DANGER = 4
    SUPPORTED_FAR_UNCERTAIN = 5

def pack_unknown(mask):
    if len(mask) != 256 or not all(type(x) is bool for x in mask):
        raise ValueError('UNKNOWN needs 256 explicit boolean values')
    return bytes(sum(int(mask[byte*8+bit]) << bit for bit in range(8)) for byte in range(32))

def unpack_unknown(mask):
    if len(mask) != 32:
        raise ValueError('UNKNOWN requires 32 bytes')
    return [bool(mask[i//8] & (1 << (i%8))) for i in range(256)]

@dataclass(frozen=True)
class Side:
    kind: int = RiskKind.UNKNOWN
    distance_mm: int = NO_DISTANCE
    flags: int = UNKNOWN

    def validate(self):
        if self.kind not in range(6) or self.flags not in (2, 3, 5, 6):
            raise ValueError('side kind/flags')
        if self.kind == RiskKind.UNKNOWN:
            if self.flags not in (2, 6) or self.distance_mm != NO_DISTANCE:
                raise ValueError('unknown side must have no supported distance')
        elif self.flags not in (3, 5) or not 0 < self.distance_mm < NO_DISTANCE:
            raise ValueError('known side requires support and a distance')
        if self.kind == RiskKind.NO_NEAR and self.flags != 5:
            raise ValueError('NO_NEAR requires complete supported observation')
        if self.kind == RiskKind.SUPPORTED_FAR_UNCERTAIN and self.flags != 3:
            raise ValueError('uncertain far candidate requires retained UNKNOWN')
        return self

@dataclass(frozen=True)
class Packet:
    session: int
    sequence: int
    capture_ns: int
    age_us: int
    quality: int
    flags: int
    producer_fault: int
    left: Side
    right: Side
    coord: int
    width: int
    height: int
    matrix: bytes
    unknown: bytes

    def validate(self):
        for name, value, limit in (('session', self.session, 0xFFFFFFFF),
                                   ('sequence', self.sequence, 0xFFFFFFFFFFFFFFFF),
                                   ('capture_ns', self.capture_ns, 0xFFFFFFFFFFFFFFFF)):
            if type(value) is not int or not 0 <= value <= limit or (name != 'capture_ns' and not value):
                raise ValueError(name)
        if type(self.age_us) is not int or not -(1 << 31) <= self.age_us < (1 << 31):
            raise ValueError('age_us')
        if self.quality not in range(4) or self.flags & ~7 or self.producer_fault & ~7:
            raise ValueError('quality/source flags/fault')
        if self.coord not in (1, 2):
            raise ValueError('coordinate space')
        gap_shape = self.width == self.height == 0 and self.quality == Quality.UNUSABLE and self.flags & GAP
        if not gap_shape and not (16 <= self.width <= 4096 and 16 <= self.height <= 4096):
            raise ValueError('shape')
        if not isinstance(self.matrix, bytes) or len(self.matrix) != MATRIX_BYTES or any(v > 4 for v in self.matrix):
            raise ValueError('matrix requires 256 level0..4 bytes')
        if not isinstance(self.unknown, bytes) or len(self.unknown) != UNKNOWN_BYTES:
            raise ValueError('UNKNOWN requires 32 bytes')
        self.left.validate(); self.right.validate()
        source_fresh = bool(self.flags & FRESH)
        if self.quality != Quality.UNUSABLE and (not source_fresh or self.flags & GAP or self.producer_fault):
            raise ValueError('observable quality requires fresh nonfaulted source')
        if self.quality == Quality.UNUSABLE and source_fresh:
            raise ValueError('UNUSABLE must not claim source freshness')
        if source_fresh and not -FUTURE_US <= self.age_us <= MAX_AGE_US:
            raise ValueError('declared freshness age outside contract')
        if self.flags & GAP and (source_fresh or self.quality != Quality.UNUSABLE):
            raise ValueError('GAP must be nonfresh UNUSABLE')
        if self.quality == Quality.COMPLETE and (not source_fresh or self.flags & GAP or self.producer_fault or
                any(self.unknown) or self.left.flags != 5 or self.right.flags != 5):
            raise ValueError('COMPLETE must have no unknown regions and no producer fault')
        if self.quality == Quality.PARTIAL and not (any(self.unknown) or self.left.flags & UNKNOWN or self.right.flags & UNKNOWN):
            raise ValueError('PARTIAL must retain an unknown region')
        if self.quality in (Quality.UNKNOWN, Quality.UNUSABLE) and (any(self.matrix) or self.unknown != b'\xff'*32):
            raise ValueError('unobservable matrix must be zero WITH all UNKNOWN bits set')
        return self

    @property
    def control_candidate(self):
        return (self.quality == Quality.COMPLETE and self.flags & FRESH and
                not self.producer_fault and not self.flags & GAP and
                not any(self.unknown) and self.left.flags == self.right.flags == 5 and
                self.left.kind == self.right.kind == RiskKind.NO_NEAR)

    def encode(self):
        self.validate()
        header = [MAGIC, VERSION, TOTAL_BYTES, self.session,
                  self.sequence & 0xFFFFFFFF, self.sequence >> 32,
                  self.capture_ns & 0xFFFFFFFF, self.capture_ns >> 32,
                  self.age_us & 0xFFFFFFFF, int(self.quality), self.flags, self.producer_fault,
                  int(self.left.kind), self.left.distance_mm, self.left.flags,
                  int(self.right.kind), self.right.distance_mm, self.right.flags,
                  COVERAGE_PPM, self.coord, self.width | (self.height << 16),
                  HEADER_BYTES, UNKNOWN_BYTES, MATRIX_BYTES]
        return struct.pack('<24I', *header) + self.matrix + self.unknown

def decode(data):
    if len(data) != TOTAL_BYTES:
        raise ValueError('packet length')
    words = WORDS.unpack(data)
    if (words[0:3] != (MAGIC, VERSION, TOTAL_BYTES) or words[18] != COVERAGE_PPM or
            words[21:24] != (HEADER_BYTES, UNKNOWN_BYTES, MATRIX_BYTES)):
        raise ValueError('magic/version/length/layout/coverage')
    age = words[8] if words[8] < (1 << 31) else words[8] - (1 << 32)
    return Packet(words[3], words[4] | words[5] << 32, words[6] | words[7] << 32,
                  age, words[9], words[10], words[11], Side(*words[12:15]), Side(*words[15:18]),
                  words[19], words[20] & 0xFFFF, words[20] >> 16,
                  data[HEADER_BYTES:HEADER_BYTES+MATRIX_BYTES], data[-UNKNOWN_BYTES:]).validate()

def _side(region, baseline_risk):
    complete = region['coverage_state'] == 'COMPLETE'
    flags = (SUPPORTED if region['supported_candidate'] else 0) | (UNKNOWN if region['unknown_region'] else 0) | (COVERAGE_COMPLETE if complete else 0)
    if not region['supported_candidate']:
        return Side(RiskKind.UNKNOWN, NO_DISTANCE, flags)
    distance = region['distance_m']
    if not math.isfinite(distance) or distance <= 0:
        raise ValueError('invalid supported side distance')
    kind = (RiskKind.NO_NEAR if region['observed_no_near_evidence'] else
            RiskKind.SUPPORTED_FAR_UNCERTAIN if not region['supported_obstacle_candidate'] else
            RiskKind.DANGER if baseline_risk['level'] == 'DANGER' else
            RiskKind.CAUTION if baseline_risk['level'] == 'CAUTION' else RiskKind.WITHIN_ATTENTION)
    return Side(kind, max(1, int(math.floor(distance*1000+.5))), flags)

def from_envelope(envelope, *, now_ns=None, resume=False, sequence=None, session=None):
    """Encode one immutable V1.1 snapshot. `now_ns` must share its PS clock domain.

    Wire sequence is a publisher event sequence, not a stable tracked-object ID.
    GAP with source sequence zero needs the publisher's explicit next sequence.
    Saved-file replay must use recorded clocks, never claim this is a live frame.
    """
    if envelope.get('schema') != 'basic_depth_regional_quality_v11':
        raise ValueError('requires regional_quality_v11 envelope')
    if envelope['quality_config']['coverage_complete_ratio'] != .9:
        raise ValueError('KPF1 v1 freezes the existing 90 percent quality rule')
    old, quality = envelope['legacy_result'], envelope['perception']
    now_ns = old['processing_ns'] if now_ns is None else now_ns
    if type(now_ns) is not int:
        raise ValueError('now_ns must be in the recorded/same monotonic domain')
    delta = now_ns - old['capture_ns']
    age = ((abs(delta)+999)//1000) * (-1 if delta < 0 else 1)
    fresh = old['frame_state']['fresh'] is True and old['frame_state']['reason'] == 'OK' and -FUTURE_US <= age <= MAX_AGE_US
    gap = old['frame_state']['reason'] == 'STREAM_GAP'
    flags = (FRESH if fresh else 0) | (GAP if gap else 0) | (RESUME if resume else 0)
    producer_fault = 1 if gap else 4 if not fresh else 0
    if not fresh:
        q = Quality.UNUSABLE
    elif quality['state'] == 'UNUSABLE':
        q = Quality.UNKNOWN
    elif quality['state'] == 'PARTIAL_WITH_OBSERVATIONS':
        q = Quality.PARTIAL
    elif quality['state'] == 'COMPLETE_OBSERVATIONS':
        q = Quality.COMPLETE
    else:
        raise ValueError('unknown perception state')
    if q in (Quality.UNKNOWN, Quality.UNUSABLE):
        matrix, unknown, left, right = bytes(256), b'\xff'*32, Side(), Side()
    else:
        matrix, unknown = bytes(old['matrix']), pack_unknown(quality['unknown_mask'])
        left = _side(quality['sides']['left'], old['left'])
        right = _side(quality['sides']['right'], old['right'])
    return Packet(old['session'] if session is None else session,
                  old['seq'] if sequence is None else sequence, old['capture_ns'], age, q, flags,
                  producer_fault, left, right, 1 if old['coordinate_space']=='native_depth' else 2,
                  old['width'], old['height'], matrix, unknown).validate()
