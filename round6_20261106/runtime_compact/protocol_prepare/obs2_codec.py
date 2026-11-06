"""OBS2/GPO2 v2: exact original observations with explicit unverified capture time.

This independent format is rejected by the unchanged KPF1 v1 magic/version.
No field grants control. CRC is CRC-32/ISO-HDLC (zlib-compatible), with its own
four bytes treated as zero over the complete 384-byte little-endian frame.
"""
from dataclasses import dataclass, field
from enum import IntEnum
import hashlib
import struct
import zlib

MAGIC = 0x324F5047  # bytes G P O 2
VERSION = 2
PACKET_BYTES, HEADER_BYTES, MATRIX_BYTES, UNKNOWN_BYTES = 384, 96, 256, 32
WORD_COUNT, CRC_WORD, CRC_BYTE_OFFSET = 96, 11, 44
COVERAGE_PPM, MAX_RECEIPT_AGE_US = 900000, 250000
OBSERVATION_ONLY, CAPTURE_TIME_UNVERIFIED, HOST_MONOTONIC_RECEIPT = 1, 2, 4
REQUIRED_FLAGS = 7
NO_DISTANCE = 0xFFFFFFFF

MAILBOX_BASE, WINDOW_BYTES = 0x9F000000, 0x10000
COMMAND, STATUS, GENERATION, REASON = 0x080, 0x084, 0x088, 0x08C
SESSION, SEQUENCE_LOW, SEQUENCE_HIGH = 0x090, 0x094, 0x098
CURRENT_RECEIPT_AGE, ACTIVE_CRC, CAPABILITY = 0x09C, 0x0A0, 0x0A4
STAGING, ACTIVE = 0x800, 0xA00
BEGIN, SEAL, ABORT = 1, 2, 3

RX_OPEN, OBS_PRESENT, CONTROL_VALID, FORCE_OFF = 1, 2, 4, 8
BUSY, RECEIPT_RECENT, ERROR, CAPTURE_UNVERIFIED = 16, 32, 64, 128


class GeometryQuality(IntEnum):
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


def _uint(name, value, bits=32, nonzero=False):
    if isinstance(value, bool) or not isinstance(value, int) or not int(nonzero) <= value < (1 << bits):
        raise ValueError('OBS2_INVALID_' + name)


@dataclass(frozen=True)
class SideObservation:
    kind: int = RiskKind.UNKNOWN
    distance_mm: int = NO_DISTANCE
    flags: int = 2

    def validate(self):
        _uint('SIDE_KIND', self.kind)
        _uint('SIDE_DISTANCE', self.distance_mm)
        _uint('SIDE_FLAGS', self.flags)
        if self.kind not in range(6) or self.flags not in (2, 3, 5, 6):
            raise ValueError('OBS2_SIDE_KIND_FLAGS')
        if self.kind == RiskKind.UNKNOWN:
            if self.flags not in (2, 6) or self.distance_mm != NO_DISTANCE:
                raise ValueError('OBS2_UNKNOWN_SIDE_HAS_NO_DISTANCE')
        elif self.flags not in (3, 5) or not 0 < self.distance_mm < NO_DISTANCE:
            raise ValueError('OBS2_KNOWN_SIDE_REQUIRES_REAL_SUPPORT')
        if self.kind == RiskKind.NO_NEAR and self.flags != 5:
            raise ValueError('OBS2_NO_NEAR_REQUIRES_COMPLETE_REGION')
        if self.kind == RiskKind.SUPPORTED_FAR_UNCERTAIN and self.flags != 3:
            raise ValueError('OBS2_UNCERTAIN_FAR_RETAINS_UNKNOWN')
        return self


@dataclass(frozen=True)
class ObservationPacket:
    session: int
    sequence: int
    receipt_ns: int
    receipt_age_us: int
    geometry_quality: int
    matrix: bytes
    unknown: bytes
    left: SideObservation = field(default_factory=SideObservation)
    right: SideObservation = field(default_factory=SideObservation)
    coordinate_space: int = 1
    width: int = 640
    height: int = 400
    flags: int = REQUIRED_FLAGS

    @property
    def control_valid(self):
        return False

    @property
    def force_off(self):
        return True

    @property
    def physical_output_permitted(self):
        return False

    @property
    def capture_time_verified(self):
        return False

    @property
    def matrix_sha256(self):
        return hashlib.sha256(self.matrix).hexdigest()

    def validate(self):
        _uint('SESSION', self.session, nonzero=True)
        _uint('SEQUENCE', self.sequence, 64, True)
        _uint('RECEIPT_NS', self.receipt_ns, 64, True)
        _uint('RECEIPT_AGE_US', self.receipt_age_us)
        if self.receipt_age_us >= MAX_RECEIPT_AGE_US:
            raise ValueError('OBS2_RECEIPT_ALREADY_STALE_NOT_CAPTURE_AGE')
        _uint('GEOMETRY_QUALITY', self.geometry_quality)
        if self.geometry_quality not in range(4) or self.flags != REQUIRED_FLAGS:
            raise ValueError('OBS2_QUALITY_OR_OBSERVATION_ONLY_FLAGS')
        if type(self.matrix) is not bytes or len(self.matrix) != MATRIX_BYTES or any(x > 4 for x in self.matrix):
            raise ValueError('OBS2_MATRIX_REQUIRES_256_LEVEL0_TO4_BYTES')
        if type(self.unknown) is not bytes or len(self.unknown) != UNKNOWN_BYTES:
            raise ValueError('OBS2_UNKNOWN_REQUIRES_32_BYTES')
        if self.coordinate_space not in (1, 2) or not 16 <= self.width <= 4096 or not 16 <= self.height <= 4096:
            raise ValueError('OBS2_COORDINATE_OR_SHAPE')
        self.left.validate(); self.right.validate()
        if self.geometry_quality == GeometryQuality.COMPLETE:
            if any(self.unknown) or self.left.flags != 5 or self.right.flags != 5:
                raise ValueError('OBS2_COMPLETE_GEOMETRY_REQUIRES_COMPLETE_REGIONS')
        elif self.geometry_quality == GeometryQuality.PARTIAL:
            if not any(self.unknown) and not ((self.left.flags | self.right.flags) & 2):
                raise ValueError('OBS2_PARTIAL_REQUIRES_UNKNOWN_REGION')
        elif any(self.matrix) or self.unknown != b'\xff' * 32:
            raise ValueError('OBS2_UNOBSERVABLE_GEOMETRY_RETAINS_ALL_UNKNOWN')
        return self

    def encode(self):
        return encode(self)


def crc32_frame(data: bytes) -> int:
    if type(data) is not bytes or len(data) != PACKET_BYTES:
        raise ValueError('OBS2_CRC_LENGTH')
    raw = bytearray(data)
    raw[CRC_BYTE_OFFSET:CRC_BYTE_OFFSET+4] = b'\x00' * 4
    return zlib.crc32(raw) & 0xFFFFFFFF


def encode(packet: ObservationPacket) -> bytes:
    if not isinstance(packet, ObservationPacket):
        raise TypeError('OBS2_PACKET_REQUIRED')
    packet.validate()
    words = [MAGIC, VERSION, PACKET_BYTES, packet.session,
             packet.sequence & 0xFFFFFFFF, packet.sequence >> 32,
             packet.receipt_ns & 0xFFFFFFFF, packet.receipt_ns >> 32,
             packet.receipt_age_us, int(packet.geometry_quality), REQUIRED_FLAGS, 0,
             int(packet.left.kind), packet.left.distance_mm, packet.left.flags,
             int(packet.right.kind), packet.right.distance_mm, packet.right.flags,
             COVERAGE_PPM, packet.coordinate_space,
             packet.width | packet.height << 16, HEADER_BYTES, UNKNOWN_BYTES, MATRIX_BYTES]
    raw = struct.pack('<24I', *words) + packet.matrix + packet.unknown
    crc = crc32_frame(raw)
    return raw[:CRC_BYTE_OFFSET] + struct.pack('<I', crc) + raw[CRC_BYTE_OFFSET+4:]


def decode(data: bytes) -> ObservationPacket:
    if type(data) is not bytes or len(data) != PACKET_BYTES:
        raise ValueError('OBS2_LENGTH_OR_IMMUTABLE_BYTES')
    words = struct.unpack('<96I', data)
    if (words[0], words[1], words[2]) != (MAGIC, VERSION, PACKET_BYTES):
        raise ValueError('OBS2_MAGIC_VERSION_LENGTH')
    if words[11] != crc32_frame(data):
        raise ValueError('OBS2_CRC_MISMATCH')
    if (words[18], words[21], words[22], words[23]) != (COVERAGE_PPM, HEADER_BYTES, UNKNOWN_BYTES, MATRIX_BYTES):
        raise ValueError('OBS2_FIXED_CONTRACT_LAYOUT')
    packet = ObservationPacket(words[3], words[4] | words[5] << 32,
        words[6] | words[7] << 32, words[8], words[9], data[96:352], data[352:384],
        SideObservation(*words[12:15]), SideObservation(*words[15:18]),
        words[19], words[20] & 0xFFFF, words[20] >> 16, words[10]).validate()
    if encode(packet) != data:
        raise ValueError('OBS2_NONCANONICAL')
    return packet


Packet = ObservationPacket
Quality = GeometryQuality
Side = SideObservation
