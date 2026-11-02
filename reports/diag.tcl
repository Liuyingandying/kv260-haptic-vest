set root "E:/KVHAP_2F_R2_DIAG"
open_project $root/kv260_i2c_bringup.xpr
puts "=== FILE STATE ==="
puts "FILES=[get_files -quiet *kv260_haptic_iobuf_wrapper.v]"
set top [get_property top [current_fileset]]
puts "TOP=$top"
# module reference probe in a scratch BD
create_bd_design scratch
if {[catch {create_bd_cell -type module -reference kv260_haptic_iobuf_wrapper probe} emsg]} {
  puts "CREATE_FAILED: $emsg"
} else {
  puts "CREATE_OK"
  set intfs [get_bd_intf_pins -quiet probe/*]
  puts "INTF_COUNT=[llength $intfs]"
  foreach i $intfs { puts "  INTF=[get_property NAME $i] TYPE=[get_property TYPE $i]" }
  set pins [get_bd_pins -quiet probe/*]
  puts "PIN_COUNT=[llength $pins]"
  foreach p $pins { puts "  PIN=[get_property NAME $p] DIR=[get_property DIR $p]" }
}
puts "=== DONE_DIAG ==="
close_project