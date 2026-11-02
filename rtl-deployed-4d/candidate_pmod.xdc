# Candidate package assignments only. Physical connector numbering/orientation
# is third-party corroborated and must be confirmed before connecting a bus.
# Official BIST: HDA11=H12 and HDA15=B10, bank45 LVCMOS33.
set_property PACKAGE_PIN H12 [get_ports i2c_scl_io]
set_property PACKAGE_PIN B10 [get_ports i2c_sda_io]
set_property IOSTANDARD LVCMOS33 [get_ports {i2c_scl_io i2c_sda_io}]
set_property DRIVE 4 [get_ports {i2c_scl_io i2c_sda_io}]
set_property SLEW SLOW [get_ports {i2c_scl_io i2c_sda_io}]

# Phase 2F: internal pull-ups (~25k) so the open-drain bus has a defined idle level
set_property PULLUP TRUE [get_ports {i2c_scl_io}]
set_property PULLUP TRUE [get_ports {i2c_sda_io}]

# Phase R2-MI4: HD-bank IO requires explicit config-bank voltage (cf. AX7020 xdc)
set_property CFGBVS VCCO [current_design]
set_property CONFIG_VOLTAGE 3.3 [current_design]
