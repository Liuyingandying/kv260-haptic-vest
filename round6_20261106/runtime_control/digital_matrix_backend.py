"""Opt-in R6M2 backend, reusing D1 atomic submit and actual buffer readback.

Construction/import never accesses hardware. An old D1 profile is rejected
before opening UIO. Digital bench only; no physical power permission.
"""
from .digital_backend import DigitalTransaction, LinuxDigitalBackend
from .digital_matrix import DigitalMatrixFrame, decode_matrix_packet, DESIGN_ID
from .backend import ControlBackendError


class DigitalMatrixTransaction(DigitalTransaction):
    FRAME_CLASS = DigitalMatrixFrame
    MODE = 'DIGITAL_MATRIX_BENCH_MODE'

    def __init__(self, transport, *, target_mask=0xffff, **kwargs):
        if type(target_mask) is not int or not 1 <= target_mask <= 0xffff:
            raise ValueError('DGM2_TARGET_MASK_REQUIRED')
        self.target_mask = target_mask
        super().__init__(transport, **kwargs)

    def decode_packet(self, data):
        return decode_matrix_packet(data, target_mask=self.target_mask)

    def sdk_index(self, frame):
        return frame.source['sdk_frame_index']

    def require_online(self, decoded, diagnostics):
        if decoded['output_enabled_block_mask'] & ~diagnostics['online_mask']:
            raise ControlBackendError('DGM2_ENABLED_BLOCK_NOT_CURRENTLY_ONLINE', details=diagnostics)

    def frame_trace(self, frame):
        decoded = self.decode_packet(frame.packet)
        return {'matrix_source_trace': {
            'source_acquisition_session': frame.source['session'],
            'sdk_frame_index': frame.source['sdk_frame_index'],
            'raw_sha256': frame.source['raw_sha256'],
            'output_enabled_block_mask': decoded['output_enabled_block_mask'],
            'level_cap': decoded['level_cap'],
            'nonzero_control_cells': sum(bool(v) for v in decoded['matrix']),
            'unknown_cells': sum(v.bit_count() for v in decoded['unknown']),
        }}


class LinuxDigitalMatrixBackend(LinuxDigitalBackend):
    FRAME_CLASS = DigitalMatrixFrame
    TRANSACTION_CLASS = DigitalMatrixTransaction
    DESIGN_ID = DESIGN_ID

    def __init__(self, profile, *, target_mask=0xffff, **kwargs):
        if profile.expected_design_id != DESIGN_ID:
            raise ValueError('DGM2_EXPLICIT_PROFILE_ID_0x52364D32_REQUIRED_BEFORE_ANY_ACCESS')
        if type(target_mask) is not int or not 1 <= target_mask <= 0xffff:
            raise ValueError('DGM2_TARGET_MASK_REQUIRED')
        self.target_mask = target_mask
        super().__init__(profile, **kwargs)

    def decode_packet(self, data):
        return decode_matrix_packet(data, target_mask=self.target_mask)

    def make_transaction(self, transport):
        return DigitalMatrixTransaction(transport, target_mask=self.target_mask,
            emit=self.emit, clock_ns=self.clock_ns, timeout_ns=self.timeout_ns,
            max_polls=self.max_polls)

    def submit(self, frame):
        if not isinstance(frame, DigitalMatrixFrame) or frame.target_mask != self.target_mask:
            raise ControlBackendError('DGM2_MATCHING_COMPILED_TARGET_PRODUCER_REQUIRED')
        return super().submit(frame)
