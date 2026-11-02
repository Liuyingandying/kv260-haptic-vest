# KV260 Haptic Phase 1C Report -- Synthesis / Resources / Timing / AXI

**Date**: 2026-11-01  |  **Scope**: isolated project only
**Status**: **B/C PASS** (synthesis + internal timing). **D/E/F NOT VERIFIED.**
Supplementary: PWM gap CLOSED, positive-OE gap STILL OPEN.

## 0. Compliance
| Rule | Status |
|---|---|
| Uses the 21/21-passing RTL | YES (unchanged) |
| AX7020 original untouched | YES (HEAD 287e2ee, status 39 lines) |
| KV260 frozen project untouched | YES |
| Functional RTL modified | **NO** (0 of 17 migrated files changed) |
| J2 pin mapping guessed | **NO** (constraint file has create_clock only, zero LOC) |
| bitstream / burn / xmutil loadapp | **NOT EXECUTED** |
| real I2C / actuator | **NOT EXECUTED** |

## 1. Synthesis
`synth_design -top kv260_haptic_top -part xck26-sfvc784-2LV-c -mode out_of_context`
- **0 errors**, 2 critical warnings, 19 warnings, 139.2 s
- 2 critical warnings = `Converted tricell instance to logic` (the two I2C IOBUFs cannot
  map to real IOBs without a PACKAGE_PIN). **Expected in OOC mode.**
- No latches inferred (`Register as Latch = 0`)
- No unintended logic removal: all debug/status taps reported as "unconnected or no load"
  are the ones intentionally left floating (`s_axi_sts_wstrb`, `level_data[7:4]`,
  `fault_clear`, `sched_busy`) -- verified against the connectivity in the reports.

## 2. Resources (see VIVADO_UTILIZATION_REPORT.md)
| | Used | Util% |
|---|---:|---:|
| CLB LUTs | 1431 | **1.22%** |
| CLB Registers | 1330 | **0.57%** |
| Register as Latch | **0** | 0.00% |
| Block RAM | 0 | 0% |
| DSP | 0 | 0% |
| IOB | 0 (OOC artefact) | -- |
| BUFG | 0 (OOC artefact) | -- |

## 3. Timing (see VIVADO_TIMING_REPORT.md)
| | Value |
|---|---|
| **WNS** | **+8.292 ns** |
| **TNS** | **0.000 ns**, 0 failing endpoints of 3979 |
| **WHS** | **+0.045 ns** |
| **THS** | **0.000 ns** |
| Verdict | **All user specified timing constraints are met** |
| Unconstrained | 62 `no_input_delay` + 71 `no_output_delay` (no pins by design) |
| CDC | single clock domain `pl_clk0`; only a reset synchroniser in the OE guard |

## 4. AXI audit (see AXI_INTERFACE_AUDIT.md)
- 3 decoded regions, **no overlap**; region `2'b11` unmapped -> would stall a PS access (**finding F1**, reported, not fixed)
- `pl_clk0` = 96.968727 MHz matches the 10.312 ns constraint
- I2C divider = **60** -> **404.04 kHz** SCL (Fast-mode compliant)
- No combinatorial loops, no latch in the decode

## 5. Supplementary verification (see KV260_HAPTIC_SUPPLEMENTARY_TEST_REPORT.md)
| Gap | Status |
|---|---|
| PWM 20 ms / 50 Hz waveform | **CLOSED** -- measured period 20.65 ms, 25.0% duty (engine model) |
| Positive OE enable (init_healthy -> oe_n=0) | **STILL OPEN** -- `init_healthy` never asserts; `reads=0` disproves the read-back theory |

## 6. Final status matrix (do NOT infer D/E/F from A/B/C)
| Level | Status | Evidence |
|---|---|---|
| **A. RTL simulation** | **PASS** | 21/21 checks, `logs/sim_revB_final.log` |
| **B. Vivado synthesis** | **PASS** | 0 errors; `logs/vivado_synth.log` |
| **C. Internal timing** | **PASS** | WNS +8.292, TNS 0, `logs/timing_summary.rpt` |
| **D. Board-level I/O timing** | **NOT VERIFIED** | no LOC, no I/O delay; 62+71 unconstrained checks |
| **E. Real I2C communication** | **NOT VERIFIED** | nothing connected, no real bus |
| **F. Actual actuator vibration** | **NOT VERIFIED** | no actuator, no supply |

## 7. Open items before hardware
1. **F1**: unmapped AXI region 0x3000-0x3FFF stalls instead of returning SLVERR (guard).
2. **Positive OE path unproven**: `init_healthy` never asserts in sim; needs internal
   init-signal dump to separate model fidelity from an RTL issue.
3. **J2 pin mapping UNKNOWN** -> D cannot be closed, and no bitstream is permissible.
4. IOB inference must be re-verified with a real XDC (expect 3 IOBs: SCL, SDA, OE#).

**Stopped here as instructed -- waiting for bench pin confirmation and hardware test
authorisation.**
