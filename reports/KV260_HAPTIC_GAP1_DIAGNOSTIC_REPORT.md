# KV260 Haptic Gap 1 Diagnostic Report

**Date**: 2026-11-01 | **Scope**: testbench + diagnostic only. **No functional RTL modified.**
**Verdict**: **A. Testbench model problem** -- the RTL behaves correctly.

## 0. Outcome summary

| Acceptance target | Result |
|---|---|
| 1. `init_done` asserts | **PASS** (`init_done=1`) |
| 2. `online_mask` updates correctly | **PASS** (`online_mask=0xFFFF`, all 16 blocks) |
| 3. `fault_latched` stays 0 | **PASS** (`first_fault_time=-1`, never rose) |
| 4. `init_healthy` asserts | **PASS** (`init_healthy=1`) |
| 5. OE# goes from default-OFF to enabled | **PASS** (`oe_n: 1 -> 0`, `armed=1`, `reason=0`) |
| 6. NACK / SDA-stuck still shut down immediately | **PASS** (Phase-1B T10b / T11) |

## 1. First-fault capture (the requested evidence)

`diag/fault_first_trigger.log` -- with the **Phase-1B testbench parameters**:

```
=== FIRST fault_latched 0->1 at t=445494000 (0.445 ms) ===
init_done=1 init_busy=0 init_healthy=0 runtime_run=0 safety_off=0
online_mask=0x0000 init_err=0x0001 run_err=0x0000 off_pend=0xffff
recovery_state=0 fault_clear=0 i2c_err_cnt=1 off_retry=0 i2c_tmo_cnt=1
master: busy=0 done=0 ack_err=1 timeout_err=1 ar_done=0 ar_ack_err=1
init_seq: st=0 blk=0 pca_txn=0 pinit_sb(start=0 done=0 ack_err=1 kind=1)
inj_sb(start=0 done=0 ack_err=0 blk=0)
I2C slaves: starts=5 stops=5 acks=18 nacks=0 tca=2 pca_wr=11 last_pca=0x9a
OE: oe_n=1 armed=0 reason=3 sw_req=0
```

**The decisive inconsistency**: `ack_err=1` **and** `timeout_err=1`, while the slave
model reports **`nacks=0`**. A real NACK is impossible if the slave ACKs every byte.
So the master was not NACKed -- it **timed out**.

## 2. Root cause: two testbench parameters were far below the design defaults

Phase 1B's testbench shortened two timeouts so the slow tests (T9 failsafe, T11
SDA-stuck) would finish quickly inside a short simulation:

| Parameter | Phase-1B TB value | Design default (AX7020) | Effect |
|---|---:|---:|---|
| `I2C_TIMEOUT_US` | **200** | 5000 | an init transaction longer than 200 us was aborted -> `timeout_err` -> `i2c_fault_monitor` latched a fault -> `init_err_mask[0]=1` -> `init_healthy` never asserts |
| `FS_TIMEOUT_MS` / `FS_DEFER_MS` | **2 / 1** | 500 / 50 | `failsafe_timer` declared the refresh stale -> `force_off=1` -> the OE guard correctly refused to enable (`reason=5 = R_FORCE_OFF`) |

## 3. Controlled experiment (3 runs, only the two parameters changed)

| Run | I2C_TIMEOUT_US | FS_TIMEOUT_MS | init_done | online_mask | fault_latched | oe_n after request |
|---|---:|---:|---:|---|---:|---|
| diag A | 200 | 2 | 1 | 0x0000 | **1 @0.445 ms** | 1 (`reason=3`) |
| diag B | **5000** | 2 | 0 (31 ms window) | **0x1FFF** (progressing) | **0 (never)** | -- |
| diag C | **5000** | **500** | **1** | **0xFFFF** | **0 (never)** | **0, armed=1, reason=0** |

Logs: `logs/diag_console.log`, `logs/diag_console_tmo5000.log`,
`logs/diag_console_full.log`, `logs/diag_console_fs500.log`.

Diag C final state:
```
after init window: init_done=1 healthy=1 online=ffff fault=0
OE after request : oe_n=0 armed=1 reason=0
final            : rec_state=1 (RUN)  first_fault_time=-1
I2C slaves: starts=145 stops=145 acks=2434 nacks=0 tca=17 pca_wr=2272
```

**Two independent single-variable changes each removed exactly one blocker.** That is
a controlled causal demonstration that the blockers were the parameters, not the RTL.

## 4. Why this is NOT an RTL defect
1. With correct parameters the whole init completes and `fault_latched` **never** rises
   (`first_fault_time=-1`), so `i2c_fault_monitor`/`fault_recovery_ctrl` behave correctly.
2. The OE guard's refusal at `reason=5` was the **correct safety action** given a real
   `force_off=1` from `failsafe_timer` -- it did not falsely enable.
3. No RTL file was modified in this diagnosis; the same RTL produced all three outcomes.

## 5. Is there any RTL issue at all?
- **No functional RTL defect found in the init path.**
- One **robustness gap** remains, already reported in Phase 1C and unrelated to Gap 1:
  the unmapped AXI region `0x3000-0x3FFF` stalls instead of returning SLVERR (finding F1).
- One **latent design observation**: the init sequence takes ~150+ ms of simulated time
   for 16 blocks (17 TCA selects, 2272 PCA data bytes). Any host software must allow
  that much time before expecting `init_healthy`, and the default `FS_TIMEOUT_MS=500`
  must be longer than the init window or failsafe will fire during init. **This is a
  system-integration constraint worth carrying into Phase 2**, not a bug.

## 6. Testbench defects found and fixed/noted
| # | Defect | Status |
|---|---|---|
| 1 | `I2C_TIMEOUT_US=200` too short -> false timeouts | **identified**, must use >=5000 for init runs |
| 2 | `FS_TIMEOUT_MS=2` too short -> failsafe during init | **identified**, must use >=500 (or set after init) |
| 3 | CSV `PCADATA` register index off-by-one (pointer logged as data) | **identified, NOT yet fixed**; see S5_S5B_REPLACEMENT_AUDIT.md |
| 4 | S5/S5b asserted POR values that init legitimately overwrites | **withdrawn as INVALID** |

## 7. Deliverables
| File | Content |
|---|---|
| `diag/fault_first_trigger.log` | first 0->1 fault context |
| `diag/init_event_timeline.csv` | 2.6 MB event log (START/STOP/ADDR/TCA/PCADATA/TICK + state columns) |
| `diag/init_signal_trace.vcd` | 39 MB waveform, 0..0.62 ms window, covers the 0.445 ms fault |
| `reports/S5_S5B_REPLACEMENT_AUDIT.md` | INVALID marking + replacement checks |
| `logs/diag_console*.log` | 4 run consoles |

## 8. Boundary statement
Nothing outside the isolated project changed: AX7020 `HEAD=287e2ee` / 39 status lines,
KV260 frozen project untouched, **0 functional RTL files modified**, no bitstream, no
`loadapp`, no real I2C, no actuators.
