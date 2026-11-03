# Offline implementation only. Constrain internal PS PL0 against the intended
# 100 MHz Linux runtime budget, exactly as the proven platform pre-opt did.
# This does NOT configure a physical clock or prove the board runtime rate.
if {[llength [get_ports -quiet]] != 2 || [llength [get_ports -quiet {i2c_scl_io i2c_sda_io}]] != 2} {
    error "Expected exactly the two verified board IIC ports"
}
foreach c {scl_meta_reg scl_sync_reg sda_meta_reg sda_sync_reg} {
    set cell [get_cells -quiet $c]
    if {[llength $cell] != 1 || [get_property REF_NAME $cell] ne "FDRE"} {
        error "Expected exact two-stage synchronizer object $c"
    }
    set async_value [get_property ASYNC_REG $cell]
    puts "SYNC_OBJECT=$c REF=[get_property REF_NAME $cell] ASYNC_REG=$async_value"
    if {![string is boolean -strict $async_value] || !$async_value} {
        error "Synchronizer $c does not have a true ASYNC_REG property"
    }
}
foreach {buf meta sync} {u_scl_iobuf scl_meta_reg scl_sync_reg u_sda_iobuf sda_meta_reg sda_sync_reg} {
    if {[get_nets -of_objects [get_pins $buf/O]] ne [get_nets -of_objects [get_pins $meta/D]] ||
        [get_nets -of_objects [get_pins $meta/Q]] ne [get_nets -of_objects [get_pins $sync/D]]} {
        error "Unexpected actual pad-to-synchronizer connection for $buf"
    }
}
set pl [get_clocks -quiet clk_pl_0]
if {[llength $pl] != 1} {error "Expected one generated PS PL0 clock"}
set source_name [get_property SOURCE_PINS $pl]
# PLCLK[0] contains glob metacharacters; exact NAME filtering preserves them.
set source_pin [get_pins -quiet -hier -filter [format {NAME == "%s"} $source_name]]
if {[llength $source_pin] != 1} {error "PS PL0 clock source not unique"}
create_clock -name clk_pl_0 -period 10.000 -waveform {0 5} $source_pin
set_property BITSTREAM.CONFIG.USR_ACCESS 0x554E4931 [current_design]
puts "UNIFIED_INTERNAL_PL0_TIMING_BUDGET_NS=10.000; RUNTIME_CLOCK_NOT_VERIFIED"
