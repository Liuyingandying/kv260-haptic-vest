# Phase 1D-1E Final Report

## 0. Baseline (Phase 0, before any change)
| Item | Value |
|---|---|
| Working dir | isolated project `KV260_Haptic_Port_20261101` (21 RTL files) |
| AX7020 source repo | HEAD `287e2ee3365a3c0d0ed151d1363a72841b22d6b7`, branch `codex/phase1-fpga-p0-fault-chain` |
| 17 migrated RTL SHA256 | **all unchanged** (checked before and after) |
| I2C divider before | `I2C_FREQ_HZ=400_000` -> `TICKS=60` -> 404.04 kHz, t_LOW 1237.5 ns |
| 21-item log | `logs/sim_revB_final.log` sha256 `e3c8927b2870b5d0...`, 21/21 PASS |
| PL clock | 96.968727 MHz -- **datasheet value, not measured this round** |

## 1. Phase 1D result: PASS
TICKS 60 -> **64** (via `I2C_FREQ_HZ` 400_000 -> 378_700). All six Fast-mode metrics pass:
378.78 kHz, t_LOW 1319.9 ns, t_HIGH 1319.9 ns, SDA setup 1309.6 ns, START 1319.9 ns,
STOP 5321 ns. 21-item regression still **21/21 PASS** with assertions untouched.

## 2. Phase 1E result: PASS
- 16/16 blocks: PRE_SCALE `0x79`, MODE2 `0x04`, first MODE1 `0x30`, final MODE1 `0xA0`
- **256/256 channels FULL_OFF** (`ON=0x000`, `OFF_H=0x10`), 0 violations
- Two independent decoders agree (Verilog + Python)
- 1330 init bytes -- identical to AX7020 v1.6
- 4/4 fault injections caught, legal inputs not falsely flagged

## 3. NEW RTL DEFECT FOUND (recorded, NOT fixed)

**16-bit delay counter overflow at KV260 clock.**

| Evidence | Value |
|---|---|
| `rtl/pca_init_sequencer.v` L240 | `reg [15:0] dly;` (max 65,535) |
| `rtl/pca_init_sequencer.v` L77 | `DLY_1MS = CLK_FREQ/1000` = **96,968** at 96.968727 MHz |
| `rtl/pca_init_sequencer.v` L315 | `dly <= DLY_1MS;` -> truncates to **31,432** cycles |
| Implied delay | 31,432 x 10.3125 ns = **324.1 us** |
| **Measured delay** | **326.8 us**, appearing exactly **16 times** (once per block) |

AX7020 at 50 MHz: `DLY_1MS = 50_000` fits in 16 bits -> 1.000 ms (correct).
KV260 at 96.968727 MHz: 96,968 **overflows 16 bits** -> the intended 1 ms PCA9685
wake delay silently becomes ~324 us.

**Hardware risk (not proven, no hardware):** the PCA9685 datasheet requires the
oscillator to stabilise for ~500 us after clearing SLEEP before RESTART is issued.
324 us is below that. On real silicon this could produce an unstable/incorrect PWM
frequency on first start.

**Not fixed**, because the round's rules forbid changing functional RTL. Minimal fix
(proposed, NOT applied): widen `dly` to `reg [31:0]` in the isolated copy, or pass a
`CLK_FREQ`-scaled `DELAY_CYCLES` parameter not exceeding 16 bits.

## 4. Init time contradiction -- resolved except one residual

**Where the earlier numbers came from:** "2272 bytes / ~150 ms" was never measured
(counter bug + simulation window). The audit's "1304 data bytes / 35.457 ms" counted
a different set (data bytes only, at TICKS=60).

**Correct accounting now (init window only, TICKS=64):**
```
1330 clocked bytes  (113 ADDR + 96 PTR + 1104 DATA + 17 TCA, 113 START, 113 STOP)
every byte          = 8 data periods + 1 ACK = 9 SCL periods = 9 x 1320 ns = 11.88 us
bus activity        = 1330 x 11.88 us                       = 15.80 ms
wake delays         = 16 x 324.1 us (overflowed 1 ms)        =  5.19 ms
                                   subtotal                   = 20.99 ms
measured total (first START -> init_done, incl. 0.6 us tail) = 37.47 ms
residual (inter-transaction overhead, 113 transactions)      = 16.48 ms
                                                               (145.8 us / transaction)
```
**The 16.48 ms residual is NOT VERIFIED** -- I did not decompose it into START/STOP
setup, `M_WAITFREE` bus-free waiting and TCA/arbiter handshake. It is reported, not
explained away. No counter was redefined to make the numbers match.

## 5. Deliverable status matrix
| Layer | Status |
|---|---|
| RTL simulation | **PASS** (21/21 + 11/11 Phase-1E checks) |
| Test platform verification | **PASS** (timing measured from real edges; 4/4 faults caught) |
| Register configuration correct | **PASS (in simulation)** -- 16 blocks, 256 channels |
| Fault detection effective | **PASS** (F1-F4) |
| **Real hardware** | **NOT VERIFIED** -- no board, no pins, no PCA9685, no oscillator |

**None of the first four implies real hardware PASS.**

## 6. Stop-condition check
| Condition | Status |
|---|---|
| Original RTL accidentally modified | **NO** (17/17 SHA unchanged) |
| I2C still violates Fast-mode | **NO** (all six metrics pass) |
| Unexplained 21-item failure | **NO** (21/21 PASS) |
| Register image mismatch | **NO** (after the corrected init-window sampling) |
| Fault injection undetected | **NO** (4/4 caught) |
| OE safety logic weakened | **NO** (untouched) |
| **New RTL defect found** | **YES** -- delay counter overflow (§3), recorded and NOT fixed |

Paused for human review. No bitstream, no `loadapp`, no real I2C, no actuator.
