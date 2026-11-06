# Source from a NEW project created by the caller. No original project is opened
# or saved. No PS initialization code, AFI/OEN writes, or hardware is executed.
if {![info exists board_root]} {error "Caller must define board_root"}
if {[llength [get_projects -quiet]] != 1} {error "Create independent board project first"}
set board_root [file normalize $board_root]
set evidence [file join $board_root evidence]
file mkdir $evidence
source [file join $board_root platform ps_baseline_parameters.tcl]
if {[llength [get_bd_designs -quiet kv260_unified_ps]]} {error "Independent BD already exists"}
create_bd_design kv260_unified_ps
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:3.5 zynq_ultra_ps_e_0]

# The original BD records 34 explicit changes; the original XCI records all
# 1692 values. Apply the exact BD changes to the same IP version and K26 part,
# then compare actual Vivado properties with the original complete XCI below.
set_property -dict $ps_baseline_explicit $ps
if {[get_property CONFIG.PSU__MAXIGP2__DATA_WIDTH $ps] != 128} {error "PS HPM0 LPD width changed"}
if {[get_property CONFIG.PSU__USE__M_AXI_GP2 $ps] != 1} {error "PS HPM0 LPD disabled"}
if {[get_property CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ $ps] != 100} {error "PS PL0 requested frequency changed"}
if {[get_property CONFIG.PSU__FPGA_PL0_ENABLE $ps] != 1} {error "PS PL0 disabled"}

set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:1.0 smartconnect_0]
set_property -dict [list CONFIG.NUM_SI 1 CONFIG.NUM_MI 3] $sc
set gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:2.0 diag_gpio]
set_property -dict [list CONFIG.C_IS_DUAL 1 CONFIG.C_GPIO_WIDTH 32 CONFIG.C_ALL_INPUTS 1 \
  CONFIG.C_GPIO2_WIDTH 32 CONFIG.C_ALL_INPUTS_2 0 CONFIG.C_DOUT_DEFAULT_2 0x00000000 \
  CONFIG.C_TRI_DEFAULT_2 0xFFFFFFFF] $gpio
# FCH1: channel1 reads the top-level measurement multiplexer, page0=0x46434831.
# This changes no PS parameter, USB pin, clock, reset or address mapping.
create_bd_port -dir I -from 31 -to 0 diag_measure_i
connect_bd_net [get_bd_ports diag_measure_i] [get_bd_pins diag_gpio/gpio_io_i]

foreach {name direction} {diag_status_i I diag_ctrl_o O diag_ctrl_t O} {
  create_bd_port -dir $direction -from 31 -to 0 $name
}
# Same dual-channel GPIO semantics as the proved original platform:
# +0 = paged diagnostic input; +8 = status input / data2 output; +0xC = direction2.
connect_bd_net [get_bd_ports diag_status_i] [get_bd_pins diag_gpio/gpio2_io_i]
connect_bd_net [get_bd_ports diag_ctrl_o] [get_bd_pins diag_gpio/gpio2_io_o]
connect_bd_net [get_bd_ports diag_ctrl_t] [get_bd_pins diag_gpio/gpio2_io_t]

create_bd_port -dir O -type clk pl_clk0
# Static metadata matches original BD. Board build additionally constrains the
# known Linux runtime PL0 rate at 100 MHz; this is not a new PS init procedure.
set_property -dict [list CONFIG.FREQ_HZ 96968727 \
  CONFIG.ASSOCIATED_BUSIF M_AXI_PERCEPTION:M_AXI_STATUS \
  CONFIG.ASSOCIATED_RESET pl_bus_resetn] [get_bd_ports pl_clk0]
create_bd_port -dir O -type rst pl_resetn0
set_property CONFIG.POLARITY ACTIVE_LOW [get_bd_ports pl_resetn0]
create_bd_port -dir I -type rst pl_bus_resetn
set_property CONFIG.POLARITY ACTIVE_LOW [get_bd_ports pl_bus_resetn]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_ports pl_clk0] \
  [get_bd_pins zynq_ultra_ps_e_0/maxihpm0_lpd_aclk] \
  [get_bd_pins smartconnect_0/aclk] [get_bd_pins diag_gpio/s_axi_aclk]
# Retain original healthy platform's top-level 16-cycle startup reset topology.
# Do not silently replace it with PS pl_resetn0/proc_sys_reset.
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_ports pl_resetn0]
connect_bd_net [get_bd_ports pl_bus_resetn] [get_bd_pins smartconnect_0/aresetn] \
  [get_bd_pins diag_gpio/s_axi_aresetn]

connect_bd_intf_net [get_bd_intf_pins zynq_ultra_ps_e_0/M_AXI_HPM0_LPD] \
  [get_bd_intf_pins smartconnect_0/S00_AXI]
foreach {name mi} {M_AXI_PERCEPTION M00_AXI M_AXI_STATUS M01_AXI} {
  set port [create_bd_intf_port -mode Master -vlnv xilinx.com:interface:aximm_rtl:1.0 $name]
  set_property -dict [list CONFIG.PROTOCOL AXI4LITE CONFIG.ADDR_WIDTH 32 \
    CONFIG.DATA_WIDTH 32 CONFIG.HAS_BURST 0 CONFIG.HAS_LOCK 0 \
    CONFIG.HAS_CACHE 0 CONFIG.HAS_QOS 0 CONFIG.HAS_REGION 0 \
    CONFIG.NUM_READ_OUTSTANDING 1 CONFIG.NUM_WRITE_OUTSTANDING 1] $port
  connect_bd_intf_net [get_bd_intf_pins smartconnect_0/$mi] $port
}
connect_bd_intf_net [get_bd_intf_pins smartconnect_0/M02_AXI] [get_bd_intf_pins diag_gpio/S_AXI]
assign_bd_address -offset 0x9F000000 -range 0x00010000 \
  [get_bd_addr_segs M_AXI_PERCEPTION/Reg] \
  -target_address_space [get_bd_addr_spaces zynq_ultra_ps_e_0/Data]
assign_bd_address -offset 0x9C000000 -range 0x00010000 \
  [get_bd_addr_segs M_AXI_STATUS/Reg] \
  -target_address_space [get_bd_addr_spaces zynq_ultra_ps_e_0/Data]
assign_bd_address -offset 0x9E000000 -range 0x00010000 \
  [get_bd_addr_segs diag_gpio/S_AXI/Reg] \
  -target_address_space [get_bd_addr_spaces zynq_ultra_ps_e_0/Data]

validate_bd_design

# Compare actual object values rather than claiming byte-copied metadata proves
# implementation. Component_Name is expected to change because this is a NEW BD.
proc ps_canonical {value} {
  set v [string trim $value]
  if {[string equal -nocase $v true]} {return 1}
  if {[string equal -nocase $v false]} {return 0}
  if {[regexp {^(0x[0-9a-fA-F]+|[0-9]+)$} $v]} {
    if {![catch {expr {wide($v)}} n]} {return $n}
  }
  return $v
}
set fp [open [file join $evidence PS_CONFIG_OBJECT_COMPARISON.tsv] w]
puts $fp "property\tstatus\texpected\tactual"
set properties [list_property $ps]
set mismatches 0
set gui_differences 0
set unavailable 0
set checked 0
foreach {prop expected} $ps_baseline_all {
  if {[lsearch -exact $properties $prop] < 0} {
    puts $fp "$prop\tNOT_EXPOSED_BY_BD\t$expected\t"
    incr unavailable
    continue
  }
  set actual [get_property $prop $ps]
  incr checked
  if {$prop eq "CONFIG.Component_Name"} {
    puts $fp "$prop\tNEW_INSTANCE_NAME\t$expected\t$actual"
  } elseif {($prop eq "CONFIG.PSU_MIO_TREE_PERIPHERALS" || $prop eq "CONFIG.PSU_MIO_TREE_SIGNALS") && [regexp {^#+$} $expected] && $actual eq ""} {
    # AMD xgui/guiparamlist.tcl classifies exactly these two as textEdit widgets.
    # Cache import may omit the display placeholder; preserve and report it,
    # never normalize an actual PSU__ MIO/USB/clock/reset configuration difference.
    puts $fp "$prop\tGUI_TEXT_METADATA_DIFF\t$expected\t$actual"
    incr gui_differences
  } elseif {[ps_canonical $expected] ne [ps_canonical $actual]} {
    puts $fp "$prop\tDIFF\t$expected\t$actual"
    incr mismatches
  } else {puts $fp "$prop\tMATCH\t$expected\t$actual"}
}
close $fp
puts "PS_BASELINE_OBJECT_COMPARISON checked=$checked not_exposed=$unavailable hardware_mismatches=$mismatches gui_text_differences=$gui_differences"
if {$mismatches != 0} {error "PS configuration differs from proved baseline; inspect comparison before build"}

set fp [open [file join $evidence BOARD_BD_OBJECTS.txt] w]
puts $fp "PS_WIDTH=[get_property CONFIG.PSU__MAXIGP2__DATA_WIDTH $ps]"
puts $fp "PS_PL0_REQUEST_MHZ=[get_property CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ $ps]"
puts $fp "PS_PL0_STATIC_METADATA_MHZ=[get_property CONFIG.PSU__CRL_APB__PL0_REF_CTRL__ACT_FREQMHZ $ps]"
puts $fp "AXI_M00=[get_bd_intf_nets -of_objects [get_bd_intf_pins smartconnect_0/M00_AXI]]"
puts $fp "AXI_M01=[get_bd_intf_nets -of_objects [get_bd_intf_pins smartconnect_0/M01_AXI]]"
puts $fp "AXI_M02=[get_bd_intf_nets -of_objects [get_bd_intf_pins smartconnect_0/M02_AXI]]"
foreach seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces zynq_ultra_ps_e_0/Data]] {
  puts $fp "SEGMENT=$seg OFFSET=[get_property OFFSET $seg] RANGE=[get_property RANGE $seg]"
}
puts $fp "GPIO_PAGE0_IDENTITY=0x52364331; DEFAULT_DATA2=0; DEFAULT_TRI2=FFFFFFFF"
puts $fp "USB_EMIO_OVERCURRENT=REQUIRES_POST_SYNTH_GND_PROOF_NOT_INFERRED_FROM_CONFIG_ONLY"
close $fp
save_bd_design
set bd [get_files -quiet */kv260_unified_ps.bd]
if {[llength $bd] != 1} {error "New board BD is not unique"}
generate_target all $bd
set wrapper [make_wrapper -files $bd -top]
add_files -norecurse $wrapper
write_bd_tcl -force [file join $evidence kv260_unified_ps_snapshot.tcl]
# Export actual assigned objects; assign_bd_address is not a report command.
set addr_report [open [file join $evidence address_map.rpt] w]
foreach seg [get_bd_addr_segs -of_objects [get_bd_addr_spaces zynq_ultra_ps_e_0/Data]] {
  puts $addr_report "SEGMENT=$seg OFFSET=[get_property OFFSET $seg] RANGE=[get_property RANGE $seg]"
}
close $addr_report
puts "UNIFIED_BOARD_BD_READY WRAPPER=$wrapper"
