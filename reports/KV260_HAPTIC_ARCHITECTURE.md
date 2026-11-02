# KV260 Haptic Architecture (Phase 1)

```
 KV260 PS (ZynqMP)
   |  M_AXI (AXI4-Lite, single port)
   v
 kv260_haptic_top
   |-- addr[13:12]=00 --> pipeline ctrl (matrix_buffer_axi 0x000-0x3FF)
   |-- addr[13:12]=01 --> pipeline sts  (axi_status_regs)
   |-- addr[13:12]=10 --> OE control register (0x2000)
   |
   +--> kv260_haptic_pipeline (= AX7020 tactile_render_top, logic unchanged)
          matrix_buffer_axi -> matrix_buffer (dual bank)
          -> dirty_detector -> block_refresh_buffer -> scheduler_fsm
          -> matrix_map_rom / duty_lut_rom
          -> pca9685_burst_builder -> tca9548_inject -> i2c_master
          -> pca_init_sequencer (arbitration) -> i2c_master
          -> i2c_fault_monitor / fault_recovery_ctrl / failsafe_timer
          |
          +-- status taps: init_healthy, fault_latched, force_off, runtime_run, online_mask, i2c_err_cnt
   |
   +--> haptic_oe_guard  --(oe_n)--> external PCA9685 OE# (3.3V pull-up, LOW = enabled)
   |
   +--> i2c_scl_pin / i2c_sda_pin  (open-drain, external 4.7k to 3.3V)
                |
                v
        TCA9548 @0x70 (ch0..3)  ->  16 x PCA9685 @0x40..0x4F  ->  256 PWM
```

## Safety chain
```
init_healthy  --\
fault_latched ---+--> haptic_oe_guard --> oe_n (1=OFF)
force_off     ---/         |
sw_oe_req ----/            +-- combinational: a hung I2C bus CANNOT keep output on
                           +-- reset/default: oe_n=1 (OFF)
                           +-- external pull-up: FPGA unconfigured => OFF
```

## Explicit non-claims
- No physical pin is assigned. **J2 mapping is UNKNOWN** (inherited from H0).
- No bitstream was generated or loaded.
- 16x16 full-array operation is architected but not verified.
