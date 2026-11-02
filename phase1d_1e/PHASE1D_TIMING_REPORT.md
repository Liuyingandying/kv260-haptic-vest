# Phase 1D -- I2C Timing Correction and Fast-mode Verification

## 1. Change made (parameter only)
`rtl/kv260_haptic_top.v`: `I2C_FREQ_HZ` **400_000 -> 378_700**. No FSM, datapath or
safety logic touched; the divider remains `TICKS = CLK_FREQ_HZ/(4*I2C_FREQ_HZ)` in
`i2c_master.v` L52.

Derivation (integer division matters -- 378_784 would give TICKS=63, not 64):
```
f_CLK = 96_968_727 Hz
4*378_700 = 1_514_800  ->  TICKS = 96_968_727/1_514_800 = 64   (exact floor)
f_SCL = f_CLK/(4*64) = 378_784 Hz
t_LOW = t_HIGH = 2*64/f_CLK = 1320.0 ns
```

## 2. Measured timing (from real SCL/SDA edges, in-frame only)
| Metric | Required (Fast-mode) | Before (TICKS=60) | **After (TICKS=64)** | Verdict |
|---|---|---|---|---|
| SCL frequency | <= 400 kHz | 404.04 kHz | **378.78 kHz** (min period 2639.9 ns) | **PASS** |
| t_LOW | >= 1300 ns | 1237.5 ns | **1319.9 ns** | **PASS** |
| t_HIGH | >= 600 ns | 1237.5 ns | **1319.9 ns** | **PASS** |
| SDA data setup | >= 100 ns | n/m | **1309.6 ns** | **PASS** |
| START setup | >= 600 ns | n/m | **1319.9 ns** | **PASS** |
| STOP setup | >= 600 ns | n/m | **5321.0 ns** | **PASS** |

Note: the printed "avg" values (period 3050 ns / 327.82 kHz) are polluted by
inter-transaction gaps because the collector also sees edges outside a frame; the
**min** values are the true bus timing and are the ones used for the verdicts.

## 3. Open-drain behaviour
The DUT drives SDA/SCL only low or releases (IOBUF), verified by the bus model which
supplies all pull-ups. The model never observes a push-pull high from the DUT.

## 4. 21-item regression (divider changed, assertions untouched)
`tb_21item_p1d.v` = the Phase-1B `tb_kv260_haptic_top_revB.v` with **only**
`I2C_FREQ_HZ(400_000) -> (378_700)`; no assertion, threshold or expected value changed.
Result: **checks=21 errors=0 -> PASS**. Log: `p1d_21item.log`.

## 5. What is NOT covered
- No SDF/timing-accurate simulation; no real rise/fall time, electrical load or
  board-level signal integrity. "Simulation passes" != "the wire passes".
- No clock stretching, no real NACK contention.
- `f_CLK = 96.968727 MHz` is the KV260 datasheet value; it was **not measured on
  the board this round** (H0 established that `k26-starter-kits` is loaded).
