# Architecture

See the top-level README for the project overview. This folder holds focused design notes.

Data path:

```
PS AXI4-Lite -> matrix_buffer_axi -> matrix_buffer (dual bank)
             -> scheduler_fsm      -> matrix_map_rom (block/channel -> source index, PCA addr, TCA ch)
             -> duty_lut_rom       -> block_refresh_buffer -> pca9685_burst_builder
             -> tca9548_inject     -> i2c_master -> IOBUF -> pads
```

Safety blocks: `failsafe_timer` (no-frame timeout), `i2c_fault_monitor` (per-block ACK tracking),
`fault_recovery_ctrl` (INIT/RUN/QUIESCE/OFF_SCAN/BACKOFF/HOLD/REINIT), `haptic_oe_guard` (OE#).
