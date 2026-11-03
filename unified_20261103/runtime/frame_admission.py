"""Host-only latest-frame admission contract for a future unified live loop.

No camera, UIO, SDK, serial, or FPGA access is implemented here. A future caller
must establish CLOCK_MONOTONIC capture/now evidence and obtain *whole pipeline*
quiescence (adapter + scheduler + dirty/burst/init + IIC), then execute/check an
action. Merely observing the action is not a hardware submission or safety PASS.

Do not wire this into frozen run_live_pl.py: its native helper deliberately
requires IIC disabled and is incompatible with the new tactile architecture.
"""
from dataclasses import dataclass, replace

import kpf1_codec as codec

MAX_AGE_NS = 250_000_000
FUTURE_NS = 40_000_000


@dataclass(frozen=True)
class AdmissionAction:
    kind: str
    reason: str
    packet: object = None
    source_packet: object = None
    inhibit_required: bool = False
    admission_allowed: bool = False
    physical_output_permitted: bool = False
    evidence_scope: str = 'HOST_MODEL_ONLY_NOT_CONNECTED_TO_LIVE'


class FrameAdmission:
    """At most one pending COMPLETE frame; never invents identity or capture time.

    SUBMIT_COMPLETE means the future transport may submit this checked frame;
    it is NOT physical output authorization. SUBMIT_OBSERVATION_INHIBIT is
    urgent, regardless of busy, and must revoke the old control path. REJECT
    actions with inhibit_required also require fail-closed handling; an invalid
    or replayed identity must not be fabricated into a valid new KPF1 event.
    """
    def __init__(self):
        self._pending = None
        self._session = self._sequence = self._capture_watermark = 0
        self._retired_floor = 0
        self._last_now_ns = None
        self.resume_required = True
        self.new_session_required = False
        self.offered = self.overwritten = self.submitted = self.inhibited = 0

    @property
    def pending_count(self):
        return int(self._pending is not None)

    def snapshot(self):
        return dict(pending_count=self.pending_count, offered=self.offered,
                    overwritten=self.overwritten, submitted=self.submitted,
                    inhibited=self.inhibited, last_seen_session=self._session,
                    last_seen_sequence=self._sequence,
                    capture_watermark_ns=self._capture_watermark,
                    resume_required=self.resume_required,
                    new_session_required=self.new_session_required,
                    retired_session_floor=self._retired_floor,
                    physical_output_permitted=False,
                    evidence_scope='HOST_MODEL_ONLY_NOT_CONNECTED_TO_LIVE')

    def _reject(self, reason, packet=None):
        self._pending = None
        self.resume_required = True
        self.inhibited += 1
        return AdmissionAction('REJECT', reason, source_packet=packet,
                               inhibit_required=True)

    def _identity(self, packet):
        if packet.session < self._session:
            return 'RETIRED_OR_OLD_SESSION'
        if self.new_session_required and packet.session <= self._retired_floor:
            return 'RECOVERY_REQUIRES_NEW_SESSION'
        if packet.session == self._session and packet.sequence <= self._sequence:
            return 'DUPLICATE_OR_OUT_OF_ORDER_SEQUENCE'
        fresh = bool(packet.flags & codec.FRESH)
        if (fresh and packet.capture_ns <= self._capture_watermark) or (
                not fresh and packet.capture_ns < self._capture_watermark):
            return 'CAPTURE_WATERMARK_REPLAY'
        self._session, self._sequence = packet.session, packet.sequence
        self._capture_watermark = max(self._capture_watermark, packet.capture_ns)
        return None

    def _clock(self, packet, now_ns, verified):
        if verified is not True:
            return 'CLOCK_MAPPING_NOT_VERIFIED', None
        if type(now_ns) is not int or now_ns < 0:
            return 'INVALID_MONOTONIC_NOW', None
        if self._last_now_ns is not None and now_ns < self._last_now_ns:
            return 'HOST_MONOTONIC_CLOCK_REGRESSION', None
        self._last_now_ns = now_ns
        if packet.capture_ns == 0:
            return 'MISSING_CAPTURE_TIMESTAMP', None
        delta = now_ns - packet.capture_ns
        if delta >= MAX_AGE_NS:
            return 'STALE_AT_ADMISSION', delta
        if delta < -FUTURE_NS:
            return 'FUTURE_CAPTURE_OUTSIDE_CONTRACT', delta
        return None, delta

    @staticmethod
    def _age_us(delta):
        age = ((abs(delta) + 999) // 1000) * (-1 if delta < 0 else 1)
        if not -(1 << 31) <= age < (1 << 31):
            raise ValueError('AGE_NOT_REPRESENTABLE_WITHOUT_FABRICATION')
        return age

    def _inhibit(self, packet, reason, *, delta=None, unusable=False):
        self._pending = None
        self.resume_required = True
        self.inhibited += 1
        severe = unusable or packet.quality == codec.Quality.UNUSABLE or bool(
            packet.flags & codec.GAP or packet.producer_fault or not packet.flags & codec.FRESH)
        if severe:
            self.new_session_required = True
            self._retired_floor = max(self._retired_floor, packet.session)
        try:
            age = packet.age_us if delta is None else self._age_us(delta)
            if severe:
                # Source identity/capture are immutable. With no verified clock,
                # keep the recorded age rather than claiming a mapped age.
                offered = replace(packet, age_us=age, quality=codec.Quality.UNUSABLE,
                                  flags=packet.flags & ~codec.FRESH,
                                  producer_fault=packet.producer_fault or 4,
                                  matrix=bytes(256), unknown=b'\xff' * 32,
                                  left=codec.Side(), right=codec.Side()).validate()
            else:
                # PARTIAL retains every level, UNKNOWN bit and side candidate.
                offered = replace(packet, age_us=age).validate()
        except ValueError:
            return AdmissionAction('REJECT', reason + ':NO_VALID_PACKET_REPRESENTATION',
                                   source_packet=packet, inhibit_required=True)
        return AdmissionAction('SUBMIT_OBSERVATION_INHIBIT', reason, offered, packet,
                               inhibit_required=True, admission_allowed=True)

    def _complete_action(self, packet, delta):
        if self.resume_required and not packet.flags & codec.RESUME:
            return self._reject('EXPLICIT_RESUME_FLAG_REQUIRED', packet)
        if self.new_session_required and packet.session <= self._retired_floor:
            return self._reject('RECOVERY_REQUIRES_NEW_SESSION', packet)
        offered = replace(packet, age_us=self._age_us(delta)).validate()
        self._pending = None
        self.resume_required = self.new_session_required = False
        self.submitted += 1
        return AdmissionAction('SUBMIT_COMPLETE', 'QUIESCENT_FRESH_COMPLETE', offered,
                               packet, admission_allowed=True)

    def offer(self, packet, *, now_ns, clock_verified=False,
              pipeline_quiescent=False, source_fault=None):
        self.offered += 1
        try:
            if not isinstance(packet, codec.Packet):
                raise ValueError('not KPF1 Packet')
            packet.validate()
        except (ValueError, TypeError, AttributeError) as exc:
            return self._reject('INVALID_KPF1_SCHEMA:' + str(exc))
        reason = self._identity(packet)
        if reason:
            return self._reject(reason, packet)
        clock_reason, delta = self._clock(packet, now_ns, clock_verified)
        if source_fault is not None:
            return self._inhibit(packet, 'SOURCE_FAULT:' + str(source_fault),
                                 delta=delta, unusable=True)
        if clock_reason:
            return self._inhibit(packet, clock_reason, delta=delta, unusable=True)
        if not packet.control_candidate:
            return self._inhibit(packet, 'QUALITY_OR_SIDEBAND_REQUIRES_INHIBIT', delta=delta)
        if self.resume_required and not packet.flags & codec.RESUME:
            return self._reject('EXPLICIT_RESUME_FLAG_REQUIRED', packet)
        if pipeline_quiescent is True:
            return self._complete_action(packet, delta)
        if self._pending is not None:
            self.overwritten += 1
        self._pending = packet
        return AdmissionAction('QUEUED_LATEST_COMPLETE', 'WHOLE_PIPELINE_BUSY',
                               source_packet=packet)

    def drain(self, *, now_ns, clock_verified=False, pipeline_quiescent=False):
        if self._pending is None:
            return AdmissionAction('NO_PENDING', 'NO_QUEUED_COMPLETE')
        packet = self._pending
        clock_reason, delta = self._clock(packet, now_ns, clock_verified)
        if clock_reason:
            return self._inhibit(packet, clock_reason, delta=delta, unusable=True)
        if pipeline_quiescent is not True:
            return AdmissionAction('QUEUED_LATEST_COMPLETE', 'WHOLE_PIPELINE_BUSY',
                                   source_packet=packet)
        return self._complete_action(packet, delta)
