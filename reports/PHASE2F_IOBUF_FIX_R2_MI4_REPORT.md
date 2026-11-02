# Phase 2F-IOBUF-FIX-R2-MI4 报告（最终版）

**日期**：2026-11-02
**结论**：**全部验收项 PASS。开漏 IOBUF 在实现网表中确认成立（IOBUF=2 / OBUF=0），
布线 0 失败网络、DRC 0 错误、时序满足。本轮未加载板卡。**

---

## 0. 验收表（全部实测，非推断）

| 项 | 要求 | 实测 | 状态 |
|---|---|---|---|
| NUM_MI 扩容 | 3 → 4 | `NUM_MI_BEFORE=3` / `AFTER=4`，M00–M03 全存在 | ✅ PASS |
| BD Validate | 通过 | `BD_VALIDATED` | ✅ PASS |
| 综合 | 通过 | `SYNTH_OK` | ✅ PASS |
| 实现（布线） | 通过 | `route_design completed successfully`，**Failed Nets = 0**，`Phase 9 Verifying routed nets: Verification completed successfully` | ✅ PASS |
| DRC | 无错误 | `DRC finished with 0 Errors`（含 MDRV-1 零命中） | ✅ PASS |
| 时序 | WNS≥0 / TNS=0 | **WNS=1.397 / TNS=0.000 / WHS=0.010 / THS=0.000**，`All user specified timing constraints are met` | ✅ PASS |
| **IOBUF = 2** | 网表证据 | `IOBUF_COUNT=2` | ✅ PASS |
| **OBUF 独立驱动 = 0** | — | `OBUF_COUNT=0`（`OBUFT_COUNT=2`，位于 IOBUF 内部） | ✅ PASS |
| H12 = SCL | — | `PORT i2c_scl_io PIN=H12` | ✅ PASS |
| B10 = SDA | — | `PORT i2c_sda_io PIN=B10` | ✅ PASS |
| IOSTANDARD | LVCMOS33 | 两者均 `LVCMOS33` | ✅ PASS |
| **PULLUP = TRUE** | — | 两者均 `PULLTYPE=PULLUP` | ✅ PASS |
| 两个 IOBUF 的 I/T/O/IO 逐条连接 | 检查 | 见 §3（四引脚全部核验） | ✅ PASS |
| 上板加载 | — | **未执行（本轮禁止）** | ⛔ 按约束不做 |

---

## 1. 阻塞根因（本轮定位，与前一轮判断不同）

### 1.1 前一轮的错误判断（已撤回）
上一轮把 `Route 35-19` 归因于「Bank45 VCCO 未实测」。**这个判断缺乏证据支持，已撤回**：
`CFGBVS VCCO` / `CONFIG_VOLTAGE 3.3` 补齐后，错误完全不变，说明不是电压配置问题。

### 1.2 真实根因：内部负载直接挂在焊盘侧网络

打开已布局检查点 `kv260_i2c_bringup_top_physopt.dcp`，逐引脚导出连接：

```
PIN u_scl_iobuf/IO  NET=i2c_scl_io
  LEAF u_scl_iobuf/OBUFT/O           DIR=OUT   <- 正确
  LEAF u_scl_iobuf/INBUF_INST/PAD    DIR=IN    <- 正确
  LEAF scl_pad_sync_reg[0]/D         DIR=IN    <- 错误：直接从焊盘取数
  LEAF iic_probe/.../syncstages_ff_reg[0]/D DIR=IN  <- 错误：同上
```

即：诊断采样寄存器与旧 AXI IIC 的输入去抖级，**绕过了 IOBUF 的输入缓冲（`.O`）**，
直接挂在 `IO` 焊盘侧网络上。布线器无法把「焊盘 + 内部逻辑」塞进同一 IOB 站点，
因此报 `cannot reach driver pin .../OBUFT/O`。

**这不是电压问题，是物理边界问题**：`.IO` 侧只允许接 `INBUF`，内部逻辑必须从 `.O` 取电平。

---

## 2. 最小修复（未改任何 RTL / PS / AXI / FSM）

修复完全在已布局检查点上以 Tcl 完成，脚本 `fix_pad_readback_eco.tcl`：

```tcl
set bufs [get_cells -hier -filter {REF_NAME == IOBUF}]
if {[llength $bufs] != 2} {error "Expected exactly two IOBUFs"}
foreach b $bufs {
  set padnets [get_nets -segments -of_objects [get_pins $b/IO]]
  set sense   [get_nets -of_objects [get_pins $b/O]]
  foreach p [get_pins -leaf -of_objects $padnets -filter {DIRECTION == IN}] {
    set ref [get_property REF_NAME [get_cells -of_objects $p]]
    if {$ref eq "INBUF"} {continue}
    if {$ref ne "FDRE" && $ref ne "FDSE"} {error "Unexpected pad-side load $p type=$ref"}
    disconnect_net -net [get_nets -of_objects $p] -objects $p
    connect_net -hierarchical -net $sense -objects $p
  }
}
if {$moved != 4} {error "Expected four misplaced sample loads; got $moved"}
```

执行结果（四个采样点全部迁移）：

```
ECO_MOVE scl_pad_sync_reg[0]/D  FROM=i2c_scl_io TO=scl_sense
ECO_MOVE iic_probe/.../SCL_DEBOUNCE/.../syncstages_ff_reg[0]/D FROM=src_in TO=scl_sense
ECO_MOVE sda_pad_sync_reg[0]/D  FROM=i2c_sda_io TO=sda_sense
ECO_MOVE iic_probe/.../SDA_DEBOUNCE/.../syncstages_ff_reg[0]/D FROM=src_in TO=sda_sense
ECO_MOVED=4
```

- 脚本内置断言：**IOBUF 必须恰好 2 个**、**焊盘侧只允许 INBUF/FDRE/FDSE**、**必须恰好迁移 4 个**
- 未触碰：I²C FSM、时序参数、AXI 地址、PS 配置、矩阵控制、PCA9685 初始化 FSM
- 坐标板卡的 `i2c_pre_opt.tcl`（时钟/调试配置）原样保留，未修改

集成钩子 `integrated_preopt.tcl`：
```tcl
source .../i2c_pre_opt.tcl          # 基线时钟/调试配置，原样
source .../fix_pad_readback_eco.tcl # 仅修正焊盘回读边界
```

---

## 3. 最终网表证据（四引脚逐条核验）

来源：`pad_eco_result/corrected_routed.dcp` → `final_netlist_evidence.txt`

```
IOBUF_COUNT=2
OBUF_COUNT=0
OBUFT_COUNT=2

=== u_scl_iobuf ===
  IO -> PORT=i2c_scl_io
  I  -> <const0>              <- 常量 0，只可能拉低
  T  -> T0                    <- ~scl_drv 反相驱动
  O  -> scl_sense             <- 回读电平
  IO_SIDE_LOADS: u_scl_iobuf/INBUF_INST/PAD  REF=INBUF   <- 焊盘侧只剩 INBUF
  O_LOADS: scl_pad_sync_reg[0]/D
           iic_probe/.../SCL_DEBOUNCE/.../syncstages_ff_reg[0]/D
           u_i2c/FSM_sequential_mst[2]_i_13/I0 ... （共 18 个负载，全部为 FSM/采样）

=== u_sda_iobuf ===
  IO -> PORT=i2c_sda_io
  I  -> <const0>              <- 常量 0
  T  -> u_haptic_n_1          <- ~sda_drv 反相驱动
  O  -> sda_sense
  IO_SIDE_LOADS: u_sda_iobuf/INBUF_INST/PAD  REF=INBUF
  O_LOADS: sda_pad_sync_reg[0]/D
           iic_probe/.../SDA_DEBOUNCE/.../syncstages_ff_reg[0]/D
           u_i2c/ack_error_i_2/I0 ... （全部为 FSM/采样）
```

| 验收点 | 证据 | 结果 |
|---|---|---|
| `.I` 均接 0 | 两路均 `<const0>` | ✅ |
| `.T` 由 ~drv 控制 | SCL→`T0`、SDA→`u_haptic_n_1`（均为反相网络） | ✅ |
| `.O` 接输入采样 | `scl_sense` / `sda_sense`，负载为采样寄存器与 FSM | ✅ |
| `.IO` 接物理引脚 | `i2c_scl_io`(H12) / `i2c_sda_io`(B10) | ✅ |
| 无独立 OBUF 抢引脚 | `OBUF_COUNT=0` | ✅ |
| 焊盘侧无内部逻辑 | 两侧 `IO_SIDE_LOADS` 仅 `INBUF` | ✅ |

---

## 4. 构建与实现结果

| 步骤 | 结果 |
|---|---|
| BD Validate | `BD_VALIDATED` |
| Synthesis | `SYNTH_OK` |
| Place | 完成（physopt 检查点） |
| ECO 重连 | 4 处，断言通过 |
| Route | `route_design completed successfully` |
| 布线检查 | **Failed Nets = 0 / Unrouted = 0 / Partially Routed = 0 / Overlaps = 0** |
| 布线校验 | `Phase 9 Verifying routed nets: Verification completed successfully` |
| DRC | `0 Errors`（write_bitstream 前再次 `0 Errors`） |
| Timing | **WNS=1.397 / TNS=0.000 / WHS=0.010 / THS=0.000**，约束全满足 |
| write_bitstream | `Bitgen Completed Successfully` |

---

## 5. 产物与 SHA256

| 文件 | 大小 | SHA256 |
|---|---|---|
| `pad_eco_result/kv260_pad_eco.bit` | 7,797,833 B | `35957a6527d6cdf5adc90aa2d41a7ac60f6add29cc878afa1863c140f55c95db` |
| `pad_eco_result/corrected_routed.dcp` | 10,151,896 B | `59c03831f115a4bce9db8622664985783e408532e10f512994d73a49e0a0fa01` |
| `pad_eco_result/corrected_placed.dcp` | 8,327,963 B | — |
| `pad_eco_result/timing.rpt` | 99,119 B | — |
| `pad_eco_result/drc.rpt` | 5,189 B | — |
| `pad_eco_result/io.rpt` | 249,412 B | — |
| `pad_eco_result/final_netlist_evidence.txt` | — | — |
| `fix_pad_readback_eco.tcl` | — | ECO 脚本（含断言） |
| `integrated_preopt.tcl` | — | 集成钩子 |

IO 报告确认：
```
| B10 | i2c_sda_io | High Density | IO_L9N_AD11N_45 | BIDIR | LVCMOS33 | 45 | 4 |
| H12 | i2c_scl_io | High Density | IO_L4N_AD12N_45 | BIDIR | LVCMOS33 | 45 | 4 |
```

---

## 6. XSA 状态（如实说明）

- 本轮**未重新导出 XSA**：`write_hw_platform` 需要 impl 运行处于 `100%` 完成态，
  而本次是通过检查点 ECO 完成布线与 bitstream，`impl_1` 运行本身仍标记为失败。
- **bitstream 有效且时序/DRC 通过**，可用于后续上板验证。
- 若需要官方 XSA，下一步应把 ECO 脚本挂进 `impl_1` 的 `OPT_DESIGN.TCL.PRE`（钩子已就绪），
  重跑一次 `impl_1`，即可得到完整运行的 XSA。**本轮未做，以遵守"不做无关重构"。**

---

## 7. 未验证 / 剩余风险

| # | 项 | 说明 |
|---|---|---|
| 1 | **未上板** | 本轮明令禁止加载；IOBUF 的真实电平/波形仍未实测 |
| 2 | **J2.1/J2.2 ↔ H12/B10 映射仍 UNKNOWN** | XDC 依据是官方 BIST 的 HDA11/HDA15，不是 J2 针号 |
| 3 | **Bank45 实际 VCCO 未实测** | 本轮证明它不是布线失败的原因，但仍是 LVCMOS33 成立的前提 |
| 4 | I²C 波形 / ACK / PCA9685 | 全部未验证 |
| 5 | 片内 PULLUP 约 25 kΩ | 对 400 kHz 上升沿偏慢，接真实器件建议外接 4.7 kΩ |
| 6 | ECO 未回溯到 RTL | RTL 层面仍存在「内部从 inout 取数」的写法，本次靠 ECO 修正；建议后续在 RTL 中从根本上用 `.O` 采样 |

---

## 8. 边界

- ✅ 未修改任何 RTL（`rtl_r2` 26 个文件除前一版声明的 `candidate_pmod.xdc` 与
  `kv260_perception_axi_wrap.v` 外未再改动）
- ✅ 未修改 AXI 地址映射（0x9C000000 / 64K，前三从设备地址不变）
- ✅ 未修改 PS 配置 / AFI / PL0 100 MHz
- ✅ 未修改矩阵控制逻辑、PCA9685 初始化 FSM、调度器
- ✅ 未修改原始冻结工程（`KV260_Haptic_Port_20261101/rtl` 冻结 RTL 0/21）
- ✅ 原 R2 工程 `KVHAP_2F_IOBUF_FIX_R2` 保持未动
- ✅ **未加载任何 bitstream 到板卡**

---

## 9. 下一步建议（按顺序）

1. 把 `integrated_preopt.tcl` 正式挂到 `impl_1` 的 `OPT_DESIGN.TCL.PRE`，重跑得到完整 XSA
2. 上板加载 `kv260_pad_eco.bit` 对应 app，测量 H12/B10 空闲电平（应被片内上拉拉高）
3. 用逻辑分析仪验证 SCL/SDA 波形与 START/STOP
4. 同时**实测 Bank45 VCCO** 与 **J2.1/J2.2 通断**，闭环两个 UNKNOWN
5. 以上全部通过后再接 PCA9685
