"""Human-attested, single-motor bench boundary. No hardware access on import.

This does not establish production readiness, exposure freshness, power ratings,
physical shutdown or actual vibration. It retains the old DGD1 wire contract.
"""
from dataclasses import dataclass, fields
from pathlib import Path
import hashlib
import json
import math
import os
import re
import struct
from runtime_control.digital_bringup import DigitalFrame, decode_digital, crc32_packet, mapping, MAGIC, VERSION, FLAGS

SCHEMA = 'R6_SUPERVISED_SINGLE_MOTOR_ATTESTATION_V1'
ORIGINAL_SUPPLY_SCHEMA = 'R6_SUPERVISED_SINGLE_MOTOR_ATTESTATION_V2'
SELECTED_TARGET_SCHEMA = 'R6_SUPERVISED_SELECTED_LIVE_TARGET_ATTESTATION_V3'
ALLOWED_TARGET_MASK = 0xf0f0
ORIGINAL_SUPPLY_BASIS = 'UNCHANGED_ORIGINAL_STM32_SUPPLY_AND_USER_OBSERVED_STATIONARY'
MODE = 'SUPERVISED_SINGLE_MOTOR_BENCH'
SOURCE_INDEX = 131
TRUE_FIELDS = ('same_original_STM32_power_supply_and_settings', 'onsite_operator',
    'independent_VPLUS_cutoff_ready', 'current_display_available')
EXPECTED_FIELDS = {'schema','nonce','boot_id','output_directory','source_index','duration_s',
    *TRUE_FIELDS,'VPLUS_state','confirmation','prior_actual_vibration_observed',
    'prior_observation_evidence','unverified_branches_Vplus_isolated',
    'shared_VPLUS','static_Vplus_voltage_V','static_current_A',
    'static_all_actuators_stationary_observed','operator_baseline_acceptable'}


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def _unique(pairs):
    require(len(pairs) == len({k for k, _ in pairs}), 'OPERATOR_PROOF_DUPLICATE_FIELD')
    return dict(pairs)


@dataclass(frozen=True)
class OperatorAttestation:
    nonce: str
    boot_id: str
    output_directory: str
    duration_s: int
    sha256: str
    prior_actual_vibration_observed: bool
    prior_observation_evidence: str
    physical_path: dict
    schema: str = SCHEMA
    source_index: int = SOURCE_INDEX

    def as_record(self):
        return {'schema': self.schema, 'nonce': self.nonce, 'boot_id': self.boot_id,
            'output_directory': self.output_directory, 'duration_s': self.duration_s,
            'source_index': self.source_index, 'attestation_sha256': self.sha256,
            **{k: True for k in TRUE_FIELDS}, **self.physical_path, 'VPLUS_state': 'ON_USER_CONFIRMED',
            'confirmation': 'USER_EXPLICITLY_CONFIRMED',
            'prior_actual_vibration_observed': self.prior_actual_vibration_observed,
            'prior_observation_evidence': self.prior_observation_evidence,
            'evidence_kind': 'EXTERNAL_OPERATOR_ATTESTATION_NOT_AN_ELECTRICAL_SENSOR',
            'numeric_power_ratings': 'NOT_INVENTED;UNCHANGED_ORIGINAL_STM32_SUPPLY_ATTESTED'}


def load_attestation(path, *, out, duration_s, source_index, boot_id, consume=False):
    """Revalidate in parent and worker; only worker consumes the out-bound nonce."""
    path, out = Path(path), Path(out)
    require(path.is_absolute() and out.is_absolute(), 'OPERATOR_PROOF_ABSOLUTE_PATHS_REQUIRED')
    require(path.is_file() and not path.is_symlink(), 'OPERATOR_PROOF_REGULAR_FILE_REQUIRED')
    require(path.stat().st_size <= 16384, 'OPERATOR_PROOF_SIZE_LIMIT')
    raw = path.read_bytes()
    row = json.loads(raw.decode('utf8'), object_pairs_hook=_unique)
    require(isinstance(row, dict), 'OPERATOR_PROOF_SCHEMA')
    selected_target_path = row.get('schema') == SELECTED_TARGET_SCHEMA
    original_supply_path = row.get('schema') in (ORIGINAL_SUPPLY_SCHEMA, SELECTED_TARGET_SCHEMA)
    expected_fields = EXPECTED_FIELDS | ({'baseline_evidence_basis','numeric_measurements_status'} if original_supply_path else set())
    require(set(row) == expected_fields and row.get('schema') in (SCHEMA, ORIGINAL_SUPPLY_SCHEMA, SELECTED_TARGET_SCHEMA),
            'OPERATOR_PROOF_SCHEMA')
    require(isinstance(row['nonce'], str) and re.fullmatch(r'[a-f0-9]{32}', row['nonce']),
            'OPERATOR_PROOF_NONCE')
    require(isinstance(boot_id, str) and re.fullmatch(r'[0-9a-f-]{36}', boot_id)
            and row['boot_id'] == boot_id, 'OPERATOR_PROOF_BOOT_MISMATCH')
    require(isinstance(row['output_directory'], str) and Path(row['output_directory']).is_absolute()
            and Path(row['output_directory']).resolve() == out.resolve(), 'OPERATOR_PROOF_OUTPUT_MISMATCH')
    if selected_target_path:
        require(type(source_index) is int and 0<=source_index<256
                and type(row['source_index']) is int and row['source_index']==source_index,
                'SUPERVISED_V3_EXACT_SELECTED_SOURCE_MISMATCH')
        target=mapping(source_index)
        require(bool(ALLOWED_TARGET_MASK & (1<<target['block'])) and target['tca_channel'] in (1,3),
                'SUPERVISED_V3_TARGET_MUST_BE_ON_VERIFIED_F0F0_BLOCK')
    else:
        require(type(source_index) is int and source_index == SOURCE_INDEX
                and type(row['source_index']) is int and row['source_index'] == SOURCE_INDEX,
                'SUPERVISED_EXACT_SOURCE_131_REQUIRED')
    require(type(duration_s) is int and duration_s in (8, 60)
            and type(row['duration_s']) is int and row['duration_s'] == duration_s,
            'OPERATOR_PROOF_DURATION_MISMATCH')
    require(all(row[k] is True for k in TRUE_FIELDS), 'OPERATOR_PHYSICAL_CONDITIONS_NOT_ATTESTED')
    require(row['VPLUS_state'] == 'ON_USER_CONFIRMED'
            and row['confirmation'] == 'USER_EXPLICITLY_CONFIRMED', 'OPERATOR_VPLUS_NOT_CONFIRMED_ON')
    require(type(row['prior_actual_vibration_observed']) is bool
            and isinstance(row['prior_observation_evidence'], str), 'OPERATOR_PRIOR_OBSERVATION_SCHEMA')
    if duration_s == 60:
        require(row['prior_actual_vibration_observed'] is True
                and bool(row['prior_observation_evidence'].strip()),
                'FIRST_PHYSICAL_TRIAL_MUST_BE_EIGHT_SECONDS')
    isolated, shared = row['unverified_branches_Vplus_isolated'], row['shared_VPLUS']
    require(type(isolated) is bool and type(shared) is bool and isolated != shared,
            'OPERATOR_EXPLICIT_ISOLATED_OR_SHARED_PHYSICAL_PATH_REQUIRED')
    baseline_keys = ('static_Vplus_voltage_V','static_current_A',
        'static_all_actuators_stationary_observed','operator_baseline_acceptable')
    require(type(row['static_all_actuators_stationary_observed']) is bool and
            type(row['operator_baseline_acceptable']) is bool, 'OPERATOR_BASELINE_BOOL_FIELDS')
    if original_supply_path:
        # An explicit, narrow migration bench basis, never fabricated electrical
        # measurements or proof of automatic protection/whole-array safety.
        require(duration_s == 8 and shared and not isolated,
                'ORIGINAL_SUPPLY_ALTERNATIVE_ONLY_SHARED_EIGHT_SECONDS')
        require(row['baseline_evidence_basis'] == ORIGINAL_SUPPLY_BASIS
                and row['numeric_measurements_status'] == 'NOT_REPORTED',
                'ORIGINAL_SUPPLY_EVIDENCE_BASIS_MISMATCH')
        require(row['static_Vplus_voltage_V'] is None and row['static_current_A'] is None,
                'ORIGINAL_SUPPLY_NOT_REPORTED_NUMBERS_MUST_BE_NULL')
        require(row['static_all_actuators_stationary_observed'] is True
                and row['operator_baseline_acceptable'] is True,
                'ORIGINAL_SUPPLY_STATIONARY_BASELINE_NOT_CONFIRMED')
    elif shared:
        voltage, current = row['static_Vplus_voltage_V'], row['static_current_A']
        require(type(voltage) in (int, float) and math.isfinite(voltage) and voltage > 0
                and type(current) in (int, float) and math.isfinite(current) and current >= 0,
                'SHARED_VPLUS_REQUIRES_REAL_FINITE_STATIC_VOLTAGE_AND_CURRENT')
        require(row['static_all_actuators_stationary_observed'] is True and
                row['operator_baseline_acceptable'] is True, 'SHARED_VPLUS_STATIC_BASELINE_NOT_CONFIRMED')
    else:
        require(row['static_Vplus_voltage_V'] is None and row['static_current_A'] is None
                and row['static_all_actuators_stationary_observed'] is False
                and row['operator_baseline_acceptable'] is False,
                'ISOLATED_PATH_MUST_NOT_INVENT_UNUSED_SHARED_BASELINE')
    physical = {k: row[k] for k in ('unverified_branches_Vplus_isolated','shared_VPLUS', *baseline_keys)}
    physical.update(unverified_branches_independent_shutdown_verified=False,
                    autonomous_full_array_safety='NOT_VERIFIED',
                    baseline_evidence_kind='USER_OBSERVED_STATIC_VALUES_NOT_AUTOMATIC_SENSOR')
    if original_supply_path:
        physical.update(baseline_evidence_basis=row['baseline_evidence_basis'],
            numeric_measurements_status='NOT_REPORTED',
            baseline_evidence_kind='USER_ATTESTED_UNCHANGED_ORIGINAL_SUPPLY_AND_QUIET_BASELINE',
            bench_known_limitations={
                'electrical_ratings_and_overload_margin':'NOT_VERIFIED',
                'automatic_overcurrent_protection':'NOT_VERIFIED_NOT_ASSERTED',
                'unverified_128_outputs':'SHARED_VPLUS_NOT_ISOLATED;NO_CONTROL_COMMANDS_TO_THESE_BRANCHES',
                'physical_control':'ONSITE_OPERATOR_INDEPENDENT_MANUAL_CUTOFF_AND_CURRENT_DISPLAY_ATTESTED',
                'native_capture_budget_s':8,'hard_physical_duration_bound':'NOT_VERIFIED',
                'actual_vibration':'NOT_VERIFIED_REQUIRES_OPERATOR_OBSERVATION',
                'production_readiness':False})
    proof = OperatorAttestation(row['nonce'], boot_id, str(out.resolve()), duration_s,
        hashlib.sha256(raw).hexdigest(), row['prior_actual_vibration_observed'],
        row['prior_observation_evidence'], physical, row['schema'], source_index)
    if consume:
        require(out.is_dir() and not out.is_symlink(), 'OPERATOR_PROOF_NEW_OUTPUT_DIRECTORY_REQUIRED')
        # Output binding, parent mkdir(exist_ok=False), exclusive journal files,
        # and this exclusive nonce record prohibit repeating the same run.
        marker = out / 'OPERATOR_ATTESTATION_CONSUMED.json'
        with marker.open('x', encoding='utf8') as fp:
            json.dump({**proof.as_record(), 'worker_pid': os.getpid()}, fp, indent=2)
            fp.flush()
            os.fsync(fp.fileno())
    return proof


@dataclass(frozen=True)
class SupervisedSingleFrame(DigitalFrame):
    source_level: int = 0
    operator_attestation: OperatorAttestation | None = None

    def as_record(self):
        record = super().as_record()
        record.update(schema='R6_SUPERVISED_SINGLE_MOTOR_PROJECTION_V1', mode=MODE,
            source_level=self.source_level, commanded_level=1,
            level_cap_semantics='SELECTED_CELL_ONLY;SOURCE_CORE_MATRIX_UNCHANGED',
            operator_bench_permission=True, production_output_permitted=False,
            physical_output_permitted=False, production_ready=False,
            VPLUS_procedure='ON_USER_CONFIRMED_UNDER_EXTERNAL_HUMAN_SUPERVISION',
            operator_attestation=self.operator_attestation.as_record(),
            actual_vibration='NOT_VERIFIED',
            actual_vibration_requirement='REQUIRES_OPERATOR_OBSERVATION')
        return record


def cap_single_frame(frame, proof):
    require(isinstance(proof, OperatorAttestation), 'VALIDATED_OPERATOR_ATTESTATION_REQUIRED')
    require(isinstance(frame, DigitalFrame) and frame.actual_live, 'SUPERVISED_REAL_LIVE_FRAME_REQUIRED')
    decoded = decode_digital(frame.packet)
    index = decoded['selected']['source_index']
    require(index == proof.source_index and all(frame.selected.get(k) == v for k, v in decoded['selected'].items()),
            'SUPERVISED_SOURCE_MAP_MISMATCH')
    require(frame.original_unknown == decoded['unknown'] and len(frame.original_matrix) == 256
            and frame.original_matrix[index] == decoded['selected']['level'],
            'SUPERVISED_SOURCE_PROVENANCE_MISMATCH')
    packet = bytearray(frame.packet)
    packet[96 + index] = 1
    struct.pack_into('<I', packet, 44, 0)
    struct.pack_into('<I', packet, 44, crc32_packet(packet))
    final = decode_digital(bytes(packet))
    require(final['selected']['level'] == 1 and sum(v != 0 for v in final['matrix']) == 1,
            'SUPERVISED_EXACT_ONE_MINIMUM_NONZERO_REQUIRED')
    kwargs = {f.name: getattr(frame, f.name) for f in fields(DigitalFrame)}
    kwargs.update(packet=bytes(packet), selected={**frame.selected, 'level': 1})
    return SupervisedSingleFrame(**kwargs, source_level=frame.selected['level'], operator_attestation=proof)


def wrap_submission(result, frame):
    """Correct procedure labels only; never alter wire/generation/readback evidence."""
    require(isinstance(frame, SupervisedSingleFrame), 'SUPERVISED_CAPPED_FRAME_REQUIRED')
    require(result.get('FRESH') is False and result.get('capture_time_verified') is False
            and result.get('physical_output_permitted') is False, 'UNEXPECTED_PRODUCTION_PERMISSION')
    record = dict(result)
    record.update(schema='R6_SUPERVISED_SINGLE_MOTOR_SUBMIT_V1', mode=MODE,
        operator_bench_permission=True, production_output_permitted=False,
        production_ready=False, VPLUS_required='ON_USER_CONFIRMED_UNDER_EXTERNAL_HUMAN_SUPERVISION',
        source_level=frame.source_level, commanded_level=1,
        operator_attestation=frame.operator_attestation.as_record(),
        scope='SUPERVISED_BENCH_DIGITAL_READBACK;ACTUAL_VIBRATION_REQUIRES_OPERATOR_OBSERVATION',
        actual_vibration='NOT_VERIFIED')
    return record


@dataclass(frozen=True)
class ManualBenchFrame(DigitalFrame):
    operator_attestation: OperatorAttestation | None = None

    def as_record(self):
        d=decode_digital(self.packet)
        return {'schema':'R6_MANUAL_SINGLE_MOTOR_COMMAND_V1','mode':'MANUAL_SINGLE_MOTOR_BENCH',
            'source_kind':'MANUAL_OPERATOR_TEST','actual_live':False,
            'session':self.session,'sequence':self.sequence,'source_index':SOURCE_INDEX,
            'commanded_level':1,'command_generation_monotonic_ns':self.receipt_monotonic_ns,
            'command_evaluated_monotonic_ns':self.evaluated_monotonic_ns,
            'capture_timestamp':None,'capture_age_ns':None,'capture_time_verified':False,'FRESH':False,
            'raw_sha256':None,'G6C1_packet_sha256':None,'sdk_frame_index':None,
            'perception_matrix':None,'perception_UNKNOWN':None,
            'command_matrix':list(d['matrix']),'wire_command_disabled_mask_hex':d['unknown'].hex(),
            'mask_semantics':'MANUAL_COMMAND_DISABLED_MASK_NOT_PERCEPTION_QUALITY',
            'wire_geometry_quality':'DGD1_SCHEMA_COMPATIBILITY_ONLY_NOT_AN_OBSERVATION',
            'wire_grid_dimensions':[16,16],'projection_packet_hex':self.packet.hex(),
            'projection_packet_sha256':hashlib.sha256(self.packet).hexdigest(),
            'selected_command_trace':dict(self.selected),'operator_bench_permission':True,
            'production_output_permitted':False,'physical_output_permitted':False,'production_ready':False,
            'VPLUS_procedure':'ON_USER_CONFIRMED_UNDER_EXTERNAL_HUMAN_SUPERVISION',
            'operator_attestation':self.operator_attestation.as_record(),
            'actual_vibration':'NOT_VERIFIED_REQUIRES_OPERATOR_OBSERVATION'}


def manual_frame(proof,*,session,sequence,now_ns):
    require(isinstance(proof,OperatorAttestation) and proof.duration_s==8 and proof.source_index==SOURCE_INDEX,
            'MANUAL_EXPLICIT_EIGHT_SECOND_SOURCE131_ATTESTATION_REQUIRED')
    require(type(session) is int and 0<session<=0xffffffff and type(sequence) is int and 0<sequence<=0xffffffff,
            'MANUAL_POSITIVE_DURABLE_SESSION_AND_SEQUENCE_REQUIRED')
    require(type(now_ns) is int and now_ns>0,'MANUAL_TRUE_LOCAL_COMMAND_TIME_REQUIRED')
    target=mapping(SOURCE_INDEX);matrix=bytearray(256);matrix[SOURCE_INDEX]=1
    disabled=bytearray(b'\xff'*32);disabled[SOURCE_INDEX//8]&=~(1<<(SOURCE_INDEX%8))
    words=[MAGIC,VERSION,384,session,sequence,0,0,0,0,1,FLAGS,0,SOURCE_INDEX,
           target['block'],target['channel'],0,0,0,900000,1,16|(16<<16),96,32,256]
    packet=struct.pack('<24I',*words)+bytes(matrix)+bytes(disabled)
    packet=packet[:44]+struct.pack('<I',crc32_packet(packet))+packet[48:]
    decode_digital(packet)
    # No SDK/raw identity or exposure data is fabricated. Existing dataclass
    # storage slots are null; manual as_record never relabels them as camera data.
    return ManualBenchFrame(packet,bytes(matrix),bytes(disabled),None,None,None,None,
        now_ns,now_ns,None,'MANUAL_OPERATOR_TEST',
        {**target,'level':1,'region_quality':'MANUAL_COMMAND_NOT_DEPTH_OBSERVATION'},None,proof)


def submit_manual(backend,frame):
    """Explicit manual entry reuses the sealed transaction engine, not LIVE gate.

    It never changes DigitalFrame.actual_live or the original Linux backend.
    """
    from runtime_control.digital_backend import DigitalTransaction
    require(isinstance(frame,ManualBenchFrame) and not frame.actual_live
            and frame.source_kind=='MANUAL_OPERATOR_TEST','EXACT_MANUAL_FRAME_REQUIRED')
    require(not backend.closed,'MANUAL_BACKEND_CLOSED')
    d=decode_digital(frame.packet)
    require(d['selected']['source_index']==SOURCE_INDEX and d['selected']['level']==1,
            'MANUAL_EXACT_SOURCE131_LEVEL1_REQUIRED')
    if backend.transaction is None:
        from runtime_control.uio import open_confirmed_transport
        transport=open_confirmed_transport(backend.profile)
        backend.transaction=DigitalTransaction(transport,emit=backend.emit,clock_ns=backend.clock_ns,
            timeout_ns=backend.timeout_ns,max_polls=backend.max_polls)
    result=backend.transaction._execute_digital(frame)
    result.update(mode='MANUAL_SINGLE_MOTOR_BENCH',source_kind='MANUAL_OPERATOR_TEST',actual_live=False,
        raw_sha256=None,G6C1_packet_sha256=None,sdk_frame_index=None,operator_bench_permission=True,
        production_output_permitted=False,production_ready=False,
        VPLUS_required='ON_USER_CONFIRMED_UNDER_EXTERNAL_HUMAN_SUPERVISION',
        scope='MANUAL_LOCAL_COMMAND_PL_READBACK;NOT_CAMERA_OR_ACTUAL_VIBRATION_ACCEPTANCE',
        actual_vibration='NOT_VERIFIED_REQUIRES_OPERATOR_OBSERVATION')
    return result
