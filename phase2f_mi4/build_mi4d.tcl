set root "E:/KVHAP_2F_IOBUF_FIX_R2_MI4"
open_project $root/kv260_i2c_bringup.xpr
set_property source_mgmt_mode All [current_project]
set_property top kv260_i2c_bringup_top [current_fileset]
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
puts "===== EVIDENCE ====="
set iobufs [get_cells -quiet -hier -filter {REF_NAME=~IOBUF}]
puts "IOBUF_COUNT=[llength $iobufs]"
foreach c $iobufs {
  puts "IOBUF_CELL $c"
  foreach pin {I T O IO} {
    set p [get_pins -quiet $c/$pin]
    if {[llength $p]} {
      set n [get_nets -quiet -of_objects $p]
      puts "   .$pin net=[get_property NAME $n]"
      foreach q [get_pins -quiet -of_objects $n] { puts "        netpin=[get_property NAME $q]" }
    }
  }
}
puts "OBUF_COUNT=[llength [get_cells -quiet -hier -filter {REF_NAME=~OBUF}]]"
foreach p {i2c_scl_io i2c_sda_io} {
  set pt [get_ports -quiet $p]
  if {[llength $pt]} { puts "PORT $p PULLTYPE=[get_property PULLTYPE $pt] IOSTD=[get_property IOSTANDARD $pt] PIN=[get_property PACKAGE_PIN $pt]" }
}
puts "WNS=[get_property SLACK [get_timing_paths -delay_type max -max_paths 1]]"
puts "WHS=[get_property SLACK [get_timing_paths -delay_type min -max_paths 1]]"
write_hw_platform -fixed -include_bit -force -file $root/kv260_haptic_2f_mi4.xsa
puts "XSA_WRITTEN"
close_project
puts "=== MI4_DONE ==="