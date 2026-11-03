"""Append after the existing receive_ram prefix; consumes payloadbytes in RAM."""
import hashlib, io, json, re, struct, sys, types, zipfile

PROOF_SHA = 'a3cb8ac20db2edf1267cd23e0af37e36914f09d27c3c41b0f06525bb9284a6f7'

def bootstrap(payload, validate_only=False):
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('schema') != 'kv260_latest_live_ram_v1': raise RuntimeError('WRONG_RAM_BUNDLE_SCHEMA')
        names = set(archive.namelist())
        if len(names) != len(archive.namelist()): raise RuntimeError('DUPLICATE_BUNDLE_MEMBERS')
        if names != set(manifest['members']) | {'manifest.json'}: raise RuntimeError('UNEXPECTED_BUNDLE_MEMBERS')
        members = {}
        for name, spec in manifest['members'].items():
            info = archive.getinfo(name)
            if info.file_size != spec['bytes'] or info.file_size > 16*1024*1024: raise RuntimeError('RAM_MEMBER_SIZE')
            data = archive.read(name)
            if hashlib.sha256(data).hexdigest() != spec['sha256']: raise RuntimeError('RAM_MEMBER_SHA256: '+name)
            members[name] = data
    evidence = json.loads(members['scale_receipt.json'])
    proof = members['scale_proof_rx.txt']
    if hashlib.sha256(proof).hexdigest() != PROOF_SHA or evidence.get('proof_sha256') != PROOF_SHA:
        raise RuntimeError('TASK_A_RAW_PROOF_SHA256_MISMATCH')
    session = json.loads(members['scale_proof_result.json'].decode('utf-8-sig'))
    if session.get('CompletionMarkerObserved') is not True or session.get('ExitCode') != 0 or session.get('Error'):
        raise RuntimeError('TASK_A_SESSION_NOT_SUCCESSFUL')
    text = proof.decode('utf-8-sig')
    if text.splitlines().count(session['BeginMarker']) != 1 or text.splitlines().count(session['EndMarker']+':0') != 1:
        raise RuntimeError('TASK_A_SESSION_MARKERS_MISSING')
    records = []
    for line in text.splitlines():
        line = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', line)
        if line.startswith('{'):
            try: records.append(json.loads(line))
            except ValueError: pass
    responses = {r['command']:bytes.fromhex(r['raw_response_hex']) for r in records if r.get('stage') == 'raw_response'}
    for name, opcode in (('GVD', 0x10), ('UAMG', 0x30), ('GET_ADV_DEPTH_TABLE_CURRENT', 0x2c)):
        raw = responses[name]
        if struct.unpack_from('<I', raw)[0] != opcode or hashlib.sha256(raw).hexdigest() != evidence['raw_response_sha256'][name]:
            raise RuntimeError('TASK_A_RESPONSE_OPCODE_OR_SHA256: '+name)
    gvd = responses['GVD']
    firmware = '.'.join(str(v) for v in gvd[16:20][::-1])
    if firmware != evidence['firmware'] or gvd[68:74].hex() != evidence['serial'] or gvd[52:58].hex() != evidence['sdk_camera_serial']:
        raise RuntimeError('TASK_A_RAW_FIRMWARE_ASIC_OR_OPTICAL_IDENTITY')
    if responses['UAMG'][4] != 1 or evidence['advanced_mode_enabled'] is not True:
        raise RuntimeError('TASK_A_ADVANCED_MODE_NOT_PROVEN')
    raw = responses['GET_ADV_DEPTH_TABLE_CURRENT']
    if struct.unpack_from('<I', raw, len(raw)-4)[0] != 20 or raw[4:24].hex() != evidence['depth_payload_hex']:
        raise RuntimeError('TASK_A_CURRENT_DEPTH_TABLE_SIZE_OR_BYTES')
    if struct.unpack_from('<I', raw, 4)[0] != evidence['depth_units_micrometers']:
        raise RuntimeError('TASK_A_DEPTH_UNITS_MISMATCH')
    if validate_only:
        return manifest, evidence
    modules = {}
    for name in ('latest_slot', 'quality_bridge', 'kpf1_codec', 'v4l2_support', 'diagnostic_monitor', 'latest_live'):
        module = types.ModuleType(name); module.__file__ = '<RAM:'+name+'.py>'
        sys.modules[name] = module
        exec(compile(members[name+'.py'], module.__file__, 'exec'), module.__dict__)
        modules[name] = module
    # checked_scale() executes before camera open or the metric core process.
    return modules['latest_live'].run_live(members['core.elf'], manifest['members']['core.elf']['sha256'],
        members['core_config.txt'], evidence, seconds=manifest['seconds'],monitor_on=manifest.get('monitor_on',False))

if 'payloadbytes' in globals():
    try:
        result = bootstrap(payloadbytes)
        raise SystemExit(0 if result['status'] == 'COMPLETED' else 3)
    except Exception as exc:
        print('\nKV260_LIVE_BOOTSTRAP_ERROR '+repr(exc), flush=True)
        raise SystemExit(3)
