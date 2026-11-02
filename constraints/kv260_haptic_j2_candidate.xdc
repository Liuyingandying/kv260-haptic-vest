# =============================================================================
# kv260_haptic_j2_candidate.xdc   -- PHASE 1G CANDIDATE CONSTRAINTS
#
# STATUS: CANDIDATE, NOT VERIFIED.  DO NOT USE FOR A DEPLOYABLE BITSTREAM.
#
# Phase 1G-R UPDATE (2026-11-01): official AMD board file evidence added.
#   Source: Vivado 2025.2 -> data/xhub/boards/XilinxBoardStore/boards/Xilinx/
#           k26c/1.4/part0_pins.xml  (Copyright (C) 2023 AMD, Apache-2.0,
#           part_name="XCK26-SFVC784-2LV-C")
#   Establishes: PACKAGE_PIN <-> SOM240 connector net
#       H12 -> som240_1_a17 (SOM240 idx 11)
#       B10 -> som240_1_b20 (SOM240 idx 23)
#       B11 -> som240_1_c22 (SOM240 idx 37)
#   STILL MISSING: SOM240 net -> J2 pin number  (needs XTP682 carrier schematic)
#
# Evidence that IS established (Vivado 2025.2 device database + place_design):
#   * part xck26-sfvc784-2LV-c contains all pins below; each is an IO pin
#   * all three belong to IOBANK 45
#   * IOSTANDARD LVCMOS33 is accepted on bank 45 (place_design completed clean
#     with these exact constraints; see phase1g/place_lvcmos33_probe.log)
#
# Evidence that is NOT established (kept as TODO, no guessing):
#   TODO 1: J2 connector pin-1 physical position and orientation   -- UNKNOWN
#   TODO 2: which J2 pin number each package pin lands on           -- UNKNOWN
#   TODO 3: actual bank 45 VCCO on the KV260 carrier                -- UNKNOWN
#   TODO 4: J2 VCC pin voltage (not necessarily equal to VCCO)      -- UNKNOWN
#   TODO 5: presence of on-board pull-ups / series resistors        -- UNKNOWN
#   TODO 6: which package pin is actually wired to a J2 IO          -- UNKNOWN
#   TODO 8: 6 of the 8 third-party "J2 pins" are assigned by the official carrier
#           board file to i2s_in (E12,D11,B11) and i2s_out (E10,D10,C11).
#           Only H12 and B10 are FREE. => oe_n on B11 CONFLICTS with i2s_in.
#           DECISION PENDING: J2 has only 8 IO, so I2C(2) + OE(1) + I2S(6) = 9 > 8.
#   TODO 9: BoardStore gives NO J2 pin numbers and NO HDA net names; rev1.0 vs
#           rev2.0 circuit differences UNKNOWN.
#   RESOLVED: HDA IIC SWITCH conflict is CLOSED at net level -- it uses
#           som240_1_d16/d17 = G11/F10, disjoint from all 8 candidates.
#
#   TODO 7: whether HDA11/HDA15 pass through the carrier "HDA IIC SWITCH"
#           (found in kv260_carrier/1.3/board.xml L281). If they do, a PL-side
#           open-drain I2C master CONFLICTS with that on-board mux.      -- UNKNOWN
#
# Because of TODO 1-6 these assignments MUST NOT be treated as a wiring
# instruction. They only reserve a legal, bank-consistent candidate set.
#
# RTL port names are taken verbatim from rtl/kv260_haptic_top.v:
#   line 57  inout  wire i2c_scl_pin
#   line 58  inout  wire i2c_sda_pin
#   line 61  output wire oe_n          (oe_n = 1 -> PCA9685 OE# HIGH -> outputs OFF)
# =============================================================================

# ---- I2C SCL: open-drain, FPGA only pulls low or releases --------------------
# Candidate pin H12 = IO_L4N_AD12N_45 (bank 45) / SOM240 som240_1_a17
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33 PULLTYPE PULLUP} [get_ports i2c_scl_pin]

# ---- I2C SDA: open-drain, FPGA only pulls low or releases --------------------
# Candidate pin B10 = IO_L9N_AD11N_45 (bank 45) / SOM240 som240_1_b20
set_property -dict {PACKAGE_PIN B10 IOSTANDARD LVCMOS33 PULLTYPE PULLUP} [get_ports i2c_sda_pin]

# ---- HAPTIC OE# : FPGA output, ACTIVE LOW enable ----------------------------
# Candidate pin B11 = IO_L10P_AD10P_45 (bank 45) / SOM240 som240_1_c22
#   oe_n = 1 => OE# high => PCA9685 outputs disabled (safe default)
#   oe_n = 0 => OE# low  => PCA9685 outputs enabled
# Pin chosen to be NON clock-capable (not HDGC) so the clock-capable pins
# E10/D10/E12/D11 in this bank stay free for future clocking.
# EXTERNAL 3.3V PULL-UP IS MANDATORY: while the FPGA is unconfigured the pin is
# high-Z, and only the external pull-up guarantees OE# = HIGH = outputs OFF.
set_property -dict {PACKAGE_PIN B11 IOSTANDARD LVCMOS33} [get_ports oe_n]

# ---- Do NOT add PULLTYPE to oe_n -------------------------------------------
# An internal pull-up would fight the external pull-up; an internal pull-down
# would ENABLE the outputs while unconfigured. Leave it alone.

# =============================================================================
# REMINDER -- external circuit requirements (not expressible in XDC):
#   1. 4.7k pull-up from SCL to 3.3V and SDA to 3.3V   (mandatory)
#   2. 4.7k-10k pull-up from OE# to 3.3V               (mandatory, safety)
#   3. All pull-ups must reference the SAME 3.3V rail that matches bank45 VCCO
#   4. Common ground between KV260 and the haptic board
#   5. PCA9685 V+ (actuator) MUST be a separate supply from VDD (logic)
# =============================================================================