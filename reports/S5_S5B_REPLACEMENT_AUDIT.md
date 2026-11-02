# S5 / S5b Replacement Audit

## 1. Original S5 / S5b: **INVALID -- must not be used as PASS evidence**

| check | original assertion | why INVALID |
|---|---|---|
| S5  | `pca_regs[8'h00] == 8'h11` (POR MODE1) | the DUT **legitimately writes MODE1 during init**, overwriting POR. The check asserted a value the design is *supposed* to change. |
| S5b | `pca_regs[8'hFE] == 8'h79` (POR PRE_SCALE) | same error. |

Both were also misleading: they read the **model's** register file, not a DUT output,
and the read path was never exercised (`reads=0`). **Status: INVALID.**
The 21-item acceptance set is unchanged (S5/S5b were supplementary-only).

## 2. Second finding: the diagnostic CSV register attribution has an OFF-BY-ONE bug

While extracting the init protocol from `diag/init_event_timeline.csv` I found a bug in
**my own diagnostic logging** (`tb_kv260_haptic_gap1.v`, `PCADATA` row):

```verilog
if (wr_idx == 1) pca_reg = shift; else pca_reg = pca_reg + 1;   // blocking
csv_row("PCADATA", {pca_reg[7:0], shift[7:0]}, n_pca_wr);        // logs post-update pca_reg
```
- for the **first** data byte (`wr_idx==1`) `pca_reg` has already been set to the
  pointer, so the row logs `{ptr, ptr}` -- the pointer is counted as if it were data;
- for later bytes `pca_reg` was already incremented, so the logged register index is
  **+1 too high**.

Consequence: the raw statistics I first computed from the CSV
(MODE1 writes = 31 with value 0x00, PRE_SCALE = 1 with value 0xFE) are **NOT reliable**
and must not be quoted as evidence. `0xFE` is the PRE_SCALE **pointer**, not the value.

**This is a testbench-logging defect, not an RTL defect.** It has no effect on any of
the 21 acceptance checks or on the S1..S6 results (they read DUT/state directly).

## 3. Replacement checks (protocol-based, split by evaluability)

| # | Replacement check | Basis | Status |
|---|---|---|---|
| S5-R2 | MODE1 (reg 0x00) is addressed/written at least once per initialised block | init protocol requirement | **SATISFIED** (writes observed; exact value NOT evaluable from this log) |
| S5-R3 | After init: `online_mask == 0xFFFF` (all 16 blocks accepted) | `init_healthy` definition | **SATISFIED** (measured `online=ffff`) |
| S5-R4 | After init: `init_healthy == 1` and `fault_latched == 0` | init success criteria | **SATISFIED** (measured `healthy=1 fault=0`) |
| S5-R5 | Init requires **no** I2C read transactions | observed behaviour | **SATISFIED** (`reads=0`) |
| S5-R1 | `PRE_SCALE` value == **0x79** (121 -> 50 Hz with 25 MHz PCA oscillator) | `pca_init_sequencer #(.PWM_HZ(50))` | **NOT EVALUABLE** -- needs a corrected log (see §2) or a DUT-side readback |

S5-R1 is intentionally **not** claimed as passed. It stays open until the CSV logging
is fixed (register index must be captured *before* the auto-increment) or until a
read-back path exists.

## 4. Standards note
- No assertion was deleted from the 21-item set.
- Two INVALID supplementary checks were withdrawn, not silently converted to PASS.
- Four replacement checks added; three evaluated and satisfied, one honestly marked
  not-evaluable.
