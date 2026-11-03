"""Conservative stdlib projection of frozen C++ results, not full V1.1 annotation.

The original core owns connected surfaces, P10, quantization and all candidates.
This module adds the unchanged 90%/support/final-age UNKNOWN contract. The five
per-region pixel categories cannot be reconstructed from C++ JSON and stay absent.
"""
import hashlib, math

CONFIG = dict(measurement_min_m=.1, measurement_max_m=6., quantize_min_m=.3,
              attention_max_m=2.5, danger_m=.8, caution_m=1.5, connect_abs_m=.08,
              connect_rel=.12, surface_min_pixels=4, percentile=10,
              complete_ratio=.9, stale_ms=250., future_tolerance_ms=40.)

def require(ok, why):
    if not ok:
        raise ValueError(why)

def validate_config(config):
    require(config == CONFIG, 'FROZEN_CONFIGURATION_MISMATCH')
    return config

def empty_cell():
    return dict(quality='UNKNOWN', distance_m=None, uv=None, component_support=0,
                region_support=0, observed_ratio=0., outside_attention=False)

def empty_risk(reason):
    result = empty_cell(); result.pop('outside_attention')
    result.update(level='UNKNOWN', reason=reason)
    return result

def final_age_gate(baseline, now_ns, source_fault=None):
    require(type(now_ns) is int and now_ns >= 0, 'INVALID_FINAL_CLOCK')
    require(type(baseline.get('capture_ns')) is int and baseline['capture_ns'] >= 0, 'INVALID_CAPTURE_CLOCK')
    # The caller owns one private decoded core tree per frame. Candidate arrays
    # are shared read-only; only metadata touched by this gate is copied.
    result = dict(baseline)
    result['frame_state'] = dict(baseline['frame_state'])
    if result['frame_state']['reason'] == 'STREAM_GAP':
        return result
    age = (now_ns-result['capture_ns'])/1e6
    result['processing_ns'] = now_ns
    result['frame_state']['age_ms'] = age
    reason = source_fault
    if reason is None and result['frame_state']['fresh']:
        reason = 'STALE_FRAME' if age > 250. else 'FUTURE_FRAME' if age < -40. else None
    if reason:
        result.update(matrix=[0]*256, cells=[empty_cell() for _ in range(256)],
                      central=empty_risk(reason), left=empty_risk(reason), right=empty_risk(reason),
                      summary=dict(result['summary'], quality='UNKNOWN'),
                      frame_state=dict(fresh=False, reason=reason, age_ms=age))
    return result

def region(candidate, fresh, rect):
    x0, y0, x1, y1 = rect; area = (x1-x0)*(y1-y0)
    ratio = candidate.get('observed_ratio')
    require(isinstance(ratio, (int, float)) and math.isfinite(ratio) and 0 <= ratio <= 1, 'INVALID_CORE_COVERAGE')
    distance = candidate.get('distance_m')
    supported = bool(fresh and distance is not None and math.isfinite(distance) and
                     .1 <= distance <= 6. and candidate.get('component_support', 0) >= 4
                     and candidate.get('region_support', 0) > 0)
    complete = bool(fresh and area > 0 and ratio >= .9)
    near = supported and distance < 2.5
    unknown = not complete or not supported
    reasons = []
    if not fresh: reasons.append('FRAME_UNAVAILABLE')
    if not area: reasons.append('NO_REGION')
    elif fresh and ratio < .9: reasons.append('INCOMPLETE_MEASUREMENT_COVERAGE')
    elif not fresh: reasons.append('MEASUREMENT_COVERAGE_NOT_AVAILABLE_FOR_NONFRESH_CORE_JSON')
    if not supported: reasons.append('NO_USABLE_SUPPORTED_SURFACE')
    return dict(rect_xyxy=list(rect), pixel_count=area, observed_ratio=ratio,
                measurement_coverage_available=fresh,
                measurement_categories='NOT_AVAILABLE_FROM_FROZEN_CPP_JSON',
                coverage_state='COMPLETE' if complete else 'INCOMPLETE',
                evidence_state='SUPPORTED_WITHIN_ATTENTION' if near else 'SUPPORTED_OUTSIDE_ATTENTION' if supported else 'UNKNOWN',
                supported_candidate=supported, supported_obstacle_candidate=near, unknown_region=unknown,
                unknown_reasons=reasons, observed_no_near_evidence=bool(supported and complete and not near),
                distance_m=distance if supported else None, uv=candidate.get('uv') if supported else None,
                component_support=candidate.get('component_support', 0) if supported else 0,
                region_support=candidate.get('region_support', 0) if supported else 0,
                selected_component_region_fraction=candidate.get('region_support', 0)/area if supported else 0.,
                safety_clear=False)

def annotate_cpp(baseline, now_ns=None, source_fault=None, config=CONFIG):
    validate_config(config)
    require(baseline.get('schema') == 'basic_depth_v1', 'WRONG_CORE_SCHEMA')
    old = baseline if now_ns is None else final_age_gate(baseline, now_ns, source_fault)
    require(old.get('coordinate_space') in ('native_depth', 'rgb_aligned_saved'), 'INVALID_COORDINATE_SPACE')
    w, h = old['width'], old['height']
    require(type(w) is int and type(h) is int and 16 <= w <= 4096 and 16 <= h <= 4096, 'INVALID_DIMENSIONS')
    matrix, cells = old.get('matrix'), old.get('cells')
    require(isinstance(matrix, list) and len(matrix) == 256 and
            all(type(v) is int and 0 <= v <= 4 for v in matrix) and
            isinstance(cells, list) and len(cells) == 256, 'INVALID_MATRIX_OR_CELLS')
    fresh = old['frame_state'].get('fresh') is True and old['frame_state'].get('reason') == 'OK'
    age = (old['processing_ns']-old['capture_ns'])/1e6
    if fresh:
        require(-40. <= age <= 250. and math.isclose(old['frame_state']['age_ms'], age, abs_tol=1e-9), 'INCONSISTENT_FRESHNESS')
    s = min(w, h); ox, oy = w//2-s//2, h//2-s//2
    require(old['roi'] == [ox, oy, ox+s, oy+s], 'ROI_MISMATCH')
    regions = []
    for index, candidate in enumerate(cells):
        row, col = divmod(index, 16)
        rect = (ox+col*s//16, oy+row*s//16, ox+(col+1)*s//16, oy+(row+1)*s//16)
        item = region(candidate, fresh, rect)
        item.update(index=index, row=row, col=col, legacy_quality=candidate['quality'], intensity=matrix[index])
        regions.append(item)
    sides = {}
    for key, rect in (('left', (0, 0, ox, h)), ('right', (ox+s, 0, w, h))):
        sides[key] = region(old[key], fresh, rect)
        sides[key]['legacy_risk_level'] = old[key]['level']
    supported = sum(r['supported_candidate'] for r in regions)
    near = sum(r['supported_obstacle_candidate'] for r in regions)
    unknown = sum(r['unknown_region'] for r in regions)
    unknown_sides = [key for key, r in sides.items() if r['unknown_region']]
    usable = fresh and (supported > 0 or any(r['supported_candidate'] for r in sides.values()))
    complete = usable and unknown == 0 and not unknown_sides
    state = 'UNUSABLE' if not usable else 'COMPLETE_OBSERVATIONS' if complete else 'PARTIAL_WITH_OBSERVATIONS'
    cause = (old['frame_state']['reason'] if not fresh else 'NO_ACCEPTED_MEASUREMENTS' if not old['summary']['observed_pixels']
             else 'NO_SUPPORTED_SURFACE' if not usable else 'REGIONAL_UNKNOWN' if not complete else 'OK')
    peripheral_near = [key for key, r in sides.items() if r['supported_obstacle_candidate']]
    inhibit = []
    if not usable: inhibit.append('FRAME_UNUSABLE')
    if unknown or unknown_sides: inhibit.append('CRITICAL_UNKNOWN_WITHOUT_INDEPENDENT_WARNING')
    if peripheral_near: inhibit.append('PERIPHERAL_RISK_NOT_ENCODED_IN_256_BYTES')
    return dict(schema='basic_depth_cpp_quality_bridge_v1',
                full_v11_annotation_equivalence=False, missing_detail='FIVE_DISJOINT_PIXEL_CATEGORIES_PER_REGION',
                legacy_result=old, legacy_diagnostic=baseline,
                legacy_matrix_sha256=hashlib.sha256(bytes(old['matrix'])).hexdigest(),
                quality_config=dict(strategy='regional_quality_v11', coverage_complete_ratio=.9,
                                    critical_scope='ALL_256_CELLS_AND_LEFT_RIGHT', control_mode='OFFLINE_INHIBITED'),
                perception=dict(state=state, reason=cause, preview_export_accepted=usable,
                    matrix_preview_accepted=usable and supported > 0, matrix_has_supported_obstacle_candidates=near > 0,
                    matrix_supported_candidate_cells=supported, matrix_supported_obstacle_cells=near,
                    matrix_unknown_cells=unknown, unknown_sides=unknown_sides, cells=regions, sides=sides,
                    unknown_mask=[r['unknown_region'] for r in regions],
                    supported_candidate_mask=[r['supported_candidate'] for r in regions],
                    observed_no_near_mask=[r['observed_no_near_evidence'] for r in regions]),
                control=dict(quality_contract_eligible=bool(usable and not unknown and not unknown_sides and not peripheral_near),
                    quality_inhibit_reasons=inhibit, physical_output_permitted=False, mode='OFFLINE_INHIBITED',
                    independent_unknown_warning='NOT_IMPLEMENTED', metadata_received_by_original_PL=False,
                    hardware_output_safety='NOT_VERIFIED', hardware_backend='NONE', pending_writes=0, commit_writes=0))

def unknown_bitmap(mask):
    require(len(mask) == 256 and all(type(v) is bool for v in mask), 'INVALID_UNKNOWN_MASK')
    return bytes(sum(int(mask[byte*8+bit]) << bit for bit in range(8)) for byte in range(32))

def form_kpf1_projection(envelope, codec, now_ns, event_sequence):
    """Keep the validated Packet; avoid a redundant target encode/decode cycle."""
    require(envelope['schema'] == 'basic_depth_cpp_quality_bridge_v1', 'WRONG_BRIDGE_SCHEMA')
    codec_view = dict(schema='basic_depth_regional_quality_v11',
                      quality_config=envelope['quality_config'], legacy_result=envelope['legacy_result'],
                      perception=envelope['perception'])
    packet = codec.from_envelope(codec_view, now_ns=now_ns, resume=False, sequence=event_sequence)
    data = packet.encode()
    require(len(data) == 384, 'KPF1_SIZE_MISMATCH')
    return packet, data

def encode_kpf1_projection(envelope, codec, now_ns, event_sequence):
    return form_kpf1_projection(envelope, codec, now_ns, event_sequence)[1]
