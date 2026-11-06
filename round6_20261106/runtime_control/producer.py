"""Fail-closed compact-observation -> formal control decision.

The present G6C1 source has no reviewed exposure-to-CLOCK_MONOTONIC mapping.
A caller-supplied bool, SDK timestamp, IPC receipt or TEST label cannot mint a
capture proof. Consequently this version deliberately cannot produce FRESH
KPF1 bytes. Matrix/UNKNOWN observations remain intact for OBS2/storage users.
"""
from dataclasses import dataclass
import hashlib
import time

MAX_AGE_NS = 250_000_000


class ControlRejected(RuntimeError):
    def __init__(self, reasons, *, session=None, sequence=None):
        self.reasons = tuple(reasons)
        self.session, self.sequence = session, sequence
        super().__init__('CONTROL_REJECTED:' + ','.join(self.reasons))


@dataclass(frozen=True)
class BoardHealth:
    """A digital status snapshot supplied by the owning runtime, not permission."""
    online_mask: int
    init_healthy: bool
    runtime_run: bool
    runtime_permit: bool
    fault_latched: bool
    sampled_monotonic_ns: int

    def __post_init__(self):
        if type(self.online_mask) is not int or not 0 <= self.online_mask <= 0xffff:
            raise ValueError('EXACT_16BIT_ONLINE_MASK_REQUIRED')
        for name in ('init_healthy', 'runtime_run', 'runtime_permit', 'fault_latched'):
            if type(getattr(self, name)) is not bool:
                raise ValueError('EXPLICIT_BOOLEAN_HEALTH_FIELDS_REQUIRED')
        if type(self.sampled_monotonic_ns) is not int or self.sampled_monotonic_ns <= 0:
            raise ValueError('HEALTH_MONOTONIC_TIMESTAMP_REQUIRED')

    def reasons(self, now_ns):
        result = []
        if self.online_mask != 0xffff:
            result.append('ALL16_PCA_HEALTH_REQUIRED')
        if not self.init_healthy or not self.runtime_run or self.fault_latched:
            result.append('LOCAL_RUNTIME_NOT_HEALTHY')
        if not self.runtime_permit:
            result.append('RUNTIME_CONTROL_PERMIT_REQUIRED')
        if now_ns < self.sampled_monotonic_ns or now_ns-self.sampled_monotonic_ns >= MAX_AGE_NS:
            result.append('HEALTH_SNAPSHOT_STALE_OR_FUTURE')
        return result


@dataclass(frozen=True)
class ControlDecision:
    session: int
    sequence: int
    sdk_frame_index: int | None
    matrix: bytes
    unknown: bytes
    geometry_state: str
    reasons: tuple[str, ...]
    evaluated_monotonic_ns: int
    source_kind: str

    @property
    def control_publishable(self):
        return False

    @property
    def capture_time_verified(self):
        return False

    @property
    def wire(self):
        return None

    @property
    def physical_output_permitted(self):
        return False

    def require_wire(self):
        raise ControlRejected(self.reasons or ('CAPTURE_CLOCK_PROOF_IMPLEMENTATION_MISSING',),
                              session=self.session, sequence=self.sequence)

    def as_record(self):
        return {
            'schema': 'R6_FORMAL_KPF1_CONTROL_DECISION_V1',
            'session': self.session, 'sequence': self.sequence,
            'sdk_frame_index': self.sdk_frame_index,
            'stage': 'FORMAL_CONTROL_DECISION', 'result': 'REJECTED',
            'source_kind': self.source_kind, 'geometry_state': self.geometry_state,
            'matrix_sha256': hashlib.sha256(self.matrix).hexdigest(),
            'unknown_sha256': hashlib.sha256(self.unknown).hexdigest(),
            'unknown_cells': sum(x.bit_count() for x in self.unknown),
            'evaluated_monotonic_ns': self.evaluated_monotonic_ns,
            'reasons': list(self.reasons), 'control_publishable': False,
            'capture_time_verified': False, 'capture_monotonic_ns': None,
            'capture_age_ns': None, 'KPF1_FRESH_emitted': False,
            'formal_control_wire_bytes': 0, 'MMIO_submitted': False,
            'observed_matrix_retained': True, 'physical_output_permitted': False,
            'scope': 'FORMAL_CONTROL_DECISION_NOT_STORAGE_OR_PHYSICAL_OUTPUT',
        }


class ControlProducer:
    def __init__(self, *, clock_ns=time.monotonic_ns, emit=None):
        self.clock_ns, self.emit = clock_ns, emit

    def evaluate_owned(self, owned, *, health=None, now_monotonic_ns=None):
        # Uses the public immutable/copy interfaces of CompactOwnedObservation.
        # It neither consumes the owner nor changes its OBS2/storage lifecycle.
        return self.evaluate(owned.observation, owned.source, health=health,
                             now_monotonic_ns=now_monotonic_ns)

    def evaluate(self, observation, source, *, health=None, now_monotonic_ns=None):
        if not isinstance(observation, dict) or not isinstance(source, dict):
            raise ValueError('OBSERVATION_AND_SOURCE_OBJECTS_REQUIRED')
        now = self.clock_ns() if now_monotonic_ns is None else now_monotonic_ns
        if type(now) is not int or now <= 0:
            raise ValueError('HOST_MONOTONIC_NOW_REQUIRED')
        matrix, mask = observation.get('matrix_flat'), observation.get('unknown_mask_bytes')
        if (not isinstance(matrix, (list, bytes)) or len(matrix) != 256 or
                any(type(x) is not int or not 0 <= x <= 4 for x in matrix) or
                not isinstance(mask, (list, bytes)) or len(mask) != 32 or
                any(type(x) is not int or not 0 <= x <= 255 for x in mask)):
            raise ValueError('EXACT_MATRIX_AND_UNKNOWN_BYTES_REQUIRED')
        session, seq = observation.get('session'), observation.get('sequence')
        if (type(session) is not int or not 0 < session <= 0xffffffff or
                type(seq) is not int or not 0 < seq <= 0xffffffffffffffff):
            raise ValueError('VALID_SESSION_AND_SEQUENCE_REQUIRED')
        if observation.get('unknown_cells') != sum(x.bit_count() for x in mask):
            raise ValueError('UNKNOWN_COUNT_MISMATCH')
        reasons = []
        if source.get('source_kind') != 'LIVE_NATIVE_PIPE' or source.get('actual_live') is not True:
            reasons.append('NOT_CURRENT_LIVE_NATIVE_SOURCE')
        if source.get('session') != session or source.get('sequence') != seq:
            reasons.append('SOURCE_OBSERVATION_IDENTITY_MISMATCH')
        if (source.get('capture_time_verified') is not False or
                source.get('capture_monotonic_ns') is not None or
                source.get('capture_age_ns') is not None):
            reasons.append('UNSUPPORTED_CAPTURE_PROOF_CLAIM')
        # This reason is unconditional until a separately reviewed clock-proof
        # implementation replaces the versioned producer. Never infer capture
        # from SDK delivery/receipt, and never accept TEST/force_fresh overrides.
        reasons.append('CAPTURE_CLOCK_PROOF_IMPLEMENTATION_MISSING')
        state = observation.get('geometry_state')
        if state != 'COMPLETE':
            reasons.append('GEOMETRY_NOT_COMPLETE')
        if any(mask):
            reasons.append('UNKNOWN_REGIONS_PRESENT')
        receipt = source.get('receipt_monotonic_ns')
        if type(receipt) is not int or receipt <= 0 or not 0 <= now-receipt < MAX_AGE_NS:
            reasons.append('IPC_RECEIPT_STALE_FUTURE_OR_MISSING')
        if health is None:
            reasons.append('BOARD_HEALTH_SNAPSHOT_REQUIRED')
        elif not isinstance(health, BoardHealth):
            raise ValueError('TYPED_BOARD_HEALTH_SNAPSHOT_REQUIRED')
        else:
            reasons.extend(health.reasons(now))
        decision = ControlDecision(session, seq, source.get('sdk_frame_index'),
                                   bytes(matrix), bytes(mask), str(state),
                                   tuple(reasons), now, str(source.get('source_kind')))
        if self.emit is not None:
            self.emit(decision.as_record())
        return decision
