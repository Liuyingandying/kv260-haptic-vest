"""Opt-in DGM2 matrix BENCH producer; does not replace accepted DGD1/KPF1.

384 bytes, CRC32 and native G6C1 validation are reused. Capture age remains
UNKNOWN/FRESH=false. Only known cells on explicitly enabled online PCA blocks
may receive a nonzero command. Default level cap1 is 1632/4096 (~39.84%) in
the unchanged Duty LUT: it is not evidence of low motor current or safe power.
This module never accesses hardware. Physical power permission remains false.
"""
from dataclasses import dataclass
import hashlib
import struct

from .digital_bringup import DigitalBringupProducer, _compact, crc32_packet, mapping
from .producer import ControlRejected

MAGIC, VERSION, FLAGS, DESIGN_ID = 0x324d4744, 2, 3, 0x52364d32


def _positive_mask(value, name):
    if type(value) is not int or not 1 <= value <= 0xffff:
        raise ValueError(name + '_POSITIVE_U16_REQUIRED')
    return value


def matrix_stage_indices(cell_limit, *, target_mask=0xf0f0):
    """Stable STM32 block/channel prefix; no dynamic target substitutions."""
    if type(cell_limit) is not int or cell_limit not in (8,32,64,128):
        raise ValueError('DGM2_EXPLICIT_MATRIX_STAGE_8_32_64_128_REQUIRED')
    _positive_mask(target_mask,'DGM2_TARGET_MASK')
    if target_mask != 0xf0f0:
        raise ValueError('DGM2_CURRENT_VERIFIED_STAGES_REQUIRE_F0F0')
    indices=sorted((i for i in range(256) if target_mask & (1<<mapping(i)['block'])),
                   key=lambda i:(mapping(i)['block'],mapping(i)['channel']))
    return tuple(indices[:cell_limit])


def _source_scope(indices, enabled):
    if indices is None:return None
    if not isinstance(indices,(tuple,list)) or not indices or len(indices)>256:
        raise ValueError('DGM2_EXPLICIT_NONEMPTY_SOURCE_SCOPE_REQUIRED')
    if any(type(i) is not int or not 0<=i<256 for i in indices) or len(set(indices))!=len(indices):
        raise ValueError('DGM2_SOURCE_SCOPE_INDICES_INVALID_OR_DUPLICATE')
    if any(not enabled & (1<<mapping(i)['block']) for i in indices):
        raise ValueError('DGM2_SOURCE_SCOPE_OUTSIDE_ENABLED_BLOCKS')
    return tuple(indices)


def decode_matrix_packet(data, *, target_mask=0xffff):
    """Validate the DGM2 wire contract, not its liveness or a physical output."""
    _positive_mask(target_mask, 'DGM2_TARGET_MASK')
    if type(data) is not bytes or len(data) != 384:
        raise ValueError('DGM2_EXACT_384_BYTES_REQUIRED')
    w = struct.unpack('<96I', data)
    if w[:3] != (MAGIC, VERSION, 384) or w[6:8] != (0, 0) or w[10] != FLAGS:
        raise ValueError('DGM2_MAGIC_VERSION_CAPTURE_UNKNOWN_OR_FLAGS')
    if w[11] != crc32_packet(data):
        raise ValueError('DGM2_CRC_MISMATCH')
    if not w[3] or not (w[4] | w[5] << 32) or w[9] not in (0, 1):
        raise ValueError('DGM2_IDENTITY_OR_GEOMETRY_QUALITY')
    output_mask = _positive_mask(w[12], 'DGM2_OUTPUT_MASK')
    if output_mask & ~target_mask:
        raise ValueError('DGM2_OUTPUT_MASK_OUTSIDE_COMPILED_TARGETS')
    if not 1 <= w[13] <= 4 or w[14:18] != (0, 0, 0, 0):
        raise ValueError('DGM2_LEVEL_CAP_OR_RESERVED')
    if (w[18] != 900000 or w[19] not in (1, 2) or
            not 16 <= (w[20] & 0xffff) <= 4096 or
            not 16 <= w[20] >> 16 <= 4096 or w[21:24] != (96, 32, 256)):
        raise ValueError('DGM2_LAYOUT_OR_GEOMETRY')
    matrix, unknown = data[96:352], data[352:]
    if unknown == b'\xff' * 32 or (w[9] == 0 and any(unknown)):
        raise ValueError('DGM2_NO_OBSERVABLE_CELL_OR_VALID_WITH_UNKNOWN')
    for index, level in enumerate(matrix):
        if level > 4 or level > w[13]:
            raise ValueError('DGM2_LEVEL_OUT_OF_RANGE_OR_ABOVE_CAP')
        if level and (unknown[index // 8] & (1 << (index % 8)) or
                      not output_mask & (1 << mapping(index)['block'])):
            raise ValueError('DGM2_NONZERO_UNKNOWN_OR_DISABLED_BLOCK')
    return {'session': w[3], 'sequence': w[4] | w[5] << 32,
            'receipt_age_us': w[8], 'geometry_quality': w[9],
            'output_enabled_block_mask': output_mask, 'level_cap': w[13],
            'matrix': matrix, 'unknown': unknown, 'CRC32': w[11],
            'flags': w[10], 'FRESH': False, 'capture_time_verified': False}


@dataclass(frozen=True)
class DigitalMatrixFrame:
    packet: bytes
    original_compact: bytes
    source: dict
    native_binary_sha256: str
    source_kind: str
    receipt_monotonic_ns: int
    evaluated_monotonic_ns: int
    target_mask: int
    allowed_source_indices: tuple | None = None

    @property
    def actual_live(self):
        return self.source_kind == 'LIVE_NATIVE_PIPE'

    @property
    def session(self):
        return decode_matrix_packet(self.packet, target_mask=self.target_mask)['session']

    @property
    def sequence(self):
        return self.source['sequence']

    def as_record(self):
        d = decode_matrix_packet(self.packet, target_mask=self.target_mask)
        original = self.source['matrix']
        return {'schema': 'R6_DGM2_MATRIX_BENCH_PROJECTION_V1',
                'mode': 'DIGITAL_MATRIX_BENCH_MODE', 'source_kind': self.source_kind,
                'actual_live': self.actual_live,
                'source_acquisition_session': self.source['session'],
                'producer_session': d['session'], 'sequence': d['sequence'],
                'sdk_frame_index': self.source['sdk_frame_index'],
                'raw_sha256': self.source['raw_sha256'],
                'G6C1_packet_sha256': hashlib.sha256(self.original_compact).hexdigest(),
                'native_binary_sha256': self.native_binary_sha256,
                'original_matrix': list(original),
                'original_unknown_hex': self.source['unknown'].hex(),
                'source_geometry_quality': self.source['geometry_quality'],
                'source_sidebands': {name: dict(zip(('kind', 'flags', 'distance_mm'),
                    struct.unpack_from('<BBH', self.original_compact, offset)))
                    for name, offset in (('left', 200), ('right', 204))},
                'projection_matrix': list(d['matrix']),
                'projection_unknown_hex': d['unknown'].hex(),
                'output_enabled_block_mask': d['output_enabled_block_mask'],
                'level_cap': d['level_cap'],
                'allowed_source_indices':list(self.allowed_source_indices) if self.allowed_source_indices is not None else None,
                'disabled_nonzero_source_indices': [i for i, v in enumerate(original)
                    if v and not d['matrix'][i]],
                'capped_source_indices': [i for i, v in enumerate(original)
                    if d['matrix'][i] and d['matrix'][i] < v],
                'projection_semantics': 'ZERO_COMMANDS_INCLUDE_DISABLED_UNKNOWN_OR_OFFLINE;NOT_OBSERVED_SAFE',
                'packet_hex': self.packet.hex(), 'packet_sha256': hashlib.sha256(self.packet).hexdigest(),
                'CRC32': d['CRC32'], 'FRESH': False, 'physical_output_permitted': False,
                'capture_time_verified': False, 'capture_age_ns': None,
                'capture_status': 'UNKNOWN',
                'sdk_device_us': self.source['sdk_device_us'],
                'sdk_system_us': self.source['sdk_system_us'],
                'SDK_delivery_age_ns': self.evaluated_monotonic_ns - self.source['host_wait_end_ns'],
                'IPC_receipt_age_ns': self.evaluated_monotonic_ns - self.receipt_monotonic_ns,
                'VPLUS_procedure': 'MUST_REMAIN_DISCONNECTED;NOT_INFERRED_FROM_REGISTER_STATE'}


class DigitalMatrixProducer(DigitalBringupProducer):
    """Reuse unchanged G6C1 gates and replay state, without single-cell projection."""
    def __init__(self, *args, allowed_target_mask=0xffff, **kwargs):
        super().__init__(*args, **kwargs)
        self.allowed_target_mask = _positive_mask(allowed_target_mask, 'DGM2_TARGET_MASK')

    def prepare_owned(self, owned, *, online_mask, output_mask=None, level_cap=1,
                      now_monotonic_ns=None, control_session=None, allowed_source_indices=None):
        return self.prepare_compact(owned.frame.raw, online_mask=online_mask,
            receipt_monotonic_ns=owned.source['receipt_monotonic_ns'], output_mask=output_mask,
            level_cap=level_cap, now_monotonic_ns=now_monotonic_ns, control_session=control_session,
            allowed_source_indices=allowed_source_indices)

    def prepare_compact(self, blob, *, online_mask, receipt_monotonic_ns,
                        output_mask=None, level_cap=1, now_monotonic_ns=None,
                        control_session=None, allowed_source_indices=None):
        f = _compact(blob, self.identity)
        if control_session is not None and (type(control_session) is not int or
                not 0 < control_session <= 0xffffffff):
            raise ValueError('DGM2_CONTROL_SESSION_POSITIVE_U32_REQUIRED')
        session = f['session'] if control_session is None else control_session
        if self.last_control_session is not None and session < self.last_control_session:
            raise ValueError('DGM2_CONTROL_SESSION_DECREASE_OR_WRAP')
        now = self.clock_ns() if now_monotonic_ns is None else now_monotonic_ns
        if (type(now) is not int or type(receipt_monotonic_ns) is not int or
                receipt_monotonic_ns <= 0 or now < receipt_monotonic_ns or
                f['encoding_begin_ns'] > receipt_monotonic_ns):
            raise ValueError('DGM2_HOST_MONOTONIC_SOURCE_ORDER')
        if (self.last_session is not None and f['session'] != self.last_session or
                f['sequence'] <= self.last_sequence or f['sdk_frame_index'] <= self.last_sdk or
                receipt_monotonic_ns <= self.last_receipt):
            raise ValueError('DGM2_SOURCE_SESSION_OR_SEQUENCE_REPLAY')
        if type(online_mask) is not int or not 0 <= online_mask <= 0xffff:
            raise ValueError('DGM2_ONLINE_MASK_REQUIRED')
        enabled = online_mask & self.allowed_target_mask if output_mask is None else output_mask
        _positive_mask(enabled, 'DGM2_OUTPUT_MASK')
        if enabled & ~(online_mask & self.allowed_target_mask):
            raise ValueError('DGM2_OUTPUT_MASK_OFFLINE_OR_OUTSIDE_COMPILED_TARGETS')
        scope=_source_scope(allowed_source_indices,enabled)
        scoped=set(scope) if scope is not None else None
        if type(level_cap) is not int or not 1 <= level_cap <= 4:
            raise ValueError('DGM2_LEVEL_CAP_1_TO_4_REQUIRED')
        if f['geometry_quality'] not in (0, 1) or f['unknown'] == b'\xff' * 32:
            raise ControlRejected(('DGM2_NO_OBSERVABLE_GEOMETRY',),
                                  session=f['session'], sequence=f['sequence'])
        projection = bytes(min(v, level_cap) if v and
            not f['unknown'][i // 8] & (1 << (i % 8)) and
            enabled & (1 << mapping(i)['block']) and (scoped is None or i in scoped) else 0 for i, v in enumerate(f['matrix']))
        words = [MAGIC, VERSION, 384, session, f['sequence'] & 0xffffffff,
                 f['sequence'] >> 32, 0, 0,
                 min(0xffffffff, (now - receipt_monotonic_ns + 999) // 1000),
                 f['geometry_quality'], FLAGS, 0, enabled, level_cap, 0, 0, 0, 0,
                 900000, 1, 640 | (400 << 16), 96, 32, 256]
        packet = struct.pack('<24I', *words) + projection + f['unknown']
        packet = packet[:44] + struct.pack('<I', crc32_packet(packet)) + packet[48:]
        decode_matrix_packet(packet, target_mask=self.allowed_target_mask)
        frame = DigitalMatrixFrame(packet, blob, f, self.native_sha, self.source_kind,
                                   receipt_monotonic_ns, now, self.allowed_target_mask, scope)
        self.last_session = f['session']; self.last_sequence = f['sequence']
        self.last_sdk = f['sdk_frame_index']; self.last_receipt = receipt_monotonic_ns
        self.last_control_session = session
        if self.emit is not None:
            self.emit(frame.as_record())
        return frame
