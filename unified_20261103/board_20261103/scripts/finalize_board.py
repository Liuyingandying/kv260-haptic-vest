"""Read-only design recheck after bitgen; no synthesis rerun and no hardware access."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent/'scripts'))
from tool_paths import vivado as find_vivado, child_env
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    previous=json.loads((ROOT/'LATEST_BUILD.json').read_text(encoding='utf8'))
    for name,value in previous['input_sha256'].items():
        if digest(ROOT/name)!=value:raise SystemExit('Build input changed: '+name)
    project=Path(previous['project'])
    if not previous['bitstreams'] or any(not Path(p).is_file() for p in previous['bitstreams']):
        raise SystemExit('No generated bitstream to verify')
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    run=ROOT/'results'/('postcheck_'+stamp);run.mkdir()
    upstream=Path(previous['result_directory'])
    for name in ('synth_connectivity.txt','synth_io.rpt','synth_clocks.rpt','synth_drc.rpt'):
        shutil.copy2(upstream/name,run/name)
    tcl=run/'postcheck.tcl'
    tcl.write_text('\n'.join([
        'set_param general.maxThreads 2',f'set run_dir {{{run.as_posix()}}}',
        f'open_project {{{project.as_posix()}}}','open_run impl_1',
        'if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "Implementation not complete"}',
        'set ports [get_ports]; if {[llength $ports] != 2} {error "Unexpected external ports"}',
        'set fp [open [file join $run_dir postroute_connectivity.txt] w]',
        'foreach p $ports {puts $fp "PORT=$p PIN=[get_property PACKAGE_PIN $p] IO=[get_property IOSTANDARD $p] DRIVE=[get_property DRIVE $p] SLEW=[get_property SLEW $p] PULL=[get_property PULLTYPE $p]"}',
        'foreach p [get_ports] {if {[get_property IOSTANDARD $p] ne "LVCMOS33" || [get_property DRIVE $p] != 4 || [get_property SLEW $p] ne "SLOW"} {error "Unexpected actual pin electrical properties"}}',
        'if {[get_property PACKAGE_PIN [get_ports i2c_scl_io]] ne "H12" || [get_property PACKAGE_PIN [get_ports i2c_sda_io]] ne "B10"} {error "Actual package mapping differs"}',
        'foreach c {scl_meta_reg scl_sync_reg sda_meta_reg sda_sync_reg} {set a [get_property ASYNC_REG [get_cells $c]]; puts $fp "SYNC=$c REF=[get_property REF_NAME [get_cells $c]] ASYNC_REG=$a"; if {![string is boolean -strict $a] || !$a} {error "ASYNC_REG not true"}}',
        'set ps [get_cells -hier -filter {REF_NAME == PS8}]; if {[llength $ps] != 1} {error "PS8 not unique"}',
        'foreach leaf {EMIOHUBPORTOVERCRNTUSB20 EMIOHUBPORTOVERCRNTUSB21 EMIOHUBPORTOVERCRNTUSB30 EMIOHUBPORTOVERCRNTUSB31} {',
        ' set p [get_pins [lindex $ps 0]/$leaf]; set net [get_nets -of_objects $p]',
        ' set refs [get_property REF_NAME [get_cells -of_objects [get_pins -leaf -of_objects $net -filter {DIRECTION == OUT}]]]',
        ' puts $fp "USB=$leaf NET=$net DRIVER_REFS=$refs"',
        ' if {[lsearch -exact $refs GND] < 0 || [lsearch -exact $refs VCC] >= 0} {error "USB overcurrent not GND"}',
        '}',
        'puts $fp "PL0_PERIOD_NS=[get_property PERIOD [get_clocks clk_pl_0]]"',
        'puts $fp "CONFIG_USR_ACCESS=[get_property BITSTREAM.CONFIG.USR_ACCESS [current_design]]"','close $fp',
        'if {[get_property PERIOD [get_clocks clk_pl_0]] != 10.0} {error "Internal timing budget is not100MHz"}',
        'report_io -file [file join $run_dir routed_io.rpt]',
        'report_clocks -file [file join $run_dir routed_clocks.rpt]',
        'report_exceptions -coverage -file [file join $run_dir routed_exceptions.rpt]',
        'report_timing_summary -report_unconstrained -file [file join $run_dir routed_timing.rpt]',
        'report_route_status -file [file join $run_dir routed_status.rpt]',
        'report_utilization -file [file join $run_dir utilization.rpt]',
        'report_drc -file [file join $run_dir routed_drc.rpt]',
        'report_drc -checks [get_drc_checks {NSTD-1 UCIO-1}] -file [file join $run_dir physical_io_drc.rpt]',
        'set f [open [file join $run_dir physical_io_drc.rpt] r]; set s [read $f]; close $f',
        'if {![regexp {Checks found:[[:space:]]+0} $s]} {error "NSTD/UCIO violations"}',
        'set wns [get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]',
        'set whs [get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]',
        'set f [open [file join $run_dir timing_result.txt] w]; puts $f "WNS=$wns WHS=$whs"; close $f',
        'if {$wns<0 || $whs<0} {error "Routed slack negative"}',
        'write_checkpoint [file join $run_dir kv260_unified_board_routed.dcp]',
        'puts "BOARD_POSTCHECK_PASS_NO_HARDWARE"','close_project','exit 0','']),encoding='utf8')
    cmd=[find_vivado(),'-mode','batch','-notrace','-source',str(tcl),'-log',str(run/'vivado.log'),'-journal',str(run/'vivado.jou')]
    env=child_env()
    print(str(run),flush=True)
    with (run/'stdout.log').open('wb') as out,(run/'stderr.log').open('wb') as err:
        p=subprocess.run(cmd,cwd=run,env=env,stdout=out,stderr=err,timeout=600)
    result=dict(command=cmd,project=str(project),stage='postcheck_existing_implemented_design',
        input_sha256=previous['input_sha256'],exit_code=p.returncode,
        status='PASS' if p.returncode==0 else 'FAIL',result_directory=str(run),
        bitstreams=previous['bitstreams'],bitstream_sha256={s:digest(Path(s)) for s in previous['bitstreams']},
        upstream_build_result=str(upstream/'RESULT.json'),upstream_process_exit_code=previous['exit_code'],
        upstream_postprocessing_error=('Upstream nonzero exit; inspect original RESULT.json and logs'
                                      if previous['exit_code'] else None),
        synthesis_rerun=False,implementation_rerun=False,board_access=False,board_load=False)
    (run/'RESULT.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    (ROOT/'LATEST_BUILD.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print(json.dumps(result,indent=2),flush=True)
    return p.returncode
if __name__=='__main__':raise SystemExit(main())
