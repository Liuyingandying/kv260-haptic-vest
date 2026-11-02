# Vivado Timing Report -- kv260_haptic_top (Phase 1C, internal logic only)

Source: `logs/timing_summary.rpt`, `logs/clocks.rpt`, `logs/cdc.rpt`
Device xck26-sfvc784-2LV-c, speed file -2LV, synthesized.

## Clock
```
create_clock -name pl_clk0 -period 10.312 -waveform {0.000 5.156} [get_ports pl_clk0]
=> Clock Summary: pl_clk0  period 10.312 ns  frequency 96.974 MHz
```
Matches the KV260 PS `pl_clk0` = 96.968727 MHz (rounding of 10.3126 ns).

## Design Timing Summary
| Metric | Value | Meaning |
|---|---:|---|
| **WNS** | **+8.292 ns** | worst negative slack, setup (positive = passing) |
| **TNS** | **0.000 ns** | total negative slack (0 = no failing endpoints) |
| TNS failing endpoints | **0** | of 3979 total endpoints |
| **WHS** | **+0.045 ns** | worst hold slack (positive = passing) |
| **THS** | **0.000 ns** | total hold slack |
| THS failing endpoints | **0** | of 3979 |
| WPWS / TPWS | +4.394 / 0.000 | pulse-width, no violations |

**"All user specified timing constraints are met."**

## Path-group analysis
| Path group | Status |
|---|---|
| `pl_clk0` (all internal logic) | **MET**, WNS +8.292 ns |
| AXI control path (`s_axi_*` -> matrix_buffer_axi) | inside `pl_clk0`, MET |
| I2C state machine (`i2c_master`) | inside `pl_clk0`, MET |
| Scheduler -> PCA9685 control (`scheduler_fsm` -> builder -> inject -> master) | inside `pl_clk0`, MET |
| OE# shutdown path (`haptic_oe_guard`) | inside `pl_clk0`, MET, purely combinational-from-status so no CDC |

## Clock domain crossings
- Single clock domain: **`pl_clk0` only** (all 21 RTL files are synchronous to one
  clock). `report_cdc` finds no inter-clock crossings.
- Inside `haptic_oe_guard` there is a 2-FF synchroniser for the active-low reset
  (`rst_sync1/rst_sync2`), marked async-assert / sync-release. This is a reset
  synchroniser, not a data CDC.
- The pipeline's `fault_recovery_ctrl`, `failsafe_timer` and `i2c_fault_monitor`
  are all in `pl_clk0`.

## Unconstrained paths (expected, must be closed with a real XDC)
| Check | Count | Consequence |
|---|---:|---|
| `no_input_delay` | **62** | **I/O setup/hold to external devices NOT analysed** |
| `no_output_delay` | **71** | **I/O setup/hold to external devices NOT analysed** |
| `unconstrained_internal_endpoints` | 0 | internal logic fully constrained |
| `no_clock` / `multiple_clock` / `loops` / `latch_loops` | 0 | clean |

## Verdict
- **Internal logic timing: PASS** (WNS +8.29 ns on a 10.312 ns clock, i.e. the design
  could run far faster than 96.97 MHz).
- **Board-level I/O timing: NOT VERIFIED** -- no pin LOC, no input/output delay.
  This cannot be closed until J2 is confirmed on the bench.
