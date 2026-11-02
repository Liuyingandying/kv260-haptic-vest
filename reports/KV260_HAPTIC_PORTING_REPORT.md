# KV260 Haptic Porting -- Phase 1 Report

**Date**: 2026-11-01  **Dir**: `KV260_Haptic_Port_20261101` (isolated)
**Status**: Phase 1A **PASS** (compile + elaborate) | Phase 1B **NOT PASS** (16/21 checks, 5 fail -- testbench defect) | Phase 1C **PARTIAL**

## 1. Protection compliance
| Item | Result |
|---|---|
| AX7020 original project unmodified | YES (`git status` = 39 lines, same as H0) |
| KV260 frozen project unmodified | YES (read-only) |
| Isolated dir created | `KV260_Haptic_Port_20261101/` |
| AX7020 8 modified RTL kept + SHA recorded | YES -> `reports/ax7020_rtl_copied_sha256.json` |
| git reset/checkout/clean | NOT EXECUTED |
| bitstream / loadapp / JTAG / real I2C / actuators | NOT EXECUTED |

## 2. What was built
1. 18 AX7020 RTL files copied **from the audited working tree** (not from a tag); every copy SHA == source SHA.
2. `kv260_haptic_pipeline.v` = additive derivative of `tactile_render_top.v`
   - module renamed, `CLK_FREQ_HZ` default -> 96_968_727
   - **4+2 read-only status taps added** (`o_init_healthy`, `o_fault_latched`, `o_force_off`, `o_runtime_run`, `o_online_mask`, `o_i2c_error_count`)
   - **zero pipeline logic changed** (`assign init_healthy = init_done && ...` still present verbatim)
3. `haptic_oe_guard.v` = **NEW** independent PCA9685 OE# safety controller (R1-R6).
4. `kv260_haptic_top.v` = **NEW** KV260 top: `pl_clk0`/`pl_resetn0`, ONE AXI4-Lite slave with 3-region decode (0x0 ctrl / 0x1000 sts / 0x2000 OE reg), open-drain I2C pads, OE# output.
5. `tb_kv260_haptic_top.v` = **NEW** acceptance testbench (T1..T12).

## 3. i2c_master adaptation -- no code change needed
All modules are already parameterised by `CLK_FREQ_HZ`:
```
TICKS = CLK_FREQ_HZ / (4 * I2C_FREQ_HZ) = 96_968_727 / 1_600_000 = 60
actual SCL = 96_968_727 / (4*60) = 404_036 Hz  (+1.0% vs 400 kHz, inside Fast-mode tolerance)
```
So the AX7020 `i2c_master.v` is used **byte-identical**; only parameters change.

## 4. Real results
| Step | Result |
|---|---|
| xvlog (21 RTL files) | **exit 0**, zero errors |
| xelab tb_kv260_haptic_top | **Built simulation snapshot tb_sim** |
| xsim run (9.1 s) | ran to `$finish`, 21 checks |
| **checks passed** | **16 / 21** |
| **checks failed** | **5** (TCA mask value, T5, T5b, T6, T7) |

## 5. Honest status
Phase 1B is **NOT PASS**. The 5 failures are traced to **my testbench's I2C slave model**, not to the RTL:
- run 1: address-phase `bitcnt` not cleared -> corrupted byte decode (98 spurious STARTs)
- after fix: TCA select now PASSES, but the **ACK is never released** (SDA held low), so the bus hangs after the first transaction (`starts=1`)

Minimal fix (already located, not yet applied): add an `ack_pending` flag set on the ACK negedge and cleared on the **next SCL rising edge**, independent of `bitcnt`.

**Therefore T5 (PCA9685 init), T6 (PWM register write) and T7 (PWM waveform) are NOT yet verified.** Do not read them as passing.

## 6. Safety design delivered (new)
| Requirement | Implementation | Verified |
|---|---|---|
| R1 independent OE# | dedicated `oe_n` output, only `haptic_oe_guard` drives it | T8a PASS |
| R2 default OFF | `oe_n <= 1'b1` on reset | T8a PASS |
| R3 unconfigured/reset/abnormal -> OFF | external pull-up + reset default | T8a/T9a/T9b PASS |
| R4 enable only after init healthy | `init_healthy && !fault && !force_off && sw_req`, sustained ARM_DELAY_MS | T8c PASS (correct fail-closed) |
| R5 NACK/failsafe -> OFF | combinational from status taps | T10/T10b PASS |
| R6 independent of I2C | disable needs no bus transaction | T11 PASS |
| I2C open-drain only | DUT drives low / releases high-Z (IOBUF), TB models pull-ups | structurally present |

## 7. Blockers to close Phase 1B
1. **Testbench ACK handshake** (definite, fix identified above).
2. **J2 pin mapping remains UNKNOWN** -- no LOC guessed anywhere; XDC not written (`phase1c` item).
3. Vivado synthesis not run (optional per task; also needs an acyclic device/pin story).

No hardware was touched. Nothing was loaded onto the board.
