"""Explicit eight-second manual D1 single-motor bench; NEVER camera evidence.

Only sealed D1 transport/scheduler and existing ordered FULL_OFF are reused.
No SDK/camera access or production freshness permission is provided.
"""
from pathlib import Path
import argparse,dataclasses,json,mmap,os,platform,signal,subprocess,sys,time
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from runtime_night import run_d1_live as r
from runtime_night import supervised_single as b


def parser():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('profile','out','gpio-helper','operator-attestation'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--duration-s',type=int,choices=[8],default=8)
    p.add_argument('--source-index',type=int,choices=[131],default=131)
    p.add_argument('--run-once',action='store_true')
    modes=p.add_mutually_exclusive_group()
    modes.add_argument('--manual-single-motor',action='store_true')
    modes.add_argument('--manual-matrix',action='store_true',help='Explicit newR6M2 matrix bench, not camera data')
    p.add_argument('--matrix-mode',action='store_true',help='Opt-in new R6M2 image only')
    p.add_argument('--matrix-cell-limit',type=int,choices=[8,32,64,128],default=8)
    p.add_argument('--output-mask',type=lambda x:int(x,0),default=0xf0f0)
    p.add_argument('--level-cap',type=int,choices=[1],default=1)
    p.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    return p


def proof(args,consume=False):
    if getattr(args,'matrix_mode',False):
        return r.matrix_module().load_attestation(args.operator_attestation,out=args.out,duration_s=8,
            stage_cells=args.matrix_cell_limit,output_mask=args.output_mask,level_cap=args.level_cap,
            test_kind='MANUAL_MATRIX',boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),consume=consume)
    return b.load_attestation(args.operator_attestation,out=args.out,duration_s=8,
        source_index=131,boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),consume=consume)


def worker(args):
    r.require(sys.platform=='linux' and platform.machine().lower()=='aarch64' and os.geteuid()==0,
              'EXISTING_ROOT_LINUX_AARCH64_REQUIRED')
    sys.path[:0]=[str(ROOT),str(ROOT/'iic_c1')]
    from runtime_control.uio import EnvironmentProfile,inspect_environment,open_confirmed_transport
    from runtime_control.digital_backend import LinuxDigitalBackend
    from digital_iic_diag import Native32
    operator=proof(args,consume=True)
    matrix_mode=getattr(args,'matrix_mode',False)
    bench=r.matrix_module() if matrix_mode else b
    expected_id=r.MATRIX_DESIGN_ID if matrix_mode else r.DESIGN_ID
    if matrix_mode:
        from runtime_control.digital_matrix_backend import LinuxDigitalMatrixBackend
    log=r.Journal(args.out);began=time.monotonic_ns()
    result={'schema':'R6_MANUAL_SINGLE_MOTOR_BENCH_V1','mode':'MANUAL_SINGLE_MOTOR_BENCH',
        'source_kind':'MANUAL_OPERATOR_TEST','actual_live':False,'FRESH':False,'capture_time_verified':False,
        'capture_timestamp':None,'raw_sha256':None,'G6C1_packet_sha256':None,'sdk_frame_index':None,
        'physical_output_permitted':False,'production_output_permitted':False,'production_ready':False,
        'operator_bench_permission':True,'operator_attestation':operator.as_record(),
        'VPLUS_actual_state':'ON_USER_CONFIRMED','requested_duration_s':8,'requested_source_index':131,
        'commanded_level':1,'SDK_started':False,'SDK_cleanup':'NOT_STARTED_MANUAL_BENCH','SDK_warning_counts':{},
        'actual_vibration':'NOT_VERIFIED_REQUIRES_OPERATOR_OBSERVATION','transactions':0,'errors':[],
        'MMIO_unknown':False,'hardware_access_scope':'D1_PL_SCHEDULER_AND_EXISTING_FULL_OFF;NO_CAMERA'}
    if matrix_mode:
        result.update(schema='R6_MANUAL_MATRIX_BENCH_V1',mode='MANUAL_MATRIX_BENCH',
            requested_source_index=None,matrix_stage_cells=args.matrix_cell_limit,
            output_enabled_block_mask=args.output_mask,expected_design_id=expected_id)
    transport=backend=rawio=memory=gpio=None;fd=None;native=None
    try:
        result['package']=r.verify_package(ROOT)
        manifest=json.loads((ROOT/'PACKAGE_MANIFEST.json').read_text())
        if matrix_mode:
            r.require(manifest.get('supports_digital_matrix_mode') is True and manifest.get('supports_manual_matrix') is True
                and manifest.get('matrix_design_id')==r.MATRIX_DESIGN_ID
                and manifest.get('matrix_allowed_target_mask')==0xf0f0
                and manifest.get('matrix_supported_stage_cells')==[8,32,64,128]
                and manifest.get('matrix_level_cap')==1,'EXACT_MANUAL_MATRIX_PACKAGE_REQUIRED')
        else:
            r.require(manifest.get('supports_manual_single_motor') is True
                and manifest.get('supervised_source_index')==131 and manifest.get('supervised_level_cap')==1,
                'EXACT_MANUAL_SINGLE_MOTOR_PACKAGE_REQUIRED')
        raw=json.loads(args.profile.read_text())
        profile=EnvironmentProfile(**{f.name:raw[f.name] for f in dataclasses.fields(EnvironmentProfile)})
        r.require(profile.expected_design_id==expected_id,'EXACT_MANUAL_MODE_PROFILE_ID_REQUIRED_BEFORE_MMIO')
        env=inspect_environment(profile);r.require(r.sha(args.gpio_helper)==r.GPIO_SHA,'GPIO_HELPER_SHA_MISMATCH')
        clock=next((s for s in Path('/sys/kernel/debug/clk/clk_summary').read_text().splitlines() if s.split() and s.split()[0]=='pl0_ref'),None)
        r.require(clock and 90_000_000<=int(clock.split()[4])<=110_000_000,'PL0_CLOCK_NOT_90_110MHZ')
        result.update(environment=env,clock=clock)
        transport=log.call('CONTROL_TRANSPORT_OPEN',open_confirmed_transport,profile)
        r.require_no_protocol_fault(log.call('MANUAL_PRE_STATUS_LDR',transport.read32,4))
        active=log.call('MANUAL_ACTIVE_SESSION_LDR',transport.read32,0x10)
        with r.open_session_ledger() as ledger:
            reserved=ledger.reserve(active,Path('/proc/sys/kernel/random/boot_id').read_text().strip(),args.out)
            session=reserved['reserved_session'];result['session_reservation']={'ledger_path':str(ledger.path),**reserved}
            log.event('MANUAL_SESSION_RESERVED_DURABLE',**result['session_reservation'])
            fd=os.open(env['devices']['gpio'],os.O_RDWR|os.O_SYNC)
            memory=mmap.mmap(fd,0x10000,flags=mmap.MAP_SHARED,prot=mmap.PROT_READ|mmap.PROT_WRITE)
            rawio=Native32(memory,args.gpio_helper);native=r.LoggedNative(rawio,log);gpio=r.D1Gpio(native,log,expected_id=expected_id)
            window_start,baseline=r.acquire_digital_window(gpio,transport,log)
            result.update(initial_status=baseline,initial_window_started_ns=window_start)
            backend=(LinuxDigitalMatrixBackend(profile,target_mask=0xf0f0,emit=log.backend,timeout_ns=r.D1_TRANSACTION_BUDGET_NS) if matrix_mode else LinuxDigitalBackend(profile,emit=log.backend,timeout_ns=r.D1_TRANSACTION_BUDGET_NS))
            deadline=time.monotonic()+8;sequence=0;previous=None
            result['command_loop_begin_monotonic_ns']=time.monotonic_ns()
            with (args.out/'transactions.jsonl').open('x',encoding='utf8') as records:
                while time.monotonic()<deadline:
                    log.gate();r.require_health(r.current_status(transport,log),baseline)
                    sequence+=1;frame=bench.manual_frame(operator,session=session,sequence=sequence,now_ns=time.monotonic_ns())
                    log.event('MANUAL_COMMAND_PREPARED_NOT_YET_SUBMITTED',record=frame.as_record())
                    gpio.page(0);log.gate();log.pending=True;log.event('MANUAL_BACKEND_SUBMIT_BEGIN',sequence=sequence)
                    submitted=bench.submit_manual(backend,frame);log.pending=False
                    log.event('MANUAL_BACKEND_SUBMIT_RETURNED',sequence=sequence)
                    r.require(submitted['packet_read_words']==96 and submitted['haptic_active_read_words']==64,
                              'MANUAL_PACKET_OR_ACTUAL_BUFFER_READBACK_INCOMPLETE')
                    result['transactions']+=1
                    projection=frame.packet[96:352];refresh=r.pwm_refresh_required(previous,projection)
                    end=min(deadline,time.monotonic()+.7);seen=[];progress=None
                    while time.monotonic()<end:
                        status=r.current_status(transport,log);r.require_health(status,baseline);seen.append(status)
                        progress=r.scheduler_progress(submitted['diagnostics_before'],status,refresh_required=refresh)
                        if progress['accepted']:break
                        time.sleep(.003)
                    records.write(json.dumps({'source':frame.as_record(),'submit_readback':submitted,
                        'scheduler_observations':seen,'scheduler_acceptance':progress,
                        'target_PWM_readback':'NOT_RUN_WHILE_SCHEDULER_OWNS_BUS',
                        'actual_vibration':'NOT_VERIFIED_REQUIRES_OPERATOR_OBSERVATION'})+'\n');records.flush()
                    # The final command may cross the requested deadline; stop,
                    # rather than prolong the loop for another scheduler poll.
                    if time.monotonic()>=deadline:break
                    r.require(progress and progress['accepted'],'MANUAL_SCHEDULER_NO_COMPLETION')
                    previous=projection;time.sleep(.05)
            result['command_loop_end_monotonic_ns']=time.monotonic_ns()
            r.supervised_revoke_before_sdk_wait(operator,result,transport,native,gpio,log)
            r.require(result['transactions']>0,'NO_MANUAL_D1_TRANSACTION')
    except BaseException as exc:
        result['errors'].append(type(exc).__name__+':'+str(exc));log.event('MANUAL_SESSION_ERROR',error=result['errors'][-1])
    finally:
        uncertain=log.pending or log.failed or log.stop.exists();result['MMIO_unknown']=uncertain
        if gpio is not None and gpio.owned and not uncertain:
            try:r.supervised_revoke_before_sdk_wait(operator,result,transport,native,gpio,log)
            except BaseException as exc:
                result['errors'].append('ORDERED_REVOKE:'+str(exc));log.event('ORDERED_REVOKE_FAILED',error=str(exc))
                if gpio.owned and not log.failed and not log.pending and not log.stop.exists():
                    try:gpio.disarm()
                    except BaseException as stop_error:result['errors'].append('DISARM_AFTER_RETURNED_ERROR:'+str(stop_error))
        elif gpio is not None and gpio.owned:result['required_action']='STOP_ALL_HARDWARE_ACCESS_NO_RETRY_OR_AUTOMATIC_CLEANUP_WRITE'
        for obj in (backend,transport,rawio):
            if obj is not None:
                try:obj.close()
                except BaseException as exc:result['errors'].append('LOCAL_CLOSE:'+str(exc))
        if memory is not None:
            try:memory.close()
            except BaseException as exc:result['errors'].append('LOCAL_UNMAP:'+str(exc))
        if fd is not None:os.close(fd)
        result['MMIO_unknown']=log.pending or log.failed or log.stop.exists()
        if result['MMIO_unknown']:result['required_action']='STOP_ALL_HARDWARE_ACCESS_NO_RETRY_OR_AUTOMATIC_CLEANUP_WRITE'
        result['elapsed_ns']=time.monotonic_ns()-began
        result['status']='PASS_DIGITAL_ONLY_NOT_PHYSICAL' if not result['errors'] and result.get('FULL_OFF_readback',{}).get('status')=='VERIFIED_REGISTER_BITS_ONLY' and result['transactions']>0 else 'FAILED_OR_PARTIAL_STOP_NO_RETRY'
        log.event('SESSION_END',status=result['status'],errors=result['errors'])
        (args.out/'WORKER_RESULT.json').write_text(json.dumps(result,indent=2));log.fp.close()
    return 0 if result['status']=='PASS_DIGITAL_ONLY_NOT_PHYSICAL' else 70


def main(argv=None):
    args=parser().parse_args(argv)
    r.require(args.run_once and (args.manual_matrix if args.matrix_mode else args.manual_single_motor),
              'EXPLICIT_MATCHING_MANUAL_KIND_AND_RUN_ONCE_REQUIRED')
    r.require(args.matrix_mode or not args.manual_matrix,'MANUAL_MATRIX_REQUIRES_EXPLICIT_MATRIX_MODE')
    for name in ('profile','out','gpio_helper','operator_attestation'):
        r.require(getattr(args,name).is_absolute(),'ABSOLUTE_ARGUMENT_PATH_REQUIRED:'+name)
    proof(args)
    if args.worker:return worker(args)
    args.out.mkdir(parents=True,exist_ok=False)
    (args.out/'ATTEMPT.json').write_text(json.dumps({'schema':'D1_ONE_ATTEMPT','mode':'MANUAL_MATRIX_BENCH' if args.matrix_mode else 'MANUAL_SINGLE_MOTOR_BENCH',
        'pid':os.getpid(),'deadline_s':63,'automatic_retry':False,'actual_live':False}))
    cmd=[sys.executable,'-B','-u',str(Path(__file__).resolve()),*(sys.argv[1:] if argv is None else argv),'--worker']
    with (args.out/'worker.stdout.log').open('xb') as stdout,(args.out/'worker.stderr.log').open('xb') as stderr:
        child=subprocess.Popen(cmd,stdout=stdout,stderr=stderr,start_new_session=True)
        try:code=child.wait(timeout=63);timeout=False
        except subprocess.TimeoutExpired:
            timeout=True;code=124;(args.out/'STOP_MMIO').write_text('PARENT_DEADLINE_NO_FURTHER_HARDWARE_ACCESS\n')
    result={'schema':'D1_BOUNDED_PARENT_V1','worker_pid':child.pid,'returncode':code,'timeout':timeout,
        'automatic_retry':False,'out':str(args.out),'mode':'MANUAL_MATRIX_BENCH' if args.matrix_mode else 'MANUAL_SINGLE_MOTOR_BENCH','actual_live':False,
        'required_action':'STOP_ALL_HARDWARE_ACCESS_NO_RETRY_NO_AUTOMATIC_CLEANUP_WRITE' if timeout else 'REVIEW_WORKER_RESULT'}
    (args.out/'SUPERVISOR_RESULT.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True);return code


if __name__=='__main__':raise SystemExit(main())
