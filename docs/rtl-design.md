# RTL design notes

See the top-level README for the project overview. This folder holds focused design notes.

| Module | Role |
|---|---|
| `matrix_buffer` / `matrix_buffer_axi` | dual-bank 256-byte frame store + AXI slave + commit |
| `matrix_map_rom` | block/channel -> source index, PCA address, TCA channel |
| `duty_lut_rom` | 5-level intensity -> 12-bit duty + FULL_OFF flag |
| `scheduler_fsm` | scans 256 channels, drives the fetch/render pipeline |
| `block_refresh_buffer` | per-block 16-channel staging before a burst |
| `pca9685_burst_builder` | assembles the 66-byte PCA9685 write burst |
| `tca9548_inject` | mux channel select transaction injection |
| `i2c_master` | open-drain bit engine, START/STOP/ACK, clock-stretch tolerant |
| `pca_init_sequencer` | 16-device init sequence, TCA scan |
| `i2c_fault_monitor` | per-block online / error masks, fault latch |
| `fault_recovery_ctrl` | initialisation and safe-shutdown state machine |
| `haptic_oe_guard` | OE# safety controller |
