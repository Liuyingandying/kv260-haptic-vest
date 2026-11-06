"""Explicit supervised DGM2 matrix bench; no SDK, hardware, or production gate on import."""
from dataclasses import dataclass,fields
from pathlib import Path
import hashlib,json,os,re,struct
from runtime_control.digital_bringup import mapping,crc32_packet
from runtime_control.digital_matrix import (DigitalMatrixFrame,decode_matrix_packet,
    matrix_stage_indices,MAGIC,VERSION,FLAGS,DESIGN_ID)
from runtime_control.digital_matrix_backend import DigitalMatrixTransaction
from .supervised_single import TRUE_FIELDS,ORIGINAL_SUPPLY_BASIS,_unique,require
SCHEMA='R6_SUPERVISED_MATRIX_BENCH_ATTESTATION_V4'
MODE='SUPERVISED_MATRIX_BENCH'
BASE_FIELDS={'schema','nonce','boot_id','output_directory','duration_s','test_kind',
    'matrix_stage_cells','output_enabled_block_mask','level_cap',*TRUE_FIELDS,
    'VPLUS_state','confirmation','shared_VPLUS','unverified_branches_Vplus_isolated',
    'static_Vplus_voltage_V','static_current_A','numeric_measurements_status',
    'static_all_actuators_stationary_observed','operator_baseline_acceptable',
    'baseline_evidence_basis','prior_actual_vibration_observed','prior_observation_evidence',
    'prior_verified_stage_cells'}

@dataclass(frozen=True)
class MatrixAttestation:
    row:dict
    sha256:str
    @property
    def duration_s(self):return self.row['duration_s']
    @property
    def stage_cells(self):return self.row['matrix_stage_cells']
    @property
    def output_mask(self):return self.row['output_enabled_block_mask']
    def as_record(self):
        return {**self.row,'attestation_sha256':self.sha256,
            'evidence_kind':'EXTERNAL_OPERATOR_ATTESTATION_NOT_AN_ELECTRICAL_SENSOR',
            'bench_known_limitations':{'electrical_ratings_and_overload_margin':'NOT_VERIFIED',
                'automatic_overcurrent_protection':'NOT_VERIFIED_NOT_ASSERTED',
                'other_128_outputs':'SHARED_VPLUS_NOT_ISOLATED;NO_COMMANDS_TO_CH0_CH2',
                'hard_physical_duration_bound':'NOT_VERIFIED;PL_LOCAL_LEASE_AND_PAD_WINDOW_NOT_PHYSICAL_POWER_SWITCH',
                'actual_vibration':'REQUIRES_OPERATOR_OBSERVATION','production_ready':False}}


def load_attestation(path,*,out,duration_s,stage_cells,output_mask,level_cap,test_kind,boot_id,consume=False):
    path,out=Path(path),Path(out)
    require(path.is_absolute() and out.is_absolute() and path.is_file() and not path.is_symlink(),'MATRIX_PROOF_REGULAR_ABSOLUTE_PATHS_REQUIRED')
    require(path.stat().st_size<=16384,'MATRIX_PROOF_SIZE')
    raw=path.read_bytes();row=json.loads(raw.decode('utf8'),object_pairs_hook=_unique)
    require(isinstance(row,dict) and set(row)==BASE_FIELDS and row['schema']==SCHEMA,'MATRIX_PROOF_SCHEMA')
    require(isinstance(row['nonce'],str) and re.fullmatch('[0-9a-f]{32}',row['nonce']),'MATRIX_PROOF_NONCE')
    require(isinstance(boot_id,str) and re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',boot_id) and row['boot_id']==boot_id,'MATRIX_PROOF_BOOT_MISMATCH')
    require(isinstance(row['output_directory'],str) and Path(row['output_directory']).is_absolute() and Path(row['output_directory']).resolve()==out.resolve(),'MATRIX_PROOF_OUTPUT_MISMATCH')
    require(type(duration_s) is int and duration_s in (8,60,600) and type(row['duration_s']) is int and row['duration_s']==duration_s,'MATRIX_PROOF_DURATION_MISMATCH')
    matrix_stage_indices(stage_cells)
    require(type(row['matrix_stage_cells']) is int and row['matrix_stage_cells']==stage_cells,'MATRIX_PROOF_STAGE_MISMATCH')
    require(type(output_mask) is int and output_mask==0xf0f0 and type(row['output_enabled_block_mask']) is int and row['output_enabled_block_mask']==output_mask,'MATRIX_PROOF_F0F0_MASK_REQUIRED')
    require(type(level_cap) is int and level_cap==1 and type(row['level_cap']) is int and row['level_cap']==1,'MATRIX_PROOF_EXACT_LEVEL_CAP1_REQUIRED')
    require(test_kind in ('MANUAL_MATRIX','LIVE_MATRIX','LIVE_MATRIX_CONTINUOUS') and row['test_kind']==test_kind,'MATRIX_PROOF_TEST_KIND_MISMATCH')
    require(all(row[k] is True for k in TRUE_FIELDS),'MATRIX_OPERATOR_PHYSICAL_CONDITIONS_NOT_ATTESTED')
    require(row['VPLUS_state']=='ON_USER_CONFIRMED' and row['confirmation']=='USER_EXPLICITLY_CONFIRMED','MATRIX_VPLUS_NOT_CONFIRMED_ON')
    require(row['shared_VPLUS'] is True and row['unverified_branches_Vplus_isolated'] is False,'MATRIX_SHARED_VPLUS_CONTEXT_REQUIRED')
    require(row['static_Vplus_voltage_V'] is None and row['static_current_A'] is None and row['numeric_measurements_status']=='NOT_REPORTED','MATRIX_ABSENT_NUMBERS_MUST_BE_NULL_NOT_INVENTED')
    require(row['baseline_evidence_basis']==ORIGINAL_SUPPLY_BASIS and row['static_all_actuators_stationary_observed'] is True and row['operator_baseline_acceptable'] is True,'MATRIX_QUIET_ORIGINAL_SUPPLY_BASELINE_REQUIRED')
    require(type(row['prior_actual_vibration_observed']) is bool and isinstance(row['prior_observation_evidence'],str) and type(row['prior_verified_stage_cells']) is int and row['prior_verified_stage_cells'] in (0,1,8,32,64,128),'MATRIX_PRIOR_OBSERVATION_SCHEMA')
    if duration_s==60:
        require(test_kind=='LIVE_MATRIX' and row['prior_actual_vibration_observed'] and row['prior_verified_stage_cells']>=stage_cells and bool(row['prior_observation_evidence'].strip()),'MATRIX_60S_REQUIRES_PRIOR_ACTUAL_SAME_STAGE_VIBRATION')
    if test_kind=='LIVE_MATRIX_CONTINUOUS':
        require(duration_s==600 and stage_cells==128 and row['prior_actual_vibration_observed']
            and row['prior_verified_stage_cells']==128 and bool(row['prior_observation_evidence'].strip()),
            'MATRIX_EXTENDED600_REQUIRES_PRIOR_ACTUAL128_AND_EXPLICIT_PROOF')
    else:require(duration_s in (8,60),'LEGACY_MATRIX_KIND_DURATION_MUST_STAY8_OR60')
    if test_kind=='MANUAL_MATRIX':require(duration_s==8,'MATRIX_MANUAL_FIRST_STAGE_EXACT8S')
    proof=MatrixAttestation(row,hashlib.sha256(raw).hexdigest())
    if consume:
        require(out.is_dir() and not out.is_symlink(),'MATRIX_NEW_OUTPUT_DIRECTORY_REQUIRED')
        with (out/'OPERATOR_ATTESTATION_CONSUMED.json').open('x') as fp:
            json.dump({**proof.as_record(),'worker_pid':os.getpid()},fp,indent=2);fp.flush();os.fsync(fp.fileno())
    return proof


@dataclass(frozen=True)
class SupervisedMatrixFrame(DigitalMatrixFrame):
    operator_attestation:MatrixAttestation|None=None
    def as_record(self):
        record=super().as_record()
        record.update(mode=MODE,operator_bench_permission=True,production_output_permitted=False,
            production_ready=False,VPLUS_procedure='ON_USER_CONFIRMED_UNDER_EXTERNAL_HUMAN_SUPERVISION',
            operator_attestation=self.operator_attestation.as_record(),actual_vibration='NOT_VERIFIED')
        return record


def wrap_live_frame(frame,proof):
    require(isinstance(proof,MatrixAttestation) and proof.row['test_kind'] in ('LIVE_MATRIX','LIVE_MATRIX_CONTINUOUS'),'MATRIX_LIVE_ATTESTATION_REQUIRED')
    require(isinstance(frame,DigitalMatrixFrame) and frame.actual_live,'MATRIX_REAL_LIVE_FRAME_REQUIRED')
    d=decode_matrix_packet(frame.packet,target_mask=0xf0f0)
    require(frame.target_mask==0xf0f0 and d['level_cap']==1 and d['output_enabled_block_mask']==proof.output_mask and frame.allowed_source_indices==matrix_stage_indices(proof.stage_cells),'MATRIX_LIVE_STAGE_OR_CAP_MISMATCH')
    scope=set(frame.allowed_source_indices)
    expected=bytes(min(v,1) if i in scope and not frame.source['unknown'][i//8]&(1<<(i%8)) else 0 for i,v in enumerate(frame.source['matrix']))
    require(d['unknown']==frame.source['unknown'] and d['matrix']==expected,
            'MATRIX_LIVE_PROJECTION_MUST_EXACTLY_MATCH_SOURCE_UNKNOWN_AND_STAGE')
    return SupervisedMatrixFrame(**{f.name:getattr(frame,f.name) for f in fields(DigitalMatrixFrame)},operator_attestation=proof)


@dataclass(frozen=True)
class ManualMatrixFrame(DigitalMatrixFrame):
    operator_attestation:MatrixAttestation|None=None
    def as_record(self):
        d=decode_matrix_packet(self.packet,target_mask=0xf0f0)
        return {'schema':'R6_MANUAL_MATRIX_COMMAND_V1','mode':'MANUAL_MATRIX_BENCH',
            'source_kind':'MANUAL_OPERATOR_TEST','actual_live':False,'session':self.session,'sequence':self.sequence,
            'raw_sha256':None,'G6C1_packet_sha256':None,'sdk_frame_index':None,'capture_timestamp':None,
            'capture_time_verified':False,'capture_age_ns':None,'FRESH':False,
            'local_command_generation_monotonic_ns':self.receipt_monotonic_ns,
            'perception_matrix':None,'perception_UNKNOWN':None,'command_matrix':list(d['matrix']),
            'command_disabled_mask_hex':d['unknown'].hex(),'allowed_source_indices':list(self.allowed_source_indices),
            'mask_semantics':'MANUAL_COMMAND_DISABLED_MASK_NOT_PERCEPTION_QUALITY',
            'output_enabled_block_mask':0xf0f0,'level_cap':1,'packet_hex':self.packet.hex(),
            'packet_sha256':hashlib.sha256(self.packet).hexdigest(),'production_ready':False,
            'physical_output_permitted':False,'production_output_permitted':False,'operator_bench_permission':True,
            'VPLUS_procedure':'ON_USER_CONFIRMED_UNDER_EXTERNAL_HUMAN_SUPERVISION',
            'operator_attestation':self.operator_attestation.as_record(),'actual_vibration':'NOT_VERIFIED'}


def manual_frame(proof,*,session,sequence,now_ns):
    require(isinstance(proof,MatrixAttestation) and proof.row['test_kind']=='MANUAL_MATRIX' and proof.duration_s==8,'MANUAL_MATRIX_EXPLICIT_PROOF_REQUIRED')
    require(type(session) is int and 0<session<=0xffffffff and type(sequence) is int and 0<sequence<=0xffffffff and type(now_ns) is int and now_ns>0,'MANUAL_MATRIX_LOCAL_IDENTITY_REQUIRED')
    scope=matrix_stage_indices(proof.stage_cells);matrix=bytearray(256);disabled=bytearray(b'\xff'*32)
    for i in scope:matrix[i]=1;disabled[i//8]&=~(1<<(i%8))
    words=[MAGIC,VERSION,384,session,sequence,0,0,0,0,1,FLAGS,0,0xf0f0,1,0,0,0,0,900000,1,16|(16<<16),96,32,256]
    packet=struct.pack('<24I',*words)+matrix+disabled;packet=packet[:44]+struct.pack('<I',crc32_packet(packet))+packet[48:]
    decode_matrix_packet(packet,target_mask=0xf0f0)
    source={'session':session,'sequence':sequence,'sdk_frame_index':None,'raw_sha256':None,'matrix':bytes(matrix),'unknown':bytes(disabled),'geometry_quality':None}
    return ManualMatrixFrame(packet,b'',source,None,'MANUAL_OPERATOR_TEST',now_ns,now_ns,0xf0f0,scope,proof)


def wrap_submission(result,frame):
    require(isinstance(frame,(SupervisedMatrixFrame,ManualMatrixFrame)) and result.get('FRESH') is False and result.get('physical_output_permitted') is False,'MATRIX_SUBMISSION_CONTEXT_REQUIRED')
    out=dict(result);out.update(mode='MANUAL_MATRIX_BENCH' if isinstance(frame,ManualMatrixFrame) else MODE,
        source_kind=frame.source_kind,actual_live=frame.actual_live,operator_bench_permission=True,
        production_output_permitted=False,production_ready=False,
        VPLUS_required='ON_USER_CONFIRMED_UNDER_EXTERNAL_HUMAN_SUPERVISION',
        operator_attestation=frame.operator_attestation.as_record(),actual_vibration='NOT_VERIFIED')
    return out


def submit_manual(backend,frame):
    require(isinstance(frame,ManualMatrixFrame) and not frame.actual_live,'EXACT_MANUAL_MATRIX_FRAME_REQUIRED')
    require(not backend.closed and backend.target_mask==0xf0f0,'MANUAL_MATRIX_BACKEND_CONTEXT')
    d=backend.decode_packet(frame.packet)
    proof=frame.operator_attestation
    require(isinstance(proof,MatrixAttestation) and proof.row['test_kind']=='MANUAL_MATRIX',
            'MANUAL_MATRIX_VALIDATED_CONTEXT_REQUIRED')
    scope=set(matrix_stage_indices(proof.stage_cells))
    require(d['output_enabled_block_mask']==proof.output_mask and d['level_cap']==1
            and d['matrix']==bytes(int(i in scope) for i in range(256)),
            'MANUAL_MATRIX_PACKET_MUST_EXACTLY_MATCH_ATTESTED_STAGE')
    if backend.transaction is None:
        from runtime_control.uio import open_confirmed_transport
        backend.transaction=backend.make_transaction(open_confirmed_transport(backend.profile))
    return wrap_submission(backend.transaction._execute_digital(frame),frame)
