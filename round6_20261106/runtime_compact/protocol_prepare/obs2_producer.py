"""Retain strict R4 observations, publish new OBS2 without claiming capture age.

The caller injects the already reviewed R4 GeminiProducer. Its KPF1 sentinel is
never reclassified as safe or edited. OBS2 is a different observation-only wire
type with CRC and an explicit host-receipt timestamp reference.
"""
from dataclasses import dataclass
import copy
from value_clone import clone
import hashlib
import math
import time

from .obs2_codec import (ObservationPacket, SideObservation, GeometryQuality,
                        RiskKind, MAX_RECEIPT_AGE_US, decode)


def side_from_region(region):
    """Map preserved true regional support; no fabricated component counts."""
    z = region['distance_m']
    supported = (type(z) in (int, float) and math.isfinite(z) and .1 <= z <= 6.0 and
                 region['component_support'] >= 4 and region['region_support'] > 0)
    complete = region['coverage'] >= .9 and region['coherent_coverage'] >= .9
    unknown = region['critical_unknown'] or not complete or not supported or region['quality'] != 'VALID'
    if not supported:
        return SideObservation(RiskKind.UNKNOWN, 0xFFFFFFFF, 6 if complete else 2)
    flags = 3 if unknown else 5
    kind = (RiskKind.SUPPORTED_FAR_UNCERTAIN if z >= 2.5 and unknown else
            RiskKind.NO_NEAR if z >= 2.5 else RiskKind.DANGER if z < 0.8 else
            RiskKind.CAUTION if z < 1.5 else RiskKind.WITHIN_ATTENTION)
    return SideObservation(kind, max(1, int(math.floor(z * 1000 + 0.5))), flags).validate()


@dataclass(frozen=True)
class ProducedObservation:
    packet: bytes
    observation: dict
    source: dict
    receipt_age_ns: int
    envelope: dict
    reason: str = 'OBSERVATION_ONLY_CAPTURE_TIME_UNVERIFIED'
    physical_output_permitted: bool = False


class ObservationProducer:
    def __init__(self, r4_producer, *, clock_ns=time.monotonic_ns):
        if not callable(getattr(r4_producer, 'produce', None)):
            raise TypeError('REVIEWED_R4_PRODUCER_REQUIRED')
        if getattr(r4_producer, 'source_kind', None) != 'LIVE_NATIVE_PIPE':
            raise ValueError('OBS2_PRODUCTION_REQUIRES_LIVE_NATIVE_PIPE_NOT_REPLAY')
        self.r4_producer = r4_producer
        self.clock_ns = clock_ns

    def produce(self, native, *, receipt_monotonic_ns, now_monotonic_ns=None):
        begin = self.clock_ns() if now_monotonic_ns is None else now_monotonic_ns
        previous = self.r4_producer.produce(native,
            receipt_monotonic_ns=receipt_monotonic_ns, now_monotonic_ns=begin)
        ended = self.clock_ns()
        if (type(ended) is not int or type(receipt_monotonic_ns) is not int or
                ended < begin or begin < receipt_monotonic_ns):
            raise ValueError('OBS2_HOST_MONOTONIC_ORDER')
        age_ns = ended - receipt_monotonic_ns
        age_us = (age_ns + 999) // 1000
        if age_us >= MAX_RECEIPT_AGE_US:
            raise ValueError('OBS2_RECEIPT_EXPIRED_DURING_PRODUCER')
        if previous.physical_output_permitted is not False:
            raise ValueError('OBS2_PHYSICAL_OUTPUT_MUST_STAY_FORBIDDEN')
        if (previous.source.get('source_kind') != 'LIVE_NATIVE_PIPE' or
                previous.source.get('actual_live') is not True):
            raise ValueError('OBS2_REVIEWED_SOURCE_IS_NOT_DECLARED_LIVE_NATIVE')
        obs = previous.observation
        geometry = {'COMPLETE': GeometryQuality.COMPLETE,
                    'PARTIAL': GeometryQuality.PARTIAL,
                    'UNKNOWN': GeometryQuality.UNKNOWN,
                    'UNUSABLE': GeometryQuality.UNUSABLE}[obs['geometry_state']]
        matrix, mask = bytes(obs['matrix_flat']), bytes(obs['unknown_mask_bytes'])
        packet = ObservationPacket(obs['session'], obs['sequence'], receipt_monotonic_ns,
            age_us, geometry, matrix, mask,
            side_from_region(obs['periphery']['left']), side_from_region(obs['periphery']['right']),
            1, obs['dimensions'][0], obs['dimensions'][1])
        wire = packet.encode()
        check = decode(wire)
        if check.matrix != matrix or check.unknown != mask:
            raise RuntimeError('OBS2_SOURCE_OBSERVATION_LOST')
        source = clone(previous.source)
        source.update(timestamp_reference='HOST_MONOTONIC_IPC_RECEIPT_NOT_CAPTURE',
                      receipt_monotonic_ns=receipt_monotonic_ns,
                      encoded_monotonic_ns=ended, receipt_age_ns=age_ns,
                      capture_monotonic_ns=None, capture_age_ns=None,
                      capture_time_verified=False,
                      v1_control_status=previous.reason)
        envelope = {'schema':'GEMINI_OBS2_PRODUCER_V2', 'source':source,
            'observation':clone(obs),
            'wire':{'hex':wire.hex(), 'bytes':len(wire),
                    'sha256':hashlib.sha256(wire).hexdigest(),
                    'matrix_sha256':check.matrix_sha256,
                    'carries_original_observation':True,
                    'geometry_quality':int(check.geometry_quality),
                    'time_source':'HOST_MONOTONIC_IPC_RECEIPT',
                    'capture_time_unverified':True,
                    'observation_only':True,
                    'control_candidate':False},
            'force_off':1, 'control_valid':0, 'physical_output_permitted':False,
            'hardware_access':False, 'publication_performed':False}
        # previous is local; transfer its independently owned observation. The
        # envelope already owns a separate clone, preserving public isolation.
        return ProducedObservation(wire, obs, source, age_ns, envelope)
