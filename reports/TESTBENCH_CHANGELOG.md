# Testbench Changelog -- `tb_kv260_haptic_top`

All changes are confined to the testbench. **No functional RTL was modified.**

## rev A -> rev B (`tb_kv260_haptic_top_revB.v`)

| # | Defect | Location (rev A) | Fix (rev B) | Symptom before | Symptom after |
|---|---|---|---|---|---|
| A | address-phase `bitcnt`/`shift` not cleared after ACK | `always @(negedge scl)` ACK branch, `if (addr_phase)` | reset `bitcnt<=0`, `shift<=0` inside the address-phase branch | run 1: `starts=98`, `mode1=0`, bus decoded garbage | byte decode correct |
| B | ACK never released (release tied to `bitcnt==8` which is already 0) | `always @(posedge scl) if (bitcnt==8) slave_sda_low<=0` | explicit `ack_phase` flag: set on SCL#8 falling edge, released on the **next** falling edge | run 2: `starts=1`, bus wedged | `starts=243 stops=242 acks=720` |
| C | T4b predicate wrong (mask `0x00` deselect treated as violation) | `chk("T4b ...")` | classify masks by **popcount**: `>=2` = violation, `==1` = select, `==0` = legal deselect; require `multi==0 && single>0` | T4b FAIL | T4b PASS (`multi=0 single=45 deselect=1`) |
| D | no visibility into anomalous masks | - | `[T4b-DIAG]` prints every mask with popcount>=2 | - | single deselect identified |

## Files
| File | Role | Status |
|---|---|---|
| `tb/tb_kv260_haptic_top_revA_before_fix.v` | pre-fix baseline (313 lines) | **preserved, unmodified** |
| `tb/tb_kv260_haptic_top_revB.v` | fixed testbench | new |

## Not changed
- 21 RTL files under `rtl/` are byte-identical to the Phase 1A copies.
- No assertion was deleted; the check count went 21 -> 21.
- No protocol constraint was relaxed; T4b became stricter about multi-channel.
