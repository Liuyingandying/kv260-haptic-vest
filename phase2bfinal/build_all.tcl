proc build_one {tag rtl_dir wrap_file ext_pins do_bitstream out_dir} {
    set part "xck26-sfvc784-2LV-c"
    set prj  "$out_dir/$tag"
    create_project -force $tag $prj -part $part
    set_property board_part xilinx.com:kv260_som:part0:1.4 [current_project]
    foreach f [glob -nocomplain $rtl_dir/*.v] { add_files -norecurse $f }
    add_files -norecurse $wrap_file
    update_compile_order -fileset sources_1

    create_bd_design "${tag}_bd"
    set ps [get_ipdefs -quiet -filter {NAME == zynq_ultra_ps_e}]
    create_bd_cell -type ip -vlnv $ps zynq_ps
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset "1"} [get_bd_cells zynq_ps]
    set_property -dict [list CONFIG.PSU__USE__M_AXI_GP0 {1} CONFIG.PSU__FPGA_PL0_ENABLE {1} \
                             CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {96.968727} \
                             CONFIG.PSU__USE__FABRIC__RST {1}] [get_bd_cells zynq_ps]

    create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 axi_sc
    set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] [get_bd_cells axi_sc]

    set wname [file rootname [file tail $wrap_file]]
    create_bd_cell -type module -reference $wname haptic
    set_property CONFIG.CLK_FREQ_HZ 90908180 [get_bd_cells haptic]

    connect_bd_intf_net [get_bd_intf_pins zynq_ps/M_AXI_HPM0_FPD] [get_bd_intf_pins axi_sc/S00_AXI]
    connect_bd_intf_net [get_bd_intf_pins axi_sc/M00_AXI]         [get_bd_intf_pins haptic/s_axi]
    connect_bd_net [get_bd_pins zynq_ps/pl_clk0] [get_bd_pins axi_sc/aclk]
    connect_bd_net [get_bd_pins zynq_ps/pl_clk0] [get_bd_pins haptic/aclk]
    connect_bd_net [get_bd_pins zynq_ps/pl_clk0] [get_bd_pins zynq_ps/maxihpm0_fpd_aclk]

    create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:5.0 rst_pl
    connect_bd_net [get_bd_pins zynq_ps/pl_clk0] [get_bd_pins rst_pl/slowest_sync_clk]
    connect_bd_net [get_bd_pins zynq_ps/pl_resetn0] [get_bd_pins rst_pl/ext_reset_in]
    connect_bd_net [get_bd_pins rst_pl/peripheral_aresetn] [get_bd_pins axi_sc/aresetn]
    connect_bd_net [get_bd_pins rst_pl/peripheral_aresetn] [get_bd_pins haptic/aresetn]

    if {$ext_pins == 1} {
        make_bd_pins_external [get_bd_pins haptic/i2c_scl_pin]
        make_bd_pins_external [get_bd_pins haptic/i2c_sda_pin]
        make_bd_pins_external [get_bd_pins haptic/wdog_heartbeat]
        make_bd_pins_external [get_bd_pins haptic/oe_n]
        puts "  ${tag} external PL pins CREATED (validation image only)"
    } else {
        puts "  ${tag} NO external PL pins created (debug image)"
    }

    assign_bd_address -offset 0xA0000000 -range 64K -target_address_space [get_bd_addr_spaces zynq_ps/Data] [get_bd_addr_segs haptic/s_axi/reg0]
    catch { set_property CONFIG.PSU__FPGA_PL1_ENABLE {0} [get_bd_cells zynq_ps] }
    if {[llength [get_bd_pins -quiet zynq_ps/maxihpm1_fpd_aclk]]} {
        if {![llength [get_bd_nets -quiet -of_objects [get_bd_pins zynq_ps/maxihpm1_fpd_aclk]]]} {
            catch { connect_bd_net [get_bd_pins zynq_ps/pl_clk0] [get_bd_pins zynq_ps/maxihpm1_fpd_aclk] }
        }
    }
    regenerate_bd_layout
    validate_bd_design
    save_bd_design
    puts "  ${tag} BD VALIDATED"

    make_wrapper -files [get_files ${tag}_bd.bd] -top
    add_files -norecurse $prj/$tag.gen/sources_1/bd/${tag}_bd/hdl/${tag}_bd_wrapper.v
    set_property top ${tag}_bd_wrapper [current_fileset]
    update_compile_order -fileset sources_1

    launch_runs synth_1 -jobs 8
    wait_on_run synth_1
    if {[get_property PROGRESS [get_runs synth_1]] != "100%"} { error "$tag SYNTH FAILED" }
    puts "  ${tag} SYNTH OK"

    if {$do_bitstream == 1} { launch_runs impl_1 -to_step write_bitstream -jobs 8 } else { launch_runs impl_1 -jobs 8 }
    wait_on_run impl_1
    if {[get_property PROGRESS [get_runs impl_1]] != "100%"} { error "$tag IMPL FAILED" }
    puts "  ${tag} IMPL OK : [get_property STATUS [get_runs impl_1]]"

    open_run impl_1
    report_utilization    -file $out_dir/${tag}_util.rpt
    report_timing_summary -delay_type min_max -report_unconstrained -file $out_dir/${tag}_timing.rpt
    report_io             -file $out_dir/${tag}_io.rpt
    puts "  ${tag} IOB = [llength [get_cells -hier -filter {PRIMITIVE_TYPE =~ IOB*}]]"
    if {$do_bitstream == 1} {
        write_hw_platform -fixed -include_bit -force -file $out_dir/${tag}_debug.xsa
        puts "  ${tag} XSA exported"
    }
    close_project
}

set root "D:/DevCache/Temp/kv260_p2bfinal"
build_one p2a_final $root/rtl $root/kv260_haptic_axi_wrapper.v   1 0 $root/out
build_one p2b_debug $root/rtl $root/kv260_haptic_debug_wrapper.v 0 1 $root/out
puts "=== ALL_DONE ==="