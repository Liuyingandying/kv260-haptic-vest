# Phase 1C synthesis: internal-logic-only analysis for kv260_haptic_top
#
# Deliberately contains NO PACKAGE_PIN / no I/O timing constraints because the
# J2 pin mapping is still UNKNOWN (see KV260_HAPTIC_H0_READONLY_AUDIT.md).
# -mode out_of_context lets us synthesise and time the INTERNAL logic only.
# Any I/O timing result is therefore explicitly "not verified".
#
# No bitstream is written. Only a checkpoint + reports.

set part   "xck26-sfvc784-2LV-c"
set top    "kv260_haptic_top"
set rtl    "D:/DevCache/Temp/kv260_haptic_synth/rtl"
set outdir "D:/DevCache/Temp/kv260_haptic_synth/out"

puts "=== Phase1C synth start: part=$part top=$top ==="
create_project -in_memory -part $part
set_property target_language Verilog [current_project]

set files [glob -nocomplain -directory $rtl *.v]
puts "=== reading [llength $files] RTL files ==="
foreach f $files {
    read_verilog -quiet $f
}
set_property top $top [current_fileset]

# ---- clock: KV260 psu_pl_clk0 = 96.968727 MHz -> 10.3126 ns ----
create_clock -name pl_clk0 -period 10.312 -waveform {0.000 5.156} [get_ports pl_clk0]
set_property -dict {PACKAGE_PIN UNKNOWN IOSTANDARD UNKNOWN} [get_ports pl_clk0] -quiet

puts "=== synth_design (out_of_context) ==="
synth_design -top $top -part $part -mode out_of_context -flatten_hierarchy rebuilt \
             -directive Default 2>&1

puts "=== reports ==="
report_utilization          -hierarchical -file $outdir/utilization_hier.rpt
report_utilization                      -file $outdir/utilization.rpt
report_utilization -cells [get_cells -hier -filter {PRIMITIVE_GROUP==REGISTER}] -file $outdir/util_ff.rpt -quiet

report_timing_summary -delay_type min_max -max_paths 20 -nworst 5 \
                      -report_unconstrained -file $outdir/timing_summary.rpt
report_timing -delay_type max -max_paths 10 -sort_by group -file $outdir/timing_setup.rpt
report_timing -delay_type min -max_paths 10 -sort_by group -file $outdir/timing_hold.rpt

report_clocks -file $outdir/clocks.rpt
report_cdc    -file $outdir/cdc.rpt -details

write_checkpoint -force $outdir/post_synth.dcp
write_verilog  -force $outdir/post_synth.v
puts "=== Phase1C synth done ==="
