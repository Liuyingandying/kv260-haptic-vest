set root E:/KVHAP_2F_IOBUF_FIX_R2_MI4
file mkdir $root/pad_eco_result
open_checkpoint $root/kv260_i2c_bringup.runs/impl_1/kv260_i2c_bringup_top_physopt.dcp
source $root/fix_pad_readback_eco.tcl
write_checkpoint -force $root/pad_eco_result/corrected_placed.dcp
route_design
report_route_status -file $root/pad_eco_result/route_status.rpt
report_drc -file $root/pad_eco_result/drc.rpt
report_timing_summary -delay_type min_max -file $root/pad_eco_result/timing.rpt
report_io -file $root/pad_eco_result/io.rpt
write_checkpoint -force $root/pad_eco_result/corrected_routed.dcp
write_bitstream -force $root/pad_eco_result/kv260_pad_eco.bit
puts PAD_ECO_VALIDATION_COMPLETE
close_design
