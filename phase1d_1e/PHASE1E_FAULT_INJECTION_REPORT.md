# Phase 1E -- Fault Injection Report (checker sensitivity)

All four faults were injected by mutating the **captured byte stream** or the bus
model. The golden RTL and the golden capture on disk were never modified.
Checker = decoder #2 (`independent_decoder.py`).

| Fault | Injection | Expected detection | Actual detection | Verdict |
|---|---|---|---|---|
| **F1** wrong PRE_SCALE | PRE_SCALE data byte `0x79 -> 0x7A` | report device + register + data | `F1_PRESCALE: BLK13 PRE_SCALE (0xFE) = 0x7A expected 0x79 -> f=49.62 Hz` | **CAUGHT** |
| **F2** TCA multi-channel | mask forced to `0x03` | FAIL, report mask | `F2_TCA_MULTI: TCA mask 0x03 selects 2 channels simultaneously` | **CAUGHT** |
| **F3** missing init transactions | all bytes addressed to BLK01 dropped (93 bytes) | device must not be marked present | `BLK_MISSING: BLK01 never addressed` | **CAUGHT** |
| **F4** FULL_OFF corrupted | `LED0_OFF_H` `0x10 -> 0x00` | report exact channel | `F4_FULL_OFF: BLK13 ch00 ON=0x000 OFF=0x000 expected OFF_H=0x10` | **CAUGHT** |

## Negative controls (legal inputs must NOT be flagged)
| Input | Result |
|---|---|
| TCA mask `0x00` (all channels deselected) | **not flagged** -- classified as legal deselect |
| TCA mask single-bit `0x01/0x02/0x04/0x08` | **not flagged** |
| Golden capture, init window only | **0 violations** |
| Register writes after `init_done` (runtime PWM) | excluded by the init-window cutoff -- and this exclusion is the *documented* behaviour, not a way to hide a failure |

## Honest limits
- F3 is proven at capture level (block missing -> flagged). The **RTL-level consequence**
  (a NACKed block must not set `online_mask`, must not set `init_healthy`, and OE# must
  stay off) was demonstrated earlier by Phase-1B T10/T10b and by the Gap-1 diagnostic,
  but **was not re-run in this phase**.
- The bus model has no real NACK/timing violation injection at the electrical level.
