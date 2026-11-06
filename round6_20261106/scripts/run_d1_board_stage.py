"""One hash-bound D1 runtime stage; no reload/retry or automatic evidence fetch.

Import/check-only is HOST-only. The emitted board wrapper performs metadata and
Linux health checks, launches the exact packaged argv once, then archives files
only after a known completed return. Unknown MMIO/timeout skips posthealth and
archive/fetch and leaves STOP_MMIO. All profiles/outputs are outside the package.
"""
from pathlib import Path,PurePosixPath
import argparse,ast,datetime,hashlib,inspect,json,re,secrets

APP='kv260-round6-digitald-20261105';STEM='kv260_round6_digitald'
FIRMWARE=f'xilinx/{APP}/{STEM}.bit.bin';DESIGN_ID=0x52364431
CONTROL_HELPER_SHA='186a644cc58ae447eec099245019105bfd52125a536e6d7a34e36831f804aa93'
CONTINUOUS_NATIVE_REL='native/gemini_live_compact_continuous.aarch64'
CONTINUOUS_NATIVE_SHA='6eb4aacce1a488b7aee6ac383030322ee2fdab2f42cb6eb828fe9c750c949cd9'
BOARD_STAGE_BUDGET_S=110;RUNTIME_WAIT_S=90;HOST_WAIT_S=120
PROFILE_FIELDS=('expected_firmware_name','dt_firmware_property','installed_bitstream_path',
                'installed_bitstream_sha256','native_helper_path','native_helper_sha256','expected_design_id')


def configure_design(matrix_mode=False):
    """Host-only explicit identity selection, before validation/source emission."""
    require(type(matrix_mode) is bool,'EXPLICIT_MATRIX_MODE_BOOLEAN_REQUIRED')
    global APP,STEM,FIRMWARE,DESIGN_ID
    APP='kv260-round6-matrixd2-20261106' if matrix_mode else 'kv260-round6-digitald-20261105'
    STEM='kv260_round6_matrixd2' if matrix_mode else 'kv260_round6_digitald'
    FIRMWARE=f'xilinx/{APP}/{STEM}.bit.bin'
    DESIGN_ID=0x52364d32 if matrix_mode else 0x52364431


def require(ok,reason):
    if not ok:raise ValueError(reason)


def sha_file(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_rel(value):
    require(isinstance(value,str) and '\\' not in value and '\x00' not in value,'UNSAFE_PACKAGE_RELATIVE_PATH')
    p=PurePosixPath(value)
    require(not p.is_absolute() and p.as_posix()==value and '..' not in p.parts and value and
            '__pycache__' not in p.parts,'UNSAFE_PACKAGE_RELATIVE_PATH')
    return p


def sha_valid(value):return isinstance(value,str) and re.fullmatch('[0-9a-f]{64}',value) is not None


def stage_budgets(duration_s):
    require(type(duration_s) is int and duration_s in (8,60,600),'APPROVED_CAPTURE_DURATION_REQUIRED')
    return {'inner_parent_s':duration_s+55,'runtime_wait_s':duration_s+82,
            'stage_s':duration_s+102,'host_s':duration_s+112}


def approved_options(latest,duration_s=8,source_index=None,renew_digital_window=False):
    stage_budgets(duration_s)
    supported=latest.get('supported_durations',[8])
    require(isinstance(supported,list) and all(type(x) is int and x in (8,60) for x in supported)
            and (duration_s in supported or duration_s==600),'RUNTIME_MANIFEST_DOES_NOT_SUPPORT_DURATION')
    if duration_s==600:
        require(DESIGN_ID==0x52364d32 and source_index is None and
                latest.get('supports_extended_matrix_session') is True and
                latest.get('extended_matrix_session_s')==600 and
                latest.get('continuous_native_sha256')==CONTINUOUS_NATIVE_SHA,
                'EXTENDED600_REQUIRES_EXPLICIT_DGM2_CAPABILITY_AND_CONTINUOUS_NATIVE')
    if source_index is not None:
        require(type(source_index) is int and 0<=source_index<256 and latest.get('supports_fixed_source_index') is True,
                'RUNTIME_MANIFEST_DOES_NOT_SUPPORT_FIXED_SOURCE_INDEX')
    require(type(renew_digital_window) is bool,'EXPLICIT_RENEWAL_BOOLEAN_REQUIRED')
    if duration_s in (60,600):require(renew_digital_window,'SIXTY_SECONDS_REQUIRES_EXPLICIT_DIGITAL_WINDOW_RENEWAL')
    if renew_digital_window:
        require(duration_s in (60,600) and latest.get('supports_digital_window_renewal') is True and
                latest.get('digital_window_renewal_interval_s')==30 and latest.get('hardware_signal_window_s')==50,
                'REVIEWED_SIXTY_SECOND_DIGITAL_WINDOW_RENEWAL_CAPABILITY_REQUIRED')


def matrix_options(latest,duration_s,source_index,operator_attestation_path,manual_single_motor,
                   matrix_cell_limit,output_mask,level_cap,supervised_matrix,manual_matrix):
    require(DESIGN_ID==0x52364d32 and latest.get('wire_protocol')=='DGM2' and
            latest.get('supports_digital_matrix_mode') is True and latest.get('matrix_design_id')==DESIGN_ID,
            'EXPLICIT_REVIEWED_DGM2_DESIGN_AND_PACKAGE_REQUIRED')
    require(source_index is None and not manual_single_motor,'MATRIX_SCOPE_HAS_NO_SINGLE_SOURCE_INDEX')
    require(type(matrix_cell_limit) is int and matrix_cell_limit in (8,32,64,128) and
            latest.get('matrix_supported_stage_cells')==[8,32,64,128],'EXPLICIT_REVIEWED_MATRIX_STAGE_REQUIRED')
    require(type(output_mask) is int and output_mask==0xf0f0 and
            latest.get('matrix_allowed_target_mask')==0xf0f0,'MATRIX_HEALTHY_F0F0_MASK_ONLY')
    require(type(level_cap) is int and level_cap==1 and latest.get('matrix_level_cap')==1,
            'MATRIX_MINIMUM_LEVEL_CAP_ONE_REQUIRED')
    require(type(supervised_matrix) is bool and type(manual_matrix) is bool and
            not (supervised_matrix and manual_matrix),'ONE_EXPLICIT_MATRIX_TEST_KIND_REQUIRED')
    if manual_matrix:
        require(duration_s==8 and operator_attestation_path is not None and
                latest.get('supports_manual_matrix') is True,'MANUAL_MATRIX_EXACT_EIGHT_SECONDS_AND_PROOF_REQUIRED')
    if supervised_matrix:
        require(operator_attestation_path is not None and latest.get('supports_supervised_matrix') is True,
                'SUPERVISED_MATRIX_EXPLICIT_PROOF_REQUIRED')
    if duration_s==600:
        require(matrix_cell_limit==128 and supervised_matrix and not manual_matrix and
                operator_attestation_path is not None,'EXTENDED600_ONLY_SUPERVISED_LIVE128_WITH_PROOF')
    if operator_attestation_path is not None:
        require(supervised_matrix or manual_matrix,'MATRIX_PROOF_REQUIRES_EXPLICIT_MATRIX_POWER_MODE')
        require(isinstance(operator_attestation_path,str) and PurePosixPath(operator_attestation_path).is_absolute(),
                'ABSOLUTE_REMOTE_MATRIX_ATTESTATION_REQUIRED')


def runtime_parent_budget(source,duration_s=8):
    """Evaluate only the AST-whitelisted pure parent helper, never runtime code."""
    stage_budgets(duration_s);tree=ast.parse(source);matches=[]
    constants={}
    for node in tree.body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            try:value=ast.literal_eval(node.value)
            except (ValueError,TypeError):continue
            if type(value) is int or (isinstance(value,(tuple,list)) and all(type(x) is int for x in value)):
                constants[node.targets[0].id]=value
    functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='parent_deadline_s']
    def is_duration(node):
        if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id=='args' and node.attr=='duration_s':
            return True
        if not (isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='session_duration_s'
                and len(node.args)==1 and isinstance(node.args[0],ast.Name) and node.args[0].id=='args' and not node.keywords):
            return False
        # The extended caller selects600 explicitly; validate both actual pure
        # selector bodies, rather than trusting an arbitrary same-named call.
        expected=ast.parse('''def extended_mode(args):return getattr(args,'extended_session_s',None) is not None
def session_duration_s(args):return args.extended_session_s if extended_mode(args) else args.duration_s
''')
        for reference in expected.body:
            found=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==reference.name]
            require(len(found)==1 and ast.dump(found[0])==ast.dump(reference),'EXTENDED_DURATION_SELECTOR_NOT_EXACT_REVIEWED_AST')
        return True
    for node in ast.walk(tree):
        if not (isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr=='wait'):continue
        for keyword in node.keywords:
            if keyword.arg!='timeout':continue
            v=keyword.value
            if (isinstance(v,ast.BinOp) and isinstance(v.op,ast.Add) and is_duration(v.left)
                    and isinstance(v.right,ast.Constant) and v.right.value==55):
                # Backward-compatible reading of the preserved eight-second package.
                require(duration_s==8,'SIXTY_SECOND_PACKAGE_REQUIRES_REVIEWED_PARENT_HELPER')
                matches.append(duration_s+55)
            elif isinstance(v,ast.Call) and isinstance(v.func,ast.Name) and v.func.id=='parent_deadline_s':
                require(len(v.args)==1 and not v.keywords and is_duration(v.args[0]) and len(functions)==1,'PARENT_HELPER_CALL_REQUIRED')
                fn=functions[0]
                allowed=(ast.FunctionDef,ast.arguments,ast.arg,ast.Expr,ast.Call,ast.Name,ast.Load,ast.Constant,
                         ast.Compare,ast.In,ast.NotIn,ast.Eq,ast.NotEq,ast.BoolOp,ast.And,ast.Or,ast.UnaryOp,ast.Not,
                         ast.Return,ast.BinOp,ast.Add,ast.Tuple,ast.List,ast.Starred)
                require(not fn.decorator_list and all(isinstance(n,allowed) for n in ast.walk(fn)),'PARENT_HELPER_NOT_PURE_WHITELISTED_AST')
                require(all(isinstance(n.func,ast.Name) and n.func.id=='require' for n in ast.walk(fn) if isinstance(n,ast.Call)),
                        'PARENT_HELPER_UNKNOWN_CALL')
                require(len(fn.args.args)==1 and fn.args.args[0].arg=='capture_s' and constants.get('PARENT_ADDITIONAL_BUDGET_S')==55,
                        'PARENT_HELPER_SIGNATURE_OR_ADDITION')
                scope={'__builtins__':{},'require':require,**constants}
                exec(compile(ast.Module(body=[fn],type_ignores=[]),'<pure_parent_budget_helper>','exec'),scope)
                # Execute the actual guard and return expression for requested duration.
                matches.append(scope['parent_deadline_s'](duration_s))
    require(matches==[duration_s+55],'RUNTIME_PARENT_BUDGET_MISMATCH')
    return matches[0]


def outcome_unknown(returncode,parent,worker,stop_exists=False):
    if returncode is None or returncode==124 or stop_exists:return True
    if not isinstance(parent,dict) or not isinstance(worker,dict):return True
    if parent.get('timeout') is not False or type(parent.get('returncode')) is not int:return True
    if returncode!=parent['returncode']:return True
    if worker.get('MMIO_unknown') is not False:return True
    if any('STOP_ALL_HARDWARE' in str(x.get('required_action','')) for x in (parent,worker)):return True
    if any('NATIVE_STOP:' in str(x) or 'TimeoutExpired' in str(x) for x in worker.get('errors',[])):return True
    return False


def critical_kernel_lines(kernel):
    tokens=('soft lockup','hard lockup','serror','internal error:',
            'rcu stall','rcu_sched detected stalls','blocked for more than')
    # Kernel driver names such as "mtdoops:" are not kernel Oops reports.
    # Require a word boundary for Oops/BUG without suppressing real reports.
    return [line for line in kernel.splitlines() if
            any(token in line.lower() for token in tokens) or
            re.search(r'(?<![a-z0-9_])(?:oops|bug):',line,re.IGNORECASE)]


def validate_returned_restart(marker,prior,parent,worker,before,after,current,receipt_sha,stop_exists=False):
    """A manual admission checks returned evidence; missing/unknown is never a retry."""
    require(marker.get('receipt_sha256')==receipt_sha and prior.get('bindings',{}).get('receipt_sha256')==receipt_sha,
            'PRIOR_STAGE_RECEIPT_BINDING_MISMATCH')
    code=prior.get('runtime_returncode')
    require(type(code) is int and not outcome_unknown(code,parent,worker,stop_exists),
            'PRIOR_RUNTIME_NOT_KNOWN_RETURNED_NO_RESTART')
    require(prior.get('posthealth_run') is True and not prior.get('MMIO_unknown',False) and
            not prior.get('kernel_new_unexpected'),'PRIOR_STAGE_POSTHEALTH_NOT_CONFIRMED')
    for key in ('boot_id','firmware','manager','installed_bitstream_sha256'):
        require(current.get(key) is not None and before.get(key)==after.get(key)==current[key],
                'PRIOR_STAGE_NOT_SAME_HEALTHY_BOOT:'+key)
    require(current['manager']=='operating','CURRENT_MANAGER_NOT_OPERATING')


def global_worker_lock_path(source):
    """Read only the reviewed path and full-worker ownership shape from AST."""
    tree=ast.parse(source);paths=[]
    for node in tree.body:
        if (isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name)
                and node.targets[0].id=='SESSION_LEDGER_PATH' and isinstance(node.value,ast.Call)
                and isinstance(node.value.func,ast.Name) and node.value.func.id=='Path' and len(node.value.args)==1):
            paths.append(ast.literal_eval(node.value.args[0]))
    require(paths==['/home/ubuntu/kv260_r6_d1_session_ledger.jsonl'],'REVIEWED_GLOBAL_WORKER_LOCK_PATH_REQUIRED')
    workers=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name=='worker']
    require(len(workers)==1,'GLOBAL_WORKER_LOCK_OWNER_REQUIRED')
    blocks=[node for node in workers[0].body if isinstance(node,ast.With)]
    require(len(blocks)==1 and len(blocks[0].items)==1,'FULL_WORKER_LOCK_SCOPE_REQUIRED')
    item=blocks[0].items[0];call=item.context_expr
    require(isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='open_session_ledger'
            and not call.args and not call.keywords and isinstance(item.optional_vars,ast.Name),
            'FULL_WORKER_LEDGER_LOCK_REQUIRED')
    body=blocks[0].body
    require(len(body)==1 and isinstance(body[0],ast.Return) and isinstance(body[0].value,ast.Call)
            and isinstance(body[0].value.func,ast.Name) and body[0].value.func.id=='_worker_locked'
            and len(body[0].value.args)==2 and isinstance(body[0].value.args[1],ast.Name)
            and body[0].value.args[1].id==item.optional_vars.id,'LOCK_MUST_COVER_COMPLETE_WORKER')
    return paths[0]


def validate_receipt(latest,manifest,receipt,duration_s=8,source_index=None,renew_digital_window=False):
    require(latest.get('schema')=='R6_D1_LOCAL_PACKAGE_READY_V1' and latest.get('status')=='READY_FOR_ROOT_DEPLOY_NOT_BOARD_ACCEPTED',
            'REVIEWED_D1_RUNTIME_LATEST_PACKAGE_REQUIRED')
    require(manifest.get('schema')=='R6_D1_DEPLOY_PACKAGE_V1' and manifest.get('mode')=='DIGITAL_BRINGUP_MODE' and
            manifest.get('expected_design_id')==DESIGN_ID and manifest.get('healthy_target_mask')==0xf0f0 and
            manifest.get('duration_s')==8 and manifest.get('raw_samples')==64 and manifest.get('FRESH') is False and
            manifest.get('physical_output_permitted') is False,'D1_RUNTIME_DIGITAL_SCOPE_REQUIRED')
    if DESIGN_ID==0x52364d32:
        require(latest.get('wire_protocol')==manifest.get('wire_protocol')=='DGM2',
                'MATRIX_DESIGN_REQUIRES_EXPLICIT_DGM2_PROTOCOL')
        for key,value in {'supports_digital_matrix_mode':True,'matrix_design_id':0x52364d32,
                'matrix_allowed_target_mask':0xf0f0,'matrix_supported_stage_cells':[8,32,64,128],
                'matrix_level_cap':1,'supports_supervised_matrix':True,'supports_manual_matrix':True}.items():
            require(latest.get(key)==manifest.get(key)==value,'MATRIX_PACKAGE_CAPABILITY_MISMATCH:'+key)
    else:
        require(latest.get('wire_protocol')!='DGM2' and manifest.get('wire_protocol')!='DGM2',
                'DGM2_PACKAGE_REQUIRES_EXPLICIT_MATRIX_DESIGN_SELECTION')
    approved_options(latest,duration_s,source_index,renew_digital_window)
    if renew_digital_window:
        require(all(manifest.get(key)==latest.get(key) for key in
                    ('supports_digital_window_renewal','digital_window_renewal_interval_s','hardware_signal_window_s')),
                'LATEST_MANIFEST_DIGITAL_WINDOW_RENEWAL_MISMATCH')
    require(manifest.get('supported_durations',[8])==latest.get('supported_durations',[8]),'LATEST_MANIFEST_DURATION_SUPPORT_MISMATCH')
    if 'supported_durations' in latest:
        deadlines={str(d):d+55 for d in latest['supported_durations']}
        require(latest.get('parent_deadlines_s')==manifest.get('parent_deadlines_s')==deadlines,'DECLARED_PARENT_DEADLINES_MISMATCH')
    if source_index is not None:require(manifest.get('supports_fixed_source_index') is True,'MANIFEST_FIXED_SOURCE_INDEX_NOT_SUPPORTED')
    if duration_s==60:
        require(manifest.get('raw_sampling_modes',{}).get('60')=='BOUNDED_RAW_SAMPLING' and latest.get('raw_sampling_modes',{}).get('60')=='BOUNDED_RAW_SAMPLING',
                'SIXTY_SECOND_BOUNDED_RAW_SAMPLING_MUST_BE_EXPLICIT')
    rows=manifest.get('files');require(isinstance(rows,dict) and 1<=len(rows)<=128,'RUNTIME_FILE_ROWS_REQUIRED')
    for rel,row in rows.items():
        safe_rel(rel);require(set(row)=={'bytes','sha256'} and type(row['bytes']) is int and row['bytes']>0 and sha_valid(row['sha256']),'RUNTIME_SIZE_SHA_REQUIRED')
    if duration_s==600:
        require(all(manifest.get(k)==latest.get(k) for k in
                    ('supports_extended_matrix_session','extended_matrix_session_s','continuous_native_sha256')) and
                rows.get(CONTINUOUS_NATIVE_REL,{}).get('sha256')==CONTINUOUS_NATIVE_SHA and
                'runtime_night/extended_session.py' in rows,
                'EXTENDED600_MANIFEST_CAPABILITIES_NATIVE_AND_LOG_SUPERVISOR_REQUIRED')
    expected=dict(rows)
    expected['PACKAGE_MANIFEST.json']={'bytes':latest['manifest_bytes'],'sha256':latest['manifest_sha256']}
    require(receipt.get('status')=='PASS' and receipt.get('uid')==1000 and receipt.get('SDK_started') is False and
            receipt.get('MMIO_access') is False and receipt.get('PL_changed') is False,'UNPRIVILEGED_DEPLOY_RECEIPT_REQUIRED')
    require(isinstance(receipt.get('target'),str) and re.fullmatch('/home/ubuntu/kv260_r6_[A-Za-z0-9_]+_[0-9a-f]{12}',receipt['target']),
            'ISOLATED_RUNTIME_TARGET_REQUIRED')
    require(receipt.get('files')==expected and sha_valid(receipt.get('archive_sha256')),'RUNTIME_RECEIPT_EXACT_FILE_BINDING_REQUIRED')
    require(latest.get('profile_fields')==list(PROFILE_FIELDS) and latest.get('profile_expected_design_id')==DESIGN_ID,
            'EXACT_D1_PROFILE_FIELDS_REQUIRED')
    require(latest.get('control_helper_sha256')==CONTROL_HELPER_SHA and
            rows.get('runtime_control/native/control_mmio32.aarch64.so',{}).get('sha256')==CONTROL_HELPER_SHA,
            'EXACT_NATIVE32_CONTROL_HELPER_REQUIRED')
    require(latest.get('native_sha256')==rows.get('native/gemini_live_compact.aarch64',{}).get('sha256') and
            latest.get('gpio_helper_sha256')==rows.get('iic_c1/gpio_mmio32.aarch64.so',{}).get('sha256'),
            'NATIVE_AND_GPIO_HELPER_PACKAGE_BINDING_REQUIRED')
    return expected


def load_runtime(pointer,receipt,duration_s=8,source_index=None,renew_digital_window=False,allow_returned_restart=False):
    latest=json.loads(pointer.read_text(encoding='utf-8-sig'));tree=Path(latest['tree'])
    require(tree.is_dir() and not tree.is_symlink(),'RUNTIME_TREE_REQUIRED')
    path=tree/'PACKAGE_MANIFEST.json';manifest=json.loads(path.read_text(encoding='utf-8-sig'))
    require(sha_file(path)==latest['manifest_sha256'],'RUNTIME_MANIFEST_SHA_MISMATCH')
    # This derived local field is not silently added to the deployed manifest.
    latest={**latest,'manifest_bytes':path.stat().st_size}
    expected=validate_receipt(latest,manifest,receipt,duration_s,source_index,renew_digital_window)
    for rel,row in expected.items():
        p=tree/rel;require(not p.is_symlink() and p.resolve().is_relative_to(tree.resolve()) and p.is_file() and
                          p.stat().st_size==row['bytes'] and sha_file(p)==row['sha256'],'LOCAL_RUNTIME_SHA_MISMATCH:'+rel)
    require({p.relative_to(tree).as_posix() for p in tree.rglob('*') if p.is_file()}==set(expected),'RUNTIME_TREE_HAS_UNDECLARED_FILES')
    runtime_source=(tree/'runtime_night/run_d1_live.py').read_text(encoding='utf-8-sig')
    for supported in latest.get('supported_durations',[8]):runtime_parent_budget(runtime_source,supported)
    if duration_s==600:runtime_parent_budget(runtime_source,600)
    if allow_returned_restart:global_worker_lock_path(runtime_source)
    return latest,manifest,tree


def make_profile(bin_sha,target):
    require(sha_valid(bin_sha),'EXPLICIT_LOADED_D1_BIN_SHA256_REQUIRED')
    return {'expected_firmware_name':FIRMWARE,
            'dt_firmware_property':'/sys/firmware/devicetree/base/fpga-region/firmware-name',
            'installed_bitstream_path':'/lib/firmware/'+FIRMWARE,'installed_bitstream_sha256':bin_sha,
            'native_helper_path':target+'/runtime_control/native/control_mmio32.aarch64.so',
            'native_helper_sha256':CONTROL_HELPER_SHA,'expected_design_id':DESIGN_ID}


def exact_command(latest,target,profile_path,out,duration_s=8,source_index=None,renew_digital_window=False,
                  operator_attestation_path=None,manual_single_motor=False,matrix_cell_limit=None,
                  output_mask=None,level_cap=None,supervised_matrix=False,manual_matrix=False):
    approved_options(latest,duration_s,source_index,renew_digital_window)
    require(type(manual_single_motor) is bool and (not manual_single_motor or operator_attestation_path is not None),
            'MANUAL_SINGLE_REQUIRES_EXPLICIT_OPERATOR_ATTESTATION')
    template=latest.get('command_argv_template')
    require(isinstance(template,list) and all(isinstance(x,str) for x in template),'EXACT_ARGV_TEMPLATE_REQUIRED')
    command=[x.replace('<TARGET>',target).replace('<D1_PROFILE_JSON>',profile_path).replace('<NEW_ABSOLUTE_OUTPUT_DIR>',out) for x in template]
    expected=['python3','-B',target+'/runtime_night/run_d1_live.py','--profile',profile_path,'--out',out,
              '--native',target+'/native/gemini_live_compact.aarch64','--native-sha256',latest['native_sha256'],
              '--sdk-library','/home/ubuntu/gemini_live_20261026T135635Z/sdk/lib/libOrbbecSDK.so.1.10',
              '--xml','/home/ubuntu/gemini_live_20261026T135635Z/sdk/config/OrbbecSDKConfig_v1.0.xml',
              '--gpio-helper',target+'/iic_c1/gpio_mmio32.aarch64.so','--duration-s','8','--raw-samples','64',
              '--run-once','--vplus-disconnected']
    require(command==expected,'PACKAGED_RUNTIME_ARGV_NOT_EXACT_APPROVED_D1_COMMAND')
    if duration_s==600:
        # Runtime keeps its old duration8 parser default and receives a distinct
        # explicit600 option; the operator proof still binds actual600 seconds.
        index=command.index('--duration-s');del command[index:index+2]
        command[command.index('--native')+1]=target+'/'+CONTINUOUS_NATIVE_REL
        command[command.index('--native-sha256')+1]=CONTINUOUS_NATIVE_SHA
        command+=['--extended-session-s','600']
    else:command[command.index('--duration-s')+1]=str(duration_s)
    if source_index is not None:command+=['--source-index',str(source_index)]
    if renew_digital_window:command+=['--renew-digital-window']
    if DESIGN_ID==0x52364d32:
        matrix_options(latest,duration_s,source_index,operator_attestation_path,manual_single_motor,
                       matrix_cell_limit,output_mask,level_cap,supervised_matrix,manual_matrix)
        matrix_args=['--matrix-mode','--matrix-cell-limit',str(matrix_cell_limit),'--output-mask',hex(output_mask),
                     '--level-cap',str(level_cap)]
        if manual_matrix:
            return ['python3','-B',target+'/runtime_night/manual_single.py','--profile',profile_path,'--out',out,
                '--gpio-helper',target+'/iic_c1/gpio_mmio32.aarch64.so','--duration-s','8','--run-once',
                '--manual-matrix',*matrix_args,'--operator-attestation',operator_attestation_path]
        command+=matrix_args
        if supervised_matrix:
            command.remove('--vplus-disconnected')
            command+=['--supervised-matrix','--operator-attestation',operator_attestation_path]
        return command
    require(matrix_cell_limit is None and output_mask is None and level_cap is None and
            not supervised_matrix and not manual_matrix,'MATRIX_OPTIONS_REQUIRE_EXPLICIT_MATRIX_DESIGN_SELECTION')
    if operator_attestation_path is not None:
        capability='supports_manual_single_motor' if manual_single_motor else 'supports_supervised_single_motor'
        require(latest.get(capability) is True and
                latest.get('supervised_source_index')==131 and latest.get('supervised_level_cap')==1,
                'REVIEWED_SUPERVISED_SINGLE_MOTOR_CAPABILITY_REQUIRED')
        require(duration_s==8 and type(source_index) is int and 0<=source_index<256 and not renew_digital_window,
                'FIRST_SUPERVISED_STAGE_EXACT_EIGHT_SECONDS_VALID_SOURCE_REQUIRED')
        if manual_single_motor:
            require(source_index==131,'MANUAL_SINGLE_EXACT_SOURCE_131_REQUIRED')
        elif source_index!=131:
            require(latest.get('supports_supervised_target_selection') is True and
                    latest.get('supervised_allowed_target_mask')==0xf0f0,
                    'REVIEWED_SUPERVISED_TARGET_SELECTION_CAPABILITY_REQUIRED')
            # Same pure matrix mapping as digital_bringup.mapping(): the left
            # and right halves select blocks0..7 and8..15 respectively.
            row,column=divmod(source_index,16)
            block=row//2+(8 if column>=8 else 0)
            require(bool(0xf0f0 & (1<<block)),'SUPERVISED_TARGET_NOT_IN_HEALTHY_CH1_CH3_MASK')
        require(isinstance(operator_attestation_path,str) and
                PurePosixPath(operator_attestation_path).is_absolute(),
                'ABSOLUTE_REMOTE_OPERATOR_ATTESTATION_REQUIRED')
        if manual_single_motor:
            # Standalone manual worker owns no SDK or native acquisition process.
            command=['python3','-B',target+'/runtime_night/manual_single.py',
                '--profile',profile_path,'--out',out,'--gpio-helper',target+'/iic_c1/gpio_mmio32.aarch64.so',
                '--duration-s','8','--source-index','131','--run-once','--manual-single-motor',
                '--operator-attestation',operator_attestation_path]
        else:
            command.remove('--vplus-disconnected')
            command+=['--supervised-single-motor','--operator-attestation',operator_attestation_path]
    return command


BOARD_SOURCE=r'''
from pathlib import Path,PurePosixPath
import datetime,fcntl,hashlib,json,os,platform,re,stat,subprocess,tarfile,time
class UnknownOutcome(RuntimeError):pass
stage=Path(STAGE_DIRECTORY);target=Path(RECEIPT['target']);run_out=stage/'run'
deadline_ns=time.monotonic_ns()+BOARD_STAGE_BUDGET_S*1_000_000_000
result={'schema':'R6_D1_BOARD_STAGE_ONCE_V1','status':'PREFLIGHT','runtime_target':str(target),
 'out':str(run_out),'stage_directory':str(stage),'automatic_retry':False,'PL_reload':False,
 'extra_MMIO_access':False,'capture_time_verified':False,'physical_output_permitted':False,
 'bindings':BINDINGS,'commands':[],'posthealth_run':False,'archive_created':False,'automatic_fetch':False}
if OPERATOR_ATTESTATION is not None:
 result.update(mode='SUPERVISED_SINGLE_MOTOR_BENCH',operator_bench_permission=True,
  production_output_permitted=False,VPLUS_procedure='ON_USER_CONFIRMED_UNDER_EXTERNAL_HUMAN_SUPERVISION',
  actual_vibration='NOT_VERIFIED',actual_vibration_requirement='REQUIRES_OPERATOR_OBSERVATION')
if MANUAL_SINGLE_MOTOR:
 result.update(mode='MANUAL_SINGLE_MOTOR_BENCH',requested_data_source='MANUAL_OPERATOR_TEST',
  SDK_requested=False,Gemini_live_chain='NOT_TESTED_BY_MANUAL_COMMAND')
if MATRIX_MODE:
 result.update(mode='MANUAL_MATRIX_BENCH' if MANUAL_MATRIX else
  ('SUPERVISED_MATRIX_BENCH' if SUPERVISED_MATRIX else 'DIGITAL_MATRIX_BENCH_MODE'),
  wire_protocol='DGM2',expected_design_id=DESIGN_ID,matrix_stage_cells=MATRIX_CELL_LIMIT,
  output_enabled_block_mask=OUTPUT_MASK,level_cap=LEVEL_CAP,
  requested_data_source='MANUAL_OPERATOR_TEST' if MANUAL_MATRIX else 'LIVE_NATIVE_PIPE')
 if DURATION_S==600:result.update(extended_session_s=600,runtime_unbounded=False,
  requested_data_source='LIVE_NATIVE_PIPE_SINGLE_CONTINUOUS_SDK_SESSION')
 if MANUAL_MATRIX:result.update(SDK_requested=False,Gemini_live_chain='NOT_TESTED_BY_MANUAL_COMMAND')
stage_admission_fd=None
def budget(needed_s=0):
 remaining=(deadline_ns-time.monotonic_ns())/1e9
 require(remaining>=needed_s,'STAGE_DEADLINE_INSUFFICIENT_NO_NEXT_COMMAND')
 return remaining
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write_new(path,obj):
 with path.open('x') as stream:json.dump(obj,stream,indent=2)
def linux_command(argv):
 budget(5);require(argv==['dmesg','--level=err,warn','--notime'],'ONLY_READONLY_KERNEL_COMMAND_ALLOWED')
 item={'argv':argv,'started_monotonic_ns':time.monotonic_ns(),'returned':False};result['commands'].append(item)
 try:
  p=subprocess.run(argv,capture_output=True,text=True,timeout=5)
 except subprocess.TimeoutExpired as exc:
  item.update(timeout=True,error=repr(exc));raise UnknownOutcome('LINUX_COMMAND_TIMEOUT_STOP')
 finally:item['ended_monotonic_ns']=time.monotonic_ns()
 item.update(returned=True,returncode=p.returncode,stdout=p.stdout,stderr=p.stderr)
 require(p.returncode==0,'KERNEL_COMMAND_RETURNED_FAILURE')
 return p.stdout
def health(label):
 boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
 fw=Path(PROFILE['dt_firmware_property']).read_bytes().rstrip(b'\0').decode('ascii')
 require(fw==FIRMWARE,'D1_FIRMWARE_NOT_ACTIVE')
 require(Path('/sys/class/fpga_manager/fpga0/state').read_text().strip()=='operating','FPGA_MANAGER_NOT_OPERATING')
 require(digest(Path(PROFILE['installed_bitstream_path']))==PROFILE['installed_bitstream_sha256'],'INSTALLED_D1_BIN_SHA_MISMATCH')
 kernel=linux_command(['dmesg','--level=err,warn','--notime'])
 (stage/('kernel_'+label+'.log')).write_text(kernel)
 info={'boot_id':boot,'firmware':fw,'manager':'operating','installed_bitstream_sha256':PROFILE['installed_bitstream_sha256'],
       'euid':os.geteuid(),'machine':platform.machine(),'sampled_monotonic_ns':time.monotonic_ns()}
 write_new(stage/('LINUX_'+label.upper()+'_HEALTH.json'),info)
 return info,kernel
def read_regular_json(path):
 require(path.resolve()==path and not path.is_symlink() and path.is_file(),'PRIOR_EVIDENCE_REGULAR_FILE_REQUIRED:'+str(path))
 return json.loads(path.read_text())

def read_restart_records(attempt):
 global stage_admission_fd
 require(attempt.is_file() and not attempt.is_symlink(),'PRIOR_RECEIPT_ATTEMPT_REQUIRED_FOR_MANUAL_RESTART')
 # Keep the original receipt marker unchanged and lock it for this admission.
 stage_admission_fd=os.open(attempt,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 require(stat.S_ISREG(os.fstat(stage_admission_fd).st_mode),'ATTEMPT_MARKER_NOT_REGULAR')
 try:fcntl.flock(stage_admission_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except OSError as exc:raise ValueError('ANOTHER_STAGE_ADMISSION_ACTIVE_NO_RESTART') from exc
 markers=[attempt]+sorted(attempt.parent.glob(attempt.stem+'_stage_*.json'))
 records=[]
 for marker_path in markers:
  marker=read_regular_json(marker_path);prior_stage=Path(marker['stage_directory'])
  require(prior_stage.is_absolute() and prior_stage.parent==Path('/home/ubuntu') and
          prior_stage.name.startswith('kv260_r6_d1_stage_') and prior_stage.resolve()==prior_stage and
          not prior_stage.is_symlink() and prior_stage.is_dir(),'PRIOR_STAGE_PATH_REQUIRED')
  prior_run=prior_stage/'run'
  prior=read_regular_json(prior_stage/'STAGE_RESULT.json')
  parent=read_regular_json(prior_run/'SUPERVISOR_RESULT.json');worker=read_regular_json(prior_run/'WORKER_RESULT.json')
  stop=(prior_run/'STOP_MMIO').exists();code=prior.get('runtime_returncode')
  require(type(code) is int and not outcome_unknown(code,parent,worker,stop),'PRIOR_RUNTIME_UNKNOWN_NO_CURRENT_HEALTH_COMMAND')
  records.append((marker,prior,parent,worker,read_regular_json(prior_stage/'LINUX_BEFORE_HEALTH.json'),
                  read_regular_json(prior_stage/'LINUX_AFTER_HEALTH.json'),stop,str(prior_stage)))
 return records

def claim_manual_restart(attempt,current,kernel,records):
 require(not critical_kernel_lines(kernel),'CURRENT_KERNEL_LOCKUP_OR_FATAL_ERROR_NO_RESTART')
 prior_stages=[]
 for marker,prior,parent,worker,before,after,stop,prior_stage in records:
  validate_returned_restart(marker,prior,parent,worker,before,after,current,BINDINGS['receipt_sha256'],stop)
  prior_stages.append(prior_stage)
 # No create/write here: the existing runtime owns this lock for the whole worker.
 ledger_fd=os.open(WORKER_LEDGER_PATH,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
 try:
  require(stat.S_ISREG(os.fstat(ledger_fd).st_mode),'GLOBAL_WORKER_LEDGER_NOT_REGULAR')
  try:fcntl.flock(ledger_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except OSError as exc:raise ValueError('GLOBAL_D1_WORKER_STILL_ACTIVE_NO_RESTART') from exc
  fcntl.flock(ledger_fd,fcntl.LOCK_UN)
 finally:os.close(ledger_fd)
 fresh=attempt.with_name(attempt.stem+'_stage_'+hashlib.sha256(str(stage).encode()).hexdigest()[:16]+'.json')
 write_new(fresh,{'schema':'D1_EXPLICIT_RETURNED_RESTART_V1','receipt_sha256':BINDINGS['receipt_sha256'],
   'stage_directory':str(stage),'boot_id':current['boot_id'],'verified_prior_stages':prior_stages,'automatic_retry':False})
 result['manual_returned_restart']={'attempt_marker':str(fresh),'original_marker_preserved':str(attempt),
   'verified_prior_stages':prior_stages,'global_worker_lock_observed_free':True,'automatic_retry':False}

def finish_unknown(reason):
 result.update(status='STOP_UNKNOWN_OUTCOME_NO_POSTHEALTH_NO_ARCHIVE',required_action='STOP_ALL_BOARD_COMMANDS_NO_RETRY_NO_POSTHEALTH_NO_FETCH',
               error=reason,MMIO_unknown=True,posthealth_skipped=True,archive_skipped=True)
 # File-only stop latch; never a register/Manager command, signal or retry.
 if run_out.is_dir() and not (run_out/'STOP_MMIO').exists():
  with (run_out/'STOP_MMIO').open('x') as stream:stream.write('OUTER_STAGE_UNKNOWN_STOP_NO_FURTHER_MMIO\n')
try:
 require(os.geteuid()==0 and platform.machine()=='aarch64','EXISTING_ROOT_AARCH64_REQUIRED')
 require(not stage.exists() and not stage.is_symlink(),'NEW_STAGE_DIRECTORY_REQUIRED')
 stage.mkdir(mode=0o700)
 require(not target.is_symlink() and target.resolve()==target,'RUNTIME_TARGET_SYMLINK')
 saved=target/'RECEIPT.json';require(saved.is_file() and not saved.is_symlink() and json.loads(saved.read_text())==RECEIPT,'BOARD_RECEIPT_MISMATCH')
 for rel,row in RECEIPT['files'].items():
  p=target/rel;require(not p.is_symlink() and p.resolve()==p and p.is_file() and p.stat().st_size==row['bytes'] and digest(p)==row['sha256'],'BOARD_RUNTIME_SHA_MISMATCH:'+rel)
 require(digest(Path(PROFILE['native_helper_path']))==CONTROL_HELPER_SHA,'CONTROL_HELPER_SHA_MISMATCH')
 profile_path=stage/'profile_D1.json';write_new(profile_path,PROFILE)
 proof_path=None
 if OPERATOR_ATTESTATION is not None:
  proof_path=str(stage/'OPERATOR_ATTESTATION.json')
  write_new(Path(proof_path),OPERATOR_ATTESTATION)
  result['operator_attestation_file']={'path':proof_path,'sha256':digest(Path(proof_path))}
 command=exact_command(LATEST,str(target),str(profile_path),str(run_out),DURATION_S,SOURCE_INDEX,RENEW_DIGITAL_WINDOW,
  proof_path,MANUAL_SINGLE_MOTOR,MATRIX_CELL_LIMIT,OUTPUT_MASK,LEVEL_CAP,SUPERVISED_MATRIX,MANUAL_MATRIX)
 result['command']=command;write_new(stage/'EXACT_RUNTIME_COMMAND.json',{'argv':command,'parent_budget_s':DURATION_S+55,'outer_wait_s':RUNTIME_WAIT_S})
 # Default remains once per receipt. Explicit manual admission needs all prior
 # stages known returned; no missing/unknown/outstanding predecessor qualifies.
 attempt=Path('/home/ubuntu')/('kv260_r6_D1_runtime_attempt_'+RECEIPT['target'].rsplit('_',1)[1]+'.json')
 # Read prior outcomes before any current Linux command. Unknown/missing/
 # outstanding predecessors stop here without a new kernel command or child.
 restart_records=read_restart_records(attempt) if ALLOW_RETURNED_RESTART else None
 before,kernel_before=health('before');result['prehealth']=before
 if OPERATOR_ATTESTATION is not None:
  require(OPERATOR_ATTESTATION['boot_id']==before['boot_id'],'OPERATOR_ATTESTATION_NOT_CURRENT_BOOT_NO_RUNTIME')
 budget(RUNTIME_WAIT_S+5)
 if ALLOW_RETURNED_RESTART:claim_manual_restart(attempt,before,kernel_before,restart_records)
 else:write_new(attempt,{'receipt_sha256':BINDINGS['receipt_sha256'],'stage_directory':str(stage),'automatic_retry':False})
 result['runtime_started_monotonic_ns']=time.monotonic_ns()
 with (stage/'runtime_parent.stdout.log').open('x') as stdout,(stage/'runtime_parent.stderr.log').open('x') as stderr:
  child=subprocess.Popen(command,cwd=target,stdout=stdout,stderr=stderr,start_new_session=True)
  result['runtime_parent_pid']=child.pid
  try:code=child.wait(timeout=RUNTIME_WAIT_S)
  except subprocess.TimeoutExpired:
   result['runtime_returncode']=None;raise UnknownOutcome('OUTER_RUNTIME_WAIT_TIMEOUT_NO_SIGNAL_NO_RETRY')
 result['runtime_returncode']=code;result['runtime_returned_monotonic_ns']=time.monotonic_ns()
 parent_path=run_out/'SUPERVISOR_RESULT.json';worker_path=run_out/'WORKER_RESULT.json'
 try:
  parent=json.loads(parent_path.read_text());worker=json.loads(worker_path.read_text())
 except (OSError,ValueError) as exc:raise UnknownOutcome('RUNTIME_RESULT_MISSING_OR_INVALID:'+repr(exc))
 if outcome_unknown(code,parent,worker,(run_out/'STOP_MMIO').exists()):raise UnknownOutcome('INNER_RUNTIME_TIMEOUT_OR_MMIO_UNKNOWN')
 result['runtime_parent_summary']=parent
 if MANUAL_SINGLE_MOTOR or MANUAL_MATRIX:result['SDK_started']=worker.get('SDK_started','UNKNOWN')
 result['runtime_worker_summary']={k:worker.get(k) for k in ('status','transactions','errors','MMIO_unknown','SDK_cleanup','SDK_warning_counts','elapsed_ns',
  'FULL_OFF_readback','motor_revoke_monotonic_ns','motor_requests_disarmed_monotonic_ns','motor_full_off_verified_monotonic_ns')}
 if (run_out/'SELECTED_TARGET.json').is_file():result['selected_target']=json.loads((run_out/'SELECTED_TARGET.json').read_text())
 after,kernel_after=health('after');result['posthealth_run']=True;result['posthealth']=after
 require(after['boot_id']==before['boot_id'],'BOOT_CHANGED_DURING_D1_RUNTIME')
 old=kernel_before.splitlines();new=kernel_after.splitlines();require(new[:len(old)]==old,'KERNEL_HISTORY_CHANGED')
 added=new[len(old):];unexpected=[line for line in added if not line.startswith('OF: overlay: WARNING: memory leak will occur if overlay removed, property:')]
 result['kernel_new_lines']=added;result['kernel_new_unexpected']=unexpected
 result['status']='PASS_D1_DIGITAL_RUNTIME_RETURNED' if code==0 and worker.get('status')=='PASS_DIGITAL_ONLY_NOT_PHYSICAL' and not unexpected else 'RETURNED_FAILED_OR_PARTIAL_REVIEW_EVIDENCE'
 if OPERATOR_ATTESTATION is not None and result['status']=='PASS_D1_DIGITAL_RUNTIME_RETURNED':
  result['status']='PASS_D1_SUPERVISED_DIGITAL_EVIDENCE_RETURNED_ACTUAL_VIBRATION_NOT_VERIFIED'
 if MANUAL_SINGLE_MOTOR and result['status']=='PASS_D1_SUPERVISED_DIGITAL_EVIDENCE_RETURNED_ACTUAL_VIBRATION_NOT_VERIFIED':
  result['status']='PASS_D1_MANUAL_SINGLE_DIGITAL_EVIDENCE_RETURNED_ACTUAL_VIBRATION_NOT_VERIFIED'
 if MATRIX_MODE and result['status'] in ('PASS_D1_DIGITAL_RUNTIME_RETURNED',
  'PASS_D1_SUPERVISED_DIGITAL_EVIDENCE_RETURNED_ACTUAL_VIBRATION_NOT_VERIFIED'):
  result['status']='PASS_DGM2_MATRIX_DIGITAL_EVIDENCE_RETURNED_ACTUAL_VIBRATION_NOT_VERIFIED'
 write_new(stage/'PRE_ARCHIVE_SUMMARY.json',result)
 entries=[]
 for p in sorted(stage.rglob('*')):
  budget(2)
  require(not p.is_symlink(),'EVIDENCE_SYMLINK_REFUSED')
  if p.is_file():entries.append({'path':p.relative_to(stage).as_posix(),'bytes':p.stat().st_size,'sha256':digest(p)})
 write_new(stage/'FILES_SHA256.json',{'files':entries,'note':'manifest excludes itself; final archive hash is returned separately'})
 archive=stage.with_suffix('.tgz');require(not archive.exists(),'NEW_ARCHIVE_REQUIRED')
 archive_begin=time.monotonic_ns()
 with tarfile.open(archive,'w:gz',compresslevel=1) as tar:
  for p in sorted(stage.rglob('*')):
   budget(1)
   if p.is_file():tar.add(p,arcname=p.relative_to(stage).as_posix(),recursive=False)
 result.update(archive_created=True,archive={'path':str(archive),'bytes':archive.stat().st_size,'sha256':digest(archive),
  'elapsed_ns':time.monotonic_ns()-archive_begin,'raw_files':sum(p.name.startswith('sample_') and p.suffix=='.y16' for p in stage.rglob('*')),
  'file_count':len(entries)+1,'uncompressed_bytes':sum(e['bytes'] for e in entries),
  'existing_fetch_script_compatible':0<archive.stat().st_size<32*1024*1024})
 result['fetch_instruction']='Use scripts/fetch_archive.py separately after reviewing this known returned result; never auto-fetch.'
except UnknownOutcome as exc:finish_unknown(str(exc))
except BaseException as exc:
 result.update(status='STAGE_FAILED_NO_RETRY',error=repr(exc),required_action='STOP_REVIEW_NO_AUTOMATIC_RETRY_OR_FETCH')
if stage_admission_fd is not None:os.close(stage_admission_fd)
result['ended_monotonic_ns']=time.monotonic_ns();result['ended_wall_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
if stage.is_dir():
 try:write_new(stage/'STAGE_RESULT.json',result)
 except BaseException as exc:result['result_save_error']=repr(exc)
print('FAST_RESULT '+json.dumps(result),flush=True)
'''


def make_source(latest,manifest,receipt,bin_sha,stage_directory,bindings,duration_s=8,source_index=None,
                renew_digital_window=False,allow_returned_restart=False,worker_ledger_path=None,
                operator_attestation=None,manual_single_motor=False,matrix_cell_limit=None,
                output_mask=None,level_cap=None,supervised_matrix=False,manual_matrix=False):
    validate_receipt(latest,manifest,receipt,duration_s,source_index,renew_digital_window)
    require(type(allow_returned_restart) is bool,'EXPLICIT_RETURNED_RESTART_BOOLEAN_REQUIRED')
    if allow_returned_restart:
        require(worker_ledger_path=='/home/ubuntu/kv260_r6_d1_session_ledger.jsonl','REVIEWED_GLOBAL_WORKER_LOCK_REQUIRED')
    p=PurePosixPath(stage_directory);target=PurePosixPath(receipt['target'])
    require(isinstance(stage_directory,str) and re.fullmatch('/home/ubuntu/kv260_r6_d1_stage_[A-Za-z0-9_]+',stage_directory),
            'ISOLATED_NEW_STAGE_ABSOLUTE_PATH_REQUIRED')
    require(not p.is_relative_to(target) and not target.is_relative_to(p),'PROFILE_OUTPUT_MUST_BE_OUTSIDE_HASHED_RUNTIME_TREE')
    profile=make_profile(bin_sha,receipt['target'])
    proof_path=None
    if operator_attestation is not None:
        require(isinstance(operator_attestation,dict),'OPERATOR_ATTESTATION_OBJECT_REQUIRED')
        if DESIGN_ID==0x52364d32:
            require(operator_attestation.get('schema')=='R6_SUPERVISED_MATRIX_BENCH_ATTESTATION_V4' and
                    operator_attestation.get('output_directory')==stage_directory+'/run' and
                    operator_attestation.get('duration_s')==duration_s and
                    isinstance(operator_attestation.get('boot_id'),str) and
                    operator_attestation.get('test_kind')==('MANUAL_MATRIX' if manual_matrix else
                        ('LIVE_MATRIX_CONTINUOUS' if duration_s==600 else 'LIVE_MATRIX')) and
                    operator_attestation.get('matrix_stage_cells')==matrix_cell_limit and
                    operator_attestation.get('output_enabled_block_mask')==output_mask and
                    operator_attestation.get('level_cap')==level_cap and 'source_index' not in operator_attestation,
                    'MATRIX_V4_ATTESTATION_STAGE_BINDING_MISMATCH')
            if duration_s==600:
                require(operator_attestation.get('prior_actual_vibration_observed') is True and
                        operator_attestation.get('prior_verified_stage_cells')==128 and
                        isinstance(operator_attestation.get('prior_observation_evidence'),str) and
                        bool(operator_attestation['prior_observation_evidence'].strip()),
                        'EXTENDED600_STAGE_REQUIRES_ACTUAL_PRIOR128_OBSERVATION')
        else:
            capability='supports_manual_single_motor' if manual_single_motor else 'supports_supervised_single_motor'
            require(all(manifest.get(k)==latest.get(k) for k in (capability,'supervised_source_index','supervised_level_cap')),
                    'SUPERVISED_LATEST_MANIFEST_CAPABILITY_MISMATCH')
            require(operator_attestation.get('output_directory')==stage_directory+'/run' and
                    operator_attestation.get('source_index')==source_index and
                    operator_attestation.get('duration_s')==duration_s and
                    isinstance(operator_attestation.get('boot_id'),str),
                    'OPERATOR_ATTESTATION_STAGE_BINDING_MISMATCH')
            if not manual_single_motor and source_index!=131:
                require(operator_attestation.get('schema')=='R6_SUPERVISED_SELECTED_LIVE_TARGET_ATTESTATION_V3',
                        'SELECTED_LIVE_TARGET_REQUIRES_V3_OPERATOR_ATTESTATION')
                require(all(manifest.get(k)==latest.get(k) for k in
                            ('supports_supervised_target_selection','supervised_allowed_target_mask')),
                        'SUPERVISED_TARGET_SELECTION_LATEST_MANIFEST_MISMATCH')
        proof_path=stage_directory+'/OPERATOR_ATTESTATION.json'
    exact_command(latest,receipt['target'],stage_directory+'/profile_D1.json',stage_directory+'/run',duration_s,source_index,renew_digital_window,
                  proof_path,manual_single_motor,matrix_cell_limit,output_mask,level_cap,supervised_matrix,manual_matrix)
    budgets=stage_budgets(duration_s)
    constants={name:globals()[name] for name in ('APP','STEM','FIRMWARE','DESIGN_ID','CONTROL_HELPER_SHA',
                                               'CONTINUOUS_NATIVE_REL','CONTINUOUS_NATIVE_SHA')}
    constants.update(BOARD_STAGE_BUDGET_S=budgets['stage_s'],RUNTIME_WAIT_S=budgets['runtime_wait_s'],DURATION_S=duration_s,SOURCE_INDEX=source_index,
                     RENEW_DIGITAL_WINDOW=renew_digital_window,ALLOW_RETURNED_RESTART=allow_returned_restart,WORKER_LEDGER_PATH=worker_ledger_path,
                     OPERATOR_ATTESTATION=operator_attestation,MANUAL_SINGLE_MOTOR=manual_single_motor,
                     MATRIX_MODE=DESIGN_ID==0x52364d32,MATRIX_CELL_LIMIT=matrix_cell_limit,OUTPUT_MASK=output_mask,
                     LEVEL_CAP=level_cap,SUPERVISED_MATRIX=supervised_matrix,MANUAL_MATRIX=manual_matrix)
    source=''.join(k+'='+repr(v)+'\n' for k,v in constants.items())
    source+=''.join(inspect.getsource(f)+'\n' for f in (require,stage_budgets,approved_options,matrix_options,exact_command,outcome_unknown,critical_kernel_lines,validate_returned_restart))
    source+='LATEST='+repr(latest)+'\nRECEIPT='+repr(receipt)+'\nPROFILE='+repr(profile)+'\nSTAGE_DIRECTORY='+repr(stage_directory)+'\nBINDINGS='+repr(bindings)+'\n'+BOARD_SOURCE
    compile(source,'<D1_single_runtime_stage>','exec');return source


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);root=Path(__file__).resolve().parents[1]
    parser.add_argument('--receipt',type=Path,required=True)
    parser.add_argument('--duration-s',type=int,choices=(8,60),default=8)
    parser.add_argument('--extended-session-s',type=int,choices=(600,),help='Explicit bounded600s live128 matrix session; requires dedicated continuous ELF and V4 actual-prior128 proof')
    parser.add_argument('--source-index',type=int)
    parser.add_argument('--renew-digital-window',action='store_true',help='Explicit reviewed 30s producer-epoch renewal for the 60s digital experiment')
    parser.add_argument('--allow-returned-restart',action='store_true',help='Manual admission after all prior stages returned in this healthy boot; never automatic retry')
    parser.add_argument('--operator-attestation',type=Path,help='Explicit supervised eight-second single-motor proof; removes V+ disconnected flag; never proves actual vibration')
    parser.add_argument('--manual-single-motor',action='store_true',help='Explicit standalone manual one-motor command; no SDK; requires operator attestation; never a Gemini live result')
    parser.add_argument('--matrix-mode',action='store_true',help='Explicit R6M2/DGM2 design selection; original D1 remains the default')
    parser.add_argument('--matrix-cell-limit',type=int,choices=(8,32,64,128))
    parser.add_argument('--output-mask',type=lambda value:int(value,0))
    parser.add_argument('--level-cap',type=int,choices=(1,))
    parser.add_argument('--supervised-matrix',action='store_true',help='Explicit V4 live-matrix proof; never a production-permission assertion')
    parser.add_argument('--manual-matrix',action='store_true',help='Explicit V4 manual matrix bench; no SDK or camera-data claim')
    parser.add_argument('--runtime-package',type=Path,default=root/'runtime_night/LATEST_PACKAGE.json')
    image=parser.add_mutually_exclusive_group(required=True);image.add_argument('--profile-bin-sha');image.add_argument('--package',type=Path,help='formal D1 FPGA LATEST_PACKAGE.json or current package directory')
    parser.add_argument('--out',type=Path,default=root/'evidence/D1_BOARD_STAGE_RESULT.json')
    parser.add_argument('--remote-out',default='/home/ubuntu/kv260_r6_d1_stage_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+secrets.token_hex(4))
    mode=parser.add_mutually_exclusive_group(required=True);mode.add_argument('--run-once',action='store_true');mode.add_argument('--check-only',action='store_true')
    args=parser.parse_args(argv);configure_design(args.matrix_mode)
    if args.extended_session_s is not None:
        require(args.duration_s==8 and args.matrix_mode and args.supervised_matrix and
                not args.manual_matrix and not args.manual_single_motor and args.matrix_cell_limit==128 and
                args.source_index is None and args.renew_digital_window,
                'EXTENDED600_STAGE_ONLY_EXPLICIT_SUPERVISED128_WITH_DEFAULT_DURATION_AND_RENEWAL')
    effective_duration=args.extended_session_s if args.extended_session_s is not None else args.duration_s
    require(not args.matrix_mode or not args.package,
            'MATRIX_STAGE_USE_EXPLICIT_PROFILE_BIN_SHA_NOT_LEGACY_D1_PACKAGE_RESOLVER')
    receipt=json.loads(args.receipt.read_text(encoding='utf-8-sig'))
    latest,manifest,tree=load_runtime(args.runtime_package,receipt,effective_duration,args.source_index,args.renew_digital_window,args.allow_returned_restart)
    if args.package:
        # This module is inert on import; only local hashes/archives are read.
        from load_d1_manager_once import resolve_package
        _,fpga_manifest,_,_=resolve_package(args.package)
        require(fpga_manifest.get('app')==APP and fpga_manifest.get('USR_ACCESS')==hex(DESIGN_ID),'EXACT_FORMAL_D1_IMAGE_PACKAGE_REQUIRED')
        bin_sha=fpga_manifest['bit_bin_sha256']
    else:bin_sha=args.profile_bin_sha
    bindings={'receipt_sha256':sha_file(args.receipt),'runtime_latest_sha256':sha_file(args.runtime_package),
              'runtime_manifest_sha256':latest['manifest_sha256'],'runtime_tree':str(tree),'loaded_D1_bin_sha256':bin_sha,
              'expected_design_id':DESIGN_ID,'wire_protocol':'DGM2' if args.matrix_mode else 'DGD1'}
    lock_path=global_worker_lock_path((tree/'runtime_night/run_d1_live.py').read_text()) if args.allow_returned_restart else None
    operator_attestation=None
    if args.operator_attestation:
        require(args.operator_attestation.is_absolute() and args.operator_attestation.is_file() and
                not args.operator_attestation.is_symlink() and args.operator_attestation.stat().st_size<=16384,
                'LOCAL_OPERATOR_ATTESTATION_REGULAR_ABSOLUTE_FILE_REQUIRED')
        operator_attestation=json.loads(args.operator_attestation.read_text(encoding='utf-8-sig'))
        bindings['operator_attestation_sha256']=sha_file(args.operator_attestation)
    source=make_source(latest,manifest,receipt,bin_sha,args.remote_out,bindings,effective_duration,args.source_index,
                       args.renew_digital_window,args.allow_returned_restart,lock_path,operator_attestation,args.manual_single_motor,
                       args.matrix_cell_limit,args.output_mask,args.level_cap,args.supervised_matrix,args.manual_matrix)
    budgets=stage_budgets(effective_duration)
    if args.check_only:
        print(json.dumps({'status':'PASS_HOST_STAGE_SOURCE_ONLY','source_sha256':hashlib.sha256(source.encode()).hexdigest(),
                          'bindings':bindings,'duration_s':effective_duration,'runtime_default_duration_s':args.duration_s,
                          'extended_session_s':args.extended_session_s,'source_index':args.source_index,
                          'renew_digital_window':args.renew_digital_window,'allow_returned_restart':args.allow_returned_restart,
                          'supervised_single_motor':operator_attestation is not None,'actual_vibration':'NOT_VERIFIED',
                          'manual_single_motor':args.manual_single_motor,
                          'matrix_mode':args.matrix_mode,'matrix_stage_cells':args.matrix_cell_limit,
                          'supervised_matrix':args.supervised_matrix,'manual_matrix':args.manual_matrix,
                          'budgets':budgets,'board_access':False},indent=2));return 0
    require(not args.out.exists() and not args.out.is_symlink(),'HOST_STAGE_RESULT_EXISTS_NO_RETRY')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.with_name(args.out.name+'.started.json').open('x',encoding='utf-8') as stream:
        json.dump({'bindings':bindings,'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'automatic_retry':False},stream,indent=2)
    import board_console
    console=board_console.Console('D1_runtime_stage_'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    try:
        result=console.run(source,'one_D1_runtime_stage_no_reload',timeout=budgets['host_s'])
        with args.out.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
        print(json.dumps(result,ensure_ascii=False),flush=True)
        return 0 if result.get('status') in ('PASS_D1_DIGITAL_RUNTIME_RETURNED',
            'PASS_D1_SUPERVISED_DIGITAL_EVIDENCE_RETURNED_ACTUAL_VIBRATION_NOT_VERIFIED',
            'PASS_D1_MANUAL_SINGLE_DIGITAL_EVIDENCE_RETURNED_ACTUAL_VIBRATION_NOT_VERIFIED',
            'PASS_DGM2_MATRIX_DIGITAL_EVIDENCE_RETURNED_ACTUAL_VIBRATION_NOT_VERIFIED') else 2
    finally:console.close()

if __name__=='__main__':raise SystemExit(main())
