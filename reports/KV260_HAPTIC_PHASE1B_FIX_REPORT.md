# KV260 Haptic Phase 1B -- ACK Fix & Full Regression Report

**Date**: 2026-11-01  **Scope**: testbench-only fix inside `KV260_Haptic_Port_20261101`
**Result**: **21 / 21 checks PASS** (errors = 0)

## 1. Change boundary compliance
| Rule | Status |
|---|---|
| Only the isolated project's testbench modified | **YES** (only `tb/tb_kv260_haptic_top_revB.v`; rev A kept as `..._revA_before_fix.v`) |
| AX7020 original untouched | **YES** (`git status` 39 lines, HEAD 287e2ee) |
| Migrated functional RTL untouched | **YES** (all 21 RTL files byte-identical to Phase 1A) |
| KV260 frozen project untouched | **YES** |
| bitstream / loadapp / real I2C / JTAG | **NOT EXECUTED** |

## 2. The two defects that were fixed (testbench only)

### Defect A -- address-phase bit counter not cleared
`decode_byte` ended the address phase without resetting `bitcnt`/`shift`, so every
subsequent byte was assembled from a stale shift register. Symptom in run 1:
`starts=98` bogus STARTs, `mode1=0 prescale=0 pwm_wr=0`.

### Defect B -- ACK driven but never released
The release condition was `posedge scl && bitcnt==8`. After Defect A was fixed,
`bitcnt` is already 0 at that edge, so **SDA was never released** and the bus
wedged after the first transaction. Symptom in run 2: `starts=1`.

## 3. Correct I2C ACK timing now implemented
```
t0  rising  SCL #8   last data bit sampled            bitcnt -> 8
t1  falling SCL #8   byte complete -> decode          SLAVE PULLS SDA LOW
t2  rising  SCL #9   ACK clock high                   SDA held LOW for whole phase
t3  falling SCL #9   ACK done                         SLAVE RELEASES SDA, bitcnt -> 0
```
- START / repeated START / STOP detected on SDA edges while SCL is high
- SDA is **only** pulled low or released (open drain); the model never drives high
- next byte restarts counting from `bitcnt=0`

## 4. T4b investigated independently (not mixed with the ACK fix)
Diagnostic instrumentation logged every non-single-bit mask:
```
[T4b-DIAG] TCA mask=0x00 at sel#1 (wr_idx=1) bitcount=0
```
**Root cause: a legitimate deselect.** The first TCA write is mask `0x00`
(no channel selected) which is exactly the documented init behaviour
("初始化会先隔离 TCA 通道，以避免 PCA 的 ALLCALL 默认地址与 TCA 0x70 冲突").
It is **not** multi-channel selection.

The previous predicate was wrong (it required every mask to be in {1,2,4,8}).
Corrected predicate, stricter about what actually matters:
```verilog
T4b = (n_tca_multi == 0) && (n_tca_single > 0);
//  n_tca_multi  : masks with popcount >= 2  -> must be zero
//  n_tca_single : masks with popcount == 1  -> must be > 0
//  mask 0x00    : counted separately as deselect (legal)
```
This **does not lower the standard**: it still forbids selecting two channels at
once, and additionally requires that real single-channel selects occur
(`single=45`).

## 5. Full regression evidence (final run)
```
checks=21  errors=0
wire: starts=243 repeated=0 stops=242 acks=720 nacks=74
wire: tca_sel=56 last_mask=08 multi=0
wire: mode1=55 prescale=1 pwm_wr=385 blocks=ffff
wire: last_pwm blk=8 ch=1 on=16
TB RESULT: PASS
```
Logs: `logs/sim_run_01.log` (pre-fix), `logs/sim_run_02.log` (rev A after partial fix),
`logs/sim_revB_final.log` (rev B PASS).

## 6. What is proven vs not proven
| Claim | Evidence | Status |
|---|---|---|
| RTL compiles and elaborates on KV260 clock | xvlog exit 0 (21 files), xelab BUILT | **PROVEN** |
| TCA9548 channel select works, never multi-channel | `tca_sel=56 multi=0 single=45` | **PROVEN (sim)** |
| PCA9685 MODE1 / PRE_SCALE writes occur | `mode1=55 prescale=1` | **PROVEN (sim)** |
| PWM registers written for all 16 blocks | `pwm_wr=385 blocks=ffff` | **PROVEN (sim)** |
| PWM duty code captured on a real block/channel | `blk=8 ch=1 on=16` | **PROVEN (sim)** |
| OE# defaults OFF, stays OFF on fault/reset/SDA-stuck | T8a/T9a/T9b/T10b/T11 | **PROVEN (sim)** |
| **OE# positively ENABLES (oe_n -> 0)** | T8c passed via the **fail-closed** branch (`init_healthy=0`) | **NOT PROVEN** |
| Physical PWM waveform / 50 Hz period | not measured | **NOT PROVEN** |
| Real hardware behaviour | nothing connected | **NOT PROVEN** |

### 6.1 Honest caveat on T8c
In this run `init_healthy` never became 1 (`online_mask=0x0000`), so `oe_n` never
went to 0. T8c therefore verified the **safe/refusing** branch, not the enabling
branch. The most likely cause is that the slave model only answers **write**
transactions and does not return register data on **read** transactions, which the
PCA init handshake may need. This is a **model limitation**, to be closed before
claiming the enable path.

## 7. Next step
21/21 passed, so per the task the gate to Phase 1C (synthesis / resources / timing /
AXI checks) is open -- **still no burning**. Two items stay open:
1. slave model read path (to positively exercise T8c enable)
2. J2 pin mapping remains **UNKNOWN** (no LOC guessed)
