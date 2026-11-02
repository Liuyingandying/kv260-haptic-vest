# INTERNAL_PULLUP_AUDIT — Phase A 物理层审计

**日期**：2026-11-02  **方式**：只读审计（未修改硬件、未上板、未写寄存器）
**核心结论**：**FPGA 内部弱上拉 + 现有 FSM 自适应时序在仿真中可覆盖 25 kΩ/1.2 nF 的极端工况；
但此前的"25 kΩ 不足以驱动总线"判断是错的，本报告予以撤回。**

---

## A1. IOBUF 实例与连接（已验证）

来源：`E:\KVHAP_2F_DEBUG_1CH\rtl_r2\kv260_haptic_iobuf_wrapper.v:56-57`

```verilog
IOBUF u_scl_iobuf (.I(1'b0), .T(~scl_drv), .O(scl_sense), .IO(i2c_scl_io));
IOBUF u_sda_iobuf (.I(1'b0), .T(~sda_drv), .O(sda_sense), .IO(i2c_sda_io));
```

| 引脚 | 连接 | 性质 |
|---|---|---|
| `.I` | `1'b0` | 只可能拉低，绝不推高 —— 严格开漏 ✅ |
| `.T` | `~scl_drv` / `~sda_drv`（LUT1 反相） | 1=释放，0=拉低 ✅ |
| `.O` | `scl_sense` / `sda_sense` | 缓冲回读 ✅ |
| `.IO` | 顶层端口 → H12 / B10 | 物理焊盘 ✅ |

## A2. PULLUP 属性是否保留（已验证）

`rtl_r2/candidate_pmod.xdc`：
```tcl
set_property IOSTANDARD LVCMOS33 [get_ports {i2c_scl_io i2c_sda_io}]
set_property PULLUP TRUE [get_ports {i2c_scl_io}]
set_property PULLUP TRUE [get_ports {i2c_sda_io}]
```

实现网表实测（`corrected_routed.dcp`）：
```
PORT i2c_scl_io PACKAGE_PIN=H12 IOSTANDARD=LVCMOS33 PULLTYPE=PULLUP PULLUP=1
PORT i2c_sda_io PACKAGE_PIN=B10 IOSTANDARD=LVCMOS33 PULLTYPE=PULLUP PULLUP=1
```
✅ 属性真实生效（UltraScale+ HD Bank 内部上拉典型 ≈ 25 kΩ）

## A3. 输入采样是否全部来自 IOBUF.O（已验证）

- haptic I²C FSM：`kv260_haptic_iobuf_wrapper.v:48` `.i2c_scl_i(scl_sense)` → 全部经 `.O` ✅
- 顶层诊断采样：`kv260_i2c_bringup_top.v:100-101` 已改为常量 `1'b1`（不再加载焊盘网）✅
- 实现网表：两个 IOBUF 的 `IO` 侧负载**只有 `INBUF`**（12 次遍历全部为 `INBUF_INST/PAD`）✅

## A4. 当前 I²C 时钟参数（已验证）

`i2c_master.v` 基线：
```verilog
localparam integer TICKS = CLK_FREQ_HZ / (4 * I2C_FREQ_HZ);   // 96968727/(4*400000) = 60
reg [7:0] div;                                                // ★ 8 bit
wire tick = (mst != M_IDLE) && (div == TICKS - 1);
```
- CLK = 96,968,727 Hz（PL0）
- I2C_FREQ_HZ = 400,000 → TICKS = 60 → SCL = 96968727/(4×60) = **404.04 kHz**
- **结构限制**：`div` 仅 8 bit，`TICKS-1` 必须 ≤ 255
  → 最低可达频率 = 96968727/(4×256) = **94.7 kHz**
  → **50 kHz / 10 kHz 在基线 RTL 上不可实现**（`tick` 永不成立，FSM 会挂死）

## A5. 紫色驱动板板载上拉（未知）

- 本轮**未上板、未拆解**，无法确认驱动板是否已焊 4.7 kΩ 上拉
- 原始硬件文档 `fpga/16x16/docs/HARDWARE_INTERFACE_MANUAL.md:48-49` 明确把 4.7 kΩ 列在 **主机侧（AX7020 板）**
  → 推断驱动板**不自带上拉**，但**证据等级：推断，未实测**

## A6. 原始 STM32 的 I²C 时序参数（已验证）

| 项 | 值 | 来源 |
|---|---|---|
| I²C 外设 | I2C4 | `README_INTEGRATION.md:92` |
| Timing 寄存器 | `0x10707DBC` | `AX7020_Migration_Feasibility_Report.md:41` |
| 档位 | Fast mode，约 250–400 kHz | 同上 |
| 上拉 | 外接 4.7 kΩ → 3.3 V | `HARDWARE_INTERFACE_MANUAL.md:48-49` |

## A7. 三类上拉必须严格区分

| 类别 | 位置 | 当前状态 | 证据 |
|---|---|---|---|
| **① FPGA 内部弱上拉** | KV260 HD Bank 内部 | **≈25 kΩ，已启用** | `PULLTYPE=PULLUP` 实测 |
| **② 驱动板板载上拉** | 紫色成品板 | **未知** | 未拆解、未测量 |
| **③ 原始主控板外部上拉** | AX7020 J11 侧 | **原架构有 4.7 kΩ** | 手册 L48-49 明文 |

**这三者在电气上是并联关系，不可互相替代或混为一谈。**
KV260 目前只有 ①，缺少 ③（因为它就是替代 AX7020 的主机）。

---

## 关键修正：撤回此前的错误结论

**此前结论（第 76 轮）**：
> 25 kΩ 允许的容性负载仅 14 pF（400 kHz）/47 pF（100 kHz），而实际总线 200–400 pF，
> 因此上升时间不足 → 全 NACK。**缺失 4.7 kΩ 是根因。**

**该结论是错的。** Phase D 仿真证明：

- 现有 FSM 在 `M_SEND`/`M_ACK` 的 phase2 会**等待 `scl_i` 实际变高才推进**
- 因此 SCL 周期被**自动拉伸**到 "标称周期 + 上升时间"
- 实测：标称 400 kHz 在 tr=12 µs 下实际变成 **70.4 kHz**，且 **ACK 采样正确**
- 极端工况 25 kΩ + 1.2 nF（tr=36 µs）仍能 ACK（26.1 kHz）

→ **"上拉不足"不是全 NACK 的原因。** 真正原因需重新定位（见 PoC 报告）。

---

## 审计结论

| 项 | 状态 |
|---|---|
| IOBUF 开漏结构 | ✅ 正确，无推挽 |
| PULLUP 属性保留 | ✅ 实测生效 |
| 输入采样经 `.O` | ✅ 全部 |
| 焊盘侧负载 | ✅ 仅 INBUF |
| 基线最低频率 | ❌ **94.7 kHz**（8-bit div 限制） |
| 驱动板板载上拉 | ⚠️ 未知，需拆解/测量 |
| 上拉不足是根因 | ❌ **已由仿真推翻** |
