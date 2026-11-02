set root "E:/KVHAP_2D"
set haptic_rtl "E:/KVHAP_2D/rtl_haptic"
open_project $root/kv260_i2c_bringup.xpr
set_property source_mgmt_mode All [current_project]

# --- add haptic RTL ---
foreach f [glob -nocomplain $haptic_rtl/*.v] { add_files -norecurse $f }
update_compile_order -fileset sources_1
puts "HAPTIC_RTL_ADDED"

# --- BD: extend smartconnect to 4 slaves, add haptic wrapper at 0xA0000000 ---
open_bd_design [get_files */kv260_ps_bd.bd]
set ps [get_bd_cells zynq_ultra_ps_e_0]
puts "PS_WIDTH=[get_property CONFIG.PSU__MAXIGP2__DATA_WIDTH $ps]"
set sc [get_bd_cells smartconnect_0]
set_property CONFIG.NUM_MI 4 $sc
if {[llength [get_bd_cells -quiet haptic_bd]]!=0} { delete_bd_objs [get_bd_cells haptic_bd] }
create_bd_cell -type module -reference kv260_haptic_bd_wrapper haptic_bd
set_property CONFIG.CLK_FREQ_HZ 100000000 [get_bd_cells haptic_bd]
catch { set_property CONFIG.FREQ_HZ 96968727 [get_bd_pins haptic_bd/aclk] }
catch { set_property CONFIG.FREQ_HZ 96968727 [get_bd_intf_pins haptic_bd/s_axi] }
catch { set_property CONFIG.FREQ_HZ 96968727 [get_bd_pins haptic_bd/aresetn] }
puts FREQ_FORCED
connect_bd_intf_net [get_bd_intf_pins smartconnect_0/M03_AXI] [get_bd_intf_pins haptic_bd/s_axi]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins haptic_bd/aclk]
connect_bd_net [get_bd_ports diag_bus_resetn] [get_bd_pins haptic_bd/aresetn]
assign_bd_address -offset 0x9C000000 -range 0x00010000 [get_bd_addr_segs haptic_bd/s_axi/reg0] -target_address_space [get_bd_addr_spaces zynq_ultra_ps_e_0/Data] -force
validate_bd_design
save_bd_design
puts "BD_VALIDATED"
assign_bd_address -export_to_file $root/evidence/address_map_2d.rpt

generate_target all [get_files */kv260_ps_bd.bd]
set wrap [make_wrapper -files [get_files */kv260_ps_bd.bd] -top]
if {[lsearch -exact [get_files -quiet *kv260_ps_bd_wrapper.v]]<0} { add_files -norecurse $wrap }
puts "WRAPPER=$wrap"

update_compile_order -fileset sources_1
reset_run synth_1
launch_runs synth_1 -jobs 8
wait_on_run synth_1
if {[get_property PROGRESS [get_runs synth_1]] ne "100%"} {error "SYNTH_FAILED [get_property STATUS [get_runs synth_1]]"}
puts "SYNTH_OK"
launch_runs impl_1 -to_step write_bitstream -jobs 8
wait_on_run impl_1
if {[get_property PROGRESS [get_runs impl_1]] ne "100%"} {error "IMPL_FAILED [get_property STATUS [get_runs impl_1]]"}
puts "IMPL_OK"
open_run impl_1
report_timing_summary -file $root/evidence/timing_2d.rpt
report_utilization -file $root/evidence/util_2d.rpt
report_io -file $root/evidence/io_2d.rpt
write_hw_platform -fixed -include_bit -force -file $root/kv260_haptic_2d.xsa
puts "XSA_WRITTEN"
close_project
puts "=== PHASE2D_BUILD_DONE ==="