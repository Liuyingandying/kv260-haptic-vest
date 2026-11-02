# Pre-Burn Checklist (do NOT burn until every line is checked)

## BLOCKING -- must be resolved first
- [ ] **J2 pin mapping confirmed by measurement** (multimeter continuity + official XTP682 page).
      J2 pin-1 orientation is UNKNOWN. No LOC has been guessed anywhere in this project.
- [ ] XDC written with real PACKAGE_PIN values (currently absent by design).
- [ ] Phase 1B testbench ACK-handshake defect fixed; T5/T6/T7 must actually PASS.
- [ ] Vivado synthesis + implementation + timing clean (not run yet).
- [ ] Bitstream generated from a design whose pins are confirmed.

## Electrical pre-conditions (hardware, user-verified)
- [ ] External 4.7k pull-up on SCL and SDA to **3.3V** (never 5V).
- [ ] TCA9548 logic supply 3.3V present, **GND common** with KV260.
- [ ] PCA9685 V+ is an **independent** supply; its rating/current/polarity measured and recorded.
- [ ] **OE# has an external 3.3V pull-up** so an unconfigured FPGA leaves outputs OFF.
- [ ] Actuator driver stage identified; polarity confirmed.
- [ ] Emergency stop = physical removal of V+ (software cannot guarantee it).

## Safety behaviour to confirm before connecting actuators
- [ ] With FPGA unconfigured: OE# reads HIGH (outputs off).
- [ ] After reset: `oe_n` stays HIGH for ARM_DELAY_MS.
- [ ] `sw_oe_req=0` keeps `oe_n` HIGH.
- [ ] A dead I2C bus leaves `oe_n` HIGH.

## Still forbidden after this checklist
- Final bitstream burn, `xmutil loadapp`, JTAG download, real I2C writes, actuator
  connection, any supply change -- **each needs separate explicit authorisation**.
