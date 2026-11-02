# Phase 1G-U Lite 结果

**日期**：2026-11-01  **结论**：心跳链路已实现并完成综合+实现，**可按验证用途使用**

## 1. 已实现功能

1. **`wdog_heartbeat` 输出端口**：约 1 kHz、50% 占空方波
   - `HB_HALF = CLK_FREQ_HZ / (2 × 1000)`：96.968727 MHz → **48,484** 拍；50 MHz → **25,000** 拍
2. **复位行为**：`pl_resetn0` 拉低 → 心跳恒为 **LOW**（无沿 → 外部看门狗保持关断）
3. **时钟停振行为**：计数器冻结 → 无新沿 → 外部看门狗超时 → OE# 回高 = 关断
4. **外部看门狗协同**（TB 建模）：边沿重触发 → 满足时允许使能；1.5× 心跳周期无沿 → 强制 OE# 高
5. 保留原有 `oe_n` 逻辑，**未做任何无关重构**

## 2. 修改文件

| 文件 | 修改 | SHA256 (前 → 后) |
|---|---|---|
| `rtl/kv260_haptic_top.v` | 新增 1 个 parameter、1 个 output 端口、约 22 行心跳逻辑 | `680ca146…` → **`cdfb57cb…`** |
| `constraints/kv260_haptic_j2_candidate_v2.xdc` | 已含三端口约束（本阶段直接可用，未改动内容） | 3,499 B |

**备份**：`phase1g/kv260_haptic_top.v.BEFORE_PHASE1GU`

**未改动**：`pca_init_sequencer.v`（`a245e271…`）、`kv260_haptic_pipeline.v`（`899240a2…`）、
其余 19 个迁移 RTL、AX7020 原工程、XDC v1。

## 3. 仿真结果（8/8 PASS，两个时钟）

| 检查 | 96.968727 MHz | 50 MHz |
|---|---|---|
| H1 复位时心跳为低 | **PASS** | **PASS** |
| H2 周期 ≈1000 µs (±2%) | **PASS**（999.934 µs） | **PASS**（1000.000 µs） |
| H3 干净 50% 方波 | **PASS**（min=max） | **PASS** |
| H4 心跳期间看门狗满足 | **PASS** | **PASS** |
| H5 时钟停止后无新沿 | **PASS**（0 沿） | **PASS**（0 沿） |
| **H6 停振 → 看门狗超时 → OE# 高（关断）** | **PASS**（`pca_oe_n=1`） | **PASS** |
| H7 恢复时钟后心跳恢复 | **PASS**（3 沿） | **PASS** |
| H8 恢复后看门狗重新满足 | **PASS** | **PASS** |

日志：`phase1g/hb_96968.log`、`phase1g/hb_50m.log`；TB：`phase1g/tb_hb_lite.v`

**这一条直接堵住了此前红队审计指出的"PL 时钟停振时 OE 保持使能"缺口**（在架构层，靠外部看门狗）。

## 4. Vivado 综合与实现结果

使用 XDC v2 全流程：`synth_design → opt_design → place_design → route_design`

| 项 | 结果 |
|---|---|
| 综合 | **0 errors，0 critical warnings**，19 warnings |
| 实现 | **Router Completed Successfully** |
| **IOBUF 推断** | **2 instances**（SCL、SDA）—— 此前的 `tricell → logic` critical warning **已消失** |
| CLB LUTs | **1439**（1.23%） |
| CLB Registers | **1365**（0.58%） |
| Block RAM / DSP | 0 / 0 |
| **WNS / TNS** | **+5.501 ns / 0.000** |
| WHS / THS | +0.023 / 0.000 |
| 约束 | **All user specified timing constraints are met** |

### XDC 端口落位核验（report_io）

| 端口 | 引脚 | Bank | 方向 | IOSTANDARD | 状态 |
|---|---|---|---|---|---|
| `i2c_scl_pin` | **H12** | 45 | BIDIR | LVCMOS33 | **FIXED** + PULLUP |
| `i2c_sda_pin` | **B10** | 45 | BIDIR | LVCMOS33 | **FIXED** + PULLUP |
| `wdog_heartbeat` | **B11** | 45 | OUTPUT | LVCMOS33 | **FIXED** |

无引脚冲突。日志：`phase1g/u_lite_impl.log`、`phase1g/utilization.rpt`、`phase1g/timing_summary.rpt`、`phase1g/report_io.rpt`

## 5. 尚未解决的问题

| # | 问题 | 影响 | 处置 |
|---|---|---|---|
| 1 | **J2 针号映射仍 UNKNOWN** | 不能实物接线 | 需 XTP682 或现场通断实测 |
| 2 | **Bank45 实际 VCCO 未测** | 若为 1.8 V 则 LVCMOS33 方案作废 | 现场万用表 |
| 3 | **Bonded IOB = 142** | 顶层约 40 个 AXI 端口未约束，被 Vivado 自动分配 | 真实部署时应走 BD 内部，非顶层 I/O；**不影响三根目标引脚** |
| 4 | 外部看门狗未实现 | 硬件尚不存在 | 方案已定（74LVC1G123 + MOSFET），未采购 |
| 5 | MOSFET 短路单点故障 | OE# 可能被持续拉低 = 持续使能 | 后续硬件评审 |
| 6 | PS 侧 BD 集成与 AXI 软件未做 | 无法运行 | 下一阶段 |
| 7 | 21 项中 T10b/T11 仍属空洞校验 | OE 安全证据不完整 | 已知，未修复 |

**未生成 bitstream，未执行 JTAG/loadapp，未接任何硬件，未改供电。**

## 6. 下一步实际硬件测试需要的条件

**软件侧（已完成）**
- ✅ 心跳 RTL + XDC v2 + 综合/实现通过

**硬件侧（必须现场确认后才可通电）**
1. **J2 Pin1 方向**与 H12/B10/B11 对应的 J2 针号（XTP682 或通断实测）
2. **实测 Bank45 VCCO = 3.3 V**（万用表，测 J2 VCC 针与 GND）
3. J2 各 IO 是否有板载串阻/上拉/ESD
4. 外部 **4.7 kΩ × 2** 上拉（SCL/SDA → 3.3 V）
5. **OE# 10 kΩ 上拉**（→ 3.3 V，保证默认关断）
6. **74LVC1G123 + N-MOSFET（2N7002/BSS138）** 实现看门狗：τ≈100 ms，心跳 1 kHz
7. 看门狗用**独立 3.3 V 电源**（不得取自 FPGA 供电）
8. 单 PCA9685 逻辑 3.3 V 供电；**V+ 执行器电源物理断开**
9. 首次通电**只测逻辑**，V+ 保持断开
