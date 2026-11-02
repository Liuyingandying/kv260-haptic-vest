# KV260 Haptic -- Independent Red-Team Audit

**Date**: 2026-11-01 | Auditor: independent pass, source + waveform + new checkers
**Baseline**: `reports/AUDIT_BASELINE_SHA256.json` (68 files hashed BEFORE any audit work)
**Discipline**: no functional RTL modified, no frozen project touched, no bitstream,
no `loadapp`, no real hardware. All new checkers live under `audit/`.

## 0. Headline: the audit found real false positives

| # | Previous claim | Audit finding |
|---|---|---|
| 1 | "pre-scale written, `prescale=1`" (Phase 1B T5b **PASS**) | **FALSE POSITIVE.** `prescale=1` counted the *pointer byte* `0xFE`, not a PRE_SCALE data write. Byte-level decode now shows **16 real PRE_SCALE writes, all `0x79`**. |
| 2 | "init takes ~150 ms, 2272 PCA bytes" (Phase 1C Gap-1 report) | **FALSE.** Never measured -- 150 ms was the *simulation window*. Measured: **1304 data bytes, `init_done` at 35.457 ms**. |
| 3 | "SCL ~404 kHz, within tolerance" | **SPEC VIOLATION, not just tolerance.** 404.06 kHz **exceeds** the 400 kHz Fast-mode ceiling, and **t_LOW = 1237.4 ns < 1300 ns**. |
| 4 | "PWM 3.27% error" | Cause is my **model's** TCK quantisation, proven arithmetically below; not independently re-measured. |
| 5 | Phase 1B T5 "MODE1 written" | Weak check: it counted any byte at register offset 0 including the pointer. Now properly verified. |

## A. 16 x PCA9685 initialisation integrity -- byte-level decode

Method: `audit/tb_audit_decoder.v` rebuilds every byte from raw SCL/SDA edges,
attributes pointer vs data correctly (`regidx = ptr + (wr_idx-2)`), and reconstructs a
per-block register image. Raw trace: `audit/audit_decode.log`.

Expected sequence taken from source: `pca_init_sequencer.v` L10-L16, L84, L107-L134.

| Check | Expected | Measured | Verdict |
|---|---|---|---|
| A1 all 16 blocks addressed | 16/16 | **16/16** | **PASS** |
| A2 `PRE_SCALE` | `0x79` (121 -> 50 Hz) | **`0x79` on all 16** | **PASS** |
| A3 first `MODE1` | `0x30` (SLEEP\|AI) | **`0x30` on all 16** | **PASS** |
| A4 `MODE2` | `0x04` (OUTDRV) | **`0x04` on all 16** | **PASS** |
| A5 all-channel FULL_OFF | `LEDn_ON=0x000`, `LEDn_OFF=0x100` | `LEDn_ON=0x000` measured; **OFF registers not tracked by my checker** | **NOT VERIFIED** |
| A6 byte budget | 16x84 = 1344 | **1304 data + 117 addr, 17 TCA selects** | **PASS** (within budget) |
| A7 MODE1 write count | 3 per block (sleep/wake/restart) | 3 per block (4 on BLK13/14) | PASS |
| A8 1 ms wake delay | after wake segment | not instrumented | **NOT VERIFIED** |

**A5 is my checker's gap, not evidence of a defect.** `pca_init_sequencer.v` L125 writes
`0x10` only to every 4th register (`LEDn_OFF_H`), so the *correct* FULL_OFF image is
`LED_ON=0x000 / OFF_H=0x10`. My assertion demanded `LED_ON=0x010`, which was simply wrong.
I did not store OFF registers, so FULL_OFF remains **not independently proven**.

**Explaining `prescale=1` vs `online_mask=FFFF`:** they are unrelated quantities.
`online_mask` is set by `i2c_fault_monitor` from *successful ACK* of each block's init
transaction -- it says nothing about register contents. `prescale=1` was a decode
artifact. **ACK/online_mask can never prove configuration correctness**, which is exactly
why this audit rebuilt the register image instead.

## B. I2C timing compliance

Source: `i2c_master.v` L52 `TICKS = CLK_FREQ_HZ/(4*I2C_FREQ_HZ)`, L72 phase 0/1=low,
2/3=high -> **symmetric 50/50 duty**.

```
TICKS  = 96_968_727 / (4*400_000) = 60   (integer division, truncates 60.6)
period = 4*60 = 240 clk = 240*10.3125 ns = 2475.0 ns
SCL    = 404_036 Hz
t_HIGH = t_LOW = 120 clk = 1237.5 ns
```

| Item | Spec (Fast-mode) | Measured | Verdict |
|---|---|---|---|
| SCL frequency | <= 400 kHz | **404.06 kHz** | **FAIL -- exceeds ceiling** |
| t_LOW | >= 1300 ns | **1237.4 ns** | **FAIL -- 4.8% short** |
| t_HIGH | >= 600 ns | 1237.4 ns | PASS |
| TCA9548A 400 kHz rating | <= 400 kHz | 404.06 kHz | **FAIL -- over device rating** |

Measured from real edges in `audit/audit_summary.txt` (min in-frame period 2474.9 ns,
min high/low 1237.4 ns). The *average* 344 kHz in that file is polluted by
inter-transaction gaps and must not be quoted; the min values are the true bus timing.

**Should the divider be 61?** `TICKS=61` gives 397.4 kHz (compliant on frequency) but
`t_LOW = 122 clk = 1258 ns`, **still below 1300 ns**. Because the duty is fixed at 50/50,
t_LOW compliance requires `2*TICKS*TCK >= 1300 ns` -> **TICKS >= 63** (384.8 kHz,
t_LOW = 1299.4 ns, marginal) or **TICKS = 64** (378.8 kHz, t_LOW = 1320 ns, safe).

**Recommendation only (RTL not modified):** either (a) set `I2C_FREQ_HZ` so that
`TICKS >= 64` (i.e. use ~375 kHz), or (b) add asymmetric phase timing
(low 3 phases / high 2) to keep 400 kHz with t_LOW >= 1.4 us.

**Inherited defect:** the AX7020 original with `CLK_FREQ_HZ=50e6` gives `TICKS=31`,
`t_LOW = 62*20 = 1240 ns` -- **the same violation already existed in the AX7020 design**.
KV260 did not introduce it, but it will now be running against real TCA9548A/PCA9685.

## C. PWM timing -- where the 3.27% comes from

Previous explanation ("10 ns quantisation") was **asserted, not proven**. Arithmetic proof:

```
model tick : pwm_tick_ns += 10   per clock
real clock : TCK = 1000/96.968727 = 10.31249 ns
ratio      : 10 / 10.31249 = 0.96970  -> model time runs 3.126% SLOW
apparent period = 20.000 ms / 0.96970 = 20.625 ms
step quantisation: 4883 ns step vs true 4882.81 ns -> +0.004%
combined expected ~ 20.626 ms ; measured 20.654 ms  (residual +0.14%)
```
**Conclusion: the 3.27% is a TESTBENCH MODEL artefact** (my `pwm_tick_ns += 10` integer
approximation), **not a PCA9685 property and not an RTL property.** It was not
independently re-measured with a corrected model, so this stays an arithmetic
explanation: **NOT VERIFIED by re-run.**

Real PCA9685 period from the byte-verified registers:
`PRE_SCALE=121` -> `f = 25e6/((121+1)*4096) = 50.03 Hz` -> **period 19.99 ms**. Compliant.

## D. OE# safety -- counter-evidence

| Path | Evidence | Verdict |
|---|---|---|
| reset default off | T8a, `oe_n=1` out of reset | PASS |
| refuse before init healthy | T8c fail-closed branch | PASS |
| allow when healthy | Gap-1 diag C: `oe_n=0, armed=1, reason=0` | PASS |
| init-time NACK | diag A: fault at 0.445 ms, `oe_n=1` | PASS |
| run-time NACK | T10b `oe_n=1` | PASS |
| SDA stuck | T11 `oe_n=1`, no hang | PASS |
| failsafe timeout | Gap-1 diag: `force_off=1` -> `reason=5`, `oe_n=1` | PASS |
| fault_clear recovery | T12 exercised | PARTIAL (path exercised, recovery to RUN not asserted) |

**Latency**: `haptic_oe_guard` registers `oe_n <= ~(arm_ready && dwell_ok)`, so shutdown =
**1 clock (10.3 ns) + the source signal's own latency**. `force_off` is combinational from
`failsafe_timer`/`fault_recovery_ctrl`; `fault_latched` comes from `i2c_fault_monitor`
which needs its own detection cycles (not measured per-cycle here). **Exact cycle count
from fault to OE# low is NOT measured** in this audit.

### Uncovered path found (real)
**If `pl_clk0` stops while the FPGA is configured, `oe_n` retains its last value.**
If the output was enabled (`oe_n=0`), a clock stop leaves OE# driven **low = outputs
enabled**, permanently, with no hardware watchdog. The external pull-up only protects the
*unconfigured* case (IOBUF tri-state). This is a genuine gap between
"RTL logic is safe" and "the hardware can always shut off".

## E. Init time: 46 ms vs "150 ms"

| | Claimed earlier | **Measured now** |
|---|---|---|
| data bytes | 2272 | **1304** |
| address bytes | -- | **117** |
| TCA selects | 17 | **17** |
| init_done time | ~150 ms | **35.457 ms** |

**The 150 ms / 2272 B figures were not measurements.** 150 ms was the length of the
simulated window I chose; 2272 was a counter that incremented on non-TCA data bytes
without verifying the address. Both are withdrawn.

Reconciling with AX7020 v1.6 (1330 B / 46 ms): byte counts now agree (1304 vs 1330, a
26-byte difference = 2 fewer bytes/block on 13 blocks, consistent with segment packing).
Time differs because AX7020 reported wall time including UART/log overhead and 50 MHz
`TICKS=31` vs 96.97 MHz `TICKS=60` give nearly identical SCL periods, so **the serial
time budget is essentially unchanged**; KV260's 35.5 ms is the pure bus time.

**No repeated initialisation** (17 TCA selects = 1 deselect + 16 block selects).
**No unnecessary channel switching** beyond that.
**No extra idle cycles detected** beyond the 16 x 1 ms wake delays (16 ms of the 35.5 ms
is mandated by the protocol; the remaining ~19.5 ms is 1304 bytes x ~15 us/byte).

## F. AXI and synthesis coverage

| Item | Method | Result | Verdict |
|---|---|---|---|
| 0x3000-0x3FFF stall | source analysis `kv260_haptic_top.v`: `sel_ctrl/sel_sts/sel_oe` all false -> `awready=0` forever | confirmed by code | **FAIL (robustness)** -- but **NOT empirically tested** (no AXI timeout test run) |
| READY/VALID completeness | source review | no combinational loop (Vivado `checking loops (0)`); ready comes from the selected sub-block | PARTIAL |
| SLVERR on bad address | source: never generated, `bresp/rresp` always `2'b00` | **NOT VERIFIED** -- no error response exists |
| OOC IOBUF omission | `vivado_synth.log`: 2x `CRITICAL WARNING [Synth 8-5799] Converted tricell instance to logic` | confirmed | **OOC omits the 2 I2C IOBs; a real XDC must re-verify 3 IOBs (SCL, SDA, OE#)** |
| unconstrained paths | `timing_summary.rpt`: 62 `no_input_delay` + 71 `no_output_delay` | confirmed | **BOARD I/O TIMING NOT VERIFIED** |
| WNS +8.292 ns scope | path groups all `pl_clk0` internal; I/O paths excluded from the constraint set | confirmed | **internal only -- does NOT cover board I/O** |

## 3. Independent counter-tests -- HONEST STATUS

**I did not complete the planned fault-injection counter-tests** (corrupt PRE_SCALE,
two TCA channels, drop one transaction, OE fails to close). Time was consumed by three
successive defects in my own decoder, which is itself informative:

| Decoder defect | Symptom it produced | Detector sensitivity shown |
|---|---|---|
| pointer byte recorded as data | PRESCALE=0xFE on all blocks (50 mismatches) | checker is sensitive to exactly this class of error |
| auto-increment off by one | MODE2 read as `0xA0` (last byte) | sensitive to register misalignment |
| `in_frame` declaration order | compile fail, silent re-run of stale snapshot | -- |

So the *decoder* demonstrably catches register-level corruption, but the **deliberate
error-injection suite was not executed** -> that requirement is **NOT DONE**, not passed.

## 4. Answers to the seven questions

**Q1. Are all 16 PCA really configured for 50 Hz?**
**Yes, now proven at byte level**: all 16 blocks received `MODE1=0x30`, `PRE_SCALE=0x79`,
`MODE2=0x04`. FULL_OFF is **not** proven (my checker never tracked OFF registers).
Earlier `prescale=1` was a false positive.

**Q2. Is 404 kHz within TCA9548A spec?**
**No.** 404.06 kHz exceeds the 400 kHz ceiling, **and** t_LOW = 1237.4 ns violates the
1300 ns Fast-mode minimum. TCA9548A is a 400 kHz part. Inherited from AX7020, now exposed
by a real bus. Fix suggestion in section B (TICKS >= 64, or asymmetric phases).

**Q3. Where does the PWM 3.3% period error come from?**
From **my testbench model**, not the hardware: `pwm_tick_ns += 10` while the real clock is
10.31249 ns -> model runs 3.13% slow -> 20.625 ms apparent. The **register-level** period
from `PRE_SCALE=121` is **19.99 ms (50.03 Hz)**. The model was not re-run corrected, so
this is an arithmetic explanation, **not a re-measurement**.

**Q4. Is any OE# safety path uncovered?**
**Yes: clock stop.** With `pl_clk0` stopped and the FPGA configured, `oe_n` holds its last
value; if it was enabled, outputs stay enabled with no hardware backstop. Also
`fault_clear` recovery is only "exercised", not asserted to reach RUN. Exact fault-to-OE#
latency in cycles was **not measured**.

**Q5. Why did init time go 46 ms -> 150 ms?**
**It did not.** The 150 ms was the simulation window, never a measurement. Measured
`init_done = 35.457 ms` with 1304 data bytes -- actually **faster** than the AX7020
figure, and byte-count-consistent (1304 vs 1330). No repeated init, no extra channel
switching.

**Q6. Did the 21 existing tests contain false positives?**
**Yes, at least one confirmed:** T5b "PRE_SCALE written" passed on a counter that was
actually counting the register *pointer* byte (`prescale=1`). T5 "MODE1 written" used the
same weak counter. T8c is self-disclosed as fail-closed-only. T7 is register-level, not
waveform. The other 17 checks were not re-derived here and should be treated as
**unverified-by-audit** rather than confirmed.

**Q7. Is the design ready for board test?**
**No.** Blocking: (a) J2 pins UNKNOWN, (b) board I/O timing unverified (62+71
unconstrained), (c) **I2C bus exceeds both the 400 kHz ceiling and the t_LOW minimum** --
a real TCA9548A/PCA9685 may NACK or behave erratically, (d) FULL_OFF unproven,
(e) OE# clock-stop gap. The fabric side, register configuration and internal timing are
in good shape; the *bus electrical contract* is not.

## 5. Scope and limitations of this audit
- Single-clock simulation only; no timing-accurate SDF back-annotation.
- Bus model ACKs every byte: real NACK/clock-stretching arbitration not exercised.
- No real TCA9548A/PCA9685, no I/O timing, no silicon.
- Section 3 counter-tests not executed.
- Three defects were found in the *auditor's own* tooling before it produced a stable
  result; earlier audit conclusions drawn with those tools are retracted where noted.
- **No absolute "RTL has no defects" claim is made**: this audit establishes specific
  facts and specific unverified areas.
