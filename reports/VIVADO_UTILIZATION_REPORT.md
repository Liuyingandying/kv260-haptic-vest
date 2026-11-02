# Vivado Utilization Report -- kv260_haptic_top (Phase 1C)

Device: **xck26-sfvc784-2LV-c** (KV260 K26)  |  Mode: **synth_design -mode out_of_context**
Vivado 2025.2  |  Design State: Synthesized  |  Source: `logs/utilization.rpt`

## Absolute utilization
| Resource | Used | Available | Util% |
|---|---:|---:|---:|
| **CLB LUTs** | **1431** | 117,120 | **1.22%** |
| LUT as Logic | 1271 | 117,120 | 1.09% |
| LUT as Memory (distributed RAM) | 160 | 57,600 | 0.28% |
| **CLB Registers (FF)** | **1330** | 234,240 | **0.57%** |
| **Register as Latch** | **0** | 234,240 | **0.00%** |
| CARRY8 | 40 | 14,640 | 0.27% |
| F7 Muxes | 41 | 58,560 | 0.07% |
| F8 Muxes | 1 | 29,280 | <0.01% |
| **Block RAM Tile** | **0** | 144 | 0.00% |
| **DSPs** | **0** | 1248 | 0.00% |
| **Bonded IOB** | **0** | 189 | 0.00% |
| BUFGCTRL / BUFGCE / BUFG_PS | **0** | 32 / 112 / 96 | 0.00% |
| MMCM / PLL | 0 / 0 | 4 / 8 | 0.00% |

## Assessment
- **Tiny footprint**: 1.22% LUT, 0.57% FF. The design uses <2% of the device, so a
  16x16 expansion (16 PCA9685 = 16 I2C target blocks, still one bus) has essentially
  unlimited headroom on the fabric side.
- **No latches inferred** (`Register as Latch = 0`) -- no accidental combinational
  feedback from the my new OE guard or the AXI decode.
- **No BRAM, no DSP** -- all FIFO/ROM state landed in distributed RAM (160 LUTRAM),
  which is expected for the small matrix-map / duty-LUT ROMs.

## Two results that are ARTEFACTS of out_of_context (must not be read as final)
| Item | Value | Why | Needed to be final |
|---|---|---|---|
| **Bonded IOB = 0** | 0 | no PACKAGE_PIN assigned (J2 UNKNOWN). Synthesis emitted `2 CRITICAL WARNING: Converted tricell instance to logic` for the two I2C IOBUFs | a real XDC with confirmed pins -> must show **2 IOB** (SCL, SDA) + 1 OE# IOB |
| **BUFGCTRL = 0** | 0 | `pl_clk0` is a top-level input port in OOC mode; no BUFG is inserted for an input clock in OOC | after BD integration the PS `pl_clk0` drives a BUFG automatically |

## Synthesis quality warnings
- 0 errors, 2 critical warnings (the tricell conversions above), 19 warnings
- Warning classes observed: 62 `no_input_delay`, 71 `no_output_delay` (I/O timing
  unconstrained by design), plus `Port ... unconnected or has no load` for
  intentionally unused debug/status taps (e.g. `s_axi_sts_wstrb`, unused
  `level_data[7:4]`, `fault_clear`, `sched_busy`).
