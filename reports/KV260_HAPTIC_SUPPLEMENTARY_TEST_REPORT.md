# Phase 1C Supplementary Verification Report

Purpose: close the two gaps left by the Phase-1B 21/21 run **without touching the
21 acceptance criteria**. Separate testbench: `tb/tb_kv260_haptic_supp.v`
(module `tb_kv260_haptic_supp`, checks S1..S6).
Log: `logs/sim_supp.log`. Elapsed 9.6 s.

## Result: 5 PASS / 6 FAIL (errors=6)

| # | Check | Result | Evidence |
|---|---|---|---|
| S1 | PCA9685 answered writes | **PASS** | `pca_hits=109` |
| S1b | PCA9685 read transactions served | **FAIL** | `reads=0` -- DUT never issued a read; the read-back path was never exercised |
| S1c | `init_healthy` asserted (init really completed) | **FAIL** | `init_healthy=0 online=0000` |
| **S2** | **OE# positively ENABLED (oe_n==0)** | **FAIL** | `oe_n=1` -- never enabled |
| S2b | guard reports armed | **FAIL** | `armed=0` |
| S3 | OE# back to OFF on request clear | **PASS** | `oe_n=1` |
| **S4** | **PWM engine period ~20 ms (50 Hz)** | **PASS** | measured **20,654,359 ns** |
| S4b | PWM high time ~25% of period | **PASS** | high **5,163,590 ns** = 25.0% |
| S5 | readback POR MODE1 | **FAIL** | check design error (see below) |
| S5b | PRE_SCALE readback | **FAIL** | check design error (see below) |
| S6 | final OE# OFF | **PASS** | `oe_n=1` |

## Gap 2 (PWM waveform) -- CLOSED
A PCA9685 PWM engine model (4096 steps, 50 Hz, 20.000 ms nominal) was driven with an
explicitly programmed 25% duty (ON=0, OFF=1024). Measured:
```
period = 20,654,359 ns  (expected 20,000,000; +3.3% due to the 10 ns vs 10.312 ns
                         clock quantisation in the model tick)
high   =  5,163,590 ns  = 25.0% of period
```
**Scope limit: this validates the PWM ENGINE MODEL and the 20 ms/50 Hz relationship.
It is NOT a measurement of a real PCA9685 output, and NOT a claim that the DUT
programmed 25% duty.**

## Gap 1 (positive OE enable) -- STILL OPEN, root cause narrowed
`init_healthy` never asserts in simulation, so `oe_n` never goes low. Two candidate
causes were tested:
1. **Read-back missing** -- DISPROVED: `reads=0`. The init sequencer issues **no**
   read transactions, so the model's lack of read data is not the blocker. (I had
   previously assumed it was; that assumption was wrong.)
2. **Init ACK/时序 handshake** -- still open. `pca_hits=109` shows the PCA addresses
   are being addressed and ACKed, yet `online_mask=0000` and `fault_latched=1`,
   meaning `i2c_fault_monitor` latched a fault during init or init never reached the
   point of marking blocks online.

**S5/S5b are my own check-design errors**: they assert the register file still holds
the POR values (`0x11` / `0x79`), but the DUT legitimately *writes* MODE1 and
PRE_SCALE during init, so the POR values are overwritten. The model's read path was
never actually exercised (`reads=0`), so these two checks prove nothing either way.

## What must happen next (evidence preserved, RTL NOT modified)
1. Dump the pipeline's internal init signals (`init_busy`, `init_done`, `init_restart`,
   `online_mask`, `fault_latched`, `i2c_error_count`, `sb_ack_err`) over time to find
   why the fault monitor latches during init.
2. Decide whether the fault is a **model fidelity** issue (most likely: the model ACKs
   every byte but does not reproduce some timing/byte-sequence the sequencer expects)
   or a **real RTL issue**. **No RTL was changed**, per instruction.
3. Re-run S1b/S2 only after that diagnosis.
