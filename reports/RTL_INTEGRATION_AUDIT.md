# Phase 4C — RTL 集成审计

**日期**：2026-11-02  **方式**：只读

---

## 1. 实际生效的 RTL 来源

| 模块 | 路径 | 说明 |
|---|---|---|
| 顶层 | `E:\KVHAP_2F_DEBUG_1CH\rtl_r2\kv260_haptic_top.v` | 生产顶层（BD 引用的是 iobuf_wrapper，其内部实例化此模块） |
| 管线 | `...\rtl_r2\kv260_haptic_pipeline.v` | 全链路集成 |
| I²C 位引擎 | `E:\KVHAP_4A_INTERNAL_PULLUP\rtl_4a\i2c_master.v` | **Phase 4A 修改版**（分频器加宽 + SCL 上升看门狗 + ACK 采样点后移） |
| 其余 12 个模块 | `...\rtl_r2\` | 未改动 |

SHA256 见 `SHA256_MANIFEST_4C.md`。

## 2. 数据链连接（从源码确认）

```
PS AXI-Lite
  ├─ addr[13:12]==00 → ctrl → matrix_buffer_axi ─commit_ack─▶ scheduler_fsm
  ├─ addr[13:12]==01 → sts  → axi_status_regs
  └─ addr[13:12]==10 → oe   → OE_CTRL (0x2000)

scheduler_fsm ─逐通道 out_valid─▶ block_refresh_buffer ─16拍+trig─▶ pca9685_burst_builder
                                                                        │ m_* 字节流
matrix_map_rom ◀─map_addr──┐                                            ▼
matrix_buffer  ◀─rd_addr───┤                                    tca9548_inject
duty_lut_rom   ◀─level─────┘                                            │
                                                                        ▼
                                                                   i2c_master
                                                                        │ scl_drv/sda_drv
                                                                        ▼
                                                              (外部 IOBUF → 总线)
```

依据：`kv260_haptic_pipeline.v:1-16` 头部注释、`:182`（builder→inject→master）、
`:303`（u_duty）、`:481`（u_pinit）、`:523`（u_fmon）。

## 3. OE 门控接入方式

- `haptic_oe_guard` 在 **`kv260_haptic_top.v:277`** 实例化（不在 pipeline 内）
- 极性（`haptic_oe_guard.v:23-24,45,109`）：
  - `oe_n = 1` → 输出**禁用**（安全）
  - `oe_n = 0` → 输出**使能**
  - `oe_n <= ~(arm_ready && dwell_ok)`，复位默认 `1`
- 物理引脚：`kv260_haptic_top` 输出 `oe_n` 端口；**当前未分配 PACKAGE_PIN**（XDC 中无 oe_n 约束）→ 该功能目前仅为软件/内部门控，无实际板级引脚

## 4. AXI-Lite 地址映射（源码确认）

`kv260_haptic_top.v:98-103`：
```verilog
wire sel_ctrl_aw = (s_axi_awaddr[13:12] == 2'b00);   // 0x0000..0x0FFF
wire sel_sts_aw  = (s_axi_awaddr[13:12] == 2'b01);   // 0x1000..0x1FFF
wire sel_oe_aw   = (s_axi_awaddr[13:12] == 2'b10);   // 0x2000..0x2FFF
```

| 偏移 | 用途 | 依据 |
|---|---|---|
| 0x0000 | MB_CTRL（EN/COMMIT） | `matrix_buffer_axi.v` `w_is_ctrl0` |
| 0x0004 | MB_STATUS | STATUS = {enable, commit_ack, act_bank} |
| 0x0100–0x01FC | 矩阵数据 64 字（256 字节） | `w_is_pend = awaddr[9:8]==2'b01` |
| 0x1000–0x1054 | 状态寄存器组 | `axi_status_regs.v` `araddr[6:2]` 索引 |
| 0x2000 | OE_CTRL（bit0 sw_req, bit1 oe_n, bit2 armed） | `haptic_oe_guard` |

## 5. Duty ROM 映射（源码确认，未假设）

`duty_lut_rom.v:27-35`：

| level | pwm | full_off | 对应 |
|---|---|---|---|
| 0 | 0x000 | **1** | 全断（写 FULL_OFF 位，非 duty=0） |
| 1 | 0x660 | 0 | 1632 (40%) |
| 2 | 0x990 | 0 | 2448 (60%) |
| 3 | 0xCC0 | 0 | 3264 (80%) |
| ≥4 | 0xFF0 | 0 | 4080 (100%) |

与 STM32 `HAPTIC_GRAY_TO_PWM_5_LEVEL = {0,1632,2448,3264,4080}` 一致。

## 6. Phase 4A/4B 遗漏的生产模块

| 模块 | 4A | 4B | 4C | 说明 |
|---|---|---|---|---|
| `i2c_master` | ✅ | ✅ | ✅ | — |
| `pca_init_sequencer` | ❌ | ✅ | ✅ | 4A 未涉及 |
| `i2c_fault_monitor` | ❌ | ✅ | ✅ | 4B 起覆盖 |
| `matrix_buffer_axi` / `matrix_buffer` | ❌ | ❌ | ✅ | **4C 首次覆盖** |
| `scheduler_fsm` | ❌ | ❌ | ✅ | **4C 首次覆盖** |
| `duty_lut_rom` | ❌ | ❌ | ✅ | **4C 首次覆盖** |
| `matrix_map_rom` | ❌ | ❌ | ✅ | **4C 首次覆盖** |
| `block_refresh_buffer` | ❌ | ❌ | ✅ | **4C 首次覆盖** |
| `pca9685_burst_builder` | ❌ | ❌ | ✅ | **4C 首次覆盖** |
| `tca9548_inject` | ❌ | ❌ | ✅ | **4C 首次覆盖** |
| `dirty_detector` / `failsafe_timer` / `tactile_v12_ctrl` | ❌ | ❌ | ✅ | **4C 首次覆盖** |
| `fault_recovery_ctrl` | ❌ | ✅(间接) | ✅ | — |
| **`haptic_oe_guard`** | ❌ | ❌ | ✅ | **4C 首次覆盖** |

**4C 是第一次实例化完整生产链路（含 OE guard 与调度/突发路径）。**
