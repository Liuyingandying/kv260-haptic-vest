set root "E:/KVHAP_2F_IOBUF_FIX_R2_MI4"
open_project $root/kv260_i2c_bringup.xpr
set_property source_mgmt_mode All [current_project]
puts "SRC_V=[llength [get_files -quiet *.v]] SRC_SV=[llength [get_files -quiet *.sv]]"
set_property top kv260_i2c_bringup_top [current_fileset]

open_bd_design [get_files */kv260_ps_bd.bd]

# ---------- Phase R2-MI4 minimal fix ----------
puts "NUM_MI_BEFORE=[get_property CONFIG.NUM_MI [get_bd_cells smartconnect_0]]"
set_property CONFIG.NUM_MI 4 [get_bd_cells smartconnect_0]
puts "NUM_MI_AFTER=[get_property CONFIG.NUM_MI [get_bd_cells smartconnect_0]]"
if {[get_property CONFIG.NUM_MI [get_bd_cells smartconnect_0]] != 4} { error "NUM_MI_NOT_4" }
foreach mi {M00_AXI M01_AXI M02_AXI M03_AXI} {
  set p [get_bd_intf_pins -quiet smartconnect_0/$mi]
  if {[llength $p] == 0} { error "MISSING $mi" }
  puts "MI_PORT_OK $mi"
}

create_bd_cell -type module -reference kv260_haptic_iobuf_wrapper haptic_fix
set_property CONFIG.CLK_FREQ_HZ 100000000 [get_bd_cells haptic_fix]
set intf [get_bd_intf_pins -quiet haptic_fix/s_axi]
puts "HAPTIC_INTF=[get_property NAME $intf] TYPE=[get_property TYPE $intf]"
if {[llength $intf] == 0} { error "HAPTIC_S_AXI_MISSING" }

connect_bd_intf_net [get_bd_intf_pins smartconnect_0/M03_AXI] [get_bd_intf_pins haptic_fix/s_axi]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins haptic_fix/aclk]
connect_bd_net [get_bd_ports diag_bus_resetn] [get_bd_pins haptic_fix/aresetn]
make_bd_pins_external [get_bd_pins haptic_fix/i2c_scl_io]
make_bd_pins_external [get_bd_pins haptic_fix/i2c_sda_io]
assign_bd_address -offset 0x9C000000 -range 0x00010000 [get_bd_addr_segs haptic_fix/s_axi/reg0] -target_address_space [get_bd_addr_spaces zynq_ultra_ps_e_0/Data] -force
puts "ADDR_MAP:"
foreach s [get_bd_addr_segs -of_objects [get_bd_addr_spaces zynq_ultra_ps_e_0/Data]] {
  puts "   [get_property NAME $s] offset=[get_property OFFSET $s] range=[get_property RANGE $s]"
}
validate_bd_design
save_bd_design
puts "BD_VALIDATED"
generate_target all [get_files */kv260_ps_bd.bd]
make_wrapper -files [get_files */kv260_ps_bd.bd] -top
set_property top kv260_i2c_bringup_top [current_fileset]
update_compile_order -fileset sources_1
reset_run synth_1
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "SYNTH_FAILED"}
puts "SYNTH_OK"
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "IMPL_FAILED"}
puts "IMPL_OK"
open_run impl_1
report_io -file $root/io_mi4.rpt
report_timing_summary -file $root/timing_mi4.rpt
report_utilization -file $root/util_mi4.rpt
report_drc -file $root/drc_mi4.rpt

puts "===== CORE NETLIST EVIDENCE ====="
set iobufs [get_cells -quiet -hier -filter {REF_NAME=~IOBUF}]
puts "IOBUF_COUNT=[llength $iobufs]"
foreach c $iobufs {
  puts "IOBUF_CELL $c"
  foreach pin {I T O IO} {
    set p [get_pins -quiet $c/$pin]
    if {[llength $p]} {
      set n [get_nets -quiet -of_objects $p]
      puts "   .$pin net=[get_property NAME $n]"
      set allpins [get_pins -quiet -of_objects $n]
      foreach q $allpins { puts "        netpin=[get_property NAME $q] dir=[get_property DIRECTION $q] cell=[get_cells -quiet -of_objects $q]" }
    }
  }
}
puts "OBUF_COUNT=[llength [get_cells -quiet -hier -filter {REF_NAME=~OBUF}]]"
puts "OBUFT_COUNT=[llength [get_cells -quiet -hier -filter {REF_NAME=~OBUFT}]]"
foreach p {i2c_scl_io i2c_sda_io} {
  set pt [get_ports -quiet $p]
  if {[llength $pt]} { puts "PORT $p PULLTYPE=[get_property PULLTYPE $pt] PULLUP=[get_property PULLUP $pt] IOSTD=[get_property IOSTANDARD $pt] PIN=[get_property PACKAGE_PIN $pt]" }
}
foreach n {i2c_scl_io i2c_sda_io} {
  foreach c [get_cells -quiet -of_objects [get_nets -quiet $n]] { puts "TOP_NET_CELL $n -> [get_property REF_NAME $c]" }
}
puts "WNS=[get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]"
puts "WHS=[get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]"
write_hw_platform -fixed -include_bit -force -file $root/kv260_haptic_2f_mi4.xsa
puts "XSA_WRITTEN"
close_project
puts "=== MI4_BUILD_DONE ==="