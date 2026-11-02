# Phase 2A：KV260 PS/PL Block Design 最小集成

**日期**：2026-11-01  **结果**：**BD 验证 + 综合 + 实现 + 时序全部通过**
**冻结 RTL**：21 个文件 SHA 全部未变（`RTL_FREEZE_1GV.json` 逐项核对，0 改动）

---

## 1. BD 架构

```
┌──────────────────────────────────────────────────────────────────┐
│  kv260_haptic_bd                                                 │
│                                                                  │
│  ┌─────────────────────┐        ┌──────────────┐                │
│  │ zynq_ultra_ps_e:3.5 │        │ smartconnect │                │
│  │  (KV260 board       │        │    :1.0      │                │
│  │   preset 1.4)       │        │ NUM_SI=1     │                │
│  │                     │        │ NUM_MI=1     │                │
│  │ M_AXI_HPM0_FPD ─────┼───────▶│ S00_AXI      │                │
│  │                     │        │      M00_AXI ┼──┐             │
│  │ pl_clk0 ────────────┼───┬────▶ aclk         │  │             │
│  │ pl_resetn0 ─────────┼─┐ │    └──────────────┘  │             │
│  └─────────────────────┘ │ │                      │             │
│                          │ │  ┌───────────────────┴──────────┐  │
│         ┌────────────────┘ │  │ kv260_haptic_axi_wrapper     │  │
│         │  ┌───────────────┘  │  (Phase 2A integration wrap) │  │
│         ▼  ▼                  │  └──┬───────────────────────┘  │
│   ┌──────────────┐            │     │ instantiates FROZEN      │
│   │proc_sys_reset│            │     ▼                          │
│   │   :5.0       │            │  kv260_haptic_top (sha cdfb57cb)│
│   │ slowest_sync │            │  + i2c_master + pca_init_fsm   │
│   │  ext_reset_in│            │  + matrix_buffer + scheduler   │
│   │ peripheral_  │            │  + oe_guard + heartbeat        │
│   │  aresetn ────┼───┬────────┘                               │
│   └──────────────┘   │                                        │
│                      └──────────▶ axi_sc/aresetn, haptic/aresetn│
│                                                                 │
│  External ports: i2c_scl_pin, i2c_sda_pin, wdog_heartbeat, oe_n │
└─────────────────────────────────────────────────────────────────┘
```

## 2. PS/PL 连接

| 项 | 配置 | 状态 |
|---|---|---|
| PS IP | `xilinx.com:ip:zynq_ultra_ps_e:3.5` + board preset `kv260_som:part0:1.4` | ✅ |
| AXI 主口 | `M_AXI_HPM0_FPD`（`PSU__USE__M_AXI_GP0=1`） | ✅ |
| SmartConnect | `smartconnect:1.0`，1 从 1 主 | ✅ |
| 从接口 | `haptic/s_axi`（AXI4-Lite，32-bit，16-bit 地址） | ✅ |
| PL 时钟 | `PSU__FPGA_PL0_ENABLE=1`，requested `96.968727` MHz | ✅ |
| 时钟分配 | `pl_clk0` → `axi_sc/aclk`、`haptic/aclk`、`maxihpm0_fpd_aclk`、`rst_pl/slowest_sync_clk` | ✅ |
| 复位 | `pl_resetn0`（低有效）→ `proc_sys_reset.ext_reset_in` → `peripheral_aresetn` → `axi_sc/aresetn` + `haptic/aresetn` | ✅ |
| 复位极性 | `peripheral_aresetn` 为**低有效**，与冻结 RTL 的 `pl_resetn0` 极性一致 | ✅ |
| PL1 时钟 | `PSU__FPGA_PL1_ENABLE=0`（禁用，消除悬空 `pl_clk1`） | ✅ |
| `maxihpm1_fpd_aclk` | 接 `pl_clk0`（消除悬空） | ✅ |

## 3. ⚠️ 时钟关键发现：PS 实际只能产生 90,908,180 Hz

**这是本阶段最重要的技术发现。**

| 项 | 值 |
|---|---|
| 请求配置 `CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ` | `96.968727`（写入成功） |
| **BD 计算出的 `pl_clk0` 实际 `FREQ_HZ`** | **`90,908,180` Hz** |
| 证据 | 首次 `validate_bd_design` 报 `FREQ_HZ does not match ... haptic/aclk(96968727) and zynq_ps/pl_clk0(90908180)` |

**原因**：PL0 由 IOPLL 经固定分频比合成，96.968727 MHz **不可精确实现**，工具回落到 90.908180 MHz。

**处理方式（未改冻结 RTL）**：Phase 2A 新增的 wrapper 带独立参数
`CLK_FREQ_HZ`，在 BD 中实例化时设为 **90,908,180**，从而把真实频率传给冻结的
`kv260_haptic_top`。**冻结 RTL 的默认值 96,968,727 一字未改。**

### 用实测频率重算全部时序常数

| 常数 | 公式 | 90,908,180 Hz 下 |
|---|---|---|
| I2C `TICKS` | `F/(4×378700)` | **60** |
| 实际 SCL | `F/(4×60)` | **378,784 Hz**（≤400 kHz ✅） |
| `t_LOW` / `t_HIGH` | `2×60/F` | **1.320 µs**（≥1.3 µs ✅） |
| 心跳半周期 `HB_HALF` | `F/2000` | **45,454** → 周期 **999.99 µs**（≈1 kHz ✅） |
| PCA 唤醒延时 `DLY_1MS` | `F/1000` | **90,908** → **1.000 ms**（≥500 µs ✅） |

**结论：频率偏移后所有时序仍合规，不需要改 RTL。**

## 4. AXI 地址映射

| 基地址 | 范围 | 目标 | 功能 |
|---|---|---|---|
| **0xA000_0000** | 64 KiB | `/haptic/s_axi/reg0` | 整个 haptic 控制窗口 |

窗口内部（冻结 RTL 的 3 段译码）：

| 偏移 | 目标寄存器区 | 功能 |
|---|---|---|
| `0x0000`–`0x0FFF` | `matrix_buffer_axi` | CTRL(0x000)/STATUS(0x004)/待写矩阵(0x100+)/活动矩阵(0x200+) |
| `0x1000`–`0x1FFF` | `axi_status_regs` | SW_CTRL(0x00C: bit0 commit/bit1 tca_en/bit2 fault_clear)、SW_FS_CTRL(0x024) |
| `0x2000`–`0x2FFF` | OE 控制寄存器 | bit0 SW_OE_REQ、bit1 SW_OE_CLR |
| **`0x3000`–`0x3FFF`** | **未映射** | ⚠️ **访问会永久挂起 AXI（PS 侧禁止访问）** |

**PS 软件必须只访问 0xA000_0000 / 0xA000_1000 / 0xA000_2000 三个窗口，禁止 0xA000_3000。**

## 5. 综合与实现结果

| 项 | 结果 |
|---|---|
| BD Validate | **通过**（`=== BD VALIDATED OK ===`） |
| HDL Wrapper | 生成 `kv260_haptic_bd_wrapper`，设为 top |
| **综合** | **0 errors，0 critical warnings**，`synth_design Complete!` |
| **实现** | **`route_design Complete!`** |
| CLB LUTs | **2129**（1.82%） |
| CLB Registers | **2173**（0.93%） |
| Block RAM / DSP | 0 / 0 |
| **Bonded IOB** | **4**（2.12%） |
| **WNS / TNS** | **+6.631 ns / 0.000** |
| WHS / THS | +0.006 / 0.000 |
| 端点 | 6724 |
| 时序 | **All user specified timing constraints are met** |

耗时：679 s（综合+实现）。

## 6. 外部 I/O 检查 —— AXI 已内部化

| 指标 | Phase 1G-U（裸顶层） | **Phase 2A（BD）** |
|---|---|---|
| Bonded IOB | **142** | **4** |
| 说明 | ~40 个 AXI 端口作为外部引脚被自动分配 | AXI 全部走 BD 内部 AXI 互连 |

**外部只保留 4 个必要信号**（符合要求）：
`i2c_scl_pin`、`i2c_sda_pin`（BIDIR）、`wdog_heartbeat`、`oe_n`（OUTPUT）。

### ⚠️ 当前这 4 个端口是 UNFIXED，且落在错误 Bank

`report_io` 显示本阶段**未加入候选 XDC v2**，因此 4 个端口被自动分配到：
`E8 / F7 / F8 / G8`，**Bank 65/66（HP Bank），IOSTANDARD `LVCMOS18*`，状态 `UNFIXED`**。

这不是错误，只是**尚未施加引脚约束**。要落到候选引脚
（H12 / B10 / B11 / bank45 / LVCMOS33），必须把
`constraints/kv260_haptic_j2_candidate_v2.xdc` 加入工程并重新实现。

**本轮未加入**，因为候选引脚仍未被确认为 J2 物理针脚（见 §7）。

## 7. 剩余问题

| # | 问题 | 影响 | 状态 |
|---|---|---|---|
| 1 | **PL0 实际频率 = 90,908,180 Hz ≠ 96,968,727 Hz** | 已通过 wrapper 参数修正，时序全合规 | **已缓解**，需在板卡上实测确认 |
| 2 | **候选 XDC v2 未加入工程** | 4 个外部端口落在 bank65/66 HP Bank 的 LVCMOS18，非目标 bank45 | 待办（需先确认 J2 针号） |
| 3 | **J2 针号映射仍 UNKNOWN** | 不能实物接线 | 阻塞硬件 |
| 4 | **Bank45 实际 VCCO 未实测** | 若 1.8 V 则 LVCMOS33 方案作废 | 阻塞硬件 |
| 5 | **0xA000_3000 未映射会挂死 AXI** | PS 误访问即死 | PS 软件必须规避 |
| 6 | **PS 侧软件/驱动未实现** | 无法实际操作 | 下一阶段 |
| 7 | 未生成 bitstream | 未部署 | 按边界要求 |
| 8 | 21 项中 T5b/T8c/T10b/T11 局限 | OE 安全证据不完整 | 保留记录 |

## 8. 边界声明

- ✅ 冻结的 21 个 RTL 文件**零改动**（逐项 SHA 核对）
- ✅ 仅新增 `phase2a/kv260_haptic_axi_wrapper.v`（集成 wrapper，含参数，无功能改动）
- ✅ 未修改 AX7020 原工程
- ✅ 未修改 PCA 初始化参数
- ✅ **未生成 bitstream**、未 JTAG、未 `loadapp`
- ✅ 未连接执行器、未改供电
- ✅ 候选引脚未被描述为已确认接线

**Phase 2A 完成，停止。**
