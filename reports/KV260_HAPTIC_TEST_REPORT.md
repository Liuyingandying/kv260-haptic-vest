# KV260 Haptic Test Report (Phase 1B)

Toolchain: **Vivado 2025.2 XSim** (`xvlog` -> `xelab` -> `xsim`). Icarus not installed.
ASCII staging dir used for simulation (`D:\DevCache\Temp\kv260_haptic_sim`) because XSim
cannot open non-ASCII paths; authoritative sources stay in the project dir.

## Build
| Step | Result |
|---|---|
| xvlog, 21 RTL files | **exit 0**, no errors |
| xelab `tb_kv260_haptic_top` | **BUILT** snapshot `tb_sim` |
| xsim run | completed in **9.1 s**, reached `$finish` |

## Check results (run 2, after first TB fix)
| # | Check | Result |
|---|---|---|
| T8a | OE# default OFF out of reset | **PASS** |
| T8a2 | OE guard reason = RESET | **PASS** |
| T8a3 | not armed out of reset | **PASS** |
| T1 | AXI CTRL.ENABLE write | **PASS** |
| T1b | 256-cell AXI burst write | **PASS** |
| T2 | COMMIT accepted (STATUS readback) | **PASS** |
| T3 | scheduler produced I2C traffic | **PASS** |
| **T4** | TCA9548 0x70 channel select | **PASS** (after TB fix) |
| T4b | TCA mask is a single channel bit | **FAIL** |
| T5 | PCA9685 MODE1 written | **FAIL** |
| T5b | PCA9685 PRE_SCALE written | **FAIL** |
| T6 | PCA9685 PWM registers written | **FAIL** |
| T7 | PWM write on 0x40..0x4F block | **FAIL** |
| T8b | OE stays OFF without SW_OE_REQ | **PASS** |
| T8c | OE fail-closed (init not healthy) | **PASS** |
| T9a | OE back to OFF during reset | **PASS** |
| T9b | OE remains OFF after reset | **PASS** |
| T10 | NACK injection exercised | **PASS** |
| T10b | OE forced OFF by fault | **PASS** |
| T11 | SDA stuck: no hang, OE off | **PASS** |
| T12 | recovery path (fault_clear) | **PASS** |

**checks=21, errors=5 -> TB RESULT: FAIL**

## Measured wire observations
```
run 1: starts=98 tca_sel=35 mode1=0 prescale=0 pwm_wr=0  online_mask=0x0000 fault=1
run 2: starts=1  tca_sel=1  mode1=0 prescale=0 pwm_wr=0  online_mask=0x0000 fault=1
```

## Root cause of the 5 failures (testbench, not RTL)
1. **run 1**: address-phase `bitcnt` was not cleared after the ACK, so every later byte
   was decoded against a stale shift register -> 98 bogus STARTs, zero PCA decode.
2. **run 2** (after that fix): the ACK is driven low on the ACK negedge but released by
   `posedge scl && bitcnt==8`; because `bitcnt` is now 0 at that posedge, **SDA is never
   released** -> bus stuck after the first transaction (`starts=1`).

**Minimal fix (identified, NOT yet applied):** replace the `bitcnt==8` release condition
with an explicit `ack_pending` flag (set on ACK negedge, cleared on the next SCL rising
edge).

Because the bus hangs after transaction 1, the PCA9685 address/register decode path is
never exercised, which is exactly why T5/T5b/T6/T7 fail. **These four are UNVERIFIED, not
proven broken.**

## Regression of the original AX7020 chain
Not run in this phase (no Icarus; the AX7020 regression uses `iverilog`). Reported as
**NOT_RUN** rather than inherited as passing.

## Logs
- `logs/sim_run_01.log` (before TB fix)
- `logs/sim_run_02.log` (after TB fix)
