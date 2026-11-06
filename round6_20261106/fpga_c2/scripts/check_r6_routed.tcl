# Offline opened implemented checkpoint only. Never opens a hardware target.
set root [file normalize [file join [file dirname [info script]] ..]]
set project [file join $root p c1 kv260_unified_board.xpr]
open_project $project
open_run impl_1
set proof [open [file join $root evidence R6_RESET_REGISTER_PROOF.txt] w]
foreach path {haptic_resetn_registered_reg u_pad_guard/core_resetn_o_reg} {
 set c [get_cells -quiet $path]
 if {[llength $c] == 0} {set c [get_cells -quiet -hier -filter [format {NAME =~ *%s*} [string map {/ *} $path]]]}
 if {[llength $c] != 1} {error "R6 registered reset missing: $path"}
 set ref [get_property REF_NAME $c]
 puts $proof "CELL=$c REF=$ref"
 if {$ref ne "FDRE"} {error "R6 reset driver is not FDRE: $c $ref"}
 set q [get_pins -of_objects $c -filter {REF_PIN_NAME == Q}]
 set net [get_nets -of_objects $q]
 puts $proof "Q=$q NET=$net FANOUT=[llength [get_pins -leaf -of_objects $net -filter {DIRECTION == IN}]]"
}
puts $proof "USR_ACCESS=[get_property BITSTREAM.CONFIG.USR_ACCESS [current_design]]"
if {[get_property BITSTREAM.CONFIG.USR_ACCESS [current_design]] ne "0x52364331"} {error "R6 USR_ACCESS mismatch"}
foreach pattern {*u_haptic*u_mb_axi*u_matrix_buffer* *u_haptic*u_sched* *u_haptic*u_map* *u_haptic*u_duty* *u_haptic*u_i2c* *u_adapter*} {
 set leaves [get_cells -quiet -hier -filter [format {IS_PRIMITIVE == 1 && NAME =~ %s} $pattern]]
 puts $proof "RETAINED_LOGIC=$pattern LEAF_COUNT=[llength $leaves]"
 if {[llength $leaves] == 0} {error "C1 required control logic was removed: $pattern"}
}
set dynamic_data 0
foreach pin [get_pins -quiet -hier -filter {DIRECTION == OUT && NAME =~ *u_adapter*/m_wdata*}] {
 set net [get_nets -of_objects $pin]
 set drivers [get_cells -of_objects [get_pins -leaf -of_objects $net -filter {DIRECTION == OUT}]]
 set refs [get_property REF_NAME $drivers]
 puts $proof "CONTROL_MATRIX_DATA_PIN=$pin NET=$net DRIVER_REFS=$refs"
 if {[lsearch -exact $refs GND] < 0 && [lsearch -exact $refs VCC] < 0 && [llength $refs] > 0} {incr dynamic_data}
}
puts $proof "DYNAMIC_CONTROL_MATRIX_DATA_PINS=$dynamic_data"
if {$dynamic_data == 0} {error "C1 control matrix data appears constant or missing"}
close $proof
# Literal checks are intentionally logged to disambiguate R5 report provenance.
puts "R6_PHYSICAL_DRC_CHECK_NAMES=NSTD-1,UCIO-1"
report_drc -checks {NSTD-1 UCIO-1} -file [file join $root evidence R6_PHYSICAL_DRC_LITERAL.rpt]
report_cdc -details -file [file join $root evidence R6_CDC_DETAILS.rpt]
report_timing_summary -report_unconstrained -file [file join $root evidence R6_FINAL_TIMING.rpt]
puts "R6_ROUTED_OBJECT_CHECK_PASS_NOT_BOARD_LOADED"
close_design
close_project
exit 0
