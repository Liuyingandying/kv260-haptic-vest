# RTL Porting Matrix -- AX7020 -> KV260

| Module | AX7020 SHA256 | Copied as | Change | Reuse level |
|---|---|---|---|---|
| matrix_buffer | bf7564d6... | identical | none | direct |
| matrix_buffer_axi | 53dc927c... | identical | none | direct |
| scheduler_fsm | 2c255705... | identical | none | direct |
| block_refresh_buffer | ff873640... | identical | none | direct |
| dirty_detector | 525c1401... | identical | none | direct |
| matrix_map_rom | 5d9ed61f... | identical | none | direct |
| duty_lut_rom | 96ff1cdc... | identical | none | direct |
| pca9685_burst_builder | 3fd53252... | identical | none | direct |
| pca_init_sequencer | 0f228050... | identical | none | direct (CLK_FREQ param) |
| tca9548_inject | 6d51f4a3... | identical | none | direct |
| i2c_fault_monitor | 078c48cf... | identical | none | direct |
| i2c_transaction_builder | cafbc7be... | identical | none | direct |
| axi_status_regs | 57b15b84... | identical | none | direct |
| failsafe_timer | e9f73146... | identical | none | direct (CLK_FREQ param) |
| fault_recovery_ctrl | c6227523... | identical | none | direct |
| i2c_master | b0282ba7... | identical | **none** (only CLK_FREQ param) | direct |
| tactile_v12_ctrl | d12212aa... | identical | none | direct |
| tactile_render_top | ab16f632... | -> `kv260_haptic_pipeline.v` | rename + CLK default + **6 read-only taps** | derived |

## New files (no AX7020 ancestor)
| File | Purpose |
|---|---|
| `rtl/haptic_oe_guard.v` | PCA9685 OE# safety controller (R1-R6) |
| `rtl/kv260_haptic_top.v` | KV260 integration top (pl_clk0/resetn0, single AXI slave, 3-region decode, OE output) |
| `tb/tb_kv260_haptic_top.v` | T1..T12 acceptance testbench |

## Adaptation required vs done
| Dimension | AX7020 | KV260 | Done? |
|---|---|---|---|
| Clock | 50 MHz FCLK | 96.968727 MHz pl_clk0 | YES (param) |
| Reset | proc_sys_reset | pl_resetn0 | YES (sync in guard) |
| AXI | 2x AXI4-Lite slaves | 1 slave + internal decode | YES |
| IO | F17/F16 J11 | **J2 UNKNOWN** | **NO -- blocked** |
| XDC | ax7020_i2c.xdc | not written (no guess) | **NO -- blocked** |
