# Verification

See the top-level README for the project overview. This folder holds focused design notes.

## Simulation

- Full 16-device initialisation against behavioural TCA9548A + 16x PCA9685 models: PASS.
- Fault injection matrix (all online / 1 online / 8 online / one channel offline / first NACK /
  no TCA / all NACK): all behaved as expected.
- End-to-end single channel: intensity 4 produced `pwm = 0x0FF0`, `full_off = 0`; the emitted
  I2C burst carried `OFF_H:OFF_L = 0x0F:0xF0`; `PRE_SCALE = 121` gives a 50.4 Hz PWM frame rate.

## Build

Synthesis, implementation, routing, DRC and timing all pass. See `reports/`.

## Hardware

The bitstream loads and runs, and AXI registers are readable from Linux. **External I2C has not
been verified** - no device on the bus has ever returned an ACK for this design.
