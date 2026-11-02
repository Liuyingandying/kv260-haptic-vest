# Phase 1F -- PCA9685 Init Delay Counter Width Fix

**Date**: 2026-11-01  **Scope**: `KV260_Haptic_Port_20261101` only
**Result**: **fix verified at both clocks; full regression green**

## 1. Baseline (recorded BEFORE the change)
| Item | Value |
|---|---|
| File | `rtl/pca_init_sequencer.v` |
| Bytes / SHA256 before | 16,868 / `0f22805029197dcaed22009798f2df56928c8cd3756148fa7780c613d4f35e3b` |
| AX7020 source SHA256 | `0f22805029197dcaed22009798f2df56928c8cd3756148fa7780c613d4f35e3b` (**identical** -> the isolated copy was byte-exact) |
| AX7020 git | HEAD `287e2ee3365a3c0d0ed151d1363a72841b22d6b7`, branch `codex/phase1-fpga-p0-fault-chain`, status 39 lines |
| Backend | preserved as `phase1f/pca_init_sequencer.v.BEFORE_PHASE1F` |
| Bytes / SHA256 after | 17,190 / `a245e27123b9ad749cae6dddfe005061d46d25eb7035dbef0935b17826c1eeaf` |

## 2. Code change (exactly 4 code lines + comments)

| # | Line (before) | Before | After |
|---|---|---|---|
| 1 | 240 | `reg [15:0] dly;` | `reg [31:0] dly;` |
| 2 | 292 | `dly <= 16'd0;` | `dly <= 32'd0;` |
| 3 | 381 | `if (dly == 16'd0)` | `if (dly == 32'd0)` |
| 4 | 386 | `dly <= dly - 16'd1;` | `dly <= dly - 32'd1;` |
| 5 | 77 | `DLY_1MS = CLK_FREQ/1000` (unchanged) | same + explanatory comment |

**Nothing else was touched**: no transaction order change, no I2C protocol change,
no PWM configuration change, no OE safety logic change, no FSM state change.
Pre/Post diff is available via `fc /n` in `phase1f/` artifacts.

## 3. Two clock frequencies: cycle count vs actual delay

| Clock | `DLY_1MS = CLK/1000` | Legal in 32-bit | 16-bit would wrap to | Expected | **Measured (fixed)** | Verdict |
|---|---:|---|---:|---:|---:|---|
| **50,000,000 Hz** (AX7020) | 50,000 | yes | 50,000 (no wrap) | 1000.000 us | **1023.660 us** | PASS |
| **96,968,727 Hz** (KV260) | 96,968 | yes | **31,432 -> 324.1 us** | 999.993 us | **1025.064 us** | PASS |

Measured value exceeds the nominal `DLY_1MS` by ~2.4-2.5% because the measurement
window is *wake-transaction STOP -> next transaction ADDR*, which includes the
inter-transaction overhead (START setup + bus-free wait), not only the counter.

**No truncation / no wrap-around**: 32-bit counter holds both values exactly.

## 4. Pre-fix defect reproduction (controlled comparison)
Same testbench, same testbench parameters, **only the `dly` width differs**:

| RTL under test | Clock | Measured wake->restart | Blocks >= 500 us | Result |
|---|---|---:|---:|---|
| **pre-fix** (backup) | 96,968,727 Hz | **349.257 us** | **0 / 16** | **FAIL** |
| **post-fix** | 96,968,727 Hz | **1025.064 us** | **16 / 16** | **PASS** |
| post-fix | 50,000,000 Hz | 1023.660 us | 16 / 16 | PASS |

349.257 us (measured) vs 324.1 us (predicted from 31,432 cycles) differ by ~25 us of
inter-transaction overhead -- consistent, and the prediction is confirmed.

## 5. Per-block wait times (16/16 individual measurements)

**Post-fix @ 96,968,727 Hz** -- all 16 blocks identical:
```
BLK00..BLK15 : 1025.064 us each,  1 measurement per block
min = 1025.064 us   max = 1025.064 us   mean = 1025.064 us
blocks with delay >= 500 us : 16 / 16
```
**Post-fix @ 50,000,000 Hz** -- all 16 blocks: `1023.660 us` each, 16/16 >= 500 us.

Individual per-block values are in `phase1f/delay_fixed_96968.log` and
`phase1f/delay_fixed_50m.log` (16 rows each).

## 6. Full regression (all re-run against the fixed RTL)

| Regression | Result | Log |
|---|---|---|
| Phase 1B 21-item | **21/21 PASS** (checks=21 errors=0), assertions untouched | `p1f_21item.log` |
| Phase 1D I2C timing | **PASS** -- min SCL period 2639.9 ns (378.8 kHz), t_LOW 1319.9 ns, t_HIGH 1319.9 ns, SDA setup 1309.6 ns, START 1319.9 ns, STOP 5321 ns | `p1f_phase1e.log` |
| Phase 1E register image | **PASS** -- 11/11 checks, 16/16 blocks `PRE_SCALE=0x79`, `MODE2=0x04`, first `MODE1=0x30`, final `MODE1=0xA0` | `p1f_phase1e.log` |
| 256-channel FULL_OFF | **PASS** -- `FULL_OFF violations: 0 / 256` (snapshot at `init_done`) | `p1f_phase1e.log` |
| Fault injection F1/F2/F4/F3 | **PASS** -- 4/4 CAUGHT with specific device/register/channel | decoder output |
| OE safety (T8a/T8b/T8c/T9a/T9b/T10/T10b/T11/T12) | **PASS** -- inside the 21-item run | `p1f_21item.log` |

**Init time change: 37.474 ms -> 48.287 ms (+10.813 ms).**
This is exactly what the fix should do: 16 blocks x (1025.06 - 349.26) us = **10.813 ms**.
The prediction matches the measurement to the millisecond, which is independent
confirmation that the delay is now real rather than truncated.

### Time budget after the fix
```
1330 clocked bytes x 9 SCL periods x 1320 ns   = 15.80 ms
16 wake delays x 1025.06 us                     = 16.40 ms
                                   subtotal     = 32.20 ms
measured total                                  = 48.29 ms
residual (113 transactions of frame overhead)   = 16.09 ms
```
The residual is unchanged from Phase 1D (16.48 ms) and is still **NOT VERIFIED**
(decomposing it into START/STOP setup, `M_WAITFREE` bus-free wait and
TCA/arbiter handshake was not done). No counter was redefined to make it balance.

## 7. Synthesis and internal timing (re-run)

`synth_design -top kv260_haptic_top -part xck26-sfvc784-2LV-c -mode out_of_context`
with `create_clock -period 10.312` (no LOC, J2 still UNKNOWN).

| Resource | Before (Phase 1C) | **After (Phase 1F)** | Delta |
|---|---:|---:|---:|
| CLB LUTs | 1431 | **1448** | **+17** |
| CLB Registers (FF) | 1330 | **1346** | **+16** |
| CARRY8 | 40 | 44 | +4 |
| Block RAM | 0 | 0 | 0 |
| DSP | 0 | 0 | 0 |
| **WNS** | +8.292 ns | **+8.276 ns** | -0.016 ns |
| **TNS** | 0.000 | **0.000** | 0 |
| WHS / THS | +0.045 / 0.000 | **+0.045 / 0.000** | 0 |
| Endpoints | 3979 | 4011 | +32 |
| Verdict | met | **"All user specified timing constraints are met"** | -- |

Synthesis: **0 errors**, 2 critical warnings (the same two `Synth 8-5799 tricell ->
logic` conversions of the I2C IOBUFs, expected in out-of-context mode without pins),
19 warnings. +16 FF is exactly the 16 extra flop bits of the 16->32 bit counter.
Elapsed 200.3 s.

## 8. Layered verdict (do not infer hardware from simulation)

| Layer | Status |
|---|---|
| RTL simulation | **PASS** |
| Test platform verification | **PASS** (pre/post comparison, 16/16 per-block) |
| Register configuration correct | **PASS (simulation)** |
| Fault detection effective | **PASS** (4/4) |
| Synthesis + internal timing | **PASS** |
| **Board-level I/O timing** | **NOT VERIFIED** (no LOC, J2 UNKNOWN) |
| **Real hardware** | **NOT VERIFIED** (no board, no real PCA9685) |

## 9. Open issues and remaining risks

1. **PCA9685 oscillator wake requirement (reduced but check on bench).** The fix restores
   ~1 ms, above the datasheet's ~500 us wake guidance. The 500 us figure itself is from
   the datasheet, **not measured on the real device** -- confirm on the bench with a scope.
2. **16.09 ms init-time residual not decomposed** -> NOT VERIFIED.
3. **J2 pin mapping still UNKNOWN**; no XDC, no board I/O timing; bitstream still forbidden.
4. **I2C clock stopped gap (from the red-team audit) still open**: if `pl_clk0` stops
   with the FPGA configured, `oe_n` holds its last value. Not addressed in this phase
   (it is an OE safety concern, and this phase was restricted to the delay fix).
5. **AXI unmapped region 0x3000-0x3FFF** still stalls instead of returning SLVERR.
6. **Out-of-context synthesis artefacts**: Bonded IOB = 0 and BUFG = 0 are OOC artefacts;
   must be re-verified with a real XDC (expect 3 IOBs: SCL, SDA, OE#).
7. **No SDF/timing-accurate simulation**; no electrical load, no rise/fall time.

## 10. Compliance
| Rule | Status |
|---|---|
| Only the isolated project modified | YES (1 file) |
| AX7020 original untouched | YES (HEAD/status unchanged; source SHA still `0f2280...`) |
| Other frozen functional RTL untouched | YES (17/17 migrated files SHA unchanged) |
| Init order / I2C protocol / PWM config / OE logic unchanged | YES |
| Test thresholds or assertions weakened | **NO** -- 21/21 and 11/11 pass with the original assertions |
| Bitstream / burn / `loadapp` / real I2C / actuator / supply | **NOT EXECUTED** |

Paused for independent review.
