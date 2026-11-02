# Phase 1E -- PCA9685 Register-Level Audit

Two **independent** decoders were used, per instruction:
- **Decoder #1 (Verilog)**: `tb_phase1e_regmap.v` -- rebuilds the image from SCL/SDA edges.
- **Decoder #2 (Python)**: `independent_decoder.py` -- re-derives pointer/auto-increment
  from the raw byte ordering only, ignoring decoder #1's PTR/DATA/regidx labels.
Both produce the same verdict. Images: `regmap_256.csv` (V), `python_regmap_256.csv` (Py).

## Critical methodology fix (this round)
The register image must be sampled **exactly at `init_done`**. An earlier check sampled
~4 ms later and reported 8 FULL_OFF "violations" in blocks 13-15 -- those were
*a runtime refresh having legitimately overwritten FULL_OFF with real PWM duties*.
The testbench now snapshots at the `init_done` rising edge. This was a **testbench
methodology error, not an RTL defect**.

## 1. Block-level results (all 16)
| Check | Value | Verdict |
|---|---|---|
| Blocks addressed | **16/16** | PASS |
| `PRE_SCALE` (0xFE) | **0x79 on 16/16** -> f = 25e6/((121+1)*4096) = **50.03 Hz** | PASS |
| `MODE2` (0x01) | **0x04 (OUTDRV) on 16/16** | PASS |
| first `MODE1` (0x00) | **0x30 (SLEEP\|AI) on 16/16** | PASS |
| `MODE1` final @init_done | **0xA0 (RESTART\|AI) on 16/16** | PASS |
| TCA selects | 17 = 1 deselect (0x00) + 16 single-channel | PASS |
| TCA masks observed | {0x00, 0x01, 0x02, 0x04, 0x08} -- never >1 bit | PASS |

Observed `MODE1` write sequence per block: `0x30 -> 0x20 -> 0xA0`
(= SLEEP|AI -> wake -> RESTART|AI), exactly the sequence in
`pca_init_sequencer.v` L10-L16 / L107-L134.

After init, the runtime refresh path writes `MODE1 = 0x20` on blocks it refreshes
(observed on BLK13/14/15 within the capture window). That is the burst builder's
documented "MODE1 re-assertion", not a defect.

## 2. 256-channel FULL_OFF (the focus of this phase)
Correct FULL_OFF per the PCA9685 datasheet and `pca_init_sequencer.v` L125
(every 4th register byte = `0x10`, i.e. `LEDn_OFF_H` bit4):

```
LEDn_ON_L = 0x00, LEDn_ON_H = 0x00, LEDn_OFF_L = 0x00, LEDn_OFF_H = 0x10
```

| Result | Value |
|---|---|
| Channels checked | **256 / 256** (16 blocks x 16 channels) |
| Violations | **0** |
| Verdict | **PASS** |

Both decoders independently agree: `ON=0x000, OFF=0x010` for every channel.
Note the corrected expectation: an earlier checker demanded `LED_ON=0x010`, which
confused `LEDn_ON_H` with `LEDn_OFF_H`. FULL_OFF is `OFF_H[4]=1`, not `ON_H`.

## 3. Transaction accounting
| Item | Value |
|---|---|
| clocked bytes in init window | **1330** (113 ADDR + 96 PTR + 1104 DATA + 17 TCA) |
| START / STOP | 113 / 113 |
| ACKs | all ACKed (model always ACKs; see limits) |
| init_done | **37.474 ms** @ TICKS=64 (was 35.457 ms @ TICKS=60) |

1330 bytes exactly matches the AX7020 v1.6 figure -> the port did not add or drop
any initialisation transaction.

## 4. Limits
- Bus model ACKs every byte: a real NACK path is only exercised by fault injection.
- No real PCA9685: register *writes* are verified; actual oscillator/PWM behaviour is not.
