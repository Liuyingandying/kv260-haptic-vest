"""Build only a new KV260 board project; never opens hardware or deploys artifacts."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent/'scripts'))
from tool_paths import vivado as find_vivado, child_env
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def check_generated_xci_path(project):
    """Fail before launching Vivado if its known generated XCI exceeds MAX_PATH."""
    xci = (Path(project) / 'kv260_unified_board.srcs' / 'sources_1' / 'bd' /
           'kv260_unified_ps' / 'ip' / 'kv260_unified_ps_zynq_ultra_ps_e_0_0' /
           'kv260_unified_ps_zynq_ultra_ps_e_0_0.xci')
    byte_count = len(str(xci).encode('utf-8'))
    if os.name == 'nt' and byte_count >= 260:
        raise SystemExit(
            f'Windows Vivado generated XCI path would be {byte_count} bytes '
            f'(must be less than 260):\n{xci}\n'
            'Unpack or copy this source package to a short ASCII directory '
            '(for example E:\\KVUPUB_20261103), then run the same command. '
            'Vivado was not started; no project or hardware was changed.')
    return byte_count

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stage',choices=('project','synth','bitstream'),default='bitstream')
    parser.add_argument('--resume-project',type=Path)
    parser.add_argument('--resume-impl',action='store_true')
    args=parser.parse_args()
    if args.resume_impl and not args.resume_project:
        raise SystemExit('--resume-impl requires a verified independent --resume-project')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    project=(args.resume_project.resolve().parent if args.resume_project else
             ROOT/'projects'/('build_'+stamp))
    check_generated_xci_path(project)
    run=ROOT/'results'/('build_'+stamp);run.mkdir(parents=True)
    if args.resume_project:
        old=args.resume_project.resolve()
        if not old.is_relative_to((ROOT/'projects').resolve()) or not old.is_file():
            raise SystemExit('Resume only an existing project in this independent board directory')
        project=old.parent
        for name in ('synth_1','impl_1'):
            oldlog=project/'kv260_unified_board.runs'/name/'runme.log'
            if oldlog.is_file():shutil.copy2(oldlog,run/(name+'_previous_runme.log'))
        if args.resume_impl:
            previous=json.loads((ROOT/'LATEST_BUILD.json').read_text(encoding='utf8'))
            for name,expected in previous['input_sha256'].items():
                if name.replace('\\','/')!='scripts/board_pre_opt.tcl' and digest(ROOT/name)!=expected:
                    raise SystemExit('Cannot reuse synthesis after input change: '+name)
            previous_run=Path(previous['result_directory'])
            for name in ('synth_connectivity.txt','synth_io.rpt','synth_clocks.rpt','synth_drc.rpt'):
                src=previous_run/name
                if not src.is_file():raise SystemExit('Missing reusable synthesis proof '+str(src))
                shutil.copy2(src,run/name)
            (run/'REUSED_SYNTHESIS_EVIDENCE.json').write_text(json.dumps(dict(source_run=str(previous_run),
                reason='Only ordinary pre-opt Tcl boolean validation changed; RTL and XDC unchanged'),indent=2),encoding='utf8')
    manifest=json.loads((ROOT/'manifests/SOURCE_SNAPSHOT.json').read_text(encoding='utf8'))
    rtl=[ROOT/row['copy'].replace('\\','/') for row in manifest['files']]
    for row,p in zip(manifest['files'],rtl):
        if digest(p)!=row['sha256']:
            raise SystemExit('Protected integration snapshot changed: '+str(p))
    top=ROOT/'rtl/kv260_unified_board_top.sv'
    xdc=ROOT/'constraints/kv260_unified_pmod.xdc'
    cdc=ROOT/'constraints/kv260_unified_cdc.xdc'
    tcl=run/'build.tcl'
    lines=[f'set board_root {{{ROOT.as_posix()}}}',f'set run_dir {{{run.as_posix()}}}',
           'set_param general.maxThreads 2',
           (f'open_project {{{args.resume_project.as_posix()}}}' if args.resume_project else
            f'create_project kv260_unified_board {{{project.as_posix()}}} -part xck26-sfvc784-2LV-c'),
           'set_property target_language Verilog [current_project]',
           'set_property simulator_language Mixed [current_project]']
    if not args.resume_project:
        lines += [f'add_files -norecurse {{{p.as_posix()}}}' for p in rtl+[top]]
        lines += [f'add_files -fileset constrs_1 -norecurse {{{xdc.as_posix()}}}',
                  'source [file join $board_root scripts create_board_bd.tcl]']
    else:
        lines += ['reset_runs impl_1' if args.resume_impl else 'reset_runs synth_1']
    lines += [f'if {{[llength [get_files -quiet {{{cdc.as_posix()}}}]] == 0}} {{add_files -fileset constrs_1 -norecurse {{{cdc.as_posix()}}}}}',
              f'set_property USED_IN_SYNTHESIS false [get_files {{{cdc.as_posix()}}}]',
              'set_property top kv260_unified_board_top [get_filesets sources_1]',
              'if {[llength [get_files -quiet [file join $board_root scripts board_pre_opt.tcl]]] == 0} {add_files -fileset utils_1 -norecurse [file join $board_root scripts board_pre_opt.tcl]}',
              'set_property STEPS.OPT_DESIGN.TCL.PRE [file join $board_root scripts board_pre_opt.tcl] [get_runs impl_1]',
              'update_compile_order -fileset sources_1',
              'report_compile_order -used_in synthesis -file [file join $run_dir compile_order.rpt]',
              'report_ip_status -file [file join $run_dir ip_status.rpt]',
              'puts "BOARD_PROJECT_READY"']
    if args.stage!='project' and not args.resume_impl:
        lines += ['launch_runs synth_1 -jobs 1','wait_on_run synth_1',
                  'if {[get_property PROGRESS [get_runs synth_1]] ne "100%" || [string match -nocase *error* [get_property STATUS [get_runs synth_1]]]} {error "Synthesis did not complete"}',
                  'open_run synth_1','report_io -file [file join $run_dir synth_io.rpt]',
                  'report_clocks -file [file join $run_dir synth_clocks.rpt]',
                  'report_drc -file [file join $run_dir synth_drc.rpt]',
                  'set proof [open [file join $run_dir synth_connectivity.txt] w]',
                  'foreach p [get_ports] {puts $proof "PORT=$p PIN=[get_property PACKAGE_PIN $p] IO=[get_property IOSTANDARD $p] DRIVE=[get_property DRIVE $p] SLEW=[get_property SLEW $p] PULL=[get_property PULLTYPE $p]"}',
                  'foreach b [get_cells -hier -filter {REF_NAME == IOBUF}] {puts $proof "IOBUF=$b"; foreach pin [get_pins -of_objects $b] {puts $proof " $pin NET=[get_nets -of_objects $pin]"}}',
                  'set ps_cells [get_cells -hier -filter {REF_NAME == PS8}]',
                  'if {[llength $ps_cells] != 1} {error "Expected one PS8"}',
                  'foreach leaf {EMIOHUBPORTOVERCRNTUSB20 EMIOHUBPORTOVERCRNTUSB21 EMIOHUBPORTOVERCRNTUSB30 EMIOHUBPORTOVERCRNTUSB31} {',
                  ' set pin [get_pins -quiet [lindex $ps_cells 0]/$leaf]',
                  ' if {[llength $pin] != 1} {error "Missing USB overcurrent pin $leaf"}',
                  ' set net [get_nets -of_objects $pin]',
                  ' set drivers [get_cells -of_objects [get_pins -leaf -of_objects $net -filter {DIRECTION == OUT}]]',
                  ' set refs [get_property REF_NAME $drivers]',
                  ' puts $proof "USB=$leaf NET=$net DRIVER_REFS=$refs"',
                  ' if {[lsearch -exact $refs GND] < 0 || [lsearch -exact $refs VCC] >= 0} {error "USB overcurrent not GND: $leaf"}',
                  '}',
                  'close $proof','puts "BOARD_SYNTHESIS_PASS"','close_design']
    elif args.resume_impl:
        lines += ['if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "Cannot reuse incomplete synthesis"}']
    if args.stage=='bitstream':
        lines += ['launch_runs impl_1 -to_step write_bitstream -jobs 1','wait_on_run impl_1',
                  'if {[get_property PROGRESS [get_runs impl_1]] ne "100%" || [string match -nocase *error* [get_property STATUS [get_runs impl_1]]]} {error "Implementation did not complete"}',
                  'open_run impl_1','report_io -file [file join $run_dir routed_io.rpt]',
                  'report_clocks -file [file join $run_dir routed_clocks.rpt]',
                  'report_exceptions -coverage -file [file join $run_dir routed_exceptions.rpt]',
                  'report_timing_summary -report_unconstrained -file [file join $run_dir routed_timing.rpt]',
                  'report_drc -file [file join $run_dir routed_drc.rpt]',
                  'report_route_status -file [file join $run_dir routed_status.rpt]',
                  'report_utilization -file [file join $run_dir utilization.rpt]',
                  'set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]',
                  'set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]',
                  'set fp [open [file join $run_dir timing_result.txt] w]; puts $fp "WNS=$wns WHS=$whs"; close $fp',
                  'if {$wns < 0 || $whs < 0} {error "Negative routed timing slack"}',
                  'report_drc -checks [get_drc_checks {NSTD-1 UCIO-1}] -file [file join $run_dir physical_io_drc.rpt]',
                  'set ff [open [file join $run_dir physical_io_drc.rpt] r]; set io_drc_text [read $ff]; close $ff',
                  'if {![regexp {Checks found:[[:space:]]+0} $io_drc_text]} {error "Physical IO DRC did not report zero violations"}',
                  'write_checkpoint [file join $run_dir kv260_unified_board_routed.dcp]',
                  'puts "BOARD_IMPLEMENTATION_PASS_NOT_BOARD_LOADED"','close_design']
    lines += ['close_project','exit 0']
    tcl.write_text('\n'.join(lines)+'\n',encoding='utf8')
    env=child_env()
    cmd=[find_vivado(),'-mode','batch','-notrace',
         '-source',str(tcl),'-log',str(run/'vivado.log'),'-journal',str(run/'vivado.jou')]
    inputs={str(p.relative_to(ROOT)):digest(p) for p in rtl+[top,xdc,cdc,ROOT/'scripts/create_board_bd.tcl',ROOT/'scripts/board_pre_opt.tcl',ROOT/'platform/ps_baseline_parameters.tcl']}
    pending=dict(command=cmd,project=str(project/'kv260_unified_board.xpr'),stage=args.stage,
                 input_sha256=inputs,board_access=False,board_load=False)
    (run/'START.json').write_text(json.dumps(pending,indent=2),encoding='utf8')
    (ROOT/'LATEST_BUILD.json').write_text(json.dumps(dict(pending,result_directory=str(run),status='RUNNING'),indent=2),encoding='utf8')
    print(json.dumps(dict(run=str(run),project=pending['project'],stage=args.stage)),flush=True)
    with (run/'stdout.log').open('wb') as out,(run/'stderr.log').open('wb') as err:
        p=subprocess.run(cmd,cwd=run,env=env,stdout=out,stderr=err,timeout=5400)
    pending.update(exit_code=p.returncode,bitstreams=[str(x) for x in project.rglob('*.bit')],
                   status='PASS' if p.returncode==0 else 'FAIL',result_directory=str(run))
    (run/'RESULT.json').write_text(json.dumps(pending,indent=2),encoding='utf8')
    (ROOT/'LATEST_BUILD.json').write_text(json.dumps(pending,indent=2),encoding='utf8')
    print(json.dumps(pending,indent=2),flush=True)
    return p.returncode

if __name__=='__main__':sys.exit(main())
