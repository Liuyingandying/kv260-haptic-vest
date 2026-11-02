# =============================================================================
# kv260_haptic_j2_candidate_v2.xdc   -- PHASE 1G-T  (v2, supersedes v1 for review)
#
# STATUS: CANDIDATE, NOT VERIFIED, NOT DEPLOYABLE.
# v1 (kv260_haptic_j2_candidate.xdc) is PRESERVED and NOT overwritten.
#
# CHANGE vs v1: oe_n is REMOVED from J2. B11 now carries WDOG_HEARTBEAT instead.
# Rationale (Phase 1G-S/T):
#   The official AMD carrier board file assigns 6 of the 8 "J2" bank-45 IOs to
#   i2s_in  (E12, D11, B11) and i2s_out (E10, D10, C11).
#   J2 has only 8 IO.  2 (I2C) + 6 (I2S) = 8 -> no 9th pin for a direct OE#.
#   Therefore OE# is NOT driven directly from J2. Instead a heartbeat is emitted
#   on B11 and an EXTERNAL hardware watchdog derives OE# from it.
#
# EVIDENCE LEVEL PER PIN (see reports/PHASE1G_T_J2_GPIO_WATCHDOG_AUDIT.md):
#   PACKAGE_PIN exists / Bank 45 / LVCMOS33 legal .... CONFIRMED (Vivado DB)
#   PACKAGE_PIN <-> SOM240 net ....................... CONFIRMED (AMD k26c board file)
#   SOM240 net <-> J2 physical pin number ............ UNKNOWN   (needs XTP682)
#
# RTL NOTE: the current RTL has `output wire oe_n`, NOT `wdog_heartbeat`.
#           Adding a heartbeat output is an RTL change and is NOT done in this
#           phase (functional RTL is frozen). This XDC therefore describes the
#           TARGET scheme only and must not be applied to the current RTL.
#
# TODO A: confirm J2 pin numbers for H12/B10/B11 in XTP682
# TODO B: confirm bank45 VCCO on the carrier by measurement
# TODO C: confirm whether J2's 8 IOs carry on-board loads (series R / ESD / pull)
# TODO D: implement `wdog_heartbeat` RTL output before this XDC can be used
# =============================================================================

# ---- J2.1 : I2C SCL, open-drain ---------------------------------------------
# H12 = IO_L4N_AD12N_45 ; SOM240 som240_1_a17 (idx 11) ; bank 45
# FREE: not referenced by any carrier board interface (Phase 1G-S)
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33 PULLTYPE PULLUP} [get_ports i2c_scl_pin]

# ---- J2.2 : I2C SDA, open-drain ---------------------------------------------
# B10 = IO_L9N_AD11N_45 ; SOM240 som240_1_b20 (idx 23) ; bank 45
# FREE: not referenced by any carrier board interface (Phase 1G-S)
set_property -dict {PACKAGE_PIN B10 IOSTANDARD LVCMOS33 PULLTYPE PULLUP} [get_ports i2c_sda_pin]

# ---- J2.8 : WDOG_HEARTBEAT -> external hardware watchdog ---------------------
# B11 = IO_L10P_AD10P_45 ; SOM240 som240_1_c22 (idx 37) ; bank 45
# NOTE: B11 IS assigned to i2s_in by the official carrier board file.
#       Usable ONLY if the optional I2S Pmod function is NOT instantiated.
#       The signal is a ~1 kHz square wave (TBD), 3.3 V LVCMOS33, push-pull.
#       NO PULLTYPE: an internal pull would fight the external watchdog input bias.
set_property -dict {PACKAGE_PIN B11 IOSTANDARD LVCMOS33} [get_ports wdog_heartbeat]

# =============================================================================
# EXTERNAL CIRCUIT (not expressible in XDC) -- see audit report section 3
#   OE#  ----[ 10k ]---- +3.3V            (mandatory: default = DISABLED)
#   OE#  ---- N-MOSFET drain
#   MOSFET source = GND
#   MOSFET gate   = monostable Q  (or inverter of watchdog /WDO)
#   heartbeat -> monostable retrigger input, tau = 50..200 ms  >> heartbeat period
#   On power-up / FPGA unconfigured / clock stop: Q = LOW -> MOSFET off -> OE# HIGH
# =============================================================================