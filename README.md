> **2026-11-03 最新统一候选：** 请从 [unified_20261103/README.md](unified_20261103/README.md) 进入。新增完整PS BD、板级顶层、IOBUF、两份XDC、冻结感知参考、384B桥接及触觉RTL；本地实现通过，**尚未上板，默认禁止实体输出，未验收震动**。现有根目录4D及分阶段资料保留为历史基线。
# kv260-haptic-vest

FPGA-based 16x16 haptic matrix controller development on AMD Kria KV260, featuring AXI frame
buffering, I2C scheduling and PCA9685-based actuator control.

> **Project status: development / partial bring-up.** See [Verification Status](#verification-status).
> RTL simulation and the full Vivado build pass. External I2C device communication and actuator
> drive have **not** been verified on hardware yet.

---

## 1. Background

This repository is the AMD Kria KV260 port of an existing 16x16 (256-point) haptic vest
controller. The original production controller is an STM32N647 design that drives a peripheral
array board containing one **TCA9548A** I2C multiplexer and **16 x PCA9685** 16-channel PWM
drivers (256 actuators total).

The goal of the port is to move the frame buffer, dirty-block tracking, scheduling and I2C
generation from the MCU into KV260 programmable logic (PL), while the PS runs Linux and exposes
the control surface over AXI4-Lite.

## 2. 16x16 matrix architecture

- 256 actuator positions, addressed as `block` (0-15) x `channel` (0-15).
- Each block is one PCA9685 (16 PWM channels).
- Blocks are distributed across 4 TCA9548A downstream channels, 4 blocks per channel.
- PCA9685 7-bit addresses are **non-sequential** by design and must not be iterated in order.

| block | TCA ch | PCA 7-bit | block | TCA ch | PCA 7-bit |
|---|---|---|---|---|---|
| 0 | 0 | 0x4D | 8 | 2 | 0x40 |
| 1 | 0 | 0x4E | 9 | 2 | 0x41 |
| 2 | 0 | 0x4F | 10 | 2 | 0x42 |
| 3 | 0 | 0x44 | 11 | 2 | 0x43 |
| 4 | 1 | 0x49 | 12 | 3 | 0x45 |
| 5 | 1 | 0x4A | 13 | 3 | 0x46 |
| 6 | 1 | 0x4B | 14 | 3 | 0x47 |
| 7 | 1 | 0x4C | 15 | 3 | 0x48 |

TCA9548A is at 7-bit address **0x70**.

## 3. KV260 PS/PL split

- **PS (Linux)**: loads the PL bitstream via the Kria `xmutil` runtime-app flow, maps the
  AXI4-Lite registers through UIO, writes intensity frames, reads status.
- **PL**: matrix double-buffer, dirty-block detector, scheduler FSM, duty LUT, PCA9685 burst
  builder, TCA9548A injector, I2C bit engine, failsafe timer and OE safety guard.

## 4. AXI data path

```
PS (AXI4-Lite)
  +-- 0x0000..0x0FFF  ctrl : matrix_buffer_axi
  |       0x0000 CTRL (bit0 EN, bit1 COMMIT)
  |       0x0004 STATUS
  |       0x0100..0x01FF  pending-bank frame window (256 bytes)
  |       0x0200..0x02FF  active-bank readback
  +-- 0x1000..0x1FFF  sts  : axi_status_regs (counters / masks / fault state)
  +-- 0x2000..0x2FFF  oe   : PCA9685 OE# safety controller
```

## 5. Double buffering and atomic frame swap

`matrix_buffer` holds two 256-byte banks. Software writes a complete frame into the *pending*
bank, then writes `CTRL.COMMIT`. The commit request is only granted at a frame boundary when the
scheduler, dirty detector and I2C engine are idle and no fault is latched; only then are the
banks swapped. This keeps a partially written frame from ever reaching the actuators.

A write to the pending window while a commit is still pending is dropped and flagged sticky in
`COMMIT_STATUS`, so a caller that wrote too early can detect it.

## 6. I2C / TCA9548A / PCA9685 control architecture

- Open-drain I2C, 378.7 kHz nominal (Fast-mode compliant `tLOW`).
- `tca9548_inject` selects the mux channel before each PCA9685 transaction.
- Each block refresh is one 66-byte burst: `[addr_W, 0x06, 16 x (ON_L, ON_H, OFF_L, OFF_H)]`.
- 5-level intensity mapping (duty LUT): 0 -> FULL_OFF bit, 1 -> 1632, 2 -> 2448, 3 -> 3264,
  >=4 -> 4080 (out of 4096).
- PCA9685 init order matches the STM32 reference: MODE1(SLEEP|AI) -> PRE_SCALE ->
  FULL_OFF -> MODE2(OUTDRV) -> MODE1(AI) -> MODE1(RESTART|AI).
- OE# is a separate safety guard: `oe_n = 1` means outputs disabled, and it only clears after
  arming and a minimum off-dwell.

## 7. Build environment

| Item | Version |
|---|---|
| Board | AMD Kria KV260, carrier `ZynqMP KV260 revB` |
| Device | `xck26-sfvc784-2LV-c` |
| Vivado | 2025.2 |
| Host OS | Windows 11 |
| Target OS | Ubuntu 24.04 LTS, kernel 6.8.0-1015-xilinx |

Build the PL design from the Vivado project, export a fixed XSA, then convert the bitstream to a
Kria runtime app (`bit.bin` + `.dtbo` + `shell.json`) and load it with `xmutil loadapp`.

## 8. Simulation and verification summary

| Check | Result |
|---|---|
| RTL simulation - full 16-device init | PASS (ONLINE_MASK = 0xFFFF) |
| RTL simulation - fault injection (7 cases) | PASS |
| RTL simulation - end-to-end single channel | PASS (`level=4 -> PWM 0x0FF0`, `PRE_SCALE=121 -> 50.4 Hz`) |
| Vivado synthesis | PASS |
| Vivado implementation + routing | PASS |
| Timing | PASS (WNS +2.320 ns, TNS 0, WHS +0.011 ns) |
| DRC | PASS (0 errors) |
| Bitstream generation | PASS |
| Runtime app load on board | PASS (`Loaded with slot_handle 0`) |
| Board register readback over UIO | PASS |
| **External I2C device ACK** | **NOT VERIFIED** |
| **PCA9685 response on hardware** | **NOT VERIFIED** |
| **256 actuator drive** | **NOT VERIFIED** |

## Verification status

Please read the table above literally. Specifically:

- **Verified:** the RTL design is functionally correct in simulation, and it synthesizes,
  implements, meets timing with zero DRC errors, and produces a bitstream that loads and runs on
  the KV260. AXI4-Lite register access from Linux works.
- **Not verified:** the external peripheral board has never acknowledged on the I2C bus while
  running this design (`ONLINE_MASK` stays `0x0000`, `I2C_TIMEOUT_CNT` stays `0`, `I2C_ERR_CNT`
  grows). The physical mapping between the KV260 carrier connector and the FPGA pins used for
  SCL/SDA is **not confirmed by measurement**. No oscilloscope or logic-analyser capture of SCL
  exists for this design, so no claim is made that SCL has been observed on the pads.
- Consequently this repository does **not** claim that external I2C communication or actuator
  drive works.

## 9. Known issues / open items

1. External peripheral board does not ACK - root cause not yet determined. Candidates: connector
   pin mapping, peripheral supply/ground, bus pull-up strength.
2. `J2` connector pin number <-> FPGA package pin mapping is unconfirmed. The current XDC is based
   on the vendor BIST reference (HDA11/HDA15), which is a SOM240 net name and is not the same thing
   as a carrier `J2` pin number.
3. On-chip pull-ups (~25 kΩ) are the only pull-ups in the current constraint file. The original
   host board provided external 4.7 kΩ pull-ups.
4. On-die pad level read-back into the diagnostic status register is currently tied off, so pad
   levels cannot be observed from software.

## 10. Roadmap

1. Confirm connector pin mapping and peripheral supply with a multimeter.
2. Add read-only pad/ACK diagnostic counters and expose them through spare AXI register space.
3. Bring up a single PCA9685, then the full 16-device array.
4. Single-channel actuator vibration, then full 256-channel validation.

## Repository layout

```
docs/                 design notes and status
rtl/                  frozen RTL baseline
rtl-deployed-4d/      RTL set used by the last built/deployed bitstream
constraints/          XDC
tb/  sim/             testbenches
reports/              build / audit / acceptance reports
audit/                independent audit notes
logs/                 captured build and test logs
phase*/               per-phase evidence and manifests
```

## References

The STM32 original firmware is kept out of this repository; only protocol and mapping facts
derived from it are documented here.
